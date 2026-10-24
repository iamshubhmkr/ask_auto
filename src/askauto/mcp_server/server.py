"""The AskAuto MCP server: four read-only tools over the database.

Returns rows, never answers: no LLM calls, no summarising. The database
connection and the metadata bundle are opened once in the lifespan handler.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiosqlite
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from askauto.config import Settings, get_settings
from askauto.mcp_server import db
from askauto.mcp_server.models import (
    QueryError,
    QueryResult,
    SampleRows,
    SchemaContext,
    TableSummary,
)
from askauto.mcp_server.sql_guard import ALLOWED_TABLES, Catalog, guard_sql

SAMPLE_ROWS_MAX = 20


@dataclass
class ServerState:
    settings: Settings
    conn: aiosqlite.Connection
    schema: SchemaContext
    catalog: Catalog


def state_of(ctx: Context) -> ServerState:
    return ctx.request_context.lifespan_context


def create_server(settings: Settings | None = None) -> MCPServer:
    """Build the server. Tests pass their own settings (temp DB, short timeout)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[ServerState]:
        if settings.schema_mode != "full":
            raise NotImplementedError(
                f"SCHEMA_MODE={settings.schema_mode!r} arrives in stage 2; use 'full'."
            )
        conn = await db.open_readonly(settings.resolved_database_path)
        try:
            schema = await db.load_schema_context(conn, settings.schema_mode)
            yield ServerState(settings=settings, conn=conn, schema=schema,
                              catalog=schema.catalog())
        finally:
            await conn.close()

    server = MCPServer(
        "askauto",
        instructions=(
            "Read-only access to the AskAuto dealer database (dealers, vehicles, sales). "
            "Call get_schema_context first: use its glossary for business terms and its "
            "allowed_values for exact category spellings."
        ),
        lifespan=lifespan,
    )

    @server.tool()
    async def get_schema_context(ctx: Context, question: str | None = None) -> SchemaContext:
        """Tables, columns (with allowed values), joins, glossary and known-unanswerable
        topics. Stage 1 returns everything; `question` is accepted for stage 2 retrieval."""
        return state_of(ctx).schema

    @server.tool()
    async def run_sql(ctx: Context, sql: str, limit: int = 500) -> QueryResult:
        """Validate and run one read-only SELECT/WITH query. Returns rows, or a structured
        error (rejected | sqlite_error | timeout) whose message explains what to fix."""
        state = state_of(ctx)
        max_rows = max(1, min(limit, state.settings.sql_max_rows))
        guarded = guard_sql(sql, state.catalog, max_rows)
        if not guarded.ok:
            return QueryResult(ok=False, sql=sql,
                               error=QueryError(kind="rejected", message=guarded.reason))
        try:
            columns, rows = await db.fetch_with_timeout(
                state.conn, guarded.sql, state.settings.sql_timeout_s
            )
        except TimeoutError:
            return QueryResult(ok=False, sql=guarded.sql, tables_used=guarded.tables_used,
                               error=QueryError(kind="timeout", message=(
                                   f"Query exceeded {state.settings.sql_timeout_s}s and was "
                                   "cancelled. Simplify it or filter more.")))
        except aiosqlite.Error as exc:
            return QueryResult(ok=False, sql=guarded.sql, tables_used=guarded.tables_used,
                               error=QueryError(kind="sqlite_error", message=str(exc)))
        return QueryResult(ok=True, sql=guarded.sql, columns=columns, rows=rows,
                           row_count=len(rows), row_cap_hit=len(rows) >= max_rows,
                           tables_used=guarded.tables_used)

    @server.tool()
    async def list_tables(ctx: Context) -> list[TableSummary]:
        """The queryable tables with description, grain and row count."""
        return [TableSummary(name=t.name, description=t.description, grain=t.grain,
                             row_count=t.row_count) for t in state_of(ctx).schema.tables]

    @server.tool()
    async def sample_rows(ctx: Context, table: str, n: int = 5) -> SampleRows:
        """Up to 20 example rows from one queryable table."""
        name = table.strip().lower()
        if name not in ALLOWED_TABLES:
            raise ToolError(f"Table '{table}' is not queryable. "
                            f"Allowed tables: {', '.join(sorted(ALLOWED_TABLES))}.")
        n = max(1, min(n, SAMPLE_ROWS_MAX))
        # Safe to format: name is one of three fixed identifiers checked above.
        columns, rows = await db.fetch(state_of(ctx).conn,
                                       f"SELECT * FROM {name} ORDER BY rowid LIMIT ?", (n,))
        return SampleRows(table=name, columns=columns, rows=rows)

    return server
