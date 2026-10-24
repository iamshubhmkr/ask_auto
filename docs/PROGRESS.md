# AskAuto — Progress

Cross-session state log. Read this at the start of every session; update it at
the end of every session.

If a session dies mid-stage, the unchecked boxes below are how the next
session knows where to resume.

**Current stage:** 1 — Session 1 done, Session 2 (metadata) next
**Last updated:** 2026-10-24

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
- [ ] `metadata/*.yaml` authored — descriptions, grain, glossary
- [ ] Known-unanswerable list written
- [ ] `scripts/build_metadata.py` merges PRAGMA + YAML
- [ ] All four `meta_*` tables populated
- [ ] `allowed_values` populated for every categorical column
- [ ] `meta_glossary` contains net revenue and its aliases
- [ ] `meta_joins` has 2 rows ← empty means the DDL was wrong
- [ ] YAML read line by line by a human ← **do not skip**

### Session 3 — MCP server
- [ ] `sql_guard.py` written first, with tests
- [ ] `tests/test_sql_guard.py` — DROP, UPDATE, PRAGMA, meta_* allowlist,
      bad column, missing LIMIT, valid join
- [ ] Four tools implemented with Pydantic signatures
- [ ] `get_schema_context` accepts a question and ignores it
- [ ] Verified by hand in MCP Inspector ← **highest-value check in the build**
- [ ] No agent code written this session

### Session 4 — Providers and config
- [ ] `config.py` via pydantic-settings
- [ ] `LLMProvider` protocol + Groq implementation
- [ ] Three call sites with correct temperatures
- [ ] Every method takes a Pydantic output schema
- [ ] Rate-limit retry with backoff
- [ ] Smoke test confirms the Groq key works

### Session 5 — Agent, happy path
- [ ] Plan reviewed before any code ← **use plan mode**
- [ ] `state.py` — single Pydantic state model
- [ ] Eight nodes, linear wiring only
- [ ] No `sqlite3` or `aiosqlite` import anywhere in `agent/`
- [ ] One question answered end to end

### Session 6 — Backward edges
- [ ] `validate` → `generate_sql` with error text, ceiling 3
- [ ] `execute` error → `generate_sql`, shares the ceiling
- [ ] `inspect` zero-rows → explains why, does not report "0"
- [ ] `classify` → refuse / clarify
- [ ] `verify` → `synthesize`, ceiling 1
- [ ] Ceilings enforced in edge functions, not nodes
- [ ] A test forces each backward edge to fire

### Session 7 — API
- [ ] `POST /ask` matching the spec contract exactly
- [ ] `GET /health`
- [ ] `lifespan` starts MCP once, compiles graph once, caches metadata
- [ ] No subprocess spawned per request
- [ ] Working `curl` command recorded below

### Session 8 — Evals
- [ ] `evals/questions.yaml` — 25 questions across all tiers
- [ ] Every reference SQL verified against the real database
- [ ] `evals/run_evals.py` scoring accuracy, validity, p50/p95
- [ ] `/trap-check` passes both traps
- [ ] Scorecard recorded below

### Session 9 — Frontend
- [ ] Streamlit client calling `POST /ask`
- [ ] Answer, SQL expander, assumptions, trace, latency
- [ ] Runs as a separate process from the API

### Stage 1 exit criteria
- [ ] ≥90% answer accuracy on the golden set
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
.venv/Scripts/pytest -q               # session 1: 10 passed

# Database (session 1) -- prints PASS/FAIL report + fingerprint, exit 1 on any FAIL
.venv/Scripts/python scripts/build_database.py
# Manual FK check (no sqlite3 CLI on this machine; use Python) -> 2 rows
.venv/Scripts/python -c "import sqlite3; print(sqlite3.connect('data/askauto.db').execute('PRAGMA foreign_key_list(sales)').fetchall())"

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

---

## Open questions

Things to resolve before the next stage.

- [ ] **Pass bar.** Stage 1 exit criteria here say ≥90% accuracy; the spec's
      "Done when" says 25 questions *pass*. Spec wins — confirm 25/25 or 90%.
- [ ] **Retry count.** "max 3 attempts" (spec) vs "retry ×3" (architecture):
      3 generations total, or 1 + 3 retries?
- [x] **Data date range** (session 1). Resolved 2026-10-23: seeded `sale_date`
      spans **2023-01-01 to 2026-06-30**. 2023–2025 are full years, so
      "last year" (= 2025) is fully covered; 2026 is a partial year.
- [ ] **Verify tolerance** (session 6). Answer says "$1.2M", row says
      1234567.89 — what rounding/format counts as "figure appears in rows"?
- [x] **DB location** (session 1). Resolved 2026-10-23: keep
      `data/askauto.db` in the repo (demo/learning project). File stays
      gitignored and is rebuilt by the build scripts. If OneDrive ever locks it,
      pause sync or point `DATABASE_PATH` elsewhere.
