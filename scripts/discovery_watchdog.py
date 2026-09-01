#!/usr/bin/env python3
"""discovery_watchdog.py — scanned=0 alarm for refresh-rental-dbs step 5b discovery.

Why this exists: since the 27 Aug 2026 switch to Haiku for every refresh-rental-dbs
slot, the run marker (~/.claude/state/refresh-rental-dbs-last.json) has intermittently
reported landlords_scanned=0 for days in a row with NOBODY alerted, because nothing
ever looked at the field across runs — the marker only ever holds the LATEST run, it
has no memory of the run before it. This script is the memory: called once per slot
(right after the slot's marker is written), it appends a counts-only snapshot to a
rolling history file and, if the scanned metric has been zero for several consecutive
daytime slots WHILE inbound WhatsApp volume over that same span was non-zero (i.e.
there was something to scan), prints a distinct alert line the wrapper can grep for
and Telegram. Detection only: this script never sends anything itself.

Two metrics, either can drive the alarm (see --metric):
  - "landlords_scanned"  the model-reported marker field named in the original bug
                          report. Always available, but the model computes it, so a
                          model that silently stops discovering can also silently
                          stop counting.
  - "candidates_found"   the DETERMINISTIC candidate count from a
                          discovery_candidates.py run (--candidates-file), when the
                          wrapper is wired to run it before invoking the model. This
                          is the harder signal to fake, since it comes from a plain
                          SQL scan, not the model under watch. Prefer this once
                          discovery_candidates.py is wired into the wrapper.

Read only except for its own history file. Counts only — never writes a name, phone,
or jid into the history.

CLI (call once per slot, after the marker is written):
    python3 discovery_watchdog.py [--marker PATH] [--history PATH]
        [--messages-db PATH] [--candidates-file PATH] [--metric landlords_scanned]
        [--min-consecutive-zero 5] [--slot 09]

Exit code is always 0 (detection must never fail the wrapper's run). Prints one line:
either "discovery_watchdog: OK ..." or a line starting "DISCOVERY_WATCHDOG_ALERT:".
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

DEFAULT_MARKER = os.path.expanduser("~/.claude/state/refresh-rental-dbs-last.json")
DEFAULT_HISTORY = os.path.expanduser("~/.claude/state/discovery-watchdog-history.json")
DEFAULT_MESSAGES_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
DEFAULT_CANDIDATES_FILE = os.path.expanduser("~/.claude/state/discovery-candidates.json")

MAX_HISTORY = 60          # ~12 days at 5 slots/day; plenty for any sane threshold
DEFAULT_MIN_CONSECUTIVE_ZERO = 5   # a full day's worth of daytime slots


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path))
    except (OSError, json.JSONDecodeError):
        return default


def _inbound_count_since(messages_db, since_ts):
    """Count of inbound (is_from_me=0) WhatsApp messages strictly after since_ts.
    Read only; mirrors the wrapper's own efficiency-gate query. Returns 0, not an
    exception, if the db is unreachable, so a transient lock never fires a false
    alarm off a bogus zero -- callers should treat 0 volume as 'unknown, skip'."""
    if not since_ts or not os.path.exists(messages_db):
        return 0
    try:
        con = sqlite3.connect("file:" + messages_db + "?mode=ro", uri=True, timeout=10)
    except sqlite3.OperationalError:
        return 0
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM messages WHERE is_from_me=0 AND timestamp > ?",
            (since_ts,),
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def build_snapshot(marker, candidates_file, messages_db, slot, history):
    """One entry to append to the rolling history. `marker` is the parsed
    refresh-rental-dbs-last.json. Idempotent: returns None if this ran_at is already
    the most recent history entry (repeat invocation against the same completed run)."""
    ran_at = marker.get("ran_at")
    if history and history[-1].get("ran_at") == ran_at:
        return None

    prev_ran_at = history[-1]["ran_at"] if history else None
    since = prev_ran_at or (marker.get("date", "") and marker["date"] + " 00:00:00+08:00")
    inbound = _inbound_count_since(messages_db, since)

    candidates = _load_json(candidates_file, None)
    candidates_found = candidates.get("candidates_found") if isinstance(candidates, dict) else None

    return {
        "date": marker.get("date"),
        "ran_at": ran_at,
        "slot": slot,
        "landlords_scanned": marker.get("landlords_scanned"),
        "landlords_added": marker.get("landlords_added"),
        "candidates_found": candidates_found,
        "inbound_since_last": inbound,
    }


def evaluate(history, metric, min_consecutive_zero):
    """Look at the tail of history. Alert only when EVERY one of the last
    `min_consecutive_zero` entries has metric==0 (or None, treated as 0 -- a run that
    never wrote the field is exactly as blind as one that wrote a real zero) AND the
    inbound volume summed over that same span is > 0 (there was something to find)."""
    if len(history) < min_consecutive_zero:
        return False, (f"discovery_watchdog: OK (only {len(history)} slot(s) recorded, "
                        f"need {min_consecutive_zero} to evaluate)")

    tail = history[-min_consecutive_zero:]
    scanned_vals = [t.get(metric) or 0 for t in tail]
    inbound_total = sum(t.get("inbound_since_last") or 0 for t in tail)

    all_zero = all(v == 0 for v in scanned_vals)
    if all_zero and inbound_total > 0:
        slots = ", ".join(f"{t.get('date')}/{t.get('slot') or '?'}" for t in tail)
        return True, (
            f"DISCOVERY_WATCHDOG_ALERT: {metric}=0 across the last {min_consecutive_zero} "
            f"slot(s) ({slots}) while {inbound_total} inbound WhatsApp message(s) arrived "
            f"in that span -- step 5b discovery may be silently broken. Check "
            f"refresh-rental-dbs.log and re-run discovery_candidates.py by hand."
        )

    return False, (
        f"discovery_watchdog: OK (last {min_consecutive_zero} {metric} values: {scanned_vals}, "
        f"inbound in span: {inbound_total})"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--marker", default=DEFAULT_MARKER)
    ap.add_argument("--history", default=DEFAULT_HISTORY)
    ap.add_argument("--messages-db", default=DEFAULT_MESSAGES_DB)
    ap.add_argument("--candidates-file", default=DEFAULT_CANDIDATES_FILE)
    ap.add_argument("--metric", choices=["landlords_scanned", "candidates_found"],
                     default="landlords_scanned")
    ap.add_argument("--min-consecutive-zero", type=int, default=DEFAULT_MIN_CONSECUTIVE_ZERO)
    ap.add_argument("--slot", default=None, help="the SGT slot label (e.g. 09, 12, 21); "
                     "informational only, for the alert message")
    args = ap.parse_args()

    marker = _load_json(args.marker, None)
    if not isinstance(marker, dict) or not marker.get("ran_at"):
        print("discovery_watchdog: no marker yet -- nothing to record")
        return

    history = _load_json(args.history, [])
    if not isinstance(history, list):
        history = []

    snap = build_snapshot(marker, args.candidates_file, args.messages_db, args.slot, history)
    if snap is not None:
        history.append(snap)
        history = history[-MAX_HISTORY:]
        tmp = args.history + ".tmp"
        os.makedirs(os.path.dirname(args.history), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
        os.replace(tmp, args.history)

    alert, message = evaluate(history, args.metric, args.min_consecutive_zero)
    print(message)
    sys.exit(0)


if __name__ == "__main__":
    main()
