"""Provider-agnostic LLM interface. Nothing here knows about Groq.

Three call sites, three jobs (docs/PROBLEM_STATEMENT.md). Their settings live
here, not in a provider, so a second provider (e.g. Bedrock) gets the same
temperatures for free.

Every call returns a validated Pydantic instance. Free prose, such as the
synthesized answer, still travels inside a schema field; nothing parses text.
"""

from copy import deepcopy
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

CallSite = Literal["classify", "generate_sql", "synthesize"]


class CallSettings(BaseModel):
    temperature: float
    # Upper bound on output (reasoning models count their reasoning here too).
    # Stops a looping model from burning the whole latency budget.
    max_output_tokens: int


CALL_SETTINGS: dict[CallSite, CallSettings] = {
    "classify": CallSettings(temperature=0.0, max_output_tokens=2048),
    "generate_sql": CallSettings(temperature=0.0, max_output_tokens=4096),
    "synthesize": CallSettings(temperature=0.4, max_output_tokens=2048),
}


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMError(RuntimeError):
    """The provider could not return a valid instance of the requested schema."""


class LLMProvider(Protocol):
    async def complete(self, call: CallSite, messages: list[Message], schema: type[T]) -> T:
        """Run one call site and return a validated instance of `schema`."""
        ...


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema adapted to strict structured-output rules.

    Every object gets additionalProperties: false and lists all of its
    properties as required, including nested models under $defs.
    """
    result = deepcopy(schema.model_json_schema())

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for item in node:
                close(item)

    close(result)
    return result
