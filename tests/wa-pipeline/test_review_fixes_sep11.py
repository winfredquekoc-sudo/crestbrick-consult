"""
test_review_fixes_sep11.py -- unittest coverage for the 11 Sep 2026 review fixes and the
remaining "still present" defects from the re attack (scratchpad/attack/flow/review.md
sections 3 and 4, scratchpad/attack/flow/after/reattack-summary.md section 3). Each
TestCase names the fix it guards.

Run: /usr/bin/python3 tests/wa-pipeline/test_review_fixes_sep11.py
"""
import sys, os, json, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
import wa_intake_echo as ECHO
import wa_money_gate as MG


def _listing(lk, status="open", deal_type="rent", budget_floor=700, lease_min_months=12,
             gender="any", nationality_mode="any", nationality_list=None, landlord_id=None,
             district=None, **extra):
    d = {
        "listing_key": lk, "status": status, "deal_type": deal_type,
        "landlord_id": landlord_id, "district": district,
        "pg_url_keywords": [lk.replace("-", " ")],
        "block_address": "Fake St 11 #05-04 Singapore 123456",
        "requirements": {
            "gender": gender, "couple_ok": True, "couple_must_be_married": False,
            "ethnicity_rule": {"mode": "any", "list": []},
            "nationality_pref": {"mode": nationality_mode, "list": nationality_list or []},
            "pass_type_allowed": [], "occupation_rule": {"mode": "any", "list": []},
            "max_pax": 2, "lease_min_months": lease_min_months, "lease_max_months": None,
            "budget_floor": budget_floor, "min_age": None, "cooking": "light",
            "pets_tenant_may_bring": False, "smoking": "no",
        },
    }
    d.update(extra)
    return d


class _ReqsFixtureMixin:
    """Same pattern as test_attack_fixes_sep11_a.py -- swaps E.listing_reqs / E._master_status
    for test control, never reads the real landlord DB."""

    def setUp(self):
        self._orig_listing_reqs = E.listing_reqs
        self._orig_master_status = E._master_status
        self._reqs = {}
        E.listing_reqs = lambda: dict(self._reqs)
        E._master_status = lambda lk, reqs=None: None

    def tearDown(self):
        E.listing_reqs = self._orig_listing_reqs
        E._master_status = self._orig_master_status

    def _state_with_rec(self, jid="6598887001@s.whatsapp.net", **rec_fields):
        st = {"version": 1, "conversations": {}}
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec.update(rec_fields)
        return st, pn, jid, rec

    def _ev(self, jid, text, msg_id="m1"):
        return {"jid": jid, "msg_id": msg_id, "text": text, "is_from_me": False}


# ---------------------------------------------------------------------------
# Review fix 1: the engine's own confirm viewing / buyer offer / disclosure sends must be
# recognised as its own outbound by BOTH the takeover latch matcher (is_engine_outbound) and
# the echo matcher (wa_intake_echo._is_our_echo) -- otherwise the engine silences itself on
# every chat it just booked, and a bridge echoed copy is reprocessed as a prospect inbound.
# ---------------------------------------------------------------------------
class TestEngineSendsRecognisedAsOwnOutbound(unittest.TestCase):
    def test_confirm_viewing_question_branch_text_is_engine_outbound(self):
        text = "Your viewing is on Sat 12 Sep, 3pm to 4pm \U0001F642"
        self.assertTrue(E.is_engine_outbound(text))
        self.assertTrue(E.is_bot_message(text))
        self.assertTrue(ECHO._is_our_echo(text))

    def test_buyer_qualified_offer_text_is_engine_outbound(self):
        text = ("Thanks, that fits what we are looking for. The next viewing "
                "is Sat 12 Sep, 3pm to 4pm. Let me know if you would like to come by.")
        self.assertTrue(E.is_engine_outbound(text))
        self.assertTrue(E.is_bot_message(text))
        self.assertTrue(ECHO._is_our_echo(text))

    def test_agent_disclosure_text_is_engine_outbound(self):
        text = "I am the agent helping the landlord with this unit \U0001F642"
        self.assertTrue(E.is_engine_outbound(text))
        self.assertTrue(E.is_bot_message(text))
        self.assertTrue(ECHO._is_our_echo(text))


# ---------------------------------------------------------------------------
# Review fix 3: the buyer QUALIFIED slot offer and the SEND_OPEN_HOUSE branch must be gated
# on the bound listing's own deal_type/status, same shape as the SEND_BUYER_FORM gate.
# ---------------------------------------------------------------------------
class TestBuyerOfferGatedOnDealTypeAndStatus(_ReqsFixtureMixin, unittest.TestCase):
    def test_qualified_slot_never_offered_on_a_rental_bound_buyer_record(self):
        self._reqs["rent-lk"] = _listing("rent-lk", deal_type="rent")
        rec = {"listing_key": "rent-lk",
               "buyer": {"financing": "valid", "budget": 2000000, "timeline": "1 month",
                         "area_or_type": "D15", "property_to_sell": "no"}}
        ev = {"text": "just confirming, that is everything"}
        a = E._buyer_followup(rec, ev, "6598887001")
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertIsNone(a.get("text"))          # no slot leaked from the rental listing
        self.assertTrue(a.get("notify"))

    def test_qualified_slot_never_offered_on_a_closed_sale_listing(self):
        self._reqs["closed-lk"] = _listing("closed-lk", deal_type="sale", status="closed")
        rec = {"listing_key": "closed-lk",
               "buyer": {"financing": "valid", "budget": 2000000, "timeline": "1 month",
                         "area_or_type": "D15", "property_to_sell": "no"}}
        ev = {"text": "just confirming, that is everything"}
        a = E._buyer_followup(rec, ev, "6598887001")
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))

    def test_qualified_slot_offered_on_an_open_sale_listing(self):
        self._reqs["open-lk"] = _listing("open-lk", deal_type="sale", status="open")
        with mock.patch.object(E, "next_slot", lambda lk: {"label": "Sun 13 Sep, 2pm"}):
            rec = {"listing_key": "open-lk",
                   "buyer": {"financing": "valid", "budget": 2000000, "timeline": "1 month",
                             "area_or_type": "D15", "property_to_sell": "no"}}
            ev = {"text": "just confirming, that is everything"}
            a = E._buyer_followup(rec, ev, "6598887001")
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertIn("Sun 13 Sep", a["text"])

    def test_open_house_ask_flagged_on_a_rental_bound_buyer_record(self):
        self._reqs["rent-lk2"] = _listing("rent-lk2", deal_type="rent")
        rec = {"listing_key": "rent-lk2", "buyer": {}}
        ev = {"text": "can u arrange an open house dis weekend?"}
        a = E._buyer_followup(rec, ev, "6598887001")
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))


# ---------------------------------------------------------------------------
# Review fix 4: a money/negotiation question riding on the SAME message that completes the
# buyer profile must FLAG_HUMAN, never auto complete with a viewing offer.
# ---------------------------------------------------------------------------
class TestBuyerCompleteStaysHumanOnMoneyContent(unittest.TestCase):
    def test_timeline_plus_counter_offer_flags_not_auto_offers(self):
        rec = {"listing_key": None,
               "buyer": {"financing": "valid", "timeline": "3 months",
                         "area_or_type": "D15", "property_to_sell": "no"}}
        ev = {"text": "my timeline is 2 months, and can the owner do 630k?"}
        a = E._buyer_followup(rec, ev, "6598887001")
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))
        self.assertIn("630k", a.get("reason") or "")

    def test_full_form_with_a_cash_and_loan_field_still_auto_completes(self):
        # regression guard: a routine filled form's own field VALUES ("cash and loan") must
        # never be misread as negotiation content just because "cash" is in the word list.
        rec = {"listing_key": None, "buyer": {}}
        ev = {"text": "Name: Kevin Tan\nCitizenship: Singaporean\nBudget: 1450000\n"
                      "Timeline to buy: 1 month\nAny property to sell first: no\n"
                      "Paying with CPF / Cash / Loan: cash and loan\nIPA valid?: yes, valid\n"
                      "Preferred area or district: D20\nProperty type: Condo\n"
                      "Bedrooms needed: 2\nFor own stay or investment: own stay"}
        a = E._buyer_followup(rec, ev, "6598887001")
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertEqual(a.get("verdict"), "QUALIFIED")


# ---------------------------------------------------------------------------
# Review fix 5: the zh language upgrade needs 2+ CJK characters AND must not fire on a bare
# acknowledgement (谢谢/谢谢你/好的/OK 谢谢 etc).
# ---------------------------------------------------------------------------
class TestLanguageUpgradeGate(_ReqsFixtureMixin, unittest.TestCase):
    def test_bare_thanks_never_upgrades_language(self):
        self._reqs["lk1"] = _listing("lk1")
        st, pn, jid, rec = self._state_with_rec(listing_key="lk1", form_sent=True, lang="en")
        E.handle_event(st, self._ev(jid, "谢谢"))
        self.assertEqual(rec.get("lang"), "en")

    def test_ok_xiexie_never_upgrades_language(self):
        self._reqs["lk1b"] = _listing("lk1b")
        st, pn, jid, rec = self._state_with_rec(listing_key="lk1b", form_sent=True, lang="en")
        E.handle_event(st, self._ev(jid, "OK 谢谢"))
        self.assertEqual(rec.get("lang"), "en")

    def test_single_cjk_char_never_upgrades_language(self):
        self._reqs["lk1c"] = _listing("lk1c")
        st, pn, jid, rec = self._state_with_rec(listing_key="lk1c", form_sent=True, lang="en")
        E.handle_event(st, self._ev(jid, "好 la can"))
        self.assertEqual(rec.get("lang"), "en")

    def test_genuine_chinese_sentence_upgrades_language(self):
        self._reqs["lk1d"] = _listing("lk1d")
        st, pn, jid, rec = self._state_with_rec(listing_key="lk1d", form_sent=True, lang="en")
        E.handle_event(st, self._ev(jid, "我想问一下租期可以短一点吗"))
        self.assertEqual(rec.get("lang"), "zh")


# ---------------------------------------------------------------------------
# Residual risk 5: the lease note YES overwrite must only move lease_term_months up when it
# is empty/below the floor AND this is the FIRST reply after the note -- a bare "yes" later
# in the chat, after a genuine short value was set in between, must never clobber it.
# ---------------------------------------------------------------------------
class TestLeaseNoteOverwriteScopedToFirstReply(unittest.TestCase):
    def test_bare_yes_as_first_reply_sets_the_floor(self):
        rec = {"profile": {}, "lease_note_sent": True, "lease_note_min": 12,
               "lease_note_resolved": False}
        a = E._lease_note_pending_resolution(rec, {"text": "yes"}, "pn1")
        self.assertIsNone(a)
        self.assertTrue(rec["lease_note_resolved"])
        self.assertEqual(rec["profile"]["lease_term_months"], 12)

    def test_bare_yes_after_a_later_short_value_never_overwrites_it(self):
        rec = {"profile": {}, "lease_note_sent": True, "lease_note_min": 12,
               "lease_note_resolved": False}
        # first reply: ambiguous, stays unresolved, flags once (marks the window "seen")
        a1 = E._lease_note_pending_resolution(rec, {"text": "let me check with my partner"}, "pn1")
        self.assertIsNotNone(a1)
        self.assertFalse(rec["lease_note_resolved"])
        # a later message genuinely sets a short lease term (via whatever path -- simulated
        # directly here since the merge step that does this in production runs outside this
        # function)
        rec["profile"]["lease_term_months"] = 3
        # now a bare "yes", answering something else entirely, arrives
        a2 = E._lease_note_pending_resolution(rec, {"text": "yes"}, "pn1")
        self.assertIsNone(a2)
        self.assertEqual(rec["profile"]["lease_term_months"], 3)   # never rewritten to 12


# ---------------------------------------------------------------------------
# Residual risk 4: missing_required drops gender/ethnicity/nationality from the nudge when
# the bound listing's own gates are all "any". The cross listing rescreen (suggest_alternative
# via the plain DISQUALIFIED redirect) must still return an alternative when a candidate is
# only NEEDS_INFO on those fields, and flag Winfred instead of a silent stall.
# ---------------------------------------------------------------------------
class TestAlternativeOfferNeverSilentlyStallsOnUnknownGates(_ReqsFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._orig_landlord_by_id = E._landlord_by_id
        E._landlord_by_id = lambda: {}
        self.addCleanup(lambda: setattr(E, "_landlord_by_id", self._orig_landlord_by_id))

    def test_gap_is_surfaced_not_silently_dropped(self):
        # the ORIGINAL listing gates all "any" -> gender/ethnicity/nationality never asked.
        self._reqs["orig"] = _listing("orig", district="D1", budget_floor=5000)
        # the ALTERNATIVE gates on gender -> NEEDS_INFO once gender is unknown.
        self._reqs["alt"] = _listing("alt", district="D1", gender="male_only", budget_floor=700,
                                      status="active")
        profile = {"no_of_pax": 1, "budget": 800, "lease_term_months": 12}  # fails "orig" (5000 floor), fits "alt" (700 floor)
        with mock.patch.object(E, "listing_unit_message", lambda k: "Unit brief for " + k):
            act = E._plain_disqualified_redirect("pn1", ["budget below 5000"], profile,
                                                  E.listing_reqs(), "orig")
        self.assertEqual(act["type"], "REDIRECT")
        self.assertIn("Unit brief for alt", act["text"])   # alternative still offered
        self.assertTrue(act.get("notify"))                 # never a silent stall
        self.assertIn("gender", act["reason"])

    def test_no_gap_when_alternative_is_fully_qualified(self):
        self._reqs["orig2"] = _listing("orig2", district="D2", budget_floor=5000)
        self._reqs["alt2"] = _listing("alt2", district="D2", gender="any", budget_floor=700,
                                       status="active")
        profile = {"no_of_pax": 1, "budget": 800, "lease_term_months": 12}
        with mock.patch.object(E, "listing_unit_message", lambda k: "Unit brief for " + k):
            act = E._plain_disqualified_redirect("pn1", ["budget below 5000"], profile,
                                                  E.listing_reqs(), "orig2")
        self.assertEqual(act["type"], "REDIRECT")
        self.assertIn("Unit brief for alt2", act["text"])
        self.assertNotIn("notify", act)   # unchanged behaviour: no gap, no extra flag


# ---------------------------------------------------------------------------
# Still present c1-01: any inbound carrying a money question combined with a short lease
# must flag Winfred, never a silent LEASE_NOTE -- across turns, not only the same message.
# ---------------------------------------------------------------------------
class TestPriceQuestionNeverSwallowedByLaterLeaseNote(_ReqsFixtureMixin, unittest.TestCase):
    def test_full_profile_after_an_earlier_unanswered_price_question_flags_not_silent_note(self):
        self._reqs["rm1"] = _listing("rm1", budget_floor=850, lease_min_months=12)
        st, pn, jid, rec = self._state_with_rec(listing_key="rm1", form_sent=True, profile={})
        a1 = E.handle_event(st, self._ev(
            jid, "Aiya bro, I found dis 3mth lease on PropertyGuru. How much is ur rent per "
                 "month? Is it for single indiv or can stay with gf?", msg_id="m1"))
        self.assertTrue(a1.get("notify"))
        self.assertTrue(rec.get("lease_note_money_flagged"))
        self.assertFalse(rec.get("lease_note_sent"))
        a2 = E.handle_event(st, self._ev(
            jid, "Name: Marcus Tan\nNationality: Singaporean\nEthnicity: Chinese\nGender: "
                 "Male\nPass type: Citizen\nNo. of pax: 2\nMove in date: 20 Sep\nLease term: "
                 "3\nBudget: 900", msg_id="m2"))
        self.assertNotEqual(a2["type"], "LEASE_NOTE")
        self.assertEqual(a2["type"], "FLAG_HUMAN")
        self.assertTrue(a2.get("notify"))
        self.assertIsNone(a2.get("text"))


# ---------------------------------------------------------------------------
# Still present c1-08: a deposit/lock in offer riding on a confirm viewing reply must be
# matched by the money gate even without the bare word "deposit" carrying the whole load.
# ---------------------------------------------------------------------------
class TestDepositAndLockInVocabularyWidened(unittest.TestCase):
    def test_pay_now_and_lock_it_in_are_money_gate_territory(self):
        self.assertTrue(MG.core_stays_human("YES lock it in for me pls, will pay deposit now if needed"))
        self.assertTrue(MG.core_stays_human("can I paynow you the deposit right now"))
        self.assertTrue(MG.core_stays_human("I can transfer the deposit today if you lock it in"))


# ---------------------------------------------------------------------------
# Still present c5s04: a landlord contact detail ask must never be read as a viewing time,
# and must surface its own distinct FLAG_HUMAN reason, not a generic ASK_ONE/no match flag.
# ---------------------------------------------------------------------------
class TestLandlordPhoneAskNotMisreadAsViewingTime(_ReqsFixtureMixin, unittest.TestCase):
    def test_hp_num_ask_is_not_a_viewing_time(self):
        self.assertFalse(E._has_viewing_time(
            "hi landlord can i get your hp num pls, wanna come view tomorrow afternoon?"))

    def test_end_to_end_flags_contact_detail_not_ask_one(self):
        self._reqs["rm-phone"] = _listing("rm-phone", budget_floor=850)
        st, pn, jid, rec = self._state_with_rec(listing_key="rm-phone", form_sent=True, profile={})
        ev = self._ev(jid, "Hi landlord can i get your hp num pls, wanna come view "
                            "tomorrow afternoon?")
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a.get("notify"))
        self.assertIn("contact detail", (a.get("reason") or "").lower())


# ---------------------------------------------------------------------------
# Still present c4rm05: an open house ask on a sale listing that carries an open_house_message
# (whether on the template or the listing registry itself) must send it verbatim; without one
# it must FLAG_HUMAN, never a viewing time guess.
# ---------------------------------------------------------------------------
class TestOpenHouseMessageFallsBackToListingRegistry(unittest.TestCase):
    def test_buyer_template_falls_back_to_listing_registrys_own_open_house_message(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tpl_path = os.path.join(tmp.name, "property-templates.json")
        json.dump({"listings": []}, open(tpl_path, "w"))
        with mock.patch.object(E, "TEMPLATES", tpl_path), \
             mock.patch.object(E, "listing_reqs", lambda: {"lk-oh": {
                 "open_house_message": "Open house this Sunday 2pm to 4pm."}}):
            tpl = E._buyer_template("lk-oh")
        self.assertEqual(tpl.get("open_house_message"), "Open house this Sunday 2pm to 4pm.")

    def test_open_house_ask_on_sale_listing_without_message_flags_human(self):
        rec = {"listing_key": "lk-nomsg", "buyer": {}}
        with mock.patch.object(E, "listing_reqs", lambda: {"lk-nomsg": {
                "deal_type": "sale", "status": "open"}}), \
             mock.patch.object(E, "TEMPLATES", os.devnull if False else E.TEMPLATES):
            with mock.patch.object(E, "_load", lambda p, d: {"listings": []}):
                ev = {"text": "can u arrange an open house dis weekend?"}
                a = E._buyer_followup(rec, ev, "pn1")
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))


# ---------------------------------------------------------------------------
# Still present c4rm03: a buyer message pushing a number ("below valuation", "can do 630k")
# must route through the money gate first, never be classified as a viewing time.
# ---------------------------------------------------------------------------
class TestNegotiationPushNeverAViewingTime(unittest.TestCase):
    def test_below_valuation_consider_is_money_gate_territory(self):
        self.assertTrue(MG.core_stays_human(
            "Hey, just checked PropertyGuru and this unit seems overs priced to me... "
            "if you sell below valuation, will you consider liao?"))

    def test_how_much_below_is_money_gate_territory(self):
        self.assertTrue(MG.core_stays_human("how much below can we talk about"))

    def test_can_the_owner_do_a_number_is_money_gate_territory(self):
        self.assertTrue(MG.core_stays_human("can the owner do 630k"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
