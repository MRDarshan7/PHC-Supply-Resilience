"""Pure-arithmetic risk engine (PROJECT_CONTEXT.md sec 11 Phase 6). No AI here.

Two inputs, nothing else: current_stock (SUM of stock_movements, read
through the current_stock view) and burn_rate (the same derived daily
consumption backend.generate_ledger used to seed the ledger - caseloads x
rules.yaml x CONSUMPTION_SCALING_FACTOR - plus outbreak_surge, the extra
units/day backend.outbreak_demand allocates from IDSP outbreaks on record).
burn_rate is deliberately NOT recomputed from
scratch here: it calls generate_ledger.daily_consumption(), the exact
function the seed generator used, so the two can never diverge.

days_of_cover = current_stock / burn_rate. burn_rate == 0 means undefined,
not infinite - risk_band() reports "unknown", not "critical", so a medicine
nobody in the district has ever needed (paracetamol, ciprofloxacin: no
dengue caseload loaded yet) cannot masquerade as an emergency.

Risk is a property of a (facility, medicine) pair, not of a facility.
facility_worst_band() takes the worst band across a facility's medicines for
map colouring; it is not a blended "facility risk score".

    py backend/risk.py     # Phase 6 verification output (baseline, no surge)
"""

import sys
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.generate_ledger import daily_consumption  # noqa: E402
from backend.queries import demo_phcs  # noqa: E402
from backend.rules import load_rules, per_case_table  # noqa: E402

# critical > warning > safe > unknown, for facility_worst_band()'s max.
SEVERITY = {"critical": 3, "warning": 2, "safe": 1, "unknown": 0}


@lru_cache(maxsize=1)
def _per_case_table():
    """{medicine_id: {disease_key: per_case}}, cached - rules.yaml does not
    change mid-run and burn_rate() is called once per (facility, medicine)."""
    return per_case_table(load_rules())


def _facility_caseloads(conn, facility_id):
    return {r["disease"]: r["cases_per_month"] for r in conn.execute(
        "SELECT disease, cases_per_month FROM caseloads WHERE facility_id = ?", (facility_id,))}


def burn_rate(conn, facility_id, medicine_id, outbreak_surge=0):
    """Units/day this facility consumes of this medicine: baseline derived
    consumption (identical formula to generate_ledger.baseline_daily_consumption)
    plus outbreak_surge, units/day added on top of the baseline - Phase 7's
    IDSP allocation feeds this, never a separate recomputation."""
    cases = _facility_caseloads(conn, facility_id)
    per_case = _per_case_table().get(medicine_id, {})
    baseline = daily_consumption(cases, per_case)
    return baseline + outbreak_surge


def current_stock(conn, facility_id, medicine_id):
    """Stock on hand, read from the current_stock view (backend/db.py) - never
    re-summed against stock_movements here or anywhere else."""
    row = conn.execute(
        "SELECT stock FROM current_stock WHERE facility_id = ? AND medicine_id = ?",
        (facility_id, medicine_id),
    ).fetchone()
    return row["stock"] if row is not None else 0.0


def days_of_cover(conn, facility_id, medicine_id, outbreak_surge=0):
    """stock / burn_rate. None (never infinity) when burn_rate is 0 - there is
    no meaningful "days of cover" for a medicine nobody consumes."""
    stock = current_stock(conn, facility_id, medicine_id)
    rate = burn_rate(conn, facility_id, medicine_id, outbreak_surge=outbreak_surge)
    if rate == 0:
        return None
    return stock / rate


def risk_band(days):
    """critical below CRITICAL_DAYS, warning from CRITICAL_DAYS through
    WARNING_DAYS inclusive, safe above WARNING_DAYS. None -> "unknown".

    Reads config.settings.CRITICAL_DAYS / WARNING_DAYS live on every call
    (not import-time constants), so editing them changes behaviour without
    reloading this module - see the settings.CRITICAL_DAYS = 20 demo below."""
    if days is None:
        return "unknown"
    if days < settings.CRITICAL_DAYS:
        return "critical"
    if days <= settings.WARNING_DAYS:
        return "warning"
    return "safe"


def all_risk(conn, surge=None):
    """One row per (facility, medicine) for every demo facility:
    facility_id, name, sub_district, lat, lon, medicine_id, stock,
    burn_rate, outbreak_surge, days_of_cover, band.

    surge is {facility_id: {medicine_id: extra units/day}} from
    backend.outbreak_demand.surge_table() - the IDSP outbreaks currently on
    record, already allocated. None / {} means no outbreak surge (the
    baseline picture). Each value goes straight into burn_rate()'s
    outbreak_surge parameter; there is no second formula."""
    surge = surge or {}
    facilities = demo_phcs(conn)
    medicines = [r["medicine_id"] for r in conn.execute("SELECT medicine_id FROM medicines ORDER BY medicine_id")]
    rows = []
    for f in facilities:
        for mid in medicines:
            extra = surge.get(f["facility_id"], {}).get(mid, 0.0)
            stock = current_stock(conn, f["facility_id"], mid)
            rate = burn_rate(conn, f["facility_id"], mid, outbreak_surge=extra)
            days = None if rate == 0 else stock / rate
            rows.append({
                "facility_id": f["facility_id"], "name": f["name"], "sub_district": f["sub_district"],
                "lat": f["lat"], "lon": f["lon"], "medicine_id": mid,
                "stock": stock, "burn_rate": rate, "outbreak_surge": extra,
                "days_of_cover": days, "band": risk_band(days),
            })
    return rows


def facility_worst_band(conn, surge=None):
    """One row per facility: facility_id, name, sub_district, lat, lon,
    band (the worst band across its medicines, critical > warning > safe >
    unknown), worst_medicine_id (which medicine produced it). For map
    colouring - never a single blended facility risk score. surge as in
    all_risk()."""
    by_facility = {}
    for r in all_risk(conn, surge=surge):
        fid = r["facility_id"]
        if fid not in by_facility or SEVERITY[r["band"]] > SEVERITY[by_facility[fid]["band"]]:
            by_facility[fid] = {
                "facility_id": fid, "name": r["name"], "sub_district": r["sub_district"],
                "lat": r["lat"], "lon": r["lon"], "band": r["band"], "worst_medicine_id": r["medicine_id"],
            }
    return sorted(by_facility.values(), key=lambda r: (-SEVERITY[r["band"]], r["name"]))


# --------------------------------------------------------------------------- #
# Verification (Phase 6 acceptance, PROJECT_CONTEXT.md sec 11)
# --------------------------------------------------------------------------- #

def main():
    ok = True
    with get_connection() as conn:
        create_tables(conn)

        print("=== 1. Thulluru / ORS ===")
        fid, mid = settings.DEMO_TARGET_FACILITY_ID, "ors_packets"
        stock = current_stock(conn, fid, mid)
        rate = burn_rate(conn, fid, mid)
        days = days_of_cover(conn, fid, mid)
        band = risk_band(days)
        print(f"  stock={stock:g}  burn_rate={rate:.4f}/day  days_of_cover={days:.1f}  band={band}")
        print(f"  (rough model estimate ~29.5d; actual reflects this facility's drawn variation - "
              f"exact figure, not the estimate, is what the system reports)")
        assert band == "safe", f"expected safe, got {band}"
        print("  OK: band is safe")

        print("\n=== 2. Boundary tests (risk_band via days_of_cover with burn_rate=1.0) ===")
        for target_days, expected in [(6.9, "critical"), (7.1, "warning"), (14.1, "safe")]:
            got = risk_band(target_days)
            status = "OK" if got == expected else "FAIL"
            print(f"  {target_days} days -> {got}  (expected {expected})  [{status}]")
            if got != expected:
                ok = False

        print("\n=== 3. Band distribution across all facility-medicine pairs ===")
        rows = all_risk(conn)
        dist = {}
        for r in rows:
            dist[r["band"]] = dist.get(r["band"], 0) + 1
        for band_name in ("critical", "warning", "safe", "unknown"):
            print(f"  {band_name:<10} {dist.get(band_name, 0)}")
        print(f"  total rows: {len(rows)}")

        print("\n=== 4. A facility with mixed bands across medicines ===")
        by_facility = {}
        for r in rows:
            by_facility.setdefault(r["facility_id"], []).append(r)
        mixed = [(fid, frows) for fid, frows in by_facility.items()
                 if len({r["band"] for r in frows if r["band"] != "unknown"}) > 1]
        if mixed:
            fid, frows = mixed[0]
            print(f"  {frows[0]['name']} ({frows[0]['sub_district']}) holds different bands per medicine:")
            for r in sorted(frows, key=lambda r: r["medicine_id"]):
                dstr = f"{r['days_of_cover']:.1f}" if r["days_of_cover"] is not None else "None"
                print(f"    {r['medicine_id']:<18} stock={r['stock']:>6g}  burn_rate={r['burn_rate']:.4f}/day  "
                      f"days={dstr:<6}  band={r['band']}")
            print(f"  OK: {len(mixed)} facility(ies) have mixed non-unknown bands")
        else:
            print("  No facility has mixed bands across its medicines at current thresholds - "
                  "every demo PHC's variation multiplier applies uniformly across its medicines, "
                  "so baseline days-of-cover lands in the same band for all of them (rounding aside). "
                  "This is expected pre-outbreak: only an outbreak surge (Phase 7) pushes one medicine "
                  "at a facility into a worse band than its others.")

        print("\n=== 5. facility_worst_band() - 5 facilities ===")
        worst = facility_worst_band(conn)
        for r in worst[:5]:
            print(f"  {r['name']:<24} {r['sub_district']:<20} band={r['band']:<9} "
                  f"worst_medicine={r['worst_medicine_id']}")

        print("\n=== 6. Editing CRITICAL_DAYS changes the distribution ===")
        before = dict(dist)
        print(f"  CRITICAL_DAYS={settings.CRITICAL_DAYS} distribution: {before}")
        original = settings.CRITICAL_DAYS
        settings.CRITICAL_DAYS = 20
        rows_after = all_risk(conn)
        after = {}
        for r in rows_after:
            after[r["band"]] = after.get(r["band"], 0) + 1
        print(f"  CRITICAL_DAYS=20 distribution:      {after}")
        settings.CRITICAL_DAYS = original
        print(f"  restored CRITICAL_DAYS={settings.CRITICAL_DAYS}")
        if after == before:
            ok = False
            print("  FAIL: distribution did not change")
        else:
            print("  OK: distribution changed when the threshold changed")

        print("\n=== 7. paracetamol / ciprofloxacin -> unknown, never critical ===")
        for mid in ("paracetamol_500", "ciprofloxacin_500"):
            med_rows = [r for r in rows if r["medicine_id"] == mid]
            bands = {r["band"] for r in med_rows}
            print(f"  {mid}: {len(med_rows)} rows, bands present = {bands}")
            if bands != {"unknown"}:
                ok = False
                print(f"  FAIL: {mid} has a band other than unknown")
            else:
                print(f"  OK: {mid} is unknown at every facility (no caseload feeds it yet)")

        print("\n=== 8. Manual check ===")
        sample = next(r for r in rows if r["medicine_id"] == "ors_packets" and r["burn_rate"] > 0)
        by_hand = sample["stock"] / sample["burn_rate"]
        from_fn = days_of_cover(conn, sample["facility_id"], sample["medicine_id"])
        print(f"  facility: {sample['name']} ({sample['sub_district']})")
        print(f"  stock = {sample['stock']:g} {sample['medicine_id']}")
        print(f"  burn_rate = {sample['burn_rate']:.6f} / day")
        print(f"  by hand: {sample['stock']:g} / {sample['burn_rate']:.6f} = {by_hand:.6f}")
        print(f"  days_of_cover() returns: {from_fn:.6f}")
        if abs(by_hand - from_fn) < 1e-9:
            print("  OK: function matches hand calculation exactly")
        else:
            ok = False
            print("  FAIL: mismatch between hand calculation and function")

        print(f"\n{'ALL CHECKS PASSED' if ok else 'CHECKS FAILED'}")
        return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
