"""Build the AskAuto database: 3 business tables from explicit DDL, plus seeded data.

Build-time only. This runs before the MCP server exists, so it opens SQLite
directly (see docs/ARCHITECTURE.md, "Two phases").

Tables are created from the spec DDL with REFERENCES clauses, never with
pandas to_sql: without them PRAGMA foreign_key_list is empty and the join graph
built in session 2 comes out silently empty.

Usage:
    python scripts/build_database.py [--db PATH] [--seed N]
"""

import argparse
import hashlib
import os
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "data/askauto.db"
DEFAULT_SEED = 42

N_SALES = 5000
START_DATE = date(2023, 1, 1)
END_DATE = date(2026, 6, 30)

# Exact categorical spellings from docs/PROBLEM_STATEMENT.md. Trap 2 depends on these.
REGIONS = ("Northeast", "Southeast", "Midwest", "West")
TIERS = ("Flagship", "Standard", "Satellite")
MAKES = ("Toyota", "Honda", "Ford", "Nissan", "Chevrolet", "Hyundai")
FUEL_TYPES = ("Gasoline", "Hybrid", "Electric")
FINANCE_TYPES = ("Loan", "Lease", "Cash")

# Verbatim from docs/PROBLEM_STATEMENT.md, "Data model".
DDL = """
CREATE TABLE dealers (
    dealer_id   TEXT PRIMARY KEY,
    dealer_name TEXT NOT NULL,
    city        TEXT,
    region      TEXT NOT NULL,   -- Northeast | Southeast | Midwest | West
    tier        TEXT NOT NULL    -- Flagship | Standard | Satellite
);

CREATE TABLE vehicles (
    vehicle_id  TEXT PRIMARY KEY,
    make        TEXT NOT NULL,   -- Toyota | Honda | Ford | Nissan | Chevrolet | Hyundai
    model       TEXT NOT NULL,
    model_year  INTEGER,
    fuel_type   TEXT NOT NULL,   -- Gasoline | Hybrid | Electric
    msrp        REAL
);

CREATE TABLE sales (
    sale_id         TEXT PRIMARY KEY,
    dealer_id       TEXT NOT NULL REFERENCES dealers(dealer_id),
    vehicle_id      TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    sale_date       TEXT NOT NULL,   -- ISO YYYY-MM-DD
    sale_price      REAL NOT NULL,   -- GROSS, before discount
    discount_amount REAL NOT NULL,
    finance_type    TEXT NOT NULL    -- Loan | Lease | Cash
);
"""

# (dealer_name, city, region, tier). 5 per region: 1 Flagship, 2 Standard, 2 Satellite.
# Names are fictional; cities are real and all distinct.
DEALERS: list[tuple[str, str, str, str]] = [
    ("Harborview Auto Group", "Boston", "Northeast", "Flagship"),
    ("Charter Oak Motors", "Hartford", "Northeast", "Standard"),
    ("Ocean State Auto", "Providence", "Northeast", "Standard"),
    ("Capital Region Cars", "Albany", "Northeast", "Satellite"),
    ("Green Mountain Motors", "Burlington", "Northeast", "Satellite"),
    ("Peachtree Motor Group", "Atlanta", "Southeast", "Flagship"),
    ("Queen City Auto", "Charlotte", "Southeast", "Standard"),
    ("Gulf Coast Motors", "Tampa", "Southeast", "Standard"),
    ("Lowcountry Auto", "Savannah", "Southeast", "Satellite"),
    ("Smoky Ridge Motors", "Knoxville", "Southeast", "Satellite"),
    ("Lakeshore Auto Group", "Chicago", "Midwest", "Flagship"),
    ("Buckeye Motors", "Columbus", "Midwest", "Standard"),
    ("North Star Auto", "Minneapolis", "Midwest", "Standard"),
    ("Isthmus Motors", "Madison", "Midwest", "Satellite"),
    ("Prairie Auto Sales", "Des Moines", "Midwest", "Satellite"),
    ("Pacific Crest Motors", "Los Angeles", "West", "Flagship"),
    ("Front Range Auto", "Denver", "West", "Standard"),
    ("Evergreen Motors", "Seattle", "West", "Standard"),
    ("Treasure Valley Auto", "Boise", "West", "Satellite"),
    ("High Desert Motors", "Albuquerque", "West", "Satellite"),
]

# (make, model, model_year, fuel_type, msrp)
VEHICLES: list[tuple[str, str, int, str, float]] = [
    ("Toyota", "Camry", 2023, "Gasoline", 27400.0),
    ("Toyota", "Camry", 2025, "Hybrid", 29500.0),
    ("Toyota", "Corolla", 2024, "Gasoline", 22800.0),
    ("Toyota", "RAV4", 2023, "Gasoline", 29800.0),
    ("Toyota", "RAV4", 2024, "Hybrid", 32500.0),
    ("Toyota", "RAV4", 2026, "Hybrid", 34200.0),
    ("Toyota", "Prius", 2025, "Hybrid", 28900.0),
    ("Toyota", "bZ4X", 2025, "Electric", 43000.0),
    ("Honda", "Civic", 2023, "Gasoline", 24600.0),
    ("Honda", "Civic", 2025, "Hybrid", 29800.0),
    ("Honda", "Accord", 2024, "Hybrid", 33500.0),
    ("Honda", "CR-V", 2023, "Gasoline", 31100.0),
    ("Honda", "CR-V", 2025, "Hybrid", 35000.0),
    ("Honda", "Pilot", 2024, "Gasoline", 40200.0),
    ("Honda", "Prologue", 2025, "Electric", 48800.0),
    ("Ford", "F-150", 2023, "Gasoline", 41600.0),
    ("Ford", "F-150", 2025, "Hybrid", 52000.0),
    ("Ford", "Escape", 2024, "Hybrid", 33500.0),
    ("Ford", "Explorer", 2025, "Gasoline", 40600.0),
    ("Ford", "Bronco", 2024, "Gasoline", 39600.0),
    ("Ford", "Mustang Mach-E", 2023, "Electric", 46900.0),
    ("Ford", "F-150 Lightning", 2026, "Electric", 54800.0),
    ("Nissan", "Altima", 2023, "Gasoline", 26400.0),
    ("Nissan", "Rogue", 2024, "Gasoline", 29200.0),
    ("Nissan", "Sentra", 2025, "Gasoline", 21600.0),
    ("Nissan", "Pathfinder", 2026, "Gasoline", 37800.0),
    ("Nissan", "Leaf", 2024, "Electric", 29300.0),
    ("Nissan", "Ariya", 2025, "Electric", 41200.0),
    ("Chevrolet", "Silverado", 2023, "Gasoline", 38900.0),
    ("Chevrolet", "Equinox", 2024, "Gasoline", 28600.0),
    ("Chevrolet", "Malibu", 2023, "Gasoline", 25100.0),
    ("Chevrolet", "Tahoe", 2025, "Gasoline", 58200.0),
    ("Chevrolet", "Equinox EV", 2025, "Electric", 34900.0),
    ("Chevrolet", "Bolt EUV", 2023, "Electric", 28800.0),
    ("Hyundai", "Elantra", 2024, "Gasoline", 22100.0),
    ("Hyundai", "Tucson", 2023, "Gasoline", 28200.0),
    ("Hyundai", "Tucson", 2025, "Hybrid", 33600.0),
    ("Hyundai", "Santa Fe", 2026, "Hybrid", 36900.0),
    ("Hyundai", "Ioniq 5", 2024, "Electric", 42600.0),
    ("Hyundai", "Ioniq 6", 2026, "Electric", 38600.0),
]

# Sales distribution weights.
TIER_WEIGHT = {"Flagship": 3.0, "Standard": 2.0, "Satellite": 1.0}
MONTH_WEIGHT = {
    1: 0.80, 2: 0.85, 3: 1.05, 4: 1.10, 5: 1.15, 6: 1.15,
    7: 1.10, 8: 1.10, 9: 0.95, 10: 0.95, 11: 0.95, 12: 1.20,
}
# Yearly growth in a fuel type's weight, so EV share has a real upward trend.
FUEL_GROWTH = {"Gasoline": 0.0, "Hybrid": 0.2, "Electric": 0.4}
# Keyed on model_year - sale_year. Only these offsets are sellable.
MODEL_YEAR_WEIGHT = {-1: 0.7, 0: 1.0, 1: 0.5}
FINANCE_WEIGHT = {"Electric": (40.0, 45.0, 15.0), "default": (58.0, 20.0, 22.0)}
PRICE_FACTOR = (0.94, 1.04)  # sale_price = msrp * U(...)
DISCOUNT_RATE = (0.02, 0.05)  # discount_amount = sale_price * U(...)  -> trap 1


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class BuildReport(BaseModel):
    db_path: str
    row_counts: dict[str, int]
    foreign_keys: list[str]
    gross_revenue: float
    net_revenue: float
    discount_gap_pct: float
    regions: list[str]
    north_east_rows: int
    categorical_values: dict[str, list[str]]
    date_min: str
    date_max: str
    ev_share_by_year: dict[str, float]
    fingerprint: str
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def resolve_db_path(arg: str | None) -> Path:
    """--db, else $DATABASE_PATH, else data/askauto.db. Relative paths are from the repo root."""
    path = Path(arg or os.environ.get("DATABASE_PATH") or DEFAULT_DB)
    return path if path.is_absolute() else REPO_ROOT / path


def make_dealers() -> list[tuple[str, str, str, str, str]]:
    return [(f"D{i:02d}", *row) for i, row in enumerate(DEALERS, start=1)]


def make_vehicles() -> list[tuple[str, str, str, int, str, float]]:
    return [(f"V{i:02d}", *row) for i, row in enumerate(VEHICLES, start=1)]


def make_sales(
    rng: random.Random,
    dealers: list[tuple[str, str, str, str, str]],
    vehicles: list[tuple[str, str, str, int, str, float]],
) -> list[tuple[str, str, str, str, float, float, str]]:
    """Generate N_SALES rows. All randomness comes from rng, in a fixed order."""
    days = [START_DATE + timedelta(n) for n in range((END_DATE - START_DATE).days + 1)]
    sale_dates = sorted(rng.choices(days, [MONTH_WEIGHT[d.month] for d in days], k=N_SALES))

    dealer_ids = [d[0] for d in dealers]
    dealer_weights = [TIER_WEIGHT[d[4]] for d in dealers]

    # Sellable vehicles and their weights, per sale year.
    by_year: dict[int, tuple[list[tuple[str, str, str, int, str, float]], list[float]]] = {}
    for year in range(START_DATE.year, END_DATE.year + 1):
        eligible = [v for v in vehicles if v[3] - year in MODEL_YEAR_WEIGHT]
        weights = [
            MODEL_YEAR_WEIGHT[v[3] - year]
            * (1 + FUEL_GROWTH[v[4]] * (year - START_DATE.year))
            for v in eligible
        ]
        by_year[year] = (eligible, weights)

    sales = []
    for n, sale_date in enumerate(sale_dates, start=1):
        dealer_id = rng.choices(dealer_ids, dealer_weights)[0]
        eligible, weights = by_year[sale_date.year]
        vehicle_id, _, _, _, fuel_type, msrp = rng.choices(eligible, weights)[0]
        sale_price = round(msrp * rng.uniform(*PRICE_FACTOR), 2)
        discount = round(sale_price * rng.uniform(*DISCOUNT_RATE), 2)
        finance = rng.choices(
            FINANCE_TYPES, FINANCE_WEIGHT.get(fuel_type, FINANCE_WEIGHT["default"])
        )[0]
        sales.append(
            (f"S{n:05d}", dealer_id, vehicle_id, sale_date.isoformat(), sale_price, discount,
             finance)
        )
    return sales


def build(db_path: Path, seed: int = DEFAULT_SEED) -> None:
    """Create the database from clean: delete any existing file, run DDL, insert data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    rng = random.Random(seed)
    dealers = make_dealers()
    vehicles = make_vehicles()
    sales = make_sales(rng, dealers, vehicles)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(DDL)
        with conn:
            conn.executemany("INSERT INTO dealers VALUES (?, ?, ?, ?, ?)", dealers)
            conn.executemany("INSERT INTO vehicles VALUES (?, ?, ?, ?, ?, ?)", vehicles)
            conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?)", sales)
    finally:
        conn.close()


def fingerprint(conn: sqlite3.Connection) -> str:
    """sha256 over every row of every table, ordered by primary key."""
    h = hashlib.sha256()
    for table, pk in (("dealers", "dealer_id"), ("vehicles", "vehicle_id"), ("sales", "sale_id")):
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {pk}"):
            h.update(repr(row).encode())
    return h.hexdigest()


def verify(db_path: Path) -> BuildReport:
    """Inspect a built database and check it against the spec and both planted traps."""
    conn = sqlite3.connect(db_path)
    try:
        q = conn.execute
        row_counts = {t: q(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("dealers", "vehicles", "sales")}
        foreign_keys = sorted(
            f"sales.{r[3]} -> {r[2]}.{r[4]}"
            for r in q("PRAGMA foreign_key_list(sales)")
        )
        fk_violations = q("PRAGMA foreign_key_check").fetchall()
        gross, net = q(
            "SELECT SUM(sale_price), SUM(sale_price - discount_amount) FROM sales"
        ).fetchone()
        gap_pct = (gross - net) / gross * 100
        regions = sorted(r[0] for r in q("SELECT DISTINCT region FROM dealers"))
        north_east_rows = q(
            "SELECT COUNT(*) FROM sales JOIN dealers USING (dealer_id) "
            "WHERE region = 'North East'"
        ).fetchone()[0]

        spec_values = {
            "dealers.region": REGIONS,
            "dealers.tier": TIERS,
            "vehicles.make": MAKES,
            "vehicles.fuel_type": FUEL_TYPES,
            "sales.finance_type": FINANCE_TYPES,
        }
        categorical_values = {}
        for key in spec_values:
            table, column = key.split(".")
            categorical_values[key] = sorted(
                r[0] for r in q(f"SELECT DISTINCT {column} FROM {table}")
            )

        date_min, date_max = q("SELECT MIN(sale_date), MAX(sale_date) FROM sales").fetchone()
        bad_model_years = q(
            "SELECT COUNT(*) FROM sales JOIN vehicles USING (vehicle_id) "
            "WHERE model_year - CAST(substr(sale_date, 1, 4) AS INTEGER) NOT IN (-1, 0, 1)"
        ).fetchone()[0]
        ev_share_by_year = {
            year: round(share * 100, 1)
            for year, share in q(
                "SELECT substr(sale_date, 1, 4), AVG(fuel_type = 'Electric') "
                "FROM sales JOIN vehicles USING (vehicle_id) GROUP BY 1 ORDER BY 1"
            )
        }
        digest = fingerprint(conn)
    finally:
        conn.close()

    checks = [
        Check(name="row counts ~20/~40/~5000",
              passed=15 <= row_counts["dealers"] <= 25
              and 30 <= row_counts["vehicles"] <= 50
              and 4500 <= row_counts["sales"] <= 5500,
              detail=str(row_counts)),
        Check(name="foreign_key_list(sales) has 2 rows",
              passed=foreign_keys == ["sales.dealer_id -> dealers.dealer_id",
                                      "sales.vehicle_id -> vehicles.vehicle_id"],
              detail="; ".join(foreign_keys) or "EMPTY"),
        Check(name="no foreign key violations",
              passed=not fk_violations, detail=f"{len(fk_violations)} violations"),
        Check(name="trap 1: gross exceeds net by 2-5%",
              passed=2.0 <= gap_pct <= 5.0,
              detail=f"gross {gross:,.2f}  net {net:,.2f}  gap {gap_pct:.2f}%"),
        Check(name="trap 2: exactly 4 region spellings",
              passed=regions == sorted(REGIONS) and north_east_rows == 0,
              detail=f"{regions}; 'North East' -> {north_east_rows} rows"),
        *[
            Check(name=f"{key} values match spec",
                  passed=set(values) == set(spec_values[key]),
                  detail=", ".join(values))
            for key, values in categorical_values.items()
        ],
        Check(name="sale_date within range",
              passed=START_DATE.isoformat() <= date_min
              and date_max <= END_DATE.isoformat(),
              detail=f"{date_min} .. {date_max}"),
        Check(name="model_year within 1 year of sale year",
              passed=bad_model_years == 0, detail=f"{bad_model_years} bad rows"),
    ]

    return BuildReport(
        db_path=str(db_path),
        row_counts=row_counts,
        foreign_keys=foreign_keys,
        gross_revenue=round(gross, 2),
        net_revenue=round(net, 2),
        discount_gap_pct=round(gap_pct, 2),
        regions=regions,
        north_east_rows=north_east_rows,
        categorical_values=categorical_values,
        date_min=date_min,
        date_max=date_max,
        ev_share_by_year=ev_share_by_year,
        fingerprint=digest,
        checks=checks,
    )


def print_report(report: BuildReport) -> None:
    print(f"Database: {report.db_path}")
    for check in report.checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
    ev = ", ".join(f"{y}: {s}%" for y, s in report.ev_share_by_year.items())
    print(f"  EV share by year: {ev}")
    print(f"  Fingerprint: {report.fingerprint}")
    print("OK" if report.passed else "FAILED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help=f"database path (default: $DATABASE_PATH or {DEFAULT_DB})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    build(db_path, args.seed)
    report = verify(db_path)
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
