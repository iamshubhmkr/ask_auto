"""Golden-set evaluation: accuracy, SQL validity, latency p50/p95.

    python evals/run_evals.py                          # full paced run, in-process app
    python evals/run_evals.py --only T1,R1 --pace 0    # a subset, no pacing
    python evals/run_evals.py --url http://localhost:8000
    python evals/run_evals.py --print-reference        # show reference query results

Before asking anything, every reference_sql is re-run through the MCP server and
must still produce the expected values: the data is seeded, so drift is a bug.
The default target is the real app in-process (real Groq, real stdio MCP server,
real lifespan); --url targets a running uvicorn instead.
"""

import argparse
import asyncio
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

EVALS_DIR = Path(__file__).resolve().parent
QUESTIONS_FILE = EVALS_DIR / "questions.yaml"
RESULTS_DIR = EVALS_DIR / "results"
P95_TARGET_MS = 8000
DEFAULT_PACE_S = 40.0  # Groq account limit is 8K tokens/min; a question uses ~5K

Status = Literal["answered", "refused", "clarification_needed", "failed", "unverified"]


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
    values: list[float] = []
    not_values: list[float] = []
    labels: list[str] = []
    ordered: bool = False
    sql_patterns: list[str] = []


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    tier: str
    question: str
    expect: Expect
    reference_sql: str | None = None


class Outcome(BaseModel):
    id: str
    tier: str
    question: str
    passed: bool
    reasons: list[str]
    status: str | None
    answer: str
    sql: str | None
    generated_sql: bool
    retried_sql: bool
    latency_ms: int
    trace: list[str]


# --- Scoring (pure; tests/test_evals.py covers it) --------------------------------


def decimals(value: float) -> int:
    """Decimal places as shown, capped at 6 (float noise shows up as many)."""
    if isinstance(value, int):
        return 0
    text = repr(value)
    if "e" in text or "." not in text:
        return 6 if "e" in text else 0
    return min(len(text.split(".")[1]), 6)


def number_matches(cell: float, expected: float) -> bool:
    """Equal at the precision the cell shows, within 1%; rates may be fraction or percent."""
    for scale in (1.0, 100.0, 0.01):
        target = expected * scale
        if target == 0:
            if cell == 0:
                return True
            continue
        diff = abs(cell - target)
        if diff <= max(0.5 * 10 ** -decimals(cell), 0.005) and diff <= 0.01 * abs(target):
            return True
    return False


def numeric_cells(rows: list[dict[str, Any]]) -> list[float]:
    return [v for row in rows for v in row.values()
            if isinstance(v, int | float) and not isinstance(v, bool)]


def text_cells(row: dict[str, Any]) -> list[str]:
    return [v.strip().lower() for v in row.values() if isinstance(v, str)]


def check_rows(expect: Expect, rows: list[dict[str, Any]], *, negatives: bool = True
               ) -> list[str]:
    """Reasons the rows fail the expectation (empty list = pass)."""
    reasons = []
    numbers = numeric_cells(rows)
    for value in expect.values:
        if not any(number_matches(n, value) for n in numbers):
            reasons.append(f"expected value {value} not in rows")
    if negatives:
        for value in expect.not_values:
            if any(number_matches(n, value) for n in numbers):
                reasons.append(f"forbidden value {value} found in rows")
    for i, label in enumerate(expect.labels):
        wanted = label.lower()
        if expect.ordered:
            if i >= len(rows) or wanted not in text_cells(rows[i]):
                reasons.append(f"expected '{label}' in row {i + 1}")
        elif not any(wanted in text_cells(row) for row in rows):
            reasons.append(f"expected label '{label}' not in rows")
    return reasons


def score(question: Question, response: dict[str, Any], latency_ms: int) -> Outcome:
    expect = question.expect
    reasons = []
    status = response.get("status")
    if status != expect.status:
        reasons.append(f"status {status!r}, expected {expect.status!r}")
    sql = response.get("sql")
    if expect.status == "answered":
        reasons += check_rows(expect, response.get("rows", []))
        for pattern in expect.sql_patterns:
            if not re.search(pattern, sql or ""):
                reasons.append(f"SQL does not match {pattern!r}")
    trace = response.get("trace", [])
    return Outcome(
        id=question.id, tier=question.tier, question=question.question,
        passed=not reasons, reasons=reasons, status=status,
        answer=response.get("answer", response.get("detail", "")), sql=sql,
        generated_sql=any(t.startswith("generate_sql") for t in trace),
        retried_sql=any(t.startswith("generate_sql: attempt 2") for t in trace),
        latency_ms=latency_ms, trace=trace,
    )


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


# --- Reference check ----------------------------------------------------------


async def reference_rows(questions: list[Question]) -> dict[str, list[dict[str, Any]]]:
    """Run every reference_sql through the MCP server (evals never open the DB)."""
    from askauto.mcp_client import McpTools, stdio_params

    rows = {}
    async with McpTools.connect(stdio_params()) as tools:
        for q in questions:
            if q.reference_sql:
                result = await tools.run_sql(q.reference_sql)
                if not result.ok:
                    raise SystemExit(f"{q.id}: reference SQL failed: {result.error.message}")
                rows[q.id] = result.rows
    return rows


def check_references(questions: list[Question], rows: dict[str, list[dict[str, Any]]]
                     ) -> list[str]:
    problems = []
    for q in questions:
        if q.expect.status == "answered" and not (q.expect.values or q.expect.labels):
            problems.append(f"{q.id}: no expected values or labels")
        if q.id in rows:
            # not_values are deliberately present in some reference rows (T1's gross).
            problems += [f"{q.id}: {r}" for r in check_rows(q.expect, rows[q.id],
                                                             negatives=False)]
    return problems


# --- Running ------------------------------------------------------------------


def ask_all(questions: list[Question], url: str | None, pace: float
            ) -> tuple[str, list[Outcome]]:
    import httpx

    if url:
        client_cm: Any = httpx.Client(base_url=url, timeout=120)
    else:
        from fastapi.testclient import TestClient

        from askauto.api import create_app
        client_cm = TestClient(create_app())

    outcomes = []
    with client_cm as client:
        model = client.get("/health").json().get("model", "?")
        for i, q in enumerate(questions):
            if i and pace:
                time.sleep(pace)
            start = time.perf_counter()
            response = client.post("/ask", json={"question": q.question})
            latency_ms = round((time.perf_counter() - start) * 1000)
            outcome = score(q, response.json(), latency_ms)
            mark = "PASS" if outcome.passed else "FAIL"
            print(f"  [{mark}] {q.id:3} {latency_ms:6d} ms  {q.question}", flush=True)
            for reason in outcome.reasons:
                print(f"           - {reason}", flush=True)
            outcomes.append(outcome)
    return model, outcomes


def scorecard(model: str, outcomes: list[Outcome]) -> dict[str, Any]:
    latencies = [o.latency_ms for o in outcomes]
    generated = [o for o in outcomes if o.generated_sql]
    tiers: dict[str, list[Outcome]] = {}
    for o in outcomes:
        tiers.setdefault(o.tier, []).append(o)
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "questions": len(outcomes),
        "passed": sum(o.passed for o in outcomes),
        "accuracy": round(sum(o.passed for o in outcomes) / len(outcomes), 4),
        "by_tier": {t: f"{sum(o.passed for o in os)}/{len(os)}" for t, os in tiers.items()},
        "sql_validity": round(sum(o.sql is not None for o in generated) / len(generated), 4)
        if generated else None,
        "first_attempt_sql_validity": round(
            sum(not o.retried_sql for o in generated) / len(generated), 4)
        if generated else None,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "outcomes": [o.model_dump() for o in outcomes],
    }


def print_scorecard(card: dict[str, Any]) -> bool:
    met = (card["passed"] == card["questions"]
           and card["sql_validity"] == 1.0
           and card["latency_p95_ms"] < P95_TARGET_MS)
    print(f"\nScorecard ({card['model']}, {card['timestamp']})")
    print(f"  Accuracy        {card['passed']}/{card['questions']} = {card['accuracy']:.0%}")
    print(f"  By tier         {card['by_tier']}")
    print(f"  SQL validity    {card['sql_validity']:.0%} (first attempt "
          f"{card['first_attempt_sql_validity']:.0%})")
    print(f"  Latency         p50 {card['latency_p50_ms']} ms, p95 {card['latency_p95_ms']} ms "
          f"(target < {P95_TARGET_MS})")
    print(f"  Stage 1 bar     {'MET' if met else 'NOT MET'} "
          "(25/25, 100% SQL validity, p95 < 8 s)")
    return met


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="evaluate a running server instead of in-process")
    parser.add_argument("--pace", type=float, default=DEFAULT_PACE_S,
                        help="seconds between questions (rate limit)")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--print-reference", action="store_true")
    args = parser.parse_args()

    raw = yaml.safe_load(QUESTIONS_FILE.read_text(encoding="utf-8"))["questions"]
    questions = [Question.model_validate(q) for q in raw]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q.id in wanted]

    rows = asyncio.run(reference_rows(questions))
    if args.print_reference:
        for q in questions:
            if q.id in rows:
                print(f"{q.id}: {json.dumps(rows[q.id])}")
        return 0
    problems = check_references(questions, rows)
    if problems:
        print("Reference check FAILED (data or questions.yaml changed):")
        print("\n".join(f"  - {p}" for p in problems))
        return 2
    print(f"Reference check passed for {len(rows)} reference queries.")

    model, outcomes = ask_all(questions, args.url, args.pace)
    card = scorecard(model, outcomes)
    met = print_scorecard(card)
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M}.json"
    path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(f"  Saved           {path.relative_to(EVALS_DIR.parent)}")
    return 0 if met else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
