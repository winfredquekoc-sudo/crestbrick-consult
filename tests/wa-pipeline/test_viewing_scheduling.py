import sys, os, datetime
# resolve relative to THIS file so the suite tests the checkout/worktree it lives in, not
# whichever copy happens to be at the shared live path (matches test_intake_engine.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
import intake_engine as E
import viewing_slot_filler as VSF

# Same fixture-listing overrides as tests/wa-pipeline/test_intake_engine.py and
# test_rental_policy_locks.py: sunshine-terrace may have since gone on hold / had its
# landlord record close for real. Force it open for THIS test process only so
# _viewing_reaction's own "room closed mid flow" gate never preempts the decline path
# under test.
_FIXTURE_LISTINGS = ("sunshine-terrace",)
_orig_listing_reqs = E.listing_reqs
def _reqs_fixtures_open():
    r = dict(_orig_listing_reqs())
    for k in _FIXTURE_LISTINGS:
        if k in r:
            c = dict(r[k]); c["status"] = "open"; r[k] = c
    return r
E.listing_reqs = _reqs_fixtures_open

_orig_master_status = E._master_status
def _master_status_fixtures_active(lk, reqs=None):
    if lk in _FIXTURE_LISTINGS:
        return "active"
    return _orig_master_status(lk, reqs)
E._master_status = _master_status_fixtures_active

P = 0; F = 0
def ok(name, cond):
    global P, F
    if cond: P += 1; print("  PASS", name)
    else: F += 1; print("  FAIL", name)

# ============================================================
# Component A: viewing_slot_filler.py — calendar aware slot filler
# ============================================================
print("== A. SLOT FILLER: pure planning logic, calendar stubbed ==")

TODAY = datetime.date(2026, 9, 8)   # a Tuesday, matches the scenario in the task brief

_orig_free_evening = VSF.winfred_free_evening
_orig_first_free_start = VSF._first_free_start

def _stub_calendar(fixed_date, fixed_time="19:30"):
    """Deterministic calendar stub: Winfred is always free on `fixed_date` at `fixed_time`,
    regardless of the after_date/date_obj asked for. Mirrors the task's instruction to stub
    winfred_free_evening so the filler's date/stacking logic is tested, not the calendar."""
    VSF.winfred_free_evening = lambda after_date, horizon_days=21: fixed_date
    VSF._first_free_start = lambda date_obj, floor=None, ceiling=None: fixed_time

def _restore_calendar():
    VSF.winfred_free_evening = _orig_free_evening
    VSF._first_free_start = _orig_first_free_start

# --- 1. proposes a slot >= 2 days out, time after 19:30 ---
free_day = TODAY + datetime.timedelta(days=2)
_stub_calendar(free_day, "19:30")
try:
    entry = {"listing_key": "test-listing-a", "slots": []}
    slot = VSF.propose_slot_for_listing("test-listing-a", entry, today=TODAY)
finally:
    _restore_calendar()

ok("a slot was proposed", slot is not None)
ok("proposed date is >= today+2", slot and datetime.date.fromisoformat(slot["date"]) >= TODAY + datetime.timedelta(days=2))
ok("proposed start is at/after 19:30", slot and slot["start"] >= "19:30")
ok("end is start+30min", slot and VSF._hm_add(slot["start"], 30) == slot["end"])
ok("capacity 1, booked 0, status open", slot and slot["capacity"] == 1 and slot["booked"] == 0 and slot["status"] == "open")
ok("carries proposed+source markers", slot and slot.get("proposed") is True and slot.get("source") == "slot-filler")
ok("label mentions the weekday and 'after'", slot and "after" in slot["label"].lower())

# --- 2. stacking: a second slot same day starts 15 min after the last one's start ---
stack_day = TODAY + datetime.timedelta(days=3)
_stub_calendar(stack_day, "19:30")
try:
    entry_with_slot = {"listing_key": "test-listing-b", "slots": [
        {"slot_id": "test-listing-b-existing", "date": stack_day.isoformat(),
         "start": "19:30", "end": "20:00", "label": "existing", "capacity": 1,
         "booked": 1, "status": "full"},
    ]}
    stacked = VSF.propose_slot_for_listing("test-listing-b", entry_with_slot, today=TODAY)
finally:
    _restore_calendar()

ok("stacked slot exists", stacked is not None)
ok("stacked slot is the SAME day as the existing one", stacked and stacked["date"] == stack_day.isoformat())
ok("stacked slot starts 15 min after the existing slot's start (19:45)", stacked and stacked["start"] == "19:45")
ok("stacked slot's own duration is still 30 min", stacked and VSF._hm_add(stacked["start"], 30) == stacked["end"])

# --- 3. idempotent: no second proposal when an open future slot already exists ---
future_date = (TODAY + datetime.timedelta(days=5)).isoformat()
listings_idem = {
    "already-has-slot": {"listing_key": "already-has-slot", "status": "open", "requirements": {}},
}
avail_idem = {
    "already-has-slot": {"listing_key": "already-has-slot", "slots": [
        {"slot_id": "x", "date": future_date, "start": "19:30", "end": "20:00",
         "label": "x", "capacity": 1, "booked": 0, "status": "open"},
    ]},
}
_stub_calendar(TODAY + datetime.timedelta(days=2), "19:30")
try:
    proposals_idem = VSF.build_proposals(today=TODAY, listings=listings_idem, avail_data=avail_idem)
finally:
    _restore_calendar()
ok("idempotent: no proposal when an open future slot already exists", proposals_idem == [])

# re running with a FULL (booked==capacity) slot on that listing SHOULD propose one
avail_full = {
    "already-has-slot": {"listing_key": "already-has-slot", "slots": [
        {"slot_id": "x", "date": future_date, "start": "19:30", "end": "20:00",
         "label": "x", "capacity": 1, "booked": 1, "status": "full"},
    ]},
}
_stub_calendar(TODAY + datetime.timedelta(days=2), "19:30")
try:
    proposals_full = VSF.build_proposals(today=TODAY, listings=listings_idem, avail_data=avail_full)
finally:
    _restore_calendar()
ok("a FULL existing slot does NOT block a new proposal", len(proposals_full) == 1)

# --- 4. skips fixed_viewing and closed/hold listings ---
listings_mixed = {
    "fixed-one": {"listing_key": "fixed-one", "status": "open",
                  "fixed_viewing": {"weekday": "Sat", "start": "19:30", "end": "20:00"},
                  "requirements": {}},
    "closed-one": {"listing_key": "closed-one", "status": "closed (tenanted)", "requirements": {}},
    "hold-one": {"listing_key": "hold-one", "status": "hold", "requirements": {}},
    "pending-one": {"listing_key": "pending-one", "status": "pending_99co", "requirements": {}},
    "truly-open": {"listing_key": "truly-open", "status": "open", "requirements": {}},
    "truly-active": {"listing_key": "truly-active", "status": "active", "requirements": {}},
}
avail_mixed = {}
_stub_calendar(TODAY + datetime.timedelta(days=2), "19:30")
try:
    proposals_mixed = VSF.build_proposals(today=TODAY, listings=listings_mixed, avail_data=avail_mixed)
finally:
    _restore_calendar()
mixed_keys = {lk for lk, _ in proposals_mixed}
ok("fixed_viewing listing never gets a proposed slot", "fixed-one" not in mixed_keys)
ok("closed listing never gets a proposed slot", "closed-one" not in mixed_keys)
ok("on hold listing never gets a proposed slot", "hold-one" not in mixed_keys)
ok("pending_99co (not yet postable) never gets a proposed slot", "pending-one" not in mixed_keys)
ok("truly open listing (no fixed_viewing, no slot) DOES get one", "truly-open" in mixed_keys)
ok("status active (the other real live state) DOES get one too", "truly-active" in mixed_keys)

# --- 5. calendar unreachable (stub returns None) -> filler proposes nothing, never guesses ---
VSF.winfred_free_evening = lambda after_date, horizon_days=21: None
try:
    no_cal = VSF.propose_slot_for_listing("test-listing-c", {"listing_key": "test-listing-c", "slots": []}, today=TODAY)
finally:
    _restore_calendar()
ok("calendar unreachable -> no slot proposed (fails closed, never fabricates a date)", no_cal is None)


# ============================================================
# Component B: intake_engine._viewing_reaction — tenant decline path
# ============================================================
print("== B. ENGINE: decline path after a viewing is offered ==")

# sunshine-terrace: no hard exclusion gates for this profile (female, Chinese, 1 pax),
# so the re-screen inside _viewing_reaction never DISQUALIFIES it and we can test the
# decline/propose/confirm branches in isolation.
_LISTING = "sunshine-terrace"
_PROFILE = {"gender": "Female", "ethnicity": "Chinese", "nationality": "SG",
            "pass_type": "SC", "no_of_pax": 1, "age": 28,
            "move_in_date": "1 Oct", "lease_term_months": 12, "budget": 1500}

def _offered_rec(pn):
    return {"version": 1, "conversations": {pn: {
        "pn": pn, "listing_key": _LISTING, "stage": "VIEWING_OFFERED", "profile": dict(_PROFILE),
        "processed_ids": [], "form_sent": True, "asked_fields": [], "viewing_asked": True,
        "viewing_confirmed": False, "asked_tenant_time": False, "exact_time_locked": False,
        "manual_takeover": False, "status": "viewing_offered",
        "offered_slot_id": "sunshine-terrace-test-slot", "offered_slot_label": "Sat 12 Sep, after 7.30pm",
        "last_inbound": None}}}

pn1 = "6590200001"
st1 = _offered_rec(pn1)
a1 = E.handle_event(st1, {"jid": pn1 + "@s.whatsapp.net", "msg_id": "d1",
                          "text": "sorry I can't make it that day", "is_from_me": 0})
ok("decline -> an action asking when they are free", a1 and "when are you free" in (a1.get("text") or "").lower())
ok("decline sets asked_tenant_time flag", st1["conversations"][pn1].get("asked_tenant_time") is True)
ok("decline does NOT confirm the viewing", st1["conversations"][pn1].get("viewing_confirmed") is False)

# their NEXT message, now carrying a day+time, routes through the existing
# VIEWING_TIME_PROPOSED path (to Winfred), same as any other counter proposal.
a2 = E.handle_event(st1, {"jid": pn1 + "@s.whatsapp.net", "msg_id": "d2",
                          "text": "how about Friday 8pm", "is_from_me": 0})
ok("follow up day+time -> VIEWING_TIME_PROPOSED", a2 and a2.get("type") == "VIEWING_TIME_PROPOSED")

# fires at most once: a THIRD decline style message must not re trigger the ask (flag latched)
pn1b = "6590200002"
st1b = _offered_rec(pn1b)
E.handle_event(st1b, {"jid": pn1b + "@s.whatsapp.net", "msg_id": "e1",
                      "text": "not available then", "is_from_me": 0})
ok("first decline latches the flag", st1b["conversations"][pn1b].get("asked_tenant_time") is True)
a3 = E.handle_event(st1b, {"jid": pn1b + "@s.whatsapp.net", "msg_id": "e2",
                           "text": "still cannot make it, busy then", "is_from_me": 0})
ok("a SECOND decline style message does not re ask (fires at most once)",
   not (a3 and "when are you free" in (a3.get("text") or "").lower()))

# a plain confirm on a fresh rec still confirms as before (unbroken regression check)
pn2 = "6590200003"
st2 = _offered_rec(pn2)
a4 = E.handle_event(st2, {"jid": pn2 + "@s.whatsapp.net", "msg_id": "c1",
                          "text": "yes ok", "is_from_me": 0})
ok("plain confirm ('yes ok') still confirms (unbroken)", a4 and a4.get("type") == "CONFIRM_VIEWING")
ok("confirm does not touch asked_tenant_time", st2["conversations"][pn2].get("asked_tenant_time") is False)

# a genuine question ('?') still routes to ANSWER_QUESTION, unaffected by the new branch
pn3 = "6590200004"
st3 = _offered_rec(pn3)
a5 = E.handle_event(st3, {"jid": pn3 + "@s.whatsapp.net", "msg_id": "q1",
                          "text": "is there aircon in the room?", "is_from_me": 0})
ok("plain question still routes to ANSWER_QUESTION (unbroken)", a5 and a5.get("type") == "ANSWER_QUESTION")

print(f"\nRESULT: {P} passed, {F} failed")
sys.exit(1 if F else 0)
