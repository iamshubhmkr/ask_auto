"""Build the meta_* tables: automatic half from PRAGMA, authored half from metadata/*.yaml.

Build-time only; opens SQLite directly. Run after scripts/build_database.py.
Idempotent: drops and recreates every meta_* table on each run.

Automatic: PRAGMA table_info, PRAGMA foreign_key_list, COUNT(DISTINCT col).
Authored:  descriptions, grain, glossary, known-unanswerable topics. The build
fails loudly if the YAML and the database disagree, or if a glossary
expression does not run.

Usage:
    python scripts/build_metadata.py [--db PATH] [--metadata-dir DIR]
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "data/askauto.db"
DEFAULT_METADATA_DIR = "metadata"

BUSINESS_TABLES = ("dealers", "vehicles", "sales")
CATEGORICAL_MAX_DISTINCT = 25  # spec: distinct_count <= 25 stores allowed_values
# Every glossary expression is checked against this join of all three tables.
GLOSSARY_CHECK_FROM = (
    "sales JOIN vehicles USING (vehicle_id) JOIN dealers USING (dealer_id)"
)

# meta_tables .. meta_glossary verbatim from docs/PROBLEM_STATEMENT.md.
# meta_unanswerable is a fifth table (see docs/PROGRESS.md, Decisions).
META_DDL = """
DROP TABLE IF EXISTS meta_tables;
DROP TABLE IF EXISTS meta_columns;
DROP TABLE IF EXISTS meta_joins;
DROP TABLE IF EXISTS meta_glossary;
DROP TABLE IF EXISTS meta_unanswerable;

CREATE TABLE meta_tables (
    table_name  TEXT PRIMARY KEY,
    description TEXT,       -- authored
    grain       TEXT,       -- authored: "one row per sale"
    row_count   INTEGER     -- automatic
);

CREATE TABLE meta_columns (
    table_name      TEXT,
    column_name     TEXT,
    data_type       TEXT,     -- automatic, PRAGMA
    is_primary_key  INTEGER,  -- automatic, PRAGMA
    is_nullable     INTEGER,  -- automatic, PRAGMA
    is_categorical  INTEGER,  -- automatic: distinct_count <= 25
    distinct_count  INTEGER,  -- automatic
    allowed_values  TEXT,     -- automatic: JSON array when categorical
    sample_values   TEXT,     -- automatic: 3 samples when not
    description     TEXT,     -- authored
    PRIMARY KEY (table_name, column_name)
);

CREATE TABLE meta_joins (
    from_table  TEXT, from_column TEXT,
    to_table    TEXT, to_column   TEXT,
    cardinality TEXT,             -- 'many-to-one'
    PRIMARY KEY (from_table, from_column, to_table, to_column)
);

CREATE TABLE meta_glossary (
    term           TEXT PRIMARY KEY,
    sql_expression TEXT,          -- 'SUM(sale_price - discount_amount)'
    notes          TEXT
);

CREATE TABLE meta_unanswerable (
    topic  TEXT PRIMARY KEY,      -- authored: e.g. 'credit scores'
    reason TEXT                   -- authored: what is missing, told to the user
);
"""


class MetadataError(ValueError):
    """The authored YAML and the database disagree."""


# --- Authored YAML -------------------------------------------------------------
# extra="forbid" so a typo such as `categorial: true` fails instead of being ignored.


class ColumnYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    categorical: bool = False


class TableYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    grain: str
    columns: dict[str, str | ColumnYaml]

    def column(self, name: str) -> ColumnYaml:
        value = self.columns[name]
        return ColumnYaml(description=value) if isinstance(value, str) else value


class GlossaryTermYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str
    kind: Literal["measure", "filter"]
    sql_expression: str
    aliases: list[str] = []
    notes: str


class UnanswerableYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    reason: str


class Authored(BaseModel):
    tables: dict[str, TableYaml]
    glossary: list[GlossaryTermYaml]
    unanswerable: list[UnanswerableYaml]


# --- meta_* rows and the report --------------------------------------------------


class TableMeta(BaseModel):
    table_name: str
    description: str
    grain: str
    row_count: int


class ColumnMeta(BaseModel):
    table_name: str
    column_name: str
    data_type: str
    is_primary_key: bool
    is_nullable: bool
    is_categorical: bool
    distinct_count: int
    allowed_values: list[str | int | float] | None
    sample_values: list[str | int | float] | None
    description: str


class JoinMeta(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str


class GlossaryEntry(BaseModel):
    term: str
    sql_expression: str
    notes: str


class UnanswerableTopic(BaseModel):
    topic: str
    reason: str


class GlossaryCheck(BaseModel):
    term: str
    kind: str
    passed: bool
    value: float | None = None  # measure: the expression's value over all sales
    matched_rows: int | None = None  # filter: rows matched
    date_span: str | None = None  # filter: 'first..last' sale_date matched
    error: str | None = None


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class MetadataReport(BaseModel):
    db_path: str
    tables: list[TableMeta]
    columns: list[ColumnMeta]
    joins: list[JoinMeta]
    glossary: list[GlossaryEntry]
    unanswerable: list[UnanswerableTopic]
    glossary_checks: list[GlossaryCheck]
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def column(self, table: str, column: str) -> ColumnMeta:
        return next(
            c for c in self.columns if c.table_name == table and c.column_name == column
        )

    def term(self, term: str) -> GlossaryEntry:
        return next(g for g in self.glossary if g.term == term)


# --- Build ---------------------------------------------------------------------


def resolve_path(arg: str | None, env_var: str | None, default: str) -> Path:
    """arg, else $env_var, else default. Relative paths are from the repo root."""
    path = Path(arg or (env_var and os.environ.get(env_var)) or default)
    return path if path.is_absolute() else REPO_ROOT / path


def load_authored(metadata_dir: Path) -> Authored:
    def read(name: str) -> dict:
        with open(metadata_dir / name, encoding="utf-8") as f:
            return yaml.safe_load(f)

    return Authored(
        tables=read("tables.yaml")["tables"],
        glossary=read("glossary.yaml")["terms"],
        unanswerable=read("unanswerable.yaml")["topics"],
    )


def db_columns(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """PRAGMA table_info rows: (cid, name, type, notnull, default, pk)."""
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def check_glossary_term(conn: sqlite3.Connection, term: GlossaryTermYaml) -> GlossaryCheck:
    """Run one glossary expression against the real data."""
    check = GlossaryCheck(term=term.term, kind=term.kind, passed=False)
    try:
        if term.kind == "measure":
            sql = f"SELECT {term.sql_expression} FROM {GLOSSARY_CHECK_FROM}"
            (value,) = conn.execute(sql).fetchone()
            check.value = value
            check.passed = value is not None
        else:
            sql = (f"SELECT COUNT(*), MIN(sale_date), MAX(sale_date) "
                   f"FROM {GLOSSARY_CHECK_FROM} WHERE {term.sql_expression}")
            rows, first, last = conn.execute(sql).fetchone()
            check.matched_rows = rows
            check.date_span = f"{first}..{last}"
            check.passed = rows > 0
    except sqlite3.Error as exc:
        check.error = str(exc)
    return check


def validate_authored(conn: sqlite3.Connection, authored: Authored) -> None:
    """Collect every disagreement between YAML and database, then raise once."""
    problems = []

    db_tables = set(BUSINESS_TABLES)
    yaml_tables = set(authored.tables)
    problems += [f"table '{t}' has no entry in tables.yaml" for t in db_tables - yaml_tables]
    problems += [f"tables.yaml names unknown table '{t}'" for t in yaml_tables - db_tables]

    for table in db_tables & yaml_tables:
        actual = {row[1] for row in db_columns(conn, table)}
        described = set(authored.tables[table].columns)
        problems += [f"column '{table}.{c}' has no description" for c in actual - described]
        problems += [f"tables.yaml names unknown column '{table}.{c}'"
                     for c in described - actual]
        problems += [f"column '{table}.{c}' has an empty description"
                     for c in described & actual
                     if not authored.tables[table].column(c).description.strip()]

    seen: set[str] = set()
    for term in authored.glossary:
        for name in (term.term, *term.aliases):
            key = name.casefold()
            if key in seen:
                problems.append(f"glossary term or alias '{name}' is defined twice")
            seen.add(key)
        check = check_glossary_term(conn, term)
        if not check.passed:
            problems.append(
                f"glossary '{term.term}' failed: {check.error or 'no result / no rows matched'}"
            )

    if problems:
        raise MetadataError("Metadata build failed:\n  - " + "\n  - ".join(problems))


def collect_tables(conn: sqlite3.Connection, authored: Authored) -> list[TableMeta]:
    return [
        TableMeta(
            table_name=table,
            description=authored.tables[table].description,
            grain=authored.tables[table].grain,
            row_count=conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
        )
        for table in BUSINESS_TABLES
    ]


def collect_columns(conn: sqlite3.Connection, authored: Authored) -> list[ColumnMeta]:
    columns = []
    for table in BUSINESS_TABLES:
        for _, name, data_type, notnull, _, pk in db_columns(conn, table):
            authored_col = authored.tables[table].column(name)
            values = [r[0] for r in conn.execute(
                f"SELECT DISTINCT {name} FROM {table} WHERE {name} IS NOT NULL ORDER BY {name}"
            )]
            categorical = len(values) <= CATEGORICAL_MAX_DISTINCT or authored_col.categorical
            columns.append(ColumnMeta(
                table_name=table,
                column_name=name,
                data_type=data_type,
                is_primary_key=pk > 0,
                is_nullable=not notnull and not pk,
                is_categorical=categorical,
                distinct_count=len(values),
                allowed_values=values if categorical else None,
                # min, median and max distinct values: deterministic, and shows the range.
                sample_values=None if categorical else
                [values[0], values[len(values) // 2], values[-1]],
                description=authored_col.description,
            ))
    return columns


def collect_joins(conn: sqlite3.Connection) -> list[JoinMeta]:
    joins = []
    for table in BUSINESS_TABLES:
        for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
            to_table, from_column, to_column = row[2], row[3], row[4]
            total, distinct = conn.execute(
                f"SELECT COUNT({from_column}), COUNT(DISTINCT {from_column}) FROM {table}"
            ).fetchone()
            joins.append(JoinMeta(
                from_table=table, from_column=from_column,
                to_table=to_table, to_column=to_column,
                cardinality="one-to-one" if total == distinct else "many-to-one",
            ))
    return sorted(joins, key=lambda j: (j.from_table, j.from_column))


def expand_glossary(authored: Authored) -> list[GlossaryEntry]:
    """One row per term and per alias, all sharing the term's expression."""
    entries = []
    for term in authored.glossary:
        expression = " ".join(term.sql_expression.split())
        entries.append(GlossaryEntry(term=term.term, sql_expression=expression,
                                     notes=term.notes))
        entries += [
            GlossaryEntry(term=alias, sql_expression=expression,
                          notes=f"Alias of '{term.term}'. {term.notes}")
            for alias in term.aliases
        ]
    return entries


def build(db_path: Path, metadata_dir: Path) -> None:
    """Validate the YAML against the database, then (re)write every meta_* table."""
    if not db_path.exists():
        raise MetadataError(f"{db_path} not found. Run scripts/build_database.py first.")
    authored = load_authored(metadata_dir)

    conn = sqlite3.connect(db_path)
    try:
        validate_authored(conn, authored)
        tables = collect_tables(conn, authored)
        columns = collect_columns(conn, authored)
        joins = collect_joins(conn)
        glossary = expand_glossary(authored)

        conn.executescript(META_DDL)
        with conn:
            conn.executemany(
                "INSERT INTO meta_tables VALUES (?, ?, ?, ?)",
                [(t.table_name, t.description, t.grain, t.row_count) for t in tables],
            )
            conn.executemany(
                "INSERT INTO meta_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(c.table_name, c.column_name, c.data_type, int(c.is_primary_key),
                  int(c.is_nullable), int(c.is_categorical), c.distinct_count,
                  json.dumps(c.allowed_values) if c.allowed_values is not None else None,
                  json.dumps(c.sample_values) if c.sample_values is not None else None,
                  c.description) for c in columns],
            )
            conn.executemany(
                "INSERT INTO meta_joins VALUES (?, ?, ?, ?, ?)",
                [(j.from_table, j.from_column, j.to_table, j.to_column, j.cardinality)
                 for j in joins],
            )
            conn.executemany(
                "INSERT INTO meta_glossary VALUES (?, ?, ?)",
                [(g.term, g.sql_expression, g.notes) for g in glossary],
            )
            conn.executemany(
                "INSERT INTO meta_unanswerable VALUES (?, ?)",
                [(u.topic, " ".join(u.reason.split())) for u in authored.unanswerable],
            )
    finally:
        conn.close()


# --- Verify --------------------------------------------------------------------


def read_meta(conn: sqlite3.Connection) -> tuple[list, list, list, list, list]:
    """Read every meta_* table back from the database."""
    def loads(value: str | None) -> list | None:
        return json.loads(value) if value is not None else None

    tables = [TableMeta(table_name=r[0], description=r[1], grain=r[2], row_count=r[3])
              for r in conn.execute("SELECT * FROM meta_tables ORDER BY rowid")]
    columns = [
        ColumnMeta(table_name=r[0], column_name=r[1], data_type=r[2],
                   is_primary_key=bool(r[3]), is_nullable=bool(r[4]),
                   is_categorical=bool(r[5]), distinct_count=r[6],
                   allowed_values=loads(r[7]), sample_values=loads(r[8]), description=r[9])
        for r in conn.execute("SELECT * FROM meta_columns ORDER BY rowid")
    ]
    joins = [JoinMeta(from_table=r[0], from_column=r[1], to_table=r[2], to_column=r[3],
                      cardinality=r[4])
             for r in conn.execute("SELECT * FROM meta_joins ORDER BY rowid")]
    glossary = [GlossaryEntry(term=r[0], sql_expression=r[1], notes=r[2])
                for r in conn.execute("SELECT * FROM meta_glossary ORDER BY rowid")]
    unanswerable = [UnanswerableTopic(topic=r[0], reason=r[1])
                    for r in conn.execute("SELECT * FROM meta_unanswerable ORDER BY rowid")]
    return tables, columns, joins, glossary, unanswerable


def verify(db_path: Path, metadata_dir: Path) -> MetadataReport:
    """Read the meta_* tables back and check them against the spec and both traps."""
    authored = load_authored(metadata_dir)
    conn = sqlite3.connect(db_path)
    try:
        tables, columns, joins, glossary, unanswerable = read_meta(conn)
        glossary_checks = [check_glossary_term(conn, t) for t in authored.glossary]
    finally:
        conn.close()

    by_term = {g.term: g for g in glossary}
    net = by_term.get("net revenue")
    region = next((c for c in columns
                   if c.table_name == "dealers" and c.column_name == "region"), None)
    rule_breaks = [
        f"{c.table_name}.{c.column_name}" for c in columns
        if (c.is_categorical and not c.allowed_values)
        or (not c.is_categorical and len(c.sample_values or []) != 3)
        or (c.distinct_count <= CATEGORICAL_MAX_DISTINCT and not c.is_categorical)
    ]
    spec_terms = ["net revenue", "gross revenue", "units sold", "average selling price",
                  "discount rate", "EV share", "last year"]
    net_aliases = ["revenue", "sales value", "turnover", "top line"]

    checks = [
        Check(name="meta_tables: 3 tables with description and grain",
              passed=len(tables) == 3 and all(t.description and t.grain for t in tables),
              detail=", ".join(f"{t.table_name}={t.row_count}" for t in tables)),
        Check(name="meta_columns: every column described",
              passed=bool(columns) and all(c.description for c in columns),
              detail=f"{len(columns)} columns"),
        Check(name="meta_joins: 2 many-to-one rows",
              passed=len(joins) == 2 and all(j.cardinality == "many-to-one" for j in joins),
              detail="; ".join(f"{j.from_table}.{j.from_column} -> {j.to_table}.{j.to_column}"
                               f" ({j.cardinality})" for j in joins) or "EMPTY"),
        Check(name="categorical rule (<=25 or override -> allowed_values, else 3 samples)",
              passed=not rule_breaks, detail=", ".join(rule_breaks) or "all columns"),
        Check(name="trap 2: dealers.region allowed_values exact",
              passed=region is not None and region.allowed_values ==
              ["Midwest", "Northeast", "Southeast", "West"],
              detail=str(region.allowed_values if region else None)),
        Check(name="trap 1: net revenue = sale_price - discount_amount",
              passed=net is not None and "sale_price - discount_amount" in net.sql_expression,
              detail=net.sql_expression if net else "MISSING"),
        Check(name="net revenue aliases resolve identically",
              passed=net is not None and all(
                  by_term.get(a) and by_term[a].sql_expression == net.sql_expression
                  for a in net_aliases),
              detail=", ".join(net_aliases)),
        Check(name="all spec glossary terms present",
              passed=all(t in by_term for t in spec_terms),
              detail=", ".join(t for t in spec_terms if t not in by_term) or "7 of 7"),
        *[
            Check(name=f"glossary '{g.term}' runs",
                  passed=g.passed,
                  detail=g.error or (f"{g.value:,.4f}" if g.kind == "measure"
                                     else f"{g.matched_rows} rows, {g.date_span}"))
            for g in glossary_checks
        ],
        Check(name="meta_unanswerable: 8 spec topics",
              passed=len(unanswerable) == 8, detail=f"{len(unanswerable)} topics"),
    ]

    return MetadataReport(
        db_path=str(db_path), tables=tables, columns=columns, joins=joins,
        glossary=glossary, unanswerable=unanswerable,
        glossary_checks=glossary_checks, checks=checks,
    )


def print_report(report: MetadataReport) -> None:
    print(f"Database: {report.db_path}")
    print("\nmeta_glossary:")
    for g in report.glossary:
        print(f"  {g.term:22} {g.sql_expression}")
    print("\nallowed_values (categorical columns):")
    for c in report.columns:
        if c.is_categorical:
            print(f"  {c.table_name}.{c.column_name} ({c.distinct_count}): "
                  f"{json.dumps(c.allowed_values)}")
    print("\nsample_values (non-categorical columns):")
    for c in report.columns:
        if not c.is_categorical:
            print(f"  {c.table_name}.{c.column_name} ({c.distinct_count}): "
                  f"{json.dumps(c.sample_values)}")
    print("\nmeta_unanswerable:")
    for u in report.unanswerable:
        print(f"  {u.topic}")
    print("\nChecks:")
    for check in report.checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
    print("OK" if report.passed else "FAILED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help=f"database path (default: $DATABASE_PATH or {DEFAULT_DB})")
    parser.add_argument("--metadata-dir", help=f"YAML directory (default: {DEFAULT_METADATA_DIR})")
    args = parser.parse_args()

    db_path = resolve_path(args.db, "DATABASE_PATH", DEFAULT_DB)
    metadata_dir = resolve_path(args.metadata_dir, None, DEFAULT_METADATA_DIR)
    try:
        build(db_path, metadata_dir)
    except MetadataError as exc:
        print(exc, file=sys.stderr)
        return 1
    report = verify(db_path, metadata_dir)
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
