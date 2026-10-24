"""Unit tests for the run_sql guard. Pure: no database, catalogue given inline."""

import pytest

from askauto.mcp_server.sql_guard import Catalog, GuardResult, guard_sql

CATALOG: Catalog = {
    "dealers": {"dealer_id": "TEXT", "dealer_name": "TEXT", "city": "TEXT",
                "region": "TEXT", "tier": "TEXT"},
    "vehicles": {"vehicle_id": "TEXT", "make": "TEXT", "model": "TEXT",
                 "model_year": "INTEGER", "fuel_type": "TEXT", "msrp": "REAL"},
    "sales": {"sale_id": "TEXT", "dealer_id": "TEXT", "vehicle_id": "TEXT",
              "sale_date": "TEXT", "sale_price": "REAL", "discount_amount": "REAL",
              "finance_type": "TEXT"},
}
MAX_ROWS = 500


def guard(sql: str) -> GuardResult:
    return guard_sql(sql, CATALOG, MAX_ROWS)


@pytest.mark.parametrize("sql", [
    "DROP TABLE sales",
    "UPDATE sales SET sale_price = 0",
    "DELETE FROM sales",
    "INSERT INTO dealers VALUES ('D99', 'x', 'y', 'West', 'Standard')",
    "REPLACE INTO dealers VALUES ('D01', 'x', 'y', 'West', 'Standard')",
    "PRAGMA table_info(sales)",
    "ATTACH DATABASE 'other.db' AS other",
    "CREATE TABLE x (a INT)",
    "VACUUM",
    "BEGIN",
])
def test_non_select_statements_rejected(sql: str) -> None:
    result = guard(sql)
    assert not result.ok
    assert "Only SELECT or WITH" in result.reason


def test_multiple_statements_rejected() -> None:
    result = guard("SELECT 1; DROP TABLE sales")
    assert not result.ok
    assert "Exactly one statement" in result.reason


@pytest.mark.parametrize("sql, table", [
    ("SELECT * FROM meta_glossary", "meta_glossary"),
    ("SELECT term FROM meta_glossary JOIN sales ON 1 = 1", "meta_glossary"),
    ("SELECT name FROM sqlite_master", "sqlite_master"),
    ("SELECT region FROM dealers WHERE dealer_id IN (SELECT table_name FROM meta_tables)",
     "meta_tables"),
])
def test_tables_outside_allowlist_rejected(sql: str, table: str) -> None:
    result = guard(sql)
    assert not result.ok
    assert f"Table '{table}' is not queryable" in result.reason


def test_schema_qualified_table_rejected() -> None:
    result = guard("SELECT * FROM main.sales")
    assert not result.ok
    assert "Qualified table" in result.reason


def test_table_valued_function_rejected() -> None:
    result = guard("SELECT * FROM pragma_table_info('sales')")
    assert not result.ok
    assert "Table-valued functions" in result.reason


@pytest.mark.parametrize("sql, column", [
    ("SELECT revenue FROM sales", "revenue"),
    ("SELECT d.regionx FROM dealers d", "regionx"),
    ("SELECT region FROM sales", "region"),  # exists, but not in the table queried
])
def test_unknown_column_rejected(sql: str, column: str) -> None:
    result = guard(sql)
    assert not result.ok
    assert "Column check failed" in result.reason
    assert column in result.reason


def test_missing_limit_injected() -> None:
    result = guard("SELECT region FROM dealers")
    assert result.ok
    assert result.sql.endswith("LIMIT 500")


def test_large_limit_clamped() -> None:
    result = guard("SELECT region FROM dealers LIMIT 10000")
    assert result.ok
    assert result.sql.endswith("LIMIT 500")


def test_small_limit_and_offset_kept() -> None:
    result = guard("SELECT region FROM dealers LIMIT 10 OFFSET 5")
    assert result.ok
    assert result.sql.endswith("LIMIT 10 OFFSET 5")


def test_non_literal_limit_rejected() -> None:
    result = guard("SELECT region FROM dealers LIMIT (SELECT 1000000)")
    assert not result.ok
    assert "LIMIT must be an integer literal" in result.reason


def test_unparseable_sql_rejected() -> None:
    result = guard("SELEC region FROM dealers")
    assert not result.ok


@pytest.mark.parametrize("sql", [
    # valid join, aliases, ORDER BY a projection alias
    "SELECT d.region, SUM(s.sale_price - s.discount_amount) AS net FROM sales s "
    "JOIN dealers d ON s.dealer_id = d.dealer_id GROUP BY d.region ORDER BY net DESC",
    # trap 2: exact spelling, USING join, unqualified columns
    "SELECT COUNT(*) FROM sales JOIN dealers USING (dealer_id) WHERE region = 'Northeast'",
    # CTE
    "WITH t AS (SELECT dealer_id, SUM(sale_price) AS g FROM sales GROUP BY dealer_id) "
    "SELECT dealer_id, g FROM t ORDER BY g DESC",
    # UNION
    "SELECT make FROM vehicles UNION SELECT region FROM dealers",
    # glossary: EV share needs the vehicles join
    "SELECT AVG(CASE WHEN fuel_type = 'Electric' THEN 1.0 ELSE 0.0 END) "
    "FROM sales JOIN vehicles USING (vehicle_id)",
    # glossary: net revenue + 'last year' filter with its subquery
    "SELECT SUM(sale_price - discount_amount) FROM sales "
    "WHERE CAST(strftime('%Y', sale_date) AS INTEGER) = "
    "(SELECT CAST(strftime('%Y', MAX(sale_date)) AS INTEGER) - 1 FROM sales)",
])
def test_valid_queries_accepted(sql: str) -> None:
    result = guard(sql)
    assert result.ok, result.reason
    assert "LIMIT" in result.sql


def test_tables_used_reported() -> None:
    result = guard("SELECT region, COUNT(*) FROM sales JOIN dealers USING (dealer_id) "
                   "GROUP BY region")
    assert result.tables_used == ["dealers", "sales"]


def test_cte_name_not_treated_as_table() -> None:
    result = guard("WITH regional AS (SELECT region FROM dealers) SELECT * FROM regional")
    assert result.ok, result.reason
    assert result.tables_used == ["dealers"]
