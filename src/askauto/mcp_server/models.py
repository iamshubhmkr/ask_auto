"""Pydantic contracts for the MCP tool surface.

No database imports, so the agent can import these to type what the tools return.
"""

from typing import Any, Literal

from pydantic import BaseModel

Scalar = str | int | float


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    is_primary_key: bool
    is_nullable: bool
    is_categorical: bool
    distinct_count: int
    allowed_values: list[Scalar] | None  # exact values; use these spellings verbatim
    sample_values: list[Scalar] | None  # min, median, max when not categorical
    description: str


class TableInfo(BaseModel):
    name: str
    description: str
    grain: str
    row_count: int
    columns: list[ColumnInfo]


class JoinInfo(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str


class GlossaryTerm(BaseModel):
    term: str
    sql_expression: str
    notes: str


class UnanswerableTopic(BaseModel):
    topic: str
    reason: str


class SchemaContext(BaseModel):
    """Everything a prompt needs about the data, built from the meta_* tables."""

    mode: Literal["full", "vector"]
    tables: list[TableInfo]
    joins: list[JoinInfo]
    glossary: list[GlossaryTerm]
    unanswerable: list[UnanswerableTopic]

    def catalog(self) -> dict[str, dict[str, str]]:
        """table -> column -> type, the shape the SQL guard validates against."""
        return {t.name: {c.name: c.data_type for c in t.columns} for t in self.tables}


class TableSummary(BaseModel):
    name: str
    description: str
    grain: str
    row_count: int


class QueryError(BaseModel):
    kind: Literal["rejected", "sqlite_error", "timeout"]
    message: str  # specific enough to feed back into the next SQL attempt


class QueryResult(BaseModel):
    """Rows only, never an answer. Zero rows is ok=True with row_count=0, not an error."""

    ok: bool
    sql: str  # what was executed (LIMIT applied), or what was rejected
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    row_count: int = 0
    row_cap_hit: bool = False  # True when the row cap may have cut the result short
    tables_used: list[str] = []
    error: QueryError | None = None


class SampleRows(BaseModel):
    table: str
    columns: list[str]
    rows: list[dict[str, Any]]
