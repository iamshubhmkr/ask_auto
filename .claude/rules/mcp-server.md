---
paths:
  - "src/askauto/mcp_server/**/*.py"
---

# MCP server rules

This is the only runtime component permitted to open the database.

- Every tool takes and returns a Pydantic model. No bare dicts.
- `run_sql` validates before executing: sqlglot AST parse, SELECT/WITH only,
  table allowlist of dealers/vehicles/sales, column existence against
  meta_columns, LIMIT injected if absent, 5s timeout.
- Validation is the boundary. Assume the agent's guard was bypassed.
- Return rows. Never summarise, interpret, or call an LLM here.
- Use aiosqlite. Never block the event loop.
- Every rejection returns a structured error explaining what failed, because
  the agent feeds that text back into its retry.
