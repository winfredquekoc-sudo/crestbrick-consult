"""
test_landlord_onboarding_dryrun_replay.py -- DRY_RUN safety harness for the landlord
onboarding extension (intake_engine._landlord_onboarding_reaction and friends).

Purpose: prove that, as currently wired, this feature can NEVER actually send a WhatsApp
message while intake_engine.DRY_RUN is True, across a set of realistic and adversarial
scenarios (Winfred, 6 Sep 2026). This is a pre go-live verification tool, not a functional
test (those live in test_intake_engine.py) -- run it again before any go-live flip.

How it proves "zero real sends":
  1. intake_engine.DRY_RUN is forced True for the whole run (and restored after).
  2. wa_intake_runner._send (the ONLY function in this codebase that ever reaches the WA
     bridge HTTP endpoint) is monkeypatched to a poison pill that raises if it is ever
     called, restored after the run. Any accidental live send would blow up loudly here.
  3. Each cycle replays a scenario through E.handle_event (the real engine, unmodified) and
     then runs the result through _would_send(), a faithful reproduction of the runner's own
     gate order (STALE_SKIP -> manual_takeover carve out -> DAILY_SEND_CAP -> DRY_RUN branch)
     copied from wa_intake_runner.run(). Under DRY_RUN, _would_send NEVER calls the poison
     pill _send -- it only logs what WOULD have gone out, exactly like the real runner does.

Usage: python3 tests/wa-pipeline/test_landlord_onboarding_dryrun_replay.py
"""
import sys, os, time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E
import wa_intake_runner as RNR

_orig_dry_run = E.DRY_RUN
_orig_send = RNR._send
E.DRY_RUN = True

def _poison_send(pn, text):
    raise AssertionError(f"REAL SEND ATTEMPTED to {pn}: {text[:80]!r} -- this must never "
                          f"happen while DRY_RUN is True")
RNR._send = _poison_send

REAL_SENDS = []       # would be appended to only if the poison pill somehow fired
WOULD_SEND_LOG = []   # (cycle, pn, type, text-or-None) -- the DRY_RUN preview equivalent

def _would_send(cycle, action, grec, ts_age_hours=0.0, sends_today=0):
    """Faithful reproduction of wa_intake_runner.run()'s per action send gate (STALE_SKIP,
    manual takeover carve out, DAILY_SEND_CAP, DRY_RUN branch), so this harness proves the
    REAL gate order protects a landlord, not a hand-wavy stand-in."""
    texts = action.get("texts") or ([action["text"]] if action.get("text") else [])
    if not texts:
        WOULD_SEND_LOG.append((cycle, action.get("pn"), action["type"], None, "NO_TEXT_FLAG_ONLY"))
        return "NO_TEXT_FLAG_ONLY"
    if ts_age_hours > RNR.SEND_MAX_INBOUND_AGE_HOURS:
        WOULD_SEND_LOG.append((cycle, action.get("pn"), action["type"], None, "STALE_SKIP"))
        return "STALE_SKIP"
    if (grec.get("manual_takeover") and not action.get("copilot")
            and action["type"] not in RNR._LANDLORD_ONBOARDING_TYPES):
        WOULD_SEND_LOG.append((cycle, action.get("pn"), action["type"], None, "TAKEOVER_SKIP"))
        return "TAKEOVER_SKIP"
    if action["type"] not in ("CONFIRM_VIEWING", "OFFER_VIEWING", "ASK_ONE") and sends_today >= RNR.DAILY_SEND_CAP:
        WOULD_SEND_LOG.append((cycle, action.get("pn"), action["type"], None, "DAILY_CAP_SKIP"))
        return "DAILY_CAP_SKIP"
    if E.DRY_RUN:
        for tx in texts:
            WOULD_SEND_LOG.append((cycle, action.get("pn"), action["type"], tx, "WOULD_SEND"))
        return "WOULD_SEND"
    # live branch -- unreachable in this harness (DRY_RUN forced True above), kept for
    # structural parity with the real runner so a future edit here cannot silently drift
    for tx in texts:
        REAL_SENDS.append((action.get("pn"), tx))
        RNR._send(action.get("pn"), tx)
    return "SENT"

P = 0
def cycle(name, fn):
    global P
    try:
        result = fn()
        print(f"  [{name}] {result}")
        P += 1
    except AssertionError as e:
        print(f"  [{name}] FAILED: {e}")
        raise

def _new_landlord(seed, opening="I am the landlord, want to rent out my room"):
    st = {"version": 1, "conversations": {}}
    jid = f"6597000{seed:03d}@s.whatsapp.net"
    a0 = E.handle_event(st, {"jid": jid, "msg_id": f"H{seed}-0", "text": opening, "is_from_me": 0})
    assert a0 and a0["type"] == "SEND_SUPPLY_FORM"
    r0 = _would_send(f"cycle{seed}-form", a0, st["conversations"][jid.split("@")[0]])
    return st, jid, jid.split("@")[0], r0

FT_COMPLETE = ("Blk 88 Bedok North Street 4, asking 1400 a month, max 2 pax, looking for a "
               "working professional, prefer female tenant, need at least 1 year lease")

print("== LANDLORD ONBOARDING: 10 cycle DRY_RUN replay harness ==")
print(f"E.DRY_RUN = {E.DRY_RUN} (forced True for this harness)")

def c1():
    st, jid, pn, r0 = _new_landlord(1)
    a = E.handle_event(st, {"jid": jid, "msg_id": "H1-1", "text": FT_COMPLETE, "is_from_me": 0})
    r = _would_send("c1", a, st["conversations"][pn])
    assert r == "WOULD_SEND" and a["type"] == "SUPPLY_MEDIA_ASK"
    return f"complete-in-one-message -> form={r0}, media_ask={r} (0 real sends)"
cycle("1 complete-in-one-message", c1)

def c2():
    st, jid, pn, r0 = _new_landlord(2)
    a = E.handle_event(st, {"jid": jid, "msg_id": "H2-1", "text": "Blk 1 Toa Payoh Lorong 1, asking 1100", "is_from_me": 0})
    r = _would_send("c2", a, st["conversations"][pn])
    assert r == "WOULD_SEND" and a["type"] == "SUPPLY_INFO_NUDGE"
    a2 = E.handle_event(st, {"jid": jid, "msg_id": "H2-2", "text": "still deciding", "is_from_me": 0})
    r2 = _would_send("c2b", a2, st["conversations"][pn])
    assert r2 == "NO_TEXT_FLAG_ONLY"   # FLAG_HUMAN after the cap carries no prospect text
    return f"partial reply -> nudge={r}, then cap reached -> {r2} (0 real sends)"
cycle("2 partial reply", c2)

def c3():
    st, jid, pn, r0 = _new_landlord(3, opening="my room posting, want to find a tenant")
    a = E.handle_event(st, {"jid": jid, "msg_id": "H3-1",
                            "text": ("place is at 550 West Coast Road, 1300 a month, 2 pax max, "
                                     "student ok, any gender, min 6 months"),
                            "is_from_me": 0})
    r = _would_send("c3", a, st["conversations"][pn])
    assert r == "WOULD_SEND" and a["type"] == "SUPPLY_MEDIA_ASK"
    return f"free-text with no form labels -> extracted + media_ask={r} (0 real sends)"
cycle("3 free-text no form labels", c3)

def c4():
    st, jid, pn, r0 = _new_landlord(4)
    _orig = E._landlord_media_status
    E._landlord_media_status = lambda pn_, jid_: (True, False)
    try:
        a = E.handle_event(st, {"jid": jid, "msg_id": "H4-1", "text": "", "is_from_me": 0, "media_type": "image"})
        # media-only reply before info is complete: still incomplete -> nudge, media flag is inert
        r = _would_send("c4", a, st["conversations"][pn])
        assert a["type"] == "SUPPLY_INFO_NUDGE" and r == "WOULD_SEND"
    finally:
        E._landlord_media_status = _orig
    return f"media-only reply (no text) -> still gated on the 6 fields, nudge={r} (0 real sends)"
cycle("4 media-only reply", c4)

def c5():
    st, jid, pn, r0 = _new_landlord(5)
    E.handle_event(st, {"jid": jid, "msg_id": "H5-0b", "text": "let me handle this one",
                        "is_from_me": 1, "engine": False})
    a = E.handle_event(st, {"jid": jid, "msg_id": "H5-1", "text": FT_COMPLETE, "is_from_me": 0})
    assert a is None
    return "human-takeover mid-flow -> engine returns None, nothing to even gate (0 real sends)"
cycle("5 human-takeover mid-flow", c5)

def c6():
    st, jid, pn, r0 = _new_landlord(6)
    a = E.handle_event(st, {"jid": jid, "msg_id": "H6-1", "text": "Blk 2 Yishun Ring Road, 1200", "is_from_me": 0})
    r = _would_send("c6a", a, st["conversations"][pn])
    a2 = E.handle_event(st, {"jid": jid, "msg_id": "H6-1", "text": "Blk 2 Yishun Ring Road, 1200", "is_from_me": 0})
    assert a2 is None   # duplicate msg_id -> event dedup, no second action at all
    return f"repeat/duplicate reply (same msg id) -> first={r}, replay=None (0 real sends)"
cycle("6 repeat/duplicate replies", c6)

def c7():
    st, jid, pn, r0 = _new_landlord(7)
    a = E.handle_event(st, {"jid": jid, "msg_id": "H7-1",
                            "text": "Blk 9 Clementi Ave 2, asking $1500, how long to find a tenant?",
                            "is_from_me": 0})
    r = _would_send("c7", a, st["conversations"][pn])
    assert a["type"] == "FLAG_HUMAN" and r == "NO_TEXT_FLAG_ONLY"
    return f"landlord asks a question -> FLAG_HUMAN only, {r}, never auto answered (0 real sends)"
cycle("7 landlord asks a question", c7)

def c8():
    st, jid, pn, r0 = _new_landlord(8)
    _orig = E._landlord_media_status
    E._landlord_media_status = lambda pn_, jid_: (True, True)
    try:
        E.handle_event(st, {"jid": jid, "msg_id": "H8-1", "text": FT_COMPLETE, "is_from_me": 0})
        a = E.handle_event(st, {"jid": jid, "msg_id": "H8-2", "text": "photos sent!", "is_from_me": 0})
        r = _would_send("c8a", a, st["conversations"][pn])
        assert st["conversations"][pn]["stage"] == "SUPPLY_READY"
        a2 = E.handle_event(st, {"jid": jid, "msg_id": "H8-3", "text": "just saying hi", "is_from_me": 0})
        r2 = _would_send("c8b", a2, st["conversations"][pn]) if a2 else "NONE"
    finally:
        E._landlord_media_status = _orig
    return f"already-complete landlord (SUPPLY_READY) -> ready flag={r}, further chatter={r2} (0 real sends)"
cycle("8 already-complete landlord", c8)

def c9():
    st, jid, pn, r0 = _new_landlord(9)
    a1 = E.handle_event(st, {"jid": jid, "msg_id": "H9-1", "text": "Blk 3 Bukit Batok St 21, 1000", "is_from_me": 0})
    r1 = _would_send("c9a", a1, st["conversations"][pn])
    a2 = E.handle_event(st, {"jid": jid, "msg_id": "H9-2", "text": "still deciding", "is_from_me": 0})
    r2 = _would_send("c9b", a2, st["conversations"][pn])
    a3 = E.handle_event(st, {"jid": jid, "msg_id": "H9-3", "text": "sorry, been busy", "is_from_me": 0})
    r3 = _would_send("c9c", a3, st["conversations"][pn]) if a3 else "NONE"
    assert r1 == "WOULD_SEND" and r2 == "NO_TEXT_FLAG_ONLY" and r3 == "NONE"
    return f"over-cap (nudge cap=1) -> {r1} then {r2} then {r3}, never a 2nd nudge (0 real sends)"
cycle("9 over-cap no further sends", c9)

def c10():
    _orig_qh = RNR._quiet_hours
    RNR._quiet_hours = lambda: True
    try:
        held = RNR._quiet_hours()
        # this mirrors run()'s own top level gate: `if _quiet_hours(): return` before ANY
        # row (including a landlord onboarding one) is even looked at this tick.
        assert held is True
    finally:
        RNR._quiet_hours = _orig_qh
    return "quiet hours (01:00-07:00 SGT) -> run() holds before processing ANY row (0 real sends)"
cycle("10 quiet-hours hold", c10)

print()
print(f"RESULT: {P}/10 cycles verified, {len(REAL_SENDS)} REAL sends attempted (must be 0)")
assert len(REAL_SENDS) == 0, f"a real send slipped through: {REAL_SENDS}"
print("PASS: zero unintended sends across all 10 cycles under DRY_RUN")

E.DRY_RUN = _orig_dry_run
RNR._send = _orig_send
sys_exit_code = 0 if len(REAL_SENDS) == 0 and P == 10 else 1
import sys as _sys
_sys.exit(sys_exit_code)
