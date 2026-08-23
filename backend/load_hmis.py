"""Load the HMIS 2019-20 AP item-wise district report into `caseloads`.

Every trap below is documented in PROJECT_CONTEXT.md §7 — do not "fix" them:

  * encoding is cp1252, NOT UTF-8 (byte 0xA0 breaks a UTF-8 read)
  * the file is TRANSPOSED: districts are columns, indicators are rows
  * five columns exist per district (Total/Public/Private/Urban/Rural) — use Total
  * S.No. cells carry stray quote characters: strip ' and whitespace before matching
  * match S.No. EXACTLY — str.contains("16.7.1") also hits 16.7.10 … 16.7.14
  * S.No. 10.11 (Childhood Diseases - Diarrhoea), Guntur Total must equal 2931
  * period is April–October 2019 = 7 months, so cases_per_month = total / 7

The result is a DISTRICT-level figure stored as one caseload row under the
sentinel facility_id from db.district_caseload_id(). Allocation across
facilities is Phase 5's job and does not happen here.
"""

import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import (  # noqa: E402
    HMIS_CSV, HMIS_ENCODING, HMIS_PERIOD_MONTHS, HMIS_SOURCE_PERIOD, TARGET_DISTRICT,
)
from backend.db import create_tables, district_caseload_id, get_connection  # noqa: E402

SNO_COLUMN = "S.No."
TYPE_COLUMN = "Type"
PARAM_COLUMN = "Parameters"
TOTAL_ROW_TYPE = "TOTAL"  # stock-flow rows reuse an S.No. with other Type values

# HMIS indicator -> internal disease key used by config/rules.yaml.
HMIS_INDICATORS = {
    "10.11": "acute_diarrhoeal_disease",  # "Childhood Diseases - Diarrhoea"
}

# Verified values from PROJECT_CONTEXT.md §7. The loader fails loudly if the
# file does not reproduce them.
EXPECTED_TOTALS = {
    ("Guntur", "10.11"): 2931,
}

UPSERT = """
INSERT INTO caseloads (facility_id, disease, cases_per_month, source_period)
VALUES (:facility_id, :disease, :cases_per_month, :source_period)
ON CONFLICT(facility_id, disease) DO UPDATE SET
    cases_per_month = excluded.cases_per_month, source_period = excluded.source_period
"""


def clean_sno(value):
    return value.replace("'", "").replace('"', "").strip()


def find_total_column(header, district):
    prefix = f"District - {district} - Total"
    matches = [c for c in header if c.startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one '{prefix}…' column, found {matches}")
    return matches[0]


def load(conn, csv_path=HMIS_CSV, district=TARGET_DISTRICT, verbose=True):
    with open(csv_path, encoding=HMIS_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        total_col = find_total_column(header, district)
        # Only the indicator rows we need are kept; exact match on cleaned S.No.
        wanted = {}
        for row in reader:
            sno = clean_sno(row[SNO_COLUMN])
            if sno in HMIS_INDICATORS and row[TYPE_COLUMN].strip() == TOTAL_ROW_TYPE:
                if sno in wanted:
                    raise SystemExit(f"S.No. {sno} matched more than one TOTAL row")
                wanted[sno] = row

    if verbose:
        print(f"HMIS: {len(header)} columns; using column {total_col!r}")

    missing = set(HMIS_INDICATORS) - set(wanted)
    if missing:
        raise SystemExit(f"HMIS indicators not found: {sorted(missing)}")

    stored = []
    with conn:
        for sno, disease in HMIS_INDICATORS.items():
            row = wanted[sno]
            total = int(float(row[total_col].strip()))
            expected = EXPECTED_TOTALS.get((district, sno))
            if expected is not None and total != expected:
                raise SystemExit(
                    f"HMIS S.No. {sno} {district} Total = {total}, expected {expected} "
                    f"(PROJECT_CONTEXT.md section 7). File or parsing has changed - stop."
                )
            record = {
                "facility_id": district_caseload_id(district),
                "disease": disease,
                "cases_per_month": total / HMIS_PERIOD_MONTHS,
                "source_period": HMIS_SOURCE_PERIOD,
            }
            conn.execute(UPSERT, record)
            stored.append(record)
            if verbose:
                print(f"  S.No. {sno} ({row[PARAM_COLUMN].strip()}): {district} Total = {total}"
                      + (f"  [matches PROJECT_CONTEXT section 7 expected {expected}]" if expected is not None else "")
                      + f" -> {record['cases_per_month']:.2f} cases/month over {HMIS_PERIOD_MONTHS} months")
                print(f"  stored caseload row: {record}")
    return {"stored": stored}


if __name__ == "__main__":
    with get_connection() as conn:
        create_tables(conn)
        load(conn)
