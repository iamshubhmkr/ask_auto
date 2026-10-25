"""FastAPI app: POST /ask and GET /health.

Run: uvicorn askauto.api:app --reload

Everything expensive happens once, in the lifespan handler: settings, the MCP
session (one stdio subprocess for the life of the app), the cached schema
bundle and the compiled graph. A request only runs the graph.
"""

import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from askauto.agent.graph import build_graph, run_agent
from askauto.agent.nodes import AgentNodes
from askauto.agent.state import AgentState
from askauto.config import Settings, get_settings
from askauto.mcp_client import McpToolError, McpTools, stdio_params
from askauto.mcp_server.models import SchemaContext
from askauto.providers import LLMError, LLMProvider, get_provider


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskResponse(BaseModel):
    answer: str
    sql: str | None
    assumptions: list[str]
    rows: list[dict[str, Any]]
    trace: list[str]
    latency_ms: int
    # Not in the spec contract (additive, see docs/PROGRESS.md): lets clients and
    # evals tell an answer from a refusal, a clarification or a give-up.
    status: Literal["answered", "refused", "clarification_needed", "failed", "unverified"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model: str
    schema_mode: str
    tables: list[str]


@dataclass
class Runtime:
    """What the lifespan builds once and every request reuses."""

    settings: Settings
    tools: McpTools
    schema: SchemaContext
    graph: CompiledStateGraph


def to_response(state: AgentState, latency_ms: int) -> AskResponse:
    ran = state.result is not None and state.result.ok
    attempt = state.current_attempt
    return AskResponse(
        answer=state.answer.text,
        sql=state.result.sql if ran else None,
        assumptions=attempt.plan.assumptions if attempt else [],
        rows=state.result.rows if ran else [],
        trace=state.trace,
        latency_ms=latency_ms,
        status=state.status,
    )


def create_app(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    mcp_target: Any | None = None,
) -> FastAPI:
    """Defaults: real settings, the Groq provider, and a stdio MCP server subprocess.
    Tests pass a fake provider and an in-process MCP server."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or get_settings()
        async with AsyncExitStack() as stack:
            tools = await stack.enter_async_context(
                McpTools.connect(mcp_target or stdio_params()))
            schema = await tools.get_schema_context()
            nodes = AgentNodes(provider or get_provider(resolved), tools, schema,
                               resolved.sql_max_rows)
            app.state.runtime = Runtime(resolved, tools, schema, build_graph(nodes))
            yield

    app = FastAPI(title="AskAuto", lifespan=lifespan)

    @app.exception_handler(LLMError)
    async def llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": f"LLM provider error: {exc}"})

    @app.exception_handler(McpToolError)
    async def mcp_error(_: Request, exc: McpToolError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": f"Data service error: {exc}"})

    @app.post("/ask")
    async def ask(body: AskRequest, request: Request) -> AskResponse:
        runtime: Runtime = request.app.state.runtime
        start = time.perf_counter()
        state = await run_agent(runtime.graph, body.question)
        return to_response(state, round((time.perf_counter() - start) * 1000))

    @app.get("/health")
    async def health(request: Request) -> HealthResponse:
        runtime: Runtime = request.app.state.runtime
        # MCP ping is gone from the 2026-07-28 protocol, and list_tools may be served
        # from the client cache. A tool call always reaches the live server.
        try:
            tables = await runtime.tools.list_tables()
        except Exception as exc:  # any failure here means the MCP session is unusable
            raise HTTPException(503, f"MCP session unavailable: {exc}") from exc
        return HealthResponse(status="ok", model=runtime.settings.groq_model,
                              schema_mode=runtime.schema.mode,
                              tables=[t.name for t in tables])

    return app


app = create_app()
