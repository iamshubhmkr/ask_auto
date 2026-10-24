"""Agent-side literal check: categorical comparisons must use allowed values (trap 2)."""

import pytest

from askauto.agent.literals import check_literals
from askauto.mcp_server.models import ColumnInfo, SchemaContext, TableInfo


def column(name: str, allowed: list[str] | None = None) -> ColumnInfo:
    return ColumnInfo(name=name, data_type="TEXT", is_primary_key=False, is_nullable=False,
                      is_categorical=allowed is not None, distinct_count=len(allowed or []),
                      allowed_values=allowed, sample_values=None if allowed else ["a", "b", "c"],
                      description="")


CONTEXT = SchemaContext(mode="full", joins=[], glossary=[], unanswerable=[], tables=[
    TableInfo(name="dealers", description="", grain="", row_count=20, columns=[
        column("dealer_id"), column("region", ["Midwest", "Northeast", "Southeast", "West"])]),
    TableInfo(name="vehicles", description="", grain="", row_count=40, columns=[
        column("vehicle_id"), column("model", ["CR-V", "F-150", "Ioniq 5"])]),
    TableInfo(name="sales", description="", grain="", row_count=5000, columns=[
        column("sale_date"), column("dealer_id"), column("vehicle_id")]),
])


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM dealers WHERE region = 'Northeast'",
    "SELECT COUNT(*) FROM dealers d WHERE d.region IN ('West', 'Midwest')",
    "SELECT COUNT(*) FROM dealers WHERE 'Southeast' = region",
    "SELECT COUNT(*) FROM vehicles WHERE model = 'F-150'",
    "SELECT COUNT(*) FROM sales WHERE sale_date = '2025-01-01'",  # not categorical
    "SELECT COUNT(*) FROM dealers WHERE LOWER(region) = 'northeast'",  # function: skipped
    "WITH r AS (SELECT region AS zone FROM dealers) SELECT * FROM r WHERE zone = 'x'",
])
def test_allowed_or_unchecked_literals_pass(sql: str) -> None:
    assert check_literals(sql, CONTEXT) is None


@pytest.mark.parametrize("sql, bad", [
    ("SELECT COUNT(*) FROM dealers WHERE region = 'North East'", "North East"),
    ("SELECT COUNT(*) FROM sales JOIN dealers d USING (dealer_id) WHERE d.region = 'NE'", "NE"),
    ("SELECT COUNT(*) FROM dealers WHERE region IN ('West', 'Pacific')", "Pacific"),
    ("SELECT COUNT(*) FROM dealers WHERE region != 'northeast'", "northeast"),
    ("SELECT COUNT(*) FROM vehicles WHERE model = 'F150'", "F150"),
])
def test_disallowed_literal_is_named_with_the_options(sql: str, bad: str) -> None:
    message = check_literals(sql, CONTEXT)
    assert message is not None
    assert message.startswith(f"'{bad}' is not an allowed value")


def test_message_lists_the_exact_spellings() -> None:
    message = check_literals("SELECT * FROM dealers WHERE region = 'North East'", CONTEXT)
    assert "'Midwest', 'Northeast', 'Southeast', 'West'" in message


def test_unparseable_sql_is_left_to_the_guard() -> None:
    assert check_literals("SELEC nonsense", CONTEXT) is None
