"""Demo integrity check: does the ledger + the week-45 IDSP report produce
the risk picture the demo (PROJECT_CONTEXT.md sec 14) depends on?

    py backend/test_demo.py          # exit 0 when every assertion holds

Ingests data/idsp_pdfs/idsp_2025_w45.pdf (cached - no Gemini call after the
first ever run), computes risk with the outbreak surge under the CURRENT
settings, and asserts:

  1. Thulluru / ors_packets days of cover is strictly below CRITICAL_DAYS
     and its band is exactly "critical" - the facility the demo turns red.
     The margin to the threshold is printed: it is the number to watch when
     any of OUTBREAK_LOCAL_BOOST, OUTBREAK_WINDOW_DAYS, CONSUMPTION_SCALING_FACTOR
     or the ledger seed changes.
  2. At least 2 OTHER facilities are at warning or worse on ORS - Phase 8's
     redistribution engine needs a non-trivial risk picture to work against.
  3. At least one facility has ORS cover above DONOR_MIN_COVER_DAYS after the
     surge AND holds an ORS batch expiring within NEAR_EXPIRY_DAYS - the
     near-expiry donor the Phase 9 memo is built around. Reported in full
     either way (candidates that fail one half of the test are listed too),
     never silently.

Every check runs; the verdict is printed per check and the exit code is 1
if any failed.
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.ingest_idsp import ingest  # noqa: E402
from backend.outbreak_demand import active_outbreaks, facility_summary, surge_table  # noqa: E402
from backend.queries import demo_phcs  # noqa: E402
from backend.risk import SEVERITY, all_risk  # noqa: E402

# A near-expiry donor must still have this much ORS cover after the surge.
DONOR_MIN_COVER_DAYS = 25
MIN_OTHER_AT_RISK = 2
MEDICINE = "ors_packets"


def near_expiry_batches(conn, medicine_id, today):
    """{facility_id: [(batch, expiry, qty), ...]} for receipt lines of this
    medicine expiring between today and today + NEAR_EXPIRY_DAYS inclusive.
    Already-expired lots are returned separately so they are never mistaken
    for near-expiry ones."""
    cutoff = (today + timedelta(days=settings.NEAR_EXPIRY_DAYS)).isoformat()
    near, expired = {}, {}
    for r in conn.execute(
            "SELECT facility_id, batch, expiry, delta FROM stock_movements "
            "WHERE medicine_id = ? AND delta > 0 AND expiry IS NOT NULL AND expiry <= ? ORDER BY expiry",
            (medicine_id, cutoff)):
        bucket = near if r["expiry"] >= today.isoformat() else expired
        bucket.setdefault(r["facility_id"], []).append((r["batch"], r["expiry"], r["delta"]))
    return near, expired


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    today = date.today()
    failures = []

    def check(cond, msg):
        print(f"  {'OK' if cond else 'FAIL'}: {msg}")
        if not cond:
            failures.append(msg)

    with get_connection() as conn:
        create_tables(conn)
        print(f"settings: OUTBREAK_LOCAL_BOOST={settings.OUTBREAK_LOCAL_BOOST:g}  "
              f"OUTBREAK_WINDOW_DAYS={settings.OUTBREAK_WINDOW_DAYS}  CRITICAL_DAYS={settings.CRITICAL_DAYS}  "
              f"WARNING_DAYS={settings.WARNING_DAYS}  CONSUMPTION_SCALING_FACTOR={settings.CONSUMPTION_SCALING_FACTOR:g}  "
              f"LEDGER_SEED={settings.LEDGER_SEED}  today={today}")

        r = ingest(settings.DEMO_IDSP_PDF, conn)
        print(f"ingest {r['source_file']}: ok={r['ok']} cache_hit={r['cache_hit']} gemini_calls={r['gemini_calls']} "
              f"stored={r['stored']}")
        check(r["ok"] and r["stored"] == 44, "44 outbreak records on record")

        facilities = demo_phcs(conn)
        active = active_outbreaks(conn)
        check(any(o["outbreak_id"] == "AP/GUN/2025/45/1997" for o in active), "Guntur outbreak is active")
        surge = surge_table(conn, facilities, active)
        after = all_risk(conn, surge=surge)
        ors = {x["facility_id"]: x for x in after if x["medicine_id"] == MEDICINE}
        target = settings.DEMO_TARGET_FACILITY_ID

        print(f"\n=== 1. {ors[target]['name']} / {MEDICINE} is critical ===")
        t = ors[target]
        margin = settings.CRITICAL_DAYS - t["days_of_cover"]
        print(f"  stock {t['stock']:g}  burn {t['burn_rate']:.4f}/day (surge {t['outbreak_surge']:.4f}/day)  "
              f"days of cover {t['days_of_cover']:.3f}  band {t['band']}")
        print(f"  margin: {t['days_of_cover']:.3f} days vs threshold {settings.CRITICAL_DAYS} -> "
              f"{margin:.3f} days ({margin * 24:.1f} hours) below the line")
        check(t["days_of_cover"] < settings.CRITICAL_DAYS,
              f"days of cover {t['days_of_cover']:.3f} < CRITICAL_DAYS {settings.CRITICAL_DAYS}")
        check(t["band"] == "critical", f"band is exactly 'critical' (got {t['band']!r})")

        print(f"\n=== 2. Other facilities at warning or worse on {MEDICINE} ===")
        others = sorted((x for fid, x in ors.items() if fid != target and SEVERITY[x["band"]] >= SEVERITY["warning"]),
                        key=lambda x: x["days_of_cover"])
        for x in others[:10]:
            print(f"  {x['name']:<24} {x['sub_district']:<24} {x['days_of_cover']:>5.1f}d  {x['band']}")
        if len(others) > 10:
            print(f"  ... {len(others) - 10} more")
        check(len(others) >= MIN_OTHER_AT_RISK,
              f"{len(others)} other facilities at warning or worse on {MEDICINE} (need >= {MIN_OTHER_AT_RISK})")

        print(f"\n=== 3. Near-expiry donor: {MEDICINE} cover > {DONOR_MIN_COVER_DAYS}d after surge AND an {MEDICINE} "
              f"batch expiring within {settings.NEAR_EXPIRY_DAYS} days ===")
        near, expired = near_expiry_batches(conn, MEDICINE, today)
        donors, rejected = [], []
        for fid, lots in near.items():
            x = ors.get(fid)
            if x is None:
                continue  # not a demo facility
            (donors if x["days_of_cover"] > DONOR_MIN_COVER_DAYS else rejected).append((x, lots))
        for x, lots in sorted(donors, key=lambda d: -d[0]["days_of_cover"]):
            for batch, expiry, qty in lots:
                print(f"  DONOR    {x['name']:<24} {x['sub_district']:<20} cover {x['days_of_cover']:>5.1f}d ({x['band']})  "
                      f"batch {batch} {qty:g} packets expires {expiry} "
                      f"({(date.fromisoformat(expiry) - today).days}d)")
        for x, lots in sorted(rejected, key=lambda d: -d[0]["days_of_cover"]):
            for batch, expiry, qty in lots:
                print(f"  too low  {x['name']:<24} {x['sub_district']:<20} cover {x['days_of_cover']:>5.1f}d ({x['band']})  "
                      f"batch {batch} expires {expiry} - cover not above {DONOR_MIN_COVER_DAYS}d")
        if expired:
            names = ", ".join(f"{ors[f]['name']} ({lots[0][1]})" for f, lots in expired.items() if f in ors)
            print(f"  WARNING: {len(expired)} {MEDICINE} lot(s) already expired as of {today}: {names} - "
                  f"regenerate the ledger (py backend/run_loaders.py)")
        if not near:
            print(f"  no {MEDICINE} batch expires within {settings.NEAR_EXPIRY_DAYS} days at any facility")
        check(len(donors) >= 1,
              f"{len(donors)} near-expiry {MEDICINE} donor(s) with cover > {DONOR_MIN_COVER_DAYS}d"
              + ("" if donors else f" - NONE: the Phase 9 memo has no near-expiry donor to recommend; "
                                   f"{len(rejected)} facility(ies) hold a near-expiry lot but sit at or below "
                                   f"{DONOR_MIN_COVER_DAYS}d after the surge"))

        print("\n=== Facility-level picture after ingest (worst medicine) ===")
        fac = facility_summary(after)
        counts = {}
        for f in fac.values():
            counts[f["band"]] = counts.get(f["band"], 0) + 1
        print("  " + "  ".join(f"{b} {counts.get(b, 0)}" for b in ("critical", "warning", "safe", "unknown")))

    print(f"\n{'DEMO INTEGRITY OK' if not failures else 'DEMO INTEGRITY FAILED'}"
          + ("" if not failures else ":\n  - " + "\n  - ".join(failures)))
    return not failures


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
