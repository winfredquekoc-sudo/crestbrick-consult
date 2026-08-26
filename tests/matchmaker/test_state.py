#!/usr/bin/env python3
"""Pure assert suite for scripts/tenant_state.py — the canonical tenant state
machine. No pytest, stdlib only:
    /usr/bin/python3 tests/matchmaker/test_state.py

All fixture people are invented (SG plausible, obviously fake). The live bug
this module exists to kill: a tenant auto closed for silence stayed closed
forever even after writing back (Joan / Dhibasri, 21 Aug 2026).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "scripts")))
import tenant_state as TS  # noqa: E402

FAILED = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except AssertionError as e:
        FAILED.append(name)
        print(f"  FAIL {name}: {e}")


def auto_closed(**over):
    t = {
        "id": "TXX", "name": "Tan Ah Test", "status": "closed (stale)",
        "prev_status": "profile-received", "contact_state": "active",
        "closed_reason": f"{TS.AUTO_CLOSE_PREFIX} 2026-07-01 (>45 days)",
        "closed_date": "2026-08-20", "last_contact": "2026-07-01",
    }
    t.update(over)
    return t


# ------------------------------------------------------------- config ----
def t_config():
    cfg = TS.load_config()
    assert cfg["stale_days"] == cfg["dead_days"], "stale and dead must be one rule"
    assert cfg["dead_days"] == 45, f"dead_days is {cfg['dead_days']}, spec says 45 (Winfred, 21 Aug 2026)"
check("config: loads, stale==dead, spec value 45", t_config)


# ------------------------------------------------------------- reopen ----
def t_reopen_joan():
    # THE Joan case: auto closed on 20 Aug, wrote back on 21 Aug.
    t = auto_closed(last_contact="2026-08-21")
    assert TS.reopen_if_returned(t) is True
    assert t["status"] == "profile-received", t["status"]
    assert t["closed_reason"] == "" and t["closed_date"] == ""
    assert "reopened: contact resumed 2026-08-21" in t["follow_up"]
check("reopen: auto closed row whose contact resumed reopens to prev_status", t_reopen_joan)

def t_reopen_needs_newer_contact():
    assert TS.reopen_if_returned(auto_closed()) is False, "contact older than close must stay closed"
    assert TS.reopen_if_returned(auto_closed(last_contact="2026-08-20")) is False, "same day is not newer"
check("reopen: does nothing without contact NEWER than closed_date", t_reopen_needs_newer_contact)

def t_reopen_only_sweep_closes():
    hand = auto_closed(last_contact="2026-08-21", closed_reason="closed by Winfred: wrong profile")
    assert TS.reopen_if_returned(hand) is False, "hand closes never reopen"
    found = auto_closed(last_contact="2026-08-21", contact_state="found_place")
    assert TS.reopen_if_returned(found) is False, "found_place never reopens"
    dnc = auto_closed(last_contact="2026-08-21", contact_state="not_interested")
    assert TS.reopen_if_returned(dnc) is False, "not_interested never reopens"
    open_row = auto_closed(status="active", last_contact="2026-08-21")
    assert TS.reopen_if_returned(open_row) is False, "non closed rows are not reopen candidates"
check("reopen: ONLY silence sweep closes reopen — hand/found/not_interested never", t_reopen_only_sweep_closes)

def t_reopen_without_prev_status():
    t = auto_closed(last_contact="2026-08-21", prev_status="")
    assert TS.reopen_if_returned(t) is True
    assert t["status"] == "open", "missing prev_status falls back to open"
check("reopen: missing prev_status falls back to open", t_reopen_without_prev_status)


# -------------------------------------------------------------- stale ----
CUTOFF = "2026-07-07"  # 45 days before 21 Aug 2026

def t_stale_boundary():
    base = {"status": "profile-received", "contact_state": "active"}
    assert TS.is_stale({**base, "last_contact": "2026-07-06"}, CUTOFF) is True, "day before cutoff is stale"
    assert TS.is_stale({**base, "last_contact": "2026-07-07"}, CUTOFF) is False, "cutoff day itself is not stale"
    assert TS.is_stale({**base, "last_contact": ""}, CUTOFF) is False, "no date, nothing to age against"
    assert TS.is_stale({**base, "last_contact": "", "data_collected": "2026-06-01"}, CUTOFF) is True, "data_collected is the fallback clock"
check("stale: strict boundary at cutoff, data_collected fallback", t_stale_boundary)

def t_stale_protections():
    assert TS.is_stale({"status": "tenanted", "last_contact": "2026-01-01"}, CUTOFF) is False
    assert TS.is_stale({"status": "active", "contact_state": "found_place", "last_contact": "2026-01-01"}, CUTOFF) is False
    assert TS.is_stale({"status": "active", "excluded": True, "last_contact": "2026-01-01"}, CUTOFF) is False
    assert TS.is_stale({"status": "closed (stale)", "last_contact": "2026-01-01"}, CUTOFF) is False, "never re close a closed row"
    assert TS.is_stale({"status": "rejected", "contact_state": "active", "last_contact": "2026-01-01"}, CUTOFF) is True, "listing level rejection still ages out"
check("stale: terminal/excluded/closed protected, bare rejected ages out", t_stale_protections)

def t_close_stale_fields():
    t = {"id": "TXX", "status": "open", "contact_state": "active", "last_contact": "2026-06-01"}
    TS.close_stale(t, "2026-08-21", 45)
    assert t["status"] == "closed (stale)" and t["prev_status"] == "open"
    assert t["closed_reason"].startswith(TS.AUTO_CLOSE_PREFIX) and "(>45 days)" in t["closed_reason"]
    assert t["closed_date"] == "2026-08-21" and t["match_status"] == "stale_closed"
check("close_stale: stamps prefix/prev_status/date/match_status", t_close_stale_fields)

def t_close_then_return_roundtrip():
    t = {"id": "TXX", "status": "form-sent", "contact_state": "active", "last_contact": "2026-06-01"}
    TS.close_stale(t, "2026-08-21", 45)
    t["last_contact"] = "2026-08-25"  # they wrote back
    assert TS.reopen_if_returned(t) is True
    assert t["status"] == "form-sent"
    assert TS.match_status_for(t, kids=False) == "active"
check("roundtrip: close for silence, contact resumes, row is matchable again", t_close_then_return_roundtrip)


# ------------------------------------------------------- match_status ----
def t_match_status():
    f = TS.match_status_for
    assert f({"contact_state": "found_place"}, False) == "found"
    assert f({"status": "tenanted"}, False) == "found"
    assert f({"status": "closed (stale)"}, False) == "stale_closed"
    assert f({"contact_state": "not_interested"}, False) == "do_not_contact"
    assert f({"excluded": True}, False) == "do_not_contact"
    assert f({"nationality": "Indian"}, False) == "excluded_india"
    assert f({"nationality": "Singaporean", "ethnicity": "Indian"}, False) == "active", "SC of Indian ethnicity is NOT excluded"
    assert f({}, True) == "excluded_family"
    assert f({"status": "deposit-pending"}, False) == "in_deal"
    assert f({"lease_term_months": 3}, False) == "short_lease"
    assert f({"lease_term_months": 8}, False) == "below_target"
    assert f({"lease_term_months": 12}, False) == "active"
    assert f({}, False) == "active", "unknown lease stays in the pool"
check("match_status_for: verbatim buckets incl. SC Indian ethnicity exemption", t_match_status)

def t_serve_priority():
    g = TS.serve_priority_for
    assert g({"lease_term_months": 12}, "active") == 1
    assert g({"lease_term_months": 8}, "below_target") == 2
    assert g({}, "active") == 3
    assert g({"lease_term_months": 24}, "stale_closed") == ""
check("serve_priority_for: 12mo+ first, 6 to 11 second, unknown third, off pool blank", t_serve_priority)


print("=" * 60)
if FAILED:
    print(f"{len(FAILED)} check(s) FAILED: {FAILED}")
    sys.exit(1)
print("all checks passed")
