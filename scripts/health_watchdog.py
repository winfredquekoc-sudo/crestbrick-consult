#!/usr/bin/env python3
"""health_watchdog.py — one output-based health check for every critical job.

Why this exists: the recurring, most expensive failure mode in this stack is a job
that exits 0 but produces nothing, with nobody watching the OUTPUT. It has bitten the
same way every time: refresh-rental-dbs discovery reported scanned=0 for days; a
launchd service silently dropped out and froze the DBs for 5 days; the Google Sheet
push went stale since early July; the spend tracker died. In each case the process
"succeeded" and only a human, days later, noticed a number that should have moved had
stayed flat.

discovery_watchdog.py fixed exactly one of these. This is that pattern generalised: a
config-driven checker that reads the OUTPUT of each critical job (a file's freshness, a
metric that should be positive, or a value that should keep changing) and prints a
distinct HEALTH_WATCHDOG_ALERT: line the caller can grep for and Telegram. Detection
only by default -- it sends nothing unless --notify is passed.

Check types (config: scripts/health_watchdog_checks.json, a list of objects):
  file_fresh    {path, max_age_hours}      -- file mtime must be within the window
  json_positive {path, field, min}         -- numeric field (dotted) must be >= min (default 1)
  json_changed  {path, field, min_span}    -- numeric field must have CHANGED across the
                                              last min_span recorded snapshots (flat = alarm)
Common per-check keys:
  name          required, unique
  night_muted   if true, an alert during SGT quiet hours (23:00-08:00) is emitted as
                HEALTH_WATCHDOG_MUTED: instead of ...ALERT:, so a nightly caller that
                greps for ALERT does not ping at 3am for a daytime job.

Read only except its own history file (counts only -- never a name, phone, or jid).
Exit code is always 0 (a health check must never fail the thing that calls it).
"""
import argparse
import datetime
import json
import os
import sys

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "health_watchdog_checks.json")
DEFAULT_HISTORY = os.path.expanduser("~/.claude/state/health-watchdog-history.json")
MAX_HISTORY_PER_CHECK = 60
QUIET_START, QUIET_END = 23, 8   # SGT hours [23:00, 08:00) are quiet


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path))
    except (OSError, json.JSONDecodeError):
        return default


def _dotted(obj, field):
    cur = obj
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _sgt_hour(force_hour):
    if force_hour is not None:
        return force_hour
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).hour


def _is_quiet(hour):
    return hour >= QUIET_START or hour < QUIET_END


def _age_hours(path):
    return (datetime.datetime.now().timestamp() - os.path.getmtime(path)) / 3600.0


def check_file_fresh(c):
    path = os.path.expanduser(c["path"])
    if not os.path.exists(path):
        return False, None, f"missing ({path})"
    age = _age_hours(path)
    limit = c.get("max_age_hours", 30)
    ok = age <= limit
    return ok, round(age, 1), (f"stale: {age:.1f}h old > {limit}h limit" if not ok
                               else f"fresh ({age:.1f}h)")


def check_json_positive(c):
    path = os.path.expanduser(c["path"])
    data = _load_json(path, None)
    if not isinstance(data, dict):
        return False, None, f"unreadable or not an object ({path})"
    val = _dotted(data, c["field"])
    if not isinstance(val, (int, float)):
        return False, None, f"field {c['field']} missing/non-numeric"
    lo = c.get("min", 1)
    ok = val >= lo
    return ok, val, (f"{c['field']}={val} < {lo}" if not ok else f"{c['field']}={val}")


def check_json_changed(c, history):
    """Flat-value alarm: the field must differ across the last min_span recorded
    snapshots. Records this run's value; only evaluates once enough history exists."""
    path = os.path.expanduser(c["path"])
    data = _load_json(path, None)
    val = _dotted(data, c["field"]) if isinstance(data, dict) else None
    if not isinstance(val, (int, float)):
        return False, None, f"field {c['field']} missing/non-numeric ({path})"
    span = c.get("min_span", 4)
    tail = [h["value"] for h in history[-span:]] + [val]
    if len(tail) < span + 1:
        return True, val, f"{c['field']}={val} (need {span + 1} samples, have {len(tail)})"
    recent = tail[-(span + 1):]
    ok = len(set(recent)) > 1
    return ok, val, (f"{c['field']} stuck at {val} for {span + 1} runs" if not ok
                     else f"{c['field']}={val} (changing)")


def run_check(c, history_for_check):
    t = c["type"]
    if t == "file_fresh":
        return check_file_fresh(c)
    if t == "json_positive":
        return check_json_positive(c)
    if t == "json_changed":
        return check_json_changed(c, history_for_check)
    return False, None, f"unknown check type '{t}'"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--history", default=DEFAULT_HISTORY)
    ap.add_argument("--only", default=None, help="run just the named check")
    ap.add_argument("--dry", action="store_true", help="do not write history")
    ap.add_argument("--force-hour", type=int, default=None, help="override SGT hour (tests)")
    ap.add_argument("--notify", default=None, metavar="CHAT_ID",
                    help="on any real (non-muted) alert, send ONE Telegram line via "
                         "~/.claude/bin/telegram_send.sh to this chat id")
    args = ap.parse_args()

    checks = _load_json(args.config, None)
    if not isinstance(checks, list):
        print(f"health_watchdog: no usable config at {args.config}")
        sys.exit(0)
    if args.only:
        checks = [c for c in checks if c.get("name") == args.only]

    history = _load_json(args.history, {})
    if not isinstance(history, dict):
        history = {}

    quiet = _is_quiet(_sgt_hour(args.force_hour))
    alerts, lines = [], []

    for c in checks:
        name = c.get("name", "?")
        hist = history.get(name, [])
        ok, value, detail = run_check(c, hist)

        if value is not None:
            hist.append({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                         "value": value})
            history[name] = hist[-MAX_HISTORY_PER_CHECK:]

        if ok:
            lines.append(f"health_watchdog: OK   {name} -- {detail}")
        elif quiet and c.get("night_muted"):
            lines.append(f"HEALTH_WATCHDOG_MUTED: {name} -- {detail} (quiet hours)")
        else:
            line = f"HEALTH_WATCHDOG_ALERT: {name} -- {detail}"
            lines.append(line)
            alerts.append(line)

    print("\n".join(lines) if lines else "health_watchdog: no checks configured")

    if not args.dry:
        tmp = args.history + ".tmp"
        os.makedirs(os.path.dirname(args.history), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
        os.replace(tmp, args.history)

    if args.notify and alerts:
        import subprocess
        body = "\n".join(alerts) + "\nWinfred Quek | CEA R073319H"
        try:
            subprocess.run(["bash", os.path.expanduser("~/.claude/bin/telegram_send.sh"),
                            args.notify], input=body, text=True, timeout=20, check=False)
        except Exception:
            pass

    sys.exit(0)


if __name__ == "__main__":
    main()
