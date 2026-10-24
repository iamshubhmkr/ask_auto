"""MCP server tests: the four tools over the real MCP protocol.

Runs against a temp database built by the build scripts. Most tests connect
in-process (Client(server)); one launches `python -m askauto.mcp_server` over stdio.
"""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

from askauto.config import Settings
from askauto.mcp_server import db
from askauto.mcp_server.server import create_server

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNAWAY_SQL = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
               "SELECT COUNT(*) FROM c")


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("db") / "askauto.db"
    load_script("build_database").build(path)
    load_script("build_metadata").build(path, REPO_ROOT / "metadata")
    return path


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return Settings(database_path=db_path, sql_timeout_s=0.5)


async def call(settings: Settings, tool: str, args: dict[str, Any] | None = None) -> Any:
    async with Client(create_server(settings)) as client:
        return await client.call_tool(tool, args or {})


async def run_sql(settings: Settings, sql: str, **kwargs: Any) -> dict[str, Any]:
    result = await call(settings, "run_sql", {"sql": sql, **kwargs})
    return result.structured_content


async def test_four_tools_listed(settings: Settings) -> None:
    async with Client(create_server(settings)) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools.tools} == {
        "get_schema_context", "run_sql", "list_tables", "sample_rows"}


async def test_schema_context_carries_both_traps(settings: Settings) -> None:
    schema = (await call(settings, "get_schema_context",
                         {"question": "ignored in stage 1"})).structured_content
    dealers = next(t for t in schema["tables"] if t["name"] == "dealers")
    region = next(c for c in dealers["columns"] if c["name"] == "region")
    assert region["allowed_values"] == ["Midwest", "Northeast", "Southeast", "West"]
    glossary = {g["term"]: g["sql_expression"] for g in schema["glossary"]}
    assert glossary["revenue"] == glossary["net revenue"] == "SUM(sale_price - discount_amount)"
    assert len(schema["unanswerable"]) == 8
    assert len(schema["joins"]) == 2


async def test_run_sql_returns_rows(settings: Settings) -> None:
    result = await run_sql(settings, "SELECT region, COUNT(*) AS units FROM sales "
                                     "JOIN dealers USING (dealer_id) GROUP BY region")
    assert result["ok"] and result["error"] is None
    assert result["row_count"] == 4
    assert sum(r["units"] for r in result["rows"]) == 5000
    assert result["sql"].endswith("LIMIT 500")
    assert result["tables_used"] == ["dealers", "sales"]


async def test_zero_rows_is_not_an_error(settings: Settings) -> None:
    result = await run_sql(settings, "SELECT dealer_name FROM dealers WHERE region = 'North East'")
    assert result["ok"] is True
    assert result["row_count"] == 0 and result["rows"] == []
    assert result["error"] is None


async def test_rejection_is_structured(settings: Settings) -> None:
    result = await run_sql(settings, "SELECT * FROM meta_glossary")
    assert result["ok"] is False
    assert result["error"]["kind"] == "rejected"
    assert "meta_glossary" in result["error"]["message"]


async def test_sqlite_runtime_error_is_structured(settings: Settings) -> None:
    # Passes the guard, fails inside SQLite.
    result = await run_sql(settings, "SELECT json_extract(dealer_name, '$') FROM dealers")
    assert result["ok"] is False
    assert result["error"]["kind"] == "sqlite_error"
    assert "JSON" in result["error"]["message"]


async def test_runaway_query_times_out_and_server_recovers(settings: Settings) -> None:
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("run_sql", {"sql": RUNAWAY_SQL})).structured_content
        assert result["ok"] is False
        assert result["error"]["kind"] == "timeout"
        # The interrupted connection must still serve the next query.
        after = (await client.call_tool("run_sql", {"sql": "SELECT COUNT(*) AS n FROM sales"})
                 ).structured_content
    assert after["ok"] and after["rows"] == [{"n": 5000}]


async def test_limit_argument_caps_rows(settings: Settings) -> None:
    result = await run_sql(settings, "SELECT sale_id FROM sales", limit=7)
    assert result["row_count"] == 7
    assert result["row_cap_hit"] is True
    assert result["sql"].endswith("LIMIT 7")


async def test_connection_is_read_only(db_path: Path) -> None:
    # Defence in depth: even SQL that skipped the guard cannot write.
    conn = await db.open_readonly(db_path)
    try:
        with pytest.raises(Exception, match="readonly"):
            await db.fetch(conn, "DELETE FROM sales")
    finally:
        await conn.close()


async def test_list_tables(settings: Settings) -> None:
    result = (await call(settings, "list_tables")).structured_content["result"]
    assert {t["name"]: t["row_count"] for t in result} == {
        "dealers": 20, "vehicles": 40, "sales": 5000}
    assert all(t["description"] and t["grain"] for t in result)


async def test_sample_rows(settings: Settings) -> None:
    result = (await call(settings, "sample_rows", {"table": "Vehicles", "n": 3})
              ).structured_content
    assert result["table"] == "vehicles"
    assert len(result["rows"]) == 3
    assert "fuel_type" in result["columns"]


async def test_sample_rows_rejects_meta_tables(settings: Settings) -> None:
    result = await call(settings, "sample_rows", {"table": "meta_glossary"})
    assert result.is_error
    assert "not queryable" in result.content[0].text


async def test_stdio_round_trip(db_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "askauto.mcp_server"],
        env={**os.environ, "DATABASE_PATH": str(db_path)},
    )
    async with Client(params) as client:
        result = await client.call_tool("run_sql", {"sql": "SELECT COUNT(*) AS n FROM sales"})
    assert result.structured_content["rows"] == [{"n": 5000}]
