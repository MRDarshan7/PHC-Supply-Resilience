from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 2.5 is deprecated by Google; 3.6 returned 503 errors on 2026-08-21.
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

# The HMIS export is not UTF-8.
HMIS_ENCODING = "cp1252"
