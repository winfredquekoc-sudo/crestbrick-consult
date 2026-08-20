#!/usr/bin/env python3
"""
close_stale_prospects.py — auto-close prospective tenants who have gone quiet.

Rule (per Winfred, 4 Jul 2026): a prospect whose last_contact is more than
STALE_DAYS (30) days ago is undesirable and is closed automatically.

Closes only non-terminal, non-deal statuses. Never touches: tenanted,
found_place, deposit-pending, anything already closed, excluded records, or
contact_state found_place / do_not_contact / not_interested. A "rejected"
status alone does NOT protect a row (see TERMINAL_STATUS). Records with no
last_contact at all fall back to data_collected; if neither exists the record
is left alone (nothing to age against).

Sets status to "closed (stale)", preserves the old status in prev_status,
stamps closed_reason + closed_date. Purely bookkeeping: sends nothing to anyone.
Idempotent: a second run changes nothing.

Usage: close_stale_prospects.py [--apply] [--days N] [--quiet]
Without --apply it is a dry run.
"""
import json, os, sys, datetime, shutil

ROOT = os.path.expanduser("~/crestbrick-consult")
P = os.path.join(ROOT, "_templates/tenant-db.json")
STALE_DAYS = 30

# "rejected" is deliberately NOT terminal here: a rejection with contact_state
# still "active" is a listing-level objection (too far / over budget), and those
# must age out like any open row — treating them as terminal made them immortal,
# and by 21 Aug 2026 they were 31% of the match roster months after going quiet.
# A real found/not-interested rejection is protected via TERMINAL_CONTACT below.
TERMINAL_STATUS = {"tenanted", "found_place", "deposit-pending"}
TERMINAL_CONTACT = {"found_place", "do_not_contact", "not_interested"}


def main():
    apply = "--apply" in sys.argv
    quiet = "--quiet" in sys.argv
    days = STALE_DAYS
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=days)).isoformat()

    d = json.load(open(P))
    closed = []
    for t in d["tenants"]:
        st = (t.get("status") or "").strip().lower()
        cs = (t.get("contact_state") or "").strip().lower()
        if t.get("excluded"):
            continue
        if st in TERMINAL_STATUS or st.startswith("closed"):
            continue
        if cs in TERMINAL_CONTACT:
            continue
        last = str(t.get("last_contact") or t.get("data_collected") or "")[:10]
        if not last:
            continue
        if last < cutoff:
            closed.append((t.get("id"), t.get("name"), st, last))
            if apply:
                t["prev_status"] = t.get("status")
                t["status"] = "closed (stale)"
                t["closed_reason"] = f"auto closed: no contact since {last} (>{days} days)"
                t["closed_date"] = today.isoformat()
                t["match_status"] = "stale_closed"

    if apply and closed:
        shutil.copy(P, P + f".bak-stale-{today.isoformat()}")
        tmp = P + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, P)

    if not quiet or closed:
        mode = "APPLIED" if apply else "DRY RUN"
        print(f"stale sweep [{mode}] cutoff {cutoff}: closed {len(closed)} prospect(s)")
        for i, n, s, l in closed[:40]:
            print(f"  {i}  {n or '(no name)'}  was {s}, last contact {l}")
        if len(closed) > 40:
            print(f"  ... and {len(closed)-40} more")


if __name__ == "__main__":
    main()
