"""LLMProvider interface and Groq implementation.

Callers use `get_provider()` and the types from `base`; only
`groq_provider.py` imports the Groq SDK.
"""

from askauto.config import Settings, get_settings
from askauto.providers.base import (
    CALL_SETTINGS,
    CallSite,
    LLMError,
    LLMProvider,
    Message,
    strict_json_schema,
)

__all__ = [
    "CALL_SETTINGS",
    "CallSite",
    "LLMError",
    "LLMProvider",
    "Message",
    "get_provider",
    "strict_json_schema",
]


def get_provider(settings: Settings | None = None) -> LLMProvider:
    from askauto.providers.groq_provider import GroqProvider

    return GroqProvider(settings or get_settings())
