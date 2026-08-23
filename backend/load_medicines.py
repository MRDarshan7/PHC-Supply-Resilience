"""Load data/medicines.csv into `medicines`. Idempotent (upsert on medicine_id)."""

import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import MEDICINES_CSV  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402

EXPECTED_COLUMNS = ["medicine_id", "name", "unit", "aliases"]

UPSERT = """
INSERT INTO medicines (medicine_id, name, unit, aliases)
VALUES (:medicine_id, :name, :unit, :aliases)
ON CONFLICT(medicine_id) DO UPDATE SET
    name = excluded.name, unit = excluded.unit, aliases = excluded.aliases
"""


def load(conn, csv_path=MEDICINES_CSV, verbose=True):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise SystemExit(f"medicines.csv columns {reader.fieldnames} != {EXPECTED_COLUMNS}")
        rows = [{k: v.strip() for k, v in r.items()} for r in reader]

    with conn:
        conn.executemany(UPSERT, rows)

    if verbose:
        print(f"Loaded {len(rows)} medicines: {[r['medicine_id'] for r in rows]}")
    return {"loaded": len(rows)}


if __name__ == "__main__":
    with get_connection() as conn:
        create_tables(conn)
        load(conn)
