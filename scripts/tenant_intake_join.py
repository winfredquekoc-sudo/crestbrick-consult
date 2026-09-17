#!/usr/bin/env python3
"""tenant_intake_join.py — deterministic join of WA intake form answers
(~/.claude/state/listing-templates/intake-state.json, conversations[<phone>].profile)
into _templates/tenant-db.json.

Why: the intake engine (src/wa-pipeline/intake_engine.py) collects a 14 field
profile per WhatsApp conversation but nothing ever wrote those answers back into
tenant-db.json, the app's actual source of truth (scripts/matchmaker/export_data.py
build_tenants()). ~195 of the 455 "Still looking" tenants have at least one field
answered in intake-state.json but blank in tenant-db.json.

Rules:
  - Fill ONLY null/blank fields. Never overwrite an existing value.
  - Never touch a row that is closed, excluded, do_not_contact, or a co-broke
    agent (is_protected() below — mirrors apply_tenant_lifecycle.py / tenant_state.py's
    own vocabulary for those four states).
  - district is never filled here — district inference is export_data.py's
    infer_district() job, not this script's.
  - move_in_date is filled only when the intake answer resolves to an exact
    calendar date (enrich.norm_date). Fuzzy/relative text ("asap", "next month",
    "early July") is deliberately left blank rather than guessed against
    *this run's* today — baking today's date into a persisted field for an
    answer given weeks ago would misrepresent the tenant's real availability.
    (export_data.py's own norm_move_in() is fine doing that at EXPORT time,
    recomputed on every build; it is wrong for a one-shot write here.)
  - Every filled field gets provenance in a `_backfill` map on the row:
    {field: {"src": "intake", "ts": <iso8601>}}. export_data.py's build_tenants()
    only reads a fixed, explicit set of keys per row (verified: it builds a new
    dict, never spreads **t), so this extra key and any new column (age/email)
    are inert to it.

Usage:
    /usr/bin/python3 scripts/tenant_intake_join.py --dry-run --json
    /usr/bin/python3 scripts/tenant_intake_join.py --apply
"""
import argparse
import collections
import datetime
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "matchmaker"))
import enrich  # noqa: E402
import tenant_profile_backfill  # noqa: E402

ROOT = os.path.expanduser("~/crestbrick-consult")
DEFAULT_TENANT_DB = os.path.join(ROOT, "_templates/tenant-db.json")
DEFAULT_INTAKE_STATE = os.path.expanduser("~/.claude/state/listing-templates/intake-state.json")

# Simple string/number fields: fill verbatim (trimmed, sanitized — see
# sanitize_value()) only when the tenant-db row has nothing there yet.
# age/email are new columns tenant-db.json doesn't carry today; see module
# docstring for why that's safe.
SIMPLE_FIELDS = ["nationality", "gender", "ethnicity", "occupation", "pass_type", "age", "email", "name"]

# blank() also treats these (case insensitive, stripped) as never-filled-in
# placeholders, on top of None/"" — so a tenant-db row that already holds the
# literal text "TBC" or "unknown" is still fair game to fill, not a real value.
_BLANK_TOKENS = {"tbc", "unknown", "to confirm", "null", "na", "n/a", "-", "?"}

# sanitize_value() rejects a verbatim string write when its stripped lowercase
# is one of these junk answers (tenant_profile_backfill's own NA vocabulary,
# widened with a few more intake-form non-answers that aren't "not answered"
# shaped enough for _NA_VALUES but are still not a real value).
_JUNK_VALUES = tenant_profile_backfill._NA_VALUES | {
    "?", "null", "unknown", "no", "yes", "-", "na", "n/a", "tbc", "as well",
}

_LEADING_JUNK_RE = re.compile(r"^[\[.*]")
# CJK Unified Ideographs + Hiragana/Katakana + Hangul syllables — broad "any
# CJK script" match, not a specific-language check.
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힣]")

_LEN_CAP = 40
_LEN_CAP_FIELDS = {"gender", "ethnicity", "nationality", "pass_type", "name", "occupation"}

_MALE_WORDS = {"male", "m", "man", "boy", "guy"}
_FEMALE_WORDS = {"female", "f", "woman", "girl", "lady"}


def _norm_gender_value(s):
    sl = s.strip().lower()
    if sl in _MALE_WORDS:
        return "Male"
    if sl in _FEMALE_WORDS:
        return "Female"
    return None


def sanitize_value(field, raw):
    """Verbatim-write guard shared by every SIMPLE_FIELDS + preferred_location
    write. `raw` is already known to be a non-empty stripped-truthy string.
    Returns the cleaned value to write, or None to reject as junk (the caller
    counts the rejection in `rejected_junk`). Never called on numeric
    (int/float) profile values — those bypass sanitization entirely."""
    v = raw.strip()
    if v.lower() in _JUNK_VALUES:
        return None
    if _LEADING_JUNK_RE.match(v):
        return None
    if "?" in v:
        return None
    if _CJK_RE.search(v):
        return None
    if field == "gender":
        return _norm_gender_value(v)
    if field in _LEN_CAP_FIELDS and len(v) > _LEN_CAP:
        return None
    if field == "name" and len(v) < 3:
        return None
    return v

_DATE_LABEL_RE = re.compile(r"^\s*(in\s+)?date\s*[:\-]?\s*", re.I)
_RANGE_RE = re.compile(r"([\d,]+\.?\d*\s?k?)\s*(?:to|-|~|–)\s*\$?\s?([\d,]+\.?\d*\s?k?)", re.I)
_SINGLE_RE = re.compile(r"\$?\s?([\d,]+\.?\d*\s?k?)", re.I)


def phone_key(raw):
    """Canonical match key for a phone/jid string. enrich.normalize_phone()
    strips everything that isn't a digit, which — since '@s.whatsapp.net' and
    '@lid' are pure letters — already drops those suffixes as a side effect.
    Reused rather than reimplemented per the investigation's own instruction."""
    if not raw:
        return None
    k = enrich.normalize_phone(raw)
    return k or None


def blank(v):
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip()
        return s == "" or s.lower() in _BLANK_TOKENS
    return False


def budget_blank(t):
    return blank(t.get("budget")) and blank(t.get("budget_min")) and blank(t.get("budget_max"))


def parse_budget(raw):
    """int/float or free text ("$1,200", "1200", "1.2k", "1000 to 1500") ->
    a dict matching tenant-db.json's own existing convention (single figure:
    budget=budget_max=n, budget_min=''; range: budget='', budget_min/max set).
    None if unparseable or outside enrich.BUDGET_PLAUSIBLE_MIN/MAX."""
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        n = int(raw)
        if enrich.BUDGET_PLAUSIBLE_MIN <= n <= enrich.BUDGET_PLAUSIBLE_MAX:
            return {"budget": n, "budget_min": "", "budget_max": n}
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = _RANGE_RE.search(s)
    if m:
        a, b = enrich._tok_to_num(m.group(1)), enrich._tok_to_num(m.group(2))
        if (a is not None and b is not None
                and enrich.BUDGET_PLAUSIBLE_MIN <= a <= enrich.BUDGET_PLAUSIBLE_MAX
                and enrich.BUDGET_PLAUSIBLE_MIN <= b <= enrich.BUDGET_PLAUSIBLE_MAX):
            lo, hi = min(a, b), max(a, b)
            return {"budget": "", "budget_min": lo, "budget_max": hi}
        return None
    m = _SINGLE_RE.search(s)
    if m:
        n = enrich._tok_to_num(m.group(1))
        if n is not None and enrich.BUDGET_PLAUSIBLE_MIN <= n <= enrich.BUDGET_PLAUSIBLE_MAX:
            return {"budget": n, "budget_min": "", "budget_max": n}
    return None


def parse_move_in(raw):
    """Strip a leading 'date:' / 'in Date:' label the intake form's free text
    answer sometimes carries, then only accept an already exact calendar date
    (enrich.norm_date). See module docstring for why fuzzy text is skipped."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = _DATE_LABEL_RE.sub("", s, count=1).strip()
    return enrich.norm_date(s)


def parse_small_int(raw, lo, hi):
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        n = int(raw)
    else:
        m = re.search(r"\d+", str(raw))
        if not m:
            return None
        n = int(m.group(0))
    return n if lo <= n <= hi else None


def is_protected(t):
    """closed / excluded / do_not_contact / agent — the four states the
    investigation named. A co-broke agent row is always excluded=True with
    exclude_reason mentioning 'co-broke agent' (apply_tenant_lifecycle.py /
    tenant_state.py route excluded rows to match_status do_not_contact), so
    the excluded check alone already covers 'agent'."""
    if t.get("excluded"):
        return True
    if (t.get("status") or "").strip().lower().startswith("closed"):
        return True
    if (t.get("contact_state") or "").strip().lower() == "do_not_contact":
        return True
    if (t.get("match_status") or "").strip().lower() in ("do_not_contact", "stale_closed"):
        return True
    return False


def build_intake_index(intake_state):
    idx = {}
    for pn, rec in (intake_state.get("conversations") or {}).items():
        prof = (rec or {}).get("profile") or {}
        if not prof:
            continue
        k = phone_key(pn)
        if not k:
            continue
        idx.setdefault(k, prof)  # first match wins on a rare key collision
    return idx


def match_profile(t, intake_idx):
    for raw in (t.get("phone"), t.get("jid")):
        k = phone_key(raw)
        if k and k in intake_idx:
            return intake_idx[k]
    return None


def tenant_phone_keys(t):
    keys = set()
    for raw in (t.get("phone"), t.get("jid")):
        k = phone_key(raw)
        if k:
            keys.add(k)
    return keys


def build_phone_owner_counts(tenants):
    """normalised phone key -> number of DISTINCT non protected tenant rows
    carrying it. Two records legitimately sharing one phone (a couple, a
    family) must never be merged or cross filled from one intake profile, so
    any key with count > 1 is a signal to skip, not a match to resolve."""
    counts = collections.Counter()
    for t in tenants:
        if is_protected(t):
            continue
        for k in tenant_phone_keys(t):
            counts[k] += 1
    return counts


def join_tenant(t, profile, ts, rejected):
    """Mutates t in place with whatever null/blank fields profile can fill.
    `rejected` is a collections.Counter mutated in place with per-field junk
    rejections (sanitize_value() returning None). Returns the list of field
    names actually filled (empty if none)."""
    filled = []
    changes = {}

    if budget_blank(t):
        b = parse_budget(profile.get("budget"))
        if b:
            t.update(b)
            changes["budget"] = {"src": "intake", "ts": ts}
            filled.append("budget")

    if blank(t.get("move_in_date")):
        d = parse_move_in(profile.get("move_in_date"))
        if d:
            t["move_in_date"] = d
            changes["move_in_date"] = {"src": "intake", "ts": ts}
            filled.append("move_in_date")

    if blank(t.get("no_of_pax")):
        n = parse_small_int(profile.get("no_of_pax"), 1, 20)
        if n is not None:
            t["no_of_pax"] = n
            changes["no_of_pax"] = {"src": "intake", "ts": ts}
            filled.append("no_of_pax")

    if blank(t.get("lease_term_months")):
        n = parse_small_int(profile.get("lease_term_months"), 1, 240)
        if n is not None:
            t["lease_term_months"] = n
            changes["lease_term_months"] = {"src": "intake", "ts": ts}
            filled.append("lease_term_months")

    if blank(t.get("preferred_location")):
        v = profile.get("preferred_location")
        if isinstance(v, str) and v.strip():
            cleaned = sanitize_value("preferred_location", v)
            if cleaned is None:
                rejected["preferred_location"] += 1
            else:
                t["preferred_location"] = cleaned
                changes["preferred_location"] = {"src": "intake", "ts": ts}
                filled.append("preferred_location")

    for f in SIMPLE_FIELDS:
        if blank(t.get(f)):
            v = profile.get(f)
            if isinstance(v, str) and v.strip():
                cleaned = sanitize_value(f, v)
                if cleaned is None:
                    rejected[f] += 1
                    continue
                t[f] = cleaned
                changes[f] = {"src": "intake", "ts": ts}
                filled.append(f)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                t[f] = v
                changes[f] = {"src": "intake", "ts": ts}
                filled.append(f)

    if changes:
        t.setdefault("_backfill", {}).update(changes)
    return filled


def run_join(tenant_db, intake_state):
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds")
    intake_idx = build_intake_index(intake_state)
    field_counts = collections.Counter()
    rejected_junk = collections.Counter()
    rows_matched = 0
    rows_filled = 0
    skipped_status = 0
    skipped_shared_phone = 0
    no_match = 0
    sample = []

    tenants = tenant_db.get("tenants", [])
    phone_owner_counts = build_phone_owner_counts(tenants)
    for t in tenants:
        if is_protected(t):
            skipped_status += 1
            continue
        if any(phone_owner_counts[k] > 1 for k in tenant_phone_keys(t)):
            skipped_shared_phone += 1
            continue
        profile = match_profile(t, intake_idx)
        if profile is None:
            no_match += 1
            continue
        rows_matched += 1
        filled = join_tenant(t, profile, ts, rejected_junk)
        if filled:
            rows_filled += 1
            for f in filled:
                field_counts[f] += 1
            if len(sample) < 5:
                sample.append({"id": t.get("id"), "fields": filled})

    return {
        "rows_total": len(tenants),
        "rows_matched": rows_matched,
        "rows_filled": rows_filled,
        "rows_no_match": no_match,
        "rows_skipped_status": skipped_status,
        "rows_skipped_shared_phone": skipped_shared_phone,
        "fields_filled": dict(field_counts),
        "rejected_junk": dict(rejected_junk),
        "sample": sample,
    }


def load_json(path):
    with open(path) as f:
        return json.load(f)


def atomic_write_with_backup(path, data):
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.bak-{ts}"
    shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return backup


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant-db", default=DEFAULT_TENANT_DB)
    ap.add_argument("--intake-state", default=DEFAULT_INTAKE_STATE)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the join; default is dry run")
    mode.add_argument("--dry-run", action="store_true", help="explicit flag with no extra effect; this is already the default")
    ap.add_argument("--json", action="store_true", help="machine readable summary")
    args = ap.parse_args()

    try:
        tenant_db = load_json(args.tenant_db)
        intake_state = load_json(args.intake_state)
    except (OSError, ValueError) as e:
        print(f"tenant_intake_join: failed to read input files: {e}", file=sys.stderr)
        return 2

    # run_join() only ever fully resolves a field or leaves it untouched (see
    # parse_budget/parse_move_in/parse_small_int — never a half filled group),
    # and nothing is written to disk until after this returns, so any
    # unexpected exception here means we abort with nothing written rather
    # than risk a partial apply.
    try:
        summary = run_join(tenant_db, intake_state)
    except Exception as e:
        print(f"tenant_intake_join: aborting, unexpected error during join: {e}", file=sys.stderr)
        return 2

    summary["mode"] = "apply" if args.apply else "dry_run"

    if args.apply:
        summary["backup"] = atomic_write_with_backup(args.tenant_db, tenant_db)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"mode: {summary['mode']}")
        print(f"rows total: {summary['rows_total']}  matched: {summary['rows_matched']}  filled: {summary['rows_filled']}")
        print(f"skipped closed/excluded/do_not_contact/agent: {summary['rows_skipped_status']}  "
              f"skipped shared phone: {summary['rows_skipped_shared_phone']}  no intake match: {summary['rows_no_match']}")
        print("fields filled:")
        for k, v in sorted(summary["fields_filled"].items()):
            print(f"  {k}: {v}")
        if summary["rejected_junk"]:
            print("rejected as junk:")
            for k, v in sorted(summary["rejected_junk"].items()):
                print(f"  {k}: {v}")
        print("sample rows that would change (id + field names, no values):")
        for s in summary["sample"]:
            print(f"  {s['id']}: {', '.join(s['fields'])}")
        if "backup" in summary:
            print(f"backup written: {summary['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
