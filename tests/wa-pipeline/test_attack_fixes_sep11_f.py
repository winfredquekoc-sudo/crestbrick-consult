"""
test_attack_fixes_sep11_f.py -- fix package F (remaining gaps c2mix04 / c4rm04), 11 Sep 2026.

  - c2mix04 (scratchpad/attack/flow/cycle2/*mix04*.json): the owner/agent disclosure
    ("I am the agent helping the landlord with this unit") had no Chinese counterpart -- a
    zh-phrased or zh-record version of "are you the owner or an agent?" fell through with no
    reply. Fixed with _OWNER_AGENT_DISCLOSURE_ZH / _FACT_OWNER_AGENT_RE_ZH in
    intake_engine.py, registered with BOT_SIGNATURES / _ENGINE_PREFIXES / wa_intake_echo.
    _OUTBOUND_ONLY. Verified end to end against a zh companion scenario via the harness
    (scratchpad/attack/flow/cycle2/c2mix04-zh-verify-bishan-agent-or-owner.json).

  - c4rm04 (scratchpad/attack/flow/cycle4/*rm04*.json): a buyer (sale) enquiry naming no
    listing got total silence to the prospect (only an internal FLAG_HUMAN). Fixed: one
    factual "which unit?" ask (EN or ZH per the record's lang), sent at most once per
    prospect via the existing buyer_unbound_flagged latch, plus a dedicated Telegram line
    "Buyer enquiry, no listing bound: <pn> <first 120 chars>" through notify_winfred.
    Verified end to end via the harness against the real cycle4 fixture.

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_fixes_sep11_f.py
"""
import sys, os, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

import wa_intake_paths as PATHS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
import wa_intake_notify as NOTIFY
import wa_intake_echo as ECHO
from test_attack_hardening_sep9_p2 import _listing, _ReqsFixtureMixin

# setUpModule/tearDownModule (not bare module-level os.environ[...] = ...) so this file's
# sandbox env vars never leak into a later test_*.py file in the same `unittest discover`
# process -- see test_attack_fixes_sep11_e.py's header comment for the full incident this
# guards against (a leaked WA_INTAKE_SANDBOX/STATE_ROOT/MSG_DB broke 30+ unrelated tests).
_SANDBOX_TMP = None
_SAVED_ENV = {}
_MODULE_PATCHES = []

def setUpModule():
    global _SANDBOX_TMP
    _SANDBOX_TMP = tempfile.mkdtemp(prefix="wa-attack-f-")
    for k in ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_MSG_DB",
              "WA_INTAKE_NO_TELEGRAM", "WA_INTAKE_NO_SEND"):
        _SAVED_ENV[k] = os.environ.get(k)
    os.environ["WA_INTAKE_SANDBOX"] = "1"
    os.environ["WA_INTAKE_STATE_ROOT"] = _SANDBOX_TMP
    os.environ["WA_INTAKE_MSG_DB"] = _SANDBOX_TMP
    os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
    os.environ["WA_INTAKE_NO_SEND"] = "1"
    PATHS.sandbox_init()
    for target, attr, value in (
            (E, "_contact_names", lambda pn: ([], True)),
            (E, "_landlord_pn_set", lambda: frozenset()),
            (E, "_landlord_form_recipients", lambda: frozenset()),
            (E, "_cobroke_agent_pn_set", lambda: frozenset())):
        p = mock.patch.object(target, attr, value)
        p.start()
        _MODULE_PATCHES.append(p)

def tearDownModule():
    for p in _MODULE_PATCHES:
        p.stop()
    _MODULE_PATCHES.clear()
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _SAVED_ENV.clear()


# ===========================================================================================
# Remaining gap c2mix04: Chinese owner/agent disclosure
# ===========================================================================================
class TestOwnerAgentDisclosureChinese(unittest.TestCase):
    def test_zh_phrased_question_gets_zh_disclosure_regardless_of_lang(self):
        self.assertEqual(E._tenant_fact_answer("你是房东还是中介?", {}, "en"),
                         E._OWNER_AGENT_DISCLOSURE_ZH)

    def test_zh_record_gets_zh_disclosure_even_for_an_english_question(self):
        self.assertEqual(
            E._tenant_fact_answer("so u own this room at Bishan or agent?", {}, "zh"),
            E._OWNER_AGENT_DISCLOSURE_ZH)

    def test_en_record_en_question_unchanged(self):
        self.assertEqual(
            E._tenant_fact_answer("so u own this room at Bishan or agent?", {}, "en"),
            E._OWNER_AGENT_DISCLOSURE)

    def test_various_zh_phrasings_all_match(self):
        for q in ("你自己是房东吗", "房东还是中介", "中介还是房东呀", "这房间是你的吗"):
            with self.subTest(q=q):
                self.assertTrue(E._FACT_OWNER_AGENT_RE_ZH.search(q), q)

    def test_registered_with_bot_signatures_and_engine_prefixes(self):
        self.assertTrue(E.is_bot_message(E._OWNER_AGENT_DISCLOSURE_ZH))
        self.assertTrue(E.is_engine_outbound(E._OWNER_AGENT_DISCLOSURE_ZH))

    def test_registered_with_echo_outbound_only(self):
        self.assertTrue(ECHO._is_our_echo(E._OWNER_AGENT_DISCLOSURE_ZH))

    def test_end_to_end_zh_record_routes_to_zh_disclosure(self):
        """Wiring test through the real handle_event: a zh record (first inbound carried CJK)
        that later asks the owner/agent question in English still gets the zh answer."""
        with mock.patch.object(E, "listing_reqs", lambda: {"lk-zh": _listing("lk-zh")}), \
                mock.patch.object(E, "_master_status", lambda lk, reqs=None: None):
            st = {"version": 1, "conversations": {}}
            pn = E.resolve_pn("6598887002@s.whatsapp.net")
            rec = E._rec(st, pn)
            rec.update(listing_key="lk-zh", form_sent=True, lang="zh")
            ev = {"jid": "6598887002@s.whatsapp.net", "msg_id": "z1",
                  "text": "are you the owner or agent?", "is_from_me": False}
            a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "ANSWER_QUESTION")
        self.assertEqual(a["text"], E._OWNER_AGENT_DISCLOSURE_ZH)


# ===========================================================================================
# Remaining gap c4rm04: unbound buyer enquiry no longer silent
# ===========================================================================================
class TestUnboundBuyerEnquiryAsksWhichUnit(_ReqsFixtureMixin, unittest.TestCase):
    def _buyer_ev(self, text, jid="6598887003@s.whatsapp.net"):
        st = {"version": 1, "conversations": {}}
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        ev = {"jid": jid, "msg_id": "b1", "text": text, "is_from_me": False}
        return st, pn, jid, rec, ev

    def test_first_unbound_buyer_enquiry_gets_one_ask_en(self):
        st, pn, jid, rec, ev = self._buyer_ev(
            "want to buy for own stay, resale one can send more details or not")
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a.get("notify"))
        self.assertTrue(a.get("buyer_unbound"))
        self.assertEqual(a["text"], E._BUYER_UNBOUND_ASK_EN)
        self.assertTrue(rec.get("buyer_unbound_flagged"))

    def test_zh_record_gets_zh_ask(self):
        # isolates the "record lang" dimension from "message classifies as sale" -- the
        # enquiry text itself stays a plain English buy signal (classify_intent has no
        # Chinese buy keywords), only the record's stamped lang is zh.
        st, pn, jid, rec, ev = self._buyer_ev(
            "want to buy for own stay, resale one can send more details or not",
            jid="6598887004@s.whatsapp.net")
        rec["lang"] = "zh"
        a = E.handle_event(st, ev)
        self.assertEqual(a["text"], E._BUYER_UNBOUND_ASK_ZH)

    def test_sent_at_most_once_per_prospect(self):
        st, pn, jid, rec, ev = self._buyer_ev(
            "want to buy for own stay, resale one can send more details or not")
        a1 = E.handle_event(st, ev)
        self.assertIsNotNone(a1)
        self.assertTrue(a1.get("buyer_unbound"))
        # SAME buyer-enquiry content again (same classify_intent -> sale, still no listing
        # bound) is what the buyer_unbound_flagged latch actually gates -- a differently
        # worded follow up can take an entirely different classify() path, which is not
        # what this latch is about.
        ev2 = dict(ev, msg_id="b2")
        a2 = E.handle_event(st, ev2)
        self.assertIsNone(a2)

    def test_notify_for_action_sends_dedicated_telegram_line(self):
        sent = []
        with mock.patch.object(NOTIFY, "notify_winfred", sent.append):
            a = {"type": "FLAG_HUMAN", "pn": "6598887005", "notify": True,
                 "buyer_unbound": True,
                 "enquiry_text": "want to buy for own stay, resale one can send more details "
                                 "or not, this text is deliberately over one hundred and "
                                 "twenty characters long so the truncation itself is provable",
                 "text": E._BUYER_UNBOUND_ASK_EN, "reason": "buyer enquiry with no listing"}
            NOTIFY.notify_for_action(a, {"conversations": {"6598887005": {}}})
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("Buyer enquiry, no listing bound: 6598887005 "))
        # first 120 chars of enquiry_text only, never the full text
        self.assertLessEqual(len(sent[0]) - len("Buyer enquiry, no listing bound: 6598887005 "), 120)

    def test_registered_with_bot_signatures_engine_prefixes_and_echo(self):
        self.assertTrue(E.is_bot_message(E._BUYER_UNBOUND_ASK_EN))
        self.assertTrue(E.is_bot_message(E._BUYER_UNBOUND_ASK_ZH))
        self.assertTrue(E.is_engine_outbound(E._BUYER_UNBOUND_ASK_EN))
        self.assertTrue(E.is_engine_outbound(E._BUYER_UNBOUND_ASK_ZH))
        self.assertTrue(ECHO._is_our_echo(E._BUYER_UNBOUND_ASK_EN))
        self.assertTrue(ECHO._is_our_echo(E._BUYER_UNBOUND_ASK_ZH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
