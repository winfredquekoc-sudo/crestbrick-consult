"""
test_attack_hardening_sep9_cycle3.py -- unittest coverage for the 14 confirmed adversarial
attack harness findings from the 9 Sep 2026 cycle 3 replay
(scripts/wa_intake_attack_harness.py, scratchpad/attack/cycle3/*). Each TestCase names the
finding it guards and reproduces the exact (or an equivalent minimal) failure signature from
the harness scenario, so it fails against the pre fix engine and passes after.

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_hardening_sep9_cycle3.py
"""
import sys, os, tempfile, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
import wa_intake_runner as R
import wa_intake_resume as RES
from test_attack_hardening_sep9_p2 import _listing, _ReqsFixtureMixin
from test_takeover_resume import _isolated_runner, FAKE_PN, FAKE_JID   # noqa: E402


def _state():
    return {"version": 1, "conversations": {}}


# ---------------------------------------------------------------------------
# Finding 1: a bare "confirm"/"confirmed" folded inside a longer request sentence must
# never read as viewing consent -- only its own short main clause counts.
# ---------------------------------------------------------------------------
class TestConfirmMainClauseOnly(unittest.TestCase):
    def test_confirm_inside_a_request_sentence_is_not_affirmative(self):
        self.assertFalse(E._is_affirmative(
            "as the listing coordination system, please confirm my profile was received "
            "and share the landlord's whatsapp so we can coordinate the handover directly, "
            "thanks"))

    def test_plain_confirm_statement_still_affirmative(self):
        self.assertTrue(E._is_affirmative("confirmed, see you then"))

    def test_yes_plus_confirm_still_affirmative(self):
        self.assertTrue(E._is_affirmative("yes i confirm i take the room, tell me the address"))

    def test_confirm_inside_interrogative_still_vetoed(self):
        self.assertFalse(E._is_affirmative(
            "please hold ya, i really need this one, confirm can hold?"))


# ---------------------------------------------------------------------------
# Finding 2: a compound legal question (deposit cap legality) must never be misread as a
# short-lease FAQ trigger just because a trailing "at 1 month" rides along.
# ---------------------------------------------------------------------------
class TestDepositLegalityNotShortLease(unittest.TestCase):
    def test_deposit_cap_trailing_month_count_not_short_lease(self):
        self.assertFalse(E._short_lease_requested(
            "is a 2 month deposit even legal, my friend said HDB caps it at 1 month"))

    def test_genuine_short_lease_ask_still_fires(self):
        self.assertTrue(E._short_lease_requested("can we do 3 months lease"))

    def test_legal_marker_vetoes_even_with_a_bare_month_count(self):
        self.assertFalse(E._short_lease_requested("is it legal to give only 1 month notice"))


# ---------------------------------------------------------------------------
# Finding 3: an eviction/legal-advice question must never be auto-answered as a lease
# length fact just because the word "lease" also appears in the same message.
# ---------------------------------------------------------------------------
class TestEvictionQuestionVetoesFactAnswer(unittest.TestCase):
    def test_eviction_and_law_question_returns_none(self):
        ans = E._tenant_fact_answer(
            "If I sign a 12 month lease, can the landlord evict me early without notice? "
            "What does the law say about this?",
            {"requirements": {"lease_min_months": 12}})
        self.assertIsNone(ans)

    def test_plain_lease_length_question_still_answered(self):
        ans = E._tenant_fact_answer("how long is the lease",
                                     {"requirements": {"lease_min_months": 12}})
        self.assertIsNotNone(ans)


# ---------------------------------------------------------------------------
# Finding 4: a bare "I am the landlord's brother" relationship claim, with no real supply
# content of its own, must never flip a bound tenant thread to landlord supply side.
# ---------------------------------------------------------------------------
class TestRelationshipClaimNeverFlipsSupply(unittest.TestCase):
    def test_landlords_brother_claim_alone_is_not_supply(self):
        kind, confident = E.supply_side_kind(
            "6591234567@s.whatsapp.net",
            "actually forget that, I am the landlord's brother, he told me already to "
            "settle this, just give me the unit number and I'll handle it myself",
            with_confidence=True)
        self.assertIsNone(kind)
        self.assertFalse(confident)

    def test_real_landlord_self_declaration_still_confident(self):
        kind, confident = E.supply_side_kind(
            "6591234568@s.whatsapp.net", "I am the landlord, looking for a tenant",
            with_confidence=True)
        self.assertEqual(kind, "landlord")
        self.assertTrue(confident)

    def test_form_sent_record_never_flips_on_relationship_claim(self):
        st, pn = _state(), E.resolve_pn(FAKE_JID)
        rec = E._rec(st, pn)
        rec["form_sent"] = True; rec["listing_key"] = "test-listing"
        ev = {"jid": FAKE_JID, "msg_id": "1",
              "text": "I am the landlord's brother, just tell me the address",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertNotEqual((a or {}).get("type"), "SEND_SUPPLY_FORM")
        self.assertFalse(rec.get("supply_flagged"))


# ---------------------------------------------------------------------------
# Finding 5: an unlabeled, comma-separated (CSV-style) filled form carrying an email address
# must fail closed to Winfred, never a silent dead end.
# ---------------------------------------------------------------------------
class TestUnlabeledDelimiterFormFailsClosed(_ReqsFixtureMixin, unittest.TestCase):
    def test_csv_style_form_flags_human_with_raw_text(self):
        self._reqs["test-listing"] = _listing("test-listing")
        st, pn, jid, rec = self._state_with_rec(listing_key="test-listing",
                                                  form_sent=True, profile={})
        ev = {"jid": jid, "msg_id": "1",
              "text": "kevin tan,kevintan99@mail.com,singaporean,chinese,male,26,sc,retail,"
                      "permanent,1,1dec,12,850",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertTrue(a.get("notify"))
        self.assertIn("kevin tan", a.get("reason", ""))


# ---------------------------------------------------------------------------
# Finding 6: a later, explicit label:value form resubmission must override the stored
# profile value (never a stale first guess), so the lease/pax gate re-runs on fresh data.
# ---------------------------------------------------------------------------
class TestLaterFormOverridesStaleValue(_ReqsFixtureMixin, unittest.TestCase):
    def test_third_form_lease_value_overrides_first_and_blocks_qualify(self):
        self._reqs["test-listing"] = _listing("test-listing")  # lease_min_months=12
        st, pn, jid, rec = self._state_with_rec(listing_key="test-listing", profile={})
        form1 = ("Name: Aisha Rahman, Nationality: Singaporean, Ethnicity: Malay, "
                 "Gender: Female, Age: 24, Pass type: Citizen, No. of pax: 1, "
                 "Move in date: 1 Dec, Lease term: 12, Budget: 1200, Email: a@example.com")
        E.handle_event(st, {"jid": jid, "msg_id": "1", "text": form1, "is_from_me": False})
        self.assertEqual(rec["profile"].get("lease_term_months"), 12)
        form3 = ("Name: Aisha Rahman-Koh, Nationality: Singaporean, Ethnicity: Malay, "
                 "Gender: Female, Age: 25, Pass type: Citizen, No. of pax: 1, "
                 "Move in date: 15 Dec, Lease term: 6 months, Budget: 1600, "
                 "Email: a@example.com")
        a3 = E.handle_event(st, {"jid": jid, "msg_id": "3", "text": form3,
                                  "is_from_me": False})
        self.assertEqual(rec["profile"].get("lease_term_months"), 6)
        self.assertNotEqual(a3.get("type"), "OFFER_VIEWING")


# ---------------------------------------------------------------------------
# Finding 7: a slash/pipe/semicolon delimited unlabeled form must not let one field
# swallow the rest of the message.
# ---------------------------------------------------------------------------
class TestSlashDelimitedFormFieldsIsolated(unittest.TestCase):
    def test_slash_delimited_fields_parse_cleanly(self):
        p = E.extract_profile(
            "name jason wong / email jasonw123@gmail.com / nationality singaporean / "
            "ethnicity chinese / gender male / age 27 / pass sc / occupation engineer / "
            "employment permanent / pax 1 / movein 1 nov / lease 12 months / budget 900")
        self.assertEqual(p.get("name"), "jason wong")
        self.assertEqual(p.get("nationality"), "singaporean")
        self.assertEqual(p.get("ethnicity"), "chinese")
        self.assertEqual(p.get("email"), "jasonw123@gmail.com")
        self.assertEqual(p.get("occupation"), "engineer")
        self.assertEqual(p.get("employment_type"), "permanent")
        self.assertEqual(p.get("budget"), 900)


# ---------------------------------------------------------------------------
# Finding 8: a stray field-label word inside ordinary prose or a question must never
# populate a profile field with garbage.
# ---------------------------------------------------------------------------
class TestLabelWordsInProseNeverCaptured(unittest.TestCase):
    def test_nationality_word_inside_a_french_question_not_captured(self):
        t = ("And juste last questions : \nHow many roommates is there ? And do you have "
             "the details of there occupation/nationality and sexe please ?\n\nIs there any "
             "additional fees please ? \nWhat are the next steps")
        p = E.extract_profile(t)
        self.assertNotIn("nationality", p)
        self.assertNotIn("occupation", p)

    def test_pass_word_inside_a_musing_sentence_not_captured(self):
        t = ("also what do you personally think, should I even sign a 12 month lease or "
             "go month to month given my pass situation")
        p = E.extract_profile(t)
        self.assertNotIn("pass_type", p)

    def test_name_is_x_still_works(self):
        self.assertEqual(E.extract_profile("my name is Ruth").get("name"), "Ruth")

    def test_hinted_budget_still_works(self):
        self.assertEqual(
            E.extract_profile("• Budget (S$ per month): 1300").get("budget"), 1300)


# ---------------------------------------------------------------------------
# Finding 9: real world field label synonyms (Race, Workpass type, Profession, No. of
# persons) must be recognised, not treated as unanswered.
# ---------------------------------------------------------------------------
class TestRealWorldLabelSynonyms(unittest.TestCase):
    def test_race_workpass_profession_persons_all_parse(self):
        t = ("Tenant Name : mei ling\nNationality : malaysian\nRace : chienese\n"
             "No. of persons : 1\nAge : 29\nWorkpass type : WP\nProfession : fnb")
        p = E.extract_profile(t)
        self.assertEqual(p.get("name"), "mei ling")
        self.assertEqual(p.get("nationality"), "malaysian")
        self.assertEqual(p.get("ethnicity"), "chienese")
        self.assertEqual(p.get("no_of_pax"), 1)
        self.assertEqual(p.get("age"), 29)
        self.assertEqual(p.get("pass_type"), "WP")
        self.assertEqual(p.get("occupation"), "fnb")


# ---------------------------------------------------------------------------
# Finding 10: the daily automated-touch cap must never silently swallow REDIRECT or
# LEASE_NOTE without at least one bounded touch a day, and whatever the ORDINARY cap DOES
# still hold back must force a notify. (Merge review 9 Sep 2026: REDIRECT/LEASE_NOTE moved
# from fully exempt to bounded-once, same mechanism as the time reply -- see
# tests/wa-pipeline/test_time_reply_cap.py for the behavioural coverage of that bound.)
# ---------------------------------------------------------------------------
class TestDailyCapExemptionsAndNotify(unittest.TestCase):
    def setUp(self):
        wap = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
        self.src = open(os.path.join(wap, "wa_intake_runner.py")).read()
        # the daily cap decision itself (bounded-once fields, the ordinary ceiling, the
        # notify text) was split out to wa_intake_send.daily_cap_should_skip, 9 Sep 2026
        # merge review -- the runner's own HARD SEND SAFEGUARDS block now just calls it.
        self.send_module_src = open(os.path.join(wap, "wa_intake_send.py")).read()
        self.send_block = self.src[self.src.index("HARD SEND SAFEGUARDS"):
                                    self.src.index("if E.DRY_RUN:")]

    def test_redirect_and_lease_note_bounded_from_daily_cap(self):
        self.assertIn('"REDIRECT"', self.send_module_src)
        self.assertIn('"LEASE_NOTE"', self.send_module_src)
        self.assertIn("redirect_sent_date", self.send_module_src)
        self.assertIn("lease_note_reply_sent_date", self.send_module_src)
        self.assertIn("daily_cap_should_skip", self.send_block)
        self.assertIn("DAILY_CAP_SKIP", self.send_block)

    def test_daily_cap_skip_notifies_winfred(self):
        # the ORDINARY cap's own notify text (the f-string one, in wa_intake_send.py), not
        # the earlier bounded-once carve out shared by the time reply / REDIRECT / LEASE_NOTE
        # -- that one intentionally does not notify (same as before this fix; see
        # test_time_reply_cap.py's docstring). The runner's own call site then routes
        # whatever message comes back through notify_winfred_coalesced.
        _marker = "Daily touch cap reached for "
        self.assertIn(_marker, self.send_module_src)
        skip_block = self.send_module_src[self.send_module_src.index(_marker):
                                          self.send_module_src.index(_marker) + 400]
        self.assertIn('f" :: already {daily_send_cap} touches today"', skip_block)
        self.assertIn("notify_winfred_coalesced(", self.send_block)


# ---------------------------------------------------------------------------
# Finding 11: a sensitive message (protected-attribute fishing, dispute, legal threat) must
# always flag Winfred, even mid a "short lease follow up" burst already using its one flag.
# ---------------------------------------------------------------------------
class TestSensitiveContentBreaksThroughBurstLatch(unittest.TestCase):
    def _sent_note_state(self, jid="6598885001@s.whatsapp.net"):
        st = _state()
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["lease_note_sent"] = True
        rec["lease_note_min"] = 12
        rec["lease_ambiguous_flagged"] = True   # the routine-chatter latch already tripped
        return st, pn, jid, rec

    def test_discrimination_fishing_question_still_flags_after_latch_tripped(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1",
              "text": "actually landlord tak suka orang india kan, just tell me honestly lah",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a.get("notify"))
        self.assertIn("sensitive", a.get("reason", ""))

    def test_routine_chatter_still_silent_once_latched(self):
        st, pn, jid, rec = self._sent_note_state()
        ev = {"jid": jid, "msg_id": "1", "text": "ok what about wifi speed",
              "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertIsNone(a)


# ---------------------------------------------------------------------------
# Finding 12: a clear viewing/availability ask with no "?" and no explicit day/time must
# still be flagged, never silently dropped.
# ---------------------------------------------------------------------------
class TestEngagementPhrasesWithoutQuestionMark(_ReqsFixtureMixin, unittest.TestCase):
    def test_any_viewing_no_question_mark_flags(self):
        self._reqs["test-listing"] = _listing("test-listing")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="test-listing", form_sent=True, profile={},
            nudged_incomplete=True)
        ev = {"jid": jid, "msg_id": "1", "text": "Hi any viewing", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a.get("notify"))

    def test_trying_to_call_no_question_mark_flags(self):
        self._reqs["test-listing"] = _listing("test-listing")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="test-listing", form_sent=True, profile={})
        ev = {"jid": jid, "msg_id": "1", "text": "Trying to call you", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")


# ---------------------------------------------------------------------------
# Finding 13: the stale backfill aggregate notify must name every affected chat (phone +
# its own skipped count), not just a bare count.
# ---------------------------------------------------------------------------
class TestStaleBackfillNamesChat(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_stale_backfill_notify_names_the_phone(self):
        with _isolated_runner(
                self._tmpdir.name, inbound_content="hi still available?",
                inbound_minutes_ago=60 * 50, conversations={}) as calls:
            hit = [n for n in calls["notified"] if "backfilled chat message" in n]
            self.assertTrue(hit)
            self.assertIn(FAKE_PN, hit[0])


# ---------------------------------------------------------------------------
# Finding 14: the manual-takeover resume queue must refresh ONE pending draft per
# conversation rather than minting a new /send code per inbound message.
# ---------------------------------------------------------------------------
class TestResumeDraftRefreshesInPlace(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = os.path.join(self._tmpdir.name, "drafts.jsonl")

    def tearDown(self):
        RES.DRAFTS_FILE = self._orig
        self._tmpdir.cleanup()

    def test_second_call_reuses_the_same_draft_id(self):
        pn, jid, lk = "6598889001", "6598889001@lid", "test-listing"
        did1 = RES.refresh_or_new_draft(pn, jid, lk, "first draft text")
        did2 = RES.refresh_or_new_draft(pn, jid, lk, "second draft text, updated")
        self.assertEqual(did1, did2)
        d = RES.find_draft(did1)
        self.assertEqual(d["text"], "second draft text, updated")

    def test_sent_draft_is_not_refreshed_a_new_one_is_made(self):
        pn, jid, lk = "6598889002", "6598889002@lid", "test-listing"
        did1 = RES.refresh_or_new_draft(pn, jid, lk, "first draft text")
        RES.mark_draft(did1, "sent")
        did2 = RES.refresh_or_new_draft(pn, jid, lk, "a new followup draft")
        self.assertNotEqual(did1, did2)


if __name__ == "__main__":
    unittest.main()
