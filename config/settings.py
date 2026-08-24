from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# gemini-2.5-flash is deprecated; gemini-3.6-flash returned 503 on 2026-08-21.
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_DELAY_SECONDS = 5

TARGET_STATE = "Andhra Pradesh"
TARGET_DISTRICT = "Guntur"

# 8 days reflects a realistic restocking horizon for a rural PHC: a district
# store needs roughly a week to receive a request, pick, and deliver, so a
# facility with under 8 days of cover is the one that will run dry before
# the next lorry arrives. The previous value (7) left the demo facility
# (Thulluru / ORS, 6.884 d after the week-45 surge) only 0.116 days of margin
# below the line - too tight to be robust against any upstream change
# (scaling factor, seed, rule table, facility set). WARNING_DAYS stays at 14.
CRITICAL_DAYS = 8
WARNING_DAYS = 14

DONOR_SAFETY_FLOOR_DAYS = 14
# 15, not 14: with the target equal to WARNING_DAYS and quantities rounded up,
# a topped-up medicine lands at exactly 14.0 days and displays "14.0 - safe"
# beside a 14-day threshold, which reads as a contradiction. One day above the
# warning line makes a completed transfer visibly safe.
TARGET_COVER_DAYS = 15

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

# ---------------------------------------------------------------------------
# Phase 7 — IDSP pipeline (backend/ingest_idsp.py, backend/outbreak_demand.py)
# ---------------------------------------------------------------------------
# Every Gemini response is cached here, keyed by the SHA-256 of the input PDF.
# A second ingest of the same file must make zero Gemini calls. The directory
# is gitignored (.cache/), so a fresh clone re-extracts once per PDF.
GEMINI_CACHE_DIR = PROJECT_ROOT / ".cache" / "gemini"
# The report the demo is built around (IDSP week 45, 2025 - see PROJECT_CONTEXT.md section 7).
DEMO_IDSP_PDF = IDSP_PDF_DIR / "idsp_2025_w45.pdf"

# Outbreak -> extra burn rate. An outbreak's cases are allocated across the
# demo facilities by each facility's share of the district caseload for that
# disease, with facilities localised to the outbreak's sub-district boosted.
#
# Why a boost at all: allocation by district caseload share alone
# under-represents geographic concentration. The source PDF places the 457
# Guntur cases at a university hostel in Thullur village, so patients present
# at the nearest facilities rather than in proportion to district-wide
# caseload. The boost applies to facilities matched at tier 1 or tier 2 by
# backend.queries.find_facilities_for_outbreak, and weights are renormalised
# so allocated cases still sum to the outbreak total.
#
# Why 5.0: at 2.0 the surge/baseline ratio for an unboosted facility is
# (457/7) / (418.71/30 x CONSUMPTION_SCALING_FACTOR) = 0.585, which takes the
# Thulluru PHC from 26.4 to 12.2 days of ORS cover (warning, not critical) and
# pushes no facility below CRITICAL_DAYS. At 5.0 Thulluru lands just under the
# 7-day line (~6.9 days); backend/test_demo.py asserts this and prints the margin.
OUTBREAK_LOCAL_BOOST = 5.0
# Allocated cases are assumed to present over this many days:
# extra_cases_per_day = allocated_cases / OUTBREAK_WINDOW_DAYS.
OUTBREAK_WINDOW_DAYS = 7

# ---------------------------------------------------------------------------
# Phase 8 - redistribution engine (backend/redistribute.py)
# ---------------------------------------------------------------------------
# DONOR_SAFETY_FLOOR_DAYS, TARGET_COVER_DAYS and MAX_TRANSFER_RADIUS_KM above
# are the engine's primary parameters. The safety rule is
#     spare = donor_stock - donor_burn_rate x DONOR_SAFETY_FLOOR_DAYS
# with donor_burn_rate INCLUDING outbreak surge; spare <= 0 means not eligible.
#
# Expiry. A lot may only travel if it will still be usable after it arrives:
# it must expire strictly later than today + TRANSFER_TRANSIT_DAYS +
# TRANSFER_MIN_USE_DAYS (the "transit-and-use window"). Straight-line
# distances under MAX_TRANSFER_RADIUS_KM are a one-day vehicle trip; two days
# covers approval-to-shelf. Five days is the least dispensing time that
# makes moving a lot worthwhile at all.
TRANSFER_TRANSIT_DAYS = 2
TRANSFER_MIN_USE_DAYS = 5

# Stage 4 scoring weights. Every component is normalised to 0-1 before
# weighting; the weights sum to 1.0 so a score is itself 0-1.
#   sufficiency   fraction of the recipient's need this donor alone can meet
#   proximity     linear distance decay, 1 - distance / MAX_TRANSFER_RADIUS_KM,
#                 clamped 0-1. Absolute, not relative to the candidate set: a
#                 single very close donor cannot collapse every other
#                 candidate's proximity to near zero (inverse distance
#                 normalised over candidates did exactly that - one donor at
#                 0.2 km left everything at 19 km scoring 0.01)
#   expiry_benefit units moved that would otherwise have expired unused at the
#                 donor AND that the recipient will consume before expiry, as
#                 a fraction of the recipient's need
#   donor_comfort how far above the safety floor the donor remains after the
#                 transfer, as a fraction of the floor (capped: a donor left
#                 with 2 x DONOR_SAFETY_FLOOR_DAYS of cover scores 1.0)
#
# PROJECT_CONTEXT.md section 10 gave sufficiency 0.40 / expiry 0.20. At 0.40,
# sufficiency over-rewarded large donors: any facility with spare >= need
# scored a full 0.40 and a near-expiry donor could not catch up. Rescuing
# stock from certain expiry is worth as much as meeting the last fraction of
# need in one movement, so the two now carry equal weight. Proximity and
# comfort are unchanged.
SCORE_WEIGHTS = {
    "sufficiency": 0.30,
    "proximity": 0.25,
    "expiry_benefit": 0.30,
    "donor_comfort": 0.15,
}

# ---------------------------------------------------------------------------
# Phase 9 - transfer memo (backend/memo.py) and API (backend/main.py)
# ---------------------------------------------------------------------------
# Gemini's second job: write the justification a District Medical Officer
# signs, and - only where the top candidates score closely - choose between
# them. It has no authority over quantities. Every number it writes is
# cross-checked against the backend-computed input and any mismatch rejects
# the whole response in favour of a deterministic template.
#
# A "close call" is any eligible donor whose score is within this margin of
# the top score. Gemini may reorder donors only inside that set; when the set
# holds one donor there is nothing to adjudicate and the backend's plan is
# the only valid answer (PROJECT_CONTEXT.md section 5: do not manufacture
# close calls). Scores are 0-1 weighted sums, so 0.05 is five points.
MEMO_CLOSE_CALL_MARGIN = 0.05
# How many ranked eligible donors the prompt describes in full (every
# rejected candidate is always included - they are part of the product).
MEMO_ELIGIBLE_IN_PROMPT = 10
# Bumping this invalidates every cached memo (the version is part of the
# cache key) - do it whenever the prompt or the input layout changes.
MEMO_PROMPT_VERSION = 1
# Memo responses are cached like IDSP extractions, under GEMINI_CACHE_DIR,
# keyed by a hash of the exact input sent; only responses that passed
# validation are cached, so a cached memo is always a valid memo.

# Shown with every recommendation (PROJECT_CONTEXT.md section 6).
DECISION_SUPPORT_NOTICE = ("Decision-support only. Recommendations require approval by the authorised "
                           "District Medical Officer. The system executes nothing autonomously.")
