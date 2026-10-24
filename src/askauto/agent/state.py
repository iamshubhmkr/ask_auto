"""The agent's single Pydantic state model, plus the schemas each LLM call returns.

Nodes take this state and return the fields they change. `trace` accumulates:
each node returns its own entry and LangGraph appends it.
"""

import operator
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from askauto.mcp_server.models import QueryResult, SchemaContext


class Classification(BaseModel):
    """classify output: a small enum plus the reason."""

    label: Literal["answerable", "ambiguous", "unanswerable"]
    reason: str = Field(description="One sentence. For unanswerable, name what data is missing.")
    clarifying_question: str | None = Field(
        description="Only for ambiguous: the single question to ask the user. Otherwise null."
    )


class SQLPlan(BaseModel):
    """generate_sql output (shape fixed by the spec)."""

    sql: str = Field(description="One SQLite SELECT or WITH query. No trailing semicolon.")
    tables_used: list[str]
    assumptions: list[str] = Field(
        description="Every interpretation made, e.g. 'last year = 2025', "
        "'revenue = net revenue', or how a user's wording was mapped to an allowed value."
    )


class Answer(BaseModel):
    """synthesize output. The prose lives in a field; nothing parses free text."""

    text: str


class SQLAttempt(BaseModel):
    plan: SQLPlan
    guard_ok: bool | None = None  # agent-side validation result
    error: str | None = None  # why this attempt failed, fed back to the next one


class FigureCheck(BaseModel):
    text: str  # the figure as written in the answer, e.g. "$167.0M"
    value: float
    matched: bool


class Verification(BaseModel):
    ok: bool
    figures: list[FigureCheck]

    @property
    def unsupported(self) -> list[str]:
        return [f.text for f in self.figures if not f.matched]


class AgentState(BaseModel):
    question: str
    classification: Classification | None = None
    schema_context: SchemaContext | None = None
    sql_attempts: list[SQLAttempt] = []
    result: QueryResult | None = None  # rows from the last executed attempt
    outcome: Literal["rows", "zero_rows", "error"] | None = None
    answer: Answer | None = None
    verification: Verification | None = None
    retry_count: int = 0  # SQL attempts beyond the first
    synth_retry_count: int = 0  # synthesize calls beyond the first
    trace: Annotated[list[str], operator.add] = []

    @property
    def current_attempt(self) -> SQLAttempt | None:
        return self.sql_attempts[-1] if self.sql_attempts else None
