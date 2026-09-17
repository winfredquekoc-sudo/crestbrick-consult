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

ROOT = os.path.expanduser("~/crestbrick-consult")
DEFAULT_TENANT_DB = os.path.join(ROOT, "_templates/tenant-db.json")
DEFAULT_INTAKE_STATE = os.path.expanduser("~/.claude/state/listing-templates/intake-state.json")

# Simple string/number fields: fill verbatim (trimmed) only when the tenant-db
# row has nothing there yet. age/email are new columns tenant-db.json doesn't
# carry today; see module docstring for why that's safe.
SIMPLE_FIELDS = ["nationality", "gender", "ethnicity", "occupation", "pass_type", "age", "email", "name"]

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
    return v is None or v == ""


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


def join_tenant(t, profile, ts):
    """Mutates t in place with whatever null/blank fields profile can fill.
    Returns the list of field names actually filled (empty if none)."""
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
            t["preferred_location"] = v.strip()
            changes["preferred_location"] = {"src": "intake", "ts": ts}
            filled.append("preferred_location")

    for f in SIMPLE_FIELDS:
        if blank(t.get(f)):
            v = profile.get(f)
            if isinstance(v, str) and v.strip():
                t[f] = v.strip()
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
    rows_matched = 0
    rows_filled = 0
    skipped_status = 0
    no_match = 0
    sample = []
    new_columns = set()

    tenants = tenant_db.get("tenants", [])
    for t in tenants:
        if is_protected(t):
            skipped_status += 1
            continue
        profile = match_profile(t, intake_idx)
        if profile is None:
            no_match += 1
            continue
        rows_matched += 1
        filled = join_tenant(t, profile, ts)
        if filled:
            rows_filled += 1
            for f in filled:
                field_counts[f] += 1
                if f in ("age", "email"):
                    new_columns.add(f)
            if len(sample) < 5:
                sample.append({"id": t.get("id"), "fields": filled})

    cols = tenant_db.setdefault("columns", [])
    for c in sorted(new_columns):
        if c not in cols:
            cols.append(c)

    return {
        "rows_total": len(tenants),
        "rows_matched": rows_matched,
        "rows_filled": rows_filled,
        "rows_no_match": no_match,
        "rows_skipped_status": skipped_status,
        "fields_filled": dict(field_counts),
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
    os.replace(tmp, path)
    return backup


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant-db", default=DEFAULT_TENANT_DB)
    ap.add_argument("--intake-state", default=DEFAULT_INTAKE_STATE)
    ap.add_argument("--apply", action="store_true", help="write the join; default is dry run")
    ap.add_argument("--dry-run", action="store_true", help="explicit flag with no extra effect; this is already the default")
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
        print(f"skipped closed/excluded/do_not_contact/agent: {summary['rows_skipped_status']}  no intake match: {summary['rows_no_match']}")
        print("fields filled:")
        for k, v in sorted(summary["fields_filled"].items()):
            print(f"  {k}: {v}")
        print("sample rows that would change (id + field names, no values):")
        for s in summary["sample"]:
            print(f"  {s['id']}: {', '.join(s['fields'])}")
        if "backup" in summary:
            print(f"backup written: {summary['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
