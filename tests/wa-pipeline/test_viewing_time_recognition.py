"""
test_viewing_time_recognition.py -- FIX 2 (9 Sep 2026): _has_viewing_time did not know
"tonight" (and friends), so a tenant asking "can I view it tonight ?" fell through to the
'?' branch as a plain question, then ANSWER_QUESTION with no matching fact -> FLAG_HUMAN
with no text sent (real chat 6580900266, evidence in the task brief). Adds a 20 positive /
20 negative regex table over _has_viewing_time, plus one end to end routing check through
_viewing_reaction proving "tonight" now reaches VIEWING_TIME_PROPOSED, not ANSWER_QUESTION.

Run: /usr/bin/python3 tests/wa-pipeline/test_viewing_time_recognition.py
"""
import sys, os
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
import intake_engine as E

# same fixture-listing override as test_viewing_scheduling.py / test_intake_engine.py --
# sunshine-terrace may have since gone on hold for real; force it open for THIS test
# process only.
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

print("== _has_viewing_time: 20 positive / 20 negative regex table ==")

POSITIVES = [
    "tonight", "this evening", "later today", "now", "right now", "this afternoon",
    "tomorrow night", "tmr night", "this weekend", "sat", "sun", "weekend",
    "今晚", "明天", "周末",
    "sat 3pm works", "tomorrow evening", "can i come 16/6", "how about wednesday",
    "lets do sunday 2pm",
]
assert len(POSITIVES) == 20, len(POSITIVES)
for p in POSITIVES:
    ok("recognises %r as a viewing time proposal" % p, E._has_viewing_time(p))

NEGATIVES = [
    # required near misses from the task brief -- must never fire
    "night shift", "weekend job", "now I am in China", "tonight I fly", "sat exam",
    # plain questions with no day/time content at all
    "is there wifi included?", "how much is the deposit", "any nearby mrt",
    "can I bring a friend", "is the room aircon", "what floor is it on",
    "any cooking allowed", "is it furnished", "how many roommates",
    "what is the deposit amount", "can you send more photos", "is parking available",
    "does it include utilities", "how far from the mrt", "is short term possible",
]
assert len(NEGATIVES) == 20, len(NEGATIVES)
for n in NEGATIVES:
    ok("does NOT treat %r as a viewing time proposal" % n, not E._has_viewing_time(n))

print("== end to end: 'tonight' routes to VIEWING_TIME_PROPOSED, not ANSWER_QUESTION ==")
# mirrors real chat 6580900266: form filled, viewing offered, then "can I view it tonight ?"
_PROFILE = {"gender": "Female", "ethnicity": "Chinese", "nationality": "SG",
            "pass_type": "SC", "no_of_pax": 1, "age": 28,
            "move_in_date": "1 Oct", "lease_term_months": 12, "budget": 1500}
def _offered_rec(pn):
    return {"version": 1, "conversations": {pn: {
        "pn": pn, "listing_key": "sunshine-terrace", "stage": "VIEWING_OFFERED",
        "profile": dict(_PROFILE), "processed_ids": [], "form_sent": True, "asked_fields": [],
        "viewing_asked": True, "viewing_confirmed": False, "asked_tenant_time": False,
        "exact_time_locked": False, "manual_takeover": False, "status": "viewing_offered",
        "offered_slot_id": "sunshine-terrace-test-slot",
        "offered_slot_label": "Sat 12 Sep, after 7.30pm", "last_inbound": None}}}

pn_tonight = "6580900266"
st = _offered_rec(pn_tonight)
a = E.handle_event(st, {"jid": pn_tonight + "@s.whatsapp.net", "msg_id": "t1",
                        "text": "can I view it tonight ?", "is_from_me": 0})
ok("'can I view it tonight ?' -> VIEWING_TIME_PROPOSED", a and a.get("type") == "VIEWING_TIME_PROPOSED")
ok("carries the existing acknowledgement template (unchanged copy)",
   a and a.get("text") == "Got it, let me confirm that slot with the owner and get back to you shortly.")
ok("real question ('is there aircon?') still routes to ANSWER_QUESTION (unbroken)",
   E.handle_event(_offered_rec("6580900267"),
                  {"jid": "6580900267@s.whatsapp.net", "msg_id": "q1",
                   "text": "is there aircon in the room?", "is_from_me": 0}).get("type")
   == "ANSWER_QUESTION")

print(f"\nRESULT: {P} passed, {F} failed")
sys.exit(1 if F else 0)
