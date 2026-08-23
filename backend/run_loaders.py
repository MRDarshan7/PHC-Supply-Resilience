"""Run every loader in order and print a verification summary.

    py backend/run_loaders.py          # load + summary
    py backend/run_loaders.py --quiet  # summary only, loaders silent

Order: facilities -> medicines -> HMIS district caseload -> allocate the
caseload across demo PHCs -> generate the seeded stock ledger. This is what
backend.main runs at startup, so a fresh deploy builds the whole ledger.db
(gitignored) from the committed data files.

Safe to run repeatedly — every step is idempotent.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import LEDGER_DB, TARGET_DISTRICT  # noqa: E402
from backend import allocate_caseloads, generate_ledger, load_facilities, load_hmis, load_medicines  # noqa: E402
from backend.db import TABLES, create_tables, district_caseload_id, get_connection, table_counts  # noqa: E402


def null_counts(conn, table):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return {c: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {c} IS NULL").fetchone()[0] for c in cols}


def print_summary(conn):
    print("\n=== SUMMARY ===")
    print(f"database: {LEDGER_DB}")

    counts = table_counts(conn)
    print("\nRow counts per table:")
    for t in TABLES:
        print(f"  {t:<16} {counts[t]}")

    print("\nNull counts (columns with nulls; otherwise 'none'):")
    for t in TABLES:
        nulls = {c: n for c, n in null_counts(conn, t).items() if n}
        print(f"  {t:<16} {nulls if nulls else 'none'}")

    print("\nFacility coordinate range:")
    ext = conn.execute("SELECT MIN(lat) mn_lat, MAX(lat) mx_lat, MIN(lon) mn_lon, MAX(lon) mx_lon FROM facilities").fetchone()
    print(f"  lat {ext['mn_lat']:.5f} .. {ext['mx_lat']:.5f} N   lon {ext['mn_lon']:.5f} .. {ext['mx_lon']:.5f} E")
    for label, col, order in (("min lat", "lat", "ASC"), ("max lat", "lat", "DESC"),
                              ("min lon", "lon", "ASC"), ("max lon", "lon", "DESC")):
        r = conn.execute(f"SELECT name, type, state, lat, lon FROM facilities ORDER BY {col} {order} LIMIT 1").fetchone()
        print(f"  {label}: {r['type']} {r['name']!r} ({r['state']}) at {r['lat']}, {r['lon']}")

    print("\nFacilities by type:")
    for r in conn.execute("SELECT type, COUNT(*) n FROM facilities GROUP BY type ORDER BY n DESC"):
        print(f"  {r['type']:<8} {r['n']}")

    print("\nPHC rows by source 'State Name' label (file holds two vintages of the same district):")
    for r in conn.execute("SELECT state, COUNT(*) n, COUNT(DISTINCT lower(name)) distinct_names "
                          "FROM facilities WHERE type='phc' GROUP BY state"):
        print(f"  {r['state']:<20} rows={r['n']:<4} distinct names={r['distinct_names']}")
    r = conn.execute("SELECT COUNT(*) n, COUNT(DISTINCT lower(name)) d FROM facilities WHERE type='phc'").fetchone()
    print(f"  all PHC rows={r['n']}, distinct PHC names across both labels={r['d']}")

    print("\nSub-district values present (facilities.sub_district):")
    subs = [r["sub_district"] for r in conn.execute(
        "SELECT DISTINCT sub_district FROM facilities ORDER BY sub_district")]
    print(f"  {len(subs)} distinct: {subs}")

    print("\nCaseload rows stored:")
    for r in conn.execute("SELECT * FROM caseloads WHERE facility_id LIKE 'district:%'"):
        print(f"  district  {dict(r)}")
    for r in conn.execute("SELECT disease, COUNT(*) n, SUM(cases_per_month) total, MIN(cases_per_month) mn, "
                          "MAX(cases_per_month) mx FROM caseloads WHERE facility_id NOT LIKE 'district:%' GROUP BY disease"):
        print(f"  facility  {r['disease']}: {r['n']} rows, {r['mn']:.2f}..{r['mx']:.2f}/month, sum {r['total']:.2f}/month")

    print(f"\nJoin check - 5 PHCs with their allocated share of the {TARGET_DISTRICT} district diarrhoea caseload:")
    q = """
        SELECT f.name AS facility, f.sub_district, c.disease, c.cases_per_month, d.cases_per_month AS district_cases
        FROM facilities f
        JOIN caseloads c ON c.facility_id = f.facility_id
        JOIN caseloads d ON d.facility_id = ? AND d.disease = c.disease
        WHERE f.type = 'phc' AND f.district = ?
        ORDER BY f.name
        LIMIT 5
    """
    for r in conn.execute(q, (district_caseload_id(TARGET_DISTRICT), TARGET_DISTRICT)):
        print(f"  {r['facility']:<16} {r['sub_district']:<14} {r['disease']}  {r['cases_per_month']:.2f}/month  "
              f"= {100 * r['cases_per_month'] / r['district_cases']:.2f}% of district {r['district_cases']:.2f}")

    print("\nSeeded ledger (source='seed'):")
    for r in conn.execute("SELECT medicine_id, COUNT(*) n, SUM(delta) total, COUNT(DISTINCT facility_id) facs "
                          "FROM stock_movements WHERE source='seed' GROUP BY medicine_id"):
        print(f"  {r['medicine_id']:<18} {r['n']:>4} rows at {r['facs']} facilities, total {r['total']:g}")
    return counts


def main(verbose=True):
    with get_connection() as conn:
        create_tables(conn)
        print("--- load_facilities ---")
        load_facilities.load(conn, verbose=verbose)
        print("--- load_medicines ---")
        load_medicines.load(conn, verbose=verbose)
        print("--- load_hmis ---")
        load_hmis.load(conn, verbose=verbose)
        print("--- allocate_caseloads ---")
        allocate_caseloads.allocate(conn, verbose=verbose)
        print("--- generate_ledger ---")
        generate_ledger.generate(conn, verbose=verbose)
        return print_summary(conn)


if __name__ == "__main__":
    main(verbose="--quiet" not in sys.argv)
