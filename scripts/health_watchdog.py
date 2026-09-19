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
  file_fresh        {path, max_age_hours}      -- file mtime must be within the window
  json_positive     {path, field, min}         -- numeric field (dotted) must be >= min (default 1)
  json_changed      {path, field, min_span}    -- numeric field must have CHANGED across the
                                                  last min_span recorded snapshots (flat = alarm)
  wa_inbound_fresh  {path, max_age_minutes, python} -- newest inbound (is_from_me=0) row in a
                                                  WhatsApp messages.db must be within the window;
                                                  queried out-of-process on SYSTEM_PYTHON so a
                                                  read-only sqlite open never touches the main
                                                  interpreter's imports
  log_pattern_absent {path, pattern, tail_lines, cleared_by} -- regex must not appear in the
                                                  tail of a log, unless cleared_by matches on a
                                                  later line in the same tail
  log_recent_count  {path, pattern, window_minutes, max, tail_lines} -- count of regex matches
                                                  timestamped within the recent window must be <= max
Common per-check keys:
  name          required, unique
  night_muted   if true, an alert during SGT quiet hours (23:00-08:00) is emitted as
                HEALTH_WATCHDOG_MUTED: instead of ...ALERT:, so a nightly caller that
                greps for ALERT does not ping at 3am for a daytime job.
  active_hours  [start, end) SGT hour window the check should even run in; outside it the
                check is SKIPPED entirely (no OK/ALERT line, no history entry).
  fix           free text; on a failing check it is appended to the detail as
                " | fix: ..." so the remedy rides along on the ALERT/MUTED line.

Read only except its own history file (counts only -- never a name, phone, or jid).
Exit code is always 0 (a health check must never fail the thing that calls it).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "health_watchdog_checks.json")
DEFAULT_HISTORY = os.path.expanduser("~/.claude/state/health-watchdog-history.json")
MAX_HISTORY_PER_CHECK = 60
QUIET_START, QUIET_END = 23, 8   # SGT hours [23:00, 08:00) are quiet
SGT = datetime.timezone(datetime.timedelta(hours=8))
SYSTEM_PYTHON = "/usr/bin/python3"

# Runs out-of-process (argv: path, lower_bound) so a bad/locked db can't wedge the
# watchdog itself. Two-pass: prefiltered recent rows first, else the newest 50 rows
# overall so a stale detail can still say HOW stale.
QUERY_SCRIPT = """
import json, sqlite3, sys, urllib.request
path, lower_bound = sys.argv[1], sys.argv[2]
uri = "file:" + urllib.request.pathname2url(path) + "?mode=ro"
con = sqlite3.connect(uri, uri=True, timeout=10)
cur = con.cursor()
cur.execute(
    "select timestamp from messages where is_from_me=0 and timestamp >= ? "
    "order by timestamp desc limit 5000", (lower_bound,))
rows = [r[0] for r in cur.fetchall()]
if not rows:
    cur.execute(
        "select timestamp from messages where is_from_me=0 "
        "order by timestamp desc limit 50")
    rows = [r[0] for r in cur.fetchall()]
print(json.dumps(rows))
"""


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
    return datetime.datetime.now(SGT).hour


def _is_quiet(hour):
    return hour >= QUIET_START or hour < QUIET_END


def _in_window(hour, c):
    ah = c.get("active_hours")
    if not ah:
        return True
    start, end = ah
    return start <= hour < end


def _oneline(s):
    return " ".join(str(s).split())


def _parse_ts(text):
    try:
        text = text.replace("Z", "+00:00")  # 3.9 fromisoformat rejects trailing Z
        dt = datetime.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SGT)
    return dt


def _tail_lines(path, n):
    """Last n lines, read backwards in 64 KiB blocks (log only ever grows)."""
    block = 65536
    data = b""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0 and data.count(b"\n") <= n:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


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


def check_wa_inbound_fresh(c, now=None):
    """Newest inbound row must be recent. Queried out-of-process because the db is
    someone else's live WAL file and rowid order is NOT time order (history sync
    inserts old rows late), so the only correct answer comes from parsing timestamps."""
    path = os.path.expanduser(c["path"])
    if not os.path.exists(path):
        return False, None, f"missing ({path})"
    limit = c.get("max_age_minutes", 45)
    python = c.get("python", SYSTEM_PYTHON)
    if not os.path.exists(python):
        python = sys.executable
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lower_bound = (now - datetime.timedelta(minutes=limit)
                   - datetime.timedelta(hours=14)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        proc = subprocess.run([python, "-c", QUERY_SCRIPT, path, lower_bound],
                              capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, None, f"query failed: {_oneline(e)[:160]}"
    if proc.returncode != 0:
        return False, None, f"query failed: {_oneline(proc.stderr)[-160:]}"
    try:
        rows = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return False, None, f"query failed: {_oneline(proc.stdout)[:160]}"
    newest = None
    for text in rows:
        ts = _parse_ts(text)
        if ts is not None and (newest is None or ts > newest):
            newest = ts
    if newest is None:
        return False, None, "no inbound rows in messages.db"
    age_min = (now - newest).total_seconds() / 60
    ok = age_min <= limit
    detail = (f"no inbound message for {age_min:.0f} min (limit {limit} min; "
             f"newest {newest.isoformat()})" if not ok
             else f"newest inbound {age_min:.0f} min ago")
    return ok, round(age_min, 1), detail


def check_log_pattern_absent(c):
    path = os.path.expanduser(c["path"])
    if not os.path.exists(path):
        return False, None, f"missing ({path})"
    n = c.get("tail_lines", 200)
    pattern = c["pattern"]
    lines = _tail_lines(path, n)
    rx = re.compile(pattern)
    hits = [i for i, line in enumerate(lines) if rx.search(line)]
    if not hits:
        return True, 0, f"no '{pattern}' in last {n} lines"
    cleared_by = c.get("cleared_by")
    if cleared_by:
        crx = re.compile(cleared_by)
        if any(crx.search(line) for line in lines[hits[-1] + 1:]):
            return True, len(hits), (f"'{pattern}' seen but cleared by "
                                     f"'{cleared_by}' later in the tail")
    basename = os.path.basename(path)
    return False, len(hits), (f"'{pattern}' present in last {n} lines of "
                              f"{basename} ({len(hits)} hit(s))")


def check_log_recent_count(c, now=None):
    path = os.path.expanduser(c["path"])
    if not os.path.exists(path):
        return False, None, f"missing ({path})"
    pattern = c["pattern"]
    rx = re.compile(pattern)
    window = c.get("window_minutes", 60)
    limit = c.get("max", 0)
    n = c.get("tail_lines", 5000)
    now = now or datetime.datetime.now(SGT)
    cutoff = now - datetime.timedelta(minutes=window)
    count, unparsed = 0, 0
    for line in _tail_lines(path, n):
        if not rx.search(line):
            continue
        try:
            ts = datetime.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            unparsed += 1
            continue
        if ts.replace(tzinfo=SGT) >= cutoff:
            count += 1
    ok = count <= limit
    suffix = (f" ({unparsed} matching line(s) without a parseable timestamp)"
             if unparsed else "")
    detail = (f"{count} '{pattern}' line(s) in last {window} min (max {limit}){suffix}"
             if not ok else f"{count} in last {window} min{suffix}")
    return ok, count, detail


def run_check(c, history_for_check):
    t = c["type"]
    if t == "file_fresh":
        return check_file_fresh(c)
    if t == "json_positive":
        return check_json_positive(c)
    if t == "json_changed":
        return check_json_changed(c, history_for_check)
    if t == "wa_inbound_fresh":
        return check_wa_inbound_fresh(c)
    if t == "log_pattern_absent":
        return check_log_pattern_absent(c)
    if t == "log_recent_count":
        return check_log_recent_count(c)
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

    hour = _sgt_hour(args.force_hour)
    quiet = _is_quiet(hour)
    alerts, lines = [], []

    for c in checks:
        name = c.get("name", "?")
        if not _in_window(hour, c):
            s, e = c["active_hours"]
            lines.append(f"health_watchdog: SKIP {name} -- outside active hours "
                         f"{s:02d}:00-{e:02d}:00 SGT")
            continue

        hist = history.get(name, [])
        try:
            ok, value, detail = run_check(c, hist)
        except Exception as e:
            ok, value, detail = False, None, "check raised %s: %s" % (
                type(e).__name__, _oneline(e)[:160])

        if value is not None:
            hist.append({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                         "value": value})
            history[name] = hist[-MAX_HISTORY_PER_CHECK:]

        if not ok and c.get("fix"):
            detail = f"{detail} | fix: {c['fix']}"
        detail = _oneline(detail)

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
        body = "\n".join(alerts) + "\nWinfred Quek | CEA R073319H"
        try:
            subprocess.run(["bash", os.path.expanduser("~/.claude/bin/telegram_send.sh"),
                            args.notify], input=body, text=True, timeout=20, check=False)
        except Exception:
            pass

    sys.exit(0)


if __name__ == "__main__":
    main()
