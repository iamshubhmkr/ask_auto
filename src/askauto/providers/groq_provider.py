"""Groq implementation of LLMProvider. The only module that imports the Groq SDK."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import groq
from pydantic import ValidationError

from askauto.config import Settings
from askauto.providers.base import (
    CALL_SETTINGS,
    CallSite,
    LLMError,
    Message,
    T,
    strict_json_schema,
)

log = logging.getLogger(__name__)

MAX_BACKOFF_S = 8.0
RETRYABLE = (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError)


class GroqProvider:
    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`client` and `sleep` are injectable so tests never touch the network or wait."""
        if client is None:
            if settings.groq_api_key is None:
                raise LLMError("GROQ_API_KEY is not set. Add it to .env (see .env.example).")
            # SDK retries off: ours are explicit, logged in one place, and testable.
            client = groq.AsyncGroq(
                api_key=settings.groq_api_key.get_secret_value(),
                timeout=settings.llm_timeout_s,
                max_retries=0,
            )
        self.client = client
        self.sleep = sleep
        self.model = settings.groq_model
        self.max_retries = settings.llm_max_retries

    async def complete(self, call: CallSite, messages: list[Message], schema: type[T]) -> T:
        settings = CALL_SETTINGS[call]
        response = await self._create_with_retry(
            model=self.model,
            messages=[m.model_dump() for m in messages],
            temperature=settings.temperature,
            max_completion_tokens=settings.max_output_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": strict_json_schema(schema),
                    "strict": True,
                },
            },
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length":
            raise LLMError(
                f"{call}: {self.model} hit the {settings.max_output_tokens}-token output cap "
                f"before finishing {schema.__name__} -- raw: {content[:200]!r}"
            )
        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            raise LLMError(
                f"{call}: {self.model} returned output that does not match "
                f"{schema.__name__}: {exc.errors()[:3]} -- raw: {content[:500]!r}"
            ) from exc

    async def _create_with_retry(self, **request: Any) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return await self.client.chat.completions.create(**request)
            except RETRYABLE as exc:
                if attempt == self.max_retries:
                    raise LLMError(
                        f"Groq call failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                delay = backoff_seconds(exc, attempt)
                # Retries add latency the agent trace cannot see; make them visible.
                log.warning("Groq %s (attempt %d/%d), retrying in %.1fs",
                            type(exc).__name__, attempt + 1, self.max_retries + 1, delay)
                await self.sleep(delay)
            except groq.APIStatusError as exc:
                # Groq checks strict-schema output after generation and answers 400
                # json_validate_failed when the model missed a field. That is a bad
                # sample, not a bad request, so it gets the same retry budget.
                if is_schema_miss(exc) and attempt < self.max_retries:
                    log.warning("Groq json_validate_failed (attempt %d/%d), retrying",
                                attempt + 1, self.max_retries + 1)
                    continue
                # Any other 4xx: retrying will not help.
                raise LLMError(f"Groq rejected the request ({exc.status_code}): {exc}") from exc
        raise AssertionError("unreachable")


def is_schema_miss(exc: Exception) -> bool:
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    return isinstance(error, dict) and error.get("code") == "json_validate_failed"


def backoff_seconds(exc: Exception, attempt: int) -> float:
    """Honour Retry-After when Groq sends one; otherwise exponential with jitter."""
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after is not None:
        try:
            return min(float(retry_after), MAX_BACKOFF_S)
        except ValueError:
            pass
    return min(2.0**attempt, MAX_BACKOFF_S) + random.uniform(0, 0.25)
