"""Regression tests for scripts/build_metadata.py: meta_* tables, glossary, both traps.

Both build scripts are loaded by path and do all the SQLite work, so these
tests never import sqlite3 themselves.
"""

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
METADATA_DIR = REPO_ROOT / "metadata"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bd = load_script("build_database")
bm = load_script("build_metadata")


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("db") / "askauto.db"
    bd.build(path)
    bm.build(path, METADATA_DIR)
    return path


@pytest.fixture(scope="module")
def report(db_path: Path) -> "bm.MetadataReport":
    return bm.verify(db_path, METADATA_DIR)


def test_all_checks_pass(report: "bm.MetadataReport") -> None:
    failed = [f"{c.name}: {c.detail}" for c in report.checks if not c.passed]
    assert not failed


def test_joins_are_two_many_to_one(report: "bm.MetadataReport") -> None:
    # Empty here means build_database lost its REFERENCES clauses.
    assert [(j.from_column, j.to_table, j.cardinality) for j in report.joins] == [
        ("dealer_id", "dealers", "many-to-one"),
        ("vehicle_id", "vehicles", "many-to-one"),
    ]


def test_trap2_region_allowed_values_exact(report: "bm.MetadataReport") -> None:
    region = report.column("dealers", "region")
    assert region.is_categorical
    assert region.allowed_values == ["Midwest", "Northeast", "Southeast", "West"]
    assert "North East" not in region.allowed_values


def test_trap1_net_revenue_and_aliases(report: "bm.MetadataReport") -> None:
    net = report.term("net revenue").sql_expression
    assert "sale_price - discount_amount" in net
    for alias in ("revenue", "sales value", "turnover", "top line"):
        assert report.term(alias).sql_expression == net


def test_gross_revenue_is_flagged(report: "bm.MetadataReport") -> None:
    gross = report.term("gross revenue")
    assert gross.sql_expression == "SUM(sale_price)"
    assert "usually a mistake" in gross.notes


def test_spec_glossary_terms_present(report: "bm.MetadataReport") -> None:
    terms = {g.term for g in report.glossary}
    assert {"net revenue", "gross revenue", "units sold", "average selling price",
            "discount rate", "EV share", "last year"} <= terms


def test_last_year_resolves_to_2025(report: "bm.MetadataReport") -> None:
    check = next(g for g in report.glossary_checks if g.term == "last year")
    assert check.date_span == "2025-01-01..2025-12-31"


def test_categorical_threshold(report: "bm.MetadataReport") -> None:
    for c in report.columns:
        if c.distinct_count <= bm.CATEGORICAL_MAX_DISTINCT:
            assert c.is_categorical, f"{c.table_name}.{c.column_name}"
        if c.is_categorical:
            assert c.allowed_values and c.sample_values is None
        else:
            assert c.allowed_values is None and len(c.sample_values) == 3


def test_model_override_stores_exact_spellings(report: "bm.MetadataReport") -> None:
    model = report.column("vehicles", "model")
    assert model.distinct_count > bm.CATEGORICAL_MAX_DISTINCT
    assert model.is_categorical
    assert {"F-150", "CR-V", "Ioniq 5"} <= set(model.allowed_values)


def test_every_table_and_column_described(report: "bm.MetadataReport") -> None:
    assert {t.table_name for t in report.tables} == {"dealers", "vehicles", "sales"}
    assert all(t.description and t.grain for t in report.tables)
    assert len(report.columns) == 18
    assert all(c.description.strip() for c in report.columns)


def test_unanswerable_topics(report: "bm.MetadataReport") -> None:
    topics = {u.topic for u in report.unanswerable}
    assert len(topics) == 8
    assert {"credit scores", "inventory levels", "salesperson attribution"} <= topics


def test_rebuild_is_idempotent(db_path: Path, report: "bm.MetadataReport") -> None:
    bm.build(db_path, METADATA_DIR)
    again = bm.verify(db_path, METADATA_DIR)
    assert again.model_dump() == report.model_dump()


def broken_metadata(tmp_path: Path, edit: str) -> Path:
    """Copy metadata/ and apply one deliberate mistake."""
    target = tmp_path / "metadata"
    shutil.copytree(METADATA_DIR, target)
    tables = yaml.safe_load((target / "tables.yaml").read_text(encoding="utf-8"))
    glossary = yaml.safe_load((target / "glossary.yaml").read_text(encoding="utf-8"))
    if edit == "unknown column":
        tables["tables"]["sales"]["columns"]["salesperson"] = "Not a real column."
    elif edit == "missing description":
        del tables["tables"]["sales"]["columns"]["discount_amount"]
    elif edit == "bad expression":
        glossary["terms"][0]["sql_expression"] = "SUM(sale_price - discount)"
    (target / "tables.yaml").write_text(yaml.safe_dump(tables), encoding="utf-8")
    (target / "glossary.yaml").write_text(yaml.safe_dump(glossary), encoding="utf-8")
    return target


@pytest.mark.parametrize("edit, message", [
    ("unknown column", "unknown column 'sales.salesperson'"),
    ("missing description", "'sales.discount_amount' has no description"),
    ("bad expression", "glossary 'net revenue' failed"),
])
def test_bad_yaml_fails_loudly(
    db_path: Path, tmp_path: Path, edit: str, message: str
) -> None:
    with pytest.raises(bm.MetadataError, match=message):
        bm.build(db_path, broken_metadata(tmp_path, edit))
