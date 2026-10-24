"""Database access for the MCP server: the only runtime code that opens SQLite.

The connection is opened read-only, so even SQL that slipped past the guard
cannot write.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from askauto.mcp_server.models import (
    ColumnInfo,
    GlossaryTerm,
    JoinInfo,
    SchemaContext,
    TableInfo,
    UnanswerableTopic,
)


async def open_readonly(path: Path) -> aiosqlite.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run scripts/build_database.py and scripts/build_metadata.py."
        )
    return await aiosqlite.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _unique(names: list[str]) -> list[str]:
    """Make column names unique so no value is lost when rows become dicts."""
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


async def fetch(
    conn: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()
) -> tuple[list[str], list[dict[str, Any]]]:
    async with conn.execute(sql, params) as cursor:
        rows = await cursor.fetchall()
        columns = _unique([d[0] for d in cursor.description or []])
    return columns, [dict(zip(columns, row, strict=True)) for row in rows]


async def fetch_with_timeout(
    conn: aiosqlite.Connection, sql: str, timeout_s: float
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run a query; on timeout, interrupt SQLite so the worker thread is freed too."""
    try:
        return await asyncio.wait_for(fetch(conn, sql), timeout_s)
    except TimeoutError:
        await conn.interrupt()
        raise


async def load_schema_context(
    conn: aiosqlite.Connection, mode: Literal["full", "vector"]
) -> SchemaContext:
    """Assemble the full metadata bundle from the meta_* tables."""

    def loads(value: str | None) -> list | None:
        return json.loads(value) if value is not None else None

    _, column_rows = await fetch(conn, "SELECT * FROM meta_columns ORDER BY rowid")
    columns: dict[str, list[ColumnInfo]] = {}
    for r in column_rows:
        columns.setdefault(r["table_name"], []).append(ColumnInfo(
            name=r["column_name"], data_type=r["data_type"],
            is_primary_key=bool(r["is_primary_key"]), is_nullable=bool(r["is_nullable"]),
            is_categorical=bool(r["is_categorical"]), distinct_count=r["distinct_count"],
            allowed_values=loads(r["allowed_values"]),
            sample_values=loads(r["sample_values"]), description=r["description"],
        ))

    _, table_rows = await fetch(conn, "SELECT * FROM meta_tables ORDER BY rowid")
    _, join_rows = await fetch(conn, "SELECT * FROM meta_joins ORDER BY rowid")
    _, glossary_rows = await fetch(conn, "SELECT * FROM meta_glossary ORDER BY rowid")
    _, unanswerable_rows = await fetch(conn, "SELECT * FROM meta_unanswerable ORDER BY rowid")

    return SchemaContext(
        mode=mode,
        tables=[TableInfo(name=r["table_name"], description=r["description"],
                          grain=r["grain"], row_count=r["row_count"],
                          columns=columns.get(r["table_name"], []))
                for r in table_rows],
        joins=[JoinInfo(**r) for r in join_rows],
        glossary=[GlossaryTerm(**r) for r in glossary_rows],
        unanswerable=[UnanswerableTopic(**r) for r in unanswerable_rows],
    )
