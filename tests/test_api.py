"""API tests: the real FastAPI app with a fake LLM provider and an in-process MCP
server on a temp database. TestClient runs the lifespan, like uvicorn does."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from askauto.agent.state import Answer, Classification, SQLPlan
from askauto.api import create_app
from askauto.config import Settings
from askauto.mcp_server.server import create_server
from askauto.providers import LLMError, Message

REPO_ROOT = Path(__file__).resolve().parents[1]
NET_SQL = "SELECT SUM(sale_price - discount_amount) AS net_revenue FROM sales"
ANSWERABLE = Classification(label="answerable", reason="covered", clarifying_question=None)
CONTRACT = {"answer", "sql", "assumptions", "rows", "trace", "latency_ms", "status"}


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    path = tmp_path_factory.mktemp("db") / "askauto.db"
    load_script("build_database").build(path)
    load_script("build_metadata").build(path, REPO_ROOT / "metadata")
    return Settings(database_path=path, groq_model="fake-model")


class FakeProvider:
    """Queued responses per call site; an Exception in the queue is raised."""

    def __init__(self, **responses: list[Any]) -> None:
        self.responses = {call: list(items) for call, items in responses.items()}

    async def complete(self, call: str, messages: list[Message], schema: type) -> BaseModel:
        response = self.responses[call].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client_for(settings: Settings, provider: FakeProvider) -> TestClient:
    app = create_app(settings=settings, provider=provider,
                     mcp_target=create_server(settings))
    return TestClient(app)


@pytest.fixture
def answered(settings: Settings) -> Iterator[TestClient]:
    provider = FakeProvider(
        classify=[ANSWERABLE] * 2,
        generate_sql=[SQLPlan(sql=NET_SQL, tables_used=["sales"],
                              assumptions=["revenue = net revenue"])] * 2,
        synthesize=[Answer(text="Net revenue was $167.0M.")] * 2)
    with client_for(settings, provider) as client:
        yield client


def test_ask_returns_the_contract(answered: TestClient) -> None:
    response = answered.post("/ask", json={"question": "What was net revenue?"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == CONTRACT
    assert body["answer"] == "Net revenue was $167.0M."
    assert body["sql"].startswith(NET_SQL)
    assert body["assumptions"] == ["revenue = net revenue"]
    assert body["rows"] == [{"net_revenue": 166989244.64}]
    assert body["trace"][0].startswith("classify:")
    assert isinstance(body["latency_ms"], int)
    assert body["status"] == "answered"


def test_one_mcp_session_serves_every_request(answered: TestClient) -> None:
    runtime = answered.app.state.runtime
    tools_before = runtime.tools
    for _ in range(2):
        assert answered.post("/ask", json={"question": "Net revenue?"}).status_code == 200
    assert answered.app.state.runtime.tools is tools_before
    # Schema fetched once at startup; each request only runs SQL.
    assert runtime.tools.calls == ["get_schema_context", "run_sql", "run_sql"]


@pytest.mark.parametrize("label, status", [
    ("unanswerable", "refused"), ("ambiguous", "clarification_needed")])
def test_refusal_and_clarification_carry_no_sql(
    settings: Settings, label: str, status: str
) -> None:
    provider = FakeProvider(classify=[Classification(
        label=label, reason="no credit data", clarifying_question="Which metric?")])
    with client_for(settings, provider) as client:
        body = client.post("/ask", json={"question": "Average credit score?"}).json()
    assert body["status"] == status
    assert body["sql"] is None and body["rows"] == [] and body["assumptions"] == []


def test_zero_rows_still_shows_the_sql(settings: Settings) -> None:
    sql = "SELECT sale_id FROM sales WHERE sale_date > '2030-01-01'"
    provider = FakeProvider(
        classify=[ANSWERABLE],
        generate_sql=[SQLPlan(sql=sql, tables_used=["sales"], assumptions=[])],
        synthesize=[Answer(text="No sales are recorded after that date.")])
    with client_for(settings, provider) as client:
        body = client.post("/ask", json={"question": "Sales after 2030?"}).json()
    assert body["status"] == "answered"
    assert body["sql"].startswith(sql) and body["rows"] == []


def test_llm_failure_is_a_clean_502(settings: Settings) -> None:
    provider = FakeProvider(classify=[LLMError("Groq call failed after 4 attempts")])
    with client_for(settings, provider) as client:
        response = client.post("/ask", json={"question": "Net revenue?"})
    assert response.status_code == 502
    assert response.json() == {
        "detail": "LLM provider error: Groq call failed after 4 attempts"}


def test_empty_question_is_rejected(answered: TestClient) -> None:
    assert answered.post("/ask", json={"question": ""}).status_code == 422


def test_health(answered: TestClient) -> None:
    response = answered.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "fake-model", "schema_mode": "full",
                               "tables": ["dealers", "vehicles", "sales"]}
    # It asked the live server, not the startup cache.
    assert answered.app.state.runtime.tools.calls[-1] == "list_tables"


def test_health_reports_a_dead_mcp_session(answered: TestClient) -> None:
    async def broken() -> None:
        raise RuntimeError("stdio pipe closed")

    answered.app.state.runtime.tools.list_tables = broken
    response = answered.get("/health")
    assert response.status_code == 503
    assert "stdio pipe closed" in response.json()["detail"]
