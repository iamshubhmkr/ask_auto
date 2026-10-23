# Building AskAuto with Claude Code — a start-to-end guide

Two things at once: a working build plan, and a practical education in Claude
Code's configuration system. By the end you should be able to set up the next
project without asking anyone for a prompt.

Verified against the official docs at https://code.claude.com/docs
(re-check before you start; this area moves quickly).

---

## Part 1 — The core idea

Every Claude Code session starts with a fresh context window. Nothing carries
over except what is **on disk**.

So the skill is not writing better prompts. It is putting knowledge where every
session picks it up automatically, so your prompts get to be short.

Compare:

> **Prompting:** a 2,000-word message describing the architecture, the rules,
> the conventions, and the task. Works once. Session ends. Paste it again,
> slightly differently, get slightly different results.

> **Configuring:** the architecture lives in `docs/`, the rules live in
> `CLAUDE.md`, the procedures live in `.claude/skills/`. Your message is
> *"read docs/PROBLEM_STATEMENT.md and build stage 3."*

The second one is reproducible, reviewable, and committable.

---

## Part 2 — The four mechanisms

| Mechanism | When it loads | Put here |
|---|---|---|
| `CLAUDE.md` | Every session, always | Short always-true facts: commands, conventions, hard rules |
| `.claude/rules/*.md` | When matching files are touched | Layer-specific conventions |
| `.claude/skills/*/SKILL.md` | On demand, or when you type `/name` | Multi-step procedures |
| Hooks | At fixed lifecycle events | Hard enforcement |

### The decision rule

Ask: **is it a fact, a scoped convention, a procedure, or a hard requirement?**

- *Fact* — "net revenue means price minus discount" → **CLAUDE.md**
- *Scoped convention* — "modules under `mcp_server/` may import sqlite3" →
  **rule with `paths:` frontmatter**
- *Procedure* — "how to verify a stage is complete" → **skill**
- *Hard requirement* — "never commit without tests passing" → **hook**

### The critical distinction

CLAUDE.md is delivered as context, not as enforced configuration. Claude reads
it and tries to follow it, but there is no guarantee of strict compliance.

So **CLAUDE.md persuades. Hooks enforce.** If something absolutely must not
happen, a hook is the answer, not a firmer sentence in CLAUDE.md.

### Size discipline

Target under 200 lines for CLAUDE.md. Longer files consume more context *and*
reduce adherence — so a bloated CLAUDE.md is worse than a short one, not just
more expensive.

This is why the spec lives in `docs/` and is referenced by path rather than
imported. You can pull files in with `@path` syntax, but imported files load at
launch and consume context every session. A 400-line spec imported into
CLAUDE.md costs you 400 lines in every session forever.

**The split that works:**

- Invariants (rules that must never break) → *directly in CLAUDE.md*, short
- Full spec, rationale, data dictionary → *`docs/`, referenced by path*

Claude reads `docs/PROBLEM_STATEMENT.md` when it needs detail, but
"only MCP touches the database" is in context every single time.

---

## Part 3 — Session 0: bootstrap

Create the folder, open it in VS Code, and run `claude` in the terminal.

### Step 1: drop in the two files from this kit

```
askauto/
├── CLAUDE.md                      # from this kit
└── docs/
    └── PROBLEM_STATEMENT.md       # from this kit
```

### Step 2: the one prompt worth writing

This is a *bootstrap* prompt. Its job is to create the configuration that does
the work in every session after this one.

```
Read CLAUDE.md and docs/PROBLEM_STATEMENT.md carefully. Do not write any
application code yet.

Set up the project scaffolding:

1. Create the directory layout described in CLAUDE.md, with __init__.py
   files and empty placeholder modules.
2. Create requirements.txt with pinned versions for: langgraph,
   langchain-core, mcp, pydantic, pydantic-settings, groq, sqlglot,
   fastapi, uvicorn, aiosqlite, pyyaml, pytest, pytest-asyncio, ruff.
   Tell me the versions you pinned and why before writing the file.
3. Create docs/ARCHITECTURE.md summarising the component boundaries and
   the agent graph, derived from the problem statement. Keep it under
   150 lines.
4. Create docs/PROGRESS.md with a stage checklist derived from the spec,
   all unchecked.
5. Create .gitignore, .env.example, and pyproject.toml with ruff config.
6. git init and make one commit.

Then stop and show me the layout. Do not implement anything.
```

Notice what this does: it makes Claude *produce* the remaining configuration
from the spec, rather than you writing it by hand.

### Step 3: verify the setup actually loaded

```
/context
```

Check `CLAUDE.md` appears under **Memory files**. If it isn't there, Claude
cannot see it and nothing else in this guide will work.

Also useful:

```
/memory     # list and edit memory files
/doctor     # checkup; also proposes trims for an oversized CLAUDE.md
/skills     # list available skills
```

---

## Part 4 — Add the path-scoped rules

Rules keep layer-specific instructions out of CLAUDE.md, loading only when
Claude touches matching files.

**`.claude/rules/mcp-server.md`**

```markdown
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
```

**`.claude/rules/agent.md`**

```markdown
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
```

**`.claude/rules/tests.md`**

```markdown
---
paths:
  - "tests/**/*.py"
---

# Testing rules

- pytest-asyncio for async tests. No event loop juggling by hand.
- MCP server tests run against a temp database built by the build scripts.
- Agent tests mock the LLM provider. Never call Groq in a unit test.
- Every planted trap in the spec gets a regression test.
```

Rules without a `paths` field load unconditionally. With `paths`, they load
when Claude reads a matching file — so you get specificity without paying
context for it.

---

## Part 5 — Add two skills

A skill is a procedure. Create one when you keep pasting the same checklist
into chat, or when a CLAUDE.md section has grown into a procedure rather than
a fact.

Skills and custom commands have merged: `.claude/commands/x.md` and
`.claude/skills/x/SKILL.md` both create `/x`. Skills are the recommended form
because they support supporting files and frontmatter control.

**`.claude/skills/stage-done/SKILL.md`**

```markdown
---
name: stage-done
description: Verify a build stage is complete before moving on. Use when a
  stage looks finished, when asked to check progress, or before committing
  the end of a stage.
---

# Stage completion check

Work through this in order. Report each as PASS or FAIL with evidence.
Do not mark a stage done in docs/PROGRESS.md until everything passes.

1. **Spec conformance** — re-read the relevant section of
   docs/PROBLEM_STATEMENT.md. List each requirement and whether it is met.
2. **Invariants** — grep for violations:
   - `sqlite3` or `aiosqlite` imported outside mcp_server/ or scripts/
   - hardcoded schema strings or DDL in prompt construction
   - Groq SDK imported outside providers/
   - any LLM call without a Pydantic output schema
3. **Tests** — run `pytest -q`. Report failures verbatim.
4. **Lint** — run `ruff check .`
5. **Manual smoke** — state the exact command you ran and its output.
6. **Progress log** — update docs/PROGRESS.md: tick the stage, note anything
   deferred and why.

If any check fails, fix it before reporting the stage complete.
```

**`.claude/skills/trap-check/SKILL.md`**

```markdown
---
name: trap-check
description: Verify the two planted data traps are handled correctly. Use
  after changing metadata, prompts, or SQL generation.
---

# Planted trap verification

The spec plants two traps. Both produce plausible wrong answers rather than
errors, so only explicit testing catches them.

## Trap 1 — gross vs net

Ask: "What was our total revenue in 2025?"

- PASS: SQL contains `sale_price - discount_amount`
- FAIL: SQL sums `sale_price` alone

Then verify the glossary lookup is what caused it, not luck. Temporarily
remove the net revenue entry from meta_glossary and confirm the answer
degrades. Restore it.

## Trap 2 — categorical spelling

Ask: "How did the Northeast region do?"

- PASS: SQL contains exactly `'Northeast'`
- FAIL: any other spelling, or zero rows returned

Confirm `meta_columns.allowed_values` for `dealers.region` is populated and
present in the prompt. An empty allowed_values column means the metadata
builder is broken even if this question happens to work.

Report both results with the generated SQL quoted.
```

Then `/stage-done` and `/trap-check` are available as commands, and Claude
will also load them on its own when the description matches what you asked.

---

## Part 6 — Superpowers

You asked about this. Here is the straight assessment.

Superpowers is a community plugin by Jesse Vincent, distributed through the
official plugin marketplace. It installs a library of skills covering
brainstorming, TDD, systematic debugging, code review, and skill authoring —
forcing a clarify → design → plan → code → verify discipline instead of
letting Claude jump straight to writing code.

```
/plugin install superpowers@claude-plugins-official
```

**My recommendation: don't install it for this project, and here's why.**

Its main value is imposing structure on projects that have none. You already
have a spec, an architecture, staged milestones, and an eval set. You have
built the discipline by hand, which is the better way to learn what the
discipline is for.

Install it *afterwards*, on your next project. Having done it manually once,
you will actually understand what each of its skills is replacing — and you'll
be in a position to judge which parts you want and which you'd rather keep
doing yourself. Installing it first means you inherit someone else's workflow
without knowing why it's shaped that way.

Do browse its skills on GitHub though. They are well-written examples of skill
authoring, which is the transferable part.

---

## Part 7 — The build sessions

One stage per session. Start each with `/clear` so you begin with a clean
context. Commit at the end of every session.

### Session 1 — Database

```
Read docs/PROBLEM_STATEMENT.md, the Data model section.

Write scripts/build_database.py:
- Explicit CREATE TABLE DDL with REFERENCES clauses, exactly as specified
- Seeded synthetic data: ~20 dealers, ~40 vehicles, ~5000 sales
- Realistic distributions; discount_amount roughly 2-5% of sale_price
- Print row counts and verify the two planted traps exist

Do NOT use pandas to_sql to create tables. See the trap note in CLAUDE.md.

Then run it and show me the verification output.
```

**Verify before moving on:**
```bash
sqlite3 data/askauto.db "PRAGMA foreign_key_list(sales);"
```
This must return two rows. If it returns nothing, the DDL was not used and
everything downstream will break silently.

### Session 2 — Metadata

```
Read docs/PROBLEM_STATEMENT.md, the Metadata tables section.

Two deliverables:

1. metadata/*.yaml — the authored half. Table descriptions, grain
   statements, column descriptions for the tricky columns, the full
   glossary, and the known-unanswerable list.
2. scripts/build_metadata.py — reads PRAGMA for the automatic half, reads
   the YAML for the authored half, writes all four meta_* tables.

Categorical rule: distinct_count <= 25 stores full allowed_values as JSON,
otherwise store 3 sample_values.

Run it, then show me the full contents of meta_glossary and the
allowed_values for every categorical column.
```

This session matters more than any other. If the metadata is wrong, every
later stage inherits the fault. Read the YAML yourself, line by line.

### Session 3 — MCP server

```
Read docs/PROBLEM_STATEMENT.md, the MCP tool surface section.

Build src/askauto/mcp_server/ with the four specified tools.

Priority is the run_sql guard. Every enforcement listed in the spec, using
sqlglot on the parsed AST. Write the validator first, with tests, before
wiring up the server.

Include tests/test_sql_guard.py covering: DROP rejected, UPDATE rejected,
PRAGMA rejected, meta_* tables rejected by allowlist, nonexistent column
rejected, missing LIMIT injected, valid join accepted.

No agent code this session.
```

**Verify by hand before writing any agent.** Run the MCP Inspector, click
through each tool, read the output. This is the single most valuable habit in
the whole build — you are testing the data layer with zero agent complexity in
the way.

### Session 4 — Providers and config

```
Build src/askauto/config.py (pydantic-settings) and
src/askauto/providers/ with an LLMProvider protocol plus a Groq
implementation.

Three call sites with different settings, per the spec: classify (temp 0),
generate_sql (temp 0, strict schema), synthesize (temp ~0.4).

Every method takes a Pydantic output schema and returns a validated
instance. Include a retry with backoff on rate limits.

Write a smoke test I can run to confirm the Groq key works.
```

### Session 5 — Agent, happy path only

```
Read docs/PROBLEM_STATEMENT.md, the Agent graph section.

Build the LangGraph agent, happy path only this session:
classify -> load_context -> generate_sql -> validate -> execute ->
inspect -> synthesize -> verify

Use plan mode first. Show me the plan before writing code.

state.py holds the single Pydantic state model. Nodes take state and
return state. Data access only via the MCP client.

No retry edges yet. On any failure, fail loudly.
```

Insist on plan mode here. This is the session where a wrong shape costs you
the most to unwind.

### Session 6 — The edges that go backwards

```
Add the conditional edges to the agent:

- validate fails -> generate_sql, with the error text in state (max 3)
- execute errors -> generate_sql, with the SQLite error (same counter)
- inspect: zero rows -> synthesize but explain why empty, not "0"
- classify: unanswerable -> refuse, ambiguous -> clarify
- verify: figure not in rows -> synthesize once more

Retry ceilings enforced in the edge functions, not the nodes.

Then write tests that force each backward edge to fire.
```

These edges are the reason LangGraph is in the project. If you skipped them
you could delete the dependency, and it would be right to.

### Session 7 — API

```
Build src/askauto/api.py.

POST /ask and GET /health, contract exactly as specified.

The lifespan handler starts the MCP session once, compiles the graph once,
and caches the metadata bundle. Do not spawn a subprocess per request.

Show me a curl command that works end to end.
```

### Session 8 — Evals

```
Read docs/PROBLEM_STATEMENT.md, the Done when section.

Build evals/questions.yaml with 25 questions: lookups, aggregations,
joins, both planted traps, refusals, clarifications. Each with an expected
answer or expected behaviour, and reference SQL where applicable.

Verify every reference query runs against the real database before you
write the expected values into the file.

Then evals/run_evals.py scoring accuracy, SQL validity, latency p50/p95.

Run it and show me the scorecard.
```

Then `/trap-check` and `/stage-done`.

### Session 9 — Frontend, then stop

```
Build a minimal Streamlit client calling POST /ask. Answer at the top, SQL
and assumptions in an expander, trace and latency in a footer.

This is a client, not part of the API process.
```

Stage 1 is now complete. Stop here. Stage 2 (the vector switch) should be a
config change to `get_schema_context`, touching nothing else — and proving
that is the whole point of stage 2.

---

## Part 8 — Habits that matter more than the config

**`/clear` between stages.** A long session accumulates irrelevant context and
gets worse, not better. Fresh context plus good CLAUDE.md beats a long session
every time.

**`/context` when something feels off.** It shows what is actually loaded. Most
"Claude is ignoring my instructions" turns out to be "the file never loaded."

**Plan mode for anything structural.** `/plan` designs without editing. Read
the plan, push back, then let it run. Cheaper than reviewing 400 lines of
wrong code.

**Add to CLAUDE.md the second time, not the first.** The docs put it well: add
to CLAUDE.md when Claude makes the same mistake a second time, or when you type
the same correction you typed last session. One mistake is noise. Twice is a
missing rule.

**Commit every stage.** Your recovery mechanism when a session goes sideways is
`git reset`, not arguing with the model.

**Auto memory is separate from CLAUDE.md.** Claude writes its own notes to
`~/.claude/projects/<project>/memory/` based on your corrections. Run `/memory`
occasionally to see what it saved — and correct it if it learned something
wrong. It's plain markdown you can edit.

**Read `docs/PROGRESS.md` before each session.** It is the state log across
sessions. If a session dies mid-stage, that file is how the next one knows
where to resume.

---

## Part 9 — When it goes wrong

| Symptom | First thing to check |
|---|---|
| Ignoring a CLAUDE.md rule | `/context` — did the file load? Then: is the rule specific enough to verify? |
| Rule loaded but not followed | Two rules may conflict. Or it needs to be a hook, not a rule. |
| CLAUDE.md too long | `/doctor` proposes trims. Move procedures to skills, scoped conventions to rules. |
| Instructions lost after `/compact` | Project-root CLAUDE.md survives compaction. If something vanished, it was conversation-only — put it in CLAUDE.md. |
| Skill not triggering | The description is the trigger. Rewrite it with the words you actually type. |
| Drifting from the spec | Say "re-read docs/PROBLEM_STATEMENT.md section X and tell me where the code diverges." |

---

## Part 10 — What you should be able to do afterwards

- Decide correctly between CLAUDE.md, a rule, a skill, and a hook
- Write a CLAUDE.md that stays under 200 lines and is actually followed
- Scope rules with `paths:` so they cost nothing until relevant
- Write a skill description that reliably triggers
- Keep a spec on disk and hold a build to it across many sessions
- Debug why an instruction isn't landing, instead of rephrasing and hoping

That last one is the real skill. Most people respond to a Claude Code session
going wrong by writing a longer prompt. The better move is almost always to fix
what's on disk.
