#!/usr/bin/env python3
"""
close_stale_prospects.py — auto-close prospective tenants who have gone quiet.

Rule (per Winfred, 4 Jul 2026): a prospect whose last_contact is more than
STALE_DAYS (45) days ago is undesirable and is closed automatically.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tenant_state as TS

ROOT = os.path.expanduser("~/crestbrick-consult")
P = os.path.join(ROOT, "_templates/tenant-db.json")
STALE_DAYS = TS.load_config()["stale_days"]

# "rejected" is deliberately NOT terminal here: a rejection with contact_state
# still "active" is a listing-level objection (too far / over budget), and those
# must age out like any open row — treating them as terminal made them immortal,
# and by 21 Aug 2026 they were 31% of the match roster months after going quiet.
# A real found/not-interested rejection is protected via TERMINAL_CONTACT below.
TERMINAL_STATUS = TS.TERMINAL_STATUS
TERMINAL_CONTACT = TS.TERMINAL_CONTACT


def main():
    apply = "--apply" in sys.argv
    quiet = "--quiet" in sys.argv
    days = STALE_DAYS
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=days)).isoformat()

    d = json.load(open(P))
    # Pass 1 — reopen sweep closed rows whose contact resumed after closing
    # (the transition the old pipeline lacked; tenant_state.reopen_if_returned).
    reopened = []
    for t in d["tenants"]:
        probe = t if apply else dict(t)
        if TS.reopen_if_returned(probe):
            reopened.append((t.get("id"), t.get("name"), str(t.get("last_contact") or "")[:10]))
    closed = []
    for t in d["tenants"]:
        if TS.is_stale(t, cutoff):
            closed.append((t.get("id"), t.get("name"), (t.get("status") or "").strip().lower(),
                           str(t.get("last_contact") or t.get("data_collected") or "")[:10]))
            if apply:
                TS.close_stale(t, today.isoformat(), days)

    if apply and (closed or reopened):
        shutil.copy(P, P + f".bak-stale-{today.isoformat()}")
        tmp = P + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, P)

    if not quiet or closed or reopened:
        mode = "APPLIED" if apply else "DRY RUN"
        print(f"stale sweep [{mode}] cutoff {cutoff}: closed {len(closed)}, reopened {len(reopened)} prospect(s)")
        for i, n, l in reopened[:40]:
            print(f"  {i}  {n or '(no name)'}  REOPENED, contact resumed {l}")
        for i, n, s, l in closed[:40]:
            print(f"  {i}  {n or '(no name)'}  was {s}, last contact {l}")
        if len(closed) > 40:
            print(f"  ... and {len(closed)-40} more")


if __name__ == "__main__":
    main()
