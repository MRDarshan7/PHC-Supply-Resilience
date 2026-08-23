"""Load data/facility_directory.csv (all-India, ~20 MB) into `facilities`.

Streams the file row by row and keeps only TARGET_DISTRICT — the whole file
is never held in memory. Idempotent: facility_id is derived deterministically
from the row's natural key, and rows are upserted.
"""

import csv
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import FACILITY_CSV, TARGET_DISTRICT  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402

FACILITY_ENCODING = "utf-8-sig"  # plain UTF-8; -sig strips a BOM if one is present

# Column mapping, resolved against the real header at load time.
COLUMN_MAP = {
    "name": "Facility Name",
    "type": "Facility Type",
    "district": "District Name",
    "state": "State Name",
    "lat": "Latitude",
    "lon": "Longitude",
}
# PROJECT_CONTEXT.md §18 Q1: existence of a sub-district column is unconfirmed,
# so it is detected rather than assumed.
SUB_DISTRICT_CANDIDATES = ["Subdistrict Name", "Sub District Name", "Sub-District Name", "Block Name", "Mandal Name"]

UPSERT = """
INSERT INTO facilities (facility_id, name, type, district, sub_district, state, lat, lon)
VALUES (:facility_id, :name, :type, :district, :sub_district, :state, :lat, :lon)
ON CONFLICT(facility_id) DO UPDATE SET
    name = excluded.name, type = excluded.type, district = excluded.district,
    sub_district = excluded.sub_district, state = excluded.state,
    lat = excluded.lat, lon = excluded.lon
"""


def _to_float(value):
    """Parse a coordinate cell; blanks and non-numeric markers like 'NA' become None."""
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def make_facility_id(state, district, sub_district, ftype, name, lat, lon):
    """Deterministic, human-readable id: <type>_<name-slug>_<6-hex hash of natural key>.

    The source file has no usable unique id (Nin_N is frequently 'NA' and
    duplicated), but (state, name, type, sub-district, lat, lon) is unique
    across every row, so hashing that key gives a stable identity across runs.
    """
    key = "|".join(str(x).strip() for x in (state, district, sub_district, ftype, name, lat, lon))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")[:24]
    return f"{ftype}_{slug}_{digest}"


def load(conn, csv_path=FACILITY_CSV, district=TARGET_DISTRICT, verbose=True):
    """Stream the CSV, upsert rows for `district`, return a summary dict."""
    skipped = []
    loaded = 0
    by_type = Counter()

    with open(csv_path, encoding=FACILITY_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames

        sub_col = next((c for c in SUB_DISTRICT_CANDIDATES if c in header), None)
        if verbose:
            print(f"Facility CSV columns ({len(header)}):")
            for c in header:
                print(f"  - {c}")
            print("Column mapping used:")
            for field, col in COLUMN_MAP.items():
                print(f"  {field:<13} <- {col!r}")
            print(f"  {'sub_district':<13} <- {sub_col!r}")
            print(f"Sub-district column present: {'YES' if sub_col else 'NO'}"
                  + (f" ({sub_col!r})" if sub_col else " - outbreaks will be allocated district-wide"))

        missing = [c for c in COLUMN_MAP.values() if c not in header]
        if missing:
            raise SystemExit(f"facility CSV is missing expected columns: {missing}")

        with conn:
            for row in reader:
                if row[COLUMN_MAP["district"]].strip() != district:
                    continue
                name = row[COLUMN_MAP["name"]].strip()
                ftype = row[COLUMN_MAP["type"]].strip()
                state = row[COLUMN_MAP["state"]].strip()
                sub_district = row[sub_col].strip() if sub_col else None
                lat = _to_float(row[COLUMN_MAP["lat"]])
                lon = _to_float(row[COLUMN_MAP["lon"]])
                if lat is None or lon is None:
                    skipped.append((name, ftype, row[COLUMN_MAP["lat"]], row[COLUMN_MAP["lon"]]))
                    continue
                conn.execute(UPSERT, {
                    "facility_id": make_facility_id(state, district, sub_district, ftype, name, lat, lon),
                    "name": name,
                    "type": ftype,
                    "district": district,
                    "sub_district": sub_district,
                    "state": state,
                    "lat": lat,
                    "lon": lon,
                })
                loaded += 1
                by_type[ftype] += 1

    summary = {
        "district": district,
        "sub_district_column": sub_col,
        "loaded": loaded,
        "skipped_null_latlon": len(skipped),
        "skipped_rows": skipped,
        "by_type": dict(by_type),
    }
    if verbose:
        print(f"Loaded {loaded} {district} facilities; skipped {len(skipped)} with null/non-numeric lat/lon:")
        for s in skipped:
            print(f"  skipped: name={s[0]!r} type={s[1]!r} lat={s[2]!r} lon={s[3]!r}")
        print(f"By type: {dict(sorted(by_type.items(), key=lambda kv: -kv[1]))}")
    return summary


if __name__ == "__main__":
    with get_connection() as conn:
        create_tables(conn)
        load(conn)
