"""Allocate the district-level HMIS caseload across the demo PHCs.

HMIS publishes ONE figure per district (Guntur diarrhoea: 2931 cases over
7 months = 418.71/month, PROJECT_CONTEXT.md §7). It gives no per-facility
breakdown, so the split across facilities is modelled:

    facility_cases = district_cases x weight_f / sum(weights)

where weight_f is a single number per facility drawn once from a fixed
log-normal distribution. The draw is keyed by (LEDGER_SEED, facility_id), so

  * the allocation is deterministic — every run gives the same split;
  * a facility's weight never depends on which other facilities exist —
    a facility that serves more people always serves more people;
  * the facility rows sum exactly to the district figure, which stays in the
    table under its district:<name> sentinel as provenance.

Phase 7 reads facility_share = facility_caseload / district_caseload from the
rows written here.

    py backend/allocate_caseloads.py
"""

import hashlib
import random
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import LEDGER_SEED, TARGET_DISTRICT  # noqa: E402
from backend.db import create_tables, district_caseload_id, get_connection  # noqa: E402
from backend.queries import demo_phcs  # noqa: E402

# Facility weight ~ LogNormal(0, CASELOAD_WEIGHT_SIGMA). Log-normal is the
# standard shape for catchment sizes: most facilities near the typical size,
# a long tail of large ones, none negative. sigma = 0.6 gives roughly a
# 5-8x spread between the 5th and 95th percentile facility and ~15-25x
# between the extremes of an 80-facility draw.
CASELOAD_WEIGHT_SIGMA = 0.6

UPSERT = """
INSERT INTO caseloads (facility_id, disease, cases_per_month, source_period)
VALUES (:facility_id, :disease, :cases_per_month, :source_period)
ON CONFLICT(facility_id, disease) DO UPDATE SET
    cases_per_month = excluded.cases_per_month, source_period = excluded.source_period
"""


def seeded_rng(*parts):
    """A random.Random whose state is a pure function of (LEDGER_SEED, *parts).

    Used for every draw in the generator so each facility's characteristics
    are reproducible on their own, independent of draw order or facility set.
    SHA-256 (not hash()) so the seed is identical across processes and platforms.
    """
    key = "|".join([str(LEDGER_SEED), *(str(p) for p in parts)])
    seed = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    return random.Random(seed)


def facility_weight(facility_id):
    """The facility's persistent caseload weight (relative catchment size)."""
    return seeded_rng(facility_id, "caseload_weight").lognormvariate(0.0, CASELOAD_WEIGHT_SIGMA)


def district_rows(conn, district=TARGET_DISTRICT):
    return [dict(r) for r in conn.execute(
        "SELECT facility_id, disease, cases_per_month, source_period FROM caseloads WHERE facility_id = ?",
        (district_caseload_id(district),),
    )]


def facility_caseloads(conn, disease):
    """{facility_id: cases_per_month} for the facility-level rows of one disease."""
    return {r["facility_id"]: r["cases_per_month"] for r in conn.execute(
        "SELECT facility_id, cases_per_month FROM caseloads WHERE disease = ? AND facility_id NOT LIKE 'district:%'",
        (disease,),
    )}


def allocate(conn, district=TARGET_DISTRICT, verbose=True):
    """Write one caseloads row per demo facility per disease. Idempotent:
    facility-level rows for each allocated disease are rebuilt from scratch,
    so a facility that leaves the demo set loses its row."""
    facilities = demo_phcs(conn)
    if not facilities:
        raise SystemExit("allocate_caseloads: demo_phcs() returned no facilities")
    sources = district_rows(conn, district)
    if not sources:
        raise SystemExit(f"allocate_caseloads: no district caseload row for {district!r} - run load_hmis first")

    weights = {f["facility_id"]: facility_weight(f["facility_id"]) for f in facilities}
    total_weight = sum(weights.values())

    result = {}
    with conn:
        for src in sources:
            conn.execute("DELETE FROM caseloads WHERE disease = ? AND facility_id NOT LIKE 'district:%'",
                         (src["disease"],))
            rows = []
            for f in facilities:
                rows.append({
                    "facility_id": f["facility_id"],
                    "disease": src["disease"],
                    "cases_per_month": src["cases_per_month"] * weights[f["facility_id"]] / total_weight,
                    "source_period": src["source_period"],
                })
            conn.executemany(UPSERT, rows)
            result[src["disease"]] = {
                "district_cases_per_month": src["cases_per_month"],
                "facilities": len(rows),
                "allocated": {r["facility_id"]: r["cases_per_month"] for r in rows},
            }

    if verbose:
        print(f"Allocated {len(sources)} district caseload row(s) across {len(facilities)} demo PHCs "
              f"(seed {LEDGER_SEED}, weight ~ LogNormal(0, {CASELOAD_WEIGHT_SIGMA}))")
        w = sorted(weights.values())
        print(f"  facility weights: min {w[0]:.3f}  median {statistics.median(w):.3f}  max {w[-1]:.3f}  "
              f"(max/min = {w[-1] / w[0]:.1f}x)")
        for disease, r in result.items():
            print_caseload_stats(disease, r)
    return result


def caseload_stats(values):
    values = sorted(values)
    return {
        "n": len(values), "min": values[0], "max": values[-1],
        "mean": statistics.fmean(values), "median": statistics.median(values), "sum": sum(values),
    }


def print_caseload_stats(disease, r):
    s = caseload_stats(r["allocated"].values())
    print(f"  {disease}: district {r['district_cases_per_month']:.2f} cases/month -> {s['n']} facilities")
    print(f"    cases/month per facility: min {s['min']:.2f}  median {s['median']:.2f}  "
          f"mean {s['mean']:.2f}  max {s['max']:.2f}")
    print(f"    sum of facility rows {s['sum']:.4f} == district {r['district_cases_per_month']:.4f}: "
          f"{'OK' if abs(s['sum'] - r['district_cases_per_month']) < 1e-6 else 'MISMATCH'}")


if __name__ == "__main__":
    with get_connection() as conn:
        create_tables(conn)
        allocate(conn)
        print("\ncaseloads table now holds:")
        for r in conn.execute("SELECT CASE WHEN facility_id LIKE 'district:%' THEN 'district' ELSE 'facility' END AS level, "
                              "disease, COUNT(*) n, SUM(cases_per_month) total FROM caseloads GROUP BY level, disease"):
            print(f"  {r['level']:<9} {r['disease']:<26} rows={r['n']:<4} total={r['total']:.2f}/month")
