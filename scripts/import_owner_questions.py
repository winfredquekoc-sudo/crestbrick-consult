#!/usr/bin/env python3
"""
import_owner_questions.py -- one off importer for the owner question queue (Step 2b of the
owner loop: src/wa-pipeline/wa_intake_owner.py). Reads a landlord clarity report's
"Section C -- Questions to ask" (free text, one Winfred already wrote in his own voice) and
enqueues ONLY the FACTUAL ones, for ACTIVE landlords, skipping anything about commission,
price, rent, race, nationality, gender, religion, deposits, or disputes -- and prints every
skip with its reason so a dry run is auditable.

Usage:
    python3 scripts/import_owner_questions.py <path-to-report.md> [--apply]
Default is a dry run (nothing written); --apply actually enqueues via
wa_intake_owner.enqueue_owner_question.
"""
import os, re, sys, json

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo) -- set before importing any
# wa-pipeline module. enqueue_owner_question() never sends or notifies on its own, but
# defense in depth here costs nothing, and this importer's --apply mode does write to the
# real owner-questions queue by design (that write is intentional production behaviour, not
# something this switch touches).
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "wa-pipeline"))
import wa_intake_paths as _P   # noqa: E402
_P.sandbox_init()              # no-op unless WA_INTAKE_SANDBOX=1 (a test); see its own
                               # docstring -- this importer's --apply mode is real production
                               # behaviour when NOT sandboxed, unchanged by this call.
import wa_intake_owner as OWN  # noqa: E402

_HEADER_RE = re.compile(r"^\*\*(.+?)\*\*\s*\(([^)]+)\)\s*$")
_BULLET_RE = re.compile(r"^-\s+(.*\S)\s*$")
_SECTION_C_RE = re.compile(r"^##\s*Section C\b", re.I)
_SECTION_NEXT_RE = re.compile(r"^##\s+", re.I)

# skip whole bullets that carry any of these -- Winfred's explicit never list, plus the
# obvious money/negotiation/dispute tells a hand written note picks up.
_SKIP_RE = re.compile(
    r"commission|\bnego\w*|\bdiscount\b|\bcheaper\b|\blower\b|\bdeposit\b|\brefund\b|"
    r"\bdispute\b|\blegal\b|\blawyer\b|\bcourt\b|\btribunal\b|\brace\b|\bethnicity\b|"
    r"\bnationality\b|\breligion\b|\bmuslim\b|\bchristian\b|\bhindu\b|\bbuddhist\b|"
    r"\bgender\b|\bmale\b|\bfemale\b|\bchinese\b|\bmalay\b|\bindian\b|\beurasian\b|"
    r"\$\s*\d|\bsgd\b|s\$|\basking\s+(?:rent|price)\b|\bhow\s+low\b|\blowest\b|\bmillion\b|"
    r"\bprice\b|\bpricing\b|\brent\b|\bcost\b|\d{3,}\s*(?:a|per)?\s*(?:month|mo)\b|"
    r"lease\s+length|minimum\s+lease|\bgst\b|\bgfa\b|\bplot\s+ratio\b|\bsole\b|"
    r"\bexclusive\b|\bpropertyguru\b|\bportal", re.I)

# one pattern per code, ordered -- first match wins (mirrors intake_engine's own
# _FACT_SHEET_PATTERNS/_FACT_*_RE style so the mapping reads the same way to a reviewer)
_CODE_PATTERNS = (
    ("AIRCON", re.compile(r"\baircon\b|\bair\s*con\b|\bair-con\b", re.I)),
    ("WIFI", re.compile(r"\bwifi\b|\bbroadband\b|\binternet\b", re.I)),
    ("UTILITIES", re.compile(
        r"\butilities\b|\belectricity\b|\bwater\s+(?:and|&)\s+wifi\b|\breconnected\b", re.I)),
    ("MRT", re.compile(r"\bmrt\b|\btrain\b|walking\s+distance", re.I)),
    ("MOVE_IN", re.compile(r"move\s*in\s+date|hand\s*over|ready\s+to\s+hand", re.I)),
    ("AVAILABILITY", re.compile(r"\bstill\s+available\b|\bstill\s+on\s+the\s+market\b", re.I)),
    ("VIEWING_WINDOW", re.compile(r"what\s+(?:days|time).*view|days?\s+and\s+times?\s+work", re.I)),
    # a generic "house rules" ask (often bundling pets/smoking/visitors in one clause) is
    # its own code, checked BEFORE the specific ones so it is not shadowed by whichever
    # single topic happens to appear first in the sentence.
    ("HOUSE_RULES", re.compile(r"house\s+rules|tenant\s+requirements|\bscreen\s+for\b", re.I)),
    ("COOKING", re.compile(r"\bcook(?:ing)?\b|\bkitchen\b(?!.{0,20}\bphoto|.{0,20}\bvideo)", re.I)),
    ("SMOKING", re.compile(r"\bsmok\w*\b", re.I)),
    ("PETS", re.compile(r"\bpets?\b\W", re.I)),
    ("VISITORS", re.compile(r"\bvisitors?\b|\bguests?\b", re.I)),
    ("PAX", re.compile(r"\bpax\b|how\s+many\s+(?:people|tenants|occupants)", re.I)),
)


def parse_section_c(text):
    """Yields (name, phone_raw, bullet_text) for every bullet under Section C."""
    lines = text.splitlines()
    in_c = False
    cur_name = cur_phone = None
    for line in lines:
        if _SECTION_C_RE.match(line):
            in_c = True
            continue
        if not in_c:
            continue
        if line.startswith("## ") and not _SECTION_C_RE.match(line):
            break                      # a later top level section ends Section C
        h = _HEADER_RE.match(line.strip())
        if h:
            cur_name, cur_phone = h.group(1).strip(), h.group(2).strip()
            continue
        b = _BULLET_RE.match(line)
        if b and cur_name:
            yield cur_name, cur_phone, b.group(1).strip()


def classify(bullet):
    """Returns (question_code, None) if this bullet is a clean factual ask, or
    (None, reason) if it must be skipped."""
    if _SKIP_RE.search(bullet):
        return None, "banned topic (commission/price/rent/deposit/race/nationality/gender/religion/dispute)"
    if bullet.startswith("(") and bullet.endswith(")"):
        return None, "not a question (editorial note)"
    for code, rx in _CODE_PATTERNS:
        if rx.search(bullet):
            return code, None
    return None, "no factual code match"


def _phone_digits(raw):
    return re.sub(r"\D", "", raw or "")


def run(report_path, apply_):
    text = open(report_path, encoding="utf-8").read()
    try:
        db = json.load(open(OWN._landlord_db()))
    except Exception as e:
        print(f"landlord-db.json unreadable ({e}); aborting import, nothing enqueued")
        return 1
    by_phone = {}
    for l in db.get("landlords", []):
        ph = _phone_digits(l.get("phone"))
        if ph:
            by_phone[ph] = l

    enqueued, skipped = [], []
    for name, phone_raw, bullet in parse_section_c(text):
        ph = _phone_digits(phone_raw)
        l = by_phone.get(ph)
        if not l:
            skipped.append((name, bullet, "no matching landlord record by phone"))
            continue
        if not OWN._landlord_eligible(l):
            status = str(l.get("status") or "").strip().lower()
            if l.get("review_flag") and status.startswith("active"):
                reason = "landlord under review (review_flag set)"
            else:
                reason = f"landlord not active (status={l.get('status')!r})"
            skipped.append((name, bullet, reason))
            continue
        code, reason = classify(bullet)
        if reason:
            skipped.append((name, bullet, reason))
            continue
        enqueued.append((l.get("id"), l.get("listing_key"), code, bullet))

    print(f"Parsed report: {report_path}")
    print(f"Would enqueue: {len(enqueued)}   Skipped: {len(skipped)}")
    print()
    print("=== TO ENQUEUE ===")
    for lid, lk, code, bullet in enqueued:
        print(f"  {lid} [{code}] ({lk or 'no listing'}): {bullet}")
    print()
    print("=== SKIPPED (with reason) ===")
    reasons = {}
    for name, bullet, reason in skipped:
        reasons[reason] = reasons.get(reason, 0) + 1
        print(f"  {name}: {bullet}\n    -> {reason}")
    print()
    print("=== SKIP REASON COUNTS ===")
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}  {reason}")

    if apply_:
        for lid, lk, code, bullet in enqueued:
            OWN.enqueue_owner_question(lid, lk, code, bullet, source="clarity-report")
        print(f"\n--apply: enqueued {len(enqueued)} questions to {OWN._queue_file()}")
    else:
        print("\nDRY RUN -- nothing written. Re-run with --apply to enqueue.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    if not args:
        sys.exit("usage: import_owner_questions.py <report.md> [--apply]")
    sys.exit(run(args[0], apply_))
