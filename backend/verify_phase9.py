"""Phase 9 acceptance: the whole chain through the API, no UI
(PROJECT_CONTEXT.md sec 11 Phase 9 manual test).

    py backend/verify_phase9.py            # exit 0 when every check holds

Starts a real uvicorn server on 127.0.0.1:8765, then over HTTP:

    GET  /facilities                         band distribution before ingest
    POST /ingest-idsp idsp_2025_w45.pdf      records extracted, facilities that changed band
    GET  /facilities                         distribution after - changed
    GET  /recommend/<thulluru>/ors_packets   the full response, printed
    POST /transfers/{id}/approve             movements at both facilities
    GET  /facilities/<thulluru>              ORS left the critical band
    GET  /facilities/<donor>                 donor still above the safety floor
    POST /ingest-idsp again                  zero Gemini calls, no duplicate records

and prints the memo in both languages with the hand verification of every
number in it. Then, in-process: a deliberately corrupted Gemini response is
rejected and the template fires; with GEMINI_API_KEY deleted the memo is
still produced. Finally the transfer is reset and backend/test_demo.py runs.

Before the server starts, the outbreaks table and any transfer rows are
cleared so the "before ingest" picture is real. Both are rebuilt by the run
itself (the ingest is served from the on-disk Gemini cache).
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402

from config import settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.memo import corruption_checks, no_key_check, print_hand_check, print_memo, utf8_stdout  # noqa: E402
from backend.redistribute import recommend  # noqa: E402
from backend.transfers import reset_demo  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
BASE = f"http://{HOST}:{PORT}"
TARGET = settings.DEMO_TARGET_FACILITY_ID
MEDICINE = "ors_packets"
PDF = settings.DEMO_IDSP_PDF.name
BANDS = ("critical", "warning", "safe", "unknown")


def band_counts(facilities):
    return {b: sum(1 for f in facilities if f["band"] == b) for b in BANDS}


def fmt_counts(c):
    return "  ".join(f"{b} {c[b]}" for b in BANDS)


def start_server(log_path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=_ROOT, stdout=log_file, stderr=subprocess.STDOUT, env=env)
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            log_file.close()
            raise SystemExit(f"server exited early (code {proc.returncode}); see {log_path}")
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return proc, log_file
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit(f"server did not answer /health within 90 s; see {log_path}")


def main():
    utf8_stdout()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    today = date.today()
    failures = []
    out_dir = Path(os.environ.get("PHASE9_OUT_DIR", _ROOT / ".cache" / "phase9"))  # gitignored
    out_dir.mkdir(parents=True, exist_ok=True)

    def check(cond, msg):
        print(f"  {'OK' if cond else 'FAIL'}: {msg}")
        if not cond:
            failures.append(msg)

    print(f"settings: CRITICAL_DAYS={settings.CRITICAL_DAYS} WARNING_DAYS={settings.WARNING_DAYS} "
          f"DONOR_SAFETY_FLOOR_DAYS={settings.DONOR_SAFETY_FLOOR_DAYS} TARGET_COVER_DAYS={settings.TARGET_COVER_DAYS} "
          f"MEMO_CLOSE_CALL_MARGIN={settings.MEMO_CLOSE_CALL_MARGIN} model={settings.GEMINI_MODEL} today={today}")

    print("\n=== 0. Reset to the pre-outbreak ledger (outbreak rows + transfers cleared; both rebuilt below) ===")
    with get_connection() as conn:
        create_tables(conn)
        r = reset_demo(conn)
        with conn:
            n_ob = conn.execute("DELETE FROM outbreaks").rowcount
        print(f"  removed {r['movements_removed']} transfer movements, {r['transfers_removed']} transfer rows, {n_ob} outbreak rows")
    conn.close()

    proc, log_file = start_server(out_dir / "verify_phase9_server.log")
    print(f"  server up at {BASE} (log: {out_dir / 'verify_phase9_server.log'})")
    client = httpx.Client(base_url=BASE, timeout=300)
    try:
        print("\n=== 1. GET /facilities before ingest ===")
        r = client.get("/facilities")
        before = r.json()
        cb = band_counts(before)
        print(f"  {r.status_code}  {len(before)} facilities  worst band: {fmt_counts(cb)}")
        t0 = next(f for f in before if f["id"] == TARGET)
        print(f"  {t0['name']}: band {t0['band']}, worst medicine {t0['worst_medicine_id']}, {t0['days_of_cover']} d; "
              f"ORS {t0['medicines'][MEDICINE]['days_of_cover']} d ({t0['medicines'][MEDICINE]['band']})")
        check(r.status_code == 200 and cb["critical"] == 0, "no facility is critical before the outbreak is ingested")

        print(f"\n=== 2. POST /ingest-idsp {PDF} ===")
        r = client.post("/ingest-idsp", json={"filename": PDF})
        ing = r.json()
        print(f"  {r.status_code}  ok={ing['ok']} cache_hit={ing['cache_hit']} gemini_calls={ing['gemini_calls']} "
              f"extracted={ing['extracted']} stored={ing['stored']} rejected={len(ing['rejected'])} "
              f"out_of_scope={ing['out_of_scope']} unknown_disease={ing['unknown_disease']}")
        guntur = next((x for x in ing["records"] if x["outbreak_id"] == "AP/GUN/2025/45/1997"), None)
        print(f"  Guntur record: {json.dumps(guntur)}")
        print(f"  bands before: {fmt_counts(ing['band_counts_before'])}   after: {fmt_counts(ing['band_counts_after'])}   "
              f"pairs changed band: {ing['pairs_changed_band']}")
        print(f"  facilities that changed band ({len(ing['facilities_changed_band'])}):")
        for c in ing["facilities_changed_band"][:12]:
            print(f"    {c['name']:<24} {c['sub_district']:<24} {c['band_before']} -> {c['band_after']}  "
                  f"{c['days_of_cover_before']} -> {c['days_of_cover_after']} d ({c['worst_medicine_id']})")
        if len(ing["facilities_changed_band"]) > 12:
            print(f"    ... {len(ing['facilities_changed_band']) - 12} more")
        check(r.status_code == 200 and ing["stored"] == 44, "44 records stored")
        check(guntur is not None and guntur["cases"] == 457 and guntur["drives_surge"], "Guntur / 457 cases drives the surge")
        check(any(c["id"] == TARGET and c["band_after"] == "critical" for c in ing["facilities_changed_band"]),
              f"{TARGET} moved into critical")

        print("\n=== 3. GET /facilities after ingest ===")
        r = client.get("/facilities")
        after = r.json()
        ca = band_counts(after)
        print(f"  {r.status_code}  worst band: {fmt_counts(ca)}   (before: {fmt_counts(cb)})")
        t1 = next(f for f in after if f["id"] == TARGET)
        print(f"  {t1['name']}: band {t1['band']}, ORS {t1['medicines'][MEDICINE]['days_of_cover']} d "
              f"({t1['medicines'][MEDICINE]['band']}), surge {t1['medicines'][MEDICINE]['outbreak_surge']}/day; stock unchanged: "
              f"{t0['medicines'][MEDICINE]['stock']} -> {t1['medicines'][MEDICINE]['stock']}")
        check(ca != cb and ca["critical"] >= 1, "distribution changed and >= 1 facility is critical")
        check(t1["medicines"][MEDICINE]["stock"] == t0["medicines"][MEDICINE]["stock"], "stock did not change - only the burn rate did")

        print("\n=== 3b. GET /outbreaks ===")
        r = client.get("/outbreaks")
        ob = r.json()
        print(f"  {r.status_code}  count {ob['count']}  in_scope {ob['in_scope']}  out_of_scope {ob['out_of_scope']}  "
              f"unknown_disease {ob['unknown_disease']}  driving_surge {ob['driving_surge']}")
        for o in ob["outbreaks"][:3]:
            print(f"    {o['outbreak_id']:<22} {o['district']:<12} {o['disease']:<28} {o['cases']:>5}  {o['scope_label']}, {o['disease_label']}")
        check(r.status_code == 200 and ob["count"] == 44 and ob["driving_surge"] == 1, "44 stored, exactly 1 drives a surge")

        print(f"\n=== 4. GET /recommend/{TARGET}/{MEDICINE} - FULL RESPONSE ===")
        r = client.get(f"/recommend/{TARGET}/{MEDICINE}")
        rec = r.json()
        full = json.dumps(rec, ensure_ascii=False, indent=1)
        (out_dir / "verify_phase9_recommend.json").write_text(full, encoding="utf-8")
        print(full)
        print(f"  ({len(full)} characters; also written to {out_dir / 'verify_phase9_recommend.json'})")
        check(r.status_code == 200 and rec["status"] == "ok", f"status {rec['status']}")
        check(rec["counts"]["eligible"] >= 2 and rec["counts"]["rejected_by_safety_check"] >= 1,
              f"{rec['counts']['eligible']} eligible, {rec['counts']['rejected']} rejected "
              f"({rec['counts']['rejected_by_safety_check']} by the donor safety check) - rejected list is in the response")
        check(all(c["reason_code"] and c["reason"] for c in rec["rejected"]), "every rejected candidate carries a reason")
        check(rec["transfers"] and all(t["status"] == "pending" for t in rec["transfers"]),
              f"pending transfer(s) created: {[(t['id'], t['from_name'], t['qty']) for t in rec['transfers']]}")

        print("\n=== 5. The memo, both languages ===")
        memo = rec["memo"]
        print_memo({**memo, "model": memo["model"]})
        check(memo["source"] in ("gemini", "gemini_cache", "template"), f"memo source: {memo['source']} "
              f"(gemini_calls {memo['gemini_calls']}, cache_hit {memo['cache_hit']})")
        check(bool(memo["rationale_en"].strip()) and bool(memo["rationale_te"].strip()), "both languages present")
        check(sum(d["quantity"] for d in memo["chosen_donors"]) == rec["plan"]["total_qty"] == rec["backend_plan"]["total_qty"],
              f"chosen quantities sum to the backend total {rec['backend_plan']['total_qty']}")
        for a in memo["validation"]["attempts"]:
            print(f"  attempt {a['attempt']} ({a['source']}): {'ok' if a['ok'] else 'REJECTED - ' + '; '.join(a['failures'])}")

        print("\n=== 6. Hand verification: every number in the memo against the input ===")
        check(print_hand_check({"validation": memo["validation"]}), "every numeral and identifier in the memo matches a backend value")

        print("\n=== 7. POST /transfers/{id}/approve ===")
        tid = rec["transfers"][0]["id"]
        donor_id = rec["transfers"][0]["from_facility"]
        r = client.post(f"/transfers/{tid}/approve", json={"approved_by": "District Medical Officer, Guntur"})
        ap = r.json()
        print(f"  {r.status_code}  transfer {tid}: {ap['transfer']['from_facility']} -> {ap['transfer']['to_facility']} "
              f"{ap['transfer']['qty']:g} {MEDICINE}, status {ap['transfer']['status']}, approved_by {ap['transfer']['approved_by']}")
        print("  movements written:")
        for m in ap["movements"]:
            print(f"    #{m['id']} {m['facility_id']:<32} {m['delta']:>+7g}  {m['source']}  lot {m['batch']} exp {m['expiry']}  {m['note']}")
        d, rcp = ap["donor"], ap["recipient"]
        print(f"  donor     {d['before']['name']}: stock {d['before']['stock']:g} -> {d['after']['stock']:g}, cover "
              f"{d['before']['days_of_cover']:.2f} -> {d['after']['days_of_cover']:.2f} d, band {d['before']['band']} -> {d['after']['band']} "
              f"(floor {d['floor_days']} d); facility worst band {d['worst_band_after']['band']}")
        print(f"  recipient {rcp['before']['name']}: stock {rcp['before']['stock']:g} -> {rcp['after']['stock']:g}, cover "
              f"{rcp['before']['days_of_cover']:.2f} -> {rcp['after']['days_of_cover']:.2f} d, band {rcp['before']['band']} -> "
              f"{rcp['after']['band']}; facility worst band {rcp['worst_band_after']['band']} ({rcp['worst_band_after']['worst_medicine_id']})")
        check(r.status_code == 200 and ap["ok"], "approved")
        check(len(ap["movements"]) >= 2 and {m["facility_id"] for m in ap["movements"]} == {donor_id, TARGET}
              and all(m["source"] == "transfer" for m in ap["movements"]),
              "movements written at BOTH facilities with source='transfer'")
        check(abs(sum(m["delta"] for m in ap["movements"])) < 1e-9, "movements net to zero (stock moved, not created)")
        r2 = client.post(f"/transfers/{tid}/approve")
        check(r2.status_code == 409, f"second approval refused ({r2.status_code} {r2.json()['detail']['code']})")

        print(f"\n=== 8. GET /facilities/{TARGET} - left the critical band on ORS ===")
        r = client.get(f"/facilities/{TARGET}")
        det = r.json()
        ors = next(m for m in det["medicines"] if m["medicine_id"] == MEDICINE)
        print(f"  {r.status_code}  {det['name']}: facility worst band {det['band']} ({det['worst_medicine_id']}, {det['days_of_cover']} d)")
        for m in det["medicines"]:
            print(f"    {m['medicine_id']:<18} stock {m['stock']:>7g}  burn {m['burn_rate']:>8}  cover {str(m['days_of_cover']):>7}  {m['band']:<9} "
                  f"lots " + "; ".join(f"{b['batch']} {b['qty']:g} (exp {b['expiry']})" for b in m["batches"]))
        print("  recent movements:")
        for m in det["movements"][:4]:
            print(f"    {m['ts']}  {m['medicine_id']:<14} {m['delta']:>+7g}  {m['source']:<9} {m['note']}")
        check(ors["band"] != "critical" and ors["days_of_cover"] >= settings.TARGET_COVER_DAYS - 1e-6,
              f"ORS: {ors['days_of_cover']} d, band {ors['band']} (was critical)")
        others_crit = [m["medicine_id"] for m in det["medicines"] if m["band"] == "critical"]
        print(f"  NOTE: facility-level band is '{det['band']}' because {others_crit or 'no other medicine'} "
              f"{'are' if len(others_crit) != 1 else 'is'} still critical under the same outbreak - the map colour is the WORST medicine.")

        print(f"\n=== 9. GET /facilities/{donor_id} - donor still above the safety floor ===")
        r = client.get(f"/facilities/{donor_id}")
        dd = r.json()
        dors = next(m for m in dd["medicines"] if m["medicine_id"] == MEDICINE)
        print(f"  {r.status_code}  {dd['name']}: ORS stock {dors['stock']:g}, burn {dors['burn_rate']} "
              f"(baseline {dors['baseline_burn_rate']} + surge {dors['outbreak_surge']}), cover {dors['days_of_cover']} d, band {dors['band']}; "
              f"lots " + "; ".join(f"{b['batch']} {b['qty']:g}" for b in dors["batches"]))
        by_hand = dors["stock"] / dors["burn_rate"]
        print(f"  by hand: {dors['stock']:g} / {dors['burn_rate']} = {by_hand:.3f} d >= floor {settings.DONOR_SAFETY_FLOOR_DAYS}")
        check(dors["days_of_cover"] >= settings.DONOR_SAFETY_FLOOR_DAYS, f"donor keeps {dors['days_of_cover']} d >= {settings.DONOR_SAFETY_FLOOR_DAYS}-day floor")
        check(dd["band"] != "critical", f"donor facility band {dd['band']}")

        print(f"\n=== 10. Extra: clear the other critical medicines at {TARGET} so the facility itself turns green ===")
        for mid in others_crit:
            r = client.get(f"/recommend/{TARGET}/{mid}")
            rr = r.json()
            if rr["status"] != "ok" or not rr["transfers"]:
                print(f"  {mid}: status {rr['status']} - no transfer")
                continue
            parts = []
            for t in rr["transfers"]:
                a = client.post(f"/transfers/{t['id']}/approve", json={"approved_by": "District Medical Officer, Guntur"})
                aj = a.json()
                parts.append(f"{t['from_name']} {t['qty']:g} ({a.status_code}, recipient -> {aj['recipient']['after']['days_of_cover']:.1f} d)")
            print(f"  {mid}: memo {rr['memo']['source']}; " + "; ".join(parts))
        r = client.get(f"/facilities/{TARGET}")
        det2 = r.json()
        print(f"  {det2['name']} now: facility band {det2['band']}; " + ", ".join(
            f"{m['medicine_id']} {m['days_of_cover']} d {m['band']}" for m in det2["medicines"] if m["band"] != "unknown"))
        check(det2["band"] != "critical", f"facility-level band after all transfers: {det2['band']}")

        print(f"\n=== 11. POST /ingest-idsp {PDF} again - idempotent ===")
        n_before = client.get("/outbreaks").json()["count"]
        r = client.post("/ingest-idsp", json={"filename": PDF})
        ing2 = r.json()
        n_after = client.get("/outbreaks").json()["count"]
        print(f"  {r.status_code}  cache_hit={ing2['cache_hit']} gemini_calls={ing2['gemini_calls']} stored={ing2['stored']}  "
              f"outbreak rows {n_before} -> {n_after}  facilities changed band: {len(ing2['facilities_changed_band'])}")
        check(ing2["cache_hit"] and ing2["gemini_calls"] == 0, "zero Gemini calls")
        check(n_before == n_after == 44 and not ing2["facilities_changed_band"], "no duplicate records, no band changes")
    finally:
        client.close()
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
    print("  server stopped")

    print("\n=== 12. Reset the transfers (demo can run again) and rebuild the pre-approval recommendation ===")
    with get_connection() as conn:
        rs = reset_demo(conn)
        print(f"  removed {rs['movements_removed']} transfer movements, {rs['transfers_removed']} transfer rows")
        result = recommend(conn, TARGET, MEDICINE, today=today)
        print(f"  recommend: status {result['status']}, backend plan "
              + ", ".join(f"{d['name']} {d['qty']}" for d in result["recommendation"]["donors"]))
        check(result["status"] == "ok", "pre-approval recommendation restored")

        print("\n=== 13. A deliberately corrupted Gemini response is rejected; the template fallback fires ===")
        base = {"rationale_en": memo["rationale_en"], "rationale_te": memo["rationale_te"], "risk_notes": memo["risk_notes"],
                "chosen_donors": [{"facility_id": d["facility_id"], "quantity": d["quantity"]} for d in memo["chosen_donors"]],
                "notable_rejections": [{"facility_id": x["facility_id"], "reason": x["reason"]} for x in memo["notable_rejections"]]}
        corruption_checks(conn, result, base, today, check)

        print("\n=== 14. Deleting GEMINI_API_KEY still produces a working templated memo ===")
        tmemo = no_key_check(conn, result, today, check)
        print_memo(tmemo)
        print("\n  hand check of the template:")
        check(print_hand_check(tmemo), "template: every numeral matches")
    conn.close()

    print("\n=== 15. py backend/test_demo.py ===")
    p = subprocess.run([sys.executable, str(_ROOT / "backend" / "test_demo.py")], cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    tail = p.stdout.strip().splitlines()[-6:]
    for line in tail:
        print(f"  | {line}")
    check(p.returncode == 0 and "DEMO INTEGRITY OK" in p.stdout, f"test_demo.py exit code {p.returncode}")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'CHECKS FAILED'}"
          + ("" if not failures else ":\n  - " + "\n  - ".join(failures)))
    return not failures


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
