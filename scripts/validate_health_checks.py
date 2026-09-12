#!/usr/bin/env python3
"""validate_health_checks.py — creation-time gate for the output-contract convention.

The convention (why this file exists): the recurring, most expensive failure in this
stack is a job that exits 0 but produces nothing, unwatched. The fix is that EVERY new
automated job registers an output contract in scripts/health_watchdog_checks.json — the
file it writes, how fresh it should be, or a metric that must stay positive/moving — so
"did it actually work" is watched by default, not bolted on after the next outage.

This script validates that config so a malformed contract can't ship. Run it before
committing any change to health_watchdog_checks.json (and ideally from CI):

    python3 scripts/validate_health_checks.py            # validates the default config
    python3 scripts/validate_health_checks.py --config PATH

Exit 0 = valid, 1 = problems (printed).

Design note learned 4 Sep 2026: file_fresh only tells the truth for files rewritten
EVERY run (full re-saves, or dedicated run-markers). For a file that rewrites only on
change, a freshness check false-alarms during quiet periods — so prefer a run-marker
(a state/timestamp file updated unconditionally each run) over a content file's mtime.
When you can't, use a generous window and say so in the note.
"""
import argparse
import json
import os
import re
import sys

VALID_TYPES = {"file_fresh", "json_positive", "json_changed",
              "wa_inbound_fresh", "log_pattern_absent", "log_recent_count"}
REQUIRED = {"name", "type"}
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "health_watchdog_checks.json")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _re_ok(pattern):
    if not isinstance(pattern, str) or not pattern:
        return False
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def validate(checks):
    errors = []
    if not isinstance(checks, list):
        return ["top level must be a JSON array of check objects"]
    seen = set()
    for i, c in enumerate(checks):
        where = f"check[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{where}: not an object")
            continue
        name = c.get("name")
        where = f"check '{name}'" if name else where
        for k in REQUIRED:
            if not c.get(k):
                errors.append(f"{where}: missing required key '{k}'")
        if name:
            if name in seen:
                errors.append(f"{where}: duplicate name")
            seen.add(name)
        t = c.get("type")
        if t and t not in VALID_TYPES:
            errors.append(f"{where}: unknown type '{t}' (valid: {sorted(VALID_TYPES)})")
        if t == "file_fresh":
            if not c.get("path"):
                errors.append(f"{where}: file_fresh needs 'path'")
            age = c.get("max_age_hours")
            if not isinstance(age, (int, float)) or age <= 0:
                errors.append(f"{where}: file_fresh needs positive 'max_age_hours'")
        elif t == "json_positive":
            if not c.get("path"):
                errors.append(f"{where}: json_positive needs 'path'")
            if not c.get("field"):
                errors.append(f"{where}: json_positive needs 'field'")
        elif t == "json_changed":
            if not c.get("path"):
                errors.append(f"{where}: json_changed needs 'path'")
            if not c.get("field"):
                errors.append(f"{where}: json_changed needs 'field'")
        elif t == "wa_inbound_fresh":
            if not c.get("path"):
                errors.append(f"{where}: wa_inbound_fresh needs 'path'")
            age = c.get("max_age_minutes")
            if not isinstance(age, (int, float)) or isinstance(age, bool) or age <= 0:
                errors.append(f"{where}: wa_inbound_fresh needs positive 'max_age_minutes'")
            fix = c.get("fix")
            if not isinstance(fix, str) or not fix.strip():
                errors.append(f"{where}: wa_inbound_fresh needs non-empty 'fix'")
        elif t == "log_pattern_absent":
            if not c.get("path"):
                errors.append(f"{where}: log_pattern_absent needs 'path'")
            if not _re_ok(c.get("pattern")):
                errors.append(f"{where}: log_pattern_absent needs a compilable 'pattern'")
            fix = c.get("fix")
            if not isinstance(fix, str) or not fix.strip():
                errors.append(f"{where}: log_pattern_absent needs non-empty 'fix'")
            if "cleared_by" in c and not _re_ok(c.get("cleared_by")):
                errors.append(f"{where}: log_pattern_absent 'cleared_by' does not compile")
        elif t == "log_recent_count":
            if not c.get("path"):
                errors.append(f"{where}: log_recent_count needs 'path'")
            if not _re_ok(c.get("pattern")):
                errors.append(f"{where}: log_recent_count needs a compilable 'pattern'")
            window = c.get("window_minutes")
            if not isinstance(window, (int, float)) or isinstance(window, bool) or window <= 0:
                errors.append(f"{where}: log_recent_count needs positive 'window_minutes'")
            if "max" in c and (not _is_int(c.get("max")) or c["max"] < 0):
                errors.append(f"{where}: log_recent_count 'max' must be a non-negative int")
            fix = c.get("fix")
            if not isinstance(fix, str) or not fix.strip():
                errors.append(f"{where}: log_recent_count needs non-empty 'fix'")
        # common, any type
        if "tail_lines" in c and (not _is_int(c.get("tail_lines")) or c["tail_lines"] <= 0):
            errors.append(f"{where}: 'tail_lines' must be a positive int")
        ah = c.get("active_hours")
        if ah is not None and (not isinstance(ah, list) or len(ah) != 2
                                or not all(_is_int(x) for x in ah)
                                or not (0 <= ah[0] < ah[1] <= 24)):
            errors.append(f"{where}: 'active_hours' must be [start, end] ints, "
                          "0 <= start < end <= 24")
        nm = c.get("night_muted")
        if nm is not None and not isinstance(nm, bool):
            errors.append(f"{where}: 'night_muted' must be a bool")
        fix = c.get("fix")
        if fix is not None and (not isinstance(fix, str) or not fix.strip()):
            errors.append(f"{where}: 'fix' must be a non-empty string")
        # advisory: a check with no note is allowed but discouraged
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()
    try:
        checks = json.load(open(args.config))
    except (OSError, json.JSONDecodeError) as e:
        print(f"validate_health_checks: cannot read/parse {args.config}: {e}")
        sys.exit(1)
    errors = validate(checks)
    if errors:
        print(f"validate_health_checks: {len(errors)} problem(s) in {args.config}:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"validate_health_checks: OK ({len(checks)} checks, all well-formed)")
    sys.exit(0)


if __name__ == "__main__":
    main()
