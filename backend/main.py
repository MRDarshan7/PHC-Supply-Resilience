"""FastAPI service (PROJECT_CONTEXT.md sec 10, sec 11 Phase 9).

    py -m uvicorn backend.main:app --reload

Every response is JSON a frontend can render without further computation:
bands, days of cover, quantities and memo text are all computed here.

    GET  /health
    GET  /facilities                    demo facilities with worst-band risk (map colouring)
    GET  /facilities/{id}               per-medicine stock, burn (baseline + surge), cover,
                                        band; batches with expiry; recent movements
    GET  /outbreaks                     stored outbreak records, in-scope vs out-of-scope
    POST /ingest-idsp {filename}        ingest a PDF from data/idsp_pdfs/ (cached, idempotent);
                                        records extracted + facilities that changed band
    GET  /recommend/{facility}/{med}    recipient state, ranked donors with score breakdown,
                                        rejected candidates with reasons, memo, post-transfer
                                        projection; creates pending transfer rows
    POST /transfers/{id}/approve        apply the transfer: movements at BOTH facilities,
                                        updated risk for both

Startup is idempotent: create_tables() and run_loaders() are safe against an
existing database, so a fresh deploy builds its own ledger and a warm
restart re-verifies it. Approved transfers (source='transfer') survive a
restart - only seed rows are regenerated.

The risk picture every endpoint reports uses the live outbreak surge:
every stored outbreak that is in scope and matched to rules.yaml, allocated
by backend.outbreak_demand.surge_table(). One code path, the same numbers
as the command-line reports.
"""

import logging
import sys
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from config import settings  # noqa: E402
from backend import transfers as tx  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.ingest_idsp import FLAG_UNKNOWN_DISEASE, ingest  # noqa: E402
from backend.memo import generate_memo  # noqa: E402
from backend.outbreak_demand import active_outbreaks, allocations, surge_table  # noqa: E402
from backend.queries import demo_phcs  # noqa: E402
from backend.redistribute import lots as stock_lots, recommend  # noqa: E402
from backend.risk import SEVERITY, all_risk  # noqa: E402
from backend.run_loaders import main as run_loaders  # noqa: E402

log = logging.getLogger(__name__)
BANDS = ("critical", "warning", "safe", "unknown")


@asynccontextmanager
async def lifespan(app):
    with get_connection() as conn:
        create_tables(conn)
    run_loaders(verbose=False)
    yield


app = FastAPI(title="PHC Supply Resilience — API", lifespan=lifespan)

# Allow all origins for now. Deployment failure #1 is CORS discovered after
# the frontend is already live; this is set before that can happen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Shared views of the risk picture
# --------------------------------------------------------------------------- #

def _round(x, nd=2):
    return None if x is None else round(x, nd)


def _medicine_names(conn):
    return {r["medicine_id"]: dict(r) for r in conn.execute("SELECT medicine_id, name, unit FROM medicines")}


def _risk_rows(conn):
    """(facilities, surge, rows): the live picture every endpoint uses."""
    facilities = demo_phcs(conn)
    surge = surge_table(conn, facilities, active_outbreaks(conn))
    return facilities, surge, all_risk(conn, surge=surge)


def _facility_view(rows, meds):
    """One entry per facility with its worst (medicine, band) and a compact
    per-medicine map, from all_risk rows."""
    by_fac = {}
    for r in rows:
        f = by_fac.setdefault(r["facility_id"], {
            "id": r["facility_id"], "name": r["name"], "sub_district": r["sub_district"], "lat": r["lat"], "lon": r["lon"],
            "band": "unknown", "worst_medicine_id": None, "worst_medicine_name": None, "days_of_cover": None,
            "outbreak_surge": False, "medicines": {},
        })
        f["medicines"][r["medicine_id"]] = {
            "name": meds[r["medicine_id"]]["name"], "unit": meds[r["medicine_id"]]["unit"],
            "stock": r["stock"], "burn_rate": _round(r["burn_rate"], 4), "outbreak_surge": _round(r["outbreak_surge"], 4),
            "days_of_cover": _round(r["days_of_cover"], 2), "band": r["band"],
        }
        if r["outbreak_surge"] > 0:
            f["outbreak_surge"] = True
        worse = SEVERITY[r["band"]] > SEVERITY[f["band"]] or (
            r["band"] == f["band"] and r["days_of_cover"] is not None
            and (f["days_of_cover"] is None or r["days_of_cover"] < f["days_of_cover"]))
        if worse:
            f.update(band=r["band"], worst_medicine_id=r["medicine_id"], worst_medicine_name=meds[r["medicine_id"]]["name"],
                     days_of_cover=_round(r["days_of_cover"], 2))
    return sorted(by_fac.values(), key=lambda f: (-SEVERITY[f["band"]], f["days_of_cover"] if f["days_of_cover"] is not None else 1e9, f["name"]))


def _band_counts(items):
    counts = {b: 0 for b in BANDS}
    for x in items:
        counts[x["band"]] += 1
    return counts


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/facilities")
def facilities():
    """Demo facilities with worst-band risk for map colouring. A list (the
    Phase 4 frontend reads it as one); each entry carries the band, the
    medicine that produced it and a per-medicine summary."""
    with db() as conn:
        meds = _medicine_names(conn)
        _, _, rows = _risk_rows(conn)
    return _facility_view(rows, meds)


@app.get("/facilities/{facility_id}")
def facility_detail(facility_id: str):
    today = date.today()
    with db() as conn:
        meds = _medicine_names(conn)
        facilities_, surge, rows = _risk_rows(conn)
        mine = [r for r in rows if r["facility_id"] == facility_id]
        if not mine:
            raise HTTPException(status_code=404, detail=f"{facility_id} is not a demo facility")
        view = _facility_view(mine, meds)[0]
        medicines = []
        for r in sorted(mine, key=lambda r: (-SEVERITY[r["band"]], r["medicine_id"])):
            batches = []
            for l in stock_lots(conn, facility_id, r["medicine_id"], today):
                batches.append({
                    "batch": l["batch"], "expiry": l["expiry"], "days_to_expiry": l["days_to_expiry"], "qty": l["qty"],
                    "expired": l["days_to_expiry"] is not None and l["days_to_expiry"] <= 0,
                    "near_expiry": l["days_to_expiry"] is not None and 0 < l["days_to_expiry"] <= settings.NEAR_EXPIRY_DAYS,
                })
            medicines.append({
                "medicine_id": r["medicine_id"], "name": meds[r["medicine_id"]]["name"], "unit": meds[r["medicine_id"]]["unit"],
                "stock": r["stock"], "baseline_burn_rate": _round(r["burn_rate"] - r["outbreak_surge"], 4),
                "outbreak_surge": _round(r["outbreak_surge"], 4), "burn_rate": _round(r["burn_rate"], 4),
                "days_of_cover": _round(r["days_of_cover"], 2), "band": r["band"], "batches": batches,
            })
        movements = [dict(m) for m in conn.execute(
            "SELECT id, medicine_id, delta, source, note, ts, batch, expiry FROM stock_movements "
            "WHERE facility_id = ? ORDER BY ts DESC, id DESC LIMIT 30", (facility_id,))]
        affecting = []
        for a in allocations(conn, facilities_, active_outbreaks(conn)):
            x = a["allocation"].get(facility_id)
            if x and x["cases"] > 0:
                affecting.append({
                    "outbreak_id": a["outbreak_id"], "disease": a["disease"], "district": a["district"],
                    "sub_district": a["sub_district"], "cases": a["cases"], "localised_here": facility_id in a["boosted"],
                    "cases_allocated": _round(x["cases"], 2), "cases_per_day": _round(x["cases_per_day"], 3),
                    "extra_burn": {m: _round(v, 4) for m, v in x["extra_burn"].items()},
                })
        pending = tx.list_transfers(conn, to_facility=facility_id, status=tx.STATUS_PENDING)
    return {
        **{k: view[k] for k in ("id", "name", "sub_district", "lat", "lon", "band", "worst_medicine_id",
                                "worst_medicine_name", "days_of_cover")},
        "thresholds": {"critical_days": settings.CRITICAL_DAYS, "warning_days": settings.WARNING_DAYS,
                       "donor_safety_floor_days": settings.DONOR_SAFETY_FLOOR_DAYS, "near_expiry_days": settings.NEAR_EXPIRY_DAYS},
        "medicines": medicines, "outbreaks_affecting": affecting, "movements": movements,
        "pending_transfers": [{k: t[k] for k in ("id", "from_facility", "medicine_id", "qty", "status", "ts")} for t in pending],
    }


@app.get("/outbreaks")
def outbreaks():
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM outbreaks ORDER BY in_scope DESC, year DESC, week DESC, outbreak_id")]
    out = []
    for r in rows:
        flags = (r["review_flags"] or "").split(",") if r["review_flags"] else []
        out.append({
            **r, "in_scope": bool(r["in_scope"]), "drives_surge": bool(r["in_scope"]) and r["disease_key"] is not None,
            "review_flags": flags,
            "scope_label": "in scope" if r["in_scope"] else "out of scope",
            "disease_label": ("matched to " + r["disease_key"]) if r["disease_key"] else "not in rule table - no surge applied",
        })
    return {
        "count": len(out), "in_scope": sum(1 for o in out if o["in_scope"]),
        "out_of_scope": sum(1 for o in out if not o["in_scope"]),
        "unknown_disease": sum(1 for o in out if FLAG_UNKNOWN_DISEASE in o["review_flags"]),
        "driving_surge": sum(1 for o in out if o["drives_surge"]),
        "outbreaks": out,
    }


class IngestRequest(BaseModel):
    filename: str = Field(..., description="A file in data/idsp_pdfs/, e.g. idsp_2025_w45.pdf")


@app.post("/ingest-idsp")
def ingest_idsp(req: IngestRequest):
    """Ingest one IDSP PDF. Idempotent: the Gemini response is cached by file
    hash and stored rows are replaced per source file, so re-ingesting makes
    zero model calls and creates no duplicates."""
    name = Path(req.filename).name
    if name != req.filename or not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="filename must be a bare .pdf file name from data/idsp_pdfs/")
    path = settings.IDSP_PDF_DIR / name
    if not path.is_file():
        available = sorted(p.name for p in settings.IDSP_PDF_DIR.glob("*.pdf"))
        raise HTTPException(status_code=404, detail={"error": f"{name} not found in data/idsp_pdfs/", "available": available})
    with db() as conn:
        meds = _medicine_names(conn)
        _, _, before_rows = _risk_rows(conn)
        before = {f["id"]: f for f in _facility_view(before_rows, meds)}
        r = ingest(path, conn)
        if not r["ok"]:
            raise HTTPException(status_code=502, detail={"error": r["error"], "source_file": r["source_file"],
                                                         "gemini_calls": r["gemini_calls"]})
        _, _, after_rows = _risk_rows(conn)
        after = {f["id"]: f for f in _facility_view(after_rows, meds)}
    changed = []
    for fid, a in after.items():
        b = before[fid]
        if a["band"] != b["band"]:
            changed.append({"id": fid, "name": a["name"], "sub_district": a["sub_district"],
                            "band_before": b["band"], "band_after": a["band"],
                            "days_of_cover_before": b["days_of_cover"], "days_of_cover_after": a["days_of_cover"],
                            "worst_medicine_id": a["worst_medicine_id"]})
    changed.sort(key=lambda c: (-SEVERITY[c["band_after"]], c["days_of_cover_after"] if c["days_of_cover_after"] is not None else 1e9))
    pair_before = {(x["facility_id"], x["medicine_id"]): x["band"] for x in before_rows}
    pairs_changed = sum(1 for x in after_rows if pair_before.get((x["facility_id"], x["medicine_id"])) != x["band"])
    return {
        "source_file": r["source_file"], "ok": True, "cache_hit": r["cache_hit"], "gemini_calls": r["gemini_calls"],
        "extracted": r["extracted"], "stored": r["stored"], "rejected": [reason for _, reason in r["rejected"]],
        "out_of_scope": r["out_of_scope"], "unknown_disease": r["unknown_disease"],
        "week_year_mismatches": r["week_year_mismatches"],
        "records": [{k: v for k, v in rec.items() if k != "flags"} | {
            "in_scope": bool(rec["in_scope"]), "drives_surge": bool(rec["in_scope"]) and rec["disease_key"] is not None}
            for rec in r["records"]],
        "band_counts_before": _band_counts(before.values()), "band_counts_after": _band_counts(after.values()),
        "facilities_changed_band": changed, "pairs_changed_band": pairs_changed,
    }


def _candidate_view(c):
    keep = ("facility_id", "name", "sub_district", "lat", "lon", "distance_km", "stock", "burn_rate",
            "baseline_burn_rate", "outbreak_surge", "days_of_cover", "band", "spare_units", "spare_nominal",
            "transferable_stock", "eligible", "stage", "reason_code", "reason", "notes", "score", "rank", "lots")
    out = {k: c.get(k) for k in keep}
    out["distance_km"] = _round(c["distance_km"], 1)
    return out


def _donor_view(d):
    keep = ("facility_id", "name", "sub_district", "distance_km", "rank", "order", "qty", "lots_given", "stock_before",
            "stock_after", "cover_before", "cover_after", "band_before", "band_after", "projected_cover_before",
            "projected_cover_after", "floor_check", "score", "summary", "burn_rate", "baseline_burn_rate", "outbreak_surge")
    out = {k: d.get(k) for k in keep}
    out["distance_km"] = _round(d["distance_km"], 1)
    return out


@app.get("/recommend/{facility_id}/{medicine_id}")
def recommend_transfer(facility_id: str, medicine_id: str):
    today = date.today()
    with db() as conn:
        meds = _medicine_names(conn)
        if medicine_id not in meds:
            raise HTTPException(status_code=404, detail=f"unknown medicine {medicine_id}")
        _, surge, rows = _risk_rows(conn)
        if not any(r["facility_id"] == facility_id for r in rows):
            raise HTTPException(status_code=404, detail=f"{facility_id} is not a demo facility")
        result = recommend(conn, facility_id, medicine_id, surge=surge, today=today, risk_rows=rows)
        memo = generate_memo(conn, result, today=today)
        pending = tx.create_pending(conn, result, memo)
    rec = result["recommendation"]
    plan = memo["plan"] or rec
    by_reason = {}
    for c in result["rejected"]:
        by_reason[c["reason_code"]] = by_reason.get(c["reason_code"], 0) + 1
    R = result["recipient"]
    return {
        "status": result["status"],
        "notice": settings.DECISION_SUPPORT_NOTICE,
        "recipient": {**R, "medicine_name": meds[medicine_id]["name"]},
        "parameters": result["parameters"],
        "eligible": [_candidate_view(c) for c in result["eligible"]],
        "rejected": [_candidate_view(c) for c in result["rejected"]],
        "rejected_by_reason": by_reason,
        "counts": {"considered": len(result["candidates"]), "eligible": len(result["eligible"]), "rejected": len(result["rejected"]),
                   "rejected_by_safety_check": by_reason.get("donor_safety_floor", 0)},
        "backend_plan": {**rec, "donors": [_donor_view(d) for d in rec["donors"]]} if rec else None,
        "memo": {k: memo[k] for k in ("source", "model", "gemini_calls", "cache_hit", "close_call", "chosen_donors",
                                      "rationale_en", "rationale_te", "risk_notes", "notable_rejections",
                                      "deviates_from_backend_plan", "validation")},
        "plan": {**plan, "donors": [_donor_view(d) for d in plan["donors"]]} if plan else None,
        "post_transfer": {
            "recipient": {"stock_before": R["stock"], "stock_after": plan["recipient_after"]["stock"],
                          "days_of_cover_before": _round(R["days_of_cover"]), "days_of_cover_after": _round(plan["recipient_after"]["days_of_cover"]),
                          "band_before": R["band"], "band_after": plan["recipient_after"]["band"],
                          "projected_cover_days": _round(plan["recipient_after"]["projected_cover_days"])},
            "donors": [{"facility_id": d["facility_id"], "name": d["name"], "qty": d["qty"],
                        "stock_before": d["stock_before"], "stock_after": d["stock_after"],
                        "days_of_cover_before": _round(d["cover_before"]), "days_of_cover_after": _round(d["cover_after"]),
                        "band_before": d["band_before"], "band_after": d["band_after"],
                        "above_floor_by_days": None if d["cover_after"] is None else _round(d["cover_after"] - settings.DONOR_SAFETY_FLOOR_DAYS, 2),
                        "floor_check_passes": d["floor_check"]["passes"]} for d in plan["donors"]],
            "incoming_lots": plan["incoming_lots"],
        } if plan else None,
        "transfers": pending,
    }


class ApproveRequest(BaseModel):
    approved_by: str = "District Medical Officer"


@app.post("/transfers/{transfer_id}/approve")
def approve_transfer(transfer_id: int, req: ApproveRequest | None = None):
    approved_by = (req.approved_by if req else None) or "District Medical Officer"
    with db() as conn:
        try:
            out = tx.approve(conn, transfer_id, approved_by)
        except tx.TransferError as e:
            status = {"not_found": 404, "not_pending": 409, "donor_unsafe": 409}.get(e.code, 400)
            raise HTTPException(status_code=status, detail={"code": e.code, "error": str(e), **e.detail})
    return {"ok": True, "notice": settings.DECISION_SUPPORT_NOTICE, **out}
