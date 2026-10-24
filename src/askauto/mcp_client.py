"""Typed MCP client used by the agent and the API. The runtime path to the data.

Wraps `mcp.Client` and turns tool results back into the Pydantic contracts from
`askauto.mcp_server.models`. Open it once and reuse it: spawning a stdio server
per request adds process startup to every answer.
"""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import Client, StdioServerParameters

from askauto.mcp_server.models import QueryResult, SchemaContext


class McpToolError(RuntimeError):
    """An MCP tool call failed outright (not a structured run_sql error)."""


def stdio_params() -> StdioServerParameters:
    """Launch `python -m askauto.mcp_server` as a subprocess speaking stdio."""
    return StdioServerParameters(command=sys.executable, args=["-m", "askauto.mcp_server"])


class McpTools:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls: list[str] = []  # tool names in call order, for traces and tests
        self._full_schema: SchemaContext | None = None

    @classmethod
    @asynccontextmanager
    async def connect(cls, server: Any) -> AsyncIterator["McpTools"]:
        """`server` is StdioServerParameters, a URL, or an in-process MCPServer (tests)."""
        async with Client(server) as client:
            yield cls(client)

    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        result = await self.client.call_tool(tool, args)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content)
            raise McpToolError(f"{tool} failed: {text}")
        return result.structured_content

    async def get_schema_context(self, question: str | None = None) -> SchemaContext:
        # In "full" mode the server ignores the question, so one fetch serves every call.
        if self._full_schema is not None:
            return self._full_schema
        context = SchemaContext.model_validate(
            await self._call("get_schema_context", {"question": question})
        )
        if context.mode == "full":
            self._full_schema = context
        return context

    async def run_sql(self, sql: str, limit: int = 500) -> QueryResult:
        return QueryResult.model_validate(await self._call("run_sql", {"sql": sql, "limit": limit}))
