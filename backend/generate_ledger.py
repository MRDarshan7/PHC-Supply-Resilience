"""Seeded stock-ledger generator (PROJECT_CONTEXT.md §8, §11 Phase 5).

India publishes no facility-level medicine stock, so the opening ledger is
DERIVED from real HMIS caseloads rather than invented. For every demo PHC
(backend.queries.demo_phcs) and every medicine in data/medicines.csv:

    daily_consumption = sum over diseases of
                        (cases_per_month / 30) x per_case[disease][medicine]
                        x CONSUMPTION_SCALING_FACTOR
    opening_stock     = daily_consumption x PROCUREMENT_CYCLE_DAYS x facility_variation

  * cases_per_month   — caseloads table, allocated from the HMIS district
                        figure by backend/allocate_caseloads.py
  * per_case          — config/rules.yaml (curated, each row sourced)
  * facility_variation — ONE number per facility, applied to every medicine
                        it holds, drawn once from a fixed log-normal keyed by
                        (LEDGER_SEED, facility_id). An under-stocked facility is
                        under-stocked in everything: that is a supply problem,
                        not per-row noise. Because days of cover = stock /
                        consumption = PROCUREMENT_CYCLE_DAYS x variation, the
                        variation range 0.4-2.0 is exactly a 12-60 day spread.

Every opening balance is written as one stock_movements row with
source='seed', a batch number and an expiry date. Re-running deletes the
previous seed rows first, so the generator is idempotent and never stacks.

Medicines whose derived consumption is zero (no disease in rules.yaml points
at them, or no caseload is loaded for the disease that does) get NO rows —
there is nothing to derive them from, and inventing a number would break the
provenance rule. They are reported, not silently skipped.

    py backend/generate_ledger.py     # allocate -> generate -> verify -> re-generate -> compare
"""

import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import (  # noqa: E402
    CONSUMPTION_SCALING_FACTOR, DEMO_TARGET_FACILITY_ID, LEDGER_SEED, NEAR_EXPIRY_DAYS,
    NEAR_EXPIRY_MIN_FACILITIES, PROCUREMENT_CYCLE_DAYS,
)
from backend.allocate_caseloads import allocate, caseload_stats, seeded_rng  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.queries import demo_phcs  # noqa: E402
from backend.rules import load_rules, per_case_table  # noqa: E402

SOURCE = "seed"
DAYS_PER_MONTH = 30

# --------------------------------------------------------------------------- #
# Distributions. Every draw is keyed by (LEDGER_SEED, facility_id, ...), see
# allocate_caseloads.seeded_rng.
# --------------------------------------------------------------------------- #

# facility_variation ~ LogNormal(0, sigma) clipped to STOCK_VARIATION_RANGE.
# Median 1.0 = exactly one procurement cycle on the shelf. The clip bounds are
# the model's physical limits: 0.4 cycles (12 days) and 2.0 cycles (60 days).
STOCK_VARIATION_SIGMA = 0.4
STOCK_VARIATION_RANGE = (0.4, 2.0)

# Shelf life by dosage-form class, days (lo, hi). Typical labelled shelf
# lives for WHO-prequalified / Indian-manufactured products of each class.
SHELF_LIFE_DAYS_BY_CLASS = {
    "ors_sachet": (730, 1095),          # ORS powder in laminated sachet: 2-3 years
    "dispersible_tablet": (730, 1095),  # zinc dispersible tablets: 2-3 years
    "tablet": (1095, 1825),             # conventional solid oral tablets: 3-5 years
    "iv_fluid": (730, 1095),            # large-volume parenteral in plastic container: 2-3 years
}
MEDICINE_CLASS = {
    "ors_packets": "ors_sachet",
    "zinc_20mg": "dispersible_tablet",
    "paracetamol_500": "tablet",
    "iv_fluids_rl": "iv_fluid",
    "ciprofloxacin_500": "tablet",
}

# Remaining life of the lot on the shelf at seed time.
#   Long-dated (most lots): supply norms require well over six months of life
#   at receipt, so remaining life is uniform between LONG_DATED_MIN_DAYS and
#   the lot's full shelf life.
#   Short-dated (SHORT_DATED_FRACTION of lots): slow-moving or short-dated at
#   receipt; 1-8 weeks left.
SHORT_DATED_FRACTION = 0.08
SHORT_DATED_RANGE_DAYS = (7, 60)
LONG_DATED_MIN_DAYS = 180

INSERT = """
INSERT INTO stock_movements (facility_id, medicine_id, delta, source, note, ts, batch, expiry)
VALUES (:facility_id, :medicine_id, :delta, :source, :note, :ts, :batch, :expiry)
"""


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #

def facility_variation(facility_id):
    """Persistent per-facility stock level multiplier, same for every medicine."""
    lo, hi = STOCK_VARIATION_RANGE
    v = seeded_rng(facility_id, "stock_variation").lognormvariate(0.0, STOCK_VARIATION_SIGMA)
    return min(max(v, lo), hi)


def daily_consumption(cases_by_disease, per_case_by_disease):
    """Units/day for one medicine at one facility: sum over the diseases that
    use it of (cases/month / 30) x per_case, times the documented scaling factor."""
    raw = sum(cases_by_disease.get(d, 0.0) / DAYS_PER_MONTH * pc for d, pc in per_case_by_disease.items())
    return raw * CONSUMPTION_SCALING_FACTOR


def load_facility_caseloads(conn):
    """{facility_id: {disease: cases_per_month}} for facility-level rows."""
    out = {}
    for r in conn.execute("SELECT facility_id, disease, cases_per_month FROM caseloads "
                          "WHERE facility_id NOT LIKE 'district:%'"):
        out.setdefault(r["facility_id"], {})[r["disease"]] = r["cases_per_month"]
    return out


def baseline_daily_consumption(conn, facilities=None):
    """{facility_id: {medicine_id: units/day}} derived from the caseloads table
    and rules.yaml. This is the baseline burn rate; later phases add outbreak
    surge on top of it (the surge changes this bottom half of days-of-cover,
    never the stock)."""
    facilities = facilities if facilities is not None else demo_phcs(conn)
    per_case = per_case_table(load_rules())
    medicines = [r["medicine_id"] for r in conn.execute("SELECT medicine_id FROM medicines ORDER BY medicine_id")]
    unknown = sorted(set(per_case) - set(medicines))
    if unknown:
        raise SystemExit(f"rules.yaml names medicines not in the medicines table: {unknown}")
    caseloads = load_facility_caseloads(conn)
    return {
        f["facility_id"]: {m: daily_consumption(caseloads.get(f["facility_id"], {}), per_case.get(m, {}))
                           for m in medicines}
        for f in facilities
    }


def lot_remaining_days(facility_id, medicine_id):
    """Days until the seeded lot expires, drawn per (facility, medicine)."""
    cls = MEDICINE_CLASS.get(medicine_id)
    if cls is None:
        raise SystemExit(f"generate_ledger: no shelf-life class for medicine {medicine_id!r} - add it to MEDICINE_CLASS")
    lo, hi = SHELF_LIFE_DAYS_BY_CLASS[cls]
    rng = seeded_rng(facility_id, medicine_id, "expiry")
    if rng.random() < SHORT_DATED_FRACTION:
        return rng.randint(*SHORT_DATED_RANGE_DAYS)
    shelf_life = rng.randint(lo, hi)
    return rng.randint(LONG_DATED_MIN_DAYS, shelf_life)


def batch_number(facility_id, medicine_id, expiry):
    code = re.sub(r"[^a-z0-9]", "", medicine_id)[:3].upper()
    seq = seeded_rng(facility_id, medicine_id, "batch").randint(100, 999)
    return f"{code}{expiry:%y%m}-{seq}"


# --------------------------------------------------------------------------- #
# Plan + write
# --------------------------------------------------------------------------- #

def plan_ledger(conn, as_of):
    """Compute every seed row without touching the database. Returns a dict:
    rows (to insert), skipped (zero-consumption pairs), facility-level info."""
    facilities = demo_phcs(conn)
    medicines = {r["medicine_id"]: dict(r) for r in conn.execute(
        "SELECT medicine_id, name, unit FROM medicines ORDER BY medicine_id")}
    consumption = baseline_daily_consumption(conn, facilities)
    caseloads = load_facility_caseloads(conn)

    rows, skipped, rounded_to_zero = [], [], []
    for f in facilities:
        fid = f["facility_id"]
        var = facility_variation(fid)
        for mid in medicines:
            daily = consumption[fid][mid]
            if daily <= 0:
                skipped.append((fid, mid))
                continue
            expected = daily * PROCUREMENT_CYCLE_DAYS * var
            stock = round(expected)
            if stock == 0:
                rounded_to_zero.append((fid, mid, expected))
                continue
            remaining = lot_remaining_days(fid, mid)
            rows.append({
                "facility_id": fid, "name": f["name"], "sub_district": f["sub_district"],
                "medicine_id": mid, "unit": medicines[mid]["unit"],
                "cases_per_month": caseloads.get(fid, {}),
                "daily": daily, "variation": var, "expected": expected, "stock": stock,
                "remaining_days": remaining, "forced_near_expiry": False,
            })

    # Guarantee: at least NEAR_EXPIRY_MIN_FACILITIES facilities hold a lot
    # expiring within NEAR_EXPIRY_DAYS. If the natural draw falls short, the
    # best-stocked facilities without one get a short-dated ORS lot — the
    # realistic mechanism (overstock -> slow-moving -> expiry) and deterministic.
    near = {r["facility_id"] for r in rows if r["remaining_days"] <= NEAR_EXPIRY_DAYS}
    natural_near = set(near)
    if len(near) < NEAR_EXPIRY_MIN_FACILITIES:
        ors_rows = sorted((r for r in rows if r["medicine_id"] == "ors_packets" and r["facility_id"] not in near),
                          key=lambda r: (-r["variation"], r["facility_id"]))
        for r in ors_rows[:NEAR_EXPIRY_MIN_FACILITIES - len(near)]:
            r["remaining_days"] = seeded_rng(r["facility_id"], r["medicine_id"], "forced_near_expiry").randint(
                10, max(10, NEAR_EXPIRY_DAYS - 2))
            r["forced_near_expiry"] = True
            near.add(r["facility_id"])

    for r in rows:
        r["expiry"] = as_of + timedelta(days=r["remaining_days"])
        r["batch"] = batch_number(r["facility_id"], r["medicine_id"], r["expiry"])

    return {
        "as_of": as_of, "facilities": facilities, "medicines": medicines, "rows": rows,
        "skipped": skipped, "rounded_to_zero": rounded_to_zero,
        "near_expiry_facilities": near, "natural_near_expiry_facilities": natural_near,
    }


def generate(conn, as_of=None, verbose=True):
    """Rebuild every source='seed' row. Idempotent."""
    as_of = as_of or date.today()
    plan = plan_ledger(conn, as_of)
    ts = f"{as_of.isoformat()}T00:00:00"
    with conn:
        deleted = conn.execute("DELETE FROM stock_movements WHERE source = ?", (SOURCE,)).rowcount
        conn.executemany(INSERT, [{
            "facility_id": r["facility_id"], "medicine_id": r["medicine_id"], "delta": r["stock"],
            "source": SOURCE, "ts": ts, "batch": r["batch"], "expiry": r["expiry"].isoformat(),
            "note": (f"opening balance: {r['daily']:.4f}/day x {PROCUREMENT_CYCLE_DAYS}d "
                     f"x variation {r['variation']:.3f} = {r['expected']:.2f}"),
        } for r in plan["rows"]])
    plan["deleted_prior_seed_rows"] = deleted
    if verbose:
        print(f"Ledger: removed {deleted} prior '{SOURCE}' rows, wrote {len(plan['rows'])} "
              f"({len(plan['facilities'])} facilities x {len(plan['medicines'])} medicines, "
              f"{len(plan['skipped'])} pairs with zero derived consumption skipped, "
              f"{len(plan['rounded_to_zero'])} rounded to zero) as of {as_of} | "
              f"seed {LEDGER_SEED}, cycle {PROCUREMENT_CYCLE_DAYS}d, scaling x{CONSUMPTION_SCALING_FACTOR}")
    return plan


def seed_rows(conn):
    """Every seed row as a comparable tuple, for the idempotency check."""
    return [tuple(r) for r in conn.execute(
        "SELECT facility_id, medicine_id, delta, source, note, ts, batch, expiry FROM stock_movements "
        "WHERE source = ? ORDER BY facility_id, medicine_id, batch", (SOURCE,))]


def total_seed_stock(conn):
    return {r["medicine_id"]: r["total"] for r in conn.execute(
        "SELECT medicine_id, SUM(delta) total FROM stock_movements WHERE source = ? GROUP BY medicine_id", (SOURCE,))}


# --------------------------------------------------------------------------- #
# Verification (Phase 5 acceptance, PROJECT_CONTEXT.md §11)
# --------------------------------------------------------------------------- #

def _pearson(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else float("nan")


def _stats_line(values, fmt="{:.2f}"):
    s = caseload_stats(values)
    return (f"min {fmt.format(s['min'])}  median {fmt.format(s['median'])}  "
            f"mean {fmt.format(s['mean'])}  max {fmt.format(s['max'])}")


def verify(conn, plan):
    ok = True
    rows = plan["rows"]
    facilities = plan["facilities"]
    ors = [r for r in rows if r["medicine_id"] == "ors_packets"]
    by_fac = {f["facility_id"]: f for f in facilities}

    print("\n=== 1. Demo facilities ===")
    print(f"  demo PHCs: {len(facilities)}")
    target = by_fac.get(DEMO_TARGET_FACILITY_ID)
    if target:
        t_rows = [r for r in rows if r["facility_id"] == DEMO_TARGET_FACILITY_ID]
        print(f"  OK: {DEMO_TARGET_FACILITY_ID} ({target['name']}, {target['sub_district']}) is included; "
              f"holds {len(t_rows)} medicines: "
              + ", ".join(f"{r['medicine_id']}={r['stock']:g} ({r['stock'] / r['daily']:.1f}d cover)" for r in t_rows))
    else:
        ok = False
        print(f"  FAIL: {DEMO_TARGET_FACILITY_ID} is NOT in the demo set")

    print("\n=== 2. Caseload distribution (cases/month per facility, acute_diarrhoeal_disease) ===")
    cases = {fid: cl.get("acute_diarrhoeal_disease", 0.0) for fid, cl in load_facility_caseloads(conn).items()
             if fid in by_fac}
    print(f"  {len(cases)} facilities: {_stats_line(cases.values())}  sum {sum(cases.values()):.2f}")

    print("\n=== 3. Implied ORS burn rate (packets/day = cases/day x 4 x scaling) ===")
    burn = [r["daily"] for r in ors]
    s = caseload_stats(burn)
    print(f"  scaling factor: x{CONSUMPTION_SCALING_FACTOR}")
    print(f"  {_stats_line(burn, '{:.3f}')}")
    if s["median"] < 2.0:
        raw_median = s["median"] / CONSUMPTION_SCALING_FACTOR
        print(f"  !! DECISION REQUIRED: median PHC burns {s['median']:.3f} ORS packets/day (< 2/day).")
        print(f"     Unscaled HMIS figure: {raw_median:.3f}/day. Bringing the median to 2/day needs "
              f"CONSUMPTION_SCALING_FACTOR = {2.0 / raw_median:.2f}; to 5/day, {5.0 / raw_median:.2f}.")
        print(f"     PROJECT_CONTEXT.md section 8: one stated factor in config, disclosed in the pitch. "
              f"Relative distribution across facilities stays HMIS-derived either way.")
    else:
        print("  OK: median >= 2 packets/day")

    print("\n=== 4. ORS days of cover = stock / daily consumption ===")
    cover = [r["stock"] / r["daily"] for r in ors]
    print(f"  {_stats_line(cover, '{:.1f}')}  (model: {PROCUREMENT_CYCLE_DAYS}d x variation "
          f"{STOCK_VARIATION_RANGE[0]}-{STOCK_VARIATION_RANGE[1]} = "
          f"{PROCUREMENT_CYCLE_DAYS * STOCK_VARIATION_RANGE[0]:.0f}-{PROCUREMENT_CYCLE_DAYS * STOCK_VARIATION_RANGE[1]:.0f}d)")
    buckets = [(0, 7, "<7 critical"), (7, 14, "7-14 warning"), (14, 30, "14-30"), (30, 45, "30-45"),
               (45, 60, "45-60"), (60, float("inf"), ">60")]
    for lo, hi, label in buckets:
        n = sum(1 for c in cover if lo <= c < hi)
        print(f"    {label:<14} {n:>3}  {'#' * n}")
    spread_ok = min(cover) <= 15 and max(cover) >= 50
    print(f"  {'OK' if spread_ok else 'WARN'}: spread {'is wide (min <= 15, max >= 50)' if spread_ok else 'narrower than expected'}")

    print("\n=== 5. Proof of derivation: largest vs smallest caseload PHC ===")
    big = max(ors, key=lambda r: r["cases_per_month"].get("acute_diarrhoeal_disease", 0))
    small = min(ors, key=lambda r: r["cases_per_month"].get("acute_diarrhoeal_disease", 0))
    print(f"  {'facility':<30} {'cases/mo':>9} {'ORS/day':>9} {'x cycle':>9} {'variation':>10} {'= stock':>9} {'cover':>7}")
    for r in (big, small):
        c = r["cases_per_month"]["acute_diarrhoeal_disease"]
        print(f"  {r['name'] + ' (' + r['sub_district'] + ')':<30} {c:>9.2f} {r['daily']:>9.3f} "
              f"{r['daily'] * PROCUREMENT_CYCLE_DAYS:>9.2f} {r['variation']:>10.3f} "
              f"{r['stock']:>9g} {r['stock'] / r['daily']:>6.1f}d")
    cb, cs = big["cases_per_month"]["acute_diarrhoeal_disease"], small["cases_per_month"]["acute_diarrhoeal_disease"]
    case_ratio, var_ratio = cb / cs, big["variation"] / small["variation"]
    stock_ratio = big["stock"] / small["stock"]
    print(f"  caseload ratio {case_ratio:.2f}x  x  variation ratio {var_ratio:.2f}x  = "
          f"{case_ratio * var_ratio:.2f}x  ;  stock ratio {stock_ratio:.2f}x (rounded to whole units)")
    print(f"  identity: ORS stock / variation = cases/month x 4 x {CONSUMPTION_SCALING_FACTOR} "
          f"(x {PROCUREMENT_CYCLE_DAYS}/{DAYS_PER_MONTH}) for every facility; max deviation from the "
          f"unrounded value = {max(abs(r['stock'] - r['expected']) for r in ors):.2f} units (rounding only)")
    xs = [r["cases_per_month"]["acute_diarrhoeal_disease"] for r in ors]
    ys = [r["stock"] for r in ors]
    print(f"  Pearson r(caseload, ORS stock) across {len(ors)} facilities = {_pearson(xs, ys):.3f}")
    if big["stock"] > small["stock"]:
        print("  OK: the largest-caseload PHC holds more ORS than the smallest")
    else:
        ok = False
        print("  FAIL: the smallest-caseload PHC holds at least as much ORS as the largest - generator is random, not derived")
    # Stronger check over the whole set: a facility with >= 5x the caseload of
    # another must hold more ORS (variation alone cannot flip a 5x gap).
    flips = [(a, b) for a in ors for b in ors
             if a["cases_per_month"]["acute_diarrhoeal_disease"] >= 5 * b["cases_per_month"]["acute_diarrhoeal_disease"]
             and a["stock"] <= b["stock"]]
    if flips:
        ok = False
        print(f"  FAIL: {len(flips)} pairs where a >=5x larger caseload holds no more ORS, e.g. "
              f"{flips[0][0]['name']} vs {flips[0][1]['name']}")
    else:
        print("  OK: no pair where a >=5x larger caseload holds less ORS")

    print(f"\n=== 6. Batches expiring within {NEAR_EXPIRY_DAYS} days (as of {plan['as_of']}) ===")
    near_rows = sorted((r for r in rows if r["remaining_days"] <= NEAR_EXPIRY_DAYS),
                       key=lambda r: (r["remaining_days"], r["name"]))
    print(f"  facilities: {len(plan['near_expiry_facilities'])} "
          f"({len(plan['natural_near_expiry_facilities'])} from the natural draw, "
          f"{len(plan['near_expiry_facilities']) - len(plan['natural_near_expiry_facilities'])} topped up to meet "
          f"NEAR_EXPIRY_MIN_FACILITIES = {NEAR_EXPIRY_MIN_FACILITIES})")
    for r in near_rows:
        print(f"    {r['name']:<24} {r['sub_district']:<24} {r['medicine_id']:<14} {r['stock']:>6g} {r['unit']:<8} "
              f"batch {r['batch']:<12} expires {r['expiry']} ({r['remaining_days']:>2}d)"
              f"{'  [topped up]' if r['forced_near_expiry'] else ''}")
    if len(plan["near_expiry_facilities"]) >= NEAR_EXPIRY_MIN_FACILITIES:
        print(f"  OK: >= {NEAR_EXPIRY_MIN_FACILITIES} facilities hold a near-expiry batch")
    else:
        ok = False
        print("  FAIL: near-expiry guarantee not met")
    print(f"  all lots: remaining life {_stats_line([r['remaining_days'] for r in rows], '{:.0f}')} days")

    print("\n=== 7. Medicines with no derived stock ===")
    if plan["skipped"]:
        by_med = {}
        for fid, mid in plan["skipped"]:
            by_med[mid] = by_med.get(mid, 0) + 1
        per_case = per_case_table(load_rules())
        loaded_diseases = {d for cl in load_facility_caseloads(conn).values() for d in cl}
        for mid, n in sorted(by_med.items()):
            if mid not in per_case:
                why = "no disease in rules.yaml uses it"
            else:
                why = (f"its diseases {sorted(per_case[mid])} have no caseload loaded "
                       f"(loaded: {sorted(loaded_diseases)})")
            print(f"  {mid:<18} 0 rows at {n} facilities - {why}")
    else:
        print("  none")
    if plan["rounded_to_zero"]:
        ok = False
        print(f"  FAIL: {len(plan['rounded_to_zero'])} (facility, medicine) pairs with positive consumption derived "
              f"to < 0.5 units and rounded to 0 stock: "
              + ", ".join(f"{by_fac[f]['name']}/{m} ({e:.2f})" for f, m, e in plan["rounded_to_zero"][:6])
              + (" ..." if len(plan["rounded_to_zero"]) > 6 else ""))
    else:
        carried = len(rows)
        print(f"  OK: no facility rounds to zero stock for any medicine it should carry "
              f"({carried} (facility, medicine) pairs with positive consumption, {carried} rows written)")
    return ok


def main():
    with get_connection() as conn:
        create_tables(conn)
        print("--- allocate_caseloads ---")
        allocate(conn)
        print("\n--- generate_ledger (run 1) ---")
        plan = generate(conn)
        rows1, total1 = seed_rows(conn), total_seed_stock(conn)
        ok = verify(conn, plan)

        print("\n=== 8. Idempotency: generate again, compare ===")
        print("--- generate_ledger (run 2) ---")
        generate(conn, as_of=plan["as_of"])
        rows2, total2 = seed_rows(conn), total_seed_stock(conn)
        fmt = lambda t: "  ".join(f"{m}={v:g}" for m, v in sorted(t.items()))  # noqa: E731
        print(f"  run 1 total seed stock: {fmt(total1)}  ({len(rows1)} rows)")
        print(f"  run 2 total seed stock: {fmt(total2)}  ({len(rows2)} rows)")
        n_seed = conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source = ?", (SOURCE,)).fetchone()[0]
        if rows1 == rows2 and total1 == total2 and n_seed == len(rows1):
            print("  OK: identical - every row (facility, medicine, delta, note, ts, batch, expiry) matches, no stacking")
        else:
            ok = False
            print("  FAIL: runs differ or rows stacked")

        print(f"\n{'ALL CHECKS PASSED' if ok else 'CHECKS FAILED'} (decision items above are not failures)")
        return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
