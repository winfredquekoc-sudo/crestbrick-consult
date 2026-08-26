#!/usr/bin/env python3
"""
tenant_state.py — the ONE home for tenant lifecycle state (Winfred, 21 Aug 2026).

Before this module, three scripts each derived partial truth (status /
contact_state / match_status) and none owned transitions, which produced the
live bug where a tenant auto closed for silence stayed closed forever even
after they wrote back (last_contact newer than closed_date, no reopen).

Rules live here; the scripts import them:
  close_stale_prospects.py   -> reopen_if_returned() then is_stale()
  apply_tenant_lifecycle.py  -> match_status_for() + serve_priority_for()

Thresholds come from scripts/matchmaker/config.json — the single home.
scoring.js keeps mirror literals for the browser; build.py fails the build
if they drift from config.json.
"""
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchmaker", "config.json")


def load_config():
    """Strict read. A missing or broken config must stop the pipeline loudly,
    never fall back to a silent hardcoded number."""
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    for k in ("stale_days", "dead_days", "cold_days", "info_rich_min"):
        if not isinstance(cfg.get(k), int):
            raise SystemExit(f"tenant_state.py: {_CONFIG_PATH} is missing integer '{k}' — refusing to run on defaults.")
    if cfg["stale_days"] != cfg["dead_days"]:
        raise SystemExit("tenant_state.py: stale_days and dead_days differ in config.json — they are the same rule seen from two sides and must stay equal.")
    return cfg


# Terminal protections. "rejected" alone is deliberately NOT terminal: a
# rejection with contact_state still active is a listing level objection and
# must age out like any open row (they were 31% of the roster by 21 Aug 2026
# when treated as immortal). A real found / not interested rejection is
# protected via TERMINAL_CONTACT.
TERMINAL_STATUS = {"tenanted", "found_place", "deposit-pending"}
TERMINAL_CONTACT = {"found_place", "do_not_contact", "not_interested"}

AUTO_CLOSE_PREFIX = "auto closed: no contact since"


def _last_signal(t):
    return str(t.get("last_contact") or t.get("data_collected") or "")[:10]


def is_protected(t):
    st = (t.get("status") or "").strip().lower()
    cs = (t.get("contact_state") or "").strip().lower()
    return bool(t.get("excluded")) or st in TERMINAL_STATUS or cs in TERMINAL_CONTACT


def is_stale(t, cutoff_iso):
    """True when an unprotected, not yet closed row last signalled before cutoff.
    Rows with no date at all are left alone (nothing to age against)."""
    st = (t.get("status") or "").strip().lower()
    if is_protected(t) or st.startswith("closed"):
        return False
    last = _last_signal(t)
    return bool(last) and last < cutoff_iso


def close_stale(t, today_iso, days):
    last = _last_signal(t)
    t["prev_status"] = t.get("status")
    t["status"] = "closed (stale)"
    t["closed_reason"] = f"{AUTO_CLOSE_PREFIX} {last} (>{days} days)"
    t["closed_date"] = today_iso
    t["match_status"] = "stale_closed"


def reopen_if_returned(t):
    """THE transition the old pipeline lacked: a row closed by the silence
    sweep whose contact resumed after closing reopens to its prior status.
    Applies ONLY to sweep closes (closed_reason carries AUTO_CLOSE_PREFIX) —
    found / not interested / hand closed rows never reopen here. Returns True
    if the row was reopened."""
    st = (t.get("status") or "").strip().lower()
    if not st.startswith("closed"):
        return False
    if not str(t.get("closed_reason") or "").startswith(AUTO_CLOSE_PREFIX):
        return False
    if (t.get("contact_state") or "").strip().lower() in TERMINAL_CONTACT:
        return False
    last = str(t.get("last_contact") or "")[:10]
    closed = str(t.get("closed_date") or "")[:10]
    if not (last and closed and last > closed):
        return False
    t["status"] = t.get("prev_status") or "open"
    t["closed_reason"] = ""
    t["closed_date"] = ""
    t["follow_up"] = ((t.get("follow_up") or "") + f" | reopened: contact resumed {last} after stale close").strip(" |")
    return True


def match_status_for(t, kids):
    """Moved verbatim from apply_tenant_lifecycle.py (21 Aug 2026) — the
    nationality test is on NATIONALITY (from India), not ethnicity, so a
    Singaporean or PR of Indian ethnicity is NOT excluded."""
    if t.get("excluded"):
        return "do_not_contact"
    cs = t.get("contact_state")
    st = t.get("status")
    if cs == "found_place" or st == "tenanted":
        return "found"
    if (st or "").startswith("closed"):
        return "stale_closed"
    if cs in ("not_interested", "do_not_contact"):
        return "do_not_contact"
    nat = (t.get("nationality") or "").strip().lower()
    if nat in ("indian", "india", "indian (india)"):
        return "excluded_india"
    if kids:
        return "excluded_family"
    if st == "deposit-pending":
        return "in_deal"
    l = t.get("lease_term_months")
    if isinstance(l, (int, float)):
        if l < 6:
            return "short_lease"
        if l < 12:
            return "below_target"
    return "active"


def serve_priority_for(t, ms):
    """Serve order within the pool: 12 months or more first, then 6 to 11,
    then lease unknown. Distinct from the hand kept PIN list (tenant-priority.json
    -> exported as `pinned`), which is Winfred naming individuals."""
    if ms not in ("active", "below_target", "in_deal"):
        return ""
    l = t.get("lease_term_months")
    if isinstance(l, (int, float)):
        if l >= 12:
            return 1
        if l >= 6:
            return 2
    return 3
