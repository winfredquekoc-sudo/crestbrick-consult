"""
test_attack_fixes_sep11_d.py -- package D (buyer flow) of the Sep 11 2026 attack fix round.
Findings detail: scratchpad/attack/flow/findings.json. Design: scratchpad/qualification-
redesign.md section 1 (Buyer) + section 2 (message-by-message) + section 3C.

Covers 8 defect keys, each reproduced first against the pre-fix shape of the bug (cited
scenario in each test's docstring), then asserted fixed:
  sale-description-not-sent-before-form   (c4rm01)
  open-house-message-never-used           (c4rm05, c4rm02 Kembangan/D'Leedon)
  qualified-buyer-dead-ends-no-offer       (c4rm05)
  buyer-silent-after-manual-takeover       (c4rm01, c4rm08)
  cobroke-agent-not-excluded-in-buyer-flow (c4rm06)
  buyer-form-return-notify-coalesced-away  (c4rm02)
  buyer-form-sent-with-no-listing-bound    (c4rm04)
  buyer-text-leaks-into-tenant-profile     (c4rm08)

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_fixes_sep11_d.py
"""
import sys, os, json, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E
import wa_intake_notify as N

# ---------- shared synthetic fixture (never touches live state) ----------
_LISTINGS = {
    "listings": [
        {"listing_key": "d-sale-plain", "status": "open", "deal_type": "sale",
         "pg_url_keywords": ["test street one fake"],
         "requirements": {"property_type": "hdb"}},
        {"listing_key": "d-sale-oh", "status": "open", "deal_type": "sale",
         "pg_url_keywords": ["test view fake"],
         "requirements": {"property_type": "private"}},
        {"listing_key": "d-sale-oh-skip", "status": "open", "deal_type": "sale",
         "pg_url_keywords": ["test villas fake"],
         "requirements": {"property_type": "private"}},
        {"listing_key": "d-rent-unrelated", "status": "open", "deal_type": "rent",
         "pg_url_keywords": ["unrelated rental room"],
         "requirements": {"gender": "any", "budget_floor": 900}},
    ]
}
_TEMPLATES = {
    "listings": [
        {"id": "d-sale-plain", "message": "Resale 4 Room flat at Test Street 1 (fake), "
                                           "asking S$ 650,000. Renovated, near a fake MRT."},
        {"id": "d-sale-oh", "message": "Resale 2 Bedroom condo at Test View (fake), "
                                       "asking S$ 1,480,000. Renovated.",
         "open_house_message": "Open house at fake Test View this Sunday 2pm to 4pm, "
                                "register at the show unit lobby, no appointment needed."},
        {"id": "d-sale-oh-skip", "skip_buyer_form": True,
         "open_house_message": "Hi, thanks for your interest in Test Villas. There's an "
                                "open house this Saturday, 11am to 12noon, do drop by."},
    ]
}


class _FixtureBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        idx_path = os.path.join(self._tmp.name, "listing-index.json")
        tpl_path = os.path.join(self._tmp.name, "property-templates.json")
        avail_path = os.path.join(self._tmp.name, "viewing-availability.json")
        json.dump(_LISTINGS, open(idx_path, "w"))
        json.dump(_TEMPLATES, open(tpl_path, "w"))
        json.dump({}, open(avail_path, "w"))
        self._patches = [
            mock.patch.object(E, "IDX", idx_path),
            mock.patch.object(E, "TEMPLATES", tpl_path),
            mock.patch.object(E, "AVAIL", avail_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        E.listing_reqs.cache_clear() if hasattr(E.listing_reqs, "cache_clear") else None

    def _ev(self, jid, mid, text, listing_key=None, is_from_me=0):
        return {"jid": jid, "msg_id": mid, "text": text, "is_from_me": is_from_me,
                "listing_key": listing_key}


class TestSaleDescriptionBeforeForm(_FixtureBase):
    """sale-description-not-sent-before-form (c4rm01-bishan-flat-psf-price-handreply-
    complete): every bound sale enquiry got the buyer form as the FIRST and ONLY message;
    the listing's own sale description was never sent. Rubric: message 1 is the
    description, message 2 the form."""

    def test_bound_sale_enquiry_sends_description_then_form(self):
        s = {"version": 1, "conversations": {}}
        a = E.handle_event(s, self._ev("6590001001@s.whatsapp.net", "m1",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000\n\nThanks", listing_key="d-sale-plain"))
        self.assertEqual(a["type"], "SEND_BUYER_FORM")
        texts = a.get("texts") or []
        self.assertEqual(len(texts), 2)
        self.assertIn("Test Street 1", texts[0])
        self.assertNotIn("Citizenship", texts[0])
        self.assertIn("Citizenship", texts[1])


class TestOpenHouseMessageUsed(_FixtureBase):
    """open-house-message-never-used (c4rm05-thomson-view-open-house-complete): the
    listing's open_house_message never appeared in any sent text, log, or state across the
    whole run; the field was inert. Now used both on request mid-conversation and via the
    skip_buyer_form override at first contact (commit a6523909)."""

    def test_buyer_asking_about_open_house_gets_the_invite_verbatim(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590002001@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test View Fake\n"
            "2 Bedroom / S$ 1,480,000", listing_key="d-sale-oh"))
        a = E.handle_event(s, self._ev(jid, "m1",
            "Hi seller, can u arrange an open house dis weekend?", listing_key="d-sale-oh"))
        self.assertEqual(a["type"], "SEND_OPEN_HOUSE")
        self.assertIn("Open house at fake Test View", a["text"])
        self.assertTrue(s["conversations"][E.resolve_pn(jid)]["open_house_sent"])
        # any later reply hands straight to Winfred, never a resend / never advice
        a2 = E.handle_event(s, self._ev(jid, "m2", "can I come this sunday then",
                                         listing_key="d-sale-oh"))
        self.assertEqual(a2["type"], "FLAG_HUMAN")
        self.assertIsNone(a2.get("text"))
        self.assertTrue(a2.get("notify"))

    def test_skip_buyer_form_listing_sends_description_plus_open_house_not_the_form(self):
        s = {"version": 1, "conversations": {}}
        a = E.handle_event(s, self._ev("6590002002@s.whatsapp.net", "m1",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Villas Fake\n"
            "6 bedroom / S$ 7,299,999", listing_key="d-sale-oh-skip"))
        self.assertEqual(a["type"], "SEND_OPEN_HOUSE")
        self.assertNotIn("Citizenship", a["text"])
        self.assertIn("There's an open house", a["text"])


class TestQualifiedBuyerGetsOffer(_FixtureBase):
    """qualified-buyer-dead-ends-no-offer (c4rm05): a fully qualified buyer (IPA valid,
    budget 98% of asking, timeline 1 month) returned FLAG_HUMAN 'no automated reply
    matched' with an empty notified list -- BUYER_COMPLETE never fired. Root cause traced:
    the form-fill text keyword-rebound the event to an unrelated listing, so
    classify_transaction read that OTHER listing's deal_type as rent and skipped
    _buyer_followup entirely. Reproduced here by passing a DIFFERENT ev listing_key on the
    same message (the keyword-rebind harness's exact failure shape) to prove the record's
    own bound listing wins."""

    def _complete_form(self):
        return ("Name: Kevin Tan\nCitizenship: Singaporean\nBudget: 1450000\n"
                "Timeline to buy: 1 month\nAny property to sell first: no\n"
                "Paying with CPF / Cash / Loan: cash and loan\nIPA valid?: yes, valid\n"
                "Preferred area or district: D20\nProperty type: Condo\n"
                "Bedrooms needed: 2\nFor own stay or investment: own stay")

    def test_qualified_buyer_gets_an_offer_not_a_dead_end_flag(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590003001@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test View Fake\n"
            "2 Bedroom / S$ 1,480,000", listing_key="d-sale-oh"))
        # the completed form's own free text (district D20, "Condo"...) is passed with a
        # DIFFERENT ev listing_key -- exactly the keyword-rebind shape that broke this in
        # the cited replay. The record's OWN bound listing (d-sale-oh) must still win.
        a = E.handle_event(s, self._ev(jid, "m1", self._complete_form(),
                                        listing_key="d-rent-unrelated"))
        self.assertIsNotNone(a)
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertEqual(a.get("verdict"), "QUALIFIED")
        self.assertTrue(a.get("notify"))
        # QUALIFIED gets an actual offer (open house here), never silence
        self.assertIsNotNone(a.get("text"))
        self.assertIn("open house", a["text"].lower())

    def test_not_a_fit_on_budget_far_below_asking_stays_silent_with_a_flag(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590003002@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000", listing_key="d-sale-plain"))
        form = ("Name: Amy\nCitizenship: Singaporean\nBudget: 400000\n"
                "Timeline to buy: 1 month\nAny property to sell first: no\n"
                "HFE valid?: yes, valid\nPreferred area or district: D18\n"
                "Property type: HDB\nFor own stay or investment: own stay")
        a = E.handle_event(s, self._ev(jid, "m1", form, listing_key="d-sale-plain"))
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertEqual(a.get("verdict"), "NOT_A_FIT")
        self.assertIsNone(a.get("text"))            # never told they don't fit
        self.assertTrue(a.get("notify"))

    def test_not_yet_financing_never_told_to_go_get_an_hfe(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590003003@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000", listing_key="d-sale-plain"))
        form = ("Name: Ben\nCitizenship: Singaporean\nBudget: 630000\n"
                "Timeline to buy: 1 month\nAny property to sell first: no\n"
                "HFE valid?: not yet, applying\nPreferred area or district: D18\n"
                "Property type: HDB\nFor own stay or investment: own stay")
        a = E.handle_event(s, self._ev(jid, "m1", form, listing_key="d-sale-plain"))
        self.assertEqual(a["type"], "BUYER_COMPLETE")
        self.assertEqual(a.get("verdict"), "NOT_YET")
        self.assertIsNone(a.get("text"))             # no advice ever auto sent
        self.assertTrue(a.get("notify"))


class TestBuyerSilentAfterManualTakeover(_FixtureBase):
    """buyer-silent-after-manual-takeover (c4rm01, c4rm08): once Winfred hand replies,
    manual_takeover latches and every later buyer message produces zero action, zero
    notify -- _copilot_verdict reads the TENANT profile shape (always empty for a buyer
    record) so it silently returns None. A buyer must still ping Winfred on every new
    inbound; the prospect stays silent (takeover holds), Winfred does not."""

    def test_qualifying_buyer_pings_winfred_after_hand_takeover_never_silent(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590004001@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000", listing_key="d-sale-plain"))
        # Winfred hand replies -> manual_takeover latches (is_from_me, not engine-tagged)
        E.handle_event(s, self._ev(jid, "w1", "hi thanks for reaching out, let me pull "
                                    "the numbers and get back to you", is_from_me=1))
        pn = E.resolve_pn(jid)
        self.assertTrue(s["conversations"][pn]["manual_takeover"])
        form = ("Name: Desmond Ong\nCitizenship: Singaporean\nBudget: 630000\n"
                "Timeline to buy: 2 months\nAny property to sell first: no\n"
                "HFE valid?: yes, valid\nPreferred area or district: D18\n"
                "Property type: HDB\nFor own stay or investment: own stay")
        a = E.handle_event(s, self._ev(jid, "m1", form))
        self.assertIsNotNone(a, "a qualifying buyer profile must never vanish silently")
        self.assertEqual(a["type"], "COPILOT_VERDICT")
        self.assertTrue(a.get("buyer"))
        self.assertTrue(a.get("notify"))
        self.assertIsNone(a.get("text"), "still silent to the PROSPECT under takeover")
        self.assertEqual(a.get("verdict"), "QUALIFIED")
        # a further follow up must still ping, never RESUME_SKIP-style silence
        a2 = E.handle_event(s, self._ev(jid, "m2", "let me know if can view this week"))
        self.assertIsNotNone(a2)
        self.assertTrue(a2.get("notify"))


class TestCobrokeAgentExcludedInBuyerFlow(_FixtureBase):
    """cobroke-agent-not-excluded-in-buyer-flow (c4rm06-hougang-cobroke-agent-excluded): a
    self declared CEA agent asking to co-broke was treated as an ordinary buyer question;
    the mid-flow exclusion re-check only looked at rec['form_sent'] (the TENANT flag,
    always False for a buyer), so a buyer record was never re-screened as an agent and
    stayed armed to keep auto-messaging him."""

    def test_cobroke_agent_message_latches_manual_takeover_on_a_buyer_record(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590005001@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000", listing_key="d-sale-plain"))
        pn = E.resolve_pn(jid)
        self.assertFalse(s["conversations"][pn]["manual_takeover"])
        a = E.handle_event(s, self._ev(jid, "m1",
            "Hi seller, im a fellow agent. Got interested buyer ask me about ur place... "
            "can we co-broke leh?"))
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertEqual(a.get("reason"), "excluded agent")
        self.assertTrue(s["conversations"][pn]["manual_takeover"])
        self.assertEqual(s["conversations"][pn]["status"], "excluded:agent")


class TestBuyerFormReturnNotifyBypassesCoalesce(_FixtureBase):
    """buyer-form-return-notify-coalesced-away (c4rm02-dleedon-condo-absd-foreigner-
    incomplete): the returned buyer form produced a FLAG_HUMAN with notify=True but an
    empty notified list -- the 30 minute per-chat coalesce window had already been opened
    by an earlier routine flag on the same chat, so the profile return was HELD instead of
    reaching Winfred within the tick."""

    def test_engine_marks_a_returned_form_flag_for_coalesce_bypass(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590006001@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000", listing_key="d-sale-plain"))
        pn = E.resolve_pn(jid)
        s["conversations"][pn]["buyer_form_sent_ts"] -= 200   # clear the 180s grace window
        # near-complete form, missing only financing -> nudge, then (after the grace
        # window) a "still missing after nudge" flag that must bypass coalescing.
        form = ("Name: Ada\nBudget: 630000\nTimeline to buy: 2 months\n"
                "Any property to sell first: no\nPreferred area or district: D18\n"
                "Property type: HDB\nFor own stay or investment: own stay")
        a1 = E.handle_event(s, self._ev(jid, "m1", form))
        self.assertEqual(a1["type"], "BUYER_NUDGE")
        s["conversations"][pn]["buyer_nudged_ts"] -= 200   # clear the 180s grace window
        a2 = E.handle_event(s, self._ev(jid, "m2", "just checking in on this"))
        self.assertEqual(a2["type"], "FLAG_HUMAN")
        self.assertIn("missing", a2["reason"])
        self.assertTrue(a2.get("bypass_coalesce"),
                         "a returned/near-complete buyer form must bypass the 30 min "
                         "coalesce window, not wait behind routine chatter")

    def test_notify_for_action_actually_bypasses_an_open_window(self):
        # unit-level proof at the notify layer: an already open coalesce window normally
        # HOLDS a second ping for the same chat: bypass_coalesce=True must skip that.
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(N, "COALESCE_FILE", os.path.join(td, "coalesce.json")):
                sent = []
                with mock.patch.object(N, "notify_winfred", sent.append):
                    N.notify_winfred_coalesced("6590006002", "first (opens the window)")
                    state = {"conversations": {"6590006002": {}}}
                    N.notify_for_action({"type": "FLAG_HUMAN", "notify": True,
                                          "pn": "6590006002", "reason": "buyer still missing X",
                                          "bypass_coalesce": True}, state)
                self.assertEqual(len(sent), 2, "the bypass ping must NOT be held")


class TestBuyerFormNeverSentUnbound(_FixtureBase):
    """buyer-form-sent-with-no-listing-bound (c4rm04-punggol-floorplan-photos-unbound): a
    buy-shaped message with no listing reference fired the full buyer intake funnel with
    listing_key null -- nobody could tell which unit the buyer meant and qualification
    could never run. Bind or flag, never a blind guess."""

    def test_unbound_buyer_enquiry_flags_instead_of_sending_a_blind_form(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590007001@s.whatsapp.net"
        a = E.handle_event(s, self._ev(jid, "m1",
            "Nice flat! Can I request for floor plan and pics lah? Tks! "
            "want to buy for own stay, resale flat budget 500k"))
        pn = E.resolve_pn(jid)
        rec = s["conversations"].get(pn, {})
        if rec.get("transaction") == "sale":
            self.assertEqual(a["type"], "FLAG_HUMAN")
            self.assertIsNone(a.get("text"))
            self.assertTrue(a.get("notify"))
            self.assertIsNone(rec.get("listing_key"))
            self.assertFalse(rec.get("buyer_form_sent"))


class TestBuyerTextNeverLeaksIntoTenantProfile(_FixtureBase):
    """buyer-text-leaks-into-tenant-profile (c4rm08-sengkang-loan-salary-handreply-
    incomplete): the tenant profile extractor ran over a buyer's financing question and
    wrote no_of_pax: 1 into rec['profile'] from 'wanted to check if 6k salary alone
    enough or need to combine with spouse income' -- junk data on a purchase record."""

    def test_buyer_message_never_populates_the_tenant_profile_dict(self):
        s = {"version": 1, "conversations": {}}
        jid = "6590008001@s.whatsapp.net"
        E.handle_event(s, self._ev(jid, "m0",
            "Hi Winfred Quek,\nI am interested in:\nSALE - Test Street 1 Fake\n"
            "4 Room / S$ 650,000", listing_key="d-sale-plain"))
        pn = E.resolve_pn(jid)
        E.handle_event(s, self._ev(jid, "m1",
            "ok, also wanted to check if 6k salary alone enough or need to combine "
            "with spouse income"))
        rec = s["conversations"][pn]
        self.assertEqual(rec.get("profile"), {},
                          "buyer inbound must never populate the tenant profile dict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
