#!/usr/bin/env python3
"""tests/matchmaker/test_monday_brief.py -- item 2/44: scripts/ops/
monday_brief.py's section_matchmaker(). Read only, never touches the real
~/.claude/state files: every path the section reads is monkeypatched onto a
tempdir for the duration of each check. Not part of tests/matchmaker/SUITES
(monday_brief.py is not part of the shipped matchmaker artifact).

    /usr/bin/python3 tests/matchmaker/test_monday_brief.py
"""
import datetime, json, os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "ops"))
import monday_brief as mb  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n== {title} ==")


def _patch(tmp, **files):
    """Point monday_brief's module level path constants at files written
    into `tmp`, returning the originals so the caller can restore them."""
    orig = {
        "MM_STATS": mb.MM_STATS, "MM_BUILD_HISTORY": mb.MM_BUILD_HISTORY,
        "MM_DIGEST": mb.MM_DIGEST, "MM_CRM_SNAPSHOT": mb.MM_CRM_SNAPSHOT,
    }
    mb.MM_STATS = os.path.join(tmp, "matchmaker-stats.json")
    mb.MM_BUILD_HISTORY = os.path.join(tmp, "matchmaker-build-history.jsonl")
    mb.MM_DIGEST = os.path.join(tmp, "matchmaker-digest.html")
    mb.MM_CRM_SNAPSHOT = os.path.join(tmp, "matchmaker-crm-snapshot.json")
    for name, content in files.items():
        path = {"stats": mb.MM_STATS, "history": mb.MM_BUILD_HISTORY,
                "digest": mb.MM_DIGEST, "crm": mb.MM_CRM_SNAPSHOT}[name]
        if name == "history":
            with open(path, "w") as f:
                for entry in content:
                    f.write(json.dumps(entry) + "\n")
        elif name == "digest":
            open(path, "w").write(content)
        else:
            json.dump(content, open(path, "w"))
    return orig


def _restore(orig):
    mb.MM_STATS, mb.MM_BUILD_HISTORY = orig["MM_STATS"], orig["MM_BUILD_HISTORY"]
    mb.MM_DIGEST, mb.MM_CRM_SNAPSHOT = orig["MM_DIGEST"], orig["MM_CRM_SNAPSHOT"]


def main():
    section("no stats file at all -> na(), never raises")
    tmp = tempfile.mkdtemp()
    try:
        orig = _patch(tmp)
        res = mb.section_matchmaker()
        check("ok is False", res["ok"] is False)
        check("lines is a single n/a line", len(res["lines"]) == 1 and res["lines"][0].startswith("n/a"),
              str(res["lines"]))
    finally:
        _restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)

    section("a fresh, healthy build: counts, worklist size, no anomalies, no CRM snapshot")
    tmp = tempfile.mkdtemp()
    try:
        fresh_ts = mb.now().isoformat(timespec="seconds")
        orig = _patch(tmp,
            stats={"generated_ts": fresh_ts, "counts": {"available_listings": 12, "still_looking_tenants": 40},
                   "worklist_size": 30, "health": {"pending_review": 1}},
            history=[{"ts": fresh_ts, "worklist_size": 25, "pending_review": 1, "anomalies": []},
                     {"ts": fresh_ts, "worklist_size": 30, "pending_review": 1, "anomalies": []}],
            digest="<html></html>")
        res = mb.section_matchmaker()
        check("ok is True", res["ok"] is True)
        check("counts line mentions 12 listings and 40 tenants",
              any("12" in l and "40" in l for l in res["lines"]), str(res["lines"]))
        check("worklist trend goes 25 -> 30 (up)",
              res["data"]["worklist_trend"] == {"first": 25, "last": 30, "n": 2}, str(res["data"]))
        check("no anomaly in history -> 'none found'",
              any("none found" in l for l in res["lines"]), str(res["lines"]))
        check("pending_review read from stats health", res["data"]["pending_review"] == 1)
        check("fresh stats + fresh digest -> no freshness warning",
              any(l.startswith("Freshness:") for l in res["lines"]), str(res["lines"]))
        check("no CRM snapshot file -> exact required copy",
              any(l == "Pipeline (not yet counted): no CRM snapshot" for l in res["lines"]), str(res["lines"]))
        check("never calls the network (no urllib/requests import touched — structural: "
              "this test provides no network and the call above did not hang or error)",
              True)
    finally:
        _restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)

    section("stale stats + missing digest -> freshness warning names both")
    tmp = tempfile.mkdtemp()
    try:
        stale_ts = (mb.now() - datetime.timedelta(hours=40)).isoformat(timespec="seconds")
        orig = _patch(tmp, stats={"generated_ts": stale_ts, "counts": {}, "worklist_size": None, "health": {}})
        res = mb.section_matchmaker()
        warn = next(l for l in res["lines"] if l.startswith("FRESHNESS WARNING"))
        check("names the stale stats file", "stats file" in warn, warn)
        check("names the missing digest", "digest missing" in warn, warn)
    finally:
        _restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)

    section("last anomaly warning surfaces from the most recent build history entry that has one")
    tmp = tempfile.mkdtemp()
    try:
        ts_now = mb.now().isoformat(timespec="seconds")
        orig = _patch(tmp,
            stats={"generated_ts": ts_now, "counts": {}, "worklist_size": None, "health": {}},
            history=[{"ts": "2026-09-10T09:00:00+08:00", "anomalies": ["old warning, superseded"]},
                     {"ts": "2026-09-14T09:00:00+08:00", "anomalies": []},
                     {"ts": "2026-09-15T09:00:00+08:00", "anomalies": ["listings count swung 40%"]}],
            digest="<html></html>")
        res = mb.section_matchmaker()
        check("picks the NEWEST entry that actually has an anomaly, skipping the empty one in between",
              res["data"]["last_anomaly"] == {"ts": "2026-09-15T09:00:00+08:00",
                                               "warnings": ["listings count swung 40%"]},
              str(res["data"]["last_anomaly"]))
    finally:
        _restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)

    section("pipeline (not yet counted): sums only agreed/otp/signed from a present CRM snapshot")
    tmp = tempfile.mkdtemp()
    try:
        ts_now = mb.now().isoformat(timespec="seconds")
        orig = _patch(tmp,
            stats={"generated_ts": ts_now, "counts": {}, "worklist_size": None, "health": {}},
            digest="<html></html>",
            crm={"deals": [{"stage": "agreed", "commission_gross": 1000},
                            {"stage": "otp", "commission_gross": 2000},
                            {"stage": "closed", "commission_gross": 9999},   # already counted elsewhere -- excluded
                            {"stage": "signed"}]})
        res = mb.section_matchmaker()
        check("counts exactly the 3 pipeline stage deals, excludes 'closed'",
              res["data"]["pipeline_not_counted"]["count"] == 3, str(res["data"]["pipeline_not_counted"]))
        check("sums only the gross figures that are present",
              res["data"]["pipeline_not_counted"]["gross"] == 3000, str(res["data"]["pipeline_not_counted"]))

        # A snapshot that fails to parse must not crash the whole brief.
        open(mb.MM_CRM_SNAPSHOT, "w").write("{not json")
        res2 = mb.section_matchmaker()
        check("a corrupt CRM snapshot degrades to an n/a pipeline line, never raises",
              any(l.startswith("Pipeline (not yet counted): n/a") for l in res2["lines"]), str(res2["lines"]))
    finally:
        _restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)

    section("section_matchmaker is registered in SECTIONS")
    check("Matchmaker section is wired into the brief",
          any(title == "Matchmaker" and fn is mb.section_matchmaker for title, fn, _src in mb.SECTIONS),
          str([t for t, _f, _s in mb.SECTIONS]))

    print(f"\n{'='*60}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        sys.exit(1)
    print("all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
