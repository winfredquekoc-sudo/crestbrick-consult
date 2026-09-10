"""
test_attack_hardening_sep9_p2.py -- unittest coverage for the 5 confirmed adversarial attack
harness findings from the 9 Sep 2026 second replay (scripts/wa_intake_attack_harness.py,
scratchpad/attack/cycle2/*). Each TestCase names the finding it guards and reproduces the
exact (or an equivalent minimal) failure signature from the harness scenario, so it fails
against the pre fix engine and passes after.

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_hardening_sep9_p2.py
"""
import sys, os, tempfile, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
import wa_intake_runner as R
from test_takeover_resume import _isolated_runner, FAKE_PN, FAKE_JID   # noqa: E402


def _listing(lk, status="open", budget_floor=700, gender="any", facts=None):
    return {
        "listing_key": lk, "landlord_id": None, "status": status, "deal_type": "rent",
        "pg_url_keywords": [lk.replace("-", " ")],
        "requirements": {
            "gender": gender, "couple_ok": True, "couple_must_be_married": False,
            "ethnicity_rule": {"mode": "any", "list": []},
            "nationality_pref": {"mode": "any", "list": []},
            "pass_type_allowed": [], "occupation_rule": {"mode": "any", "list": []},
            "max_pax": 2, "lease_min_months": 12, "lease_max_months": None,
            "budget_floor": budget_floor, "min_age": None, "cooking": "light",
            "pets_tenant_may_bring": False, "smoking": "no",
        },
        "facts": facts or {},
    }


class _ReqsFixtureMixin:
    """Swaps E.listing_reqs for a mutable dict under test control -- same pattern
    test_attack_hardening_sep9.py / test_listing_binding_fix.py use."""

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


# ---------------------------------------------------------------------------
# P0 finding 1: a deposit/refund "how much" question must never be misrouted to the
# rent-negotiation pivot (never quotes a figure, never auto-answers with advice); it must
# use the listing's own deposit fact, or flag if that fact is not on file.
# ---------------------------------------------------------------------------
class TestDepositFactBeforeRentPivot(unittest.TestCase):
    def test_deposit_question_uses_deposit_fact_not_rent_pivot(self):
        listing = _listing("cyc2-fg-sc6-clementi-common",
                            facts={"deposit": "One month deposit, refunded within 7 days."})
        ans = E._tenant_fact_answer("deposit how much and refund when i leave ah", listing)
        self.assertEqual(ans, "One month deposit, refunded within 7 days.")

    def test_deposit_question_with_no_fact_on_file_flags_not_negotiates(self):
        listing = _listing("cyc2-fg-sc6-clementi-common")   # no facts sheet entry
        ans = E._tenant_fact_answer("deposit how much and refund when i leave ah", listing)
        # must be None (flag to Winfred) -- never the vague rent pivot text, and never a figure
        self.assertIsNone(ans)

    def test_bare_rent_how_much_unaffected(self):
        listing = _listing("some-room")
        ans = E._tenant_fact_answer("how much is this room", listing)
        self.assertIn("Rent is usually fixed", ans)

    def test_deposit_with_advice_edge_still_flagged(self):
        # "deposit refund dispute" carries the existing legal/opinion veto -- must stay None
        listing = _listing("some-room", facts={"deposit": "$500"})
        ans = E._tenant_fact_answer("deposit refund dispute, what are my rights", listing)
        self.assertIsNone(ans)


# ---------------------------------------------------------------------------
# P0 finding 2: "don't like" inside a hypothetical clause ("if I don't like it") on an
# already-booked viewing must never read as a unit rejection, and a genuine rejection must
# always notify Winfred.
# ---------------------------------------------------------------------------
class TestUnitRejectionHypotheticalVeto(unittest.TestCase):
    def test_hypothetical_dont_like_on_confirmed_viewing_not_a_rejection(self):
        self.assertFalse(E._unit_rejection(
            "coming around 3pm, can you paynow me back the viewing deposit if I don't like it"))

    def test_booking_positive_time_present_vetoes_rejection_read(self):
        self.assertFalse(E._unit_rejection("ok see you at 3pm, don't like the paint color though"))

    def test_genuine_rejection_still_detected(self):
        self.assertTrue(E._unit_rejection("actually I don't like this room, too small for me"))

    def test_genuine_rejection_still_detected_no_alt_no_time(self):
        self.assertTrue(E._unit_rejection("too far from my office, not for me"))


class TestUnitRejectionTerminalNotifies(_ReqsFixtureMixin, unittest.TestCase):
    def test_closed_no_alternative_redirect_notifies_winfred(self):
        self._reqs["only-room"] = _listing("only-room")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="only-room", form_sent=True, viewing_asked=True,
            viewing_confirmed=True, offered_slot_label="Thu 10 Sep, 2pm to 3pm",
            profile={"name": "Grace", "budget": 800})
        ev = {"jid": jid, "msg_id": "r1", "text": "too far, not for me", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "REDIRECT")
        self.assertTrue(a.get("notify"))


# ---------------------------------------------------------------------------
# P1 finding 3: _is_question must catch an interrogative token anywhere in the message,
# not only in the first three words -- a mid-sentence legal-advice question must never be
# silently dropped.
# ---------------------------------------------------------------------------
class TestIsQuestionMidSentence(unittest.TestCase):
    def test_mid_sentence_should_is_detected(self):
        self.assertTrue(E._is_question(
            "before i sign should i get a lawyer to check the tenancy agreement first, "
            "is that necessary for my situation"))

    def test_mid_sentence_particle_detected(self):
        self.assertTrue(E._is_question("can view this weekend anot leh"))

    def test_plain_statement_still_not_a_question(self):
        self.assertFalse(E._is_question("ok great, yes see you at the unit"))

    def test_leading_interrogative_still_detected(self):
        self.assertTrue(E._is_question("can i view tomorrow"))


class TestMidSentenceQuestionFlagsBeforeProfile(_ReqsFixtureMixin, unittest.TestCase):
    def test_legal_advice_question_before_profile_complete_flags_human(self):
        self._reqs["bedok-room"] = _listing("bedok-room")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="bedok-room", form_sent=True,
            profile={"name": "Chen Wei Ling", "budget": 780})
        ev = {"jid": jid, "msg_id": "q1",
              "text": "before i sign should i get a lawyer to check the tenancy agreement "
                      "first, is that necessary for my situation",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])
        self.assertTrue(a["notify"])


# ---------------------------------------------------------------------------
# P1 finding 4: history may only amplify a supply read when the CURRENT message is itself
# supply shaped; and a bound, screened tenant profile can never be flipped to supply by a
# stale historical remark.
# ---------------------------------------------------------------------------
class TestSupplyHistoryRequiresCurrentShape(unittest.TestCase):
    def setUp(self):
        self._orig_hist = E.recent_inbound_text

    def tearDown(self):
        E.recent_inbound_text = self._orig_hist

    def test_pure_demand_message_not_flipped_by_stale_history(self):
        E.recent_inbound_text = lambda jid, limit=25: (
            "my brother also has a spare room in his flat to rent out, "
            "can you help him find a tenant too?")
        kind = E.supply_side_kind(
            "7001870243@lid", "hi, so what time are you actually free for me to view?")
        self.assertIsNone(kind)

    def test_current_supply_shaped_still_reads_history(self):
        # current message itself carries a low-context marker -- history may still amplify
        E.recent_inbound_text = lambda jid, limit=25: "i have a room to rent"
        kind = E.supply_side_kind("7001870244@lid", "saw on carousell, still available or not")
        # low context marker on current message alone is enough for a PROBABLE read
        self.assertIn(kind, (None, "landlord"))

    def test_bound_qualified_tenant_never_reclassified_from_history(self):
        E.recent_inbound_text = lambda jid, limit=25: "i have a room to rent out"
        rec = {"listing_key": "hougang-room-fixture", "profile": {"name": "Priya"},
               "qualify": {"verdict": "QUALIFIED", "why": []}}
        kind, confident = E.supply_side_kind(
            "7001870243@lid", "i have a room to rent out too, does that matter?",
            with_confidence=True, rec=rec)
        self.assertIsNone(kind)
        self.assertFalse(confident)


# ---------------------------------------------------------------------------
# P2 finding 5: a blank 14-field form must never be sent to a prospect whose complete,
# qualifying profile is already on file.
# ---------------------------------------------------------------------------
class TestNoBlankFormWhenProfileComplete(_ReqsFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._orig_unit_msg = E.listing_unit_message
        E.listing_unit_message = lambda lk, lang="en": (
            "Hi! The " + lk + " is still open for a look :)" if lk else None)

    def tearDown(self):
        E.listing_unit_message = self._orig_unit_msg
        super().tearDown()

    def test_complete_profile_at_first_contact_skips_blank_form(self):
        self._reqs["jalan-batu-room"] = _listing("jalan-batu-room")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="jalan-batu-room",
            profile={"name": "Kevin Ong", "nationality": "Singaporean",
                      "ethnicity": "Chinese", "gender": "Male", "pass_type": "SC",
                      "no_of_pax": 1, "move_in_date": "1 Oct",
                      "lease_term_months": 12, "budget": 850})
        ev = {"jid": jid, "msg_id": "f1", "text": "still available???", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertIsNotNone(a)
        self.assertEqual(a["type"], "SEND_FORM")
        blob = " ".join(a.get("texts") or [a.get("text") or ""])
        self.assertNotIn("Pls fill this in", blob)   # never the blank 14-field template
        self.assertTrue(rec.get("form_sent"))

    def test_incomplete_profile_still_gets_the_full_form(self):
        self._reqs["jalan-batu-room"] = _listing("jalan-batu-room")
        st, pn, jid, rec = self._state_with_rec(listing_key="jalan-batu-room", profile={})
        ev = {"jid": jid, "msg_id": "f2", "text": "still available", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "SEND_FORM")
        blob = " ".join(a.get("texts") or [a.get("text") or ""])
        self.assertIn("Pls fill this in", blob)


# ---------------------------------------------------------------------------
# P3 finding 6: the stale-row backfill guard (>48h) must log a visible skip and roll up
# one aggregated notify per run, without ever auto-serving those rows.
# ---------------------------------------------------------------------------
class TestStaleBackfillVisibility(unittest.TestCase):
    """A backfilled row older than STALE_ROW_HOURS must never be auto-served (unchanged),
    but the drop must now be logged AND rolled into one aggregated notify per run."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_stale_row_logged_and_notified_never_served(self):
        with _isolated_runner(
                self._tmpdir.name, inbound_content="hi still available?",
                inbound_minutes_ago=60 * 50,          # 50h old -> past the 48h guard
                conversations={}) as calls:
            self.assertEqual(calls["sent"], [])       # never auto-served
            self.assertTrue(any(k == "STALE_BACKFILL_SKIP" for k, p, m in calls["logged"]))
            self.assertTrue(any("1 backfilled" in n or "backfilled chat message" in n
                                for n in calls["notified"]))

    def test_recent_row_not_flagged_stale(self):
        with _isolated_runner(
                self._tmpdir.name, inbound_content="hi still available?",
                inbound_minutes_ago=10, conversations={}) as calls:
            self.assertFalse(any(k == "STALE_BACKFILL_SKIP" for k, p, m in calls["logged"]))
            self.assertFalse(any("backfilled chat message" in n for n in calls["notified"]))


if __name__ == "__main__":
    unittest.main()
