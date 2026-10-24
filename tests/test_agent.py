"""Agent tests: the real graph and a real in-process MCP server on a temp database,
with the LLM provider mocked (never call Groq in a unit test)."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

from askauto.agent import prompts
from askauto.agent.graph import NODE_ORDER, AgentError, build_graph, run_agent
from askauto.agent.nodes import AgentNodes
from askauto.agent.state import AgentState, Answer, Classification, SQLPlan
from askauto.config import Settings
from askauto.mcp_client import McpTools
from askauto.mcp_server.models import ColumnInfo, SchemaContext, TableInfo
from askauto.mcp_server.server import create_server
from askauto.providers import Message

REPO_ROOT = Path(__file__).resolve().parents[1]
NET_SQL = "SELECT SUM(sale_price - discount_amount) AS net_revenue FROM sales"
ANSWERABLE = Classification(label="answerable", reason="sales data covers it",
                            clarifying_question=None)


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


class FakeProvider:
    """Returns queued responses per call site and records every call."""

    def __init__(self, **responses: list[BaseModel]) -> None:
        self.responses = {call: list(items) for call, items in responses.items()}
        self.calls: list[tuple[str, list[Message], type[BaseModel]]] = []

    async def complete(self, call: str, messages: list[Message], schema: type) -> Any:
        self.calls.append((call, messages, schema))
        response = self.responses[call].pop(0)
        assert isinstance(response, schema)
        return response

    def prompt(self, call: str) -> str:
        return "\n".join(m.content for c, msgs, _ in self.calls if c == call for m in msgs)


def plan(sql: str = NET_SQL) -> SQLPlan:
    return SQLPlan(sql=sql, tables_used=["sales"], assumptions=["revenue = net revenue"])


async def ask(db_path: Path, provider: FakeProvider, question: str = "What was net revenue?"
              ) -> tuple[AgentState | AgentError, McpTools]:
    async with McpTools.connect(create_server(Settings(database_path=db_path))) as tools:
        startup = await tools.get_schema_context()
        tools.calls.clear()  # count only what the graph does
        graph = build_graph(AgentNodes(provider, tools, startup))
        try:
            return await run_agent(graph, question), tools
        except AgentError as exc:
            return exc, tools


def happy_provider(answer: str = "Net revenue was $167.0M ($166,989,244.64).") -> FakeProvider:
    return FakeProvider(classify=[ANSWERABLE], generate_sql=[plan()],
                        synthesize=[Answer(text=answer)])


async def test_happy_path_runs_all_eight_nodes(db_path: Path) -> None:
    state, _ = await ask(db_path, happy_provider())
    assert isinstance(state, AgentState)
    assert [entry.split(":")[0] for entry in state.trace] == list(NODE_ORDER)
    assert state.result.rows == [{"net_revenue": 166989244.64}]
    assert state.outcome == "rows"
    assert state.verification.ok
    assert state.retry_count == 0


async def test_each_call_site_gets_its_schema(db_path: Path) -> None:
    provider = happy_provider()
    await ask(db_path, provider)
    assert [(call, schema) for call, _, schema in provider.calls] == [
        ("classify", Classification), ("generate_sql", SQLPlan), ("synthesize", Answer)]


async def test_mcp_traffic(db_path: Path) -> None:
    # classify needs no MCP call; the cached schema bundle serves load_context.
    _, tools = await ask(db_path, happy_provider())
    assert tools.calls == ["run_sql"]


async def test_prompts_carry_the_metadata(db_path: Path) -> None:
    provider = happy_provider()
    await ask(db_path, provider)
    sql_prompt = provider.prompt("generate_sql")
    assert "'Northeast'" in sql_prompt  # trap 2: exact allowed value
    assert "SUM(sale_price - discount_amount)" in sql_prompt  # trap 1: glossary
    assert "one row per sale" in sql_prompt  # grain
    assert "'F-150'" in sql_prompt  # model override from tables.yaml
    assert "credit scores" in provider.prompt("classify")  # known-unanswerable list


def test_prompts_are_derived_from_schema_context() -> None:
    context = SchemaContext(mode="full", joins=[], glossary=[], unanswerable=[], tables=[
        TableInfo(name="widgets", description="Widget catalogue.", grain="one row per widget",
                  row_count=3, columns=[ColumnInfo(
                      name="colour", data_type="TEXT", is_primary_key=False, is_nullable=False,
                      is_categorical=True, distinct_count=2, allowed_values=["Red", "Blue"],
                      sample_values=None, description="Paint colour.")])])
    rendered = prompts.render_schema(context)
    assert "widgets -- one row per widget" in rendered
    assert "'Red', 'Blue'" in rendered


def test_prompt_source_has_no_hardcoded_schema() -> None:
    source = Path(prompts.__file__).read_text(encoding="utf-8")
    for name in ("dealers", "vehicles", "sales", "sale_price", "discount_amount",
                 "region", "Northeast", "fuel_type"):
        assert not re.search(rf"\b{name}\b", source), f"prompts.py names '{name}'"


async def test_unanswerable_fails_loudly_without_touching_data(db_path: Path) -> None:
    provider = FakeProvider(classify=[Classification(
        label="unanswerable", reason="no credit score data", clarifying_question=None)])
    error, tools = await ask(db_path, provider, "Average credit score of buyers?")
    assert isinstance(error, AgentError)
    assert "unanswerable" in str(error)
    assert tools.calls == []
    assert error.state.trace == ["classify: unanswerable - no credit score data"]


async def test_rejected_sql_fails_loudly(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE],
                            generate_sql=[plan("SELECT * FROM meta_glossary")])
    error, tools = await ask(db_path, provider)
    assert isinstance(error, AgentError)
    assert "Table 'meta_glossary' is not queryable" in str(error)
    assert tools.calls == []  # the agent-side check stopped it before MCP


async def test_zero_rows_fails_loudly_for_now(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE], generate_sql=[plan(
        "SELECT dealer_name FROM dealers WHERE region = 'North East'")])
    error, _ = await ask(db_path, provider)
    assert isinstance(error, AgentError)
    assert "0 rows" in str(error)


async def test_unverified_figure_fails_loudly(db_path: Path) -> None:
    error, _ = await ask(db_path, happy_provider("Net revenue was $250M."))
    assert isinstance(error, AgentError)
    assert "$250M" in str(error)
    assert error.state.verification.unsupported == ["$250M"]
