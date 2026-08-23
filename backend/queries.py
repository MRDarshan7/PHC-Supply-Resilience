"""Canonical facility selection and outbreak localisation.

Every consumer that needs "the facilities we actually use" goes through
active_facilities() / active_phcs(). The filter is defined here and nowhere
else. Exclusions are logged, never silent.

demo_phcs() narrows active_phcs() to the subset the ledger, map and donor
engine operate on: no placeholder coordinates, no known-bad rows, and no two
facilities on the same point.

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

from config.settings import (  # noqa: E402
    ACTIVE_FACILITY_STATE, ACTIVE_LAT_RANGE, ACTIVE_LON_RANGE,
    DEMO_EXCLUDED_FACILITY_IDS, DEMO_PLACEHOLDER_COORDINATES, DEMO_TARGET_FACILITY_ID,
)
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
# Demo subset
# --------------------------------------------------------------------------- #

def demo_exclusion_stats(conn):
    """Apply the demo filters to active_phcs() one stage at a time and return
    what each stage removed. demo_phcs() is the last stage's survivors.

    Stages, in order:
      1. drop every facility on a point in DEMO_PLACEHOLDER_COORDINATES
         (all of them — a placeholder is not anyone's real location)
      2. drop facility_id in DEMO_EXCLUDED_FACILITY_IDS (known-bad source rows)
      3. drop any facility sharing an exact (lat, lon) with an earlier one —
         first by name (then facility_id) is kept, so the choice is deterministic
    """
    active = active_phcs(conn)

    excluded_placeholder = [f for f in active if on_placeholder(f)]
    after_placeholder = [f for f in active if not on_placeholder(f)]
    placeholder_counts = {}
    for f in excluded_placeholder:
        placeholder_counts[(f["lat"], f["lon"])] = placeholder_counts.get((f["lat"], f["lon"]), 0) + 1

    excluded_ids = [f for f in after_placeholder if f["facility_id"] in DEMO_EXCLUDED_FACILITY_IDS]
    after_ids = [f for f in after_placeholder if f["facility_id"] not in DEMO_EXCLUDED_FACILITY_IDS]
    missing_ids = sorted(set(DEMO_EXCLUDED_FACILITY_IDS) - {f["facility_id"] for f in active})

    kept, dropped, seen = [], [], {}
    for f in sorted(after_ids, key=lambda f: (f["name"], f["facility_id"])):
        key = (f["lat"], f["lon"])
        if key in seen:
            dropped.append({"dropped": f, "kept": seen[key]})
        else:
            seen[key] = f
            kept.append(f)

    return {
        "active": len(active),
        "excluded_placeholder": excluded_placeholder,
        "placeholder_counts": placeholder_counts,       # {(lat, lon): rows dropped}
        "excluded_facility_ids": excluded_ids,
        "excluded_ids_not_found": missing_ids,          # configured ids absent from active set
        "coordinate_collisions": dropped,               # [{"dropped": f, "kept": f}, ...]
        "kept": kept,
    }


def on_placeholder(f, tolerance=1e-6):
    """True when the facility sits on a configured placeholder coordinate."""
    return any(abs(f["lat"] - lat) < tolerance and abs(f["lon"] - lon) < tolerance
               for lat, lon in DEMO_PLACEHOLDER_COORDINATES)


def demo_phcs(conn):
    """active_phcs() minus placeholder-coordinate rows, known-bad rows and exact
    coordinate duplicates. This is the facility set the ledger is generated
    for. Ordered by name. Logs every exclusion stage."""
    s = demo_exclusion_stats(conn)
    log.info(
        "demo phcs: kept %d of %d active | excluded %d on placeholder coordinates %s "
        "| excluded %d by facility_id | dropped %d exact coordinate duplicates",
        len(s["kept"]), s["active"], len(s["excluded_placeholder"]), DEMO_PLACEHOLDER_COORDINATES,
        len(s["excluded_facility_ids"]), len(s["coordinate_collisions"]),
    )
    for missing in s["excluded_ids_not_found"]:
        log.warning("demo phcs: DEMO_EXCLUDED_FACILITY_IDS entry %r is not an active PHC - "
                    "the source row may have changed; check the exclusion is still needed", missing)
    for c in s["coordinate_collisions"]:
        log.info("  coordinate collision at %s, %s: kept %r (%s), dropped %r (%s)",
                 c["kept"]["lat"], c["kept"]["lon"], c["kept"]["name"], c["kept"]["sub_district"],
                 c["dropped"]["name"], c["dropped"]["sub_district"])
    if not any(f["facility_id"] == DEMO_TARGET_FACILITY_ID for f in s["kept"]):
        log.error("demo phcs: target facility %s is NOT in the demo set", DEMO_TARGET_FACILITY_ID)
    return s["kept"]


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

        print("\n== demo_phcs() ==")
        ds = demo_exclusion_stats(conn)
        demo = demo_phcs(conn)
        print(f"  active PHCs: {ds['active']}")
        print(f"  excluded on placeholder coordinates: {len(ds['excluded_placeholder'])}")
        for (plat, plon), n in ds["placeholder_counts"].items():
            subs = sorted({f["sub_district"] for f in ds["excluded_placeholder"] if on_placeholder(f)})
            print(f"    {n} rows at {plat}, {plon} (sub_district {subs})")
        kept_urban = [f for f in demo if f["sub_district"] == "Urban Health Facilities"]
        print(f"  'Urban Health Facilities' rows with genuine coordinates kept: {len(kept_urban)}")
        print(f"  excluded by facility_id {DEMO_EXCLUDED_FACILITY_IDS}: "
              f"{[(f['name'], f['lat'], f['lon']) for f in ds['excluded_facility_ids']]}")
        if ds["excluded_ids_not_found"]:
            print(f"  WARNING configured exclusions not found among active PHCs: {ds['excluded_ids_not_found']}")
        print(f"  exact coordinate collisions: {len(ds['coordinate_collisions'])}")
        for c in ds["coordinate_collisions"]:
            print(f"    {c['kept']['lat']}, {c['kept']['lon']}: kept {c['kept']['facility_id']} "
                  f"({c['kept']['sub_district']}), dropped {c['dropped']['facility_id']} ({c['dropped']['sub_district']})")
        print(f"  demo count: {len(demo)}")
        target = [f for f in demo if f["facility_id"] == DEMO_TARGET_FACILITY_ID]
        assert target, f"{DEMO_TARGET_FACILITY_ID} missing from demo_phcs()"
        print(f"  OK: target {DEMO_TARGET_FACILITY_ID} ({target[0]['name']}, {target[0]['sub_district']}) "
              f"at {target[0]['lat']}, {target[0]['lon']} is in the demo set")
        assert len({(f["lat"], f["lon"]) for f in demo}) == len(demo), "demo set still has duplicate coordinates"
        print("  OK: every demo facility has a unique coordinate")

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
