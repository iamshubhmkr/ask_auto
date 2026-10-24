"""verify: figures in the answer must come from the rows, at the precision shown."""

import pytest

from askauto.agent.verify import verify_answer
from askauto.mcp_server.models import QueryResult


def result(*rows: dict) -> QueryResult:
    return QueryResult(ok=True, sql="SELECT ...", rows=list(rows), row_count=len(rows),
                       columns=list(rows[0]) if rows else [])


NET = result({"net_revenue": 166989244.64})


def check(text: str, rows: QueryResult = NET, question: str = "What was net revenue?",
          assumptions: list[str] | None = None) -> bool:
    return verify_answer(text, rows, question=question, assumptions=assumptions or [],
                         sql="SELECT SUM(x) FROM t").ok


@pytest.mark.parametrize("text", [
    "Net revenue was $166,989,244.64.",
    "Net revenue was $167.0M.",
    "Net revenue was about $167 million.",
    "Net revenue was $0.17B.",
    "Net revenue was 166989244.64 dollars.",
])
def test_rounded_and_scaled_figures_match(text: str) -> None:
    assert check(text)


def test_percentages_match_fractions_and_percent_values() -> None:
    rows = result({"discount_rate": 0.0351, "ev_share_pct": 36.2})
    assert check("Discount rate was 3.5% and EV share 36.2%.", rows)
    assert check("Discount rate was 3.51 percent.", rows)


def test_counts_with_commas_match() -> None:
    assert check("We sold 5,000 units.", result({"units": 5000}))


def test_row_count_is_a_valid_figure() -> None:
    rows = result({"region": "West"}, {"region": "Midwest"}, {"region": "Northeast"})
    assert check("Sales came from 3 regions.", rows)


def test_numbers_from_question_and_assumptions_are_context() -> None:
    assert check("In 2025 net revenue was $167.0M.", assumptions=["last year = 2025"])
    assert check("The top 5 dealers earned $167.0M.", question="Top 5 dealers by revenue?")


def test_numbers_inside_text_cells_are_context() -> None:
    rows = result({"model": "Ioniq 5", "units": 120}, {"model": "F-150", "units": 300})
    assert check("The Ioniq 5 sold 120 units and the F-150 sold 300.", rows)


def test_invented_figure_fails() -> None:
    verification = verify_answer("Net revenue was $200M, up 12% on 2024.", NET,
                                 question="What was net revenue?", assumptions=[], sql="")
    assert not verification.ok
    assert verification.unsupported == ["$200M", "12%", "2024"]


def test_wrong_precision_fails() -> None:
    # 166.99M rounds to 167.0M, not 166.5M.
    assert not check("Net revenue was $166.5M.")


def test_answer_without_figures_passes() -> None:
    assert check("No sales matched that filter.")
