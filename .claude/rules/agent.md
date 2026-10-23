---
paths:
  - "src/askauto/agent/**/*.py"
---

# Agent rules

- State is one Pydantic model in state.py. Nodes take state, return state.
- Nodes must not import sqlite3 or aiosqlite. All data access via MCP client.
- Every LLM call passes a Pydantic output schema. Never parse prose.
- Conditional edges live in graph.py as named functions, not inline lambdas.
- Every node appends to state.trace so the API can return the path taken.
- Retry ceilings are enforced in the edge function, not inside the node.
