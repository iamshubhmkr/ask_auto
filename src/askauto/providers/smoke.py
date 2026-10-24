"""Smoke test against the real Groq API: one call per call site.

    python -m askauto.providers.smoke

Confirms the key in .env works and that each call site returns a validated
schema. Prompts are deliberately schema-free (no table definitions): building
real prompts from the meta_* tables is the agent's job (session 5).
"""

import asyncio
import sys
import time
from typing import Literal

from pydantic import BaseModel

from askauto.config import get_settings
from askauto.providers import CALL_SETTINGS, CallSite, Message, get_provider


class Classification(BaseModel):
    label: Literal["answerable", "ambiguous", "unanswerable"]
    reason: str


class SQLPlan(BaseModel):
    sql: str
    tables_used: list[str]
    assumptions: list[str]


class Answer(BaseModel):
    text: str


CASES: list[tuple[CallSite, type[BaseModel], str]] = [
    ("classify", Classification,
     "A car dealer group has sales data but no customer data. Classify the question "
     "'What is the average credit score of our buyers?'"),
    ("generate_sql", SQLPlan,
     "Write one SQLite query that counts the rows of a table named sales."),
    ("synthesize", Answer,
     "Question: 'How many cars did we sell?' Query result: [{'units': 5000}]. "
     "Answer in one sentence."),
]


async def main() -> int:
    settings = get_settings()
    provider = get_provider(settings)
    print(f"Model: {settings.groq_model}")
    for call, schema, prompt in CASES:
        start = time.perf_counter()
        result = await provider.complete(call, [Message(role="user", content=prompt)], schema)
        elapsed = time.perf_counter() - start
        print(f"\n[{call}] temperature={CALL_SETTINGS[call].temperature} "
              f"latency={elapsed:.2f}s -> {schema.__name__}")
        print(f"  {result.model_dump()}")
    print("\nOK: the key works and all three call sites return validated schemas.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
