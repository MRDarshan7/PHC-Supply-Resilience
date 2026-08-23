from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# gemini-2.5-flash is deprecated; gemini-3.6-flash returned 503 on 2026-08-21.
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_DELAY_SECONDS = 5

TARGET_STATE = "Andhra Pradesh"
TARGET_DISTRICT = "Guntur"

CRITICAL_DAYS = 7
WARNING_DAYS = 14

DONOR_SAFETY_FLOOR_DAYS = 14
TARGET_COVER_DAYS = 14

MAX_TRANSFER_RADIUS_KM = 75

# Canonical "facilities we actually use" filter — logic lives in backend/queries.py.
# The 2016 facility directory lists Guntur twice under two State Name labels
# ("Andhra Pradesh" and "Andhra Pradesh Old"); only the current vintage is used.
ACTIVE_FACILITY_STATE = "Andhra Pradesh"
# Guntur bounding box. Two source rows carry coordinates hundreds of km away
# (a sub-centre placed in Hyderabad, a PHC placed near Kadapa); the box drops them.
ACTIVE_LAT_RANGE = (15.5, 17.0)
ACTIVE_LON_RANGE = (79.3, 81.2)

DATA_DIR = PROJECT_ROOT / "data"
IDSP_PDF_DIR = DATA_DIR / "idsp_pdfs"
FACILITY_CSV = DATA_DIR / "facility_directory.csv"
HMIS_CSV = DATA_DIR / "hmis_raw.csv"
MEDICINES_CSV = DATA_DIR / "medicines.csv"
RULES_YAML = PROJECT_ROOT / "config" / "rules.yaml"
LEDGER_DB = DATA_DIR / "ledger.db"

# The HMIS export is not UTF-8 — it is cp1252 and errors on byte 0xA0 if read as UTF-8.
HMIS_ENCODING = "cp1252"
# HMIS 2019-20 AP item-wise report covers April–October 2019 = 7 months (divide by 7, not 12).
HMIS_PERIOD_MONTHS = 7
HMIS_SOURCE_PERIOD = "2019-20 Apr-Oct (7 months)"
