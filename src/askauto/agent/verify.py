"""verify: every figure in the answer must come from the query rows.

Deterministic, no LLM. A figure matches when some numeric cell rounds to it at
the precision written, allowing for $ / commas, percentages and K/M/B scaling:
"$167.0M" matches 166989244.64, "3.5%" matches 0.0351.

Numbers that also appear in the question, the assumptions, the SQL or a text
cell are context, not claims (the year 2025, "top 5", "Ioniq 5"), and are skipped.

This reads the answer text as a check on the LLM, as the spec's verify step
requires. It never extracts data from prose for use downstream.
"""

import re

from askauto.agent.state import FigureCheck, Verification
from askauto.mcp_server.models import QueryResult

FIGURE = re.compile(
    r"""
    (?<![\w.\-/])                       # not inside a word, decimal, code (F-150) or date
    (?P<sign>-)?\$?
    (?P<int>\d{1,3}(?:,\d{3})+|\d+)
    (?:\.(?P<dec>\d+))?
    (?:\s*(?P<unit>%|percent\b|[kKmMbB]\b|thousand\b|million\b|billion\b|bn\b))?
    """,
    re.VERBOSE,
)
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
         "b": 1e9, "bn": 1e9, "billion": 1e9}


class Figure:
    def __init__(self, match: re.Match[str]) -> None:
        self.text = match.group(0).strip()
        self.raw = float(match.group("int").replace(",", "") + "." + (match.group("dec") or "0"))
        self.decimals = len(match.group("dec") or "")
        unit = (match.group("unit") or "").lower()
        self.percent = unit in ("%", "percent")
        self.scale = SCALE.get(unit, 1.0)

    def matches(self, value: float) -> bool:
        candidates = [value * 100, value] if self.percent else [value]
        tolerance = 0.5 * 10 ** -self.decimals + 1e-9
        return any(abs(abs(c) / self.scale - self.raw) <= tolerance for c in candidates)


def figures_in(text: str) -> list[Figure]:
    return [Figure(m) for m in FIGURE.finditer(text)]


def verify_answer(
    text: str, result: QueryResult, *, question: str, assumptions: list[str], sql: str
) -> Verification:
    # An empty result is not a measured zero, so "0" is only a valid figure when
    # rows came back (e.g. "3 regions").
    numbers: list[float] = [float(result.row_count)] if result.row_count else []
    context = [question, sql, *assumptions]
    for row in result.rows:
        for cell in row.values():
            if isinstance(cell, bool):
                continue
            if isinstance(cell, int | float):
                numbers.append(float(cell))
            elif isinstance(cell, str):
                context.append(cell)
    ignored = {f.raw for chunk in context for f in figures_in(chunk)}

    checks = []
    for figure in figures_in(text):
        if figure.raw in ignored and figure.scale == 1.0 and not figure.percent:
            continue
        checks.append(FigureCheck(text=figure.text, value=figure.raw,
                                  matched=any(figure.matches(n) for n in numbers)))
    return Verification(ok=all(c.matched for c in checks), figures=checks)


def describe(verification: Verification) -> str:
    total = len(verification.figures)
    matched = total - len(verification.unsupported)
    note = f"{matched}/{total} figures found in rows"
    return note if verification.ok else f"{note}; not found: {', '.join(verification.unsupported)}"
