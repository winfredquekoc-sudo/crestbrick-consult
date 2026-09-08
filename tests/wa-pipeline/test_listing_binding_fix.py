"""
test_listing_binding_fix.py -- unittest coverage for the listing-binding fix (Sep 2026):

  B(1) outbound rows (Winfred's hand text AND automation acks) now feed match_listing()
       and bind rec["listing_key"] the same way an inbound enquiry does.
  B(2) an unbound-but-complete profile is screened against every OPEN listing instead of
       dead-ending on "listing not bound": exactly one confident, text-mentioned match
       binds and continues; anything else is a FLAG_HUMAN with the candidate keys (or "no
       open listing fits") and a profile summary, latched per distinct match signature.
  B(3) _copilot_verdict returns a COPILOT_VERDICT with verdict "UNBOUND" for a complete
       profile under manual takeover with no listing bound, latched the same way.

Run: /usr/bin/python3 -m unittest tests.wa-pipeline.test_listing_binding_fix -v
(or directly: /usr/bin/python3 tests/wa-pipeline/test_listing_binding_fix.py)
"""
import sys, os, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
import intake_engine as E

COMPLETE_PROFILE = {
    "name": "Alex Tan", "nationality": "SC", "ethnicity": "Chinese", "gender": "Male",
    "age": 28, "pass_type": "SC", "no_of_pax": 1, "move_in_date": "1 Oct",
    "lease_term_months": 12, "budget": 1300,
}

def _listing(lk, status="open", budget_floor=1200, gender="any"):
    return {
        "listing_key": lk, "landlord_id": None, "status": status,
        "pg_url_keywords": [lk.replace("-", " ")],
        "requirements": {
            "gender": gender, "couple_ok": False,
            "ethnicity_rule": {"mode": "any", "list": []},
            "nationality_pref": {"mode": "any", "list": []},
            "max_pax": 2, "lease_min_months": 12, "budget_floor": budget_floor,
        },
    }


class _ReqsFixtureMixin:
    """Swaps E.listing_reqs for a mutable dict under test control, restored on tearDown --
    same pattern test_intake_engine.py / test_rental_policy_locks.py already use."""

    def setUp(self):
        self._orig_listing_reqs = E.listing_reqs
        self._orig_master_status = E._master_status
        self._reqs = {}
        E.listing_reqs = lambda: dict(self._reqs)
        E._master_status = lambda lk, reqs=None: None   # no landlord_id on test fixtures

    def tearDown(self):
        E.listing_reqs = self._orig_listing_reqs
        E._master_status = self._orig_master_status

    def _state_with_rec(self, jid="6598887777@s.whatsapp.net", **rec_fields):
        st = {"version": 1, "conversations": {}}
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec.update(rec_fields)
        return st, pn, jid, rec


class TestOutboundBinding(_ReqsFixtureMixin, unittest.TestCase):
    """B(1): an is_from_me row (hand reply OR automation ack) binds listing_key."""

    def test_hand_reply_binds_listing_key(self):
        st, pn, jid, rec = self._state_with_rec()
        self.assertIsNone(rec.get("listing_key"))
        ev = {"jid": jid, "msg_id": "o1", "text": "yes still available at 47 marine crescent",
              "is_from_me": True, "engine": False, "listing_key": "marine-crescent"}
        a = E.handle_event(st, ev)
        self.assertIsNone(a)   # a hand reply is never a prospect-facing action
        self.assertEqual(rec.get("listing_key"), "marine-crescent")
        self.assertTrue(rec.get("manual_takeover"))   # still latches takeover as before

    def test_automation_ack_binds_listing_key_without_takeover(self):
        st, pn, jid, rec = self._state_with_rec()
        ev = {"jid": jid, "msg_id": "o2",
              "text": "Hi, I'm Winfred Quek. I received your enquiry from 93 Paya Lebar Way",
              "is_from_me": True, "engine": True, "listing_key": "paya-lebar-way"}
        E.handle_event(st, ev)
        self.assertEqual(rec.get("listing_key"), "paya-lebar-way")
        self.assertFalse(rec.get("manual_takeover"))   # engine-tagged outbound is not a hand reply

    def test_outbound_binding_never_overwrites_existing(self):
        st, pn, jid, rec = self._state_with_rec(listing_key="already-bound")
        ev = {"jid": jid, "msg_id": "o3", "text": "some other listing", "is_from_me": True,
              "engine": True, "listing_key": "different-listing"}
        E.handle_event(st, ev)
        self.assertEqual(rec.get("listing_key"), "already-bound")

    def test_runner_computes_listing_key_unconditionally(self):
        """Source check on the actual fix: match_listing must no longer be gated behind
        'if not ifm' in either the per-row loop or the pre-pass."""
        src = open(os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_runner.py")).read()
        loop = src[src.index("reqs_tick = E.listing_reqs()"):src.index("a = E.handle_event(state, ev)")]
        self.assertNotIn('if not ifm:\n                ev["listing_key"]', loop)
        self.assertIn('ev["listing_key"] = match_listing(content, reqs_tick)', loop)
        pre_pass = loop[:loop.index("for rowid, rid, jid, ifm")]
        self.assertIn("match_listing(_content, reqs_tick)", pre_pass)


class TestUnboundCompleteProfile(_ReqsFixtureMixin, unittest.TestCase):
    """B(2): the STAGE 2 dead end on an unbound complete profile."""

    def _rec_ready_for_stage2(self, st, jid="6598887000@s.whatsapp.net"):
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["profile"] = dict(COMPLETE_PROFILE)
        rec["last_inbound"] = "any update on marine crescent?"
        return pn, rec

    def test_single_confident_match_binds_and_continues(self):
        self._reqs["marine-crescent"] = _listing("marine-crescent")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._rec_ready_for_stage2(st)
        ev = {"jid": "6598887000@s.whatsapp.net", "msg_id": "m1",
              "text": "any update on marine crescent?", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(rec.get("listing_key"), "marine-crescent")
        self.assertIsNotNone(a)
        self.assertNotEqual(a.get("type"), "FLAG_HUMAN")

    def test_zero_matches_flags_no_fit(self):
        self._reqs["oxley-edge"] = _listing("oxley-edge", gender="female_only")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._rec_ready_for_stage2(st)
        rec["profile"]["gender"] = "Male"   # disqualified from the only open listing
        ev = {"jid": "6598887000@s.whatsapp.net", "msg_id": "m1", "text": "any update?",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a.get("type"), "FLAG_HUMAN")
        self.assertIsNone(rec.get("listing_key"))
        self.assertIn("no open listing fits", a.get("reason", ""))
        self.assertIn("profile:", a.get("reason", ""))

    def test_multiple_matches_flags_with_candidate_list(self):
        self._reqs["listing-a"] = _listing("listing-a")
        self._reqs["listing-b"] = _listing("listing-b")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._rec_ready_for_stage2(st)
        rec["last_inbound"] = "no specific address mentioned"
        ev = {"jid": "6598887000@s.whatsapp.net", "msg_id": "m1", "text": "any update?",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a.get("type"), "FLAG_HUMAN")
        self.assertIsNone(rec.get("listing_key"))
        self.assertIn("listing-a", a.get("reason", ""))
        self.assertIn("listing-b", a.get("reason", ""))
        self.assertEqual(sorted(a.get("hot_matches") or []), ["listing-a", "listing-b"])

    def test_single_match_without_text_mention_flags_instead_of_guessing(self):
        # QUALIFIES for exactly one listing, but nothing anyone said named it -- must NOT
        # silently bind off qualify() alone.
        self._reqs["quiet-listing"] = _listing("quiet-listing")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._rec_ready_for_stage2(st)
        rec["last_inbound"] = "any update?"
        ev = {"jid": "6598887000@s.whatsapp.net", "msg_id": "m1", "text": "any update?",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a.get("type"), "FLAG_HUMAN")
        self.assertIsNone(rec.get("listing_key"))

    def test_flag_latches_once_then_refires_on_new_signature(self):
        self._reqs["listing-a"] = _listing("listing-a")
        self._reqs["listing-b"] = _listing("listing-b")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._rec_ready_for_stage2(st)
        rec["last_inbound"] = "no specific address mentioned"
        ev = {"jid": "6598887000@s.whatsapp.net", "msg_id": "m1", "text": "any update?",
              "is_from_me": False}
        a1 = E.handle_event(st, ev)
        self.assertEqual(a1.get("type"), "FLAG_HUMAN")
        ev2 = dict(ev, msg_id="m2")
        a2 = E.handle_event(st, ev2)
        self.assertIsNone(a2)   # same signature -> silent
        # a THIRD listing opens and now also matches -> signature changes -> re-fires
        self._reqs["listing-c"] = _listing("listing-c")
        ev3 = dict(ev, msg_id="m3")
        a3 = E.handle_event(st, ev3)
        self.assertEqual(a3.get("type"), "FLAG_HUMAN")
        self.assertIn("listing-c", a3.get("reason", ""))

    def test_unavailable_listing_excluded_from_matches(self):
        self._reqs["closed-listing"] = _listing("closed-listing", status="closed (tenanted)")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._rec_ready_for_stage2(st)
        ev = {"jid": "6598887000@s.whatsapp.net", "msg_id": "m1", "text": "any update?",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a.get("type"), "FLAG_HUMAN")
        self.assertIn("no open listing fits", a.get("reason", ""))


class TestCopilotUnboundVerdict(_ReqsFixtureMixin, unittest.TestCase):
    """B(3): _copilot_verdict on a complete, unbound profile under manual takeover."""

    def _manual_rec(self, st, jid="6598886000@s.whatsapp.net"):
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["manual_takeover"] = True
        rec["profile"] = dict(COMPLETE_PROFILE)
        return pn, rec

    def test_incomplete_profile_returns_none(self):
        st = {"version": 1, "conversations": {}}
        pn, rec = self._manual_rec(st)
        rec["profile"] = {"name": "Alex"}   # incomplete
        self.assertIsNone(E._copilot_verdict(rec))

    def test_complete_unbound_profile_returns_copilot_unbound(self):
        self._reqs["listing-a"] = _listing("listing-a")
        st = {"version": 1, "conversations": {}}
        pn, rec = self._manual_rec(st)
        a = E._copilot_verdict(rec)
        self.assertIsNotNone(a)
        self.assertEqual(a["type"], "COPILOT_VERDICT")
        self.assertEqual(a["verdict"], "UNBOUND")
        self.assertEqual(a["hot_matches"], ["listing-a"])
        self.assertIn("name=Alex Tan", a["profile_summary"])

    def test_latches_once_then_refires_on_new_match_signature(self):
        st = {"version": 1, "conversations": {}}
        pn, rec = self._manual_rec(st)
        a1 = E._copilot_verdict(rec)
        self.assertEqual(a1["verdict"], "UNBOUND")
        self.assertEqual(a1["hot_matches"], [])
        a2 = E._copilot_verdict(rec)
        self.assertIsNone(a2)   # same signature (no matches) -> silent
        self._reqs["listing-a"] = _listing("listing-a")
        a3 = E._copilot_verdict(rec)
        self.assertIsNotNone(a3)
        self.assertEqual(a3["hot_matches"], ["listing-a"])

    def test_terminal_record_returns_none(self):
        st = {"version": 1, "conversations": {}}
        pn, rec = self._manual_rec(st)
        rec["terminal"] = True
        self.assertIsNone(E._copilot_verdict(rec))


if __name__ == "__main__":
    unittest.main(verbosity=2)
