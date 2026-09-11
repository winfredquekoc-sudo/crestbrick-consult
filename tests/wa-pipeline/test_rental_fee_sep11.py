"""
test_rental_fee_sep11.py -- unittest coverage for Winfred's 11 Sep 2026 rule: a RENTAL
tenant's plain agent fee question gets one true, boring fact answer (one month commission
per year of lease); a haggle on that same fee, or any fee question on a BUYER record, stays
silent and flagged -- never answered, never negotiated.

Run: /usr/bin/python3 tests/wa-pipeline/test_rental_fee_sep11.py
"""
import sys, os, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
import wa_intake_echo as ECHO
import wa_money_gate as MG


def _listing(lk, status="open", budget_floor=700, lease_min_months=12):
    return {
        "listing_key": lk, "landlord_id": None, "status": status, "deal_type": "rent",
        "pg_url_keywords": [lk.replace("-", " ")],
        "block_address": "Fake St 11 #05-04 Singapore 123456",
        "requirements": {
            "gender": "any", "couple_ok": True, "couple_must_be_married": False,
            "ethnicity_rule": {"mode": "any", "list": []},
            "nationality_pref": {"mode": "any", "list": []},
            "pass_type_allowed": [], "occupation_rule": {"mode": "any", "list": []},
            "max_pax": 2, "lease_min_months": lease_min_months, "lease_max_months": None,
            "budget_floor": budget_floor, "min_age": None, "cooking": "light",
            "pets_tenant_may_bring": False, "smoking": "no",
        },
        "facts": {},
    }


class _ReqsFixtureMixin:
    """Same pattern as test_attack_fixes_sep11_a.py / test_review_fixes_sep11.py -- swaps
    E.listing_reqs / E._master_status for test control, never reads the real landlord DB."""

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


_FEE_EN = ("Just to share, the agent fee for rental is one month commission for every year "
           "of lease \U0001F642")
_FEE_ZH = "跟您分享一下，租房的中介费是每一年租期收一个月佣金 \U0001F642"


# ---------------------------------------------------------------------------
# wa_money_gate.fee_question_kind: unit coverage for the plain/haggle split itself.
# ---------------------------------------------------------------------------
class TestFeeQuestionKind(unittest.TestCase):
    def test_plain_fee_questions(self):
        for t in ("what's your agent fee?", "how much is the agency fee",
                  "any fee to pay?", "need to pay you anything?", "got charge ah?",
                  "what commission do you take"):
            self.assertEqual(MG.fee_question_kind(t), "plain", t)

    def test_haggle_fee_questions(self):
        for t in ("can you waive the agent fee", "agent fee can discount anot",
                  "whats your agent fee if I book directly", "can split the commission",
                  "no need to pay agent fee right"):
            self.assertEqual(MG.fee_question_kind(t), "haggle", t)

    def test_no_fee_content_returns_none(self):
        for t in ("is the rent negotiable", "can cook here", "", None):
            self.assertIsNone(MG.fee_question_kind(t))

    def test_price_trigger_re_still_catches_fee_for_everyone_else(self):
        self.assertTrue(MG.PRICE_TRIGGER_RE.search("what's your agent fee?"))
        self.assertTrue(MG.core_stays_human("what's your commission?"))


# ---------------------------------------------------------------------------
# plain rental fee -> text + notify (once per prospect)
# ---------------------------------------------------------------------------
class TestPlainRentalFeeAnswered(_ReqsFixtureMixin, unittest.TestCase):
    def test_plain_fee_question_gets_the_fact_and_notifies(self):
        self._reqs["rm-fee"] = _listing("rm-fee")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="rm-fee", form_sent=True, profile={})
        a = E.handle_event(st, self._ev(jid, "hi what's your agent fee?"))
        self.assertEqual(a["type"], "ANSWER_QUESTION")
        self.assertEqual(a["text"], _FEE_EN)
        self.assertTrue(a.get("notify"))
        self.assertIn("rental agent fee stated", a.get("reason", ""))
        self.assertTrue(rec.get("fee_answered"))

    def test_second_ask_never_repeats_the_fee_text(self):
        self._reqs["rm-fee2"] = _listing("rm-fee2")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="rm-fee2", form_sent=True, profile={})
        a1 = E.handle_event(st, self._ev(jid, "whats your agent fee", msg_id="m1"))
        self.assertEqual(a1["text"], _FEE_EN)
        a2 = E.handle_event(st, self._ev(jid, "so how much is the agent fee again",
                                          msg_id="m2"))
        self.assertNotEqual(a2.get("text"), _FEE_EN)
        self.assertIsNone(a2.get("text"))


# ---------------------------------------------------------------------------
# ZH record gets the ZH fact text
# ---------------------------------------------------------------------------
class TestZhRecordGetsZhFeeText(_ReqsFixtureMixin, unittest.TestCase):
    def test_zh_record_gets_zh_text(self):
        self._reqs["rm-fee-zh"] = _listing("rm-fee-zh")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="rm-fee-zh", form_sent=True, profile={}, lang="zh")
        a = E.handle_event(st, self._ev(jid, "whats your agent fee?"))
        self.assertEqual(a["text"], _FEE_ZH)
        self.assertTrue(a.get("notify"))


# ---------------------------------------------------------------------------
# haggle on the fee -> silent flag, never answered
# ---------------------------------------------------------------------------
class TestFeeHaggleStaysSilent(_ReqsFixtureMixin, unittest.TestCase):
    def test_waive_agent_fee_flags_silently(self):
        self._reqs["rm-haggle1"] = _listing("rm-haggle1")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="rm-haggle1", form_sent=True, profile={})
        a = E.handle_event(st, self._ev(jid, "can you waive the agent fee for me"))
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))
        self.assertIn("rental agent fee haggle", a.get("reason", ""))
        self.assertFalse(rec.get("fee_answered"))

    def test_book_directly_bypass_flags_silently(self):
        self._reqs["rm-haggle2"] = _listing("rm-haggle2")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="rm-haggle2", form_sent=True, profile={})
        a = E.handle_event(st, self._ev(jid, "whats your agent fee if I book directly"))
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))
        self.assertIn("rental agent fee haggle", a.get("reason", ""))


# ---------------------------------------------------------------------------
# buyer (sale) record -- fee question always silent + flagged, never answered.
# ABSD/stamp duty on the same buyer record is the 11 Sep 2026 second-rule regression guard.
# ---------------------------------------------------------------------------
class TestBuyerFeeAndAdviceStaySilent(_ReqsFixtureMixin, unittest.TestCase):
    def _buyer_rec(self, jid):
        return self._state_with_rec(
            jid, buyer_form_sent=True, buyer_complete=True,
            buyer={"budget": "1.5m", "timeline": "3 months", "financing": "IPA",
                   "area_or_type": "D15 condo"})

    def test_buyer_fee_question_is_silent_and_flagged(self):
        st, pn, jid, rec = self._buyer_rec("6598887002@s.whatsapp.net")
        a = E.handle_event(st, self._ev(jid, "whats your agent fee for this purchase?"))
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))
        self.assertFalse(rec.get("fee_answered"))

    def test_buyer_absd_stamp_duty_question_is_silent_and_flagged(self):
        st, pn, jid, rec = self._buyer_rec("6598887003@s.whatsapp.net")
        a = E.handle_event(st, self._ev(jid, "how much ABSD and stamp duty will I pay?"))
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))


# ---------------------------------------------------------------------------
# echo matcher: both texts must be recognised as the engine's own outbound.
# ---------------------------------------------------------------------------
class TestEchoRecognisesBothFeeTexts(unittest.TestCase):
    def test_en_text_is_engine_outbound(self):
        self.assertTrue(E.is_engine_outbound(_FEE_EN))
        self.assertTrue(E.is_bot_message(_FEE_EN))
        self.assertTrue(ECHO._is_our_echo(_FEE_EN))

    def test_zh_text_is_engine_outbound(self):
        self.assertTrue(E.is_engine_outbound(_FEE_ZH))
        self.assertTrue(E.is_bot_message(_FEE_ZH))
        self.assertTrue(ECHO._is_our_echo(_FEE_ZH))


# ---------------------------------------------------------------------------
# c3rm02 replay (scratchpad/attack/flow/cycle3/c3rm02-agent-fee-one-month-unbound.json):
# message 0 is unbound (no listing named or bound yet) and must still get the fee line +
# form; message 2 ("...if I book directly") must stay silent and flagged.
# ---------------------------------------------------------------------------
class TestC3rm02Replay(_ReqsFixtureMixin, unittest.TestCase):
    def test_message0_unbound_gets_fee_line_plus_form(self):
        jid = "7080906331@lid"
        st = {"version": 1, "conversations": {}}
        ev = self._ev(jid, "Agent fee how much lah? I want to rent this house on "
                            "propertyguru for only a month")
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "SEND_FORM")
        self.assertEqual(a["texts"][0], _FEE_EN)
        self.assertTrue(a.get("notify"))
        self.assertIn("rental agent fee stated", a.get("reason", ""))

    def test_message2_book_directly_stays_silent_and_flagged(self):
        jid = "7080906331@lid"
        st = {"version": 1, "conversations": {}}
        E.handle_event(st, self._ev(
            jid, "Agent fee how much lah? I want to rent this house on propertyguru for "
                 "only a month", msg_id="m0"))
        E.handle_event(st, self._ev(
            jid, "Can it be month to month? Need something short term only", msg_id="m1"))
        a = E.handle_event(st, self._ev(
            jid, "Also whats your agent fee if I book directly", msg_id="m2"))
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))


if __name__ == "__main__":
    unittest.main()
