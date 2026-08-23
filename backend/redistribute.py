"""Redistribution engine (PROJECT_CONTEXT.md sec 10, sec 11 Phase 8).

Fully deterministic. No AI in this file. For one (recipient facility,
medicine) pair it answers: which facilities can SAFELY spare stock, how much
each should send, and why every other facility was not chosen.

Every number comes from the same two inputs the risk engine uses - current
stock (SUM of stock_movements) and burn rate (baseline consumption plus the
IDSP outbreak surge), read through backend.risk.all_risk - so the days-of-
cover figures here are the ones on the map, never a second calculation.

Stage 1 - candidate screen (every demo PHC except the recipient):
    holds the medicine; within MAX_TRANSFER_RADIUS_KM (haversine); its own
    days of cover is above DONOR_SAFETY_FLOOR_DAYS; it holds stock that has
    not expired and will not expire within the transit-and-use window
    (TRANSFER_TRANSIT_DAYS + TRANSFER_MIN_USE_DAYS).

Stage 2 - donor safety check, the rule that prevents harm:
    spare = current_stock - donor_burn_rate x DONOR_SAFETY_FLOOR_DAYS
    spare <= 0 -> NOT eligible
    donor_burn_rate INCLUDES the outbreak surge, for the recipient and for
    every candidate alike. The Stage 1 cover test and this rule are the same
    inequality (cover > floor <=> spare > 0), so there is ONE check, made
    once, with one input: the surge-inclusive burn rate from the risk engine.
    A donor admitted on pre-outbreak numbers would be admitted on a fiction.
    A facility that is itself critical or warning fails this check the same
    way a green facility that cannot spare floor days does; the sentence says
    which. Nothing reaches a recommendation without passing it.

Stage 3 - transfer quantity:
    need         = recipient_burn_rate x TARGET_COVER_DAYS - recipient_stock
    transfer_qty = min(need, donor_spare)
    need is rounded UP to whole units and spare DOWN, so the recipient reaches
    the target and a donor never gives a fraction more than its spare. A donor
    gives its earliest-expiring transferable lot first. If no single donor
    covers the need, the remainder comes from the next-ranked donors until
    met or every eligible donor is exhausted.

Stage 4 - scoring (weights in config.settings.SCORE_WEIGHTS, components 0-1):
    sufficiency, proximity, expiry_benefit, donor_comfort - definitions in
    settings and in score_candidates() below.

Stage 5 - return chosen AND rejected candidates. Every rejection carries a
    machine-readable reason code and a sentence written for the District
    Medical Officer. The rejected list is part of the recommendation, not
    debug output: an officer who can see why the nearest facility was
    excluded has reason to trust the one that was chosen.

Expiry-aware guard. Cover everywhere in this project is stock / burn_rate and
that is the figure the safety rule and the hand check use. Lots do expire,
though. fefo_projection() walks a facility's lots earliest-expiry-first at its
burn rate and reports when it really runs out and how much it wastes; a
transfer is capped so the donor's projected cover after giving is never
pushed below min(DONOR_SAFETY_FLOOR_DAYS, its projected cover before). With
one lot per facility (the seeded ledger) the guard never binds; it exists for
a ledger that has already received transfers and holds mixed-dated lots.

    py backend/redistribute.py      # Phase 8 verification report (Thulluru / ORS)
"""

import logging
import math
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.risk import SEVERITY, all_risk, risk_band  # noqa: E402

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
EPS = 1e-9

# Machine-readable rejection codes -> which stage raises them.
REASON_STAGE = {
    "no_stock": 1,
    "out_of_radius": 1,
    "donor_safety_floor": 2,      # the Stage 1 cover test IS the Stage 2 rule; evaluated once
    "stock_expired": 1,
    "stock_expiring_in_window": 1,
    "stock_undated": 1,
    "spare_below_one_unit": 2,
    "expiry_guard": 2,
}


class DonorSafetyError(RuntimeError):
    """A recommendation would leave a donor below DONOR_SAFETY_FLOOR_DAYS.
    By construction this cannot happen; it is re-checked after allocation
    anyway, because a recommendation that starves a donor is the one failure
    this engine exists to prevent."""


# --------------------------------------------------------------------------- #
# Geometry and parameters
# --------------------------------------------------------------------------- #

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance. Straight-line, not road - adequate for ranking."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def transit_and_use_window_days():
    """A lot must expire strictly later than today + this many days to travel."""
    return settings.TRANSFER_TRANSIT_DAYS + settings.TRANSFER_MIN_USE_DAYS


def parameters(today):
    """Snapshot of every setting the engine read, returned with each result so
    a recommendation is reproducible and the memo can cite the thresholds."""
    return {
        "today": today.isoformat(),
        "donor_safety_floor_days": settings.DONOR_SAFETY_FLOOR_DAYS,
        "target_cover_days": settings.TARGET_COVER_DAYS,
        "max_transfer_radius_km": settings.MAX_TRANSFER_RADIUS_KM,
        "transfer_transit_days": settings.TRANSFER_TRANSIT_DAYS,
        "transfer_min_use_days": settings.TRANSFER_MIN_USE_DAYS,
        "transit_and_use_window_days": transit_and_use_window_days(),
        "near_expiry_days": settings.NEAR_EXPIRY_DAYS,
        "score_weights": dict(settings.SCORE_WEIGHTS),
    }


def _cover(stock, burn_rate):
    """stock / burn_rate, or None when burn_rate is 0 (undefined, as in risk.py)."""
    return None if burn_rate <= 0 else stock / burn_rate


def _finite(days):
    """Projected cover for JSON output: inf (burn rate 0) becomes None."""
    return None if days is None or days == math.inf else days


# --------------------------------------------------------------------------- #
# Lots and expiry
# --------------------------------------------------------------------------- #

def _expiry_key(lot):
    d = lot["days_to_expiry"]
    return (d is None, d if d is not None else 0, lot["batch"] or "")


def lots(conn, facility_id, medicine_id, today):
    """Stock on hand broken down by lot, earliest expiry first:
    [{batch, expiry (ISO or None), days_to_expiry (int or None), qty}].

    Receipt lines (delta > 0) define lots by (batch, expiry). Issue lines that
    name a batch draw from that lot; issue lines without one draw first-
    expiry-first-out from what is left. Lots drawn to zero are dropped. The
    quantities sum to the current_stock view for this pair."""
    by_lot, unattributed = {}, 0.0
    for r in conn.execute(
            "SELECT delta, batch, expiry FROM stock_movements "
            "WHERE facility_id = ? AND medicine_id = ? ORDER BY ts, id", (facility_id, medicine_id)):
        key = (r["batch"], r["expiry"])
        if r["delta"] > 0:
            by_lot[key] = by_lot.get(key, 0.0) + r["delta"]
        elif r["batch"] is not None and key in by_lot:
            by_lot[key] += r["delta"]
        else:
            unattributed -= r["delta"]
    out = []
    for (batch, expiry), qty in by_lot.items():
        dte = (date.fromisoformat(expiry) - today).days if expiry else None
        out.append({"batch": batch, "expiry": expiry, "days_to_expiry": dte, "qty": qty})
    out.sort(key=_expiry_key)
    for lot in out:
        if unattributed <= EPS:
            break
        take = min(max(lot["qty"], 0.0), unattributed)
        lot["qty"] -= take
        unattributed -= take
    return [lot for lot in out if lot["qty"] > EPS]


def is_expired(lot):
    """A lot can be dispensed until the day it expires, not on it."""
    return lot["days_to_expiry"] is not None and lot["days_to_expiry"] <= 0


def is_transferable(lot, window_days):
    """Dated, and expires strictly after today + the transit-and-use window."""
    return lot["days_to_expiry"] is not None and lot["days_to_expiry"] > window_days


def split_lots(lots_, qty):
    """(given, retained): take qty earliest-expiry-first. Copies, never mutates."""
    given, retained, remaining = [], [], qty
    for lot in sorted(lots_, key=_expiry_key):
        take = min(lot["qty"], remaining) if remaining > EPS else 0.0
        if take > EPS:
            given.append({**lot, "qty": take})
        if lot["qty"] - take > EPS:
            retained.append({**lot, "qty": lot["qty"] - take})
        remaining -= take
    return given, retained


def fefo_projection(lots_, burn_rate, start_day=0.0):
    """Project dispensing of `lots_` at burn_rate units/day from start_day
    (days after today), earliest-expiry-first. A lot that arrives later
    carries available_from (day number); it pre-empts a later-expiring lot
    the moment it arrives. Units still on the shelf on a lot's expiry day are
    wasted. Undated lots are dispensed last and never waste.

    Returns {cover_days: real days of cover from start_day, stock_out_day,
             consumed, wasted, lots: [{..., consumed, wasted, finished_day}]}.
    burn_rate 0 -> infinite cover, nothing consumed."""
    live = [{**lot, "remaining": lot["qty"], "consumed": 0.0, "wasted": 0.0, "finished_day": None}
            for lot in lots_]
    done = []
    t = float(start_day)
    if burn_rate <= 0:
        return {"cover_days": math.inf, "stock_out_day": math.inf, "consumed": 0.0, "wasted": 0.0, "lots": live}
    while live:
        for lot in live:  # expire what has expired by now
            if lot["days_to_expiry"] is not None and lot["days_to_expiry"] <= t + EPS:
                lot["wasted"] += lot["remaining"]
                lot["remaining"] = 0.0
        done += [lot for lot in live if lot["remaining"] <= EPS]
        live = [lot for lot in live if lot["remaining"] > EPS]
        if not live:
            break
        available = [lot for lot in live if lot.get("available_from", 0.0) <= t + EPS]
        if not available:
            t = min(lot["available_from"] for lot in live)  # wait for the next arrival
            continue
        lot = min(available, key=_expiry_key)
        horizon = lot["remaining"] / burn_rate
        if lot["days_to_expiry"] is not None:
            horizon = min(horizon, lot["days_to_expiry"] - t)
        arrivals = [x["available_from"] - t for x in live if x.get("available_from", 0.0) > t + EPS]
        if arrivals:
            horizon = min(horizon, min(arrivals))
        c = min(lot["remaining"], burn_rate * max(horizon, 0.0))
        lot["remaining"] -= c
        lot["consumed"] += c
        t += c / burn_rate
        if lot["remaining"] <= EPS:
            lot["remaining"] = 0.0
            lot["finished_day"] = t
        elif lot["days_to_expiry"] is not None and lot["days_to_expiry"] <= t + EPS:
            lot["finished_day"] = t  # dispensed until expiry; the rest is wasted at the top of the loop
    detail = sorted(done, key=_expiry_key)
    for lot in detail:
        lot.pop("remaining", None)
    return {
        "cover_days": t - start_day, "stock_out_day": t,
        "consumed": sum(x["consumed"] for x in detail), "wasted": sum(x["wasted"] for x in detail),
        "lots": detail,
    }


# --------------------------------------------------------------------------- #
# Stages 1-2: one candidate
# --------------------------------------------------------------------------- #

def _facility_fields(row):
    return {k: row[k] for k in ("facility_id", "name", "sub_district", "lat", "lon")}


def _fmt_days(d):
    return "n/a" if d is None else ("inf" if d == math.inf else f"{d:.1f}")


def assess_donor(conn, recipient, row, today, unit):
    """Stages 1 and 2 for one candidate facility `row` (an all_risk row for
    the medicine). Returns the candidate dict with eligible / stage /
    reason_code / reason filled in, plus every figure the stages used so the
    verdict can be checked by hand."""
    floor = settings.DONOR_SAFETY_FLOOR_DAYS
    window = transit_and_use_window_days()
    burn = row["burn_rate"]
    surge = row["outbreak_surge"]
    baseline_burn = burn - surge
    stock = row["stock"]
    c = {
        **_facility_fields(row),
        "distance_km": haversine_km(recipient["lat"], recipient["lon"], row["lat"], row["lon"]),
        "stock": stock, "burn_rate": burn, "baseline_burn_rate": baseline_burn, "outbreak_surge": surge,
        "days_of_cover": row["days_of_cover"], "band": row["band"],
        "lots": [], "transferable_lots": [], "held_back_lots": [], "transferable_stock": 0.0,
        "spare_nominal": None, "spare_units": 0,
        "projected_cover_before": None, "projected_wasted_before": 0.0,
        "eligible": False, "stage": None, "reason_code": None, "reason": None, "notes": [],
        "score": None,
    }

    def reject(code, sentence):
        c.update(eligible=False, stage=REASON_STAGE[code], reason_code=code, reason=sentence)
        return c

    # ---- Stage 1 -------------------------------------------------------- #
    if stock <= 0:
        return reject("no_stock", f"Does not hold {unit} of this medicine (stock {stock:g}).")
    if c["distance_km"] > settings.MAX_TRANSFER_RADIUS_KM:
        return reject("out_of_radius",
                      f"{c['distance_km']:.1f} km away, beyond the {settings.MAX_TRANSFER_RADIUS_KM:g} km transfer radius.")

    # ---- Stage 2: the donor safety check (one check, one input) --------- #
    # Evaluated here, at the Stage 1 "own cover above the floor" position,
    # with the surge-inclusive burn rate. cover > floor <=> spare > 0.
    c["spare_nominal"] = stock - burn * floor
    if c["spare_nominal"] <= 0:
        cover = c["days_of_cover"]
        arithmetic = f"spare = {stock:g} - {burn:.2f} x {floor:g} = {c['spare_nominal']:.1f}"
        if SEVERITY[c["band"]] >= SEVERITY["warning"]:
            return reject("donor_safety_floor",
                          f"Is itself {c['band']}: {stock:g} {unit} at {burn:.2f}/day ({baseline_burn:.2f} baseline + "
                          f"{surge:.2f} outbreak surge) is {cover:.1f} days of cover, at or below the {floor:g}-day "
                          f"safety floor ({arithmetic}). It needs stock; it cannot give any.")
        return reject("donor_safety_floor",
                      f"Outbreak surge raises its consumption from {baseline_burn:.2f} to {burn:.2f}/day; "
                      f"{stock:g} {unit} is {cover:.1f} days of cover, not above the {floor:g}-day safety floor "
                      f"({arithmetic}). Giving any stock would create a second shortage.")

    c["lots"] = lots(conn, row["facility_id"], row["medicine_id"], today)
    if abs(sum(l["qty"] for l in c["lots"]) - stock) > 1e-6:
        log.warning("%s/%s: lot quantities (%g) do not sum to current stock (%g)", row["facility_id"],
                    row["medicine_id"], sum(l["qty"] for l in c["lots"]), stock)
    c["transferable_lots"] = [l for l in c["lots"] if is_transferable(l, window)]
    c["held_back_lots"] = [l for l in c["lots"] if not is_transferable(l, window)]
    c["transferable_stock"] = sum(l["qty"] for l in c["transferable_lots"])
    dated = [l for l in c["lots"] if l["days_to_expiry"] is not None]
    if c["transferable_stock"] <= EPS:
        if dated and all(is_expired(l) for l in dated):
            latest = max(dated, key=lambda l: l["days_to_expiry"])
            return reject("stock_expired",
                          f"Its stock ({stock:g} {unit}) expired on {latest['expiry']}; nothing usable to send.")
        if dated:
            latest = max(dated, key=lambda l: l["days_to_expiry"])
            return reject("stock_expiring_in_window",
                          f"All of its stock ({stock:g} {unit}) expires by {latest['expiry']} "
                          f"({latest['days_to_expiry']} days) - within the {window}-day transit-and-use window "
                          f"({settings.TRANSFER_TRANSIT_DAYS} days in transit + {settings.TRANSFER_MIN_USE_DAYS} "
                          f"days minimum dispensing), so it would expire before the recipient could use it.")
        return reject("stock_undated",
                      f"Its stock ({stock:g} {unit}) has no recorded expiry; cannot certify it will survive transit.")
    for l in c["held_back_lots"]:
        if is_expired(l):
            c["notes"].append(f"{l['qty']:g} {unit} in lot {l['batch']} expired {l['expiry']} - excluded.")
        elif l["days_to_expiry"] is None:
            c["notes"].append(f"{l['qty']:g} {unit} in lot {l['batch']} has no expiry - excluded.")
        else:
            c["notes"].append(f"{l['qty']:g} {unit} in lot {l['batch']} expire {l['expiry']} "
                              f"({l['days_to_expiry']} d), inside the {window}-day window - cannot travel.")

    # ---- Stage 3 input: whole units this donor can actually send -------- #
    spare_units = int(math.floor(min(c["spare_nominal"], c["transferable_stock"]) + EPS))
    if spare_units < 1:
        return reject("spare_below_one_unit",
                      f"Passes the safety check with only {min(c['spare_nominal'], c['transferable_stock']):.2f} "
                      f"{unit} to spare - less than one whole unit.")

    # ---- Expiry-aware guard (binds only with mixed-dated lots) ---------- #
    before = fefo_projection(c["lots"], burn)
    c["projected_cover_before"] = _finite(before["cover_days"])
    c["projected_wasted_before"] = before["wasted"]
    guard_floor = min(floor, before["cover_days"])

    def guard_ok(q):
        _, kept = split_lots(c["transferable_lots"], q)
        return fefo_projection(c["held_back_lots"] + kept, burn)["cover_days"] + 1e-6 >= guard_floor

    if not guard_ok(spare_units):
        lo, hi = 0, spare_units  # guard_ok(0) is True by construction; find the largest q that passes
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if guard_ok(mid) else (lo, mid)
        c["notes"].append(f"spare capped from {spare_units} to {lo} {unit}: what it would keep expires before "
                          f"the {floor:g}-day floor is covered (projected cover before {before['cover_days']:.1f} d).")
        spare_units = lo
        if spare_units < 1:
            return reject("expiry_guard",
                          f"Its transferable stock is long-dated but the stock it would keep expires first; "
                          f"giving anything leaves it under {guard_floor:.1f} days of usable cover.")
    c["spare_units"] = spare_units
    c["eligible"] = True
    return c


# --------------------------------------------------------------------------- #
# Stages 3-4: quantity and score
# --------------------------------------------------------------------------- #

def recipient_projection(recipient, incoming_lots):
    """FEFO projection at the recipient with `incoming_lots` arriving after
    TRANSFER_TRANSIT_DAYS; the recipient dispenses its own stock meanwhile."""
    incoming = [{**l, "available_from": float(settings.TRANSFER_TRANSIT_DAYS), "incoming": True} for l in incoming_lots]
    return fefo_projection(recipient["lots"] + incoming, recipient["burn_rate"])


def rescued_units(candidate, given, recipient):
    """Units of `given` (lots leaving this donor) that would have expired
    unused at the donor AND that the recipient will dispense before expiry."""
    if not given:
        return 0.0
    waste_at_donor = {(l["batch"], l["expiry"]): l["wasted"]
                      for l in fefo_projection(candidate["lots"], candidate["burn_rate"])["lots"]}
    proj = recipient_projection(recipient, given)
    used_in_time = {(l["batch"], l["expiry"]): l["consumed"] for l in proj["lots"] if l.get("incoming")}
    return sum(min(l["qty"], waste_at_donor.get((l["batch"], l["expiry"]), 0.0),
                   used_in_time.get((l["batch"], l["expiry"]), 0.0)) for l in given)


def score_candidates(eligible, recipient):
    """Stage 4. Fills candidate["score"] = {total, components, ...} for every
    eligible candidate, each scored as if it were the only donor.

    sufficiency    = min(need, spare) / need
    proximity      = 1 - distance / MAX_TRANSFER_RADIUS_KM, clamped 0-1 (absolute,
                     not relative to the candidate set)
    expiry_benefit = rescued_units / need, capped at 1
    donor_comfort  = (cover after giving - floor) / floor, clamped 0-1
    total          = sum of weight x component (weights sum to 1)"""
    if not eligible:
        return
    w = settings.SCORE_WEIGHTS
    floor = settings.DONOR_SAFETY_FLOOR_DAYS
    need = recipient["need_units"]
    radius = settings.MAX_TRANSFER_RADIUS_KM
    for c in eligible:
        qty = min(need, c["spare_units"])
        given, _ = split_lots(c["transferable_lots"], qty)
        rescued = rescued_units(c, given, recipient)
        cover_after = _cover(c["stock"] - qty, c["burn_rate"])
        comp = {
            "sufficiency": qty / need,
            "proximity": max(0.0, min(1.0, 1.0 - c["distance_km"] / radius)),
            "expiry_benefit": min(1.0, rescued / need),
            "donor_comfort": 1.0 if cover_after is None else max(0.0, min(1.0, (cover_after - floor) / floor)),
        }
        c["score"] = {
            "total": sum(w[k] * comp[k] for k in w),
            "components": comp,
            "weighted": {k: w[k] * comp[k] for k in w},
            "qty_alone": qty, "rescued_units": rescued, "cover_after_alone": cover_after,
        }


def rank(eligible):
    """Highest score first; ties broken by distance, then facility_id."""
    return sorted(eligible, key=lambda c: (-c["score"]["total"], c["distance_km"], c["facility_id"]))


def _chosen_summary(d, n_eligible, unit):
    comp = d["score"]["components"]
    floor = settings.DONOR_SAFETY_FLOOR_DAYS
    margin = "n/a" if d["cover_after"] is None else f"{d['cover_after'] - floor:.1f}"
    rescued = d["score"]["rescued_units"]
    tail = f"; {rescued:g} {unit} of it would otherwise have expired unused." if rescued > EPS else "."
    return (f"Ranked {d['rank']} of {n_eligible} eligible (score {d['score']['total']:.3f}): sends {d['qty']:g} {unit} "
            f"({comp['sufficiency']:.0%} of the need on its own), {d['distance_km']:.1f} km away, keeps "
            f"{_fmt_days(d['cover_after'])} days of cover after giving ({margin} above the {floor:g}-day floor)" + tail)


# --------------------------------------------------------------------------- #
# Stage 3: allocation over an ordered donor list
# --------------------------------------------------------------------------- #

def allocate(recipient, ordered, unit, n_eligible=None):
    """Stage 3 over `ordered` (eligible candidates, in the order they are to be
    drawn on): each sends min(remaining need, its spare_units) until the need
    is met or the list is exhausted. Returns (donors, remaining_units).

    recommend() passes the scorer's ranking. backend/memo.py passes the same
    candidates in the order Gemini chose among close-scoring donors - the
    quantities still come from here, never from the model. Every donor is
    re-checked against DONOR_SAFETY_FLOOR_DAYS after its quantity is fixed;
    a failure raises DonorSafetyError rather than returning a plan."""
    floor = settings.DONOR_SAFETY_FLOOR_DAYS
    n_eligible = len(ordered) if n_eligible is None else n_eligible
    remaining, donors = recipient["need_units"], []
    for i, c in enumerate(ordered):
        if remaining <= 0:
            break
        qty = min(remaining, c["spare_units"])
        if qty < 1:
            continue
        given, kept = split_lots(c["transferable_lots"], qty)
        retained = c["held_back_lots"] + kept
        stock_after = c["stock"] - qty
        cover_after = _cover(stock_after, c["burn_rate"])
        d = {
            **{k: c[k] for k in ("facility_id", "name", "sub_district", "lat", "lon", "distance_km", "burn_rate",
                                 "baseline_burn_rate", "outbreak_surge", "score", "spare_units", "spare_nominal")},
            "rank": c.get("rank", i + 1), "order": i + 1, "qty": qty, "lots_given": given, "lots_retained": retained,
            "stock_before": c["stock"], "stock_after": stock_after,
            "cover_before": c["days_of_cover"], "cover_after": cover_after,
            "band_before": c["band"], "band_after": risk_band(cover_after),
            "projected_cover_before": c["projected_cover_before"],
            "projected_cover_after": _finite(fefo_projection(retained, c["burn_rate"])["cover_days"]),
            "floor_check": {
                "stock_after": stock_after, "burn_rate": c["burn_rate"], "floor_days": floor,
                "cover_after": cover_after, "passes": cover_after is None or cover_after + 1e-9 >= floor,
            },
        }
        if not d["floor_check"]["passes"]:
            raise DonorSafetyError(
                f"{c['name']} would be left with {cover_after:.3f} days of cover after sending {qty:g} {unit} "
                f"({stock_after:g} / {c['burn_rate']:.4f}), below the {floor:g}-day floor")
        d["summary"] = _chosen_summary(d, n_eligible, unit)
        donors.append(d)
        remaining -= qty
    return donors, remaining


def build_plan(recipient, donors, remaining):
    """The recommendation dict for an allocation: donors, totals, the
    recipient's nominal and FEFO-projected state after every lot arrives."""
    total_qty = recipient["need_units"] - remaining
    stock_after = recipient["stock"] + total_qty
    incoming = [{**l, "from_facility_id": d["facility_id"], "from_name": d["name"]} for d in donors for l in d["lots_given"]]
    proj = recipient_projection(recipient, incoming)
    incoming_detail = []
    for l in proj["lots"]:
        if not l.get("incoming"):
            continue
        incoming_detail.append({
            "from_facility_id": l["from_facility_id"], "from_name": l["from_name"], "batch": l["batch"],
            "expiry": l["expiry"], "days_to_expiry": l["days_to_expiry"], "qty": l["qty"],
            "near_expiry": l["days_to_expiry"] is not None and l["days_to_expiry"] <= settings.NEAR_EXPIRY_DAYS,
            "consumed_by_day": l["finished_day"], "consumed_in_time": l["wasted"] <= EPS,
            "wasted": l["wasted"],
        })
    return {
        "donors": donors, "total_qty": total_qty, "need_units": recipient["need_units"], "shortfall": remaining,
        "met": remaining <= 0, "split": len(donors) > 1,
        "recipient_after": {
            "stock": stock_after, "days_of_cover": _cover(stock_after, recipient["burn_rate"]),
            "band": risk_band(_cover(stock_after, recipient["burn_rate"])),
            "projected_cover_days": _finite(proj["cover_days"]),
        },
        "incoming_lots": incoming_detail,
    }


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #

def _live_surge(conn):
    from backend.outbreak_demand import surge_table  # lazy: pulls in the Gemini client
    return surge_table(conn)


def recommend(conn, recipient_id, medicine_id, surge=None, today=None, risk_rows=None):
    """Run all five stages for (recipient_id, medicine_id).

    surge: {facility_id: {medicine_id: extra units/day}} from
    backend.outbreak_demand.surge_table(); None = the live table (every
    active outbreak on record); {} = baseline, no outbreak.
    risk_rows: a precomputed all_risk(conn, surge=surge) to avoid recomputing
    it when running the engine for many pairs.

    Returns a JSON-serialisable dict: recipient, parameters, candidates (all
    considered), eligible (ranked), rejected, recommendation, status.
    status: ok | partial | no_eligible_donors | no_need | no_burn_rate."""
    today = today or date.today()
    if surge is None:
        surge = _live_surge(conn)
    rows = risk_rows if risk_rows is not None else all_risk(conn, surge=surge)
    med_rows = [r for r in rows if r["medicine_id"] == medicine_id]
    by_id = {r["facility_id"]: r for r in med_rows}
    if recipient_id not in by_id:
        raise ValueError(f"{recipient_id} is not a demo facility or {medicine_id} is not a medicine")
    unit = conn.execute("SELECT unit FROM medicines WHERE medicine_id = ?", (medicine_id,)).fetchone()
    unit = unit["unit"] if unit else "units"
    row = by_id[recipient_id]
    target = settings.TARGET_COVER_DAYS
    need_raw = row["burn_rate"] * target - row["stock"]
    recipient = {
        **_facility_fields(row), "medicine_id": medicine_id, "unit": unit,
        "stock": row["stock"], "burn_rate": row["burn_rate"], "baseline_burn_rate": row["burn_rate"] - row["outbreak_surge"],
        "outbreak_surge": row["outbreak_surge"], "days_of_cover": row["days_of_cover"], "band": row["band"],
        "target_cover_days": target, "need": need_raw,
        "need_units": int(math.ceil(round(need_raw, 6))) if need_raw > 0 else 0,
        "lots": lots(conn, recipient_id, medicine_id, today),
    }
    result = {"recipient": recipient, "parameters": parameters(today), "candidates": [], "eligible": [],
              "rejected": [], "recommendation": None, "status": None}
    if row["burn_rate"] <= 0:
        result["status"] = "no_burn_rate"
        return result
    if recipient["need_units"] <= 0:
        result["status"] = "no_need"
        return result

    candidates = [assess_donor(conn, recipient, r, today, unit) for r in med_rows if r["facility_id"] != recipient_id]
    candidates.sort(key=lambda c: (c["distance_km"], c["facility_id"]))
    eligible = [c for c in candidates if c["eligible"]]
    score_candidates(eligible, recipient)
    ranked = rank(eligible)
    for i, c in enumerate(ranked):
        c["rank"] = i + 1  # the scorer's rank, kept on the candidate whatever order it is later allocated in
    result.update(candidates=candidates, eligible=ranked, rejected=[c for c in candidates if not c["eligible"]])

    # ---- Stage 3 allocation, in rank order ------------------------------ #
    donors, remaining = allocate(recipient, ranked, unit)
    result["recommendation"] = build_plan(recipient, donors, remaining)
    result["status"] = "ok" if remaining <= 0 else ("partial" if donors else "no_eligible_donors")
    return result


# --------------------------------------------------------------------------- #
# Verification (Phase 8 acceptance, PROJECT_CONTEXT.md sec 11)
# --------------------------------------------------------------------------- #

def _print_candidates(res):
    unit = res["recipient"]["unit"]
    print(f"  {'facility':<25} {'sub_district':<20} {'km':>5} {'stock':>6} {'burn':>7} {'surge':>6} {'cover':>6} "
          f"{'spare':>7} {'elig':<4} reason")
    print(f"  {'':<25} {'':<20} {'':>5} {'':>6} {'/day':>7} {'/day':>6} {'':>6} {'':>7} {'':<4} "
          f"(burn includes the surge; cover = stock / burn; spare = stock - burn x floor)")
    for c in res["candidates"]:
        spare = "" if c["spare_nominal"] is None else f"{c['spare_nominal']:.1f}"
        code = "" if c["eligible"] else f"[S{c['stage']}] {c['reason_code']}"
        print(f"  {c['name']:<25} {c['sub_district']:<20} {c['distance_km']:>5.1f} {c['stock']:>6g} {c['burn_rate']:>7.3f} "
              f"{c['outbreak_surge']:>6.2f} {_fmt_days(c['days_of_cover']):>6} {spare:>7} "
              f"{'yes' if c['eligible'] else 'no':<4} {code}")
        if not c["eligible"]:
            print(f"  {'':<25} {'':<20} {'':>47} -> {c['reason']}")
        for n in c["notes"]:
            print(f"  {'':<25} {'':<20} {'':>47} note: {n}")
    counts = {}
    for c in res["rejected"]:
        counts[c["reason_code"]] = counts.get(c["reason_code"], 0) + 1
    print(f"  considered {len(res['candidates'])}: eligible {len(res['eligible'])}, rejected {len(res['rejected'])} "
          + "(" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (REASON_STAGE[kv[0]], kv[0]))) + ")")
    return counts


def _print_ranked(res, top=10):
    unit = res["recipient"]["unit"]
    w = settings.SCORE_WEIGHTS
    print(f"  weights: " + ", ".join(f"{k} {v:g}" for k, v in w.items()))
    print(f"  {'#':>2} {'facility':<25} {'km':>5} {'spare':>6} {'alone':>6} {'score':>6} | "
          + " ".join(f"{k[:10]:>10}" for k in w) + "  (component x weight)")
    for i, c in enumerate(res["eligible"][:top]):
        s = c["score"]
        print(f"  {i + 1:>2} {c['name']:<25} {c['distance_km']:>5.1f} {c['spare_units']:>6} {s['qty_alone']:>6} "
              f"{s['total']:>6.3f} | "
              + " ".join(f"{s['components'][k]:.2f}x{w[k]:.2f}" for k in w))
    if len(res["eligible"]) > top:
        print(f"  ... {len(res['eligible']) - top} more eligible")


def _print_plan(res):
    r, rec, unit = res["recipient"], res["recommendation"], res["recipient"]["unit"]
    floor = settings.DONOR_SAFETY_FLOOR_DAYS
    print(f"  status: {res['status']}  need {rec['need_units']} {unit}  allocated {rec['total_qty']}  "
          f"shortfall {rec['shortfall']}  split across {len(rec['donors'])} donor(s)")
    for d in rec["donors"]:
        s = d["score"]
        print(f"  #{d['rank']} {d['name']} ({d['sub_district']}) - send {d['qty']:g} {unit}, {d['distance_km']:.1f} km, "
              f"score {s['total']:.3f}")
        print(f"       components: " + ", ".join(f"{k} {s['components'][k]:.3f} x {settings.SCORE_WEIGHTS[k]:g} = "
                                                f"{s['weighted'][k]:.3f}" for k in settings.SCORE_WEIGHTS))
        print(f"       lots given: " + "; ".join(f"{l['qty']:g} from {l['batch']} (expires {l['expiry']}, "
                                                f"{l['days_to_expiry']} d)" for l in d["lots_given"]))
        print(f"       {d['summary']}")
    print(f"\n  post-transfer state (nominal cover = stock / burn, the figure on the map; projected = FEFO with expiry):")
    print(f"  {'party':<28} {'role':<9} {'stock before':>12} {'stock after':>11} {'cover before':>12} {'cover after':>11} "
          f"{'band':>18} {'projected':>16}")
    ra = rec["recipient_after"]
    print(f"  {r['name']:<28} {'recipient':<9} {r['stock']:>12g} {ra['stock']:>11g} {_fmt_days(r['days_of_cover']):>12} "
          f"{_fmt_days(ra['days_of_cover']):>11} {r['band'] + ' -> ' + ra['band']:>18} "
          f"{_fmt_days(ra['projected_cover_days']):>16}")
    for d in rec["donors"]:
        print(f"  {d['name']:<28} {'donor #' + str(d['rank']):<9} {d['stock_before']:>12g} {d['stock_after']:>11g} "
              f"{_fmt_days(d['cover_before']):>12} {_fmt_days(d['cover_after']):>11} "
              f"{d['band_before'] + ' -> ' + d['band_after']:>18} "
              f"{_fmt_days(d['projected_cover_before']) + ' -> ' + _fmt_days(d['projected_cover_after']):>16}")


def _print_hand_check(d, unit):
    f = d["floor_check"]
    print(f"  top donor: {d['name']}")
    print(f"    stock after   = {d['stock_before']:g} - {d['qty']:g} = {f['stock_after']:g} {unit}")
    print(f"    burn rate     = {d['baseline_burn_rate']:.4f} baseline + {d['outbreak_surge']:.4f} surge = {f['burn_rate']:.4f} {unit}/day")
    print(f"    cover after   = {f['stock_after']:g} / {f['burn_rate']:.4f} = {f['cover_after']:.4f} days")
    print(f"    safety floor  = {f['floor_days']:g} days")
    print(f"    {f['cover_after']:.4f} >= {f['floor_days']:g} ? {'YES - donor stays above the floor' if f['passes'] else 'NO - CRITICAL FAILURE'}")
    print(f"    spare by hand = {d['stock_before']:g} - {f['burn_rate']:.4f} x {f['floor_days']:g} = "
          f"{d['stock_before'] - f['burn_rate'] * f['floor_days']:.4f}; engine spare_nominal {d['spare_nominal']:.4f}, "
          f"whole units {d['spare_units']}, sent {d['qty']:g} (<= spare)")
    return f["passes"]


def _eligibility_at_floor(conn, rid, mid, surge, today, rows, floor):
    original = settings.DONOR_SAFETY_FLOOR_DAYS
    settings.DONOR_SAFETY_FLOOR_DAYS = floor
    try:
        return recommend(conn, rid, mid, surge=surge, today=today, risk_rows=rows)
    finally:
        settings.DONOR_SAFETY_FLOOR_DAYS = original


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from backend.ingest_idsp import ingest
    from backend.outbreak_demand import active_outbreaks, surge_table
    from backend.queries import demo_phcs

    today = date.today()
    failures = []

    def check(cond, msg):
        print(f"  {'OK' if cond else 'FAIL'}: {msg}")
        if not cond:
            failures.append(msg)

    with get_connection() as conn:
        create_tables(conn)
        p = parameters(today)
        print(f"settings: CRITICAL_DAYS={settings.CRITICAL_DAYS} WARNING_DAYS={settings.WARNING_DAYS} "
              f"DONOR_SAFETY_FLOOR_DAYS={p['donor_safety_floor_days']} TARGET_COVER_DAYS={p['target_cover_days']} "
              f"MAX_TRANSFER_RADIUS_KM={p['max_transfer_radius_km']} window={p['transit_and_use_window_days']}d "
              f"({p['transfer_transit_days']} transit + {p['transfer_min_use_days']} use) "
              f"NEAR_EXPIRY_DAYS={p['near_expiry_days']} today={today}")
        r = ingest(settings.DEMO_IDSP_PDF, conn)
        print(f"ingest {r['source_file']}: ok={r['ok']} cache_hit={r['cache_hit']} gemini_calls={r['gemini_calls']} stored={r['stored']}")
        facilities = demo_phcs(conn)
        surge = surge_table(conn, facilities, active_outbreaks(conn))
        rows = all_risk(conn, surge=surge)
        rid, mid = settings.DEMO_TARGET_FACILITY_ID, "ors_packets"

        res = recommend(conn, rid, mid, surge=surge, today=today, risk_rows=rows)
        rec, unit = res["recipient"], res["recipient"]["unit"]

        print(f"\n=== 1. Recipient: {rec['name']} ({rec['sub_district']}) / {mid} ===")
        print(f"  stock {rec['stock']:g} {unit}   burn {rec['burn_rate']:.4f}/day ({rec['baseline_burn_rate']:.4f} baseline "
              f"+ {rec['outbreak_surge']:.4f} surge)   days of cover {rec['days_of_cover']:.3f}   band {rec['band']}")
        print(f"  need = {rec['burn_rate']:.4f} x {rec['target_cover_days']} - {rec['stock']:g} = {rec['need']:.2f} "
              f"-> {rec['need_units']} {unit} (rounded up to whole units)")
        print(f"  own lots: " + "; ".join(f"{l['qty']:g} in {l['batch']} expires {l['expiry']} ({l['days_to_expiry']} d)"
                                         for l in rec["lots"]))
        check(rec["band"] == "critical", "recipient is critical")

        print(f"\n=== 2. All candidates considered ({len(res['candidates'])}, nearest first) ===")
        counts = _print_candidates(res)
        check(len(res["eligible"]) >= 2, f"{len(res['eligible'])} eligible donors (need >= 2)")
        check(all(c["reason_code"] and c["reason"] for c in res["rejected"]),
              "every rejected candidate carries a reason code and a sentence")

        print(f"\n=== 3. Rejected by the donor safety check (surge-inclusive burn rate) ===")
        safety = [c for c in res["rejected"] if c["reason_code"] == "donor_safety_floor"]
        for c in sorted(safety, key=lambda c: c["distance_km"])[:12]:
            print(f"  {c['name']:<25} {c['distance_km']:>5.1f} km  cover {c['days_of_cover']:>5.1f} d ({c['band']:<8})  "
                  f"burn {c['baseline_burn_rate']:.2f} + {c['outbreak_surge']:.2f} surge  spare {c['spare_nominal']:.1f}")
        if len(safety) > 12:
            print(f"  ... {len(safety) - 12} more")
        itself = [c for c in safety if SEVERITY[c["band"]] >= SEVERITY["warning"]]
        print(f"  {len(itself)} of {len(safety)} are themselves at warning or critical; "
              f"{len(safety) - len(itself)} are green but cannot spare {settings.DONOR_SAFETY_FLOOR_DAYS:g} days")
        if safety:
            nearest = min(res["candidates"], key=lambda c: c["distance_km"])
            nearest_safety = min(safety, key=lambda c: c["distance_km"])
            print(f"  nearest candidate of all: {nearest['name']} ({nearest['distance_km']:.1f} km) - "
                  f"{'eligible' if nearest['eligible'] else nearest['reason_code']}; nearest safety rejection: "
                  f"{nearest_safety['name']} ({nearest_safety['distance_km']:.1f} km)")
        else:
            print("  NONE. No candidate was rejected by the donor safety check. The acceptance criterion is NOT met.")
        check(len(safety) >= 1, f"{len(safety)} facility(ies) rejected specifically by the donor safety check")

        print(f"\n=== 4. Ranked eligible donors (Stage 4) ===")
        _print_ranked(res)

        print(f"\n=== 5. Recommendation and post-transfer state ===")
        _print_plan(res)
        plan = res["recommendation"]
        check(res["status"] == "ok", f"need fully met (status {res['status']})")
        ra = plan["recipient_after"]
        check(ra["days_of_cover"] + 1e-9 >= settings.TARGET_COVER_DAYS or plan["shortfall"] > 0,
              f"recipient reaches target cover: {ra['days_of_cover']:.3f} d >= {settings.TARGET_COVER_DAYS} "
              f"(or donors exhausted: shortfall {plan['shortfall']})")
        check(all(d["floor_check"]["passes"] for d in plan["donors"]), "no donor pushed below the floor (engine check)")
        check(all(d["qty"] <= d["spare_units"] for d in plan["donors"]), "every donor sends at most its spare")

        print(f"\n=== 6. Hand verification of the top donor ===")
        if plan["donors"]:
            ok = _print_hand_check(plan["donors"][0], unit)
            check(ok, "top donor's post-transfer cover is above DONOR_SAFETY_FLOOR_DAYS")
            if not ok:
                print("\nCRITICAL FAILURE: the recommendation would starve the donor. Stopping.")
                return False
        else:
            check(False, "no donor to verify")

        print(f"\n=== 7. Near-expiry stock among recommended donors (NEAR_EXPIRY_DAYS = {settings.NEAR_EXPIRY_DAYS}) ===")
        any_near = False
        for d in plan["donors"]:
            near = [l for l in d["lots_given"] + d["lots_retained"]
                    if l["days_to_expiry"] is not None and l["days_to_expiry"] <= settings.NEAR_EXPIRY_DAYS]
            if near:
                any_near = True
                for l in near:
                    print(f"  {d['name']}: lot {l['batch']} {l['qty']:g} {unit} expires {l['expiry']} ({l['days_to_expiry']} d)")
            else:
                print(f"  {d['name']}: no near-expiry lot (earliest expiry "
                      f"{min(l['days_to_expiry'] for l in d['lots_given'] + d['lots_retained'])} d)")
        for l in plan["incoming_lots"]:
            tag = "NEAR-EXPIRY " if l["near_expiry"] else ""
            print(f"  {tag}{l['qty']:g} {unit} from {l['from_name']} (lot {l['batch']}, expires {l['expiry']}, "
                  f"{l['days_to_expiry']} d): recipient finishes it by day "
                  f"{_fmt_days(l['consumed_by_day'])} after {settings.TRANSFER_TRANSIT_DAYS} d transit "
                  f"-> {'consumed in time' if l['consumed_in_time'] else str(round(l['wasted'], 1)) + ' would expire unused'}")
        if not any_near:
            print("  No recommended donor holds near-expiry stock under the current ranking.")
        near_elig = [c for c in res["eligible"] if any(l["days_to_expiry"] <= settings.NEAR_EXPIRY_DAYS for l in c["lots"])]
        if near_elig:
            print("  eligible donors that DO hold near-expiry stock, and where they rank:")
            for c in near_elig:
                i = res["eligible"].index(c) + 1
                l = min(c["lots"], key=_expiry_key)
                print(f"    #{i:<3} {c['name']:<25} {c['distance_km']:>5.1f} km  spare {c['spare_units']:>4}  "
                      f"score {c['score']['total']:.3f}  lot {l['batch']} {l['qty']:g} expires {l['expiry']} ({l['days_to_expiry']} d)"
                      f"  rescued {c['score']['rescued_units']:g}  expiry_benefit {c['score']['components']['expiry_benefit']:.2f}")

        print(f"\n=== 8. DONOR_SAFETY_FLOOR_DAYS changes eligibility ===")
        by_floor = {}
        for floor in (14, 20):
            rf = _eligibility_at_floor(conn, rid, mid, surge, today, rows, floor)
            by_floor[floor] = rf
            top = rf["recommendation"]["donors"][0] if rf["recommendation"] and rf["recommendation"]["donors"] else None
            sc = {}
            for c in rf["rejected"]:
                sc[c["reason_code"]] = sc.get(c["reason_code"], 0) + 1
            print(f"  floor {floor:>2} d: eligible {len(rf['eligible']):>2}, rejected {len(rf['rejected']):>2} "
                  f"(safety check {sc.get('donor_safety_floor', 0)}) "
                  f"status {rf['status']}; top donor "
                  + (f"{top['name']} sends {top['qty']:g}, keeps {top['cover_after']:.1f} d" if top else "none"))
        e14 = {c["facility_id"]: c for c in by_floor[14]["eligible"]}
        e20 = {c["facility_id"]: c for c in by_floor[20]["eligible"]}
        lost = [e14[f] for f in e14 if f not in e20]
        gained = [f for f in e20 if f not in e14]
        print(f"  eligible at 14 but not at 20: {len(lost)}" + (" - " + ", ".join(
            f"{c['name']} ({c['days_of_cover']:.1f} d)" for c in sorted(lost, key=lambda c: c["days_of_cover"])[:8])
            + (" ..." if len(lost) > 8 else "") if lost else ""))
        print(f"  eligible at 20 but not at 14: {len(gained)} (must be 0 - a higher floor can only remove donors)")
        check(len(e14) != len(e20) and not gained, "changing DONOR_SAFETY_FLOOR_DAYS changes which donors qualify")
        print(f"  restored DONOR_SAFETY_FLOOR_DAYS = {settings.DONOR_SAFETY_FLOOR_DAYS}")

        print(f"\n=== 9. Split across donors when no single one suffices ===")
        original_target = settings.TARGET_COVER_DAYS
        settings.TARGET_COVER_DAYS = 30  # demonstration only: a need no single eligible donor can cover
        try:
            rs = recommend(conn, rid, mid, surge=surge, today=today, risk_rows=rows)
        finally:
            settings.TARGET_COVER_DAYS = original_target
        ps = rs["recommendation"]
        max_spare = max((c["spare_units"] for c in rs["eligible"]), default=0)
        print(f"  with TARGET_COVER_DAYS = 30 (demonstration) the need is {ps['need_units']} {unit}; largest single spare "
              f"is {max_spare}")
        for d in ps["donors"]:
            print(f"    #{d['rank']} {d['name']:<25} sends {d['qty']:>4g}  keeps {d['cover_after']:.1f} d  "
                  f"(floor {settings.DONOR_SAFETY_FLOOR_DAYS}) {'ok' if d['floor_check']['passes'] else 'BELOW FLOOR'}")
        print(f"    allocated {ps['total_qty']} of {ps['need_units']}, shortfall {ps['shortfall']}, status {rs['status']}")
        check(ps["split"] and ps["met"] and all(d["floor_check"]["passes"] for d in ps["donors"]),
              f"need split across {len(ps['donors'])} donors, fully met, every donor above the floor")
        top_spare = res["eligible"][0]["spare_units"] if res["eligible"] else 0
        check((len(plan["donors"]) == 1) == (top_spare >= plan["need_units"]),
              f"at the real target the engine splits only when it has to (top donor spare {top_spare} vs need "
              f"{plan['need_units']}: {len(plan['donors'])} donor(s))")
        print(f"  restored TARGET_COVER_DAYS = {settings.TARGET_COVER_DAYS}")

        print(f"\n=== 10. Every other critical (facility, medicine) pair ===")
        crit = [r for r in rows if r["band"] == "critical" and not (r["facility_id"] == rid and r["medicine_id"] == mid)]
        crit.sort(key=lambda r: (r["days_of_cover"], r["name"]))
        print(f"  {len(crit)} other critical pairs")
        print(f"  {'facility':<25} {'medicine':<16} {'cover':>6} {'need':>6} {'elig':>4} {'safety':>6}  result")
        all_viable = True
        for r in crit:
            rr = recommend(conn, r["facility_id"], r["medicine_id"], surge=surge, today=today, risk_rows=rows)
            pr = rr["recommendation"]
            n_safety = sum(1 for c in rr["rejected"] if c["reason_code"] == "donor_safety_floor")
            if pr and pr["donors"]:
                ra2 = pr["recipient_after"]
                desc = (", ".join(f"{d['name']} {d['qty']:g}" for d in pr["donors"])
                        + f" -> {ra2['days_of_cover']:.1f} d ({ra2['band']})"
                        + ("" if pr["met"] else f"; SHORT {pr['shortfall']}"))
            else:
                desc = f"NO DONOR ({rr['status']})"
            viable = rr["status"] == "ok" and all(d["floor_check"]["passes"] for d in (pr["donors"] if pr else []))
            all_viable = all_viable and viable
            print(f"  {r['name']:<25} {r['medicine_id']:<16} {r['days_of_cover']:>6.2f} "
                  f"{rr['recipient']['need_units']:>6} {len(rr['eligible']):>4} {n_safety:>6}  {desc}")
        check(all_viable, "every other critical pair gets a viable, fully-met recommendation")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'CHECKS FAILED'}"
          + ("" if not failures else ":\n  - " + "\n  - ".join(failures)))
    return not failures


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
