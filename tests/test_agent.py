"""Agent tests: the real graph and a real in-process MCP server on a temp database,
with the LLM provider mocked (never call Groq in a unit test).

Every backward and sideways edge has a test that forces it to fire.
"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

from askauto.agent import graph as graph_module
from askauto.agent import prompts
from askauto.agent.graph import MAX_SQL_ATTEMPTS, NODE_ORDER, build_graph, run_agent
from askauto.agent.nodes import AgentNodes
from askauto.agent.state import AgentState, Answer, Classification, SQLPlan
from askauto.config import Settings
from askauto.mcp_client import McpTools
from askauto.mcp_server.models import ColumnInfo, SchemaContext, TableInfo
from askauto.mcp_server.server import create_server
from askauto.providers import Message

REPO_ROOT = Path(__file__).resolve().parents[1]
NET_SQL = "SELECT SUM(sale_price - discount_amount) AS net_revenue FROM sales"
GOOD_ANSWER = "Net revenue was $167.0M ($166,989,244.64)."
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

    def count(self, call: str) -> int:
        return sum(1 for c, _, _ in self.calls if c == call)

    def prompt(self, call: str, index: int = 0) -> str:
        messages = [msgs for c, msgs, _ in self.calls if c == call][index]
        return "\n".join(m.content for m in messages)


def plan(sql: str = NET_SQL) -> SQLPlan:
    return SQLPlan(sql=sql, tables_used=["sales"], assumptions=["revenue = net revenue"])


def answers(*texts: str) -> list[Answer]:
    return [Answer(text=t) for t in texts]


async def ask(db_path: Path, provider: FakeProvider, question: str = "What was net revenue?"
              ) -> tuple[AgentState, McpTools]:
    async with McpTools.connect(create_server(Settings(database_path=db_path))) as tools:
        startup = await tools.get_schema_context()
        tools.calls.clear()  # count only what the graph does
        state = await run_agent(build_graph(AgentNodes(provider, tools, startup)), question)
        return state, tools


def steps(state: AgentState) -> list[str]:
    return [entry.split(":")[0] for entry in state.trace]


# --- Happy path -------------------------------------------------------------------


async def test_happy_path_runs_all_eight_nodes(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE], generate_sql=[plan()],
                            synthesize=answers(GOOD_ANSWER))
    state, tools = await ask(db_path, provider)
    assert steps(state) == list(NODE_ORDER)
    assert state.status == "answered"
    assert state.result.rows == [{"net_revenue": 166989244.64}]
    assert state.retry_count == 0 and state.synth_retry_count == 0
    # classify needs no MCP call; the cached schema bundle serves load_context.
    assert tools.calls == ["run_sql"]
    assert [(call, schema) for call, _, schema in provider.calls] == [
        ("classify", Classification), ("generate_sql", SQLPlan), ("synthesize", Answer)]


async def test_prompts_carry_the_metadata(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE], generate_sql=[plan()],
                            synthesize=answers(GOOD_ANSWER))
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


def test_edges_are_named_functions_not_lambdas() -> None:
    source = Path(graph_module.__file__).read_text(encoding="utf-8")
    assert "lambda" not in source


# --- Sideways: classify -> refuse / clarify --------------------------------------------


async def test_unanswerable_is_refused_without_touching_data(db_path: Path) -> None:
    provider = FakeProvider(classify=[Classification(
        label="unanswerable", reason="There is no credit score data.",
        clarifying_question=None)])
    state, tools = await ask(db_path, provider, "What is the average credit score of buyers?")
    assert steps(state) == ["classify", "refuse"]
    assert state.status == "refused"
    assert tools.calls == []
    assert "no credit score data" in state.answer.text
    assert "Only the finance type" in state.answer.text  # reason from meta_unanswerable


async def test_ambiguous_asks_for_clarification(db_path: Path) -> None:
    provider = FakeProvider(classify=[Classification(
        label="ambiguous", reason="'best' could mean units or revenue",
        clarifying_question="Do you mean best by units sold or by net revenue?")])
    state, tools = await ask(db_path, provider, "Which dealer is best?")
    assert steps(state) == ["classify", "clarify"]
    assert state.status == "clarification_needed"
    assert state.answer.text == "Do you mean best by units sold or by net revenue?"
    assert tools.calls == []


# --- Backwards: SQL retries ------------------------------------------------------


async def test_validate_failure_retries_with_the_error(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE],
                            generate_sql=[plan("SELECT * FROM meta_glossary"), plan()],
                            synthesize=answers(GOOD_ANSWER))
    state, tools = await ask(db_path, provider)
    assert state.status == "answered"
    assert state.retry_count == 1
    assert steps(state)[2:5] == ["generate_sql", "validate", "generate_sql"]
    assert "Table 'meta_glossary' is not queryable" in provider.prompt("generate_sql", 1)
    assert tools.calls == ["run_sql"]  # the bad attempt never reached the server


async def test_misspelled_category_retries_with_allowed_values(db_path: Path) -> None:
    # Trap 2 caught before execution instead of silently returning zero rows.
    bad = "SELECT COUNT(*) AS n FROM sales JOIN dealers USING (dealer_id) " \
          "WHERE region = 'North East'"
    provider = FakeProvider(classify=[ANSWERABLE],
                            generate_sql=[plan(bad), plan(bad.replace("North East", "Northeast"))],
                            synthesize=answers("The Northeast count is in the rows."))
    state, _ = await ask(db_path, provider, "How many cars did the North East sell?")
    assert state.status == "answered"
    feedback = provider.prompt("generate_sql", 1)
    assert "'North East' is not an allowed value for region" in feedback
    assert "'Northeast'" in feedback
    assert state.result.rows[0]["n"] > 0


async def test_execution_error_retries_and_shares_the_counter(db_path: Path) -> None:
    # Passes the agent-side check, fails inside SQLite.
    provider = FakeProvider(classify=[ANSWERABLE],
                            generate_sql=[plan("SELECT json_extract(dealer_name, '$') AS j "
                                               "FROM dealers"), plan()],
                            synthesize=answers(GOOD_ANSWER))
    state, tools = await ask(db_path, provider)
    assert state.status == "answered"
    assert state.retry_count == 1
    assert tools.calls == ["run_sql", "run_sql"]
    assert "sqlite_error" in provider.prompt("generate_sql", 1)


async def test_sql_ceiling_gives_up_honestly(db_path: Path) -> None:
    bad = plan("SELECT * FROM meta_glossary")
    provider = FakeProvider(classify=[ANSWERABLE], generate_sql=[bad] * MAX_SQL_ATTEMPTS)
    state, tools = await ask(db_path, provider)
    assert provider.count("generate_sql") == MAX_SQL_ATTEMPTS == 3
    assert steps(state)[-1] == "give_up"
    assert state.status == "failed"
    assert "after 3 attempts" in state.answer.text
    assert "meta_glossary" in state.answer.text
    assert tools.calls == []


# --- Zero rows: explained, not "0" ---------------------------------------------------


async def test_zero_rows_are_explained_not_reported_as_zero(db_path: Path) -> None:
    provider = FakeProvider(
        classify=[ANSWERABLE],
        generate_sql=[plan("SELECT sale_id FROM sales WHERE sale_date > '2030-01-01'")],
        synthesize=answers("There were 0 sales.",  # a measured zero it is not
                           "No sales are recorded after that date."))
    state, _ = await ask(db_path, provider, "How many sales after 2030?")
    assert state.outcome == "zero_rows"
    assert "returned no rows" in provider.prompt("synthesize", 0)
    assert state.synth_retry_count == 1
    assert state.status == "answered"
    assert state.answer.text == "No sales are recorded after that date."


# --- Backwards: verify -> synthesize (once) ---------------------------------------------


async def test_unverified_figure_triggers_one_resynthesis(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE], generate_sql=[plan()],
                            synthesize=answers("Net revenue was $250M.", GOOD_ANSWER))
    state, _ = await ask(db_path, provider)
    assert steps(state)[-4:] == ["synthesize", "verify", "synthesize", "verify"]
    assert "$250M" in provider.prompt("synthesize", 1)  # told what to fix
    assert state.status == "answered"
    assert state.synth_retry_count == 1


async def test_second_verify_failure_adds_a_caveat(db_path: Path) -> None:
    provider = FakeProvider(classify=[ANSWERABLE], generate_sql=[plan()],
                            synthesize=answers("Net revenue was $250M.",
                                               "Net revenue was $250M."))
    state, _ = await ask(db_path, provider)
    assert provider.count("synthesize") == 2
    assert steps(state)[-1] == "add_caveat"
    assert state.status == "unverified"
    assert state.answer.text.startswith("Net revenue was $250M.")
    assert "could not be verified against the query results: $250M" in state.answer.text
