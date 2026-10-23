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
