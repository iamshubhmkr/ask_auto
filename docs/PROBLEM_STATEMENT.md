# AskAuto — Problem Statement

**Build a conversational analytics assistant over a small SQL database, using an
agentic pipeline that shows its work and admits what it doesn't know.**

This document is the source of truth. Where code and this document disagree,
this document wins. Ask before deviating.

---

## Context

A car dealer group keeps three related tables in a SQL database: dealership
locations, a vehicle catalogue, and sales transactions. Answering a business
question today needs someone who can write SQL *and* who knows the local
conventions — that "revenue" means price minus discount, that regions are
spelled a particular way. That knowledge sits in two people's heads.

## Goal

An API that takes a plain-English question, works out the right SQL, runs it,
and answers in plain English — returning the query alongside the answer so
anyone can verify it.

## Users

General managers and regional directors. Comfortable with business numbers,
not with SQL.

---

## Required behaviour

- Decide whether a question is answerable **before** writing any SQL
- Build prompts from a **queryable metadata store**, not hardcoded schema text
- Return every number with the SQL that produced it, plus any assumptions made
- Resolve business terms like "net revenue" through a glossary, identically
  every time
- Use exact category values from metadata instead of guessing spellings
- Validate generated SQL before execution; reject anything that isn't a read
- Retry on invalid SQL, using the database error as feedback (max 3 attempts)
- Tell an error apart from a query that legitimately returned nothing
- Refuse when the data can't answer, naming what is missing
- Ask for clarification when a question is ambiguous, rather than guessing
- Check that every figure in the final answer appears in the query results

---

## Data model

Three business tables. Explicit DDL with `REFERENCES` clauses.

```sql
CREATE TABLE dealers (
    dealer_id   TEXT PRIMARY KEY,
    dealer_name TEXT NOT NULL,
    city        TEXT,
    region      TEXT NOT NULL,   -- Northeast | Southeast | Midwest | West
    tier        TEXT NOT NULL    -- Flagship | Standard | Satellite
);

CREATE TABLE vehicles (
    vehicle_id  TEXT PRIMARY KEY,
    make        TEXT NOT NULL,   -- Toyota | Honda | Ford | Nissan | Chevrolet | Hyundai
    model       TEXT NOT NULL,
    model_year  INTEGER,
    fuel_type   TEXT NOT NULL,   -- Gasoline | Hybrid | Electric
    msrp        REAL
);

CREATE TABLE sales (
    sale_id         TEXT PRIMARY KEY,
    dealer_id       TEXT NOT NULL REFERENCES dealers(dealer_id),
    vehicle_id      TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    sale_date       TEXT NOT NULL,   -- ISO YYYY-MM-DD
    sale_price      REAL NOT NULL,   -- GROSS, before discount
    discount_amount REAL NOT NULL,
    finance_type    TEXT NOT NULL    -- Loan | Lease | Cash
);
```

Scale: ~20 dealers, ~40 vehicles, ~5,000 sales. Seeded, so answers are
deterministic.

### Deliberate traps

Two, planted on purpose. The system must handle both.

1. **Gross vs net.** `SUM(sale_price)` returns a plausible wrong number.
   Only the glossary prevents it.
2. **Exact categorical spelling.** `'Northeast'`, not `'North East'`.
   Only the allowed-values list prevents silent zero-row results.

---

## Metadata tables

Four tables in the same database, describing the three above.

```sql
CREATE TABLE meta_tables (
    table_name  TEXT PRIMARY KEY,
    description TEXT,       -- authored
    grain       TEXT,       -- authored: "one row per sale"
    row_count   INTEGER     -- automatic
);

CREATE TABLE meta_columns (
    table_name      TEXT,
    column_name     TEXT,
    data_type       TEXT,     -- automatic, PRAGMA
    is_primary_key  INTEGER,  -- automatic, PRAGMA
    is_nullable     INTEGER,  -- automatic, PRAGMA
    is_categorical  INTEGER,  -- automatic: distinct_count <= 25
    distinct_count  INTEGER,  -- automatic
    allowed_values  TEXT,     -- automatic: JSON array when categorical
    sample_values   TEXT,     -- automatic: 3 samples when not
    description     TEXT,     -- authored
    PRIMARY KEY (table_name, column_name)
);

CREATE TABLE meta_joins (
    from_table  TEXT, from_column TEXT,
    to_table    TEXT, to_column   TEXT,
    cardinality TEXT,             -- 'many-to-one'
    PRIMARY KEY (from_table, from_column, to_table, to_column)
);

CREATE TABLE meta_glossary (
    term           TEXT PRIMARY KEY,
    sql_expression TEXT,          -- 'SUM(sale_price - discount_amount)'
    notes          TEXT
);
```

**Automatic half** comes from `PRAGMA table_info`, `PRAGMA foreign_key_list`,
and `COUNT(DISTINCT col)`.

**Authored half** comes from YAML files in `metadata/`. This is where the value
is. PRAGMA can tell you `sale_price` is a REAL; only a human can tell you it is
the gross figure.

### Glossary content (minimum)

| Term | Expression |
|---|---|
| net revenue | `SUM(sale_price - discount_amount)` |
| gross revenue | `SUM(sale_price)` — flag when used, usually a mistake |
| units sold | `COUNT(*)` on sales |
| average selling price | `AVG(sale_price - discount_amount)` |
| discount rate | `SUM(discount_amount) / SUM(sale_price)` |
| EV share | share of sales where `fuel_type = 'Electric'` |
| last year | the previous full calendar year |

Add aliases: *revenue*, *sales value*, *turnover*, *top line* all resolve to
net revenue.

### Known-unanswerable list

Feed this to the classify step so refusals are fast and confident.

> Not available: customer identity or demographics, credit scores, cost basis
> or true margin, inventory levels, test drives, service history, salesperson
> attribution, competitor data.

---

## MCP tool surface

Four tools, all read-only. The MCP server is the only runtime component that
opens the database.

```
get_schema_context(question: str | None) -> SchemaContext
run_sql(sql: str, limit: int = 500)      -> QueryResult   # rows only
list_tables()                            -> list[TableSummary]
sample_rows(table: str, n: int = 5)      -> list[dict]
```

`get_schema_context` accepts a question from day one. In stage 1 it ignores it
and returns everything. In stage 2 it uses it to retrieve a slice. The caller
never changes.

### run_sql must enforce

- `SELECT` or `WITH` only — reject DROP, UPDATE, DELETE, INSERT, ATTACH, PRAGMA
- Table allowlist: `dealers`, `vehicles`, `sales` only
- All referenced columns exist in `meta_columns`
- `LIMIT` present, injected if absent
- Query timeout (5s) and row cap

Validation uses sqlglot on the parsed AST. Not regex.

---

## Agent graph

State is one Pydantic model carrying: question, classification, schema context,
sql attempts, rows, answer, retry count, trace.

| Node | Job | Exits to |
|---|---|---|
| `classify` | answerable / ambiguous / unanswerable | refuse, clarify, or load_context |
| `load_context` | fetch metadata via MCP | generate_sql |
| `generate_sql` | Groq at temp 0, typed output | validate |
| `validate` | sqlglot AST checks | execute, or back to generate_sql |
| `execute` | MCP `run_sql` | inspect |
| `inspect` | rows / zero-rows / error | synthesize, or back to generate_sql |
| `synthesize` | rows to prose | verify |
| `verify` | every figure appears in rows | done, or back to synthesize (once) |

Backward edges are the reason LangGraph is used. Retry ceiling: 3 on the SQL
loop, 1 on synthesis.

`generate_sql` must return:

```python
class SQLPlan(BaseModel):
    sql: str
    tables_used: list[str]
    assumptions: list[str]   # e.g. "last year = 2025"
```

---

## API contract

```
POST /ask
  request:  {"question": "..."}
  response: {
    "answer": str,
    "sql": str | None,
    "assumptions": list[str],
    "rows": list[dict],
    "trace": list[str],
    "latency_ms": int
  }
```

Also `GET /health`.

MCP session, compiled graph, and cached metadata are initialised once in the
FastAPI `lifespan` handler.

---

## Three LLM calls, three jobs

| Call | Temperature | Output |
|---|---|---|
| classify | 0 | small enum + reason |
| generate SQL | 0 | strict Pydantic schema |
| synthesize | ~0.4 | free prose |

Lumping these into one call is what makes naive text-to-SQL brittle.

---

## Technology

| Layer | Choice |
|---|---|
| Data | SQLite |
| Metadata | PRAGMA (automatic) + YAML (authored) |
| Data access | MCP server — sole runtime DB access |
| SQL safety | sqlglot, AST-level, server-side |
| Orchestration | LangGraph, typed Pydantic state |
| LLM | Groq behind an `LLMProvider` interface |
| API | FastAPI on uvicorn, async |
| Contracts | Pydantic throughout |
| Frontend | Thin client calling `POST /ask` |

---

## Deliberately not used

**No vector database or embeddings.** With three tables the full metadata fits
comfortably in one prompt, so semantic retrieval would add failure modes
without adding capability.

**No multi-agent architecture.** One kind of question needs one specialist.

Both are staged for later. The interfaces are designed so enabling vector
retrieval is a configuration change, and a second retrieval mode is where a
multi-agent split will genuinely earn its place.

### Growth path

| Stage | Adds |
|---|---|
| 1 | Working single agent, `SCHEMA_MODE=full` |
| 2 | Vector schema retrieval, `SCHEMA_MODE=vector` |
| 3 | Retrieval over a worked-SQL-example library |
| 4 | Free-text column → supervisor + two specialists |

---

## Done when

25 known-answer questions pass — lookups, aggregations, joins, both planted
traps, refusals, clarifications — with 100% SQL validity and p95 latency under
8 seconds.

## Out of scope

Authentication, multi-tenancy, write operations, row-level security, real-time
ingestion.
