"""Outbreak records -> extra burn rate (PROJECT_CONTEXT.md sec 2, sec 11 Phase 7).

An outbreak never changes how much stock a facility has. It changes how fast
that stock is consumed, and it enters the calculation as the outbreak_surge
input to risk.burn_rate() - before days of cover is computed. One formula,
no fork: this module only produces the number that parameter receives.

Allocation of one outbreak's cases across the demo facilities:

    base_weight_f  = facility caseload / district caseload for that disease
    weight_f       = base_weight_f x OUTBREAK_LOCAL_BOOST
                       if f is localised to the outbreak's sub-district
                       (queries.find_facilities_for_outbreak, tier 1 or 2)
                     else base_weight_f
    allocated_f    = cases x weight_f / sum(weights)          (sums to cases)
    extra_cases_per_day_f = allocated_f / OUTBREAK_WINDOW_DAYS
    extra_burn_f[m] = extra_cases_per_day_f x per_case[disease][m]
                       for every medicine m the disease maps to in rules.yaml

Why not all of it on one PHC: the 457 Guntur cases came from a university
hostel in Thullur, and the report notes patients were treated both at a
campus health camp and at NRI General Hospital. District-wide by caseload
share, boosted for the affected sub-district, reflects how patients present.

CONSUMPTION_SCALING_FACTOR is NOT applied to the surge. It corrects routine
HMIS under-reporting in the baseline; IDSP outbreak counts are directly
counted cases and need no such correction.

Only outbreaks that are in scope (district in the facilities table for the
target state) AND whose disease is in rules.yaml contribute. Everything else
is on record, flagged, and ignored here: no surge, no guess.

    py backend/outbreak_demand.py     # ingest demo PDF (cached) -> before/after risk report
"""

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.generate_ledger import load_facility_caseloads  # noqa: E402
from backend.ingest_idsp import alias_table, classify, ingest, known_districts  # noqa: E402
from backend.queries import demo_phcs, find_facilities_for_outbreak  # noqa: E402
from backend.risk import SEVERITY, all_risk  # noqa: E402
from backend.rules import load_rules  # noqa: E402

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Which outbreaks count
# --------------------------------------------------------------------------- #

def active_outbreaks(conn):
    """Stored outbreaks that may drive a surge: in scope and matched to a
    rules.yaml disease. The verdict is re-derived from the live facilities
    table and rule file (the authorities), and a stored verdict that no
    longer agrees is logged - re-ingesting the file (cached, free) refreshes
    it. Each row carries disease_key from the live match."""
    districts = known_districts(conn)
    aliases = alias_table()
    active = []
    for r in conn.execute("SELECT * FROM outbreaks ORDER BY year, week, outbreak_id"):
        row = dict(r)
        in_scope, disease_key, flags = classify(row["state"], row["district"], row["disease"], districts, aliases)
        if (int(in_scope), disease_key) != (row["in_scope"], row["disease_key"]):
            log.warning("%s: stored verdict (in_scope=%s, disease_key=%s) differs from live rules "
                        "(in_scope=%s, disease_key=%s) - using live; re-ingest %s to refresh",
                        row["outbreak_id"], row["in_scope"], row["disease_key"], int(in_scope), disease_key,
                        row["source_file"])
        if in_scope and disease_key:
            row["disease_key"] = disease_key
            active.append(row)
    return active


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #

def facility_base_shares(conn, facilities, disease_key):
    """({facility_id: share}, basis). Share of the district caseload for this
    disease across the demo facilities (sums to 1). If no facility has a
    caseload for the disease, fall back to each facility's share of its
    all-disease caseload (its catchment size - the same per-facility weight
    allocate_caseloads uses for every disease), then to a uniform split.
    The basis used is returned and logged whenever it is not the first."""
    caseloads = load_facility_caseloads(conn)
    ids = [f["facility_id"] for f in facilities]
    by_disease = {fid: caseloads.get(fid, {}).get(disease_key, 0.0) for fid in ids}
    total = sum(by_disease.values())
    if total > 0:
        return {fid: v / total for fid, v in by_disease.items()}, f"share of district {disease_key} caseload"
    by_all = {fid: sum(caseloads.get(fid, {}).values()) for fid in ids}
    total = sum(by_all.values())
    if total > 0:
        log.warning("no %s caseload at any demo facility - base weights use each facility's share of "
                    "its all-disease caseload (catchment proxy)", disease_key)
        return {fid: v / total for fid, v in by_all.items()}, "share of all-disease caseload (no caseload for this disease)"
    log.warning("no caseload at all for demo facilities - base weights uniform")
    return {fid: 1.0 / len(ids) for fid in ids}, "uniform (no caseload data)"


def localise(conn, outbreak):
    """(tier, tier_label, {facility_id}) - the PHCs find_facilities_for_outbreak
    puts in the outbreak's sub-district at tier 1 or 2. Empty set at tier 3
    (district-wide, nothing to boost) or when the district is unknown."""
    res = find_facilities_for_outbreak(conn, outbreak["district"], outbreak.get("sub_district") or "",
                                       facility_type="phc")
    ids = {f["facility_id"] for f in res["facilities"]} if res["tier"] in (1, 2) else set()
    return res["tier"], res["tier_label"], ids


def allocate_cases(cases, base_shares, boosted, boost=None):
    """{facility_id: allocated cases}. Boosted facilities' weights are
    multiplied by `boost` (OUTBREAK_LOCAL_BOOST), then everything is
    renormalised so the allocation sums to `cases` exactly."""
    boost = settings.OUTBREAK_LOCAL_BOOST if boost is None else boost
    weights = {fid: w * (boost if fid in boosted else 1.0) for fid, w in base_shares.items()}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("allocate_cases: all weights are zero")
    return {fid: cases * w / total for fid, w in weights.items()}


def allocate_outbreak(conn, outbreak, facilities=None, rules=None):
    """Full allocation of one active outbreak. Returns a dict with the
    outbreak's identity, tier / boosted facilities, the share basis, the
    disease's per_case table and, per facility: base_share, weight, cases,
    cases_per_day and extra_burn {medicine_id: units/day}."""
    facilities = facilities if facilities is not None else demo_phcs(conn)
    rules = rules if rules is not None else load_rules()
    disease_key = outbreak["disease_key"]
    per_case = {row["medicine"]: float(row["per_case"]) for row in rules[disease_key]}

    shares, basis = facility_base_shares(conn, facilities, disease_key)
    tier, tier_label, localised = localise(conn, outbreak)
    boosted = localised & set(shares)          # only demo facilities carry a ledger
    allocated = allocate_cases(outbreak["cases"], shares, boosted)

    by_fac = {f["facility_id"]: f for f in facilities}
    allocation = {}
    for fid, cases in allocated.items():
        per_day = cases / settings.OUTBREAK_WINDOW_DAYS
        allocation[fid] = {
            "name": by_fac[fid]["name"], "sub_district": by_fac[fid]["sub_district"],
            "base_share": shares[fid],
            "weight": shares[fid] * (settings.OUTBREAK_LOCAL_BOOST if fid in boosted else 1.0),
            "cases": cases, "cases_per_day": per_day,
            "extra_burn": {mid: per_day * pc for mid, pc in per_case.items()},
        }
    log.info("%s: %d %s cases -> %d facilities by %s; tier %s (%s), boosted x%g: %s",
             outbreak["outbreak_id"], outbreak["cases"], disease_key, len(allocation), basis, tier, tier_label,
             settings.OUTBREAK_LOCAL_BOOST, sorted(by_fac[f]["name"] for f in boosted) or "none")
    return {
        "outbreak_id": outbreak["outbreak_id"], "district": outbreak["district"],
        "sub_district": outbreak.get("sub_district") or "", "disease": outbreak["disease"],
        "disease_key": disease_key, "cases": outbreak["cases"],
        "tier": tier, "tier_label": tier_label, "share_basis": basis,
        "boosted": sorted(boosted), "localised_not_in_demo": sorted(localised - boosted),
        "per_case": per_case, "allocation": allocation,
    }


def allocations(conn, facilities=None, outbreaks=None):
    """allocate_outbreak() for every active outbreak (or the ones given)."""
    facilities = facilities if facilities is not None else demo_phcs(conn)
    outbreaks = outbreaks if outbreaks is not None else active_outbreaks(conn)
    rules = load_rules()
    return [allocate_outbreak(conn, ob, facilities, rules) for ob in outbreaks]


def surge_from_allocations(allocs):
    """{facility_id: {medicine_id: extra units/day}} summed over allocations.
    Facilities with no surge are simply absent."""
    table = {}
    for a in allocs:
        for fid, x in a["allocation"].items():
            slot = table.setdefault(fid, {})
            for mid, extra in x["extra_burn"].items():
                slot[mid] = slot.get(mid, 0.0) + extra
    return table


def surge_table(conn, facilities=None, outbreaks=None):
    """{facility_id: {medicine_id: extra units/day}} over every active
    outbreak - the value risk.all_risk(conn, surge=...) feeds to burn_rate()."""
    return surge_from_allocations(allocations(conn, facilities, outbreaks))


# --------------------------------------------------------------------------- #
# Verification (Phase 7 Part C, PROJECT_CONTEXT.md sec 11)
# --------------------------------------------------------------------------- #

BANDS = ("critical", "warning", "safe", "unknown")


def band_counts(rows):
    counts = {b: 0 for b in BANDS}
    for r in rows:
        counts[r["band"]] += 1
    return counts


def facility_summary(rows):
    """{facility_id: {facility_id, name, sub_district, band, worst_medicine_id, days}} -
    the worst (facility, medicine) row per facility; days is that row's
    days of cover (None when every medicine is unknown)."""
    out = {}
    for r in rows:
        cur = out.get(r["facility_id"])
        worse = cur is None or SEVERITY[r["band"]] > SEVERITY[cur["band"]] or (
            r["band"] == cur["band"] and r["days_of_cover"] is not None
            and (cur["days"] is None or r["days_of_cover"] < cur["days"]))
        if worse:
            out[r["facility_id"]] = {"facility_id": r["facility_id"], "name": r["name"], "sub_district": r["sub_district"],
                                     "band": r["band"], "worst_medicine_id": r["medicine_id"], "days": r["days_of_cover"]}
    return out


def _days(d):
    return "n/a" if d is None else f"{d:.1f}"


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("backend.queries", "google_genai", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'OK' if cond else 'FAIL'}: {msg}")

    with get_connection() as conn:
        create_tables(conn)
        target = settings.DEMO_TARGET_FACILITY_ID

        print("=== 0. Risk BEFORE ingest (no outbreak surge) ===")
        before = all_risk(conn, surge={})
        before_pairs = band_counts(before)
        before_fac = facility_summary(before)
        before_fac_bands = band_counts([{"band": f["band"]} for f in before_fac.values()])
        print(f"  (facility, medicine) pairs: " + "  ".join(f"{b} {before_pairs[b]}" for b in BANDS))
        print(f"  facilities by worst band:   " + "  ".join(f"{b} {before_fac_bands[b]}" for b in BANDS))

        print(f"\n=== 1. Ingest {settings.DEMO_IDSP_PDF.name} ===")
        r = ingest(settings.DEMO_IDSP_PDF, conn)
        print(f"  ok={r['ok']}  cache_hit={r['cache_hit']}  gemini_calls={r['gemini_calls']}  "
              f"extracted {r['extracted']}  stored {r['stored']}  rejected {len(r['rejected'])}  "
              f"out_of_scope {r['out_of_scope']}  unknown_disease {r['unknown_disease']}")
        check(r["ok"] and r["stored"] == 44, "44 valid records on record")
        print("  database query - SELECT outbreak_id, district, sub_district, disease, cases FROM outbreaks WHERE in_scope = 1:")
        for row in conn.execute("SELECT outbreak_id, district, sub_district, disease, cases FROM outbreaks WHERE in_scope = 1"):
            print(f"    {tuple(row)}")

        print("\n=== 2. Active outbreaks (in scope AND disease in rules.yaml) ===")
        active = active_outbreaks(conn)
        for ob in active:
            print(f"  {ob['outbreak_id']}  {ob['district']} / {ob['sub_district'] or '-'}  {ob['disease']}  "
                  f"{ob['cases']} cases  -> {ob['disease_key']}")
        n_all = conn.execute("SELECT COUNT(*) FROM outbreaks").fetchone()[0]
        inactive = conn.execute("SELECT SUM(in_scope = 0) oos, SUM(disease_key IS NULL) unk, "
                                "SUM(in_scope = 1 AND disease_key IS NULL) in_scope_unk FROM outbreaks").fetchone()
        print(f"  {len(active)} of {n_all} outbreaks drive a surge. Inactive: {inactive['oos']} out of scope, "
              f"{inactive['unk']} with no rules.yaml disease ({inactive['in_scope_unk']} of those in scope)")
        check(all(ob["disease_key"] is not None for ob in active), "no active outbreak lacks a rule (flagged rows apply no surge)")
        check(len(active) >= 1 and any(ob["outbreak_id"] == "AP/GUN/2025/45/1997" for ob in active),
              "the Guntur outbreak is active")

        facilities = demo_phcs(conn)
        allocs = allocations(conn, facilities, active)
        surge = surge_from_allocations(allocs)

        print("\n=== 3. Allocation (OUTBREAK_LOCAL_BOOST = %g, OUTBREAK_WINDOW_DAYS = %d) ===" % (
            settings.OUTBREAK_LOCAL_BOOST, settings.OUTBREAK_WINDOW_DAYS))
        for a in allocs:
            alloc = a["allocation"]
            total_cases = sum(x["cases"] for x in alloc.values())
            print(f"  {a['outbreak_id']}: {a['cases']} {a['disease']} cases over {len(alloc)} demo PHCs")
            print(f"    base weight: {a['share_basis']} (sums to {sum(x['base_share'] for x in alloc.values()):.6f})")
            print(f"    localisation: tier {a['tier']} ({a['tier_label']}); boosted x{settings.OUTBREAK_LOCAL_BOOST:g}: "
                  + (", ".join(alloc[f]["name"] for f in a["boosted"]) or "none"))
            if a["localised_not_in_demo"]:
                print(f"    localised but not in the demo set (no ledger, not boosted): {a['localised_not_in_demo']}")
            print(f"    allocated cases sum = {total_cases:.6f}  (outbreak total {a['cases']})  "
                  f"-> {total_cases / settings.OUTBREAK_WINDOW_DAYS:.2f} extra cases/day district-wide")
            check(abs(total_cases - a["cases"]) < 1e-6, "allocated cases sum to the outbreak total")
            print(f"    per_case (rules.yaml): " + ", ".join(f"{m} x{pc:g}" for m, pc in a["per_case"].items()))
            print(f"    {'facility':<44} {'base share':>10} {'weight':>8} {'cases':>7} {'cases/day':>9}  extra burn/day")
            top = sorted(alloc.items(), key=lambda kv: -kv[1]["cases"])[:5]
            shown = {fid for fid, _ in top}
            if target in alloc and target not in shown:
                top.append((target, alloc[target]))
            for fid, x in top:
                tag = " <- target" if fid == target else ""
                print(f"    {x['name'] + ' (' + x['sub_district'] + ')':<44} {x['base_share']:>10.4%} {x['weight']:>8.4f} "
                      f"{x['cases']:>7.2f} {x['cases_per_day']:>9.3f}  "
                      + ", ".join(f"{m} {v:.2f}" for m, v in x["extra_burn"].items()) + tag)
            med_ids = set(a["per_case"])
            check(all(set(x["extra_burn"]) == med_ids for x in alloc.values()),
                  f"surge applies only to the medicines {a['disease_key']} maps to: {sorted(med_ids)}")

        print("\n=== 4. Risk AFTER ingest (surge fed to risk.burn_rate via outbreak_surge) ===")
        after = all_risk(conn, surge=surge)
        after_pairs = band_counts(after)
        after_fac = facility_summary(after)
        after_fac_bands = band_counts([{"band": f["band"]} for f in after_fac.values()])
        print(f"  {'':<28} {'critical':>9} {'warning':>9} {'safe':>9} {'unknown':>9}")
        print(f"  {'pairs before':<28} " + " ".join(f"{before_pairs[b]:>9}" for b in BANDS))
        print(f"  {'pairs after':<28} " + " ".join(f"{after_pairs[b]:>9}" for b in BANDS))
        print(f"  {'facilities before':<28} " + " ".join(f"{before_fac_bands[b]:>9}" for b in BANDS))
        print(f"  {'facilities after':<28} " + " ".join(f"{after_fac_bands[b]:>9}" for b in BANDS))
        n_surged = sum(1 for r in after if r["outbreak_surge"] > 0)
        print(f"  (facility, medicine) pairs carrying a surge: {n_surged} of {len(after)}")
        check(all(b["stock"] == a["stock"] for b, a in zip(before, after)), "stock did not change - only the burn rate did")

        print(f"\n=== 5. {before_fac[target]['name']} ({target}) / ORS ===")
        b = next(r for r in before if r["facility_id"] == target and r["medicine_id"] == "ors_packets")
        a = next(r for r in after if r["facility_id"] == target and r["medicine_id"] == "ors_packets")
        print(f"  stock            {b['stock']:g} packets (unchanged: {a['stock']:g})")
        print(f"  burn rate        before {b['burn_rate']:.4f}/day   after {a['burn_rate']:.4f}/day   "
              f"(+{a['outbreak_surge']:.4f}/day surge, x{a['burn_rate'] / b['burn_rate']:.2f})")
        print(f"  days of cover    before {_days(b['days_of_cover'])}   after {_days(a['days_of_cover'])}")
        print(f"  band             before {b['band']}   after {a['band']}")
        by_hand = b["stock"] / (b["burn_rate"] + a["outbreak_surge"])
        check(abs(by_hand - a["days_of_cover"]) < 1e-9,
              f"by hand: {b['stock']:g} / ({b['burn_rate']:.4f} + {a['outbreak_surge']:.4f}) = {by_hand:.4f} days")
        print("  other medicines at this facility:")
        for mid in ("zinc_20mg", "iv_fluids_rl", "paracetamol_500", "ciprofloxacin_500"):
            bb = next(r for r in before if r["facility_id"] == target and r["medicine_id"] == mid)
            aa = next(r for r in after if r["facility_id"] == target and r["medicine_id"] == mid)
            print(f"    {mid:<18} stock {bb['stock']:>5g}  burn {bb['burn_rate']:.3f} -> {aa['burn_rate']:.3f}  "
                  f"days {_days(bb['days_of_cover']):>5} -> {_days(aa['days_of_cover']):>5}  {bb['band']} -> {aa['band']}")

        print("\n=== 6. Band transitions ===")
        moves = {}
        for bb, aa in zip(before, after):
            if bb["band"] != aa["band"]:
                moves.setdefault((bb["band"], aa["band"]), []).append(aa)
        into_crit = [r for (_, to), rs in moves.items() if to == "critical" for r in rs]
        into_warn = [r for (_, to), rs in moves.items() if to == "warning" for r in rs]
        print(f"  (facility, medicine) pairs: {sum(len(v) for v in moves.values())} changed band")
        for (frm, to), rs in sorted(moves.items(), key=lambda kv: (-SEVERITY[kv[0][1]], kv[0][0])):
            by_med = {}
            for r in rs:
                by_med[r["medicine_id"]] = by_med.get(r["medicine_id"], 0) + 1
            print(f"    {frm} -> {to}: {len(rs)}  ({', '.join(f'{m} {n}' for m, n in sorted(by_med.items()))})")
        fac_moves = {}
        for fid, bf in before_fac.items():
            af = after_fac[fid]
            if bf["band"] != af["band"]:
                fac_moves.setdefault((bf["band"], af["band"]), []).append((af, bf))
        fac_into_crit = sum(len(v) for (_, to), v in fac_moves.items() if to == "critical")
        fac_into_warn = sum(len(v) for (_, to), v in fac_moves.items() if to == "warning")
        print(f"  facilities (worst band): {fac_into_crit} move into critical, {fac_into_warn} move into warning")
        for (frm, to), v in sorted(fac_moves.items(), key=lambda kv: (-SEVERITY[kv[0][1]], kv[0][0])):
            print(f"    {frm} -> {to}: {len(v)}")
            for af, bf in sorted(v, key=lambda t: (t[0]["days"] if t[0]["days"] is not None else 1e9))[:8]:
                print(f"      {af['name']:<24} {af['sub_district']:<24} {af['worst_medicine_id']:<14} "
                      f"{_days(bf['days']):>5}d -> {_days(af['days']):>5}d")
            if len(v) > 8:
                print(f"      ... {len(v) - 8} more")
        check(fac_into_crit >= 1, f">= 1 facility moved into critical ({fac_into_crit})")
        print(f"  summary: pairs into critical {len(into_crit)}, pairs into warning {len(into_warn)}; "
              f"facilities into critical {fac_into_crit}, into warning {fac_into_warn}")

        print("\n=== 7. Five worst facilities after ingest (lowest days of cover on any medicine) ===")
        worst = sorted((f for f in after_fac.values() if f["days"] is not None), key=lambda f: f["days"])[:5]
        print(f"  {'facility':<24} {'sub_district':<24} {'medicine':<14} {'days':>6}  band      before")
        for f in worst:
            bf = before_fac[f["facility_id"]]
            print(f"  {f['name']:<24} {f['sub_district']:<24} {f['worst_medicine_id']:<14} {f['days']:>6.1f}  "
                  f"{f['band']:<9} {bf['band']} ({_days(bf['days'])}d on {bf['worst_medicine_id']})")

        print(f"\n{'ALL CHECKS PASSED' if ok else 'CHECKS FAILED'}")
        return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
