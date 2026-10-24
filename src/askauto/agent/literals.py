"""Agent-side check: string literals compared to categorical columns must be allowed values.

Trap 2 (`region = 'North East'`) is valid SQL that silently returns zero rows.
This turns it into a validation error that lists the allowed values, so the
retry edge can fix it. Pure sqlglot; the server guard is unchanged.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from askauto.mcp_server.models import SchemaContext


def _allowed_values(context: SchemaContext) -> dict[tuple[str, str], set[str]]:
    return {
        (table.name, column.name): set(column.allowed_values)
        for table in context.tables
        for column in table.columns
        if column.is_categorical and column.allowed_values
        and all(isinstance(v, str) for v in column.allowed_values)
    }


def _comparisons(root: exp.Expression) -> list[tuple[exp.Column, list[str]]]:
    """(column, string literals) for `col = 'x'`, `col != 'x'` and `col IN ('x', ...)`."""
    found = []
    for node in root.find_all(exp.EQ, exp.NEQ, exp.In):
        if isinstance(node, exp.In):
            column, candidates = node.this, node.expressions
        else:
            column, other = node.left, node.right
            if isinstance(column, exp.Literal):
                column, other = other, column
            candidates = [other]
        strings = [c.this for c in candidates if isinstance(c, exp.Literal) and c.is_string]
        if isinstance(column, exp.Column) and strings:
            found.append((column, strings))
    return found


def check_literals(sql: str, context: SchemaContext) -> str | None:
    """Return a retry-ready error message, or None when every literal is allowed."""
    try:
        root = sqlglot.parse_one(sql, read="sqlite")
    except SqlglotError:
        return None  # parse errors are the SQL guard's to report
    allowed = _allowed_values(context)
    aliases = {t.alias_or_name.lower(): t.name.lower() for t in root.find_all(exp.Table)}

    for column, strings in _comparisons(root):
        name = column.name.lower()
        if column.table:
            tables = {aliases.get(column.table.lower())}
        else:
            tables = set(aliases.values())
        valid_sets = [allowed[(t, name)] for t in tables if (t, name) in allowed]
        if not valid_sets:
            continue  # not a categorical column we know (or a CTE column)
        valid = set().union(*valid_sets)
        for value in strings:
            if value not in valid:
                options = ", ".join(f"'{v}'" for v in sorted(valid))
                return (f"'{value}' is not an allowed value for {column.name}; "
                        f"it would match no rows. Use one of: {options}.")
    return None
