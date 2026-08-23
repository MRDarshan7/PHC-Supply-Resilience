"""IDSP weekly outbreak report PDF -> validated outbreak records
(PROJECT_CONTEXT.md sec 5 Job 1, sec 11 Phase 7).

    py backend/ingest_idsp.py [path/to/idsp_YYYY_wWW.pdf]   # default: DEMO_IDSP_PDF

What happens to one PDF, in order:

  1. Extract. The file is hashed (SHA-256). If GEMINI_CACHE_DIR holds a
     response for that hash it is used and Gemini is NOT called. Otherwise
     the PDF bytes go to Gemini through config.gemini_client.call_gemini
     (structured JSON output, RESPONSE_SCHEMA below) and the raw response is
     written to the cache before anything else happens. A malformed response
     (not a list holding at least one object) is retried once, then the ingest
     fails cleanly - nothing is cached, nothing is stored.
  2. Cross-check. The file name (idsp_<year>_w<week>.pdf) is parsed and
     compared with the week/year Gemini returned for every row. Mismatches
     are logged, never silently corrected; the document's values are stored.
  3. Validate every row (validate_row). Three outcomes:
       REJECT (logged, not stored): outbreak_id / district / disease missing,
         cases not a positive integer, week or year unusable, duplicate id.
       STORE + FLAG out_of_scope: the district does not exist in the
         facilities table for TARGET_STATE. Recorded for completeness;
         nothing downstream allocates it.
       STORE + FLAG unknown_disease: the disease label does not match a
         rules.yaml disease_aliases entry exactly (case and whitespace
         insensitive - NO fuzzy matching, the rule table is curated and
         guessing a clinical relationship is unacceptable). disease_key is
         NULL and no surge is ever applied to the row.
  4. Store. Validated rows replace any earlier rows from the same source file
     in the outbreaks table, so re-running never duplicates.

Gemini's role ends at step 1: it reports what the document says. It has no
say in which rows are stored, which medicines a disease maps to, or any
quantity downstream (sec 6). Nothing here raises on bad input - ingest()
returns a result whose "ok" / "error" fields say what happened.
"""

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from google.genai import types  # noqa: E402

from config import gemini_client, settings  # noqa: E402
from backend.db import create_tables, get_connection  # noqa: E402
from backend.rules import disease_aliases, load_rules  # noqa: E402

log = logging.getLogger(__name__)

FILENAME_RE = re.compile(r"idsp_(\d{4})_w(\d{1,2})\.pdf$", re.IGNORECASE)
PDF_MAGIC = b"%PDF-"
FLAG_OUT_OF_SCOPE = "out_of_scope"
FLAG_UNKNOWN_DISEASE = "unknown_disease"

# Output schema (PROJECT_CONTEXT.md sec 5) - verified in Phase 1: 44/44 rows,
# zero hallucinated numbers. Field names are the outbreaks table's columns.
RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "outbreak_id": {"type": "STRING"},
            "state": {"type": "STRING"},
            "district": {"type": "STRING"},
            "sub_district": {"type": "STRING"},
            "disease": {"type": "STRING"},
            "cases": {"type": "INTEGER"},
            "deaths": {"type": "INTEGER"},
            "week": {"type": "INTEGER"},
            "year": {"type": "INTEGER"},
            "status": {"type": "STRING"},
        },
        "required": ["outbreak_id", "state", "district", "sub_district", "disease",
                     "cases", "deaths", "week", "year", "status"],
    },
}

# The Phase 1 prompt, verbatim - it is the thing that was verified to work.
PROMPT = """Extract every outbreak row from this IDSP weekly outbreak report PDF.

There are two tables to extract from:
1. The main weekly outbreak table.
2. The "DISEASE OUTBREAKS OF PREVIOUS WEEKS REPORTED LATE" table at the end of the document.

Combine rows from both tables into a single flat list.

For each row, extract: outbreak_id, state, district, sub_district, disease, cases, deaths, week, year, status.

sub_district is usually not a separate column in the table. Extract it from the
Comments column text, which typically reads something like "Sub-District X, District Y".
If no sub-district is mentioned in the comments, return an empty string for sub_district.
"""

UPSERT = """
INSERT INTO outbreaks (outbreak_id, state, district, sub_district, disease, cases, deaths,
                       week, year, status, source_file, in_scope, disease_key, review_flags)
VALUES (:outbreak_id, :state, :district, :sub_district, :disease, :cases, :deaths,
        :week, :year, :status, :source_file, :in_scope, :disease_key, :review_flags)
ON CONFLICT(outbreak_id) DO UPDATE SET
    state = excluded.state, district = excluded.district, sub_district = excluded.sub_district,
    disease = excluded.disease, cases = excluded.cases, deaths = excluded.deaths,
    week = excluded.week, year = excluded.year, status = excluded.status,
    source_file = excluded.source_file, in_scope = excluded.in_scope,
    disease_key = excluded.disease_key, review_flags = excluded.review_flags
"""


class IngestError(Exception):
    """A problem that stops this ingest (bad file, unusable Gemini response).
    Always caught inside ingest() and reported in the result, never raised out.
    `info` carries whatever extract() knew at the time (sha256, gemini_calls)."""

    def __init__(self, message, info=None):
        super().__init__(message)
        self.info = info or {}


# --------------------------------------------------------------------------- #
# 1. Extraction, with the disk cache in front of Gemini
# --------------------------------------------------------------------------- #

def file_sha256(data):
    return hashlib.sha256(data).hexdigest()


def parse_filename(path):
    """(year, week) from idsp_<year>_w<week>.pdf, or (None, None) when the
    name does not follow the pattern or the week is outside 1-53."""
    m = FILENAME_RE.search(Path(path).name)
    if not m or not 1 <= int(m.group(2)) <= 53:
        return None, None
    return int(m.group(1)), int(m.group(2))


def cache_path(sha, cache_dir=None):
    return Path(cache_dir or settings.GEMINI_CACHE_DIR) / f"idsp_{sha}.json"


def read_cache(sha, cache_dir=None):
    """The cached row list for this file hash, or None. A cache file that does
    not hold a list is treated as a miss and logged (it will be re-extracted)."""
    p = cache_path(sha, cache_dir)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload["rows"]
        if not isinstance(rows, list):
            raise ValueError("'rows' is not a list")
        return rows
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("cache file %s unreadable (%s) - ignoring it", p, e)
        return None


def write_cache(sha, rows, source_file, cache_dir=None):
    """Write atomically (temp file + replace) so a crash mid-write never
    leaves a half-written cache entry that would later read as corrupt."""
    p = cache_path(sha, cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sha256": sha, "source_file": source_file, "model": settings.GEMINI_MODEL,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows,
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


def response_problem(response):
    """None when the response is usable (a list holding at least one object),
    else a description of what is wrong with it. Individual non-object items
    in an otherwise usable list are rejected row by row in validate_row."""
    if not isinstance(response, list):
        return f"expected a JSON array, got {type(response).__name__}"
    if not response:
        return "empty array - no outbreak rows extracted"
    if not any(isinstance(r, dict) for r in response):
        return f"none of the {len(response)} items is an object"
    return None


def extract(pdf_path, call=None, cache_dir=None):
    """Raw rows for one PDF plus {"sha256", "cache_hit", "gemini_calls"}.

    `call` is the Gemini entry point - gemini_client.call_gemini unless a
    test injects something else. Raises IngestError on a missing / non-PDF
    file or an unusable response (after one retry). Successful responses are
    cached before returning; failures are never cached."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise IngestError(f"file not found: {pdf_path}")
    data = pdf_path.read_bytes()
    if not data.startswith(PDF_MAGIC):
        raise IngestError(f"not a PDF (no %PDF- header, {len(data)} bytes): {pdf_path.name}")
    sha = file_sha256(data)
    info = {"sha256": sha, "cache_hit": False, "gemini_calls": 0}

    cached = read_cache(sha, cache_dir)
    if cached is not None:
        info["cache_hit"] = True
        log.info("cache hit for %s (sha256 %s...): %d rows, Gemini not called", pdf_path.name, sha[:12], len(cached))
        return cached, info

    call = call or gemini_client.call_gemini
    contents = [types.Part.from_bytes(data=data, mime_type="application/pdf"), PROMPT]
    problem = None
    for attempt in (1, 2):
        info["gemini_calls"] += 1
        log.info("calling Gemini (%s) for %s, attempt %d", settings.GEMINI_MODEL, pdf_path.name, attempt)
        try:
            response = call(contents, RESPONSE_SCHEMA)
        except Exception as e:  # the client has already retried 503/429; anything left is final
            raise IngestError(f"Gemini call failed: {type(e).__name__}: {e}", info) from e
        problem = response_problem(response)
        if problem is None:
            write_cache(sha, response, pdf_path.name, cache_dir)
            log.info("cached %d rows for %s at %s", len(response), pdf_path.name, cache_path(sha, cache_dir))
            return response, info
        log.warning("Gemini response for %s is malformed (%s) - %s", pdf_path.name, problem,
                    "retrying once" if attempt == 1 else "giving up")
    raise IngestError(f"Gemini response malformed after {info['gemini_calls']} attempts: {problem}", info)


# --------------------------------------------------------------------------- #
# 2/3. Validation
# --------------------------------------------------------------------------- #

def _norm(s):
    """Case- and whitespace-insensitive comparison key."""
    return " ".join(str(s if s is not None else "").split()).lower()


def _text(v):
    return " ".join(str(v).split()) if v is not None else ""


def as_int(v, minimum):
    """v as an int >= minimum, else None. Accepts int, integral float and
    digit strings; rejects bool, fractions, values below minimum, text."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, float) and v.is_integer():
        n = int(v)
    elif isinstance(v, str) and v.strip().lstrip("-").isdigit():
        n = int(v.strip())
    else:
        return None
    return n if n >= minimum else None


def known_districts(conn, state=None):
    """{normalised district name} present in the facilities table for the
    target state - the in-scope test for an outbreak's district."""
    state = state or settings.TARGET_STATE
    return {_norm(r["district"]) for r in conn.execute(
        "SELECT DISTINCT district FROM facilities WHERE state = ?", (state,))}


def alias_table(rules=None, aliases=None):
    """{normalised IDSP label: rules.yaml disease key}, dropping aliases that
    point at a disease the rule table does not define (logged - a config error)."""
    rules = rules if rules is not None else load_rules()
    aliases = aliases if aliases is not None else disease_aliases()
    table = {}
    for label, key in aliases.items():
        if key not in rules:
            log.warning("rules.yaml: disease_aliases %r -> %r but no %r rule block exists; alias ignored", label, key, key)
            continue
        table[_norm(label)] = key
    return table


def match_disease(label, aliases):
    """rules.yaml disease key for an IDSP disease label, or None. Exact match
    on the normalised label only - never fuzzy, never guessed."""
    return aliases.get(_norm(label))


def classify(state, district, disease, districts, aliases):
    """(in_scope, disease_key, flags) for one outbreak - the same verdict at
    ingest time and whenever a consumer re-checks a stored row."""
    in_scope = _norm(state) == _norm(settings.TARGET_STATE) and _norm(district) in districts
    disease_key = match_disease(disease, aliases)
    flags = []
    if not in_scope:
        flags.append(FLAG_OUT_OF_SCOPE)
    if disease_key is None:
        flags.append(FLAG_UNKNOWN_DISEASE)
    return in_scope, disease_key, flags


def validate_row(raw, source_file, filename_year, filename_week, districts, aliases):
    """(record, None) when the row is storable, (None, reason) when rejected.
    record carries every outbreaks column plus a 'flags' list."""
    if not isinstance(raw, dict):
        return None, f"row is not an object ({type(raw).__name__})"
    outbreak_id = _text(raw.get("outbreak_id"))
    if not outbreak_id:
        return None, "outbreak_id missing"
    district = _text(raw.get("district"))
    if not district:
        return None, f"{outbreak_id}: district missing"
    disease = _text(raw.get("disease"))
    if not disease:
        return None, f"{outbreak_id}: disease missing"
    cases = as_int(raw.get("cases"), minimum=1)
    if cases is None:
        return None, f"{outbreak_id}: cases must be a positive integer, got {raw.get('cases')!r}"

    deaths = as_int(raw.get("deaths"), minimum=0)
    if deaths is None and raw.get("deaths") not in (None, ""):
        log.warning("%s: deaths %r is not a non-negative integer - stored as NULL", outbreak_id, raw.get("deaths"))

    year = as_int(raw.get("year"), minimum=2000)
    week = as_int(raw.get("week"), minimum=1)
    if week is not None and week > 53:
        week = None
    if year is None or week is None:
        if filename_year is None or filename_week is None:
            return None, (f"{outbreak_id}: week/year unusable ({raw.get('week')!r}/{raw.get('year')!r}) "
                          f"and file name carries none")
        log.warning("%s: week/year unusable (%r/%r) - using file name's %d/w%d",
                    outbreak_id, raw.get("week"), raw.get("year"), filename_year, filename_week)
        year, week = filename_year, filename_week

    state = _text(raw.get("state"))
    in_scope, disease_key, flags = classify(state, district, disease, districts, aliases)
    record = {
        "outbreak_id": outbreak_id, "state": state, "district": district,
        "sub_district": _text(raw.get("sub_district")), "disease": disease,
        "cases": cases, "deaths": deaths, "week": week, "year": year, "status": _text(raw.get("status")),
        "source_file": source_file, "in_scope": int(in_scope), "disease_key": disease_key,
        "review_flags": ",".join(flags) or None, "flags": flags,
    }
    return record, None


def validate_rows(rows, source_file, filename_year, filename_week, districts, aliases):
    """Validate every extracted row. Returns (records, rejected, mismatches):
    rejected = [(raw_row, reason)], mismatches = [(outbreak_id, year, week)]
    for rows whose week/year differ from the file name's."""
    records, rejected, mismatches, seen = [], [], [], set()
    for raw in rows:
        record, reason = validate_row(raw, source_file, filename_year, filename_week, districts, aliases)
        if record is None:
            log.warning("rejected row: %s", reason)
            rejected.append((raw, reason))
            continue
        if record["outbreak_id"] in seen:
            reason = f"{record['outbreak_id']}: duplicate outbreak_id in this response"
            log.warning("rejected row: %s", reason)
            rejected.append((raw, reason))
            continue
        seen.add(record["outbreak_id"])
        if filename_year is not None and (record["year"], record["week"]) != (filename_year, filename_week):
            log.warning("%s: document says %d/w%d, file name says %d/w%d - storing the document's values",
                        record["outbreak_id"], record["year"], record["week"], filename_year, filename_week)
            mismatches.append((record["outbreak_id"], record["year"], record["week"]))
        where = f"{record['outbreak_id']} ({record['state']} / {record['district']} / {record['disease']}, {record['cases']} cases)"
        if FLAG_OUT_OF_SCOPE in record["flags"]:
            log.debug("flagged %s: %s", ",".join(record["flags"]), where)
        elif FLAG_UNKNOWN_DISEASE in record["flags"]:
            log.warning("in scope but disease not in rules.yaml - stored, NO surge, needs review: %s", where)
        else:
            log.info("in scope, matched %s: %s", record["disease_key"], where)
        records.append(record)
    return records, rejected, mismatches


# --------------------------------------------------------------------------- #
# 4. Store
# --------------------------------------------------------------------------- #

def store(conn, records, source_file):
    """Replace this source file's rows with `records`. One transaction."""
    with conn:
        conn.execute("DELETE FROM outbreaks WHERE source_file = ?", (source_file,))
        conn.executemany(UPSERT, records)


def ingest(pdf_path, conn, call=None, cache_dir=None):
    """Run the whole pipeline for one PDF. Never raises on bad input: the
    result's "ok" is False and "error" says why. Keys:
    source_file, ok, error, sha256, cache_hit, gemini_calls, filename_year,
    filename_week, extracted, stored, rejected [(raw, reason)],
    week_year_mismatches [(id, year, week)], records [stored records],
    out_of_scope, unknown_disease."""
    pdf_path = Path(pdf_path)
    result = {
        "source_file": pdf_path.name, "ok": False, "error": None, "sha256": None,
        "cache_hit": False, "gemini_calls": 0, "filename_year": None, "filename_week": None,
        "extracted": 0, "stored": 0, "rejected": [], "week_year_mismatches": [], "records": [],
        "out_of_scope": 0, "unknown_disease": 0,
    }
    result["filename_year"], result["filename_week"] = parse_filename(pdf_path)
    if result["filename_year"] is None:
        log.warning("%s: file name does not match idsp_<year>_w<week>.pdf - week/year cross-check skipped",
                    pdf_path.name)
    try:
        rows, info = extract(pdf_path, call=call, cache_dir=cache_dir)
    except IngestError as e:
        log.error("ingest of %s failed: %s", pdf_path.name, e)
        result.update(e.info)
        result["error"] = str(e)
        return result
    result.update(info)
    result["extracted"] = len(rows)

    districts = known_districts(conn)
    aliases = alias_table()
    records, rejected, mismatches = validate_rows(
        rows, pdf_path.name, result["filename_year"], result["filename_week"], districts, aliases)
    store(conn, records, pdf_path.name)

    result.update(ok=True, stored=len(records), rejected=rejected, week_year_mismatches=mismatches,
                  records=records,
                  out_of_scope=sum(1 for r in records if FLAG_OUT_OF_SCOPE in r["flags"]),
                  unknown_disease=sum(1 for r in records if FLAG_UNKNOWN_DISEASE in r["flags"]))
    log.info("%s: %d extracted, %d stored (%d out of scope, %d unknown disease), %d rejected, "
             "%d week/year mismatches, cache_hit=%s, gemini_calls=%d",
             pdf_path.name, result["extracted"], result["stored"], result["out_of_scope"],
             result["unknown_disease"], len(rejected), len(mismatches), result["cache_hit"], result["gemini_calls"])
    return result


# --------------------------------------------------------------------------- #
# Verification (Phase 7 Part A acceptance, PROJECT_CONTEXT.md sec 11)
# --------------------------------------------------------------------------- #

# The demo outbreak exactly as verified against the PDF (PROJECT_CONTEXT.md sec 7).
EXPECTED_GUNTUR = {
    "outbreak_id": "AP/GUN/2025/45/1997", "state": "Andhra Pradesh", "district": "Guntur",
    "sub_district": "Thullur", "disease": "Acute Diarrhoeal Disease", "cases": 457, "deaths": 0,
    "week": 45, "year": 2025, "status": "Under Control",
}
EXPECTED_RECORDS = 44


class ForbidGemini:
    """Stand-in for call_gemini that must never be reached. Counts attempts."""

    def __init__(self):
        self.calls = 0

    def __call__(self, contents, schema):
        self.calls += 1
        raise AssertionError("Gemini was called - the cache did not serve this request")


def _fake_call(responses):
    """A call_gemini stand-in returning each of `responses` in turn."""
    it = iter(responses)

    def call(contents, schema):
        call.calls += 1
        return next(it)
    call.calls = 0
    return call


def _print_result(r):
    print(f"  file: {r['source_file']}  sha256: {(r['sha256'] or '')[:16]}...  "
          f"cache_hit={r['cache_hit']}  gemini_calls={r['gemini_calls']}")
    if not r["ok"]:
        print(f"  FAILED cleanly: {r['error']}")
        return
    print(f"  file name says year={r['filename_year']} week={r['filename_week']}; "
          f"rows agreeing: {r['stored'] - len(r['week_year_mismatches'])} / {r['stored']}"
          + (f"; mismatches: {r['week_year_mismatches']}" if r["week_year_mismatches"] else "; no mismatches"))
    print(f"  extracted {r['extracted']}  stored {r['stored']}  rejected {len(r['rejected'])}  "
          f"out_of_scope {r['out_of_scope']}  unknown_disease {r['unknown_disease']}")
    for raw, reason in r["rejected"]:
        print(f"    rejected: {reason}")




def _malformed_checks(tmp, tconn, conn, check):
    """Part 3 of main(): malformed inputs. tconn is a scratch database so no
    fake row ever reaches the real ledger; tmp is a scratch cache directory."""
    tconn.execute("INSERT INTO facilities VALUES ('x', 'X', 'phc', 'Guntur', 'T', ?, 16.0, 80.0)",
                  (settings.TARGET_STATE,))
    forbid = ForbidGemini()

    print("  a. file does not exist")
    ra = ingest(tmp / "idsp_2025_w53.pdf", conn, call=forbid, cache_dir=tmp)
    check(not ra["ok"] and "not found" in ra["error"] and forbid.calls == 0, f"rejected: {ra['error']}")

    print("  b. file is not a PDF")
    junk = tmp / "idsp_2025_w52.pdf"
    junk.write_bytes(b"this is not a pdf\x00\xff" * 100)
    rb = ingest(junk, conn, call=forbid, cache_dir=tmp)
    check(not rb["ok"] and "not a PDF" in rb["error"] and forbid.calls == 0,
          f"rejected before any Gemini call: {rb['error']}")

    print("  c. Gemini returns something that is not a list of rows (twice)")
    fake_pdf = tmp / "idsp_2025_w51.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake for testing only")
    bad = _fake_call([{"oops": "not a list"}, "still garbage"])
    rc = ingest(fake_pdf, tconn, call=bad, cache_dir=tmp)
    check(not rc["ok"] and rc["gemini_calls"] == 2 and bad.calls == 2,
          f"retried once then failed cleanly after {rc['gemini_calls']} calls, not stored: {rc['error']}")
    check(read_cache(file_sha256(fake_pdf.read_bytes()), tmp) is None, "malformed response was not cached")

    print("  d. rows with bad fields inside an otherwise valid response")
    good = {"state": settings.TARGET_STATE, "district": "Guntur", "sub_district": "",
            "disease": "Acute Diarrhoeal Disease", "deaths": 0, "week": 51, "year": 2025, "status": ""}
    rows = [
        {**good, "outbreak_id": "T/1", "cases": 0},
        {**good, "outbreak_id": "T/2", "cases": -5},
        {**good, "outbreak_id": "T/3", "cases": "many"},
        {**good, "outbreak_id": "T/4", "cases": 12, "district": ""},
        {**good, "outbreak_id": "", "cases": 12},
        "not even an object",
        {**good, "outbreak_id": "T/6", "cases": 12, "sub_district": "Thullur",
         "deaths": None, "week": None, "year": None, "status": "x"},
        {**good, "outbreak_id": "T/6", "cases": 3},
        {**good, "outbreak_id": "T/7", "cases": 9, "state": "Kerala", "district": "Kannur",
         "sub_district": "Thodupuzha", "disease": "Measles"},
    ]
    rd = ingest(fake_pdf, tconn, call=_fake_call([rows]), cache_dir=tmp)
    _print_result(rd)
    stored_ids = [x["outbreak_id"] for x in rd["records"]]
    check(rd["ok"] and len(rd["rejected"]) == 7 and stored_ids == ["T/6", "T/7"],
          f"7 bad rows rejected with reasons, good rows stored: {stored_ids}")
    t6 = tconn.execute("SELECT week, year, deaths FROM outbreaks WHERE outbreak_id = 'T/6'").fetchone()
    check(tuple(t6) == (51, 2025, None), f"T/6 fell back to file-name week/year, NULL deaths: {tuple(t6)}")
    t7 = tconn.execute("SELECT in_scope, disease_key, review_flags FROM outbreaks "
                       "WHERE outbreak_id = 'T/7'").fetchone()
    check(tuple(t7) == (0, None, "out_of_scope,unknown_disease"), f"T/7 stored and flagged: {tuple(t7)}")


def main(pdf_path=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("backend.queries", "google_genai", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)  # AFC advisory on every call
    pdf_path = Path(pdf_path or settings.DEMO_IDSP_PDF)
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'OK' if cond else 'FAIL'}: {msg}")

    with get_connection() as conn:
        create_tables(conn)

        print(f"=== 1. Ingest {pdf_path.name} (run 1) ===")
        r1 = ingest(pdf_path, conn)
        _print_result(r1)
        check(r1["ok"], "ingest succeeded")
        if r1["ok"]:
            check(r1["stored"] == EXPECTED_RECORDS,
                  f"{r1['stored']} valid records stored (expected {EXPECTED_RECORDS})")
            guntur = conn.execute("SELECT * FROM outbreaks WHERE outbreak_id = ?",
                                  (EXPECTED_GUNTUR["outbreak_id"],)).fetchone()
            if guntur is None:
                check(False, f"{EXPECTED_GUNTUR['outbreak_id']} not stored")
            else:
                got = {k: guntur[k] for k in EXPECTED_GUNTUR}
                diffs = {k: (got[k], v) for k, v in EXPECTED_GUNTUR.items() if got[k] != v}
                print("  Guntur row: " + ", ".join(f"{k}={got[k]!r}" for k in EXPECTED_GUNTUR)
                      + f", in_scope={guntur['in_scope']}, disease_key={guntur['disease_key']}, "
                        f"review_flags={guntur['review_flags']}")
                check(not diffs, "Guntur row matches PROJECT_CONTEXT.md section 7 exactly"
                      + (f" - differences (got, expected): {diffs}" if diffs else ""))
                check(guntur["in_scope"] == 1 and guntur["disease_key"] == "acute_diarrhoeal_disease"
                      and guntur["review_flags"] is None,
                      "Guntur row is in scope, matched to rules.yaml, unflagged")
            n_db = conn.execute("SELECT COUNT(*) FROM outbreaks WHERE source_file = ?",
                                (pdf_path.name,)).fetchone()[0]
            check(n_db == r1["stored"], f"outbreaks table holds {n_db} rows for this file")

        print(f"\n=== 2. Ingest {pdf_path.name} again (run 2) - Gemini forbidden ===")
        forbid = ForbidGemini()
        r2 = ingest(pdf_path, conn, call=forbid)
        _print_result(r2)
        check(r2["ok"] and r2["cache_hit"], "served from cache")
        check(r2["gemini_calls"] == 0 and forbid.calls == 0, f"zero Gemini calls (attempted: {forbid.calls})")
        n_db2 = conn.execute("SELECT COUNT(*) FROM outbreaks WHERE source_file = ?", (pdf_path.name,)).fetchone()[0]
        check(n_db2 == r1["stored"], f"row count unchanged after re-ingest ({n_db2}) - no duplicates")
        check(r2["stored"] == r1["stored"] and [x["outbreak_id"] for x in r2["records"]] ==
              [x["outbreak_id"] for x in r1["records"]], "same records both runs")

        print("\n=== 3. Malformed input is rejected cleanly ===")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tconn = get_connection(tmp / "scratch.db")  # never touch the real ledger with fake rows
            create_tables(tconn)
            try:
                _malformed_checks(tmp, tconn, conn, check)
            finally:
                tconn.close()  # Windows cannot delete an open SQLite file

        print("\n=== 4. Diseases in the stored set vs rules.yaml ===")
        aliases = alias_table()
        by_disease = conn.execute(
            "SELECT disease, disease_key, COUNT(*) n, SUM(cases) cases, SUM(in_scope) in_scope "
            "FROM outbreaks WHERE source_file = ? GROUP BY disease, disease_key ORDER BY n DESC, disease",
            (pdf_path.name,)).fetchall()
        for r in by_disease:
            verdict = f"-> {r['disease_key']}" if r["disease_key"] else "NO RULE - flagged, no surge"
            print(f"  {r['disease']:<40} {r['n']:>3} outbreaks  {r['cases']:>6} cases  "
                  f"in_scope={r['in_scope']}  {verdict}")
        flagged = conn.execute("SELECT COUNT(*) FROM outbreaks WHERE source_file = ? AND disease_key IS NULL",
                               (pdf_path.name,)).fetchone()[0]
        unmatched_but_aliased = [r["disease"] for r in by_disease if r["disease_key"] is None
                                 and match_disease(r["disease"], aliases)]
        check(not unmatched_but_aliased, "every stored disease_key agrees with the live alias table")
        print(f"  {flagged} outbreaks carry no rules.yaml disease (stored, flagged unknown_disease, disease_key NULL)")
        print(f"  rules.yaml aliases: {dict(disease_aliases())}")

        print(f"\n{'ALL CHECKS PASSED' if ok else 'CHECKS FAILED'}")
        return ok


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1] if len(sys.argv) > 1 else None) else 1)
