"""SQL guard for run_sql. Validates on the sqlglot AST, never by string matching.

Pure: no database access. The column catalogue is passed in (built from
meta_columns), so the guard is unit-testable and the agent's validate node can
reuse it. The server-side call is the one that matters: assume any agent-side
check was bypassed.

Every rejection carries a specific reason, because the agent feeds it back into
its next SQL attempt.
"""

import sqlglot
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

DIALECT = "sqlite"
ALLOWED_TABLES = frozenset({"dealers", "vehicles", "sales"})

# Allowlist of statement roots. WITH ... is attached to these, so CTEs pass.
# Anything else (DML, DDL, PRAGMA, ATTACH, or a fallback Command) is rejected.
READ_ROOTS = (exp.Select, exp.SetOperation)
# Belt and braces: none of these may appear anywhere in the tree.
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.Pragma, exp.Attach, exp.Detach, exp.Command, exp.Transaction, exp.Commit,
)

Catalog = dict[str, dict[str, str]]
"""table name -> column name -> SQLite type, built from meta_columns."""


class GuardResult(BaseModel):
    ok: bool
    sql: str  # the SQL to execute (LIMIT applied) when ok; the input otherwise
    reason: str | None = None
    tables_used: list[str] = []


def _reject(sql: str, reason: str) -> GuardResult:
    return GuardResult(ok=False, sql=sql, reason=reason)


def _ambiguity_hint(root: exp.Expression, catalog: Catalog, tables: set[str]) -> str | None:
    """Name an unqualified column that exists in more than one joined table.

    sqlglot reports these as "could not be resolved"; SQLite calls them ambiguous.
    The specific wording tells the next attempt exactly what to fix.
    """
    for column in root.find_all(exp.Column):
        if column.table:
            continue
        owners = sorted(t for t in tables if column.name.lower() in catalog.get(t, {}))
        if len(owners) > 1:
            return (f"Column '{column.name}' is ambiguous: it exists in "
                    f"{' and '.join(owners)}. Qualify it, e.g. {owners[0]}.{column.name}.")
    return None


def guard_sql(sql: str, catalog: Catalog, max_rows: int) -> GuardResult:
    """Validate a read-only query and return it with a LIMIT of at most max_rows."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError as exc:
        return _reject(sql, f"SQL could not be parsed: {str(exc).splitlines()[0]}")

    if len(statements) != 1:
        return _reject(sql, f"Exactly one statement is allowed; got {len(statements)}.")
    root = statements[0]

    if not isinstance(root, READ_ROOTS):
        return _reject(
            sql, f"Only SELECT or WITH queries are allowed; got {root.key.upper()}."
        )
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _reject(sql, f"{node.key.upper()} is not allowed inside a query.")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    tables_used = set()
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            return _reject(
                sql, f"Table-valued functions are not allowed: {table.this.sql(DIALECT)}."
            )
        if table.args.get("db") or table.args.get("catalog"):
            return _reject(
                sql, f"Qualified table '{table.sql(DIALECT)}' is not allowed; use '{name}'."
            )
        if name in cte_names:
            continue
        if name not in ALLOWED_TABLES:
            return _reject(
                sql,
                f"Table '{name}' is not queryable. "
                f"Allowed tables: {', '.join(sorted(ALLOWED_TABLES))}.",
            )
        tables_used.add(name)

    try:
        qualify(root.copy(), schema=catalog, dialect=DIALECT,
                validate_qualify_columns=True)
    except SqlglotError as exc:
        return _reject(sql, _ambiguity_hint(root, catalog, tables_used)
                       or f"Column check failed: {exc}.")

    limit = root.args.get("limit")
    if limit is None:
        root = root.limit(max_rows)
    else:
        value = limit.expression
        if not (isinstance(value, exp.Literal) and value.is_int):
            return _reject(sql, "LIMIT must be an integer literal.")
        if int(value.this) > max_rows:
            root = root.limit(max_rows)

    return GuardResult(ok=True, sql=root.sql(dialect=DIALECT), tables_used=sorted(tables_used))
