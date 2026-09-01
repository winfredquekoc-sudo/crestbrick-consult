#!/usr/bin/env python3
"""Self-test for scripts/discovery_watchdog.py's evaluate() -- the pure decision logic,
no db/filesystem needed. Confirms: (a) it stays quiet with too little history, (b) it
stays quiet when scanned=0 but nothing inbound arrived (nothing to find is not a bug),
(c) it fires only once BOTH conditions hold across the full window, (d) a single
non-zero slot in the window resets it.

Run directly:  python3 tests/scripts/test_discovery_watchdog.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import discovery_watchdog as W


def slot(date, scanned, inbound):
    return {"date": date, "ran_at": date + "T09:00:00+0800", "slot": "09",
            "landlords_scanned": scanned, "landlords_added": 0,
            "candidates_found": None, "inbound_since_last": inbound}


def main():
    # (a) too little history -> never alerts.
    short = [slot("2026-08-28", 0, 5)]
    alert, msg = W.evaluate(short, "landlords_scanned", min_consecutive_zero=5)
    assert alert is False, msg

    # (b) scanned=0 for the whole window but zero inbound the whole time -> quiet.
    quiet_zero = [slot(f"2026-08-{25+i}", 0, 0) for i in range(5)]
    alert, msg = W.evaluate(quiet_zero, "landlords_scanned", min_consecutive_zero=5)
    assert alert is False, msg

    # (c) scanned=0 for the whole window WITH inbound traffic -> fires.
    broken = [slot(f"2026-08-{25+i}", 0, 3) for i in range(5)]
    alert, msg = W.evaluate(broken, "landlords_scanned", min_consecutive_zero=5)
    assert alert is True, msg
    assert msg.startswith("DISCOVERY_WATCHDOG_ALERT:"), msg

    # (d) one healthy slot inside the window -> does not fire.
    recovering = [slot(f"2026-08-{25+i}", 0, 3) for i in range(4)] + [slot("2026-08-30", 2, 3)]
    alert, msg = W.evaluate(recovering, "landlords_scanned", min_consecutive_zero=5)
    assert alert is False, msg

    # None is treated the same as 0 (a run that never wrote the field is exactly as
    # blind as one that wrote a real zero).
    none_scanned = [slot(f"2026-08-{25+i}", None, 3) for i in range(5)]
    alert, msg = W.evaluate(none_scanned, "landlords_scanned", min_consecutive_zero=5)
    assert alert is True, msg

    print("test_discovery_watchdog: OK (quiet/fire/reset all correct)")


if __name__ == "__main__":
    main()
