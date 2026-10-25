"""Eval scoring rules. Pure functions; no LLM, no database."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_evals",
                                                  REPO_ROOT / "evals" / "run_evals.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ev = load_runner()


def question(**expect: object) -> "ev.Question":
    return ev.Question(id="X1", tier="test", question="q?",
                       expect=ev.Expect(**{"status": "answered", **expect}))


def response(rows: list[dict], sql: str | None = "SELECT 1", status: str = "answered",
             trace: list[str] | None = None) -> dict:
    return {"answer": "a", "sql": sql, "rows": rows, "status": status,
            "trace": trace or ["generate_sql: attempt 1"]}


@pytest.mark.parametrize("cell, expected", [
    (52228431.6, 52228431.6),
    (52228431.600000024, 52228431.6),  # float noise
    (52228432, 52228431.6),  # rounded to an integer in SQL
    (0.36, 0.362171),  # rate rounded to 2 dp
    (36.22, 0.362171),  # rate as a percentage
    (351, 351),
])
def test_number_matches(cell: float, expected: float) -> None:
    assert ev.number_matches(cell, expected)


@pytest.mark.parametrize("cell, expected", [
    (350, 351),  # off by one
    (0, 0.362171),  # zero is not a rounded rate
    (0.04, 0.0351),  # within 0.005 but 14% out
    (54134516.45, 52228431.6),  # gross instead of net
])
def test_number_mismatches(cell: float, expected: float) -> None:
    assert not ev.number_matches(cell, expected)


def test_values_and_trap_patterns() -> None:
    q = question(values=[100.0], not_values=[110.0],
                 sql_patterns=[r"(\w+\.)?sale_price\s*-\s*(\w+\.)?discount_amount"])
    good = ev.score(q, response([{"net": 100.0}],
                                sql="SELECT SUM(s.sale_price - s.discount_amount) FROM sales s"), 5)
    assert good.passed
    gross = ev.score(q, response([{"rev": 110.0}], sql="SELECT SUM(sale_price) FROM sales"), 5)
    assert not gross.passed
    assert any("forbidden value" in r for r in gross.reasons)
    assert any("SQL does not match" in r for r in gross.reasons)


def test_ordered_labels_check_row_position() -> None:
    q = question(labels=["Harborview Auto Group", "Peachtree Motor Group"], ordered=True)
    rows = [{"dealer": "Harborview Auto Group"}, {"dealer": "Peachtree Motor Group"}]
    assert ev.score(q, response(rows), 5).passed
    assert not ev.score(q, response(rows[::-1]), 5).passed


def test_unordered_labels_are_case_insensitive() -> None:
    q = question(labels=["atlanta"])
    assert ev.score(q, response([{"city": "Atlanta"}]), 5).passed


def test_status_mismatch_fails() -> None:
    q = ev.Question(id="R1", tier="refusal", question="credit?",
                    expect=ev.Expect(status="refused"))
    outcome = ev.score(q, response([], sql=None, status="answered"), 5)
    assert not outcome.passed
    assert outcome.reasons == ["status 'answered', expected 'refused'"]


def test_retry_is_recorded() -> None:
    q = question(values=[1.0])
    trace = ["generate_sql: attempt 1", "validate: rejected", "generate_sql: attempt 2"]
    assert ev.score(q, response([{"n": 1}], trace=trace), 5).retried_sql


def test_reference_check_ignores_not_values() -> None:
    # T1's reference query returns the gross figure on purpose.
    q = question(values=[100.0], not_values=[110.0])
    assert ev.check_references([q], {"X1": [{"net": 100.0, "gross": 110.0}]}) == []


def test_percentile_nearest_rank() -> None:
    assert ev.percentile([100, 200, 300, 400], 50) == 200
    assert ev.percentile(list(range(1, 21)), 95) == 19
