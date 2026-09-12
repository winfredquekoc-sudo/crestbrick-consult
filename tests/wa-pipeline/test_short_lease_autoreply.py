"""
test_short_lease_autoreply.py -- unittest coverage for the short lease auto reply (Feature 2,
Winfred 8 Sep 2026): "send an automated reply if people are requesting to lease for 6 months
or lesser, stating that landlord prefers 1 year lease."

Run: /usr/bin/python3 tests/wa-pipeline/test_short_lease_autoreply.py
"""
import sys, os, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E

LEASE_TEXT = "Just to share, the landlord prefers a minimum 1 year lease \U0001F64F Would that work for you?"

POSITIVE_PHRASES = [
    "6 months", "3 mth", "half a year", "6month lease", "for 4 months", "short term",
    "short lease", "few months", "temporary", "1 month", "2 months", "5 months", "6 mos",
    "3mo", "4 mths", "looking for a 6 month lease", "can it be 3 months only",
    "is 2 months ok", "need it for just 5 months", "half year lease", "3 month contract",
    "need something short term", "just few months stay", "is 6months possible",
    "want a temporary stay only",
]

NEGATIVE_PHRASES = [
    "1 year", "12 months", "6 to 12 months", "at least 6 months", "one year lease",
    "2 years", "13 months", "18 months", "24 months", "at least 1 year", "6 to 24 months",
    "minimum 6 months", "6-12 months", "1 to 2 years", "yes 1 year works", "12 mths",
    "not sure yet", "any duration is fine", "flexible on lease length", "when can I view",
    "is there wifi", "hi", "budget is 600", "room 6", "level 6 unit",
]


class TestPhraseTable(unittest.TestCase):
    def test_at_least_25_positive_phrases_fire(self):
        self.assertGreaterEqual(len(POSITIVE_PHRASES), 25)
        for p in POSITIVE_PHRASES:
            with self.subTest(phrase=p):
                self.assertTrue(E._short_lease_requested(p), f"expected fire on {p!r}")

    def test_at_least_25_negative_phrases_do_not_fire(self):
        self.assertGreaterEqual(len(NEGATIVE_PHRASES), 25)
        for p in NEGATIVE_PHRASES:
            with self.subTest(phrase=p):
                self.assertFalse(E._short_lease_requested(p), f"unexpected fire on {p!r}")

    def test_empty_text_does_not_fire(self):
        self.assertFalse(E._short_lease_requested(""))
        self.assertFalse(E._short_lease_requested(None))


def _state():
    return {"version": 1, "conversations": {}}


class TestUnifiedWording(unittest.TestCase):
    """The note goes out with the SAME wording everywhere, whether triggered by the free
    text scan (bound or not, any stage) or the older qualify()-driven complete-profile path."""

    def test_free_text_trigger_fires_once_bound_and_form_sent(self):
        # B1 (Sep 2026): the note names "the landlord", so it only fires once the record is
        # an established prospect -- form already sent AND a listing bound.
        st = _state()
        jid = "6598880001@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "test-listing"
        ev = {"jid": jid, "msg_id": "1", "text": "hi is a 3 month lease possible",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "LEASE_NOTE")
        self.assertEqual(a["text"], LEASE_TEXT)
        self.assertTrue(rec.get("lease_note_sent"))

    def test_free_text_trigger_never_fires_before_form_sent_or_unbound(self):
        # real incident, 8-9 Sep 2026: pn 6590590183, wandering across 3 properties with no
        # confirmed listing_key, auto-sent a LEASE_NOTE naming a landlord that was never
        # actually confirmed. Must flag Winfred instead, once, and never auto-send.
        st = _state()
        jid = "6598880001b@s.whatsapp.net"
        ev = {"jid": jid, "msg_id": "1", "text": "hi is a 3 month lease possible",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))
        pn = E.resolve_pn(jid)
        rec = st["conversations"][pn]
        self.assertFalse(rec.get("lease_note_sent"))
        self.assertFalse(rec.get("form_sent"))   # Stage 1 never ran for this message
        # a second short lease mention in the same still unbound chat flags only once
        ev2 = {"jid": jid, "msg_id": "2", "text": "can it be 4 months instead",
              "is_from_me": False}
        a2 = E.handle_event(st, ev2)
        self.assertIsNone(a2)

    def test_fires_once_per_chat(self):
        st = _state()
        jid = "6598880002@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "test-listing"
        ev1 = {"jid": jid, "msg_id": "1", "text": "looking for a 4 month lease",
               "is_from_me": False}
        a1 = E.handle_event(st, ev1)
        self.assertEqual(a1["type"], "LEASE_NOTE")
        ev2 = {"jid": jid, "msg_id": "2", "text": "sorry, can't, need something shorter",
               "is_from_me": False}
        a2 = E.handle_event(st, ev2)
        # second reply while the first note is still unresolved, insisting on staying short,
        # is read as a decline -> FLAG_HUMAN, never a second note and never an auto reject
        self.assertEqual(a2["type"], "FLAG_HUMAN")
        self.assertTrue(rec.get("lease_decline_flagged"))


class TestYesNoHandling(unittest.TestCase):
    def _sent_note_state(self, jid="6598880003@s.whatsapp.net"):
        st = _state()
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["lease_note_sent"] = True
        rec["lease_note_min"] = 12
        return st, pn, jid, rec

    def test_no_answer_flags_human_never_auto_rejects(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "no, can't, need it shorter",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertFalse(rec.get("terminal"))     # never auto-rejected/closed
        self.assertTrue(rec.get("lease_decline_flagged"))

    def test_decline_flagged_only_once(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "cannot, need shorter", "is_from_me": False}
        a1 = E.handle_event(st, ev)
        self.assertEqual(a1["type"], "FLAG_HUMAN")
        ev2 = {"jid": jid, "msg_id": "2", "text": "cannot, still need shorter",
               "is_from_me": False}
        a2 = E.handle_event(st, ev2)
        self.assertIsNone(a2)   # stays silent, one flag only

    def test_question_about_minimum_flags_human(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "why must it be 1 year?", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(rec.get("lease_q_flagged"))
        self.assertFalse(rec.get("lease_note_resolved"))

    def test_yes_resolves_and_sets_profile_when_empty(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "yes ok that works", "is_from_me": False}
        E.handle_event(st, ev)
        self.assertTrue(rec.get("lease_note_resolved"))
        self.assertEqual(rec["profile"].get("lease_term_months"), 12)

    def test_explicit_one_year_resolves(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "ok can do 1 year", "is_from_me": False}
        E.handle_event(st, ev)
        self.assertTrue(rec.get("lease_note_resolved"))

    def test_ambiguous_reply_flags_once_never_resolves(self):
        # 9 Sep 2026 fix: an ambiguous reply used to be swallowed forever (silent every time,
        # including a later completed form or photo). It now flags Winfred ONCE with a
        # neutral reason, never resolves the note, and never repeats the flag.
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "let me check my schedule", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])
        self.assertTrue(a["notify"])
        self.assertFalse(rec.get("lease_note_resolved"))
        ev2 = {"jid": jid, "msg_id": "2", "text": "still deciding", "is_from_me": False}
        self.assertIsNone(E.handle_event(st, ev2))   # second ambiguous reply -> silent (latch)

    def test_never_fires_again_once_already_agreed_to_a_year(self):
        st = _state()
        jid = "6598880004@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["profile"]["lease_term_months"] = 12
        ev = {"jid": jid, "msg_id": "1", "text": "by the way is 6 months an option too",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertFalse(a and a.get("type") == "LEASE_NOTE")


class TestQualifyDrivenFallbackUnified(unittest.TestCase):
    """The older, complete-profile qualify() path (a bare numeric 'Lease term: 4' with no
    unit word, caught only once the whole form is in) still fires and uses the SAME
    unified wording, not the old dynamic 'minimum lease of X months' text."""

    def setUp(self):
        self._orig_listing_reqs = E.listing_reqs
        self._orig_master_status = E._master_status
        listing = {
            "listing_key": "test-listing", "landlord_id": None, "status": "open",
            "pg_url_keywords": ["test listing"],
            "requirements": {
                "gender": "any", "couple_ok": False,
                "ethnicity_rule": {"mode": "any", "list": []},
                "nationality_pref": {"mode": "any", "list": []},
                "max_pax": 2, "lease_min_months": 12, "budget_floor": 1000,
            },
        }
        E.listing_reqs = lambda: {"test-listing": listing}
        E._master_status = lambda lk, reqs=None: None

    def tearDown(self):
        E.listing_reqs = self._orig_listing_reqs
        E._master_status = self._orig_master_status

    def test_complete_profile_short_numeric_lease_gets_unified_text(self):
        st = _state()
        jid = "6598880005@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["listing_key"] = "test-listing"
        rec["form_sent"] = True
        rec["profile"] = {
            "name": "Alex", "nationality": "SC", "ethnicity": "Chinese", "gender": "Male",
            "pass_type": "SC", "no_of_pax": 1, "move_in_date": "1 Oct", "budget": 1200,
        }
        ev = {"jid": jid, "msg_id": "1", "text": "Lease term: 4", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "LEASE_NOTE")
        self.assertEqual(a["text"], LEASE_TEXT)


class TestShortLeaseRegexTable(unittest.TestCase):
    """Opus review table, 9 Sep 2026. The four past/deposit phrasings below all fired the
    note wrongly before this pass, and the Chinese ask was silently missed."""

    MUST_NOT_FIRE = ["1 year", "12 months", "6 to 12 months", "at least 6 months",
                     "minimum 6 months", "6 months ago", "moved here 3 months ago",
                     "stayed 6 months at my last place", "6 month deposit",
                     "1 month notice", "2 months advance"]
    MUST_FIRE = ["6 months", "6 mth", "half a year", "3 month lease", "short term",
                 "just 2 months", "1 month only", "\u79df6\u4e2a\u6708", "\u77ed\u79df"]

    def test_must_not_fire(self):
        for phrase in self.MUST_NOT_FIRE:
            with self.subTest(phrase=phrase):
                self.assertFalse(E._short_lease_requested(phrase))

    def test_must_fire(self):
        for phrase in self.MUST_FIRE:
            with self.subTest(phrase=phrase):
                self.assertTrue(E._short_lease_requested(phrase))



if __name__ == "__main__":
    unittest.main(verbosity=2)
