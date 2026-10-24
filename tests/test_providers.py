"""LLMProvider tests with a fake Groq client. Never touches the network.

Groq exception classes are reached through the provider module, so the Groq
SDK is still only imported inside src/askauto/providers/.
"""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from askauto.config import Settings
from askauto.providers import (
    CALL_SETTINGS,
    LLMError,
    LLMProvider,
    Message,
    get_provider,
    groq_provider,
    strict_json_schema,
)
from askauto.providers.groq_provider import GroqProvider

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MESSAGES = [Message(role="user", content="question")]


class Plan(BaseModel):
    sql: str
    assumptions: list[str]


class Outer(BaseModel):
    plan: Plan
    note: str | None = None


class FakeCompletions:
    """Returns or raises the queued outcomes in order, recording every request."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Any:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        content, finish_reason = outcome if isinstance(outcome, tuple) else (outcome, "stop")
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=finish_reason)])


def make_provider(
    outcomes: list[Any], **settings: Any
) -> tuple[GroqProvider, FakeCompletions, list[float]]:
    completions = FakeCompletions(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = GroqProvider(Settings(groq_api_key=None, **settings), client=client,
                            sleep=fake_sleep)
    return provider, completions, sleeps


def status_error(status: int, headers: dict[str, str] | None = None) -> Exception:
    response = httpx.Response(status, headers=headers or {},
                              request=httpx.Request("POST", GROQ_URL))
    cls = {429: groq_provider.groq.RateLimitError,
           400: groq_provider.groq.BadRequestError,
           500: groq_provider.groq.InternalServerError}[status]
    return cls(f"HTTP {status}", response=response, body=None)


VALID_PLAN = '{"sql": "SELECT 1", "assumptions": []}'


@pytest.mark.parametrize("call, temperature", [
    ("classify", 0.0), ("generate_sql", 0.0), ("synthesize", 0.4),
])
async def test_call_site_sets_temperature(call: str, temperature: float) -> None:
    provider, completions, _ = make_provider([VALID_PLAN])
    await provider.complete(call, MESSAGES, Plan)
    request = completions.requests[0]
    assert request["temperature"] == temperature == CALL_SETTINGS[call].temperature
    assert request["max_completion_tokens"] == CALL_SETTINGS[call].max_output_tokens


async def test_sends_strict_json_schema_and_model() -> None:
    provider, completions, _ = make_provider([VALID_PLAN], groq_model="some/model")
    await provider.complete("generate_sql", MESSAGES, Plan)
    request = completions.requests[0]
    assert request["model"] == "some/model"
    assert request["messages"] == [{"role": "user", "content": "question"}]
    fmt = request["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Plan"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


async def test_parses_into_schema() -> None:
    provider, _, _ = make_provider(['{"sql": "SELECT 2", "assumptions": ["x"]}'])
    plan = await provider.complete("generate_sql", MESSAGES, Plan)
    assert plan == Plan(sql="SELECT 2", assumptions=["x"])


async def test_invalid_output_raises_llm_error() -> None:
    provider, _, _ = make_provider(['{"sql": "SELECT 1"}'])  # assumptions missing
    with pytest.raises(LLMError, match="does not match Plan"):
        await provider.complete("generate_sql", MESSAGES, Plan)


async def test_truncated_output_raises_llm_error() -> None:
    # The qwen failure mode: a looping model runs into the output cap.
    provider, _, _ = make_provider([('{"sql": "SELECT 1"}]}]}]}', "length")])
    with pytest.raises(LLMError, match="output cap"):
        await provider.complete("generate_sql", MESSAGES, Plan)


async def test_rate_limit_is_retried_with_backoff() -> None:
    provider, completions, sleeps = make_provider([status_error(429), VALID_PLAN])
    plan = await provider.complete("classify", MESSAGES, Plan)
    assert plan.sql == "SELECT 1"
    assert len(completions.requests) == 2
    assert len(sleeps) == 1 and 1.0 <= sleeps[0] <= 1.25  # 2**0 + jitter


async def test_retry_after_header_is_honoured() -> None:
    provider, _, sleeps = make_provider(
        [status_error(429, {"retry-after": "3"}), VALID_PLAN])
    await provider.complete("classify", MESSAGES, Plan)
    assert sleeps == [3.0]


async def test_server_error_is_retried() -> None:
    provider, completions, _ = make_provider([status_error(500), VALID_PLAN])
    await provider.complete("classify", MESSAGES, Plan)
    assert len(completions.requests) == 2


async def test_gives_up_after_max_retries() -> None:
    provider, completions, sleeps = make_provider(
        [status_error(429)] * 3, llm_max_retries=2)
    with pytest.raises(LLMError, match="after 3 attempts"):
        await provider.complete("classify", MESSAGES, Plan)
    assert len(completions.requests) == 3
    assert len(sleeps) == 2


async def test_bad_request_is_not_retried() -> None:
    provider, completions, sleeps = make_provider([status_error(400), VALID_PLAN])
    with pytest.raises(LLMError, match="rejected the request \\(400\\)"):
        await provider.complete("classify", MESSAGES, Plan)
    assert len(completions.requests) == 1
    assert sleeps == []


def test_strict_json_schema_closes_nested_objects() -> None:
    schema = strict_json_schema(Outer)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["plan", "note"]  # optional fields required too
    nested = schema["$defs"]["Plan"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["sql", "assumptions"]


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(LLMError, match="GROQ_API_KEY is not set"):
        GroqProvider(Settings(groq_api_key=None))


def test_get_provider_returns_an_llm_provider() -> None:
    provider = get_provider(Settings(groq_api_key="gsk_test"))
    assert isinstance(provider, GroqProvider)
    typed: LLMProvider = provider  # satisfies the Protocol
    assert callable(typed.complete)
