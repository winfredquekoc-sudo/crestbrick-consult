import sys, os
# resolve relative to THIS file so the suite tests the checkout/worktree it lives in, not
# whichever copy happens to be at the shared live path (matches test_intake_engine.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E

# Same fixture-listing overrides as tests/wa-pipeline/test_rental_policy_locks.py: several
# fixture listings have since closed/gone on hold for real. Force them open for this test
# process only so the flow reaches VIEWING_OFFERED instead of dead-ending on "room no longer
# available".
_FIXTURE_LISTINGS = ("caspian", "hougang-703", "bedok-north-522", "tampines-855",
                     "sunshine-terrace", "rivervale-185c", "bayshore", "eastpoint-green")
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

# caspian's real requirements already carry cooking:"all" and lease_min_months:6 (real
# fixture, no monkeypatch needed for those two facts). It carries NO "facts" sheet, which is
# exactly what we want for the "no fabrication" wifi test below.
def _viewing_offered_state(pn, listing="caspian"):
    return {"version": 1, "conversations": {pn: {
        "pn": pn, "listing_key": listing, "stage": "VIEWING_OFFERED",
        "profile": {"name": "Test"}, "processed_ids": [], "form_sent": True,
        "asked_fields": [], "viewing_asked": True, "viewing_confirmed": False,
        "manual_takeover": False, "status": "viewing_offered", "offered_slot_id": "s1"}}}

print("== 1. FACTUAL AUTO-ANSWER: cooking question on a listing with cooking on file ==")
st_cook = _viewing_offered_state("6590200001")
a_cook = E.handle_event(st_cook, {"jid": "6590200001@s.whatsapp.net", "msg_id": "c1",
                                   "text": "is cooking allowed in the unit?", "is_from_me": 0})
ok("cooking question -> ANSWER_QUESTION", a_cook and a_cook.get("type") == "ANSWER_QUESTION")
ok("cooking question -> non-empty text (auto-answered, not flagged blind)",
   bool((a_cook or {}).get("text")))
ok("fact_answered latches after the auto-answer",
   st_cook["conversations"]["6590200001"]["fact_answered"] is True)

print("== 2. FACTUAL AUTO-ANSWER: lease question always answerable (floor 12 months) ==")
st_lease = _viewing_offered_state("6590200002")
a_lease = E.handle_event(st_lease, {"jid": "6590200002@s.whatsapp.net", "msg_id": "l1",
                                     "text": "how long is the minimum lease?", "is_from_me": 0})
ok("lease question -> ANSWER_QUESTION", a_lease and a_lease.get("type") == "ANSWER_QUESTION")
ok("lease answer says '1 year' (caspian lease_min_months=6, floored to 12)",
   "1 year" in ((a_lease or {}).get("text") or ""))

print("== 3. SAFETY VETO: opinion / negotiation / legal questions always stay flagged ==")
st_deal = _viewing_offered_state("6590200003")
a_deal = E.handle_event(st_deal, {"jid": "6590200003@s.whatsapp.net", "msg_id": "d1",
                                   "text": "is this a good deal?", "is_from_me": 0})
ok("'is this a good deal?' -> ANSWER_QUESTION with text None (flagged)",
   a_deal and a_deal.get("type") == "ANSWER_QUESTION" and a_deal.get("text") is None)

st_nego = _viewing_offered_state("6590200004")
a_nego = E.handle_event(st_nego, {"jid": "6590200004@s.whatsapp.net", "msg_id": "n1",
                                   "text": "can you do 200 less?", "is_from_me": 0})
ok("'can you do 200 less?' -> text None (negotiation veto)",
   a_nego and a_nego.get("type") == "ANSWER_QUESTION" and a_nego.get("text") is None)

st_legal = _viewing_offered_state("6590200005")
a_legal = E.handle_event(st_legal, {"jid": "6590200005@s.whatsapp.net", "msg_id": "e1",
                                     "text": "what if I end the lease early?", "is_from_me": 0})
ok("'what if I end the lease early?' -> text None (legal veto, even though 'lease' also matches)",
   a_legal and a_legal.get("type") == "ANSWER_QUESTION" and a_legal.get("text") is None)

print("== 4. NO FABRICATION: fact question with nothing on file never invents an answer ==")
st_wifi = _viewing_offered_state("6590200006")
a_wifi = E.handle_event(st_wifi, {"jid": "6590200006@s.whatsapp.net", "msg_id": "w1",
                                   "text": "is there wifi in the unit?", "is_from_me": 0})
ok("wifi question, no facts sheet on caspian -> text None (no fabrication)",
   a_wifi and a_wifi.get("type") == "ANSWER_QUESTION" and a_wifi.get("text") is None)
ok("fact_answered stays False (no fact was actually answered)",
   st_wifi["conversations"]["6590200006"]["fact_answered"] is False)

print("== 5. ONE-ANSWER CAP: a second factual question in the same chat is flagged, not answered ==")
st_cap = _viewing_offered_state("6590200007")
a_cap1 = E.handle_event(st_cap, {"jid": "6590200007@s.whatsapp.net", "msg_id": "cap1",
                                  "text": "is cooking allowed?", "is_from_me": 0})
ok("first factual question -> answered", a_cap1 and bool(a_cap1.get("text")))
ok("fact_answered latched True after first answer",
   st_cap["conversations"]["6590200007"]["fact_answered"] is True)
a_cap2 = E.handle_event(st_cap, {"jid": "6590200007@s.whatsapp.net", "msg_id": "cap2",
                                  "text": "how long is the lease?", "is_from_me": 0})
ok("second factual question in the same chat -> text None (one-answer cap, flagged instead)",
   a_cap2 and a_cap2.get("type") == "ANSWER_QUESTION" and a_cap2.get("text") is None)

print("== 6. MANUAL TAKEOVER: engine stays silent, never auto-answers ==")
st_mt = _viewing_offered_state("6590200008")
st_mt["conversations"]["6590200008"]["manual_takeover"] = True
st_mt["conversations"]["6590200008"]["human_takeover"] = True
st_mt["conversations"]["6590200008"]["copilot_muted"] = True
a_mt = E.handle_event(st_mt, {"jid": "6590200008@s.whatsapp.net", "msg_id": "mt1",
                               "text": "is cooking allowed?", "is_from_me": 0})
ok("manual takeover -> engine silent (no auto-answer, no action at all)", a_mt is None)

print("== 6. RENT: general price/negotiability gets the vague viewing-pivot reply; a figure flags ==")
st_rent = _viewing_offered_state("6590200010")
a_rent = E.handle_event(st_rent, {"jid": "6590200010@s.whatsapp.net", "msg_id": "r1",
                                   "text": "how much is the rent?", "is_from_me": 0})
ok("'how much is the rent?' -> vague reply, no figure quoted",
   a_rent and a_rent.get("type") == "ANSWER_QUESTION" and a_rent.get("text")
   and "usually fixed" in a_rent["text"].lower() and "viewing" in a_rent["text"].lower()
   and "$" not in a_rent["text"])

st_neg = _viewing_offered_state("6590200011")
a_neg = E.handle_event(st_neg, {"jid": "6590200011@s.whatsapp.net", "msg_id": "r2",
                                 "text": "is the rent negotiable?", "is_from_me": 0})
ok("'is the rent negotiable?' -> same vague reply, not flagged",
   a_neg and a_neg.get("type") == "ANSWER_QUESTION" and a_neg.get("text")
   and "usually fixed" in a_neg["text"].lower())

st_haggle = _viewing_offered_state("6590200012")
a_haggle = E.handle_event(st_haggle, {"jid": "6590200012@s.whatsapp.net", "msg_id": "r3",
                                       "text": "can you lower the rent to 1400?", "is_from_me": 0})
ok("'lower the rent to 1400' -> text None (specific figure flags to Winfred)",
   a_haggle and a_haggle.get("type") == "ANSWER_QUESTION" and a_haggle.get("text") is None)

print(f"\nRESULT: {P} passed, {F} failed")
sys.exit(1 if F else 0)
