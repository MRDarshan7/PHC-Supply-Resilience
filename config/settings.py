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

DATA_DIR = PROJECT_ROOT / "data"
IDSP_PDF_DIR = DATA_DIR / "idsp_pdfs"
FACILITY_CSV = DATA_DIR / "facility_directory.csv"
HMIS_CSV = DATA_DIR / "hmis_raw.csv"
MEDICINES_CSV = DATA_DIR / "medicines.csv"
RULES_YAML = PROJECT_ROOT / "config" / "rules.yaml"

# The HMIS export is not UTF-8 — it is cp1252 and errors on byte 0xA0 if read as UTF-8.
HMIS_ENCODING = "cp1252"
