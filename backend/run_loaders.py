"""Run every loader in order and print a verification summary.

    py backend/run_loaders.py          # load + summary
    py backend/run_loaders.py --quiet  # summary only, loaders silent

Safe to run repeatedly — every loader is idempotent.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import LEDGER_DB, TARGET_DISTRICT  # noqa: E402
from backend import load_facilities, load_hmis, load_medicines  # noqa: E402
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
    for r in conn.execute("SELECT * FROM caseloads"):
        print(f"  {dict(r)}")

    print(f"\nJoin check - 5 PHCs alongside the {TARGET_DISTRICT} district diarrhoea caseload:")
    q = """
        SELECT f.name AS facility, f.sub_district, f.state, c.disease, c.cases_per_month, c.source_period
        FROM facilities f
        JOIN caseloads c ON c.facility_id = ?
        WHERE f.type = 'phc' AND f.district = ?
        ORDER BY f.name, f.state
        LIMIT 5
    """
    for r in conn.execute(q, (district_caseload_id(TARGET_DISTRICT), TARGET_DISTRICT)):
        print(f"  {r['facility']:<16} {r['sub_district']:<14} {r['state']:<19} {r['disease']}  "
              f"{r['cases_per_month']:.2f}/month  [{r['source_period']}]")
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
        return print_summary(conn)


if __name__ == "__main__":
    main(verbose="--quiet" not in sys.argv)
