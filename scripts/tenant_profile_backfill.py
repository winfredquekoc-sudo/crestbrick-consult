#!/usr/bin/env python3
"""tenant_profile_backfill.py — nightly repair for tenant-db profiles.

Tenants whose intake form arrived as an edited or follow-up WhatsApp message
(or quoted back inside the bot's own form, which the live intake engine
filters out as a bot echo) end up "profile-received" with null required
fields. This script re-reads each such tenant's inbound chat history and
fills ONLY the null fields. It never overwrites a non-null value and never
sends anything. Parsing logic is copied from intake_engine.extract_profile
(the engine is live; do not import it).

Runs standalone: python3 tenant_profile_backfill.py [--dry-run]
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

TENANT_DB = os.path.expanduser("~/crestbrick-consult/_templates/tenant-db.json")
MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")

# a tenant is a backfill candidate when >= 2 of these are missing
GAP_FIELDS = ["name", "nationality", "pass_type", "no_of_pax", "move_in_date", "budget"]
# everything the parser can fill
FILLABLE = ["name", "nationality", "ethnicity", "gender", "age", "pass_type",
            "occupation", "no_of_pax", "move_in_date", "lease_term_months",
            "budget", "budget_note"]

# zero-width chars WhatsApp inserts on iOS bullet lists ("⁠Ethnicity / Nationality")
# make labels invisible to naive matching — strip them before parsing.
_INVISIBLE = dict.fromkeys(map(ord, "⁠​‌‍﻿"), None)

_NA_VALUES = {"", "-", "--", "na", "n/a", "n.a", "n.a.", "nil", "none", "tbc", "nan", "?"}

# portal enquiry boilerplate ("RENT - 606D Tampines... S$ 3,700 /mo") must never
# be read as the tenant's own budget/profile — same stripper as the live engine.
_BOILERPLATE_RE = re.compile(
    r"(?im)^\s*(hi winfred.*|hi propertyguru.*|i am interested in:?.*|"
    r"(?:rent|sale)\s*-\s.*|room\s*/\s*s?\$.*|\d[\s-]*(?:room|beds?)\s+hdb.*|"
    r"https?://\S+.*|ref id:.*|thanks?\.?)\s*$")

# field -> regex matched against a cleaned line KEY (text before the first colon,
# parentheticals removed, lowercased). One key can hit several fields
# ("Ethnicity / Nationality : Indian" fills both).
KEY_PATTERNS = {
    "name":              r"\bname\b",
    "nationality":       r"nationality",
    "ethnicity":         r"ethnic|\brace\b",
    "gender":            r"\bgender\b|\bsex\b|males?\s*/\s*females?",
    "age":               r"\bage\b",
    "pass_type":         r"\bpass\b|type of pass|\bvisa\b",
    "occupation":        r"occupation|profession|\bjob\b",
    "no_of_pax":         r"\bpax\b|occupants?|no\.?\s*of\s*(?:pax|people|person)|number of (?:pax|people)",
    "move_in_date":      r"move.?in|intended move|start(?:ing)? date",
    "lease_term_months": r"\blease\b|term of lease|\btenure\b",
    "budget":            r"budget|\brent(?:al)?\b",
}
_KEY_RES = {f: re.compile(p, re.I) for f, p in KEY_PATTERNS.items()}


def _to_int(s):
    if s is None:
        return None
    txt = str(s).lower().strip()
    m = re.search(r"(\d[\d,\.]*)\s*([km])?\b", txt)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except Exception:
        return None
    suf = m.group(2)
    mul = 1000 if suf == "k" else 1000000 if (suf == "m" or "million" in txt) else 1
    val = val * mul
    if not (val == val) or val > 1e12:
        return None
    return int(val)


def _clean_value(v):
    v = (v or "").strip().lstrip("•").strip()
    # drop a leading "(...)" format hint so a blank field that only echoes the
    # hint collapses to empty and is never read as a real value
    v = re.sub(r"^\([^)]*\)\s*", "", v).strip()
    return None if v.lower() in _NA_VALUES else v


def _norm_gender(g):
    gl = g.lower()
    has_f = bool(re.search(r"\bf(?:emale)?s?\b", gl))
    has_m = bool(re.search(r"\bm(?:ale)?s?\b", gl))
    if "couple" in gl or "married" in gl or (has_f and has_m):
        return g.strip()
    if gl.startswith("f"):
        return "Female"
    if gl.startswith("m"):
        return "Male"
    return g.strip()


def _parse_budget(val, out):
    # per-head pricing ("per $500", "$500 per pax") is not a whole-unit budget:
    # keep the raw string in budget_note and leave budget null for a human call
    if re.search(r"\bper\b(?!\s*month)|\beach\b|/\s*pax|\bpp\b", val, re.I):
        out.setdefault("budget_note", val.strip())
        return
    b = _to_int(val)
    # a bare small decimal with no unit ("1.2", "3.6") means thousands
    mdec = re.search(r"\b(\d\.\d{1,2})\b", val)
    if mdec and (b is None or b < 100):
        b = int(float(mdec.group(1)) * 1000)
    if b and 200 < b < 20000:
        out["budget"] = b


def _set_field(field, val, out):
    if field in out:
        return
    if field == "name":
        if not re.search(r"[a-zA-Z]", val):
            return
        out["name"] = val
    elif field in ("nationality", "ethnicity", "pass_type", "occupation", "move_in_date"):
        out[field] = val
    elif field == "gender":
        out["gender"] = _norm_gender(val)
    elif field == "age":
        n = _to_int(val)
        if n and 10 <= n < 120:
            out["age"] = n
    elif field == "no_of_pax":
        n = _to_int(val)
        if n and 0 < n < 12:
            out["no_of_pax"] = n
    elif field == "lease_term_months":
        vl = val.lower()
        if "year" in vl or "yr" in vl:
            out["lease_term_months"] = 12 * (_to_int(vl) or 1)
        else:
            n = _to_int(vl)
            if n and n <= 3 and not re.search(r"month|mth|\bmo\b", vl):
                # a bare "1" or "2" with no unit means years ("Lease term: 1"),
                # a 1 month tenancy does not exist in this pipeline
                out["lease_term_months"] = 12 * n
            elif n and n <= 36:
                out["lease_term_months"] = n
    elif field == "budget":
        _parse_budget(val, out)


_PAX_PROSE_RE = re.compile(r"\b(\d{1,2})\s*pax\b", re.I)
_PASS_PROSE_RE = re.compile(
    r"\b(employment pass|work permit|student pass|s\s*pass|ep|wp|pr|sc|stp|dp|ltvp)\s+holders?\b", re.I)


def parse_message(text):
    """Best-effort profile parse of one inbound message. Keyed lines first,
    then high-precision prose fallbacks. Returns only confidently parsed fields."""
    out = {}
    t = _BOILERPLATE_RE.sub("", (text or "").translate(_INVISIBLE))
    for line in t.splitlines():
        line = re.sub(r"^[\s•\-\*–—·o]+", "", line).strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = re.sub(r"\([^)]*\)", "", key).strip().lower()
        if not key or len(key) > 60:
            continue
        val = _clean_value(val)
        if not val:
            continue
        for field, rx in _KEY_RES.items():
            if rx.search(key):
                _set_field(field, val, out)
    if "no_of_pax" not in out:
        m = _PAX_PROSE_RE.search(t)
        if m:
            _set_field("no_of_pax", m.group(1), out)
    if "pass_type" not in out:
        m = _PASS_PROSE_RE.search(t)
        if m:
            out["pass_type"] = m.group(1).upper() if len(m.group(1)) <= 6 else m.group(1).title()
    return out


def _is_missing(field, val):
    if val in (None, "", [], {}):
        return True
    # "Unknown (+65 ... group)" is the pipeline's placeholder, not a real name
    if field == "name" and re.match(r"^\s*unknown\b", str(val), re.I):
        return True
    return False


def inbound_messages(con, jid, limit=60):
    rows = con.execute(
        "SELECT content FROM messages WHERE chat_jid=? AND is_from_me=0 "
        "AND content IS NOT NULL AND content!='' ORDER BY timestamp DESC LIMIT ?",
        (jid, limit)).fetchall()
    return [c for (c,) in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change, write nothing")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()

    try:
        with open(TENANT_DB) as f:
            db = json.load(f)
    except Exception as e:
        print(f"ERROR: cannot read tenant DB {TENANT_DB}: {e}", file=sys.stderr)
        return 1
    try:
        con = sqlite3.connect(f"file:{MSG_DB}?mode=ro", uri=True, timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("SELECT 1 FROM messages LIMIT 1")
    except Exception as e:
        print(f"ERROR: cannot read messages DB {MSG_DB}: {e}", file=sys.stderr)
        return 1

    tenants = db["tenants"] if isinstance(db, dict) else db
    candidates = [t for t in tenants
                  if t.get("status") in ("profile-received", "open")
                  and sum(1 for f in GAP_FIELDS if _is_missing(f, t.get(f))) >= 2]

    updated = {}
    for t in candidates:
        jid = t.get("jid")
        if not jid:
            continue
        try:
            msgs = inbound_messages(con, jid)
        except Exception:
            continue
        # newest message wins per field: an edited/corrected resend of the form
        # is stored newer than the incomplete original
        parsed = {}
        for m in msgs:  # already newest first
            for k, v in parse_message(m).items():
                parsed.setdefault(k, v)
        filled = {}
        for field in FILLABLE:
            if field in parsed and _is_missing(field, t.get(field)):
                filled[field] = parsed[field]
        if not filled:
            continue
        for k, v in filled.items():
            t[k] = v
        note = f"profile backfilled from chat {today}"
        t["follow_up"] = (t["follow_up"] + "; " + note) if t.get("follow_up") else note
        updated[t.get("id") or jid] = (t, filled)
    con.close()

    mode = "DRY RUN, nothing written" if args.dry_run else "live"
    print(f"Tenant profile backfill {today} ({mode})")
    print(f"Scanned: {len(candidates)} tenants (status profile-received/open, >=2 gaps)")
    print(f"Updated: {len(updated)}")
    for tid, (t, filled) in updated.items():
        detail = ", ".join(f"{k}={v!r}" for k, v in filled.items())
        print(f" - {tid} ({t.get('phone') or t.get('jid')}): {detail}")

    if updated and not args.dry_run:
        bak = TENANT_DB + ".bak-backfill-" + today.replace("-", "")
        # never clobber an earlier same-day backup: it holds the true pre-run state
        if not os.path.exists(bak):
            shutil.copy2(TENANT_DB, bak)
            print(f"Backup: {bak}")
        if isinstance(db, dict):
            db["last_updated"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(TENANT_DB), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(db, f, indent=1, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, TENANT_DB)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        print(f"Wrote: {TENANT_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
