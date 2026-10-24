"""Regression tests for scripts/build_database.py: DDL, both planted traps, determinism.

The build script is loaded by path and does all the SQLite work, so these tests
never import sqlite3 themselves.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_database.py"


def load_build_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_database", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bd = load_build_script()


def build_and_verify(db_path: Path, seed: int = bd.DEFAULT_SEED) -> "bd.BuildReport":
    bd.build(db_path, seed)
    return bd.verify(db_path)


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> "bd.BuildReport":
    return build_and_verify(tmp_path_factory.mktemp("db") / "askauto.db")


def test_all_checks_pass(report: "bd.BuildReport") -> None:
    failed = [f"{c.name}: {c.detail}" for c in report.checks if not c.passed]
    assert not failed


def test_foreign_keys_declared(report: "bd.BuildReport") -> None:
    # Empty here means the tables weren't built from the REFERENCES DDL.
    assert report.foreign_keys == [
        "sales.dealer_id -> dealers.dealer_id",
        "sales.vehicle_id -> vehicles.vehicle_id",
    ]


def test_row_counts(report: "bd.BuildReport") -> None:
    assert report.row_counts == {"dealers": 20, "vehicles": 40, "sales": 5000}


def test_trap1_gross_exceeds_net_by_2_to_5_pct(report: "bd.BuildReport") -> None:
    assert report.gross_revenue > report.net_revenue
    assert 2.0 <= report.discount_gap_pct <= 5.0


def test_trap2_regions_spelled_exactly(report: "bd.BuildReport") -> None:
    assert report.regions == ["Midwest", "Northeast", "Southeast", "West"]
    assert report.north_east_rows == 0


def test_categorical_values_match_spec(report: "bd.BuildReport") -> None:
    assert report.categorical_values["vehicles.make"] == sorted(bd.MAKES)
    assert report.categorical_values["vehicles.fuel_type"] == sorted(bd.FUEL_TYPES)
    assert report.categorical_values["dealers.tier"] == sorted(bd.TIERS)
    assert report.categorical_values["sales.finance_type"] == sorted(bd.FINANCE_TYPES)


def test_sale_dates_in_agreed_range(report: "bd.BuildReport") -> None:
    assert report.date_min >= "2023-01-01"
    assert report.date_max <= "2026-06-30"
    # "last year" (2025) must be a full year of data.
    assert "2025" in report.ev_share_by_year


def test_same_seed_gives_identical_data(
    report: "bd.BuildReport", tmp_path: Path
) -> None:
    again = build_and_verify(tmp_path / "again.db")
    assert again.fingerprint == report.fingerprint


def test_different_seed_changes_data(report: "bd.BuildReport", tmp_path: Path) -> None:
    other = build_and_verify(tmp_path / "other.db", seed=bd.DEFAULT_SEED + 1)
    assert other.fingerprint != report.fingerprint


def test_rebuild_replaces_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "askauto.db"
    bd.build(db_path)
    rebuilt = build_and_verify(db_path)
    assert rebuilt.row_counts["sales"] == 5000
