"""Transfers: from a recommendation to ledger movements (PROJECT_CONTEXT.md
sec 2 "DMO approves -> ledger updates -> risk resolves", sec 11 Phase 9).

A recommendation becomes one PENDING row in the transfers table per chosen
donor (from_facility -> to_facility, one medicine, one quantity, the memo
attached). Nothing touches the ledger until a District Medical Officer
approves a row. Approval:

  1. re-runs the donor safety check against the ledger AS IT IS NOW
     (backend.redistribute.assess_donor, surge-inclusive burn rate): if the
     donor could no longer spare the quantity - stock moved since the
     recommendation, a new outbreak - the approval is refused, never applied
     partially;
  2. appends stock_movements rows with source='transfer' at BOTH facilities,
     one pair per lot drawn (earliest-expiring transferable lot first, the
     same split the recommendation showed), carrying batch and expiry so the
     recipient's new lot is tracked to its own expiry;
  3. marks the transfer approved. Nothing is ever mutated or deleted: stock is
     SUM(delta), so the movement log is the whole story.

reset_demo() removes every transfer movement and transfer row so the demo
can be run again from the seeded ledger. It is the one deliberately
destructive function here and is not exposed by the API.
"""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import settings  # noqa: E402
from backend.redistribute import assess_donor, split_lots  # noqa: E402
from backend.risk import all_risk, facility_worst_band  # noqa: E402

log = logging.getLogger(__name__)

SOURCE = "transfer"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_SUPERSEDED = "superseded"


class TransferError(Exception):
    """A transfer cannot be applied. `code` is machine-readable
    (not_found | not_pending | donor_unsafe | no_donor_row)."""

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def row_to_dict(r):
    d = dict(r)
    if d.get("memo"):
        try:
            d["memo"] = json.loads(d["memo"])
        except ValueError:
            pass
    return d


def get_transfer(conn, transfer_id):
    r = conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
    return row_to_dict(r) if r else None


def list_transfers(conn, to_facility=None, medicine_id=None, status=None):
    q, args = "SELECT * FROM transfers WHERE 1=1", []
    if to_facility:
        q, args = q + " AND to_facility = ?", args + [to_facility]
    if medicine_id:
        q, args = q + " AND medicine_id = ?", args + [medicine_id]
    if status:
        q, args = q + " AND status = ?", args + [status]
    return [row_to_dict(r) for r in conn.execute(q + " ORDER BY id", args)]


def supersede_pending(conn, to_facility, medicine_id):
    """A fresh recommendation for the pair replaces any still-pending one."""
    with conn:
        cur = conn.execute("UPDATE transfers SET status = ? WHERE to_facility = ? AND medicine_id = ? AND status = ?",
                           (STATUS_SUPERSEDED, to_facility, medicine_id, STATUS_PENDING))
    return cur.rowcount


def create_pending(conn, result, memo):
    """One pending transfer per donor in the memo's plan (the backend's
    allocation in the order the memo chose). Returns the rows created."""
    recipient = result["recipient"]
    plan = memo.get("plan") or result.get("recommendation") or {}
    donors = plan.get("donors") or []
    if not donors:
        return []
    supersede_pending(conn, recipient["facility_id"], recipient["medicine_id"])
    memo_json = json.dumps({k: memo[k] for k in ("source", "model", "chosen_donors", "rationale_en", "rationale_te",
                                                 "risk_notes", "notable_rejections", "deviates_from_backend_plan")},
                           ensure_ascii=False)
    rows = []
    with conn:
        for d in donors:
            cur = conn.execute(
                "INSERT INTO transfers (from_facility, to_facility, medicine_id, qty, status, memo, approved_by, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (d["facility_id"], recipient["facility_id"], recipient["medicine_id"], d["qty"], STATUS_PENDING,
                 memo_json, _now()))
            rows.append({
                "id": cur.lastrowid, "from_facility": d["facility_id"], "from_name": d["name"],
                "to_facility": recipient["facility_id"], "to_name": recipient["name"],
                "medicine_id": recipient["medicine_id"], "qty": d["qty"], "status": STATUS_PENDING, "order": d["order"],
            })
    return rows


def _risk_for(rows, facility_id, medicine_id):
    r = next((x for x in rows if x["facility_id"] == facility_id and x["medicine_id"] == medicine_id), None)
    if r is None:
        return None
    return {k: r[k] for k in ("facility_id", "name", "medicine_id", "stock", "burn_rate", "outbreak_surge",
                              "days_of_cover", "band")}


def _worst(conn, surge, facility_id):
    return next((w for w in facility_worst_band(conn, surge=surge) if w["facility_id"] == facility_id), None)


def approve(conn, transfer_id, approved_by, today=None, surge=None):
    """Apply one pending transfer. Raises TransferError if it cannot be
    applied; on success returns {transfer, movements, donor, recipient}
    where donor/recipient carry the medicine's risk before and after and
    the facility's worst band after."""
    today = today or date.today()
    t = get_transfer(conn, transfer_id)
    if t is None:
        raise TransferError("not_found", f"transfer {transfer_id} does not exist")
    if t["status"] != STATUS_PENDING:
        raise TransferError("not_pending", f"transfer {transfer_id} is {t['status']}, not pending",
                            {"status": t["status"]})
    if surge is None:
        from backend.outbreak_demand import surge_table  # lazy: pulls in the Gemini client
        surge = surge_table(conn)
    mid, qty = t["medicine_id"], t["qty"]
    rows = all_risk(conn, surge=surge)
    donor_row = next((r for r in rows if r["facility_id"] == t["from_facility"] and r["medicine_id"] == mid), None)
    recip_row = next((r for r in rows if r["facility_id"] == t["to_facility"] and r["medicine_id"] == mid), None)
    if donor_row is None or recip_row is None:
        raise TransferError("no_donor_row", "donor or recipient is not a demo facility")
    unit = conn.execute("SELECT unit FROM medicines WHERE medicine_id = ?", (mid,)).fetchone()["unit"]

    # 1. The donor safety check, again, on the ledger as it stands now.
    recipient = {"lat": recip_row["lat"], "lon": recip_row["lon"]}
    c = assess_donor(conn, recipient, donor_row, today, unit)
    if not c["eligible"] or c["spare_units"] < qty:
        why = c["reason"] if not c["eligible"] else (
            f"can spare only {c['spare_units']} {unit} now, not {qty:g}")
        log.warning("transfer %d REFUSED at approval: %s - %s", transfer_id, donor_row["name"], why)
        raise TransferError("donor_unsafe", f"{donor_row['name']} cannot safely give {qty:g} {unit} now: {why}",
                            {"reason_code": c["reason_code"], "spare_units": c["spare_units"],
                             "days_of_cover": c["days_of_cover"], "floor_days": settings.DONOR_SAFETY_FLOOR_DAYS})
    before = {"donor": _risk_for(rows, t["from_facility"], mid), "recipient": _risk_for(rows, t["to_facility"], mid)}

    # 2. Movements at both facilities, one pair per lot.
    given, _ = split_lots(c["transferable_lots"], qty)
    ts = _now()
    movements = []
    with conn:
        for lot in given:
            for fid, delta, note in (
                    (t["from_facility"], -lot["qty"], f"transfer #{transfer_id}: {lot['qty']:g} {unit} to {recip_row['name']}, "
                                                      f"approved by {approved_by}"),
                    (t["to_facility"], lot["qty"], f"transfer #{transfer_id}: {lot['qty']:g} {unit} from {donor_row['name']}, "
                                                   f"approved by {approved_by}")):
                cur = conn.execute(
                    "INSERT INTO stock_movements (facility_id, medicine_id, delta, source, note, ts, batch, expiry) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (fid, mid, delta, SOURCE, note, ts, lot["batch"], lot["expiry"]))
                movements.append({"id": cur.lastrowid, "facility_id": fid, "medicine_id": mid, "delta": delta,
                                  "source": SOURCE, "note": note, "ts": ts, "batch": lot["batch"], "expiry": lot["expiry"]})
        # 3. Mark approved.
        conn.execute("UPDATE transfers SET status = ?, approved_by = ?, ts = ? WHERE id = ?",
                     (STATUS_APPROVED, approved_by, ts, transfer_id))
    log.info("transfer %d applied: %g %s %s -> %s (%d movements)", transfer_id, qty, unit, donor_row["name"],
             recip_row["name"], len(movements))

    after_rows = all_risk(conn, surge=surge)
    return {
        "transfer": get_transfer(conn, transfer_id),
        "movements": movements,
        "donor": {"before": before["donor"], "after": _risk_for(after_rows, t["from_facility"], mid),
                  "worst_band_after": _worst(conn, surge, t["from_facility"]),
                  "floor_days": settings.DONOR_SAFETY_FLOOR_DAYS},
        "recipient": {"before": before["recipient"], "after": _risk_for(after_rows, t["to_facility"], mid),
                      "worst_band_after": _worst(conn, surge, t["to_facility"])},
    }


def reset_demo(conn):
    """Remove every transfer movement and transfer row (demo reset). Seed
    rows and outbreaks are untouched. Returns what was removed."""
    with conn:
        m = conn.execute("DELETE FROM stock_movements WHERE source = ?", (SOURCE,)).rowcount
        t = conn.execute("DELETE FROM transfers").rowcount
    log.info("demo reset: removed %d transfer movements and %d transfer rows", m, t)
    return {"movements_removed": m, "transfers_removed": t}
