#!/usr/bin/env python3
"""Close tenant prospects who already told us they found another place / no longer wish to rent.

Scans the live intake-state for ACTIVE (non-terminal) conversations whose last inbound message
trips the engine's withdrawal_signal(), and marks them closed (terminal). This is the backlog
counterpart to the live auto-close in intake_engine.handle_event — both share one definition of
"withdrawal", so there is no second place to keep in sync.

Dry-run by default (prints what WOULD close). Pass --apply to write (holds the runner flock and
backs up intake-state first). Pass --quiet for a one-line summary (nightly use). Safe to re-run."""
import os, sys, json, fcntl, shutil, tempfile, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "wa-pipeline"))
import intake_engine as E

IST  = os.path.expanduser("~/.claude/state/listing-templates/intake-state.json")
LOCK = os.path.expanduser("~/.claude/state/listing-templates/.wa-intake.lock")
APPLY = "--apply" in sys.argv
QUIET = "--quiet" in sys.argv


def main():
    if not os.path.exists(IST):
        print("no intake-state at", IST)
        return 0
    state = json.load(open(IST))
    convs = state.get("conversations", {})
    hits = []
    for pn, rec in convs.items():
        if rec.get("terminal"):
            continue
        last = rec.get("last_inbound") or ""
        if E.withdrawal_signal(last):
            hits.append((pn, rec, last))

    if not QUIET:
        print(f"scanned {len(convs)} conversations, {len(hits)} active prospect(s) signalled withdrawal:\n")
        for pn, rec, last in hits:
            nm = (rec.get("profile") or {}).get("name") or "(no name)"
            print(f"  - {nm} [{pn}] {rec.get('listing_key')} | status={rec.get('status')}")
            print(f"      last said: \"{last[:120]}\"")

    if not hits:
        if QUIET:
            print("withdrawal-sweep: 0 to close")
        return 0

    if not APPLY:
        print(f"\nDRY-RUN. Re-run with --apply to close these {len(hits)}.")
        return 0

    lf = open(LOCK, "a+")
    fcntl.flock(lf, fcntl.LOCK_EX)
    try:
        shutil.copy2(IST, IST + ".bak-withdraw-" + time.strftime("%Y%m%d-%H%M%S"))
        state = json.load(open(IST))                      # reload under lock
        n = 0
        for pn, _, last in hits:
            rec = state["conversations"].get(pn)
            if not rec or rec.get("terminal"):
                continue
            rec["terminal"] = True
            rec["stage"] = "WITHDRAWN"
            rec["status"] = "closed (found elsewhere)"
            rec["closed_reason"] = "sweep: prospect signalled withdrawal :: " + last[:120]
            n += 1
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(IST))
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, IST)
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()

    print(f"withdrawal-sweep: closed {n} prospect(s) who found another place / stopped renting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
