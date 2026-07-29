#!/usr/bin/env python3
"""
sanitize_landlord_db.py — strip the false "URA quota" justification from landlord
requirements in landlord-db.json.

Why: URA's ethnic integration quota applies to HDB/EC PURCHASE and whole-flat letting,
NOT room rentals, so "URA quota" is factually wrong as a reason for any rental listing
requirement. A landlord may genuinely prefer a tenant ethnicity, and that preference is
kept for matching, but it is recorded as the landlord's own preference, never attributed
to a URA quota (even if the landlord themselves cited one).

This is a deterministic backstop: the refresh-rental-dbs skill is told not to write the
phrase, and this guarantees it never persists even if the model (or a landlord's own
message) reintroduces it. Idempotent; atomic write.

Usage:
  python3 sanitize_landlord_db.py            # dry run, reports what WOULD change
  python3 sanitize_landlord_db.py --apply     # writes landlord-db.json
"""
import json, os, re, sys

P = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
APPLY = "--apply" in sys.argv

# "URA quota" / "URA EIP quota" / "ethnic quota (URA)" etc. -> "landlord preference"
_PAREN_RE = re.compile(r"\(\s*ura(?:\s+eip)?\s+quota\s*\)", re.I)            # (URA quota)
_PHRASE_RE = re.compile(r"\bura(?:\s+eip)?\s+quota\b|\bethnic\s+quota\b", re.I)  # bare phrase

def _clean(s):
    if not isinstance(s, str) or "quota" not in s.lower():
        return s, False
    new = _PAREN_RE.sub("(landlord preference)", s)
    new = _PHRASE_RE.sub("landlord preference", new)
    new = re.sub(r"\s{2,}", " ", new).strip()
    return new, (new != s)

def _walk(obj):
    """Clean every string value in nested dicts/lists. Returns (obj, changes[])."""
    changes = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                nv, ch = _clean(v)
                if ch:
                    changes.append((k, v, nv)); obj[k] = nv
            elif isinstance(v, (dict, list)):
                _, cs = _walk(v); changes += cs
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                nv, ch = _clean(v)
                if ch:
                    changes.append((i, v, nv)); obj[i] = nv
            elif isinstance(v, (dict, list)):
                _, cs = _walk(v); changes += cs
    return obj, changes

def main():
    # also strip the false "URA quota" wording from the downstream rental copy the bot uses,
    # not just the landlord DB source (belt-and-braces; these are hand-maintained but the rule
    # is "URA quota must never persist in any rental copy").
    EXTRA = [os.path.expanduser("~/.claude/state/listing-templates/property-templates.json"),
             os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")]
    total = 0
    for path in [P] + EXTRA:
        try:
            d = json.load(open(path))
        except Exception:
            continue
        _, ch = _walk(d)
        if ch:
            total += len(ch)
            for k, old, new in ch:
                print(f"  {os.path.basename(path)} [{k}]: {old!r} -> {new!r}")
            if APPLY:
                tmp = path + ".tmp"
                json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
                os.replace(tmp, path)
    print(f"URA-quota labels found: {total}")
    if not APPLY and total:
        print("DRY RUN. Re-run with --apply to write.")
    elif APPLY and total:
        print(f"WROTE {total} fix(es) across landlord-db + listing copy.")

if __name__ == "__main__":
    main()
