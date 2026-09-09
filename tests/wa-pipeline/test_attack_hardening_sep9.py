"""
test_attack_hardening_sep9.py -- unittest coverage for the 14 confirmed adversarial attack
harness findings fixed 9 Sep 2026 (wa_intake_attack_harness.py replay). Each TestCase names
the finding it guards and reproduces the exact (or an equivalent minimal) failure signature
from the harness scenario, so it fails against the pre fix engine and passes after.

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_hardening_sep9.py
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


def _listing(lk, status="open", budget_floor=1000, gender="any", facts=None):
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
    test_listing_binding_fix.py / test_rental_policy_locks.py already use."""

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
# Finding 1: tenant availability enquiry containing a supply keyword ("carousell") must
# never be misrouted into the landlord onboarding flow.
# ---------------------------------------------------------------------------
class TestSupplyKeywordDemandVeto(unittest.TestCase):
    def test_carousell_with_availability_question_is_not_supply(self):
        kind, confident = E.supply_side_kind(
            "7000000001@lid", "bayshore park still there? saw on carousell",
            with_confidence=True)
        self.assertIsNone(kind)

    def test_carousell_alone_stays_probable_never_confident(self):
        # a bare "saw on carousell" with no availability question is still only PROBABLE
        # (never auto sends the supply form on its own).
        kind, confident = E.supply_side_kind(
            "7000000002@lid", "hi saw on carousell", with_confidence=True)
        self.assertEqual(kind, "landlord")
        self.assertFalse(confident)

    def test_first_person_owner_phrase_unaffected_still_confident(self):
        kind, confident = E.supply_side_kind(
            "7000000003@lid", "i have a room to rent", with_confidence=True)
        self.assertEqual(kind, "landlord")
        self.assertTrue(confident)


# ---------------------------------------------------------------------------
# Finding 2: a terminal record must still notify Winfred (never re-send) on a fresh
# enquiry, a mention of another open listing, or a complaint -- once per distinct text.
# ---------------------------------------------------------------------------
class TestTerminalRecordReNotify(_ReqsFixtureMixin, unittest.TestCase):
    def test_fresh_enquiry_on_terminal_record_notifies_once_no_send(self):
        self._reqs["bayshore"] = _listing("bayshore")
        st, pn, jid, rec = self._state_with_rec(
            terminal=True, status="disqualified", listing_key="bayshore", form_sent=True)
        ev = {"jid": jid, "msg_id": "t1", "text": "still available???", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])
        self.assertTrue(a["notify"])
        ev2 = dict(ev, msg_id="t2")
        self.assertIsNone(E.handle_event(st, ev2))   # identical repeat -> silent

    def test_discrimination_accusation_on_terminal_record_flags(self):
        st, pn, jid, rec = self._state_with_rec(terminal=True, status="house_gate:N1")
        ev = {"jid": jid, "msg_id": "t1",
              "text": "why no reply, is it because I'm Indian? that's discrimination you know",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])

    def test_plain_yes_on_terminal_record_stays_fully_silent(self):
        st, pn, jid, rec = self._state_with_rec(terminal=True, status="closed (found elsewhere)")
        ev = {"jid": jid, "msg_id": "t1", "text": "yes 3pm", "is_from_me": False}
        self.assertIsNone(E.handle_event(st, ev))


# ---------------------------------------------------------------------------
# Finding 3: the "not a clear tenant enquiry" and closed/hold listing FLAG_HUMAN returns
# must carry notify=True, latched once per record.
# ---------------------------------------------------------------------------
class TestFlagHumanNotifyLatch(_ReqsFixtureMixin, unittest.TestCase):
    def test_not_a_clear_enquiry_notifies_once(self):
        st, pn, jid, rec = self._state_with_rec()
        ev = {"jid": jid, "msg_id": "n1", "text": "ok thanks bye", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a["notify"])
        ev2 = {"jid": jid, "msg_id": "n2", "text": "just checking in", "is_from_me": False}
        a2 = E.handle_event(st, ev2)
        self.assertFalse(a2["notify"])   # latched: no repeat ping to Winfred

    def test_closed_listing_enquiry_notifies_once(self):
        self._reqs["closed-room"] = _listing("closed-room", status="closed (tenanted)")
        st, pn, jid, rec = self._state_with_rec()
        ev = {"jid": jid, "msg_id": "c1", "text": "still available?", "is_from_me": False,
              "listing_key": "closed-room"}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a["notify"])
        self.assertTrue(rec.get("status", "").startswith("listing_closed"))
        ev2 = {"jid": jid, "msg_id": "c2", "text": "still there???", "is_from_me": False}
        a2 = E.handle_event(st, ev2)
        self.assertFalse(a2["notify"])   # latched: no repeat ping to Winfred


# ---------------------------------------------------------------------------
# Finding 4: any tenant question asked before a viewing is offered must get a reply (a
# known fact) or a flag -- never silence -- with a one fact per prospect latch.
# ---------------------------------------------------------------------------
class TestPreViewingQuestionCheck(_ReqsFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._reqs["facts-room"] = _listing(
            "facts-room", facts={"mrt": "5 minutes walk to Woodlands MRT."})

    def test_fact_on_file_answered_once(self):
        st, pn, jid, rec = self._state_with_rec(
            listing_key="facts-room", form_sent=True, profile={})
        ev = {"jid": jid, "msg_id": "q1", "text": "how far MRT from here", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "ANSWER_QUESTION")
        self.assertEqual(a["text"], "5 minutes walk to Woodlands MRT.")
        self.assertTrue(rec.get("fact_answered"))

    def test_second_question_after_one_fact_answer_flags_human(self):
        st, pn, jid, rec = self._state_with_rec(
            listing_key="facts-room", form_sent=True, profile={}, fact_answered=True)
        ev = {"jid": jid, "msg_id": "q2", "text": "can cook curry ah", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])
        self.assertTrue(a["notify"])

    def test_legal_advice_question_flags_never_silent(self):
        st, pn, jid, rec = self._state_with_rec(
            listing_key="facts-room", form_sent=True, profile={})
        ev = {"jid": jid, "msg_id": "q3",
              "text": "is it legal for landlord to break the lease early",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])

    def test_incidental_single_field_match_does_not_suppress_the_question(self):
        # "alone" trips the free text solo pax signal (no_of_pax=1) -- a single incidental
        # field must never let a genuine safety question go unanswered and unflagged.
        st, pn, jid, rec = self._state_with_rec(
            listing_key="facts-room", form_sent=True, profile={}, fact_answered=True)
        ev = {"jid": jid, "msg_id": "q4",
              "text": "is this area safe at night for a girl to walk alone",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")

    def test_completed_form_reply_is_not_intercepted_as_a_question(self):
        st, pn, jid, rec = self._state_with_rec(
            listing_key="facts-room", form_sent=True, profile={})
        ev = {"jid": jid, "msg_id": "q5", "text": (
            "Name: Nur Aisyah\nNationality: Indonesian\nEthnicity: Malay\nGender: Female\n"
            "Pass type: Work Permit\nOccupation: cleaner\nEmployment type: fixed term\n"
            "No. of pax: 1\nMove in date: next week\nLease term: 12\nBudget: 1200"),
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertNotEqual((a or {}).get("type"), "FLAG_HUMAN")
        self.assertEqual(rec["profile"].get("name"), "Nur Aisyah")


# ---------------------------------------------------------------------------
# Finding 5: the short lease note gate must never swallow a completed form or a photo, and
# must not mislabel a non lease question as a lease question.
# ---------------------------------------------------------------------------
class TestLeaseNotePendingResolution(_ReqsFixtureMixin, unittest.TestCase):
    def test_completed_form_falls_through_not_swallowed(self):
        st, pn, jid, rec = self._state_with_rec(
            form_sent=True, lease_note_sent=True, lease_note_min=12, profile={})
        ev = {"jid": jid, "msg_id": "l1", "text": (
            "Name: Ravi Kumar\nNationality: Indian\nEthnicity: Indian\nGender: Male\n"
            "Pass type: S Pass\nOccupation: warehouse supervisor\nEmployment type: permanent\n"
            "No. of pax: 1\nMove in date: next week\nLease term: 3\nBudget: 750"),
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertTrue(rec.get("lease_note_resolved"))
        self.assertEqual(rec["profile"].get("name"), "Ravi Kumar")

    def test_media_only_message_falls_through(self):
        st, pn, jid, rec = self._state_with_rec(
            form_sent=True, lease_note_sent=True, lease_note_min=12, profile={})
        ev = {"jid": jid, "msg_id": "l2", "text": "", "media_type": "image", "is_from_me": False}
        E.handle_event(st, ev)
        self.assertTrue(rec.get("lease_note_resolved"))

    def test_non_lease_question_gets_neutral_reason_not_mislabelled(self):
        st, pn, jid, rec = self._state_with_rec(
            form_sent=True, lease_note_sent=True, lease_note_min=12, profile={})
        ev = {"jid": jid, "msg_id": "l3",
              "text": "hello? can hold the room for me still right", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertNotIn("1 year minimum lease", a["reason"])

    def test_ambiguous_reply_flags_once_never_silent_forever(self):
        st, pn, jid, rec = self._state_with_rec(
            form_sent=True, lease_note_sent=True, lease_note_min=12, profile={})
        ev = {"jid": jid, "msg_id": "l4", "text": "let me check my schedule", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a["notify"])
        ev2 = dict(ev, msg_id="l5")
        self.assertIsNone(E.handle_event(st, ev2))


# ---------------------------------------------------------------------------
# Finding 6: grab() must not let ordinary free text permanently poison the profile, and a
# later clean labelled form must correct an earlier free text guess.
# ---------------------------------------------------------------------------
class TestGrabProvenanceOverwrite(_ReqsFixtureMixin, unittest.TestCase):
    def test_form_corrects_earlier_free_text_name_guess(self):
        st, pn, jid, rec = self._state_with_rec(form_sent=True, profile={})
        ev1 = {"jid": jid, "msg_id": "f1", "text": (
            "attaching my passport photo page here as ID, name Alex Tan, hope that helps."),
               "is_from_me": False}
        E.handle_event(st, ev1)
        self.assertNotEqual(rec["profile"].get("name"), "Alex Tan")  # garbled free text guess
        ev2 = {"jid": jid, "msg_id": "f2", "text": (
            "Email: alex.tan88@gmail.com\nName: Alex Tan\nNationality: Singaporean\n"
            "Ethnicity: Chinese\nGender: Male\nAge: 31\nPass type: Citizen\n"
            "Occupation: Software engineer\nEmployment type: Permanent\nNo. of pax: 2\n"
            "Move in date: 1 Oct\nLease term: 12\nBudget: 1400\nLocation: near MRT"),
               "is_from_me": False}
        E.handle_event(st, ev2)
        self.assertEqual(rec["profile"].get("name"), "Alex Tan")   # corrected by the real form

    def test_free_text_never_overwrites_a_form_value(self):
        st, pn, jid, rec = self._state_with_rec(
            form_sent=True, profile={"name": "Alex Tan"},
            profile_provenance={"name": "form"})
        ev = {"jid": jid, "msg_id": "f3", "text": "btw my name is actually someone else",
              "is_from_me": False}
        E.handle_event(st, ev)
        self.assertEqual(rec["profile"].get("name"), "Alex Tan")


# ---------------------------------------------------------------------------
# Finding 7: grab() must stop a value at the next recognised field label (comma or
# newline joined), never store the label continuation word, and strip stray punctuation.
# ---------------------------------------------------------------------------
class TestGrabFieldBoundaries(unittest.TestCase):
    def test_comma_joined_fields_each_get_their_own_value(self):
        p = E.extract_profile("Nationality: Singaporean, Ethnicity: Chinese, Gender: Male")
        self.assertEqual(p.get("nationality"), "Singaporean")
        self.assertEqual(p.get("ethnicity"), "Chinese")
        self.assertEqual(p.get("gender"), "Male")

    def test_label_alone_on_its_own_line_reads_the_next_line_as_value(self):
        p = E.extract_profile("Pass type\nWork Permit")
        self.assertEqual(p.get("pass_type"), "Work Permit")

    def test_blank_form_never_captures_the_label_continuation_word(self):
        p = E.extract_profile(E.INTAKE_FORM)
        self.assertNotIn("pass_type", p)
        self.assertEqual(p, {})

    def test_multi_field_one_liner_does_not_bleed_across_fields(self):
        p = E.extract_profile(
            "Pass type: Citizen, No. of pax: 1, Move in date: 5 Oct, Lease term: 12, Budget: 700")
        self.assertEqual(p.get("pass_type"), "Citizen")
        self.assertEqual(p.get("no_of_pax"), 1)
        self.assertEqual(p.get("move_in_date"), "5 Oct")
        self.assertEqual(p.get("lease_term_months"), 12)
        self.assertEqual(p.get("budget"), 700)

    def test_free_text_still_works_unbroken(self):
        self.assertEqual(E.extract_profile("my name is Ruth").get("name"), "Ruth")
        self.assertEqual(E.extract_profile("name's Ruth").get("name"), "Ruth")


# ---------------------------------------------------------------------------
# Finding 8: Chinese label synonyms must parse, and an unparseable form (5+ labelled lines,
# under 3 fields) must be flagged rather than silently treated as empty.
# ---------------------------------------------------------------------------
class TestChineseLabelsAndSafetyNet(_ReqsFixtureMixin, unittest.TestCase):
    def test_chinese_labels_parse(self):
        p = E.extract_profile(
            "姓名： Chen Wei Ling\n国籍： Malaysian\n"
            "种族： Chinese\n性别： Female\n职业： sales\n"
            "人数： 1\n入住日期： 20 Sep\n租期： 12\n"
            "预算： 700")
        self.assertEqual(p.get("name"), "Chen Wei Ling")
        self.assertEqual(p.get("nationality"), "Malaysian")
        self.assertEqual(p.get("ethnicity"), "Chinese")
        self.assertEqual(p.get("gender"), "Female")
        self.assertEqual(p.get("no_of_pax"), 1)
        self.assertEqual(p.get("lease_term_months"), 12)
        self.assertEqual(p.get("budget"), 700)

    def test_unparseable_form_shaped_message_flags_human(self):
        st, pn, jid, rec = self._state_with_rec(form_sent=True, profile={})
        # 6 recognised label:colon lines, every value left blank -- 0 fields parse.
        ev = {"jid": jid, "msg_id": "u1",
              "text": "姓名：\n国籍：\n种族：\n性别：\n预算：\n职业：",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a["notify"])


# ---------------------------------------------------------------------------
# Finding 9: a bare "confirm" inside an interrogative must never read as consent.
# ---------------------------------------------------------------------------
class TestConfirmInterrogativeGuard(unittest.TestCase):
    def test_bare_confirm_inside_question_is_not_affirmative(self):
        self.assertFalse(E._is_affirmative(
            "please hold ya, i really need this one, confirm can hold?"))

    def test_confirm_opening_with_can_is_not_affirmative(self):
        self.assertFalse(E._is_affirmative("can you confirm my slot is still open"))

    def test_yes_plus_confirm_still_affirmative(self):
        self.assertTrue(E._is_affirmative("yes i confirm i take the room, tell me the address"))

    def test_plain_confirm_statement_still_affirmative(self):
        self.assertTrue(E._is_affirmative("confirmed, see you then"))

    def test_ok_can_unaffected(self):
        self.assertTrue(E._is_affirmative("ok can"))


# ---------------------------------------------------------------------------
# Finding 10: a resent copy of the filled profile form must never be misread as a
# proposed viewing time.
# ---------------------------------------------------------------------------
class TestFormResendNotViewingTime(unittest.TestCase):
    FORM_TEXT = (
        "Email: alex.tan88@gmail.com\nName: Alex Tan\nNationality: Singaporean\n"
        "Ethnicity: Chinese\nGender: Male\nAge: 31\nPass type: Citizen\n"
        "Occupation: Software engineer\nEmployment type: Permanent\nNo. of pax: 2\n"
        "Move in date: 1 Oct\nLease term: 12\nBudget: 1400\nLocation: near MRT")

    def test_form_shaped_text_never_has_a_viewing_time(self):
        self.assertFalse(E._has_viewing_time(self.FORM_TEXT.lower()))

    def test_plain_proposed_date_is_still_detected(self):
        self.assertTrue(E._has_viewing_time("can we do 3 sep 2pm instead"))


# ---------------------------------------------------------------------------
# Finding 11: a viewing date already in the past must never be accepted unconditionally.
# ---------------------------------------------------------------------------
class TestPastViewingDateRejected(_ReqsFixtureMixin, unittest.TestCase):
    def test_past_date_flags_instead_of_confirming(self):
        self._reqs["past-room"] = _listing("past-room")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="past-room", form_sent=True, viewing_asked=True,
            viewing_confirmed=True, offered_slot_label="Thu 10 Sep, 2pm to 3pm",
            offered_slot_id="past-room-slot-1", profile=dict(COMPLETE_PROFILE))
        ev = {"jid": jid, "msg_id": "p1", "text": "Actually can we do 3 Sep, 2pm instead?",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a["text"])
        self.assertIn("already passed", a["reason"])

    def test_future_date_still_accepted(self):
        self.assertFalse(E._proposed_date_in_past("can we do 20 Dec instead"))


# ---------------------------------------------------------------------------
# Finding 12: an unbound conversation must not go permanently silent after one flag --
# fresh actionable content (a time, a figure, a question) still needs Winfred once each.
# ---------------------------------------------------------------------------
class TestUnboundRecordFreshContentNotify(_ReqsFixtureMixin, unittest.TestCase):
    def test_fresh_question_after_initial_flag_still_notifies_once(self):
        st, pn, jid, rec = self._state_with_rec(
            form_sent=True, profile=dict(COMPLETE_PROFILE))
        ev1 = {"jid": jid, "msg_id": "u1", "text": "any update?", "is_from_me": False}
        a1 = E.handle_event(st, ev1)
        self.assertEqual(a1["type"], "FLAG_HUMAN")
        ev2 = dict(ev1, msg_id="u2")   # exact repeat -> silent
        self.assertIsNone(E.handle_event(st, ev2))
        ev3 = {"jid": jid, "msg_id": "u3", "text": "can you do 750 instead", "is_from_me": False}
        a3 = E.handle_event(st, ev3)
        self.assertEqual(a3["type"], "FLAG_HUMAN")
        self.assertTrue(a3["notify"])


# ---------------------------------------------------------------------------
# Finding 13: a post offer question without a literal "?" must still get answered/flagged.
# ---------------------------------------------------------------------------
class TestUnpunctuatedPostOfferQuestion(unittest.TestCase):
    def test_can_i_bring_my_cat_without_question_mark_is_a_question(self):
        self.assertTrue(E._is_question("actually can I bring my cat"))

    def test_plain_statement_is_not_a_question(self):
        self.assertFalse(E._is_question("ok thanks see you then"))


# ---------------------------------------------------------------------------
# Finding 14 (superseded, 9 Sep 2026 final attack pass -- merge review mandatory fix): a
# borderline budget ASK_ONE used to quote the tenant's own figure back and ask them to
# stretch it ("is your $680 budget firm, or could you stretch to $700?") -- that is a live
# negotiation, outside the CEA role boundary. It must now fall through to the SAME
# figure-free ask as any other askable gap, exactly like a generic missing field.
# ---------------------------------------------------------------------------
class TestBudgetBorderlineAskOnce(unittest.TestCase):
    def test_ask_text_never_quotes_a_figure(self):
        txt = E._ask_one_text(["budget 680 just under 700"], ["your budget"], False)
        self.assertNotIn("680", txt)
        self.assertNotIn("700", txt)
        self.assertNotRegex(txt, r"\$\d")
        self.assertIn("your budget", txt)

    def test_generic_gap_unaffected(self):
        txt = E._ask_one_text(["gender missing"], ["your gender"], False)
        self.assertIn("your gender", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
