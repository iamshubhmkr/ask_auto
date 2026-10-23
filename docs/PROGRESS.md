# AskAuto — Progress

Cross-session state log. Read this at the start of every session; update it at
the end of every session.

If a session dies mid-stage, the unchecked boxes below are how the next
session knows where to resume.

**Current stage:** 1 — Session 0 done, Session 1 (database) next
**Last updated:** 2026-10-23

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
- [ ] `scripts/build_database.py` with explicit DDL
- [ ] Seeded data: ~20 dealers, ~40 vehicles, ~5,000 sales
- [ ] `PRAGMA foreign_key_list(sales)` returns 2 rows ← **verify by hand**
- [ ] Row counts identical across two consecutive runs
- [ ] Trap 1 present: gross and net revenue differ by 2–5%
- [ ] Trap 2 present: `region` has exactly 4 distinct values

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
.venv/Scripts/pytest -q               # exit 5 "no tests ran" until session 1+

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

---

## Open questions

Things to resolve before the next stage.

- [ ] **Pass bar.** Stage 1 exit criteria here say ≥90% accuracy; the spec's
      "Done when" says 25 questions *pass*. Spec wins — confirm 25/25 or 90%.
- [ ] **Retry count.** "max 3 attempts" (spec) vs "retry ×3" (architecture):
      3 generations total, or 1 + 3 retries?
- [ ] **Data date range** (session 1). "Last year" = previous full calendar
      year. Seeded sales must cover 2025 so "last year" (today: 2026) has data.
      Which years should the synthetic data span?
- [ ] **Verify tolerance** (session 6). Answer says "$1.2M", row says
      1234567.89 — what rounding/format counts as "figure appears in rows"?
- [ ] **DB location** (session 1). Repo is inside OneDrive; a live SQLite file
      there can hit sync locks. Keep `data/` in repo or point `DATABASE_PATH`
      outside OneDrive?
