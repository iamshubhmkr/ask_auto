"""Prompt construction. Every fact about the data comes from a SchemaContext,
which the MCP server builds from the meta_* tables.

This file holds instructions only. It must never name a business table, column
or category value: tests/test_agent.py checks the source for that.
"""

import json
from typing import Any

from askauto.agent.state import SQLAttempt, SQLPlan
from askauto.mcp_server.models import ColumnInfo, QueryResult, SchemaContext
from askauto.providers import Message

SYNTH_MAX_ROWS = 50


def _literal(value: Any) -> str:
    return f"'{value}'" if isinstance(value, str) else str(value)


def _column_line(column: ColumnInfo, detailed: bool) -> str:
    flags = ", primary key" if column.is_primary_key else ""
    line = f"- {column.name} {column.data_type}{flags}: {column.description}"
    if not detailed:
        return line
    if column.allowed_values is not None:
        values = ", ".join(_literal(v) for v in column.allowed_values)
        line += f"\n  Allowed values (use exactly as written): {values}"
    elif column.sample_values is not None:
        values = ", ".join(_literal(v) for v in column.sample_values)
        line += f"\n  Sample values (min, median, max): {values}"
    return line


def render_schema(context: SchemaContext, detailed: bool = True) -> str:
    """The data description every prompt is built on."""
    parts = ["## Tables"]
    for table in context.tables:
        parts.append(f"\n### {table.name} -- {table.grain} ({table.row_count} rows)")
        parts.append(table.description)
        parts.extend(_column_line(c, detailed) for c in table.columns)

    parts.append("\n## Joins")
    parts.extend(f"- {j.from_table}.{j.from_column} = {j.to_table}.{j.to_column} "
                 f"({j.cardinality})" for j in context.joins)

    parts.append("\n## Glossary (business terms; use the SQL expression verbatim)")
    if detailed:
        parts.extend(f"- {g.term}: {g.sql_expression}\n  {g.notes}" for g in context.glossary)
    else:
        parts.append(", ".join(g.term for g in context.glossary))

    parts.append("\n## Not available in this data")
    parts.extend(f"- {u.topic}: {u.reason}" for u in context.unanswerable)
    return "\n".join(parts)


CLASSIFY_INSTRUCTIONS = """\
You triage questions for an analytics assistant over a car dealer group's database.
Decide one label:
- answerable: the data described below can answer it. Glossary definitions and
  sensible, stated assumptions count (for example "revenue" has a glossary default,
  so it is not ambiguous).
- ambiguous: it could reasonably mean materially different things, the choice
  changes the answer, and nothing below settles it. Give one clarifying_question.
  Spelling, spacing, case or abbreviation differences from stored category values
  are NOT ambiguity: the SQL step maps them to the exact stored value.
- unanswerable: it needs data listed under "Not available" or otherwise absent.
  In reason, name exactly what is missing.
Prefer answerable whenever a glossary term or a stated assumption resolves it."""

SQL_INSTRUCTIONS = """\
You write one SQLite query that answers the user's question. Rules:
1. Use only the tables, columns and joins described below.
2. When the question uses a glossary term or alias, use its SQL expression verbatim.
3. Filter categorical columns only with their listed allowed values, written exactly
   as shown. Map the user's wording to the matching value and record that mapping
   in assumptions.
4. Exactly one SELECT or WITH statement. No trailing semicolon, no comments.
   When joining tables, prefix every column with its table name or alias.
5. Give every computed column a clear alias.
6. Aggregate in SQL so the result holds the figures the answer needs.
7. List every interpretation in assumptions: resolved time periods (state the
   actual year), metric definitions used, and value mappings."""

SYNTH_INSTRUCTIONS = """\
You answer a business user's question from the result of a SQL query.
Rules:
- Use only figures that appear in the result rows. You may round them
  (166989244.64 -> $167.0M) or show a fraction as a percentage (0.351 -> 35.1%).
- Never compute new figures (no sums, differences, averages or growth rates)
  that are not already in the rows.
- Mention the key assumptions briefly in plain words.
- Two to four sentences of plain English. No SQL, no markdown tables."""


def classify_messages(question: str, context: SchemaContext) -> list[Message]:
    system = f"{CLASSIFY_INSTRUCTIONS}\n\n{render_schema(context, detailed=False)}"
    return [Message(role="system", content=system), Message(role="user", content=question)]


def sql_messages(
    question: str, context: SchemaContext, failed: list[SQLAttempt]
) -> list[Message]:
    """`failed` holds earlier attempts and their errors, so a retry can fix them."""
    system = f"{SQL_INSTRUCTIONS}\n\n{render_schema(context)}"
    user = question
    if failed:
        lines = [f"- SQL: {a.plan.sql}\n  Error: {a.error}" for a in failed]
        user += "\n\nPrevious attempts failed; fix the cause:\n" + "\n".join(lines)
    return [Message(role="system", content=system), Message(role="user", content=user)]


def synthesize_messages(
    question: str, plan: SQLPlan, result: QueryResult, unsupported: list[str]
) -> list[Message]:
    """`unsupported` lists figures a previous answer used that were not in the rows."""
    payload = {
        "question": question,
        "assumptions": plan.assumptions,
        "columns": result.columns,
        "rows": result.rows[:SYNTH_MAX_ROWS],
        "row_count": result.row_count,
        "rows_truncated": result.row_cap_hit or result.row_count > SYNTH_MAX_ROWS,
    }
    if result.row_count == 0:
        payload["sql"] = plan.sql
    user = json.dumps(payload, default=str)
    if result.row_count == 0:
        user += ("\n\nThe query returned no rows. Explain in plain words which filters "
                 "matched nothing, using the SQL and assumptions. Do not state any figure "
                 "such as 0 or $0: an empty result is not a measured zero.")
    if unsupported:
        user += ("\n\nYour previous answer used figures that are not in the rows: "
                 f"{', '.join(unsupported)}. Rewrite it using only figures from the rows.")
    return [Message(role="system", content=SYNTH_INSTRUCTIONS),
            Message(role="user", content=user)]
