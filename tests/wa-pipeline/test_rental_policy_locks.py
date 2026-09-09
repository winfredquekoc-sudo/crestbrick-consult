import sys, os
# resolve relative to THIS file so the suite tests the checkout/worktree it lives in, not
# whichever copy happens to be at the shared live path (matches test_intake_engine.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
import intake_engine as E

# Same fixture-listing overrides as tests/wa-pipeline/test_intake_engine.py: several fixture
# listings have since closed/gone on hold for real (caspian tenanted 9 Jul 2026, etc). Force
# them open for this test process only so the full enquiry -> form -> qualify flow runs end
# to end instead of dead-ending on "room no longer available".
_FIXTURE_LISTINGS = ("caspian", "hougang-703", "bedok-north-522", "tampines-855",
                     "sunshine-terrace", "rivervale-185c", "bayshore", "eastpoint-green")
# Point E.IDX at a STATIC local fixture, not the live index (Winfred's landlord database
# and listing sync run nightly and can change any morning -- a suite that reads the live
# file breaks with no code change, exactly what happened 9 Sep 2026 when a routine sync
# flipped caspian's ethnicity gate and bedok-north-522 / tampines-855's gender gate to
# gate_unverified and silently broke this suite on every branch). tests/wa-pipeline/
# fixtures/listing-index.json is a one time snapshot (PII stripped) frozen for this suite.
# Reassigning the PATH constant (not listing_reqs itself) keeps this test process safe for
# _isolated_runner style tests elsewhere in the suite that scope their OWN mock.patch.object
# of E.IDX to a tmp file per test -- those still take priority and are correctly restored
# back to this fixture path afterward (discover-mode cross file regression, 9 Sep 2026 merge
# review: an earlier cut replaced E.listing_reqs itself wholesale and broke that nesting).
_FIXTURE_IDX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", "listing-index.json")
E.IDX = _FIXTURE_IDX_PATH
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

# Words that must NEVER leak to a rejected tenant: they would either reveal the protected
# attribute that caused the rejection, or make the discriminatory nature of the gate legible.
_FORBIDDEN = ("indian", "malay", "chinese", "nationality", "ethnicity", "mismatch",
              "female only", "male only", "race", "quota", "disqualif")

def _disqualifying_flow(jid_suffix, listing_key, enquiry_text, form_text):
    """Drive a full inbound flow (enquiry -> completed form) and return the engine's
    outbound REDIRECT dict for the completed-form step."""
    jid = f"659010{jid_suffix}@s.whatsapp.net"
    st = {"version": 1, "conversations": {}}
    E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": enquiry_text,
                         "is_from_me": 0, "listing_key": listing_key})
    pn = jid.split("@")[0]
    # skip the 3 minute anti-spam grace period between SEND_FORM and the form reply,
    # same convention as test_intake_engine.py section 2.
    st["conversations"][pn]["form_sent_ts"] -= 300
    action = E.handle_event(st, {"jid": jid, "msg_id": "m2", "text": form_text, "is_from_me": 0})
    return action, st, pn

print("== 1. POLICY LOCK: disqualifying scenarios never leak the reason to the tenant ==")

_SCENARIOS = [
    ("01", "caspian",
     "Hi is Caspian still available?",
     "Name: Raj\nNationality: Indian\nEthnicity: Indian\nGender: Male\nAge: 30\n"
     "Type of Pass: EP\nNo. of Pax: 1\nIntended Move in Date: 1 Aug\n"
     "Preferred Lease Term: 12\nBudget: 1200",
     "Indian tenant on caspian (nationality exclude)"),
    ("02", "bedok-north-522",
     "Hi is the Bedok North room still available?",
     "Name: Tom\nNationality: Singaporean\nEthnicity: Chinese\nGender: Male\nAge: 30\n"
     "Type of Pass: SC\nNo. of Pax: 1\nIntended Move in Date: 1 Aug\n"
     "Preferred Lease Term: 12\nBudget: 1300",
     "single male on bedok-north-522 (female_only, no couple)"),
    ("03", "tampines-855",
     "Hi is Tampines 855 still available?",
     "Name: Sam\nNationality: Singaporean\nEthnicity: Chinese\nGender: Male\nAge: 35\n"
     "Type of Pass: SC\nNo. of Pax: 1\nIntended Move in Date: 1 Aug\n"
     "Preferred Lease Term: 12\nBudget: 1000",
     "male on tampines-855 (female_only)"),
]

_redirect_actions = []
for suffix, listing_key, enquiry, form, label in _SCENARIOS:
    action, st, pn = _disqualifying_flow(suffix, listing_key, enquiry, form)
    _redirect_actions.append((label, action))
    text = (action or {}).get("text") or ""
    ok(f"{label} -> REDIRECT", action and action.get("type") == "REDIRECT")
    ok(f"{label} -> reply is the generic not-a-fit text", "not a fit" in text.lower())
    leaked = [w for w in _FORBIDDEN if w in text.lower()]
    ok(f"{label} -> no protected attribute or reason leaked to tenant (found: {leaked})",
       len(leaked) == 0)

print("== 2. CROSS-SELL SHAPE: rejection always hands the tenant somewhere to go ==")
for label, action in _redirect_actions:
    text = (action or {}).get("text") or ""
    ok(f"{label} -> reply includes the CHANNEL referral", E.CHANNEL in text)

print("== 3. MANUAL-TAKEOVER LATCH: hand reply silences the engine on that chat ==")
st_mt = {"version": 1, "conversations": {}}
jid_mt = "6590100010@s.whatsapp.net"
a_enq = E.handle_event(st_mt, {"jid": jid_mt, "msg_id": "mt1",
                                "text": "Hi is Caspian still available?",
                                "is_from_me": 0, "listing_key": "caspian"})
ok("enquiry -> SEND_FORM before any takeover", a_enq and a_enq["type"] == "SEND_FORM")
a_hand = E.handle_event(st_mt, {"jid": jid_mt, "msg_id": "mt2",
                                 "text": "let me check with the landlord ah",
                                 "is_from_me": 1, "engine": False})
pn_mt = jid_mt.split("@")[0]
ok("Winfred's hand reply latches manual_takeover",
   st_mt["conversations"][pn_mt]["manual_takeover"] is True)
a_after = E.handle_event(st_mt, {"jid": jid_mt, "msg_id": "mt3",
                                  "text": "Name: Bob\nNationality: Singaporean\nBudget: 1200",
                                  "is_from_me": 0})
ok("subsequent inbound after hand reply -> no auto-send (silent)", a_after is None)
a_after2 = E.handle_event(st_mt, {"jid": jid_mt, "msg_id": "mt4",
                                   "text": "hello? any update?", "is_from_me": 0})
ok("engine stays silent on further inbound too", a_after2 is None)

print("== 4. NO DOUBLE-SEND on a qualified match (OFFER_VIEWING fires at most once) ==")
# There is no direct tenant-side "notify landlord" call in intake_engine.handle_event --
# forwarding a qualified tenant's profile is represented by the OFFER_VIEWING action (its
# text literally says "I will send your profile over now"), gated by the one-shot
# rec["viewing_asked"] flag. A landlord-form-side match/notify pipeline exists separately in
# landlord_tenant_matcher.py (track_match/mark_match_notified), but that fires when a
# LANDLORD's form completes, not from this tenant flow -- so the analogous, testable-in-here
# guarantee is that a qualified tenant's "send profile to landlord" action never double-fires,
# even under a rapid duplicate resend of the exact same completed form.
st_ds = {"version": 1, "conversations": {
    "6590999020": {"pn": "6590999020", "listing_key": "bayshore", "stage": "NEW",
                    "profile": {}, "processed_ids": [], "form_sent": False,
                    "asked_fields": [], "viewing_asked": False, "viewing_confirmed": False,
                    "manual_takeover": False, "status": "new", "last_inbound": None}}}
jid_ds = "6590999020@s.whatsapp.net"
pn_ds = "6590999020"
a_ds1 = E.handle_event(st_ds, {"jid": jid_ds, "msg_id": "ds1",
                                "text": "hi is the bayshore room available to rent?",
                                "is_from_me": 0})
ok("first enquiry -> SEND_FORM", a_ds1 and a_ds1["type"] == "SEND_FORM")
st_ds["conversations"][pn_ds]["form_sent_ts"] -= 300
_form_text = ("Name: John\nNationality: Malaysian\nNo. of Pax: 1\nBudget: 2500\n"
              "Lease: 12 months")
a_ds2 = E.handle_event(st_ds, {"jid": jid_ds, "msg_id": "ds2", "text": _form_text,
                                "is_from_me": 0})
ok("first completed form (qualified) -> OFFER_VIEWING fires (the one match/forward action)",
   a_ds2 and a_ds2["type"] == "OFFER_VIEWING")
ok("viewing_asked latches after the first offer",
   st_ds["conversations"][pn_ds]["viewing_asked"] is True)
# rapid duplicate: same tenant, identical completed form resent (e.g. WhatsApp retry / a
# second near-identical enquiry+form), different msg_id so the event-id dedup doesn't hide it.
a_ds3 = E.handle_event(st_ds, {"jid": jid_ds, "msg_id": "ds3", "text": _form_text,
                                "is_from_me": 0})
ok("duplicate resend of the completed form -> NOT a second OFFER_VIEWING",
   not (a_ds3 and a_ds3.get("type") == "OFFER_VIEWING"))
ok("state still shows exactly one offer (viewing_asked stays a single latch, stage unchanged)",
   st_ds["conversations"][pn_ds]["viewing_asked"] is True
   and st_ds["conversations"][pn_ds]["stage"] == "VIEWING_OFFERED")

print("== 5. AMBIGUOUS TIME: free-form reply never fabricates a confirmed viewing ==")
def _viewing_offered_state(pn):
    return {"version": 1, "conversations": {pn: {
        "pn": pn, "listing_key": "caspian", "stage": "VIEWING_OFFERED",
        "profile": {"name": "Test"}, "processed_ids": [], "form_sent": True,
        "asked_fields": [], "viewing_asked": True, "viewing_confirmed": False,
        "manual_takeover": False, "status": "viewing_offered", "offered_slot_id": "s1"}}}
st_amb = _viewing_offered_state("6590100099")
a_amb = E.handle_event(st_amb, {"jid": "6590100099@s.whatsapp.net", "msg_id": "amb1",
                                 "text": "I can do Saturday 3pm", "is_from_me": 0})
ok("free-form time proposal -> VIEWING_TIME_PROPOSED, not an auto-confirm",
   a_amb and a_amb["type"] == "VIEWING_TIME_PROPOSED")
_amb_text = (a_amb or {}).get("text") or ""
ok("reply text does not fabricate a confirmed viewing (no 'confirmed' claim)",
   "confirmed" not in _amb_text.lower() and "your viewing is" not in _amb_text.lower())
ok("engine still pings Winfred to confirm the slot with the owner",
   a_amb and a_amb.get("notify") is True)
# contrast: an explicit "yes" against a slot that WAS actually offered is allowed to confirm.
st_yes = _viewing_offered_state("6590100098")
a_yes = E.handle_event(st_yes, {"jid": "6590100098@s.whatsapp.net", "msg_id": "y1",
                                 "text": "yes please", "is_from_me": 0})
ok("explicit yes against an ACTUALLY offered slot -> CONFIRM_VIEWING (contrast case)",
   a_yes and a_yes["type"] == "CONFIRM_VIEWING")

print("== 6. LANDLORD REDACTION: tenant flow never emits a landlord-bound send with name/email/budget ==")
# Winfred's rule (2 Sep 2026): any landlord-facing tenant summary must omit the tenant's
# name, email and budget. Today the engine has NO auto-send-to-landlord path at all -- a
# qualified tenant yields OFFER_VIEWING (tenant-facing slot offer + a Winfred ping), and the
# actual forward to the owner is Winfred's manual step. This guard LOCKS that: it trips the
# day any change adds a landlord-directed send carrying those fields.
_LANDLORD_SEND_TYPES = ("SEND_TO_LANDLORD", "NOTIFY_LANDLORD", "LANDLORD_PROFILE",
                        "FORWARD_TO_LANDLORD", "SEND_PROFILE", "LANDLORD_NOTIFY")
st_rd = {"version": 1, "conversations": {
    "6590999030": {"pn": "6590999030", "listing_key": "bayshore", "stage": "NEW",
                    "profile": {}, "processed_ids": [], "form_sent": False,
                    "asked_fields": [], "viewing_asked": False, "viewing_confirmed": False,
                    "manual_takeover": False, "status": "new", "last_inbound": None}}}
jid_rd = "6590999030@s.whatsapp.net"; pn_rd = "6590999030"
a_rd1 = E.handle_event(st_rd, {"jid": jid_rd, "msg_id": "rd1",
                                "text": "hi is the bayshore room available to rent?",
                                "is_from_me": 0})
st_rd["conversations"][pn_rd]["form_sent_ts"] -= 300
# distinctive, greppable PII markers so any echo of them anywhere is unmistakable.
_rd_form = ("Name: Zorptenant Uniquename\nEmail: zorptenant@example.com\n"
            "Nationality: Malaysian\nNo. of Pax: 1\nBudget: 2500\nLease: 12 months")
a_rd2 = E.handle_event(st_rd, {"jid": jid_rd, "msg_id": "rd2", "text": _rd_form, "is_from_me": 0})
_actions = [a for a in (a_rd1, a_rd2) if a]
ok("qualified tenant -> OFFER_VIEWING (tenant-facing + Winfred ping), not a landlord send",
   a_rd2 and a_rd2["type"] == "OFFER_VIEWING")
ok("tenant flow emits NO landlord-directed send action type",
   all(a.get("type") not in _LANDLORD_SEND_TYPES for a in _actions))
_out = ((a_rd2 or {}).get("text") or "").lower()
ok("qualified reply text does not echo the tenant's budget figure", "2500" not in _out)
ok("qualified reply text carries no email address", "@" not in _out)

print("== 7. UNIT REJECTION: 'dont like' always routes onward (nearby alt OR channel); 'found a place' closes ==")
# Winfred's rule (2 Sep 2026): when a tenant does not like the unit, auto-route them onward --
# a nearby same-district/preferred-location alternative if one fits, else the rental channel.
# Already built: withdrawal_signal is checked FIRST (someone who found a place elsewhere is
# closed, never cross-sold), then _unit_rejection cross-sells via suggest_alternative (SUGGEST_ALT)
# or, if nothing fits, REDIRECT with the CHANNEL link. This locks both branches.
def _active_rec(pn, listing="caspian"):
    return {"version": 1, "conversations": {pn: {
        "pn": pn, "listing_key": listing, "stage": "FORM_SENT", "profile": {},
        "processed_ids": [], "form_sent": True, "asked_fields": [], "viewing_asked": False,
        "viewing_confirmed": False, "manual_takeover": False, "status": "awaiting_form",
        "alt_suggested": False, "buyer_form_sent": False, "last_inbound": None}}}
st_ur = _active_rec("6590100077")
a_ur = E.handle_event(st_ur, {"jid": "6590100077@s.whatsapp.net", "msg_id": "ur1",
                               "text": "i don't like this one, too small", "is_from_me": 0})
ok("unit rejection -> SUGGEST_ALT or REDIRECT (tenant never just dropped)",
   a_ur and a_ur.get("type") in ("SUGGEST_ALT", "REDIRECT"))
_urt = (a_ur or {}).get("text") or ""
ok("onward route is concrete: a nearby alternative to view, OR the rental channel link",
   (a_ur and a_ur.get("type") == "SUGGEST_ALT" and "arrange a viewing" in _urt.lower())
   or (E.CHANNEL in _urt))
# contrast: 'found a place already' is a withdrawal -> AUTO_CLOSED, never a cross-sell / channel push.
st_w = _active_rec("6590100076")
a_w = E.handle_event(st_w, {"jid": "6590100076@s.whatsapp.net", "msg_id": "w1",
                             "text": "thanks, i found a place already", "is_from_me": 0})
ok("withdrawal ('found a place already') -> AUTO_CLOSED, not a cross-sell",
   a_w and a_w.get("type") == "AUTO_CLOSED")

print(f"\nRESULT: {P} passed, {F} failed")
sys.exit(1 if F else 0)
