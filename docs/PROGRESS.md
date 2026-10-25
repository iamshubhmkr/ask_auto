# AskAuto — Progress

Cross-session state log. Read this at the start of every session; update it at
the end of every session.

If a session dies mid-stage, the unchecked boxes below are how the next
session knows where to resume.

**Current stage:** 1 — Sessions 8 (built) and 9 done; full eval run pending
**Last updated:** 2026-10-25

---

## Stage 1 — Working single agent

### Session 0 — Bootstrap
- [x] Directory layout created
- [x] `requirements.txt` with pinned versions — resolved in `.venv`, `pip check` clean
- [x] `docs/ARCHITECTURE.md` reviewed — kept as shipped in the kit
- [x] `.gitignore`, `.env.example`, `pyproject.toml` with ruff config
- [x] `.claude/rules/` — mcp-server, agent, tests
- [x] `.claude/skills/` — stage-done, trap-check
- [x] `.claude/hooks/guard_imports.py` — import-boundary guard (see Decisions)
- [x] `git init`
- [x] First commit `508d851`, pushed to https://github.com/iamshubhmkr/ask_auto
- [ ] `/context` confirms CLAUDE.md loaded ← **user runs this**

### Session 1 — Database
- [x] `scripts/build_database.py` with explicit DDL (spec DDL verbatim, no pandas)
- [x] Seeded data: 20 dealers, 40 vehicles, 5,000 sales (seed 42)
- [x] `PRAGMA foreign_key_list(sales)` returns 2 rows ← verified by hand
- [x] Row counts identical across two consecutive runs — and full data
      fingerprint identical: `14cb4047…fb1287e7`
- [x] Trap 1 present: gross 173,070,073.75 vs net 166,989,244.64 (gap 3.51%)
- [x] Trap 2 present: `region` has exactly 4 distinct values; `'North East'` → 0 rows
- [x] `tests/test_build_database.py` — 10 tests incl. both traps + determinism
- [x] Proved the FK check catches the `to_sql` mistake (stripped REFERENCES → FAIL)

**Data facts for later sessions** (seed 42):
sales 2023-01-01 .. 2026-06-30 (2026 is a half year). 5 dealers per region:
1 Flagship, 2 Standard, 2 Satellite; Flagship sells ~3× a Satellite.
EV share by year: 2023 17.0%, 2024 27.1%, 2025 36.2%, 2026 43.4%.
Full fingerprint: `14cb404714fb9d9ad2f2cf1d95b29df2cca13096175414c156dec539fb1287e7`
— if a rebuild prints anything else, the data changed (CLAUDE.md: that's a bug).

### Session 2 — Metadata
- [x] `metadata/*.yaml` authored — `tables.yaml` (every table + column described),
      `glossary.yaml` (7 spec terms + 8 aliases = 15 rows)
- [x] Known-unanswerable list written — `metadata/unanswerable.yaml`, 8 topics
- [x] `scripts/build_metadata.py` merges PRAGMA + YAML; fails loudly on YAML/DB mismatch
      or a glossary expression that doesn't run
- [x] All four `meta_*` tables populated — plus fifth `meta_unanswerable` (see Decisions)
- [x] `allowed_values` populated for every categorical column (11 columns incl. `model` override)
- [x] `meta_glossary` contains net revenue and its aliases (revenue, sales value,
      turnover, top line — identical expression)
- [x] `meta_joins` has 2 rows, both many-to-one
- [x] `tests/test_build_metadata.py` — 15 tests incl. both traps, idempotency, 3 broken-YAML cases
- [x] YAML read line by line by a human — user reviewed 2026-10-24, no changes

**Resolved glossary values** (seed 42, useful for evals): net revenue 166,989,244.64 ·
gross revenue 173,070,073.75 · units sold 5,000 · ASP 33,397.85 · discount rate 3.51% ·
EV share (all years) 29.08% · last year = 2025 (1,477 sales, 2025-01-01..2025-12-31).

### Session 3 — MCP server
- [x] `sql_guard.py` written first, with tests — pure (sqlglot only, no DB), passed 33/33 first run
- [x] `tests/test_sql_guard.py` — DROP, UPDATE, PRAGMA, meta_* allowlist,
      bad column, missing LIMIT, valid join — plus DELETE/INSERT/REPLACE/ATTACH/VACUUM,
      multi-statement, sqlite_master, `main.sales`, `pragma_table_info()`, LIMIT clamp
- [x] Four tools implemented with Pydantic signatures (`server.py`, contracts in `models.py`)
- [x] `get_schema_context` accepts a question and ignores it
- [x] Verified by hand — via `python -m askauto.mcp_server.probe` (real stdio server)
- [ ] Verified by hand in MCP Inspector ← needs Node.js (not installed); command below
- [x] No agent code written this session
- [x] `tests/test_mcp_server.py` — 13 tests via in-process `Client` + 1 stdio round trip:
      zero rows ≠ error, rejected / sqlite_error / timeout all structured,
      timeout recovers, connection read-only

**Facts for the agent (session 5):**
- `run_sql` never raises for bad SQL: check `result.ok`; `error.kind` is
  `rejected | sqlite_error | timeout`, `error.message` is the retry feedback.
  Zero rows = `ok=True, row_count=0`.
- Tool exceptions lose their message over MCP ("Error executing tool X") —
  only `ToolError` text survives. Hence run_sql returns errors as data.
- `list_tables` (a list return) arrives wrapped: `structured_content["result"]`.
- `sql_guard.guard_sql` + `models.py` import no DB code, so `agent/` may reuse them.

### Session 4 — Providers and config
- [x] `config.py` via pydantic-settings — `groq_api_key` (SecretStr), `groq_model`,
      `llm_timeout_s`, `llm_max_retries` added to the session-3 DB settings
- [x] `LLMProvider` protocol (`providers/base.py`, provider-agnostic) + `GroqProvider`
      (`providers/groq_provider.py`, the only file importing `groq`)
- [x] Three call sites with correct temperatures — `CALL_SETTINGS` in base.py:
      classify 0.0 · generate_sql 0.0 · synthesize 0.4, each with an output-token cap
- [x] Every method takes a Pydantic output schema — single
      `complete(call, messages, schema) -> T`, Groq strict json_schema mode
- [x] Rate-limit retry with backoff — 429 / 5xx / connection errors; honours
      `retry-after`; 400s not retried; truncated output (`finish_reason=length`) → `LLMError`
- [x] Smoke test confirms the Groq key works — `python -m askauto.providers.smoke`
      (classify 1.02s · generate_sql 0.69s · synthesize 0.60s)
- [x] `tests/test_providers.py` — 15 tests, fake client, no network

**Model comparison (2026-10-24, same SQLPlan task, 10 runs each):**

| Model | Degenerate output | Avg latency | Max |
|---|---|---|---|
| `qwen/qwen3.8-27b` | 5/10 (5/5 on a minimal prompt) | 5.7–13.8 s | 24 s |
| `openai/gpt-oss-20b` ← **default** | 0/10 | 0.55 s | 0.65 s |
| `openai/gpt-oss-120b` | 0/10 | 0.75 s | 0.91 s |

qwen loops inside the JSON `sql` string (`"}]}]}…`) until a limit stops it; the
output still *validates* as a string. Lesson: a 3-run sample picked the wrong
model. Re-test with more runs before trusting a latency number.

### Session 5 — Agent, happy path
- [x] Plan reviewed before any code ← plan mode, user-approved
- [x] `state.py` — single Pydantic state model (`AgentState`) + call schemas
      (`Classification`, `SQLPlan`, `Answer`) and `SQLAttempt`, `Verification`
- [x] Eight nodes, linear wiring only — `nodes.py` (`AgentNodes`), `graph.py`
      (4 named edge functions that raise `AgentError` on non-happy outcomes)
- [x] No `sqlite3` or `aiosqlite` import anywhere in `agent/` (grep + hook)
- [x] One question answered end to end — `python -m askauto.agent "…"`, real Groq + stdio MCP
- [x] `src/askauto/mcp_client.py` — typed `McpTools` (shared with the API in session 7)
- [x] `agent/prompts.py` — everything rendered from `SchemaContext`; a test asserts the
      source names no business table/column/value
- [x] `agent/verify.py` — figures match at shown precision (user decision)
- [x] `tests/test_agent.py` (10) + `tests/test_verify.py` (13) — LLM mocked, real MCP

**Live questions (gpt-oss-20b), each checked against the DB by hand:**

| Question | Result | Latency |
|---|---|---|
| net revenue last year | $52.2M — DB 52,228,431.60 ✓ (gross would be $54.1M); used both glossary expressions | 3.8 s |
| North East units 2024 | 351 ✓, mapped to `'Northeast'` in assumptions | 2.9 s |
| top 3 dealers by revenue 2025 | D01 $4.61M, D06 $4.61M, D16 $3.88M ✓ | 3.9 s |
| average credit score | classify → unanswerable (stops before any MCP call) | — |

**Found by live runs, fixed this session (mocks could not have caught them):**
1. classify called "North East" *ambiguous* (read the column description as doubt) →
   instruction: spelling variants are not ambiguity.
2. Unqualified `dealer_id` in a join was rejected as "could not be resolved" —
   SQLite calls it ambiguous → guard now says which tables and how to qualify;
   SQL prompt says to qualify columns when joining.

**For session 6:** nodes already pass earlier failed attempts to `sql_messages`
and unsupported figures to `synthesize_messages`; only `graph.py` edges change.

### Session 6 — Backward edges
- [x] `validate` → `generate_sql` with error text, ceiling 3 (3 attempts **total**, user decision)
- [x] `execute` error → `generate_sql`, shares the ceiling; ceiling hit → `give_up` (status `failed`)
- [x] `inspect` zero-rows → explains why, does not report "0" (prompt branch + verify
      rejects "0" when no rows came back)
- [x] `classify` → refuse / clarify (deterministic terminal nodes, no MCP call)
- [x] `verify` → `synthesize`, ceiling 1; second failure → `add_caveat` (status `unverified`)
- [x] Ceilings enforced in edge functions, not nodes (`MAX_SQL_ATTEMPTS`, `MAX_RESYNTHESIS` in graph.py)
- [x] A test forces each backward edge to fire — `tests/test_agent.py` (15 tests)
- [x] Agent-side literal check (`agent/literals.py`, 14 tests): `region = 'North East'` →
      validation error listing allowed values → retry (user decision)
- [x] `AgentState.status`: answered · refused · clarification_needed · failed · unverified

**Live (gpt-oss-20b), all checked against the DB:** credit score → refused, naming the
gap · "best dealer" → clarifying question · Ford Prius → counted 0 (a real zero: one
COUNT row, not zero rows) · net revenue last year $52.2M ✓ · North East 2024 351 ✓ ·
top 3 dealers ✓ · EV share 2025 36.2% ✓ · Hybrid 2024 339 ✓.

**Found by live runs, fixed this session:**
1. Groq answers **400 `json_validate_failed`** when gpt-oss-20b misses a schema field
   (strict mode is checked after generation). Was fatal → now retried, shared budget.
2. Latency spikes (13–35 s) were invisible rate-limit sleeps → provider now logs every retry.

**Rate limit (this Groq account): 8,000 tokens/min**, 1,000 requests per reset window.
One question ≈ 3.7K prompt tokens + output ≈ 5K → ~1.5 questions/min before 429s.
Single question: 2–4 s. Back-to-back: throttled. See open question on evals.

### Session 7 — API
- [x] `POST /ask` matching the spec contract exactly — plus additive `status` (user decision)
- [x] `GET /health` — real round trip via the `list_tables` tool (see Decisions)
- [x] `lifespan` starts MCP once, compiles graph once, caches metadata (`create_app` factory)
- [x] No subprocess spawned per request — verified live: MCP process count 2 → 2 across
      requests (2 OS processes = Windows venv launcher + its child interpreter = 1 server)
- [x] Working `curl` command recorded below (Verified commands)
- [x] Errors: `LLMError` → 502, `McpToolError` → 503, bad body → 422 — no tracebacks
- [x] `tests/test_api.py` — 9 tests: contract, one MCP session, refuse/clarify, zero rows,
      502, 422, health ok + dead session → 503

**Live via uvicorn + curl:** health ok · net revenue last year $52.2M, `status: answered`,
3549 ms · credit score → refused, `sql: null`, 592 ms · empty question → 422.

**Found by tests this session:** MCP `ping` is removed in protocol 2026-07-28 (works only
in legacy mode) → `/health` always returned 503. And `list_tools` may be served from the
MCP client's response cache, which would report a dead server as healthy. Fixed by a tool
call (never cached): `McpTools.list_tables()`.

### Session 8 — Evals
- [x] `evals/questions.yaml` — 25 questions: lookup 4 · aggregation 6 · join 6 ·
      trap 4 · refusal 3 · clarification 2
- [x] Every reference SQL verified against the real database — expected values were
      filled **from** `--print-reference`, not typed; the runner re-checks them every run
      (via MCP, so evals never open the DB)
- [x] `evals/run_evals.py` scoring accuracy, validity, p50/p95; paced (`--pace 40`,
      user decision) for the 8K tokens/min limit; JSON to `evals/results/`
- [x] `tests/test_evals.py` — scoring rules (float noise, rates as %, ordered labels,
      gross-vs-net, status mismatch)
- [ ] **Full eval run** ← pending (run once, with nothing else using the Groq quota)
- [ ] `/trap-check` passes both traps
- [ ] Scorecard recorded below

### Session 9 — Frontend
- [x] Streamlit client calling `POST /ask` — `frontend/streamlit_app.py`, streamlit 1.66.1
- [x] Answer, SQL expander, assumptions, trace, latency — answer framed by status;
      `$` escaped (Streamlit renders `$…$` as LaTeX)
- [x] Runs as a separate process from the API — imports only streamlit + httpx,
      never `askauto`; the HTTP contract is its only coupling
- [x] Plain HTML page at `GET /` (user request) — `src/askauto/web/index.html`, served by
      the API (same origin, no CORS); no framework, no CDN; 15 clickable suggested
      questions by group; answer, assumptions, SQL, rows, trace + latency; text inserted
      via `textContent` only (test asserts no `innerHTML`). Live: "How many F150s" → 257 ✓
- [x] Verified: page HTTP 200; `AppTest` drove the real page against the live API —
      sidebar "API connected", answer "\$52.2 million", SQL expander, rows table,
      footer `answered · 2917 ms · classify → … → verify`

### Stage 1 exit criteria
- [ ] 25/25 on the golden set (spec "Done when"; user decision over ≥90%)
- [ ] 100% SQL validity
- [ ] p95 under 8 seconds
- [ ] `/stage-done` passes clean

---

## Later stages — not started

### Stage 2 — Vector schema retrieval
- [ ] `scripts/build_index.py`
- [ ] `SCHEMA_MODE=vector` path inside `get_schema_context`
- [ ] Embedding model loaded once at startup, never per request
- [ ] Index rebuilt in the same script run as the metadata
- [ ] **Only `mcp_server/` and config changed** ← the real test of stage 1

### Stage 3 — SQL example retrieval
- [ ] 20 worked question→SQL pairs
- [ ] Top-3 retrieval into the generate_sql prompt
- [ ] Eval delta measured against stage 2

### Stage 4 — Free text and multi-agent
- [ ] A free-text column added to the schema
- [ ] Supervisor + sql_agent + text_agent as subgraphs
- [ ] Parallel fan-out with `asyncio.gather`

---

## Scorecard history

Record every eval run so regressions are visible.

| Date | Stage | Accuracy | SQL validity | p50 | p95 | Cost/question |
|---|---|---|---|---|---|---|
| | | | | | | |

---

## Verified commands

Record exact commands that worked, so the next session doesn't guess.

```bash
# Environment (Windows, Git Bash; Python 3.12.10)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip install -e . --no-deps   # src/ layout -> importable askauto

# Checks
.venv/Scripts/python -m pip check     # "No broken requirements found."
.venv/Scripts/ruff check .            # "All checks passed!"
.venv/Scripts/pytest -q               # session 9: 157 passed (~18s), no network

# Database (session 1) -- prints PASS/FAIL report + fingerprint, exit 1 on any FAIL
.venv/Scripts/python scripts/build_database.py
# Manual FK check (no sqlite3 CLI on this machine; use Python) -> 2 rows
.venv/Scripts/python -c "import sqlite3; print(sqlite3.connect('data/askauto.db').execute('PRAGMA foreign_key_list(sales)').fetchall())"

# Metadata (session 2) -- ALWAYS after build_database.py (which deletes the DB file)
# Prints meta_glossary, allowed_values, samples, unanswerable, PASS/FAIL checks
.venv/Scripts/python scripts/build_metadata.py

# MCP server (session 3)
.venv/Scripts/python -m askauto.mcp_server           # stdio server (waits for a client)
.venv/Scripts/python -m askauto.mcp_server.probe     # launches it + calls every tool
# MCP Inspector (after installing Node.js LTS):
npx @modelcontextprotocol/inspector .venv/Scripts/python -m askauto.mcp_server

# LLM provider (session 4) -- real Groq calls, needs GROQ_API_KEY in .env
.venv/Scripts/python -m askauto.providers.smoke

# Agent (session 5) -- one question end to end, real Groq + stdio MCP server
.venv/Scripts/python -m askauto.agent "What was our net revenue last year?"

# API (session 7) -- start once, then query
.venv/Scripts/python -m uvicorn askauto.api:app --port 8000
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "What was our net revenue last year?"}'
# PowerShell: write the JSON body to a file and pass --data-binary "@body.json"
# (PowerShell mangles inline double quotes passed to curl.exe)
# Web page (plain HTML, served by the API): http://localhost:8000/
# Browser test page for the API: http://localhost:8000/docs

# Frontend (session 9) -- needs the API running; opens http://localhost:8501
.venv/Scripts/python -m streamlit run frontend/streamlit_app.py

# Evals (session 8)
.venv/Scripts/python evals/run_evals.py --print-reference   # reference results only
.venv/Scripts/python evals/run_evals.py                     # full paced run, ~20 min
.venv/Scripts/python evals/run_evals.py --only T1,T3 --pace 0

# Guard hook, piped test (expect exit 2 + BLOCKED message)
echo '{"tool_input":{"file_path":"src/askauto/agent/x.py","content":"import sqlite3\n"}}' \
  | python .claude/hooks/guard_imports.py
```

---

## Decisions and deviations

Anything done differently from the spec, with the reason. Empty is good.

| Date | Decision | Reason |
|---|---|---|
| 2026-10-23 | Added `PreToolUse` hook `.claude/hooks/guard_imports.py` (not in BUILD_GUIDE) | Turns the sqlite3/aiosqlite and Groq import invariants from CLAUDE.md guidance into enforcement. Scope: `src/askauto/` only; blind to writes made via Bash. Verified live: blocked a `sqlite3` write into `agent/`. |
| 2026-10-23 | Kept kit `docs/ARCHITECTURE.md` (266 lines) instead of regenerating under 150 | Already authored and consistent with the spec; regenerating would lose content. |
| 2026-10-23 | `pyproject.toml` has `[project]` + setuptools; `pip install -e . --no-deps` | `src/` layout needs an install for `python -m askauto.mcp_server`. Deps stay in `requirements.txt` only. |
| 2026-10-23 | Spec docs moved from repo root into `docs/` | CLAUDE.md references `docs/…` paths. Notebooks moved alongside. |
| 2026-10-23 | Sales data spans 2023-01-01 to 2026-06-30 | Spec gives no range; "last year" must resolve to a full year of data (2025). User-approved. |
| 2026-10-23 | DB at `data/askauto.db` inside the repo (OneDrive) | Demo/learning project; simplicity over sync-lock risk. User-approved. |
| 2026-10-24 | No NULLs in seeded data, though spec allows them on city/model_year/msrp | Avoid adding traps the spec didn't ask for. |
| 2026-10-24 | Vehicles only sold within ±1 year of their model_year; EV/Hybrid weight grows yearly | Realism; gives "EV share" a real trend for evals. EV share is high vs real US market — fine for a demo. |
| 2026-10-24 | Build tests load the script via importlib; tests never import sqlite3 | Keeps the `/stage-done` grep (sqlite3 outside mcp_server/ or scripts/) clean. |
| 2026-10-24 | **Fifth meta table `meta_unanswerable(topic, reason)`** (spec says four) | Spec requires the known-unanswerable list to feed classify but defines no table for it. A table keeps the "prompts built from meta_* tables" invariant. User-approved. |
| 2026-10-24 | **`vehicles.model` forced categorical** via `categorical: true` in tables.yaml (33 distinct > 25) | Exact spellings (`F-150`, `CR-V`, `Ioniq 5`) must reach the prompt, or misspellings return zero rows silently — the same failure as trap 2. Threshold stays 25 elsewhere. User-approved. |
| 2026-10-24 | **"last year" anchored to `MAX(sale_date)`**, not today's date | Always 2025 for this dataset, so eval answers never drift with the calendar. User-approved. |
| 2026-10-24 | `sample_values` = min, median, max distinct value | Spec says "3 samples" without saying which; this is deterministic and shows the range. |
| 2026-10-24 | Glossary YAML has a `kind: measure \| filter` field (not stored in the table) | Lets the build execute each expression the right way to prove it works. |
| 2026-10-24 | `sample_rows` returns Pydantic `SampleRows{table, columns, rows}` (spec: `list[dict]`) | mcp-server rule / CLAUDE.md: tools return Pydantic models. User-approved. |
| 2026-10-24 | `run_sql` returns errors as data (`ok=False`, `error.kind`) instead of raising | MCP v2 replaces a raised exception's message with "Error executing tool X"; the agent needs the reason to retry. |
| 2026-10-24 | Minimal `config.py` created in session 3 (DB path, schema mode, timeout, row cap) | The server needs settings now; session 4 adds the Groq fields. |
| 2026-10-24 | Manual MCP check via a Python stdio probe; Inspector deferred | Node.js not installed. User chose probe now + Inspector later. |
| 2026-10-24 | `mcp` 2.x API: `MCPServer`, not `FastMCP` | Pinned `mcp==2.3.0`; v1 tutorials won't match. Verified against installed source. |
| 2026-10-24 | Default model `openai/gpt-oss-20b` (spec says only "Groq") | qwen3.8-27b looped/degenerated on structured SQL; gpt-oss-20b 0/10 failures, ~0.55 s. User-approved. Override via `GROQ_MODEL`. |
| 2026-10-24 | One `complete(call, messages, schema)` method; temperatures in provider-agnostic `CALL_SETTINGS` | A Bedrock provider reuses the same per-call settings; agent code never sees a temperature. |
| 2026-10-24 | Synthesize returns a schema too (prose in a field) | CLAUDE.md: structured output only, never parse prose. Spec's "free prose" is the field's content. |
| 2026-10-24 | Output-token cap per call site; `finish_reason=length` → `LLMError` | Bounds latency when a model loops (seen with qwen). |
| 2026-10-24 | SDK retries off (`max_retries=0`); own backoff, injectable `sleep` | Explicit and unit-testable; honours `retry-after`. |
| 2026-10-24 | Provider tests reach Groq exception classes via `groq_provider.groq` | Tests still never `import groq` (CLAUDE.md invariant). |
| 2026-10-24 | **verify matches at shown precision**; numbers from question/assumptions/SQL/text cells are context | Resolves the open "verify tolerance" question. User-approved. |
| 2026-10-24 | Nodes record outcomes; edge functions decide (session 5: raise `AgentError`) | Session 6 changes `graph.py` only. Retry ceilings will live in edge functions (agent rule). |
| 2026-10-24 | classify uses the startup schema bundle, no MCP call | Spec: an unanswerable question never touches the database. |
| 2026-10-24 | Agent reuses `mcp_server/sql_guard.py` + `models.py` | Both import no DB code; one validator, two call sites. Server check still authoritative. |
| 2026-10-24 | `mcp_client.py` at package root (not in `agent/`) | Shared by agent and API (session 7). |
| 2026-10-24 | verify reads the answer text | Required by spec ("every figure appears in rows"); a check on LLM output, not data extraction — consistent with "never parse prose". |
| 2026-10-24 | Guard names ambiguous columns (session-3 module touched) | Live run: vague "could not be resolved" would waste a session-6 retry. |
| 2026-10-24 | **SQL ceiling = 3 attempts total** | Spec "max 3 attempts". User-approved. Resolves the retry-count open question. |
| 2026-10-24 | **Unverified after re-synthesis → keep answer + caveat** naming the figures | User-approved. SQL and rows still returned so the user can check. |
| 2026-10-24 | **Agent-side literal check** against `allowed_values` (server guard unchanged) | Turns trap 2's silent zero rows into a fixable validation error. User-approved. |
| 2026-10-24 | 4 terminal nodes beyond the spec's 8: refuse, clarify, give_up, add_caveat | Each route needs a node to write the answer; all deterministic, no LLM. |
| 2026-10-24 | `AgentError` removed | Every outcome now has a route; only infrastructure errors (LLMError, MCP) propagate. |
| 2026-10-24 | Retry Groq 400 `json_validate_failed` (providers/ touched) | A bad sample, not a bad request — found live, crashed a question. |
| 2026-10-25 | **`/ask` response adds `status`** (spec contract has none) | Additive; evals must tell answered / refused / clarification / failed / unverified apart. User-approved. |
| 2026-10-25 | `sql` and `rows` are null/empty unless a query ran ok; zero-row runs keep their SQL | "Every number with the SQL that produced it"; a refusal produced none. Zero rows: the user needs the filters. |
| 2026-10-25 | `LLMError` → 502, `McpToolError` → 503 | Clean JSON errors instead of tracebacks; resolves the session-6 open question. |
| 2026-10-25 | `/health` calls the `list_tables` tool | MCP ping removed in protocol 2026-07-28; `list_tools` may be cached client-side. |
| 2026-10-25 | `create_app(settings, provider, mcp_target)` factory; `app = create_app()` | Tests inject a fake provider + in-process server; uvicorn uses the defaults. |
| 2026-10-25 | **Stage 1 bar = 25/25** (not ≥90%) | Spec "Done when" wins. User-approved. |
| 2026-10-25 | **Eval runner paced** (40 s default) | 8K tokens/min Groq limit. Latency measured per request, excluding the pacing gap. User-approved. |
| 2026-10-25 | Eval values matched at the precision the agent shows, within 1%; rates as fraction or % | The agent may round in SQL (0.36) or return a percentage (36.2); both are correct answers. |
| 2026-10-25 | Session 9 built before the session-8 run (user request) | Command service outage; user asked for one combined test run at the end. |
| 2026-10-25 | **streamlit 1.66.1** added (new dependency) | Named by the guide for session 9; user approved via plan. Pulls pandas + pyarrow. |
| 2026-10-25 | Frontend in `frontend/`, outside the `askauto` package | A separate process; the API contract is its only coupling. |
| 2026-10-25 | Plain HTML page served by the API at `GET /` (user request), alongside Streamlit | Same origin avoids CORS; still a thin client calling `/ask`. `package-data` ships it. |

---

## Open questions

Things to resolve before the next stage.

- [ ] **Pass bar.** Stage 1 exit criteria here say ≥90% accuracy; the spec's
      "Done when" says 25 questions *pass*. Spec wins — confirm 25/25 or 90%.
- [x] **Retry count.** Resolved 2026-10-24: 3 SQL attempts total (see Decisions).
- [ ] **Rate limit vs evals/p95** (session 8). Groq account allows 8K tokens/min ≈ 1.5
      questions/min. 25 eval questions back-to-back will be throttled and blow p95.
      Options: pace the eval runner (~40 s between questions), shrink prompts
      (classify uses ~1.2K tokens), or a higher Groq tier. Decide before session 8.
- [x] **API error handling**. Resolved 2026-10-25: 502 / 503 JSON errors (see Decisions).
- [x] **Data date range** (session 1). Resolved 2026-10-23: seeded `sale_date`
      spans **2023-01-01 to 2026-06-30**. 2023–2025 are full years, so
      "last year" (= 2025) is fully covered; 2026 is a partial year.
- [x] **Verify tolerance**. Resolved 2026-10-24: match at shown precision (see Decisions).
- [x] **DB location** (session 1). Resolved 2026-10-23: keep
      `data/askauto.db` in the repo (demo/learning project). File stays
      gitignored and is rebuilt by the build scripts. If OneDrive ever locks it,
      pause sync or point `DATABASE_PATH` elsewhere.
