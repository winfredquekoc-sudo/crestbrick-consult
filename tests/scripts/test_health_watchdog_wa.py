#!/usr/bin/env python3
"""Self-test for the WhatsApp health checks in scripts/health_watchdog.py.

Confirms: (a) wa_inbound_fresh finds the true newest inbound row by PARSING timestamps
even when a mixed-offset row is lexically newer than the real newest, (b) log_pattern_absent
respects tail_lines and cleared_by, (c) log_recent_count only counts parseable, in-window
hits and tallies unparsed matching lines into the detail, (d) the active_hours window
helper, (e) main() end to end via subprocess honours active_hours/night_muted/fix, never
writes history under --dry, and the wa-bridge-405 fix uses the gui/$(id -u)/ launchctl
target, (f) the config validator accepts the real config and rejects a batch of malformed
entries including bad tail_lines and a missing wa_inbound_fresh fix, (g) a check that
raises never kills the sweep, (h) _oneline collapses whitespace, (i) _parse_ts accepts a
trailing Z.

Run directly:  python3 tests/scripts/test_health_watchdog_wa.py
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import health_watchdog as H
import validate_health_checks as V

JID = "6500000000@s.whatsapp.net"
WATCHDOG_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                               "scripts", "health_watchdog.py")


def _write_lines(path, lines):
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _ts_text(dt):
    """dt (aware) -> 'YYYY-MM-DD HH:MM:SS+HH:MM' text, the messages.db format."""
    off_min = int(dt.utcoffset().total_seconds() // 60)
    sign = "+" if off_min >= 0 else "-"
    off_min = abs(off_min)
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f"{sign}{off_min // 60:02d}:{off_min % 60:02d}"


def _make_messages_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("""create table messages (
        id TEXT, chat_jid TEXT, sender TEXT, content TEXT,
        timestamp TIMESTAMP, is_from_me BOOLEAN)""")
    con.executemany(
        "insert into messages (id, chat_jid, sender, content, timestamp, is_from_me) "
        "values (?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def main():
    d = tempfile.mkdtemp()

    # (a) wa_inbound_fresh: mixed offsets, must find the true newest by parsing.
    NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
    NEG4 = datetime.timezone(datetime.timedelta(hours=-4))
    t_selfsent = _ts_text((NOW - datetime.timedelta(minutes=1)).astimezone(H.SGT))
    t_10min = _ts_text((NOW - datetime.timedelta(minutes=10)).astimezone(NEG4))
    t_5h = _ts_text((NOW - datetime.timedelta(hours=5)).astimezone(H.SGT))
    assert t_10min < t_5h, "fixture must be lexically-newer-but-actually-older, as intended"

    db1 = os.path.join(d, "messages_mixed.db")
    _make_messages_db(db1, [
        ("m1", JID, JID, "hi", t_selfsent, 1),   # outbound, must be ignored
        ("m2", JID, JID, "hey", t_10min, 0),     # true newest inbound (-04:00)
        ("m3", JID, JID, "yo", t_5h, 0),         # lexically newer, actually 5h old
    ])
    ok, val, detail = H.check_wa_inbound_fresh({"path": db1, "max_age_minutes": 45}, now=NOW)
    assert ok and 8 <= val <= 12, (ok, val, detail)
    ok, _, detail = H.check_wa_inbound_fresh({"path": db1, "max_age_minutes": 5}, now=NOW)
    assert not ok and "no inbound message" in detail, detail
    ok, _, detail = H.check_wa_inbound_fresh({"path": os.path.join(d, "nope.db")}, now=NOW)
    assert not ok and "missing" in detail, detail

    db2 = os.path.join(d, "messages_selfonly.db")
    _make_messages_db(db2, [("m1", JID, JID, "hi", t_selfsent, 1)])
    ok, _, detail = H.check_wa_inbound_fresh({"path": db2, "max_age_minutes": 45}, now=NOW)
    assert not ok and "no inbound" in detail, detail

    # (b) log_pattern_absent: tail window + cleared_by.
    log405 = os.path.join(d, "bridge.log")
    pattern = r"Client outdated \(405\)"
    line_405 = ("05:47:50.908 [Client ERROR] Client outdated (405) connect failure "
               "(client version: 2.3000.1038187123)")

    early = [f"line {i}" for i in range(300)]
    early[49] = line_405
    _write_lines(log405, early)
    ok, val, detail = H.check_log_pattern_absent({"path": log405, "pattern": pattern,
                                                  "tail_lines": 200})
    assert ok and val == 0, detail   # line 50 sits outside the last 200 of 300

    late = [f"line {i}" for i in range(300)]
    late[249] = line_405
    _write_lines(log405, late)
    ok, val, detail = H.check_log_pattern_absent({"path": log405, "pattern": pattern,
                                                  "tail_lines": 200})
    assert not ok and val == 1 and "present" in detail, (ok, val, detail)

    with open(log405, "a") as f:
        f.write("[Client INFO] Connected to WhatsApp\n")
    ok, val, detail = H.check_log_pattern_absent({
        "path": log405, "pattern": pattern, "tail_lines": 200,
        "cleared_by": r"\[Client INFO\] Connected to WhatsApp"})
    assert ok and "cleared" in detail, detail

    ok, _, detail = H.check_log_pattern_absent({"path": os.path.join(d, "nope.log"),
                                                "pattern": pattern})
    assert not ok and "missing" in detail, detail

    all_lines = [f"l{i}" for i in range(10)]
    tail_path = os.path.join(d, "tail.log")
    _write_lines(tail_path, all_lines)
    assert H._tail_lines(tail_path, 3) == all_lines[-3:]
    assert H._tail_lines(tail_path, 100) == all_lines

    # (c) log_recent_count: only parseable, in-window hits count.
    NOW_SGT = datetime.datetime(2026, 9, 9, 20, 0, 0, tzinfo=H.SGT)
    sf_pattern = r"\| SEND_FAIL \|"
    sf_10 = ((NOW_SGT - datetime.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            + " | SEND_FAIL | 6500000000 | SEND_FORM")
    sf_2h = ((NOW_SGT - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            + " | SEND_FAIL | 6500000000 | SEND_FORM")
    sent_5 = ((NOW_SGT - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
             + " | SENT | 6500000000 | SEND_FORM")
    garbage = "not-a-timestamp-line | SEND_FAIL | oops"
    log_sf = os.path.join(d, "dry-run-preview.log")
    _write_lines(log_sf, [sf_10, sf_2h, sent_5, garbage])

    ok, val, detail = H.check_log_recent_count(
        {"path": log_sf, "pattern": sf_pattern, "window_minutes": 60, "max": 0}, now=NOW_SGT)
    assert not ok and val == 1, (ok, val, detail)
    assert ("1 matching line(s) without a parseable timestamp" in detail), detail  # garbage
    ok, val, _ = H.check_log_recent_count(
        {"path": log_sf, "pattern": sf_pattern, "window_minutes": 60, "max": 1}, now=NOW_SGT)
    assert ok and val == 1
    ok, val, _ = H.check_log_recent_count(
        {"path": log_sf, "pattern": sf_pattern, "window_minutes": 180, "max": 0}, now=NOW_SGT)
    assert not ok and val == 2
    ok, _, detail = H.check_log_recent_count(
        {"path": os.path.join(d, "nope2.log"), "pattern": sf_pattern, "window_minutes": 60},
        now=NOW_SGT)
    assert not ok and "missing" in detail, detail

    # (d) active_hours window helper.
    assert H._in_window(7, {"active_hours": [8, 23]}) is False
    assert H._in_window(8, {"active_hours": [8, 23]}) is True
    assert H._in_window(22, {"active_hours": [8, 23]}) is True
    assert H._in_window(23, {"active_hours": [8, 23]}) is False
    assert H._in_window(12, {}) is True

    # (h) _oneline: collapses any run of whitespace, including newlines.
    assert H._oneline("a\nb  c") == "a b c"

    # (i) _parse_ts: trailing Z (3.9 fromisoformat rejects it natively) parses as UTC.
    ts = H._parse_ts("2026-09-09T02:00:00Z")
    assert ts is not None and ts.tzinfo is not None
    assert ts.utcoffset() == datetime.timedelta(0), ts

    # (e) end to end through main(), using the real config's 3 new entries with
    # fixture paths swapped in and made to fail deterministically.
    real_now_utc = datetime.datetime.now(datetime.timezone.utc)
    stale_text = _ts_text((real_now_utc - datetime.timedelta(hours=5)).astimezone(H.SGT))
    stale_db = os.path.join(d, "e2e_stale_messages.db")
    _make_messages_db(stale_db, [("m1", JID, JID, "hi", stale_text, 0)])

    bridge_log = os.path.join(d, "e2e_bridge.log")
    _write_lines(bridge_log, [
        "00:00:00.000 [Client INFO] Starting WhatsApp client...",
        line_405,
    ])

    real_now_sgt = datetime.datetime.now(H.SGT)
    sendfail_line = ((real_now_sgt - datetime.timedelta(minutes=5))
                     .strftime("%Y-%m-%d %H:%M:%S") + " | SEND_FAIL | 6500000000 | SEND_FORM")
    sendfail_log = os.path.join(d, "e2e_dry-run-preview.log")
    _write_lines(sendfail_log, [sendfail_line])

    real_checks = json.load(open(H.DEFAULT_CONFIG))
    swap = {"wa-inbound-silence": stale_db, "wa-bridge-405": bridge_log,
           "wa-send-fail-recent": sendfail_log}
    tmp_checks = [dict(c, path=swap[c["name"]]) for c in real_checks if c.get("name") in swap]
    assert len(tmp_checks) == 3, "expected exactly the 3 new checks in the real config"

    tmp_config = os.path.join(d, "e2e_config.json")
    json.dump(tmp_checks, open(tmp_config, "w"))
    tmp_history = os.path.join(d, "e2e_history.json")

    def _run(hour):
        proc = subprocess.run(
            [sys.executable, WATCHDOG_SCRIPT, "--config", tmp_config, "--history", tmp_history,
             "--dry", "--force-hour", str(hour)],
            capture_output=True, text=True, timeout=30)
        return proc.stdout

    out3 = _run(3)
    assert "HEALTH_WATCHDOG_ALERT: wa-bridge-405" in out3, out3
    alert_line = next(l for l in out3.splitlines() if "ALERT: wa-bridge-405" in l)
    assert "whatsmeow" in alert_line and "rebuild" in alert_line, alert_line
    assert "gui/$(id -u)/com.crestbrick.whatsapp-bridge" in alert_line, alert_line
    assert "SKIP wa-inbound-silence" in out3, out3
    assert "SKIP wa-send-fail-recent" in out3, out3
    assert not os.path.exists(tmp_history), "--dry must never write history"

    out12 = _run(12)
    for name in ("wa-inbound-silence", "wa-bridge-405", "wa-send-fail-recent"):
        assert f"HEALTH_WATCHDOG_ALERT: {name}" in out12, out12
    assert not os.path.exists(tmp_history), "--dry must never write history"

    # (g) main(): a check that raises (open() on a directory) never kills the sweep,
    # and the other checks in the same config still run and print.
    broken_dir = os.path.join(d, "not_a_file_dir")
    os.mkdir(broken_dir)
    ok_file = os.path.join(d, "ok.json")
    json.dump({"n": 1}, open(ok_file, "w"))
    guard_config = [
        {"name": "will-raise", "type": "log_recent_count", "path": broken_dir,
         "pattern": "x", "window_minutes": 60, "fix": "n/a"},
        {"name": "will-pass", "type": "file_fresh", "path": ok_file, "max_age_hours": 24},
    ]
    guard_config_path = os.path.join(d, "guard_config.json")
    json.dump(guard_config, open(guard_config_path, "w"))
    guard_history = os.path.join(d, "guard_history.json")
    guard_proc = subprocess.run(
        [sys.executable, WATCHDOG_SCRIPT, "--config", guard_config_path,
         "--history", guard_history, "--dry"],
        capture_output=True, text=True, timeout=30)
    assert guard_proc.returncode == 0, guard_proc.stderr
    assert ("HEALTH_WATCHDOG_ALERT: will-raise -- check raised"
           in guard_proc.stdout), guard_proc.stdout
    assert "OK   will-pass" in guard_proc.stdout, guard_proc.stdout

    # (f) validator: real config clean, a batch of malformed entries flagged.
    errors = V.validate(json.load(open(H.DEFAULT_CONFIG)))
    assert errors == [], errors

    bad = [
        {"name": "t1", "type": "log_pattern_absent", "path": "/tmp/x.log", "pattern": "foo"},
        {"name": "t2", "type": "file_fresh", "path": "/tmp/y", "max_age_hours": 10,
         "active_hours": [23, 8]},
        {"name": "t3", "type": "totally_unknown"},
        {"name": "t4", "type": "log_recent_count", "path": "/tmp/z.log", "pattern": "foo",
         "window_minutes": 60, "fix": "x", "tail_lines": 0},
        {"name": "t5", "type": "log_recent_count", "path": "/tmp/z.log", "pattern": "foo",
         "window_minutes": 60, "fix": "x", "tail_lines": "5000"},
        {"name": "t6", "type": "wa_inbound_fresh", "path": "/tmp/db", "max_age_minutes": 45},
    ]
    errors = V.validate(bad)
    assert len(errors) == 6, errors
    assert any("t4" in e and "tail_lines" in e for e in errors), errors
    assert any("t5" in e and "tail_lines" in e for e in errors), errors
    assert any("t6" in e and "fix" in e for e in errors), errors

    print("test_health_watchdog_wa: OK (wa_inbound_fresh/log_pattern_absent/"
         "log_recent_count/active_hours/main/validator all correct)")


if __name__ == "__main__":
    main()
