"""Ask one question end to end: python -m askauto.agent "What was net revenue last year?"

Uses the real Groq provider and launches the MCP server over stdio.
"""

import asyncio
import json
import sys
import time

from askauto.agent.graph import AgentError, build_graph, run_agent
from askauto.agent.nodes import AgentNodes
from askauto.agent.state import AgentState
from askauto.config import get_settings
from askauto.mcp_client import McpTools, stdio_params
from askauto.providers import get_provider


def show(state: AgentState) -> None:
    print("Trace:")
    for entry in state.trace:
        print(f"  {entry}")
    if attempt := state.current_attempt:
        print(f"\nSQL: {state.result.sql if state.result else attempt.plan.sql}")
        print(f"Assumptions: {attempt.plan.assumptions}")
    if state.result and state.result.ok:
        print(f"Rows ({state.result.row_count}): "
              f"{json.dumps(state.result.rows[:10], default=str)}")
    if state.answer:
        print(f"\nAnswer: {state.answer.text}")


async def main(question: str) -> int:
    settings = get_settings()
    provider = get_provider(settings)
    async with McpTools.connect(stdio_params()) as tools:
        startup_schema = await tools.get_schema_context()
        graph = build_graph(AgentNodes(provider, tools, startup_schema, settings.sql_max_rows))
        start = time.perf_counter()
        try:
            state = await run_agent(graph, question)
        except AgentError as exc:
            show(exc.state)
            print(f"\nFAILED: {exc}")
            return 1
        show(state)
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"\nLatency: {elapsed_ms:.0f} ms (model {settings.groq_model})")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        sys.exit('usage: python -m askauto.agent "your question"')
    sys.exit(asyncio.run(main(sys.argv[1])))
