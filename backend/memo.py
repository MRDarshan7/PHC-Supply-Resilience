"""Transfer memo - Gemini's second and final job (PROJECT_CONTEXT.md sec 5
Job 2, sec 6, sec 11 Phase 9).

The memo is the justification a District Medical Officer signs. Its input
is the output of backend.redistribute.recommend(): the recipient's state,
the ranked eligible donors with quantities and score breakdowns, and every
rejected candidate with its reason. All of it is computed before Gemini is
involved, and Gemini's latitude is exactly this:

  * where the top candidates score closely (within MEMO_CLOSE_CALL_MARGIN
    of the best score) it may choose among THOSE donors on grounds the
    scorer cannot weigh; when there is no close call the backend's plan is
    the only valid answer;
  * it writes the rationale in English and Telugu, the risk notes, and
    names the rejected candidates an officer should know about - above all
    the ones rejected by the donor safety check, because that is what makes
    the recommendation trustworthy.

Gemini has no authority over quantities. It may not originate a number. The
quantity each chosen donor sends is recomputed here by the backend from the
ORDER Gemini chose (redistribute.allocate), and the model's stated
quantities must agree with it.

Validation - enforced in code, never by prompting:

  1. every facility_id in chosen_donors is in the supplied candidate set
     (eligible donors) AND in the close-call set; every notable rejection
     is in the supplied rejected set;
  2. the backend re-allocates in the chosen order; each stated quantity must
     equal the backend's, and the total must equal what the backend computed
     for the recommendation;
  3. every numeral anywhere in the generated text (both rationales, every
     risk note, every rejection note) must equal a value in the supplied
     input - extracted and cross-checked; dates, batch numbers and outbreak
     ids are checked as whole tokens. One unmatched number rejects the
     whole response;
  4. the Telugu rationale must actually be in Telugu script.

Any failure is logged, the response is discarded, and a deterministic
template built from the same data takes its place (after one retry). The
recommendation still works; it is merely less articulate. The template is
run through the same validator as a self-check.

Responses that pass validation are cached to disk keyed by a hash of the
exact input (same pattern as backend/ingest_idsp.py), so the demo never
repeats a Gemini call for the same recommendation.

    py backend/memo.py     # Phase 9 memo verification (Thulluru / ORS)
"""

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import gemini_client, settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.redistribute import DonorSafetyError, _expiry_key, allocate, build_plan, recommend  # noqa: E402
from backend.rules import load_rules, per_case_table  # noqa: E402

log = logging.getLogger(__name__)

SOURCE_GEMINI = "gemini"
SOURCE_CACHE = "gemini_cache"
SOURCE_TEMPLATE = "template"

# Output schema (PROJECT_CONTEXT.md sec 5 Job 2): chosen_donors[], rationale in
# both languages, risk_notes, plus the rejected candidates the memo names.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "chosen_donors": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"facility_id": {"type": "STRING"}, "quantity": {"type": "INTEGER"}},
                "required": ["facility_id", "quantity"],
            },
        },
        "rationale_en": {"type": "STRING"},
        "rationale_te": {"type": "STRING"},
        "risk_notes": {"type": "ARRAY", "items": {"type": "STRING"}},
        "notable_rejections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"facility_id": {"type": "STRING"}, "reason": {"type": "STRING"}},
                "required": ["facility_id", "reason"],
            },
        },
    },
    "required": ["chosen_donors", "rationale_en", "rationale_te", "risk_notes", "notable_rejections"],
}

PROMPT = """You are drafting the stock-transfer memo that a District Medical Officer (DMO) in Guntur district, Andhra Pradesh, will sign. It recommends moving one medicine from one or more donor Primary Health Centres to a recipient facility that is running out.

Everything you need is in the JSON document after these instructions. The backend computed all of it: current stock, consumption including the outbreak surge, days of cover, the donor safety check, and the exact quantities. You have two jobs, in this order.

JOB 1 - choose the donor(s).
The scorer has ranked every eligible donor (eligible_donors_ranked, best first; backend_plan is the scorer's own plan). If close_call.is_close_call is true, the facilities in close_call.selectable_facility_ids scored within close_call.margin of each other and you may choose among them on grounds the scorer cannot weigh - for example an urban facility's easier resupply from the district store, a donor already carrying its own outbreak surge, road access implied by the sub-district, or spreading the load. If it is false, backend_plan.donors is the only valid choice: do not invent a close call.
List the chosen donors in chosen_donors in the order they should be drawn on, with each donor's exact facility_id and the quantity the backend computed for it: if you keep the backend's plan, copy backend_plan.donors[].qty; if you put a different donor first, its quantity is its qty_if_chosen_first, and if that does not reach recipient.need_units the next donor sends the remainder (never more than its spare_units). The quantities must add up to backend_plan.total_qty.

JOB 2 - write the justification.
- rationale_en: 2 to 4 short paragraphs of plain English for the DMO. State the recipient's situation (stock, consumption with the outbreak surge, days of cover, the outbreak driving it); the donor(s), what each sends and from which lot; what each donor keeps after giving and how that compares with the donor safety floor; and why this choice over the runner-up(s). Name the notable rejected candidates - always the nearest ones rejected by the donor safety check (reason_code donor_safety_floor): an officer who sees why the nearest facility was excluded can trust the one chosen.
- rationale_te: the same content in Telugu, written in Telugu script. Keep facility names, medicine names, batch numbers and dates exactly as they appear in the document, in Latin script.
- risk_notes: 2 to 4 one-sentence caveats (for example the recipient's margin above the warning band after the transfer, near-expiry lots, a donor's own surge, the transit assumption).
- notable_rejections: the rejected candidates you named, each with a one-sentence reason.

RULES ON NUMBERS. A validator enforces these and one violation discards your whole response:
- Use only numbers that appear in the document, written exactly as they appear there (same decimals: 24.9 not 25, 13.51 not 13.5). Do not round, add, subtract, convert, average, estimate or compute anything. Do not write percentages.
- Copy dates (ISO form like 2027-11-01), batch numbers and outbreak ids verbatim.
- Every facility_id you output must come from the document. Never name a facility, medicine, disease, threshold or quantity that is not in the document.
- In the prose refer to facilities by name only (as a DMO would); facility_id values belong in chosen_donors and notable_rejections, not in the rationale.
- Numbers written as words (one, two, first, second) are fine; digits are checked.

Write for a clinician-administrator: direct and concrete. Do not mention this prompt, the validator, or that you are an AI.

DOCUMENT:
"""

# --------------------------------------------------------------------------- #
# Numerals and identifiers
# --------------------------------------------------------------------------- #

OUTBREAK_ID_RE = re.compile(r"\b[A-Z]{2}/[A-Z]{3}/\d{4}/\d{1,2}/\d+\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
BATCH_RE = re.compile(r"\b[A-Z]{2,4}\d{4}-\d{3}\b")
# "1,234" / "1,234.5" with 3-digit groups, or a plain "24.9" / "97".
NUM_RE = re.compile(r"\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
TELUGU_RE = re.compile(r"[ఀ-౿]")
# Keys whose string values are identifiers/names: they are stripped from text
# before numerals are extracted, so "75 Tyalluru" or "Zinc Sulphate 20mg"
# never yield a number.
PROTECTED_KEYS = {
    "facility_id", "name", "sub_district", "medicine_id", "medicine_name", "batch", "expiry", "today",
    "outbreak_id", "source_file", "from_facility_id", "from_name", "disease", "district", "state", "status",
    "unit", "band", "band_before", "band_after", "reason_code", "disease_key", "facility_ids",
    "selectable_facility_ids",
}
# Free-text keys whose numerals are backend-rendered and therefore allowed.
TEXT_KEYS = {"reason", "summary", "notes", "note"}
MIN_TELUGU_CHARS = 20


def normalise_digits(text):
    """Telugu (and any other script's) decimal digits -> ASCII, so Telugu
    prose is checked by the same extractor."""
    out = []
    for ch in text:
        if ch.isdecimal() and not ch.isascii():
            out.append(str(unicodedata.decimal(ch)))
        else:
            out.append(ch)
    return "".join(out)


def to_decimal(numeral):
    try:
        return Decimal(numeral.replace(",", ""))
    except InvalidOperation:
        return None


def tokenize(text, protected):
    """(identifiers, numerals) found in `text`. Identifiers are protected
    strings (names, ids), outbreak ids, ISO dates and batch numbers, removed
    before numerals are extracted; numerals are every remaining number."""
    t = normalise_digits(text or "")
    ids = []
    for s in sorted(protected, key=len, reverse=True):
        if s and s in t:
            ids.append(s)
            t = t.replace(s, " ")
    for rx in (OUTBREAK_ID_RE, DATE_RE, BATCH_RE):
        ids.extend(rx.findall(t))
        t = rx.sub(" ", t)
    return ids, NUM_RE.findall(t)


def _walk(node, path=""):
    """(path, leaf) for every leaf of a JSON-like structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def _leaf_key(path):
    return path.rsplit(".", 1)[-1].split("[")[0]


def collect_allowed(doc):
    """Walk the input document. Returns (numbers, identifiers, protected):
    numbers {Decimal: [paths]} for every numeric leaf and every numeral in
    a free-text leaf; identifiers {token: [paths]} for dates / batches /
    outbreak ids; protected {string} for names and ids containing digits.
    Two passes, so every protected string is known before any free text is
    scanned for numerals."""
    numbers, identifiers, protected = {}, {}, set()
    leaves = list(_walk(doc))
    for path, v in leaves:
        if not isinstance(v, str):
            continue
        for rx in (OUTBREAK_ID_RE, DATE_RE, BATCH_RE):
            for tok in rx.findall(v):
                identifiers.setdefault(tok, []).append(path)
        key = _leaf_key(path)
        if key not in TEXT_KEYS and any(ch.isdigit() for ch in v):
            protected.add(v)
    for path, v in leaves:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            numbers.setdefault(Decimal(str(v)), []).append((path, str(v)))
        elif isinstance(v, str) and _leaf_key(path) in TEXT_KEYS:
            _, nums = tokenize(v, protected)
            for n in nums:
                d = to_decimal(n)
                if d is not None:
                    numbers.setdefault(abs(d), []).append((path, n))
    return numbers, identifiers, protected


def _decimals(numeral):
    return len(numeral.split(".")[1]) if "." in numeral else 0


def best_source(numeral, entries):
    """The input path that best explains `numeral`: one rendered with the
    same number of decimals (17.0 km rather than a count of 17), else the
    first. Counts and ranks are the least specific, so they come last."""
    if not entries:
        return None, None
    vague = {"rank", "order", "eligible_count", "rejected_count", "week", "year", "stage"}

    def key(e):
        path, rendered = e
        return (_decimals(rendered) != _decimals(numeral),
                _leaf_key(path) in vague or path.startswith("rejected_by_reason"))
    return min(entries, key=key)


# --------------------------------------------------------------------------- #
# Input document
# --------------------------------------------------------------------------- #

def _num(x, nd=None):
    """Numbers as the memo may cite them: integral values as ints, otherwise
    rounded to nd decimals. None passes through."""
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, int):
        return x
    if float(x).is_integer():
        return int(x)
    return round(float(x), 2 if nd is None else nd)


def _lot(l):
    return {"batch": l["batch"], "expiry": l["expiry"], "days_to_expiry": l["days_to_expiry"], "qty": _num(l["qty"])}


def _band_label(band):
    return {"critical": f"critical (below {settings.CRITICAL_DAYS} days)",
            "warning": f"warning ({settings.CRITICAL_DAYS} to {settings.WARNING_DAYS} days)",
            "safe": f"safe (above {settings.WARNING_DAYS} days)"}.get(band, band)


def outbreaks_for_medicine(conn, medicine_id):
    """Active outbreaks whose disease maps to this medicine in rules.yaml -
    the ones whose surge is in the recipient's burn rate."""
    from backend.outbreak_demand import active_outbreaks  # lazy: pulls in the Gemini client
    diseases = set(per_case_table(load_rules()).get(medicine_id, {}))
    return [ob for ob in active_outbreaks(conn) if ob["disease_key"] in diseases]


def close_call_set(result, margin=None):
    """Facility ids Gemini may choose among: every eligible donor within
    `margin` of the top score, plus every donor in the backend's plan (so a
    split plan can always be reproduced). (ids, is_close_call)."""
    margin = settings.MEMO_CLOSE_CALL_MARGIN if margin is None else margin
    eligible = result["eligible"]
    if not eligible:
        return [], False
    top = eligible[0]["score"]["total"]
    close = [c["facility_id"] for c in eligible if c["score"]["total"] + 1e-9 >= top - margin]
    plan_ids = [d["facility_id"] for d in (result["recommendation"] or {}).get("donors", [])]
    ids = close + [i for i in plan_ids if i not in close]
    return ids, len(close) > 1


def _donor_after(c, need):
    """Figures for `c` as first donor: quantity, stock and cover after."""
    qty = min(need, c["spare_units"])
    stock_after = c["stock"] - qty
    cover_after = None if c["burn_rate"] <= 0 else stock_after / c["burn_rate"]
    return qty, stock_after, cover_after


def build_memo_input(conn, result, today, outbreaks=None):
    """The document Gemini sees - every number pre-rounded to the precision
    the memo may cite. This is also the universe of allowed numbers."""
    R = result["recipient"]
    rec = result["recommendation"] or {}
    p = result["parameters"]
    med = conn.execute("SELECT medicine_id, name, unit FROM medicines WHERE medicine_id = ?",
                       (R["medicine_id"],)).fetchone()
    unit = R["unit"]
    floor = p["donor_safety_floor_days"]
    need = R["need_units"]
    selectable, is_close = close_call_set(result)
    outbreaks = outbreaks_for_medicine(conn, R["medicine_id"]) if outbreaks is None else outbreaks

    shown = result["eligible"][:settings.MEMO_ELIGIBLE_IN_PROMPT]
    shown_ids = {c["facility_id"] for c in shown}
    shown += [c for c in result["eligible"] if c["facility_id"] in selectable and c["facility_id"] not in shown_ids]
    eligible_doc = []
    for c in shown:
        qty, stock_after, cover_after = _donor_after(c, need)
        earliest = min(c["transferable_lots"], key=_expiry_key) if c["transferable_lots"] else None
        eligible_doc.append({
            "rank": c["rank"], "facility_id": c["facility_id"], "name": c["name"], "sub_district": c["sub_district"],
            "distance_km": _num(c["distance_km"], 1), "stock": _num(c["stock"]),
            "baseline_burn_rate": _num(c["baseline_burn_rate"], 2), "outbreak_surge": _num(c["outbreak_surge"], 2),
            "burn_rate": _num(c["burn_rate"], 2), "days_of_cover": _num(c["days_of_cover"], 1), "band": c["band"],
            "spare_units": c["spare_units"], "qty_if_chosen_first": qty, "stock_after_if_chosen_first": _num(stock_after),
            "cover_after_if_chosen_first": _num(cover_after, 1),
            "cover_margin_above_floor_if_chosen_first": None if cover_after is None else _num(cover_after - floor, 1),
            "score_total": _num(c["score"]["total"], 3),
            "score_components": {k: _num(v, 2) for k, v in c["score"]["components"].items()},
            "rescued_from_expiry_units": _num(c["score"]["rescued_units"]),
            "earliest_transferable_lot": _lot(earliest) if earliest else None,
            "selectable": c["facility_id"] in selectable, "notes": list(c["notes"]),
        })

    plan_doc = None
    if rec:
        plan_doc = {
            "donors": [{
                "order": d["order"], "rank": d["rank"], "facility_id": d["facility_id"], "name": d["name"],
                "sub_district": d["sub_district"], "distance_km": _num(d["distance_km"], 1), "qty": d["qty"],
                "lots_given": [_lot(l) for l in d["lots_given"]],
                "stock_before": _num(d["stock_before"]), "stock_after": _num(d["stock_after"]),
                "cover_before": _num(d["cover_before"], 1), "cover_after": _num(d["cover_after"], 1),
                "cover_margin_above_floor": None if d["cover_after"] is None else _num(d["cover_after"] - floor, 1),
                "band_before": d["band_before"], "band_after": d["band_after"],
                "score_total": _num(d["score"]["total"], 3), "summary": d["summary"],
            } for d in rec["donors"]],
            "total_qty": rec["total_qty"], "need_units": rec["need_units"], "shortfall": rec["shortfall"],
            "met": rec["met"], "split": rec["split"],
            "recipient_after": {
                "stock": _num(rec["recipient_after"]["stock"]),
                "days_of_cover": _num(rec["recipient_after"]["days_of_cover"], 1),
                "band": rec["recipient_after"]["band"],
                "projected_cover_days": _num(rec["recipient_after"]["projected_cover_days"], 1),
            },
            "incoming_lots": [{
                "from_facility_id": l["from_facility_id"], "from_name": l["from_name"], "batch": l["batch"],
                "expiry": l["expiry"], "days_to_expiry": l["days_to_expiry"], "qty": _num(l["qty"]),
                "near_expiry": l["near_expiry"], "consumed_in_time": l["consumed_in_time"],
            } for l in rec["incoming_lots"]],
        }

    rejected_doc = []
    for c in sorted(result["rejected"], key=lambda c: (c["stage"], c["distance_km"])):
        rejected_doc.append({
            "facility_id": c["facility_id"], "name": c["name"], "sub_district": c["sub_district"],
            "distance_km": _num(c["distance_km"], 1), "stock": _num(c["stock"]),
            "baseline_burn_rate": _num(c["baseline_burn_rate"], 2), "outbreak_surge": _num(c["outbreak_surge"], 2),
            "burn_rate": _num(c["burn_rate"], 2), "days_of_cover": _num(c["days_of_cover"], 1), "band": c["band"],
            "stage": c["stage"], "reason_code": c["reason_code"], "reason": c["reason"],
        })
    by_reason = {}
    for c in result["rejected"]:
        by_reason[c["reason_code"]] = by_reason.get(c["reason_code"], 0) + 1

    return {
        "today": p["today"],
        "thresholds": {
            "critical_days": settings.CRITICAL_DAYS, "warning_days": settings.WARNING_DAYS,
            "donor_safety_floor_days": floor, "target_cover_days": p["target_cover_days"],
            "max_transfer_radius_km": p["max_transfer_radius_km"], "transfer_transit_days": p["transfer_transit_days"],
            "transfer_min_use_days": p["transfer_min_use_days"],
            "transit_and_use_window_days": p["transit_and_use_window_days"], "near_expiry_days": p["near_expiry_days"],
            "score_weights": {k: _num(v, 2) for k, v in p["score_weights"].items()},
        },
        "medicine": {"medicine_id": med["medicine_id"], "medicine_name": med["name"], "unit": med["unit"]},
        "recipient": {
            "facility_id": R["facility_id"], "name": R["name"], "sub_district": R["sub_district"],
            "stock": _num(R["stock"]), "baseline_burn_rate": _num(R["baseline_burn_rate"], 2),
            "outbreak_surge": _num(R["outbreak_surge"], 2), "burn_rate": _num(R["burn_rate"], 2),
            "days_of_cover": _num(R["days_of_cover"], 1), "band": R["band"], "band_label": _band_label(R["band"]),
            "target_cover_days": R["target_cover_days"], "need_units": need,
            "lots": [_lot(l) for l in R["lots"]],
        },
        "outbreaks_driving_surge": [{
            "outbreak_id": ob["outbreak_id"], "disease": ob["disease"], "state": ob["state"], "district": ob["district"],
            "sub_district": ob["sub_district"] or "", "cases": ob["cases"], "deaths": ob["deaths"],
            "week": ob["week"], "year": ob["year"], "status": ob["status"],
        } for ob in outbreaks],
        "status": result["status"],
        "backend_plan": plan_doc,
        "close_call": {"is_close_call": is_close, "margin": settings.MEMO_CLOSE_CALL_MARGIN,
                       "selectable_facility_ids": selectable},
        "eligible_count": len(result["eligible"]),
        "eligible_donors_ranked": eligible_doc,
        "rejected_count": len(result["rejected"]),
        "rejected_by_reason": by_reason,
        "rejected": rejected_doc,
        "unit": unit,
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

class MemoContext:
    """Everything validation needs, built once per recommendation."""

    def __init__(self, conn, result, today, outbreaks=None):
        self.result = result
        self.today = today
        self.unit = result["recipient"]["unit"]
        self.input = build_memo_input(conn, result, today, outbreaks)
        self.numbers, self.identifiers, self.protected = collect_allowed(self.input)
        self.eligible_by_id = {c["facility_id"]: c for c in result["eligible"]}
        self.rejected_by_id = {c["facility_id"]: c for c in result["rejected"]}
        self.selectable = list(self.input["close_call"]["selectable_facility_ids"])
        self.is_close_call = self.input["close_call"]["is_close_call"]
        self.plan_numbers = {}

    def allow_plan(self, plan):
        """Numbers from a backend re-allocation (Gemini's chosen order) are
        backend-computed too; admit them for the text check."""
        doc = {"plan": {
            "donors": [{"qty": d["qty"], "stock_after": _num(d["stock_after"]), "cover_after": _num(d["cover_after"], 1),
                        "cover_margin_above_floor": None if d["cover_after"] is None
                        else _num(d["cover_after"] - settings.DONOR_SAFETY_FLOOR_DAYS, 1),
                        "lots_given": [_lot(l) for l in d["lots_given"]]} for d in plan["donors"]],
            "total_qty": plan["total_qty"],
            "recipient_after": {"stock": _num(plan["recipient_after"]["stock"]),
                                "days_of_cover": _num(plan["recipient_after"]["days_of_cover"], 1)},
        }}
        nums, ids, _ = collect_allowed(doc)
        self.plan_numbers = nums
        for k, v in ids.items():
            self.identifiers.setdefault(k, []).extend(v)

    def lookup(self, dec):
        return self.numbers.get(dec) or self.plan_numbers.get(dec)


def _text_fields(resp):
    yield "rationale_en", resp.get("rationale_en")
    yield "rationale_te", resp.get("rationale_te")
    for i, n in enumerate(resp.get("risk_notes") or []):
        yield f"risk_notes[{i}]", n
    for i, r in enumerate(resp.get("notable_rejections") or []):
        yield f"notable_rejections[{i}].reason", (r or {}).get("reason") if isinstance(r, dict) else None


def check_numbers(resp, ctx):
    """Cross-check every numeral and identifier in the response text against
    the input. Returns (failures, report) where report lists each numeral
    with the input path it matched."""
    failures, report = [], []
    for field, text in _text_fields(resp):
        if not isinstance(text, str):
            continue
        ids, nums = tokenize(text, ctx.protected)
        for tok in ids:
            if tok in ctx.protected:
                continue  # a name or id from the document
            paths = ctx.identifiers.get(tok)
            report.append({"field": field, "token": tok, "kind": "identifier", "matched": bool(paths),
                           "source": paths[0] if paths else None, "source_value": tok if paths else None})
            if not paths:
                failures.append(f"{field}: identifier {tok!r} does not appear in the input")
        for n in nums:
            d = to_decimal(n)
            entries = ctx.lookup(abs(d)) if d is not None else None
            source, source_value = best_source(n, entries)
            report.append({"field": field, "token": n, "kind": "number", "matched": bool(entries),
                           "source": source, "source_value": source_value,
                           "value": str(d.normalize()) if d is not None else None})
            if not entries:
                failures.append(f"{field}: number {n} does not match any backend-computed value")
    return failures, report


def validate_response(resp, ctx):
    """(ok, failures, plan, report). plan is the backend re-allocation in the
    chosen order (None when the donor list itself is invalid)."""
    failures = []
    if not isinstance(resp, dict):
        return False, [f"response is not an object ({type(resp).__name__})"], None, []
    for key in ("chosen_donors", "rationale_en", "rationale_te", "risk_notes", "notable_rejections"):
        if key not in resp:
            failures.append(f"missing field {key}")
    if failures:
        return False, failures, None, []
    for key in ("rationale_en", "rationale_te"):
        if not isinstance(resp[key], str) or not resp[key].strip():
            failures.append(f"{key} is empty")
    if not isinstance(resp["risk_notes"], list) or not all(isinstance(x, str) for x in resp["risk_notes"]):
        failures.append("risk_notes is not a list of strings")
    if isinstance(resp.get("rationale_te"), str) and len(TELUGU_RE.findall(resp["rationale_te"])) < MIN_TELUGU_CHARS:
        failures.append("rationale_te is not written in Telugu script")

    # 1. chosen donors: in the candidate set, in the close-call set, no repeats
    chosen = resp["chosen_donors"]
    plan = None
    if not isinstance(chosen, list) or not chosen:
        failures.append("chosen_donors is empty")
    else:
        ids, bad = [], False
        for i, d in enumerate(chosen):
            fid = d.get("facility_id") if isinstance(d, dict) else None
            qty = d.get("quantity") if isinstance(d, dict) else None
            if not isinstance(fid, str) or fid not in ctx.eligible_by_id:
                failures.append(f"chosen_donors[{i}]: facility_id {fid!r} is not in the supplied candidate set")
                bad = True
            elif fid not in ctx.selectable:
                failures.append(f"chosen_donors[{i}]: {fid} is eligible but outside the close-call set "
                                f"{ctx.selectable} - the scorer's verdict was not close")
                bad = True
            if fid in ids:
                failures.append(f"chosen_donors[{i}]: {fid} listed twice")
                bad = True
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
                failures.append(f"chosen_donors[{i}]: quantity {qty!r} is not a positive integer")
                bad = True
            ids.append(fid)
        # 2. the backend re-allocates in the chosen order; quantities must agree
        if not bad:
            try:
                donors, remaining = allocate(ctx.result["recipient"], [ctx.eligible_by_id[i] for i in ids], ctx.unit,
                                             n_eligible=len(ctx.result["eligible"]))
            except DonorSafetyError as e:
                failures.append(f"donor safety: {e}")
                donors, remaining = [], None
            if remaining is not None:
                by_id = {d["facility_id"]: d for d in donors}
                for i, d in enumerate(chosen):
                    fid, qty = d["facility_id"], d["quantity"]
                    if fid not in by_id:
                        failures.append(f"chosen_donors[{i}]: {fid} would send nothing - the need is already met "
                                        f"by the donors before it")
                    elif by_id[fid]["qty"] != qty:
                        failures.append(f"chosen_donors[{i}]: quantity for {fid} is {qty}, the backend computed "
                                        f"{by_id[fid]['qty']} for this order")
                total = sum(d["qty"] for d in donors)
                expected = (ctx.result["recommendation"] or {}).get("total_qty", 0)
                if total != expected:
                    failures.append(f"total quantity {total} != backend total {expected}")
                if not failures:
                    plan = build_plan(ctx.result["recipient"], donors, remaining)
                    ctx.allow_plan(plan)

    # 5. notable rejections: in the rejected set; the safety check must be represented
    notable = resp["notable_rejections"]
    if not isinstance(notable, list):
        failures.append("notable_rejections is not a list")
        notable = []
    seen = set()
    for i, r in enumerate(notable):
        fid = r.get("facility_id") if isinstance(r, dict) else None
        if fid not in ctx.rejected_by_id:
            failures.append(f"notable_rejections[{i}]: facility_id {fid!r} is not in the supplied rejected set")
        elif fid in seen:
            failures.append(f"notable_rejections[{i}]: {fid} listed twice")
        seen.add(fid)
    safety = [c["facility_id"] for c in ctx.result["rejected"] if c["reason_code"] == "donor_safety_floor"]
    if safety and not any(f in safety for f in seen):
        failures.append("no candidate rejected by the donor safety check is named among notable_rejections")

    # 3. every number in the text
    num_failures, report = check_numbers(resp, ctx)
    failures += num_failures
    return not failures, failures, plan, report


# --------------------------------------------------------------------------- #
# Deterministic template (the floor the recommendation never drops below)
# --------------------------------------------------------------------------- #

UNIT_TE = {"packets": "ప్యాకెట్లు", "tablets": "మాత్రలు", "litres": "లీటర్లు"}
BAND_TE = {"critical": "అత్యవసర (critical)", "warning": "హెచ్చరిక (warning)", "safe": "సురక్షిత (safe)",
           "unknown": "తెలియని (unknown)"}
REASON_TE = {
    "no_stock": "ఈ మందు నిల్వ లేదు",
    "out_of_radius": "బదిలీ పరిధికి వెలుపల ఉంది",
    "stock_expired": "నిల్వ గడువు ముగిసింది",
    "stock_expiring_in_window": "రవాణా-వినియోగ వ్యవధిలోపే నిల్వ గడువు ముగుస్తుంది",
    "stock_undated": "నిల్వకు గడువు తేదీ నమోదు కాలేదు",
    "spare_below_one_unit": "ఒక యూనిట్ కంటే తక్కువ మిగులు",
    "expiry_guard": "మిగిలే నిల్వ ముందుగా గడువు ముగుస్తుంది",
}


def notable_rejections_for(doc, n_safety=3, n_other=2):
    """The rejected candidates a memo should name: the nearest rejected by
    the donor safety check, then the nearest rejected for other reasons."""
    rej = sorted(doc["rejected"], key=lambda c: c["distance_km"])
    safety = [c for c in rej if c["reason_code"] == "donor_safety_floor"][:n_safety]
    other = [c for c in rej if c["reason_code"] != "donor_safety_floor"][:n_other]
    return safety + other


def _lots_phrase(lots):
    return "; ".join(f"{l['qty']} from lot {l['batch']} (expires {l['expiry']}, {l['days_to_expiry']} days)" for l in lots)


def template_response(doc):
    """A complete memo built from the input document alone. Same shape as a
    Gemini response, same numbers, so it passes the same validator."""
    R, T, M = doc["recipient"], doc["thresholds"], doc["medicine"]
    unit, unit_te = doc["unit"], UNIT_TE.get(doc["unit"], doc["unit"])
    plan = doc["backend_plan"]
    floor = T["donor_safety_floor_days"]
    n_elig = doc["eligible_count"]
    obs = doc["outbreaks_driving_surge"]

    # -- English ---------------------------------------------------------- #
    status = doc.get("status")
    if status == "no_burn_rate":
        p1 = (f"{R['name']} ({R['sub_district']}) holds {R['stock']} {unit} of {M['medicine_name']} and records no "
              f"consumption of it: no disease in the clinical rule table with a caseload here uses this medicine, so days of "
              f"cover is undefined and no transfer can be sized.")
    else:
        p1 = (f"{R['name']} ({R['sub_district']}) holds {R['stock']} {unit} of {M['medicine_name']}. Its consumption is "
              f"{R['burn_rate']} {unit}/day ({R['baseline_burn_rate']} baseline plus {R['outbreak_surge']} from the outbreak "
              f"surge), which is {R['days_of_cover']} days of cover - {R['band_label']}.")
        if R["need_units"] > 0:
            p1 += f" Restoring {T['target_cover_days']} days of cover needs {R['need_units']} {unit}."
    if obs and status != "no_burn_rate":
        ob = obs[0]
        where = f"{ob['district']} district" + (f", sub-district {ob['sub_district']}" if ob["sub_district"] else "")
        p1 += (f" The surge comes from IDSP outbreak {ob['outbreak_id']}: {ob['disease']}, {where}, {ob['cases']} cases "
               f"reported in week {ob['week']} of {ob['year']}.")
    if plan and plan["donors"]:
        parts = []
        for d in plan["donors"]:
            parts.append(
                f"{d['name']} ({d['sub_district']}), {d['distance_km']} km away, sends {d['qty']} {unit}: "
                f"{_lots_phrase(d['lots_given'])}. After giving it keeps {d['stock_after']} {unit} = {d['cover_after']} "
                f"days of cover, {d['cover_margin_above_floor']} days above the {floor}-day donor safety floor. It ranked "
                f"{d['rank']} of {n_elig} eligible donors (score {d['score_total']}).")
        p2 = ("Recommended donor: " if len(parts) == 1 else "Recommended donors, in order: ") + " ".join(parts)
        runners = [c for c in doc["eligible_donors_ranked"]
                   if c["selectable"] and c["facility_id"] not in {d["facility_id"] for d in plan["donors"]}]
        if runners:
            p2 += " Close alternatives the scorer ranked within the margin: " + "; ".join(
                f"{c['name']} (score {c['score_total']}, {c['distance_km']} km, would keep "
                f"{c['cover_after_if_chosen_first']} days after sending {c['qty_if_chosen_first']} {unit})"
                for c in runners) + ". The scorer's ranking was kept."
        ra = plan["recipient_after"]
        p3 = (f"After the transfer {R['name']} holds {ra['stock']} {unit} = {ra['days_of_cover']} days of cover "
              f"({_band_label(ra['band'])}).")
        if not plan["met"]:
            p3 += f" The eligible donors cannot cover the full need: {plan['shortfall']} {unit} remain short."
    elif status == "no_need":
        p2 = (f"No transfer is needed: {R['name']} already holds {R['days_of_cover']} days of cover, at or above the "
              f"{T['target_cover_days']}-day target, with the outbreak surge included.")
        p3 = ""
    elif status == "no_burn_rate":
        p2 = "No transfer applies. If this medicine is in use here, the caseload or the rule table needs updating first."
        p3 = ""
    else:
        p2 = (f"No eligible donor was found within {T['max_transfer_radius_km']} km: every candidate failed the "
              f"screen or the donor safety check. Escalate to the district store.")
        p3 = ""
    notable = notable_rejections_for(doc)
    p4 = ""
    if notable:
        p4 = "Notable exclusions (nearest first): " + " ".join(
            f"{c['name']} ({c['distance_km']} km) - {c['reason']}" for c in notable)
    rationale_en = "\n\n".join(x for x in (p1, p2, p3, p4) if x)

    # -- Telugu ----------------------------------------------------------- #
    band_te = BAND_TE.get(R["band"], R["band"])
    if status == "no_burn_rate":
        t1 = (f"{R['name']} ({R['sub_district']}) వద్ద {M['medicine_name']} నిల్వ {R['stock']} {unit_te} ఉంది, కానీ దీని వినియోగం "
              f"నమోదు కాలేదు: క్లినికల్ నియమ పట్టికలో ఈ మందును ఉపయోగించే వ్యాధికి ఇక్కడ కేసులు లేవు, కాబట్టి నిల్వ రోజులు "
              f"నిర్ధారించలేము మరియు బదిలీ పరిమాణం లెక్కించలేము.")
    else:
        t1 = (f"{R['name']} ({R['sub_district']}) వద్ద {M['medicine_name']} నిల్వ {R['stock']} {unit_te} ఉంది. ప్రస్తుత వినియోగం "
              f"రోజుకు {R['burn_rate']} {unit_te} (సాధారణ {R['baseline_burn_rate']} + వ్యాధి వ్యాప్తి వల్ల అదనంగా "
              f"{R['outbreak_surge']}), అంటే {R['days_of_cover']} రోజుల నిల్వ - {band_te} స్థితి"
              + (f" ({T['critical_days']} రోజుల కంటే తక్కువ)" if R["band"] == "critical" else "") + ".")
        if R["need_units"] > 0:
            t1 += f" {T['target_cover_days']} రోజుల నిల్వకు చేరుకోవడానికి {R['need_units']} {unit_te} అవసరం."
    if obs and status != "no_burn_rate":
        ob = obs[0]
        where = f"{ob['district']} జిల్లా" + (f", {ob['sub_district']} ఉప-జిల్లా" if ob["sub_district"] else "")
        t1 += (f" ఈ అదనపు వినియోగానికి కారణం IDSP నివేదికలోని వ్యాధి వ్యాప్తి {ob['outbreak_id']}: {ob['disease']}, "
               f"{where}, {ob['year']} సంవత్సరం {ob['week']}వ వారంలో {ob['cases']} కేసులు.")
    if plan and plan["donors"]:
        parts = []
        for d in plan["donors"]:
            lots_te = "; ".join(f"లాట్ {l['batch']} నుండి {l['qty']} (గడువు {l['expiry']}, {l['days_to_expiry']} రోజులు)"
                                for l in d["lots_given"])
            parts.append(
                f"{d['name']} ({d['sub_district']}) - {d['distance_km']} కి.మీ. దూరం - {d['qty']} {unit_te} పంపుతుంది: "
                f"{lots_te}. బదిలీ తర్వాత దాని వద్ద {d['stock_after']} {unit_te} = {d['cover_after']} రోజుల నిల్వ "
                f"మిగులుతుంది, ఇది {floor} రోజుల దాత భద్రతా కనీస పరిమితి కంటే {d['cover_margin_above_floor']} రోజులు "
                f"ఎక్కువ. అర్హత పొందిన {n_elig} దాతలలో దీని స్థానం {d['rank']} (స్కోరు {d['score_total']}).")
        t2 = ("సిఫార్సు చేసిన దాత: " if len(parts) == 1 else "సిఫార్సు చేసిన దాతలు (క్రమంలో): ") + " ".join(parts)
        if runners:
            t2 += " స్కోరు పరంగా దగ్గరగా ఉన్న ప్రత్యామ్నాయాలు: " + "; ".join(
                f"{c['name']} (స్కోరు {c['score_total']}, {c['distance_km']} కి.మీ., {c['qty_if_chosen_first']} {unit_te} "
                f"పంపిన తర్వాత {c['cover_after_if_chosen_first']} రోజుల నిల్వ ఉంటుంది)" for c in runners
            ) + ". స్కోరింగ్ క్రమాన్నే ఉంచారు."
        ra = plan["recipient_after"]
        t3 = (f"బదిలీ తర్వాత {R['name']} వద్ద {ra['stock']} {unit_te} = {ra['days_of_cover']} రోజుల నిల్వ ఉంటుంది "
              f"({BAND_TE.get(ra['band'], ra['band'])}).")
        if not plan["met"]:
            t3 += f" అర్హత పొందిన దాతలు పూర్తి అవసరాన్ని తీర్చలేరు: {plan['shortfall']} {unit_te} కొరత మిగిలి ఉంది."
    elif status == "no_need":
        t2 = (f"బదిలీ అవసరం లేదు: {R['name']} వద్ద ఇప్పటికే {R['days_of_cover']} రోజుల నిల్వ ఉంది, ఇది వ్యాధి వ్యాప్తి అదనపు "
              f"వినియోగంతో సహా {T['target_cover_days']} రోజుల లక్ష్యానికి సమానం లేదా ఎక్కువ.")
        t3 = ""
    elif status == "no_burn_rate":
        t2 = "బదిలీ వర్తించదు. ఈ మందు ఇక్కడ వాడుకలో ఉంటే, ముందుగా కేసుల డేటా లేదా నియమ పట్టికను నవీకరించాలి."
        t3 = ""
    else:
        t2 = (f"{T['max_transfer_radius_km']} కి.మీ. పరిధిలో అర్హత పొందిన దాత ఎవరూ లేరు: ప్రతి కేంద్రం పరిశీలనలో లేదా "
              f"దాత భద్రతా తనిఖీలో విఫలమైంది. జిల్లా స్టోర్‌కు నివేదించాలి.")
        t3 = ""
    t4 = ""
    if notable:
        items = []
        for c in notable:
            if c["reason_code"] == "donor_safety_floor":
                items.append(f"{c['name']} ({c['distance_km']} కి.మీ.) - దాత భద్రతా తనిఖీలో తిరస్కరణ: వ్యాధి వ్యాప్తితో "
                             f"వినియోగం రోజుకు {c['burn_rate']} {unit_te}; {c['stock']} {unit_te} నిల్వ అంటే "
                             f"{c['days_of_cover']} రోజులు మాత్రమే, {floor} రోజుల భద్రతా పరిమితి కంటే తక్కువ.")
            else:
                items.append(f"{c['name']} ({c['distance_km']} కి.మీ.) - {REASON_TE.get(c['reason_code'], c['reason_code'])}.")
        t4 = "గమనించదగిన మినహాయింపులు (దగ్గరి నుండి): " + " ".join(items)
    t5 = "ఈ సిఫార్సు నిర్ణయ-సహాయం మాత్రమే; జిల్లా వైద్యాధికారి ఆమోదం తర్వాతే బదిలీ అమలు అవుతుంది."
    rationale_te = "\n\n".join(x for x in (t1, t2, t3, t4, t5) if x)

    # -- Risk notes ------------------------------------------------------- #
    notes = []
    if plan and plan["donors"]:
        ra = plan["recipient_after"]
        if ra["days_of_cover"] is not None:
            notes.append(f"After the transfer the recipient sits at {ra['days_of_cover']} days of cover, against a "
                         f"{T['warning_days']}-day warning line; if the outbreak surge continues beyond the "
                         f"{T['target_cover_days']}-day target it will need a further movement.")
        for d in plan["donors"]:
            if d["cover_margin_above_floor"] is not None:
                notes.append(f"{d['name']} keeps {d['cover_after']} days after giving ({d['cover_margin_above_floor']} above "
                             f"the {floor}-day floor); that figure already includes its own outbreak surge.")
        near = [l for l in plan["incoming_lots"] if l["near_expiry"]]
        for l in near:
            notes.append(f"Lot {l['batch']} from {l['from_name']} expires {l['expiry']} ({l['days_to_expiry']} days); it "
                         + ("is projected to be consumed in time." if l["consumed_in_time"]
                            else "may not be fully consumed before expiry."))
        notes.append(f"Transit is assumed at {T['transfer_transit_days']} days; the ledger treats the transfer as "
                     f"immediate once approved.")
    elif status == "no_need":
        notes.append("Re-check after the next IDSP report: the outbreak surge is already included in this figure, and a "
                     "larger surge would change it.")
    elif status == "no_burn_rate":
        notes.append("No clinical rule maps a disease with a caseload at this facility to this medicine; nothing here "
                     "is invented to fill the gap.")
    else:
        notes.append("No transfer is possible from the current eligible set; the shortage stands until district "
                     "resupply arrives.")
    rejections = [{"facility_id": c["facility_id"], "reason": c["reason"]} for c in notable]
    chosen = [{"facility_id": d["facility_id"], "quantity": d["qty"]} for d in (plan["donors"] if plan else [])]
    return {"chosen_donors": chosen, "rationale_en": rationale_en, "rationale_te": rationale_te,
            "risk_notes": notes, "notable_rejections": rejections}


# --------------------------------------------------------------------------- #
# Cache (same pattern as ingest_idsp: hash of the input, atomic write)
# --------------------------------------------------------------------------- #

def cache_key(doc):
    payload = json.dumps({"version": settings.MEMO_PROMPT_VERSION, "model": settings.GEMINI_MODEL,
                          "prompt": PROMPT, "input": doc}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_path(sha, cache_dir=None):
    return Path(cache_dir or settings.GEMINI_CACHE_DIR) / f"memo_{sha}.json"


def read_cache(sha, cache_dir=None):
    p = cache_path(sha, cache_dir)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        resp = payload["response"]
        if not isinstance(resp, dict):
            raise ValueError("'response' is not an object")
        return resp
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("memo cache file %s unreadable (%s) - ignoring it", p, e)
        return None


def write_cache(sha, response, doc, cache_dir=None):
    p = cache_path(sha, cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sha256": sha, "model": settings.GEMINI_MODEL, "prompt_version": settings.MEMO_PROMPT_VERSION,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recipient": doc["recipient"]["facility_id"], "medicine": doc["medicine"]["medicine_id"],
        "today": doc["today"], "response": response,
    }
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def _enrich(resp, ctx, plan):
    """Attach names, ranks and reason codes to the ids in a validated response."""
    by_order = {d["facility_id"]: d for d in (plan["donors"] if plan else [])}
    chosen = []
    for d in resp["chosen_donors"]:
        c = ctx.eligible_by_id.get(d["facility_id"], {})
        p = by_order.get(d["facility_id"], {})
        chosen.append({"facility_id": d["facility_id"], "name": c.get("name"), "sub_district": c.get("sub_district"),
                       "quantity": d["quantity"], "rank": c.get("rank"), "order": p.get("order"),
                       "distance_km": _num(c.get("distance_km"), 1)})
    rejections = []
    for r in resp["notable_rejections"]:
        c = ctx.rejected_by_id.get(r["facility_id"], {})
        rejections.append({"facility_id": r["facility_id"], "name": c.get("name"), "sub_district": c.get("sub_district"),
                           "distance_km": _num(c.get("distance_km"), 1), "reason_code": c.get("reason_code"),
                           "reason": r["reason"]})
    return chosen, rejections


def generate_memo(conn, result, today=None, call=None, cache_dir=None, outbreaks=None, allow_gemini=True):
    """The memo for one recommend() result. Never raises on a model problem:
    the worst case is a validated template.

    call: Gemini entry point (gemini_client.call_gemini unless a test injects
    one). allow_gemini=False skips the model entirely (template only).

    Returns {source, model, gemini_calls, cache_hit, close_call, chosen_donors,
    rationale_en, rationale_te, risk_notes, notable_rejections, plan,
    deviates_from_backend_plan, validation {passed, attempts, numerals,
    template_self_check}, input}."""
    today = today or date.today()
    ctx = MemoContext(conn, result, today, outbreaks)
    doc = ctx.input
    attempts = []
    out = {
        "source": None, "model": settings.GEMINI_MODEL, "gemini_calls": 0, "cache_hit": False, "cache_key": None,
        "close_call": {"is_close_call": ctx.is_close_call, "margin": settings.MEMO_CLOSE_CALL_MARGIN,
                       "selectable": [{"facility_id": c["facility_id"], "name": c["name"], "rank": c["rank"],
                                       "score_total": _num(c["score"]["total"], 3)}
                                      for c in result["eligible"] if c["facility_id"] in ctx.selectable]},
    }

    chosen_resp, plan, report = None, None, []
    actionable = result["status"] in ("ok", "partial") and bool(result["recommendation"]["donors"])
    if actionable and allow_gemini:
        sha = cache_key(doc)
        out["cache_key"] = sha
        cached = read_cache(sha, cache_dir)
        if cached is not None:
            ok, failures, plan, report = validate_response(cached, ctx)
            attempts.append({"attempt": 0, "source": SOURCE_CACHE, "ok": ok, "failures": failures})
            if ok:
                chosen_resp, out["source"], out["cache_hit"] = cached, SOURCE_CACHE, True
                log.info("memo cache hit (%s...): validated, Gemini not called", sha[:12])
            else:
                log.warning("cached memo %s... no longer validates (%s) - regenerating", sha[:12], "; ".join(failures))
        if chosen_resp is None:
            if not os.environ.get("GEMINI_API_KEY") and call is None:
                log.warning("GEMINI_API_KEY is not set - memo falls back to the template")
                attempts.append({"attempt": 1, "source": SOURCE_GEMINI, "ok": False,
                                 "failures": ["GEMINI_API_KEY is not set"]})
            else:
                call = call or gemini_client.call_gemini
                contents = PROMPT + json.dumps(doc, ensure_ascii=False, indent=1)
                for attempt in (1, 2):
                    out["gemini_calls"] += 1
                    try:
                        resp = call(contents, RESPONSE_SCHEMA)
                    except Exception as e:  # the client has already retried 503/429
                        failures = [f"Gemini call failed: {type(e).__name__}: {e}"]
                        attempts.append({"attempt": attempt, "source": SOURCE_GEMINI, "ok": False, "failures": failures})
                        log.warning("memo attempt %d: %s", attempt, failures[0])
                        break
                    ok, failures, plan, report = validate_response(resp, ctx)
                    attempts.append({"attempt": attempt, "source": SOURCE_GEMINI, "ok": ok, "failures": failures})
                    if ok:
                        chosen_resp, out["source"] = resp, SOURCE_GEMINI
                        write_cache(sha, resp, doc, cache_dir)
                        break
                    log.warning("memo attempt %d REJECTED (%d problem%s): %s", attempt, len(failures),
                                "" if len(failures) == 1 else "s", " | ".join(failures))
                    plan = None

    # Template: always validated by the same code path. It must pass; if it
    # ever does not, that is a bug in this file, reported loudly but the memo
    # is still returned - a recommendation never goes out without one.
    template = template_response(doc)
    t_ok, t_failures, t_plan, t_report = validate_response(template, ctx) if actionable else (True, [], None, [])
    if not t_ok:
        log.error("TEMPLATE memo failed its own validation: %s", "; ".join(t_failures))
    if chosen_resp is None:
        chosen_resp, plan, report = template, (t_plan or result["recommendation"]), t_report
        out["source"] = SOURCE_TEMPLATE
        if actionable:
            log.info("memo: using the deterministic template (%s)", "; ".join(
                f"attempt {a['attempt']} {a['source']}: {'ok' if a['ok'] else a['failures'][0]}" for a in attempts) or "Gemini not attempted")

    chosen, rejections = _enrich(chosen_resp, ctx, plan)
    backend_ids = [d["facility_id"] for d in result["recommendation"]["donors"]] if result["recommendation"] else []
    out.update({
        "chosen_donors": chosen, "rationale_en": chosen_resp["rationale_en"], "rationale_te": chosen_resp["rationale_te"],
        "risk_notes": list(chosen_resp["risk_notes"]), "notable_rejections": rejections,
        "plan": plan, "deviates_from_backend_plan": [d["facility_id"] for d in chosen] != backend_ids,
        "validation": {"passed": out["source"] != SOURCE_TEMPLATE or t_ok, "attempts": attempts, "numerals": report,
                       "template_self_check": {"ok": t_ok, "failures": t_failures}},
        "input": doc,
    })
    return out


# --------------------------------------------------------------------------- #
# Verification (Phase 9 acceptance, PROJECT_CONTEXT.md sec 11)
# --------------------------------------------------------------------------- #

def _fake_call(responses):
    it = iter(responses)

    def call(contents, schema):
        call.calls += 1
        return next(it)
    call.calls = 0
    return call


def print_memo(memo, width=100):
    import textwrap
    print(f"  source: {memo['source']}   model: {memo['model']}   gemini_calls: {memo['gemini_calls']}   "
          f"cache_hit: {memo['cache_hit']}   close call: {memo['close_call']['is_close_call']} "
          f"(selectable: {', '.join(s['name'] for s in memo['close_call']['selectable'])})")
    print("  chosen donors: " + "; ".join(f"{d['name']} ({d['facility_id']}) {d['quantity']} [rank {d['rank']}]"
                                          for d in memo["chosen_donors"])
          + ("   <- differs from the backend's own plan" if memo["deviates_from_backend_plan"] else "   (= backend plan)"))
    print("\n  --- rationale_en ---")
    for para in memo["rationale_en"].split("\n"):
        print(textwrap.fill(para, width, initial_indent="  ", subsequent_indent="  ") if para.strip() else "")
    print("\n  --- rationale_te ---")
    for para in memo["rationale_te"].split("\n"):
        print(("  " + para) if para.strip() else "")
    print("\n  --- risk_notes ---")
    for n in memo["risk_notes"]:
        print(textwrap.fill(n, width, initial_indent="  - ", subsequent_indent="    "))
    print("\n  --- notable_rejections ---")
    for r in memo["notable_rejections"]:
        print(textwrap.fill(f"{r['name']} ({r['distance_km']} km, {r['reason_code']}): {r['reason']}", width,
                            initial_indent="  - ", subsequent_indent="    "))


def print_hand_check(memo):
    """Every numeral and identifier the validator extracted from the memo
    text, with the input value it matched - the hand verification."""
    v = memo["validation"]
    rows = v["numerals"]
    print(f"  {len(rows)} tokens extracted from the memo text; all matched: {all(r['matched'] for r in rows)}")
    print(f"  {'field':<30} {'token':<22} {'kind':<10} {'matched':<8} {'input value':<14} source in input")
    for r in rows:
        print(f"  {r['field']:<30} {r['token']:<22} {r['kind']:<10} {'yes' if r['matched'] else 'NO':<8} "
              f"{str(r.get('source_value') if r.get('source_value') is not None else '-'):<14} {r['source'] or '-'}")
    distinct = {}
    for r in rows:
        distinct.setdefault((r["token"], r["kind"]), r)
    print(f"  distinct tokens: {len(distinct)}; unmatched: {[k[0] for k, r in distinct.items() if not r['matched']]}")
    return all(r["matched"] for r in rows)


def corruption_checks(conn, result, base_response, today, check):
    """Deliberately corrupted responses must be rejected and the template
    must fire. base_response is a VALID response (Gemini's or the template's)."""
    with tempfile.TemporaryDirectory() as tmp:
        ctx = MemoContext(conn, result, today)
        ok, failures, _, _ = validate_response(base_response, ctx)
        check(ok, f"the base response validates cleanly ({len(failures)} problems)")
        plan_qty = result["recommendation"]["donors"][0]["qty"]

        cases = []
        bad_qty = json.loads(json.dumps(base_response))
        bad_qty["chosen_donors"][0]["quantity"] = plan_qty + 23
        cases.append(("wrong quantity injected", bad_qty, "quantity"))

        absent = Decimal("25")  # a plausible-looking figure that is provably not in the input
        while ctx.lookup(absent):
            absent += 1
        bad_num = json.loads(json.dumps(base_response))
        bad_num["rationale_en"] = bad_num["rationale_en"] + f" The donor will still have about {absent} days of stock."
        cases.append((f"hallucinated number {absent} injected into rationale_en", bad_num, "does not match"))

        bad_fac = json.loads(json.dumps(base_response))
        bad_fac["chosen_donors"] = [{"facility_id": "phc_nowhere_000000", "quantity": plan_qty}]
        cases.append(("facility not in the candidate set", bad_fac, "not in the supplied candidate set"))

        eligible_outside = [c for c in result["eligible"] if c["facility_id"] not in ctx.selectable]
        if eligible_outside:
            bad_close = json.loads(json.dumps(base_response))
            bad_close["chosen_donors"] = [{"facility_id": eligible_outside[0]["facility_id"],
                                           "quantity": min(result["recipient"]["need_units"], eligible_outside[0]["spare_units"])}]
            cases.append(("eligible donor outside the close-call set", bad_close, "outside the close-call set"))

        bad_te = json.loads(json.dumps(base_response))
        bad_te["rationale_te"] = bad_te["rationale_en"]
        cases.append(("Telugu rationale written in English", bad_te, "Telugu script"))

        for label, resp, needle in cases:
            ok, failures, _, _ = validate_response(resp, ctx)
            hit = any(needle in f for f in failures)
            check(not ok and hit, f"{label}: rejected - " + (failures[0] if failures else "no failure recorded"))

        fake = _fake_call([bad_qty, bad_num])
        memo = generate_memo(conn, result, today=today, call=fake, cache_dir=tmp)
        check(memo["source"] == SOURCE_TEMPLATE and fake.calls == 2 and memo["gemini_calls"] == 2,
              f"corrupted responses twice -> template fallback fired after {memo['gemini_calls']} calls "
              f"(source={memo['source']})")
        check(not cache_path(cache_key(memo["input"]), tmp).exists(), "rejected responses were not cached")
        check(memo["validation"]["template_self_check"]["ok"], "template passed the same validator")
        check([d["facility_id"] for d in memo["chosen_donors"]] == [d["facility_id"] for d in result["recommendation"]["donors"]]
              and sum(d["quantity"] for d in memo["chosen_donors"]) == result["recommendation"]["total_qty"],
              f"template memo carries the backend's plan: {[(d['name'], d['quantity']) for d in memo['chosen_donors']]}")
        return memo


def adjudication_checks(conn, result, base_response, today, check):
    """Gemini may reorder donors inside the close-call set; the backend then
    recomputes every quantity from that order. Exercised with fake responses
    so the path is proven whatever the live model decides."""
    ctx = MemoContext(conn, result, today)
    plan_ids = [d["facility_id"] for d in result["recommendation"]["donors"]]
    others = [i for i in ctx.selectable if i not in plan_ids]
    if not others:
        print("  (no close call in this recommendation - nothing to adjudicate; skipping)")
        return
    alt = ctx.eligible_by_id[others[0]]
    qty = min(result["recipient"]["need_units"], alt["spare_units"])
    resp = json.loads(json.dumps(base_response))
    resp["chosen_donors"] = [{"facility_id": alt["facility_id"], "quantity": qty}]
    ok, failures, plan, _ = validate_response(resp, ctx)
    check(ok and plan and [d["facility_id"] for d in plan["donors"]] == [alt["facility_id"]]
          and plan["donors"][0]["qty"] == qty and plan["total_qty"] == result["recommendation"]["total_qty"],
          f"runner-up {alt['name']} (rank {alt['rank']}, score {alt['score']['total']:.3f}) chosen first: accepted, "
          f"backend re-allocated {qty} {ctx.unit}, donor keeps {plan['donors'][0]['cover_after']:.1f} d" if ok else
          f"runner-up choice rejected: {failures}")
    with tempfile.TemporaryDirectory() as tmp:
        memo = generate_memo(conn, result, today=today, call=_fake_call([resp]), cache_dir=tmp)
        check(memo["source"] == SOURCE_GEMINI and memo["deviates_from_backend_plan"]
              and [d["facility_id"] for d in memo["chosen_donors"]] == [alt["facility_id"]]
              and memo["plan"]["donors"][0]["floor_check"]["passes"],
              f"memo carries the adjudicated plan ({memo['chosen_donors'][0]['name']} {memo['chosen_donors'][0]['quantity']}), "
              f"deviates_from_backend_plan={memo['deviates_from_backend_plan']}, donor floor check passes")

    # A split need: the order decides the quantities, the model does not.
    original = settings.TARGET_COVER_DAYS
    settings.TARGET_COVER_DAYS = 30
    try:
        split = recommend(conn, result["recipient"]["facility_id"], result["recipient"]["medicine_id"], today=today)
    finally:
        settings.TARGET_COVER_DAYS = original
    if not split["recommendation"]["split"]:
        print("  (no split at TARGET_COVER_DAYS=30; skipping the split check)")
        return
    sctx = MemoContext(conn, split, today)
    sel = [sctx.eligible_by_id[i] for i in sctx.selectable]
    order = list(reversed(sel))  # draw on the close-call set in reverse order
    donors, remaining = allocate(split["recipient"], order, sctx.unit, n_eligible=len(split["eligible"]))
    stated = [{"facility_id": d["facility_id"], "quantity": d["qty"]} for d in donors]
    tmpl = template_response(sctx.input)
    good = {**tmpl, "chosen_donors": stated}
    ok, failures, plan, _ = validate_response(good, sctx)
    check(ok and plan["total_qty"] == split["recommendation"]["total_qty"],
          f"split need {split['recipient']['need_units']} in reverse order accepted: "
          + ", ".join(f"{d['name']} {d['qty']}" for d in donors) + f" (total {plan['total_qty'] if ok else '-'})")
    bad = json.loads(json.dumps(good))
    if len(bad["chosen_donors"]) > 1:
        bad["chosen_donors"][0]["quantity"] -= 10
        bad["chosen_donors"][1]["quantity"] += 10
        ok, failures, _, _ = validate_response(bad, sctx)
        check(not ok and any("the backend computed" in f for f in failures),
              "same total but quantities shifted between donors: rejected - " + (failures[0] if failures else ""))
    extra = json.loads(json.dumps(good))
    spare_id = next((c["facility_id"] for c in split["eligible"] if c["facility_id"] not in {d["facility_id"] for d in stated}
                     and c["facility_id"] in sctx.selectable), None)
    if spare_id:
        extra["chosen_donors"].append({"facility_id": spare_id, "quantity": 1})
        ok, failures, _, _ = validate_response(extra, sctx)
        check(not ok, "a donor appended after the need is met: rejected - " + (failures[0] if failures else ""))


def no_key_check(conn, result, today, check):
    """Remove GEMINI_API_KEY from the environment; the memo must still work."""
    saved = os.environ.pop("GEMINI_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            memo = generate_memo(conn, result, today=today, cache_dir=tmp)
            check(memo["source"] == SOURCE_TEMPLATE and memo["gemini_calls"] == 0,
                  f"without GEMINI_API_KEY: source={memo['source']}, gemini_calls={memo['gemini_calls']}")
            check(len(TELUGU_RE.findall(memo["rationale_te"])) >= MIN_TELUGU_CHARS and memo["rationale_en"],
                  "templated memo has both languages")
            check(memo["validation"]["template_self_check"]["ok"], "templated memo passes the numeric cross-check")
            return memo
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved


def utf8_stdout():
    """Telugu on a Windows console: the default cp1252 stream cannot encode it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("backend.queries", "google_genai", "httpx", "backend.outbreak_demand"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    from backend.ingest_idsp import ingest
    today = date.today()
    failures = []

    def check(cond, msg):
        print(f"  {'OK' if cond else 'FAIL'}: {msg}")
        if not cond:
            failures.append(msg)

    with get_connection() as conn:
        create_tables(conn)
        r = ingest(settings.DEMO_IDSP_PDF, conn)
        print(f"ingest {r['source_file']}: ok={r['ok']} cache_hit={r['cache_hit']} gemini_calls={r['gemini_calls']}")
        rid, mid = settings.DEMO_TARGET_FACILITY_ID, "ors_packets"
        result = recommend(conn, rid, mid, today=today)
        print(f"recommend {rid}/{mid}: status {result['status']}, eligible {len(result['eligible'])}, "
              f"rejected {len(result['rejected'])}, backend plan "
              + ", ".join(f"{d['name']} {d['qty']}" for d in result["recommendation"]["donors"]))

        print("\n=== 1. Memo (Gemini if reachable, else template) ===")
        memo = generate_memo(conn, result, today=today)
        print_memo(memo)
        check(memo["source"] in (SOURCE_GEMINI, SOURCE_CACHE, SOURCE_TEMPLATE), f"memo produced (source {memo['source']})")
        check(memo["rationale_en"] and len(TELUGU_RE.findall(memo["rationale_te"])) >= MIN_TELUGU_CHARS,
              "both languages present")
        check(sum(d["quantity"] for d in memo["chosen_donors"]) == result["recommendation"]["total_qty"],
              f"chosen quantities sum to the backend total {result['recommendation']['total_qty']}")
        for a in memo["validation"]["attempts"]:
            print(f"  attempt {a['attempt']} ({a['source']}): {'ok' if a['ok'] else 'REJECTED - ' + '; '.join(a['failures'])}")

        print("\n=== 2. Hand verification: every number in the memo against the input ===")
        check(print_hand_check(memo), "every numeral/identifier in the memo matches a backend-computed value")

        if memo["source"] != SOURCE_TEMPLATE:
            print("\n=== 2b. Second call for the same input is served from cache ===")
            memo2 = generate_memo(conn, result, today=today)
            check(memo2["cache_hit"] and memo2["gemini_calls"] == 0 and memo2["rationale_en"] == memo["rationale_en"],
                  f"cache hit, zero Gemini calls (source {memo2['source']})")

        print("\n=== 3. Corrupted Gemini responses are rejected; template fallback fires ===")
        base = {k: memo[k] for k in ("rationale_en", "rationale_te", "risk_notes")}
        base["chosen_donors"] = [{"facility_id": d["facility_id"], "quantity": d["quantity"]} for d in memo["chosen_donors"]]
        base["notable_rejections"] = [{"facility_id": r["facility_id"], "reason": r["reason"]} for r in memo["notable_rejections"]]
        corruption_checks(conn, result, base, today, check)

        print("\n=== 3b. Close-call adjudication: the model picks the order, the backend sets every quantity ===")
        adjudication_checks(conn, result, base, today, check)

        print("\n=== 4. Deleting the API key still yields a working templated memo ===")
        tmemo = no_key_check(conn, result, today, check)
        print_memo(tmemo)
        print("\n  hand check of the template:")
        check(print_hand_check(tmemo), "template: every numeral matches")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'CHECKS FAILED'}"
          + ("" if not failures else ":\n  - " + "\n  - ".join(failures)))
    return not failures


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
