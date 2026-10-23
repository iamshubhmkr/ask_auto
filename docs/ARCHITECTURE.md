# AskAuto — Architecture

Companion to `docs/PROBLEM_STATEMENT.md`. That document says *what* to build;
this one says *how the pieces fit*. Where they disagree, the problem statement
wins.

---

## Two phases

The system has a build phase and a runtime phase, and they have different
rules about database access.

**Build time** — runs once, on a developer machine, before the server exists.
Scripts in `scripts/` open SQLite directly. This is correct and intentional,
the same way database migrations bypass the application layer.

**Runtime** — serves requests. The MCP server is the only process permitted to
open the database. FastAPI and the agent reach data exclusively through it.

The "only MCP touches the database" invariant governs the request path, not
the tooling.

---

## Build-time components

| Script | Opens DB | Produces |
|---|---|---|
| `scripts/build_database.py` | directly | 3 business tables + seeded data |
| `scripts/build_metadata.py` | directly | 4 `meta_*` tables |
| `scripts/build_index.py` | reads `meta_tables` | vector index (stage 2 only) |

Order matters. `build_metadata` reads PRAGMA output from tables that
`build_database` must have created with explicit `REFERENCES` clauses.

`build_metadata.py` merges two sources:

- **Automatic** — `PRAGMA table_info`, `PRAGMA foreign_key_list`,
  `COUNT(DISTINCT col)`. Gives structure.
- **Authored** — YAML in `metadata/`. Gives meaning: descriptions, grain,
  glossary, the known-unanswerable list.

The authored half cannot be generated. It is where the business knowledge
lives.

---

## Runtime components

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Thin client  │────▶│   FastAPI    │────▶│  MCP server  │────▶ SQLite
└──────────────┘     │   uvicorn    │     │ sqlglot guard│
                     └──────┬───────┘     └──────────────┘
                            │                     ▲
                            ▼                     │
                     ┌──────────────┐             │
                     │  LangGraph   │─────────────┘
                     │    agent     │
                     └──────┬───────┘
                            │
                            ▼
                     ┌──────────────┐
                     │ LLMProvider  │────▶ Groq
                     └──────────────┘
```

### Boundaries

| Component | May import | Must not import |
|---|---|---|
| `mcp_server/` | `aiosqlite`, `sqlglot` | Groq SDK, LangGraph |
| `agent/` | MCP client, `LLMProvider` | `sqlite3`, `aiosqlite`, Groq SDK |
| `providers/` | Groq SDK | anything data-related |
| `api.py` | agent, MCP client | `sqlite3`, Groq SDK |
| `scripts/` | `sqlite3`, `yaml` | agent, MCP, providers |

These are enforceable by grep, and `/stage-done` checks them.

### Startup sequence

FastAPI's `lifespan` handler runs once:

1. Load settings from env via `pydantic-settings`
2. Open the MCP session, keep the handle on `app.state`
3. Compile the LangGraph graph
4. Fetch and cache the metadata bundle

Doing any of these per request is the most common performance mistake here.
An MCP stdio subprocess spawned per question adds process startup to every
response.

---

## MCP tool surface

```
get_schema_context(question: str | None) -> SchemaContext
run_sql(sql: str, limit: int = 500)      -> QueryResult
list_tables()                            -> list[TableSummary]
sample_rows(table: str, n: int = 5)      -> list[dict]
```

Every signature is a Pydantic model.

`get_schema_context` takes a question from day one even though stage 1 ignores
it. This is deliberate: it makes stage 2 (vector retrieval) a change inside
the server with no caller modification. The switch is
`SCHEMA_MODE = "full" | "vector"` in settings.

### The guard in `run_sql`

Enforced server-side, on the parsed AST, not with string matching:

- `SELECT` or `WITH` only
- Table allowlist: `dealers`, `vehicles`, `sales`
- Columns must exist in `meta_columns`
- `LIMIT` injected if absent
- 5 second timeout, row cap

Rejections return structured errors with the reason, because the agent feeds
that text back into its retry. A vague rejection wastes a retry attempt.

The agent may also validate. The server validation is the one that matters —
assume the agent's check was bypassed.

---

## Agent graph

State is one Pydantic model. Nodes take state, return state.

```
                        question
                           │
                           ▼
                     ┌──────────┐
                     │ classify │
                     └────┬─────┘
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
       [refuse]      [clarify]    ┌──────────────┐
            │             │       │ load_context │
            │             │       └──────┬───────┘
            │             │              ▼
            │             │      ┌──────────────┐
            │             │  ┌──▶│ generate_sql │
            │             │  │   └──────┬───────┘
            │             │  │          ▼
            │             │  │   ┌──────────┐
            │             │  ├───┤ validate │
            │             │  │   └────┬─────┘
            │             │  │        ▼
            │             │  │   ┌─────────┐
            │             │  │   │ execute │
            │             │  │   └────┬────┘
            │             │  │        ▼
            │             │  │   ┌─────────┐
            │             │  └───┤ inspect │
            │             │      └────┬────┘
            │             │           ▼
            │             │    ┌────────────┐
            │             │    │ synthesize │◀──┐
            │             │    └─────┬──────┘   │
            │             │          ▼          │
            │             │      ┌────────┐     │
            │             │      │ verify ├─────┘
            │             │      └───┬────┘
            └─────────────┴──────────┴──▶ response
```

### Why LangGraph

Three edges go backwards or sideways. Without them this would be a linear
function chain and the dependency would not be justified:

| Edge | Condition | Ceiling |
|---|---|---|
| `validate` → `generate_sql` | AST check failed | 3 |
| `inspect` → `generate_sql` | SQLite error | shares the 3 |
| `verify` → `synthesize` | figure not in rows | 1 |
| `classify` → `refuse` | unanswerable | — |
| `classify` → `clarify` | ambiguous | — |

Ceilings live in the edge functions, not the nodes.

### Node responsibilities

| Node | LLM? | MCP? | Notes |
|---|---|---|---|
| `classify` | yes, temp 0 | no | uses the known-unanswerable list |
| `load_context` | no | yes | cached after first call |
| `generate_sql` | yes, temp 0 | no | returns `SQLPlan` |
| `validate` | no | no | sqlglot AST |
| `execute` | no | yes | `run_sql` |
| `inspect` | no | no | three-way branch |
| `synthesize` | yes, ~0.4 | no | prose |
| `verify` | no | no | numeric containment check |

Every node appends to `state.trace` so the API can return the path taken.

`classify` runs before `load_context` on purpose: an unanswerable question
costs one cheap LLM call and never touches the database.

---

## Request flow

```
POST /ask
  → classify
  → load_context
  → generate_sql ──┐
  → validate       │ retry ×3
  → execute        │
  → inspect ───────┘
  → synthesize ──┐
  → verify ──────┘ retry ×1
  → {answer, sql, assumptions, rows, trace, latency_ms}
```

Typical path: 3 LLM calls, 2 MCP calls, under 8 seconds p95.

---

## Where async matters, and where it doesn't

**Does not help:** single-request latency. `generate_sql` and `synthesize` are
inherently sequential — you cannot write the answer before you have the rows.

**Does help:** serving concurrent requests without blocking, keeping the event
loop free during network waits, and streaming tokens to the client.

Be honest about this when explaining the system. Overselling async as "faster"
invites a question you cannot answer.

---

## Stage boundaries

| Stage | Change | Touches |
|---|---|---|
| 1 | Working agent, `SCHEMA_MODE=full` | everything |
| 2 | Vector schema retrieval | `mcp_server/` only, plus a config flag |
| 3 | SQL example retrieval | `mcp_server/` only |
| 4 | Free-text column, supervisor + specialists | `agent/` graph shape |

Stage 2 touching only one module is the test of whether stage 1 got the
boundaries right. If it requires agent changes, the abstraction leaked.

---

## Deployment swap (not built, documented)

| Local | Production | Changes |
|---|---|---|
| SQLite | Athena / Redshift | one `mcp_server/` module |
| Groq | Bedrock | one `providers/` implementation |
| `.env` | Secrets Manager | config loader |
| `uvicorn` | ECS Fargate behind ALB | Dockerfile + task definition |

Named gaps, deliberately unbuilt: authentication, row-level security, query
cost governance, audit logging of executed SQL, drift monitoring on eval
scores.
