# AskAuto

Natural-language questions over a 3-table SQLite database. Agentic text-to-SQL
with a metadata catalogue, an MCP data boundary, and a LangGraph agent.

Full spec: `docs/PROBLEM_STATEMENT.md`
Architecture: `docs/ARCHITECTURE.md`
Current stage and progress: `docs/PROGRESS.md`

Read the spec before starting any new component. Do not infer requirements
from the code alone.

<!-- Maintainer note: keep this file under 200 lines. Detail belongs in docs/. -->

## Architecture invariants

These are not negotiable. If a change would violate one, stop and ask.

- **Only the MCP server opens the database at runtime.** No other module may
  import `sqlite3` or `aiosqlite` outside `src/askauto/mcp_server/`.
  Build scripts in `scripts/` are exempt — they run before the server exists.
- **MCP returns rows, never answers.** No LLM calls, no summarising, no
  interpretation inside the MCP server.
- **Prompts are built from the `meta_*` tables**, never from hardcoded schema
  strings or f-string DDL.
- **SQL is validated before execution**, server-side, using sqlglot on the AST.
  String matching is not validation.
- **Only `dealers`, `vehicles` and `sales` are queryable.** The `meta_*` tables
  are context, not query targets. Enforce with an allowlist in the validator.
- **No vector database, no embeddings** until stage 2. If a task seems to need
  them, re-read the spec — it probably doesn't.
- **Provider calls go through the `LLMProvider` interface.** Never import the
  Groq SDK outside `src/askauto/providers/`.

## Commands

```bash
# Build (in order, from clean)
python scripts/build_database.py      # DDL + seeded synthetic data
python scripts/build_metadata.py      # PRAGMA + YAML -> meta_* tables

# Run
python -m askauto.mcp_server          # MCP server, stdio
uvicorn askauto.api:app --reload      # API on :8000

# Verify
pytest -q                             # unit tests
python evals/run_evals.py             # golden question set
```

## Layout

```
scripts/           build-time only; may open the DB directly
src/askauto/
  config.py        pydantic-settings
  providers/       LLM interface + Groq implementation
  mcp_server/      the ONLY runtime DB access
  agent/           state.py, nodes.py, graph.py
  api.py           FastAPI
metadata/          authored YAML (descriptions, grain, glossary)
docs/              spec, architecture, progress log
evals/             golden questions + runner
tests/
```

## Conventions

- Python 3.11+. Type hints on every function signature.
- Pydantic models for anything crossing a module boundary. No bare dicts.
- `async def` for all I/O. No blocking calls inside async functions.
- Structured LLM output only — always pass a Pydantic schema, never parse prose.
- Ruff for linting. Line length 100.
- Tests live beside the layer they test: `tests/test_mcp_server.py`, etc.

## Known traps in this project

Claude has got these wrong before. Check them explicitly.

- **Never create tables with pandas `to_sql`.** It produces no `REFERENCES`
  clauses, so `PRAGMA foreign_key_list` returns nothing and the join graph
  comes out silently empty. Write explicit `CREATE TABLE` DDL, then insert.
- **`sale_price` is gross.** Net revenue is `sale_price - discount_amount`.
  Resolve it through `meta_glossary`, never hardcode it in a prompt.
- **Categorical values must come from `meta_columns.allowed_values`.** The
  region is spelled `Northeast`, not `North East`. A wrong spelling returns
  zero rows with no error.
- **Start the MCP session once** in the FastAPI `lifespan` handler and reuse
  it. Do not spawn a subprocess per request.
- **The synthetic data is seeded.** If a number changes between runs, that is
  a bug, not randomness.

## Working agreement

- Use plan mode for anything touching more than two files.
- One stage per session. Do not start the next stage without being asked.
- After finishing a stage: run the tests, update `docs/PROGRESS.md`, commit.
- If the spec is ambiguous, ask. Do not choose silently and move on.
- Do not add dependencies without saying which and why first.
