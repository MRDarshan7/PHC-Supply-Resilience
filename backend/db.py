"""SQLite schema and connection helpers.

Schema follows PROJECT_CONTEXT.md §10 exactly.

THE ONE RULE: current stock is ALWAYS computed as SUM(delta) over
stock_movements. There is no mutable quantity column anywhere in this
schema, and none may be added — a cached quantity drifts out of sync with
the movement log. Use the `current_stock` view or `current_stock()` helper.
"""

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import LEDGER_DB  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
    facility_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    district      TEXT NOT NULL,
    sub_district  TEXT,
    state         TEXT NOT NULL,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS medicines (
    medicine_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL,
    aliases       TEXT
);

-- facility_id is either a real facilities.facility_id or the district-level
-- sentinel produced by district_caseload_id() (used until Phase 5 allocates
-- the district caseload across facilities). No FK for that reason.
CREATE TABLE IF NOT EXISTS caseloads (
    facility_id      TEXT NOT NULL,
    disease          TEXT NOT NULL,
    cases_per_month  REAL NOT NULL,
    source_period    TEXT NOT NULL,
    PRIMARY KEY (facility_id, disease)
);

-- Append-only ledger. Stock is derived, never stored.
CREATE TABLE IF NOT EXISTS stock_movements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id   TEXT NOT NULL REFERENCES facilities(facility_id),
    medicine_id   TEXT NOT NULL REFERENCES medicines(medicine_id),
    delta         REAL NOT NULL,
    source        TEXT NOT NULL,
    note          TEXT,
    ts            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_movements_fac_med
    ON stock_movements (facility_id, medicine_id);

-- Columns mirror the Gemini extraction output in PROJECT_CONTEXT.md section 5
-- (state, deaths, status) on top of the section 10 schema.
CREATE TABLE IF NOT EXISTS outbreaks (
    outbreak_id   TEXT PRIMARY KEY,
    state         TEXT,
    district      TEXT NOT NULL,
    sub_district  TEXT,
    disease       TEXT NOT NULL,
    cases         INTEGER NOT NULL,
    deaths        INTEGER,
    week          INTEGER NOT NULL,
    year          INTEGER NOT NULL,
    status        TEXT,
    source_file   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transfers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    from_facility  TEXT NOT NULL REFERENCES facilities(facility_id),
    to_facility    TEXT NOT NULL REFERENCES facilities(facility_id),
    medicine_id    TEXT NOT NULL REFERENCES medicines(medicine_id),
    qty            REAL NOT NULL,
    status         TEXT NOT NULL,
    memo           TEXT,
    approved_by    TEXT,
    ts             TEXT NOT NULL
);

-- The only sanctioned way to read stock.
CREATE VIEW IF NOT EXISTS current_stock AS
    SELECT facility_id, medicine_id, SUM(delta) AS stock
    FROM stock_movements
    GROUP BY facility_id, medicine_id;
"""

TABLES = ["facilities", "medicines", "caseloads", "stock_movements", "outbreaks", "transfers"]

# Additive migrations for databases created before a column existed:
# (table, column, declared type). create_tables() adds any that are missing,
# so an existing ledger.db never needs to be rebuilt for a new nullable column.
COLUMN_MIGRATIONS = [
    ("outbreaks", "state", "TEXT"),
    ("outbreaks", "deaths", "INTEGER"),
    ("outbreaks", "status", "TEXT"),
]


def get_connection(db_path=LEDGER_DB):
    """Open the ledger database, creating the parent directory if needed."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables(conn):
    """Create all tables, indexes and views, then apply additive column
    migrations. Safe to run repeatedly."""
    conn.executescript(SCHEMA)
    for table, column, ctype in COLUMN_MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
    conn.commit()


def table_columns(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def district_caseload_id(district):
    """Sentinel facility_id for a district-level caseload row."""
    return f"district:{district}"


def current_stock(conn, facility_id, medicine_id):
    """Stock on hand = SUM(delta) of every movement for this (facility, medicine)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(delta), 0) AS stock FROM stock_movements "
        "WHERE facility_id = ? AND medicine_id = ?",
        (facility_id, medicine_id),
    ).fetchone()
    return row["stock"]


def table_counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


if __name__ == "__main__":
    with get_connection() as conn:
        create_tables(conn)
        print(f"Schema ready at {LEDGER_DB}")
        print(table_counts(conn))
