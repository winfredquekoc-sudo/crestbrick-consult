#!/usr/bin/env python3
"""Self-test for scripts/health_watchdog.py's check functions and quiet-hours helper.

Confirms: (a) file_fresh passes a just-written file and fails a stale/missing one,
(b) json_positive honours the min threshold and flags a missing/non-numeric field,
(c) json_changed stays quiet while accumulating samples, fires on a flat run, and
resets when the value moves, (d) the SGT quiet-hours window is [23:00, 08:00).

Run directly:  python3 tests/scripts/test_health_watchdog.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import health_watchdog as H


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def main():
    d = tempfile.mkdtemp()

    # (a) file_fresh: fresh passes, stale fails, missing fails.
    fresh = os.path.join(d, "fresh.json")
    _write(fresh, {"n": 1})
    ok, _, _ = H.check_file_fresh({"path": fresh, "max_age_hours": 24})
    assert ok, "just-written file should be fresh"
    old = os.path.join(d, "old.json")
    _write(old, {"n": 1})
    os.utime(old, (time.time() - 40 * 3600, time.time() - 40 * 3600))
    ok, _, detail = H.check_file_fresh({"path": old, "max_age_hours": 30})
    assert not ok and "stale" in detail, detail
    ok, _, detail = H.check_file_fresh({"path": os.path.join(d, "nope.json"), "max_age_hours": 30})
    assert not ok and "missing" in detail, detail

    # (b) json_positive: threshold + missing field.
    metric = os.path.join(d, "m.json")
    _write(metric, {"candidates_found": 165, "zero": 0})
    ok, val, _ = H.check_json_positive({"path": metric, "field": "candidates_found", "min": 1})
    assert ok and val == 165, val
    ok, _, _ = H.check_json_positive({"path": metric, "field": "zero", "min": 1})
    assert not ok, "zero < min should fail"
    ok, _, detail = H.check_json_positive({"path": metric, "field": "absent", "min": 1})
    assert not ok and "missing" in detail, detail

    # (c) json_changed: quiet while learning, fires flat, resets on change.
    c = {"path": metric, "field": "candidates_found", "min_span": 4}
    hist = []
    for i in range(4):  # first 4 samples: not enough history yet -> OK
        ok, val, _ = H.check_json_changed(c, hist)
        assert ok, f"sample {i} should be OK while learning"
        hist.append({"ts": str(i), "value": val})
    ok, _, detail = H.check_json_changed(c, hist)   # 5th identical -> flat alarm
    assert not ok and "stuck" in detail, detail
    moved = hist[:-1] + [{"ts": "x", "value": 999}]  # a moved value in the window -> OK
    ok, _, _ = H.check_json_changed(c, moved)
    assert ok, "a changing value must not alarm"

    # (d) quiet-hours window is [23:00, 08:00).
    assert H._is_quiet(23) and H._is_quiet(3) and H._is_quiet(7)
    assert not H._is_quiet(8) and not H._is_quiet(12) and not H._is_quiet(22)

    print("test_health_watchdog: OK (file_fresh/json_positive/json_changed/quiet all correct)")


if __name__ == "__main__":
    main()
