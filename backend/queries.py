"""Canonical facility selection and outbreak localisation.

Every consumer that needs "the facilities we actually use" goes through
active_facilities() / active_phcs(). The filter is defined here and nowhere
else. Exclusions are logged, never silent.

    py backend/queries.py      # prints verification output
"""

import difflib
import logging
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import ACTIVE_FACILITY_STATE, ACTIVE_LAT_RANGE, ACTIVE_LON_RANGE  # noqa: E402
from backend.db import create_tables, get_connection, table_columns  # noqa: E402

log = logging.getLogger(__name__)

FACILITY_COLUMNS = "facility_id, name, type, district, sub_district, state, lat, lon"
_ACTIVE_WHERE = (
    "state = :state AND lat BETWEEN :lat_lo AND :lat_hi AND lon BETWEEN :lon_lo AND :lon_hi"
)


def _params(facility_type=None):
    return {
        "state": ACTIVE_FACILITY_STATE,
        "lat_lo": ACTIVE_LAT_RANGE[0], "lat_hi": ACTIVE_LAT_RANGE[1],
        "lon_lo": ACTIVE_LON_RANGE[0], "lon_hi": ACTIVE_LON_RANGE[1],
        "type": facility_type,
    }


# --------------------------------------------------------------------------- #
# Active facilities
# --------------------------------------------------------------------------- #

def exclusion_stats(conn, facility_type=None):
    """Counts for each filter stage, plus the rows the bounding box removed."""
    type_clause = " AND type = :type" if facility_type else ""
    p = _params(facility_type)
    total = conn.execute(f"SELECT COUNT(*) FROM facilities WHERE 1=1{type_clause}", p).fetchone()[0]
    current = conn.execute(f"SELECT COUNT(*) FROM facilities WHERE state = :state{type_clause}", p).fetchone()[0]
    kept = conn.execute(f"SELECT COUNT(*) FROM facilities WHERE {_ACTIVE_WHERE}{type_clause}", p).fetchone()[0]
    bbox_rows = conn.execute(
        f"SELECT name, type, state, lat, lon FROM facilities "
        f"WHERE state = :state AND NOT ({_ACTIVE_WHERE}){type_clause} ORDER BY name", p,
    ).fetchall()
    return {
        "facility_type": facility_type or "all",
        "total": total,
        "excluded_old_vintage": total - current,
        "excluded_bbox": current - kept,
        "excluded_bbox_rows": [dict(r) for r in bbox_rows],
        "kept": kept,
    }


def active_facilities(conn, facility_type=None):
    """Facilities in the current vintage whose coordinates fall inside the
    district bounding box. Optionally restricted to one facility type.
    Logs how many rows each filter excluded."""
    type_clause = " AND type = :type" if facility_type else ""
    rows = conn.execute(
        f"SELECT {FACILITY_COLUMNS} FROM facilities WHERE {_ACTIVE_WHERE}{type_clause} ORDER BY name",
        _params(facility_type),
    ).fetchall()

    s = exclusion_stats(conn, facility_type)
    log.info(
        "active facilities (%s): kept %d of %d rows | excluded %d with state != %r (old vintage) "
        "| excluded %d outside lat %s / lon %s",
        s["facility_type"], s["kept"], s["total"], s["excluded_old_vintage"], ACTIVE_FACILITY_STATE,
        s["excluded_bbox"], ACTIVE_LAT_RANGE, ACTIVE_LON_RANGE,
    )
    for r in s["excluded_bbox_rows"]:  # per-row detail at DEBUG; summary line above is always logged
        log.debug("  excluded by bounding box: %s %r at %s, %s", r["type"], r["name"], r["lat"], r["lon"])
    return [dict(r) for r in rows]


def active_phcs(conn):
    """active_facilities() restricted to type = 'phc'."""
    return active_facilities(conn, facility_type="phc")


# --------------------------------------------------------------------------- #
# Outbreak localisation
# --------------------------------------------------------------------------- #

def _norm(s):
    """Lower-case letters only; drop a trailing Telugu '-u' (Thulluru -> thullur,
    Ponnuru -> ponnur) so IDSP and directory spellings line up."""
    s = re.sub(r"[^a-z]", "", (s or "").lower())
    if len(s) > 4 and s.endswith("u"):
        s = s[:-1]
    return s


def names_match(a, b, threshold=0.9):
    """True when two place names are the same allowing for spelling variation.

    0.9 accepts th/t and trailing-vowel variants (Thullur ~ Tullur ~ Thulluru)
    but rejects different villages one letter apart (Thallur vs Thullur)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


TIER_LABELS = {
    1: "facility name matches sub-district",
    2: "facility sub_district column matches",
    3: "all facilities in district",
}


def find_facilities_for_outbreak(conn, district, sub_district, facility_type=None):
    """Three-tier localisation of an outbreak to active facilities.

    Tier 1: facilities whose NAME matches `sub_district` (fuzzy).
    Tier 2: facilities whose sub_district column matches `sub_district` (fuzzy).
    Tier 3: every active facility in `district`.

    Returns {"tier": 1|2|3|None, "tier_label": str, "district": str,
             "sub_district": str, "facilities": [dict, ...]}.
    tier is None (and facilities empty) when the district itself is unknown.
    """
    in_district = [
        f for f in active_facilities(conn, facility_type)
        if f["district"].strip().lower() == (district or "").strip().lower()
    ]
    result = {"district": district, "sub_district": sub_district, "tier": None,
              "tier_label": "district not found among active facilities", "facilities": []}
    if not in_district:
        log.warning("localisation: no active facilities for district %r", district)
        return result

    tiers = []
    if sub_district and sub_district.strip():
        tiers.append((1, [f for f in in_district if names_match(f["name"], sub_district)]))
        tiers.append((2, [f for f in in_district if names_match(f["sub_district"], sub_district)]))
    tiers.append((3, in_district))

    for tier, matches in tiers:
        if matches:
            result.update(tier=tier, tier_label=TIER_LABELS[tier], facilities=matches)
            log.info("localisation %r / %r -> tier %d (%s): %d facilities",
                     district, sub_district, tier, TIER_LABELS[tier], len(matches))
            return result
    return result  # unreachable in practice: tier 3 is non-empty when in_district is


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

def _print_result(res):
    print(f"  tier={res['tier']} ({res['tier_label']}); {len(res['facilities'])} facilities")
    for f in res["facilities"][:8]:
        print(f"    {f['type']:<8} {f['name']:<22} sub_district={f['sub_district']:<16} {f['lat']}, {f['lon']}")
    if len(res["facilities"]) > 8:
        print(f"    ... {len(res['facilities']) - 8} more")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with get_connection() as conn:
        create_tables(conn)

        print("outbreaks columns:", table_columns(conn, "outbreaks"))

        print("\n== active_phcs() ==")
        phcs = active_phcs(conn)
        print(f"  count: {len(phcs)}")
        s = exclusion_stats(conn, "phc")
        print(f"  excluded: {s['excluded_old_vintage']} old vintage, {s['excluded_bbox']} outside bounding box:")
        for r in s["excluded_bbox_rows"]:
            print(f"    {r['type']} {r['name']!r} at {r['lat']}, {r['lon']}")

        print("\n== active_facilities() ==")
        facs = active_facilities(conn)
        print(f"  count: {len(facs)}")
        s = exclusion_stats(conn)
        print(f"  excluded: {s['excluded_old_vintage']} old vintage, {s['excluded_bbox']} outside bounding box:")
        for r in s["excluded_bbox_rows"]:
            print(f"    {r['type']:<8} {r['name']!r:<22} at {r['lat']}, {r['lon']}")
        lats = [f["lat"] for f in facs]
        lons = [f["lon"] for f in facs]
        print(f"  lat range after filter: {min(lats):.5f} .. {max(lats):.5f}  (box {ACTIVE_LAT_RANGE})")
        print(f"  lon range after filter: {min(lons):.5f} .. {max(lons):.5f}  (box {ACTIVE_LON_RANGE})")
        print("  by type:", {t: sum(1 for f in facs if f["type"] == t) for t in sorted({f["type"] for f in facs})})

        print("\n== find_facilities_for_outbreak('Guntur', 'Thullur') ==")
        res = find_facilities_for_outbreak(conn, "Guntur", "Thullur")
        _print_result(res)
        assert res["tier"] == 1, res["tier"]
        assert any(f["name"] == "Thulluru" and f["type"] == "phc" for f in res["facilities"]), \
            "PHC 'Thulluru' missing from tier-1 result"
        print("  OK: tier 1 and PHC 'Thulluru' present")

        print("\n== find_facilities_for_outbreak('Guntur', 'Thullur', facility_type='phc') ==")
        _print_result(find_facilities_for_outbreak(conn, "Guntur", "Thullur", facility_type="phc"))

        print("\n== tier-2 example: ('Guntur', 'PV Palem') - a sub_district value, not a facility name ==")
        _print_result(find_facilities_for_outbreak(conn, "Guntur", "PV Palem", facility_type="phc"))

        print("\n== tier-3 example: ('Guntur', 'Nowhere Such Place') ==")
        _print_result(find_facilities_for_outbreak(conn, "Guntur", "Nowhere Such Place", facility_type="phc"))

        print("\n== unknown district: ('Palnadu', 'Thullur') ==")
        _print_result(find_facilities_for_outbreak(conn, "Palnadu", "Thullur"))
