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

# Demo subset — logic in backend/queries.py demo_phcs(), built on active_phcs().
# The facility the whole demo is built around (IDSP week-45 outbreak, sub-district Thullur).
DEMO_TARGET_FACILITY_ID = "phc_thulluru_40a5f3"
# Placeholder coordinates. 16 of the 41 "Urban Health Facilities" rows share
# this one point (the other 25 carry genuine, distinct coordinates and are
# kept). Every facility on a listed point is dropped: they would stack into a
# single map dot and sit 0 km from each other, which would let the donor
# engine recommend a meaningless "0 km transfer".
DEMO_PLACEHOLDER_COORDINATES = ((16.306652, 80.43654),)
# Bad source rows, by facility_id. Pedakakani carries longitude 81.18573 — the
# real village sits beside Guntur city at ~80.45.
DEMO_EXCLUDED_FACILITY_IDS = ("phc_pedakakani_597952",)

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

# ---------------------------------------------------------------------------
# Phase 5 — ledger generator (backend/allocate_caseloads.py, backend/generate_ledger.py)
# ---------------------------------------------------------------------------
# Every random draw in the generator is keyed by (LEDGER_SEED, facility_id, purpose),
# so each facility's characteristics are stable regardless of which other
# facilities exist. Change the seed and every facility changes; keep it and
# every re-run is identical.
LEDGER_SEED = 20251103  # Monday of IDSP week 45, 2025 — the demo outbreak week

# opening_stock = daily_consumption x PROCUREMENT_CYCLE_DAYS x facility_variation
PROCUREMENT_CYCLE_DAYS = 30

# PROJECT_CONTEXT.md §8: the ONE documented factor by which baseline
# consumption is scaled, disclosed in the pitch.
#
# Reasoning: HMIS routine surveillance under-reports diarrhoea. Unscaled, the
# Guntur figure (2931 cases over 7 months, S.No. 10.11) allocates a median of
# 3.58 cases/month across the 104 demo PHCs — 0.48 ORS packets/day for a
# facility serving ~30,000 people — which is not credible for a primary care
# facility. x8 scales consumption to realistic PHC dispensing volumes
# (median ~3.8 ORS packets/day, max ~15).
#
# The factor multiplies daily consumption only. The caseloads table remains
# pure HMIS (facility rows sum exactly to the district figure) and the
# relative distribution across facilities stays HMIS-derived, so Phase 7
# facility shares are untouched. Set to 1.0 to see the raw HMIS figure.
CONSUMPTION_SCALING_FACTOR = 8.0

# A batch counts as near-expiry when it expires within this many days.
NEAR_EXPIRY_DAYS = 30
# The generator guarantees at least this many demo facilities hold a
# near-expiry batch — near-expiry redistribution is a core part of the demo.
NEAR_EXPIRY_MIN_FACILITIES = 3
