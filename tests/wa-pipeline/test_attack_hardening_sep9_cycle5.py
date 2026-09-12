"""
test_attack_hardening_sep9_cycle5.py -- unittest coverage for the confirmed adversarial
attack harness findings from the 9 Sep 2026 cycle 5 replay (scripts/wa_intake_attack_harness.py,
scratchpad/attack/cycle5/*). Each TestCase names the finding it guards and reproduces the
exact (or an equivalent minimal) failure signature from the harness scenario, so it fails
against the pre fix engine and passes after.

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_hardening_sep9_cycle5.py
"""
import sys, os, json, tempfile, unittest
from unittest import mock

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
import wa_intake_resume as RES
import wa_intake_draft_worker as WORKER
from test_attack_hardening_sep9_p2 import _listing, _ReqsFixtureMixin
from test_takeover_resume import _isolated_runner, FAKE_PN, FAKE_JID


# ---------------------------------------------------------------------------
# Finding 1 (P0, c5ec07): a mid thread self disclosed agent who recants and fills a tenant
# shaped profile must never reach a real OFFER_VIEWING through the co-pilot verdict path.
# ---------------------------------------------------------------------------
class TestExcludedRecordNeverAutoOffers(_ReqsFixtureMixin, unittest.TestCase):
    def test_agent_recant_then_full_profile_never_offers_viewing(self):
        self._reqs["r1"] = _listing("r1", budget_floor=700)
        st, pn, jid, rec = self._state_with_rec(
            jid="6598887777@s.whatsapp.net", listing_key="r1", form_sent=True, profile={})
        a1 = E.handle_event(st, {
            "jid": jid, "msg_id": "m1",
            "text": "quick disclosure, I'm actually a PropNex agent scouting this for my client",
            "is_from_me": False})
        self.assertEqual(a1["type"], "FLAG_HUMAN")
        self.assertTrue(rec.get("manual_takeover"))
        self.assertTrue(rec.get("copilot_muted"))
        self.assertEqual(rec.get("status"), "excluded:agent")
        form = ("Name: Derek Lim\nNationality: Singaporean\nEthnicity: Chinese\nGender: Male\n"
                "Pass type: Citizen\nNo. of pax: 2\nMove in date: 1 Nov\nLease term: 12\n"
                "Budget: 1500")
        a2 = E.handle_event(st, {"jid": jid, "msg_id": "m2", "text": form, "is_from_me": False})
        self.assertNotEqual((a2 or {}).get("type"), "OFFER_VIEWING")
        self.assertIsNone((a2 or {}).get("text"))

    def test_excluded_status_guard_blocks_even_without_copilot_muted(self):
        """Belt and suspenders: the _copilot_verdict guard alone (independent of the
        copilot_muted latch) must refuse a prospect facing action on an excluded record."""
        self._reqs["r2"] = _listing("r2", budget_floor=700)
        st, pn, jid, rec = self._state_with_rec(
            listing_key="r2", status="excluded:landlord", manual_takeover=True,
            profile={"name": "T", "nationality": "Singaporean", "ethnicity": "Chinese",
                     "gender": "Male", "pass_type": "Citizen", "no_of_pax": 1,
                     "move_in_date": "1 Nov", "lease_term_months": 12, "budget": 1500})
        a = E._copilot_verdict(rec)
        self.assertNotEqual((a or {}).get("type"), "OFFER_VIEWING")


# ---------------------------------------------------------------------------
# Finding 2 (P0, c5rm04): a self disclosed ethnicity question quoting a THIRD PARTY's
# ("some landlords") preference must never read as the tenant rejecting the unit.
# ---------------------------------------------------------------------------
class TestUnitRejectionThirdPartyAndSelfDisclosure(unittest.TestCase):
    def test_third_party_preference_is_not_a_rejection(self):
        self.assertFalse(E._unit_rejection(
            "btw I'm Indian, is that ok? some landlords here don't like, just want to check "
            "first before I waste time"))

    def test_genuine_first_person_rejection_still_detected(self):
        self.assertTrue(E._unit_rejection("actually I don't like this room, too small for me"))

    def test_protected_disclosure_question_routes_to_flag_not_close(self):
        self.assertTrue(E._protected_disclosure_question(
            "btw I'm Indian, is that ok? some landlords here don't like, just want to check "
            "first before I waste time"))

    def test_handler_flags_instead_of_closing_the_lead(self):
        st = {"version": 1, "conversations": {}}
        jid = "6598887002@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec.update(listing_key="marine-crescent", form_sent=True, profile={})
        ev = {"jid": jid, "msg_id": "d1",
              "text": "btw I'm Indian, is that ok? some landlords here don't like, just want "
                      "to check first before I waste time", "is_from_me": False}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertNotEqual(a.get("type"), "REDIRECT")
        self.assertFalse(rec.get("terminal"))


# ---------------------------------------------------------------------------
# Finding 3 (P1, c5ec05): a media duration caption and a bare day word must never register
# as a proposed viewing time.
# ---------------------------------------------------------------------------
class TestHasViewingTimeMediaDuration(unittest.TestCase):
    def test_voice_message_duration_caption_is_not_a_time(self):
        self.assertFalse(E._has_viewing_time("[Voice message, 0:42]"))

    def test_video_duration_caption_is_not_a_time(self):
        self.assertFalse(E._has_viewing_time("[Video, 0:30]"))

    def test_bare_colon_time_with_no_day_word_is_not_a_time(self):
        # "[Video, 1:10]" stripped of its caption leaves a bare "1:10" -- must not count
        # without an accompanying day word (only am/pm or a day word makes it real).
        self.assertFalse(E._has_viewing_time("random note 1:10 no context"))

    def test_genuine_day_and_time_still_counts(self):
        self.assertTrue(E._has_viewing_time("can we view 31 Sep, 6pm"))

    def test_explicit_calendar_date_still_counts(self):
        self.assertTrue(E._has_viewing_time("can i come 16/6"))

    def test_bare_relative_day_word_still_counts_unchanged(self):
        # existing, deliberate behaviour (test_intake_engine.py) -- a bare "tomorrow" alone
        # is still treated as a proposed day, this fix never touches that.
        self.assertTrue(E._has_viewing_time("tomorrow evening"))


# ---------------------------------------------------------------------------
# Finding 4 (P1, c5rm01): a statement phrased tenant message must always reach the dead end
# catch all, even right after two earlier notifies left the status unchanged.
# ---------------------------------------------------------------------------
class TestDeadEndCatchAllTextAware(_ReqsFixtureMixin, unittest.TestCase):
    def test_third_distinct_message_on_same_status_still_flags(self):
        self._reqs["r3"] = _listing("r3")
        st, pn, jid, rec = self._state_with_rec(jid="6598887003@s.whatsapp.net")
        E.handle_event(st, {"jid": jid, "msg_id": "m0", "text": "hi r3 still available",
                             "is_from_me": False, "listing_key": "r3"})
        a1 = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "Is this still available?",
                                  "is_from_me": False})
        self.assertEqual(a1["type"], "FLAG_HUMAN")
        a2 = E.handle_event(st, {"jid": jid, "msg_id": "m2",
                                  "text": "Can i ask details about the room?", "is_from_me": False})
        self.assertEqual(a2["type"], "FLAG_HUMAN")
        a3 = E.handle_event(st, {"jid": jid, "msg_id": "m3",
                                  "text": "Also please let me know if can send video",
                                  "is_from_me": False})
        self.assertIsNotNone(a3)
        self.assertEqual(a3["type"], "FLAG_HUMAN")
        self.assertTrue(a3["notify"])

    def test_exact_repeat_still_suppressed(self):
        self._reqs["r4"] = _listing("r4")
        st, pn, jid, rec = self._state_with_rec(jid="6598887004@s.whatsapp.net")
        E.handle_event(st, {"jid": jid, "msg_id": "n0", "text": "hi r4 still available",
                             "is_from_me": False, "listing_key": "r4"})
        ev = {"jid": jid, "msg_id": "n1",
              "text": "Also please let me know if can send video", "is_from_me": False}
        a1 = E.handle_event(st, ev)
        self.assertEqual(a1["type"], "FLAG_HUMAN")
        ev2 = dict(ev); ev2["msg_id"] = "n2"
        a2 = E.handle_event(st, ev2)
        self.assertIsNone(a2)


# ---------------------------------------------------------------------------
# Finding 5 (P1, c5rm07): a deposit/payment claim and an urgent unit number demand must
# reach Winfred as DISTINGUISHABLE drafts, never identical boilerplate.
# ---------------------------------------------------------------------------
class TestProcessDraftNeededCarriesVerbatimMessage(unittest.TestCase):
    def test_two_different_inbounds_produce_different_notify_text(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, id TEXT, "
                    "chat_jid TEXT, is_from_me INTEGER, content TEXT, timestamp TEXT)")
        con.commit()
        tmp = f"/tmp/test-drafts-diff-{os.getpid()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            # item 3 (9 Sep 2026 merge redo): process_draft_needed now only SPAWNS a
            # background request -- capture what it would have spawned (instead of really
            # Popen-ing claude-guard) and feed each straight into finish_resume_draft, which
            # is where the verbatim-inbound-in-the-notify behaviour this test guards
            # actually lives now.
            captured = []
            with mock.patch.object(WORKER, "spawn_request",
                                   side_effect=lambda kind, key, jid, prompt, context=None:
                                   (captured.append({"pn": key, "jid": jid, "context": context}),
                                    "spawned")[1]):
                rec1 = {"profile": {}, "listing_key": "r1",
                        "last_inbound": "Btw here's my PayNow, I already sent $200 deposit"}
                RES.process_draft_needed(con, "id", "6598887000@lid", "6598887000", rec1, None,
                                         lambda m: None, lambda *a: None)
                rec2 = {"profile": {}, "listing_key": "r1",
                        "last_inbound": "Any update? also can you just tell me the unit number"}
                RES.process_draft_needed(con, "id", "6598887000@lid", "6598887000", rec2, None,
                                         lambda m: None, lambda *a: None)
            self.assertEqual(len(captured), 2)
            notified = []
            for record in captured:
                RES.finish_resume_draft(record, "Thanks for checking in! Happy to help.", None,
                                        notified.append, lambda *a: None)
            self.assertEqual(len(notified), 2)
            self.assertNotEqual(notified[0], notified[1])
            self.assertIn("PayNow", notified[0])
            self.assertIn("unit number", notified[1])
        finally:
            RES.DRAFTS_FILE = orig
            try: os.remove(tmp)
            except OSError: pass


# ---------------------------------------------------------------------------
# Finding 6+7 (P1/P2, hg5-03, c5rm03): a listing with empty pg_url_keywords, and a listing
# named in plain English shorter than its own keyword, must both still be bindable.
# ---------------------------------------------------------------------------
class TestMatchListingFallbackTokens(unittest.TestCase):
    def test_empty_keywords_listing_binds_off_condo_name(self):
        reqs = {
            "toh-tuck": {
                "listing_key": "toh-tuck", "status": "open", "pg_url_keywords": [],
                "property_name": "11 Toh Tuck Rd, Singapore 596290 (High Oak Condo, "
                                 "Beauty World MRT ~5min, D21)",
                "block_address": "11 Toh Tuck Rd, Singapore 596290 (High Oak Condo, "
                                 "Beauty World MRT ~5min, D21)",
            },
        }
        self.assertEqual(
            R.match_listing("hi is the toh tuck rd high oak condo room near beauty world "
                            "mrt still available", reqs),
            "toh-tuck")

    def test_plain_english_name_shorter_than_keyword_binds(self):
        reqs = {
            "bayshore": {
                "listing_key": "bayshore", "status": "open",
                "pg_url_keywords": ["500170240", "blk 62 bayshore"],
                "property_name": "Bayshore Park", "block_address": "Blk 62 Bayshore Park #15-07",
            },
        }
        self.assertEqual(
            R.match_listing("Hi Winfred, I am interested in this common room at Bayshore. "
                            "Let me know if it's still available.", reqs),
            "bayshore")

    def test_fallback_never_outranks_a_real_keyword_hit(self):
        reqs = {
            "bayshore": {
                "listing_key": "bayshore", "status": "open",
                "pg_url_keywords": ["500170240", "blk 62 bayshore"],
                "property_name": "Bayshore Park", "block_address": "Blk 62 Bayshore Park #15-07",
            },
            "other-bayshore": {
                "listing_key": "other-bayshore", "status": "open", "pg_url_keywords": [],
                "property_name": "Bayshore Gardens", "block_address": "9 Some Other Road",
            },
        }
        # a real keyword hit still wins outright even though a second listing's fallback
        # token ("bayshore") would also match this text.
        self.assertEqual(
            R.match_listing("still available at blk 62 bayshore?", reqs), "bayshore")

    def test_fallback_tie_stays_ambiguous_not_a_guess(self):
        reqs = {
            "a": {"listing_key": "a", "status": "open", "pg_url_keywords": [],
                  "property_name": "Sunview Terrace", "block_address": "1 Some Road"},
            "b": {"listing_key": "b", "status": "open", "pg_url_keywords": [],
                  "property_name": "Sunview Heights", "block_address": "2 Other Road"},
        }
        self.assertIsNone(R.match_listing("still available near sunview?", reqs))


# ---------------------------------------------------------------------------
# Finding (P2, sc5): a first touch enquiry that also names a short/visa tied lease must
# still get its welcome + form, never zero prospect facing text; the lease ask itself rides
# as a notify alongside the SEND_FORM action, never auto answered.
# ---------------------------------------------------------------------------
class TestShortLeaseFirstTouchStillSendsForm(_ReqsFixtureMixin, unittest.TestCase):
    def test_first_touch_with_short_lease_still_sends_form_and_flags(self):
        self._reqs["admiralty"] = _listing("admiralty")
        st, pn, jid, rec = self._state_with_rec(jid="6598887008@s.whatsapp.net")
        a = E.handle_event(st, {
            "jid": jid, "msg_id": "sl1",
            "text": "hi admiralty common room still open, my work permit renewal only "
                    "confirm in 4 months, so i can only commit 4 months first then extend, "
                    "is that ok",
            "is_from_me": False, "listing_key": "admiralty"})
        self.assertEqual(a["type"], "SEND_FORM")
        self.assertTrue(a.get("notify"))
        self.assertIn("short lease", a.get("reason", ""))
        self.assertFalse(rec.get("lease_note_sent"))
        self.assertTrue(rec.get("form_sent"))

    def test_genuinely_unbound_wandering_still_flags_silently(self):
        st, pn, jid, rec = self._state_with_rec(jid="6598887009@s.whatsapp.net")
        a = E.handle_event(st, {
            "jid": jid, "msg_id": "sl2", "text": "can we do just 3 months lease first ah",
            "is_from_me": False})
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertIsNone(a.get("text"))
        self.assertFalse(rec.get("form_sent"))


# ---------------------------------------------------------------------------
# Finding 8 (P2, sc1): a bare "rent" verb inside an HDB ethnic quota eligibility question
# must never misfire the vague rent pivot, and the one shot fact budget must not be spent
# on the generic pivot.
# ---------------------------------------------------------------------------
class TestFactRentPivotEligibilityVeto(unittest.TestCase):
    def test_ethnic_quota_question_never_gets_rent_pivot(self):
        listing = _listing("hdb-quota")
        ans = E._tenant_fact_answer(
            "before that, is this HDB with the race quota thing or private, i heard "
            "foreigner cannot rent some HDB because of ethnic quota, want to check first",
            listing)
        self.assertIsNone(ans)

    def test_rent_pivot_never_consumes_fact_budget(self):
        listing = _listing("mrt-room", facts={"mrt": "5 minutes to the MRT"})
        ans1 = E._tenant_fact_answer("can rent for how much ah", listing)
        self.assertEqual(ans1, E._RENT_PIVOT_TEXT)
        # the one shot budget is only spent by the CALLER on a genuine facts sheet hit; the
        # rent pivot text itself must never be treated as having spent it.
        self.assertNotEqual(E._RENT_PIVOT_TEXT, "5 minutes to the MRT")

    def test_bare_rent_still_pivots_when_no_eligibility_terms(self):
        listing = _listing("some-room")
        ans = E._tenant_fact_answer("how much is this room", listing)
        self.assertEqual(ans, E._RENT_PIVOT_TEXT)


# ---------------------------------------------------------------------------
# Finding 9 (P2, c5ec02): a stale backfilled row must still BIND the listing key (read
# only, never served) so a later in window message inherits the anchor.
# ---------------------------------------------------------------------------
class TestStaleBackfillBindsListingKeyOnly(unittest.TestCase):
    def test_stale_row_binds_but_never_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx_path = os.path.join(tmp, "no-idx.json")
            with open(idx_path, "w") as f:
                json.dump({"listings": [{
                    "listing_key": "attack-c5ec02-metatest", "status": "open",
                    "deal_type": "rent", "pg_url_keywords": ["c5ec02 metatest"],
                    "requirements": {"gender": "any", "budget_floor": 700,
                                     "lease_min_months": 12, "cooking": "light",
                                     "smoking": "no", "pets_tenant_may_bring": False,
                                     "ethnicity_rule": {"mode": "any", "list": []},
                                     "nationality_pref": {"mode": "any", "list": []},
                                     "max_pax": 2},
                }]}, f)
            with _isolated_runner(
                    tmp, inbound_content="hi is c5ec02 metatest room still available, saw "
                                         "on propertyguru",
                    inbound_minutes_ago=48 * 60 + 30) as calls:
                pass
            self.assertEqual(calls["sent"], [])
            with open(os.path.join(tmp, "intake-state.json")) as f:
                state = json.load(f)
            rec = state["conversations"].get(FAKE_PN)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.get("listing_key"), "attack-c5ec02-metatest")
            self.assertFalse(rec.get("form_sent"))


# ---------------------------------------------------------------------------
# Finding 10 (P2, hg5-07): a numbered/positional answer with zero recognised labels must
# map onto the intake template's field order instead of discarding the profile.
# ---------------------------------------------------------------------------
class TestPositionalFormParsing(unittest.TestCase):
    def test_numbered_list_maps_to_template_field_order(self):
        out = E._extract_positional_form(
            "1) Alex 2) Singaporean 3) Chinese 4) Male 5) SC 6) engineer 7) permanent "
            "8) 1 9) next month 10) 12 11) 2000")
        self.assertEqual(out["name"], "Alex")
        self.assertEqual(out["nationality"], "Singaporean")
        self.assertEqual(out["ethnicity"], "Chinese")
        self.assertEqual(out["gender"], "Male")
        self.assertEqual(out["pass_type"], "SC")
        self.assertEqual(out["occupation"], "engineer")
        self.assertEqual(out["employment_type"], "permanent")
        self.assertEqual(out["no_of_pax"], 1)
        self.assertEqual(out["move_in_date"], "next month")
        self.assertEqual(out["lease_term_months"], 12)
        self.assertEqual(out["budget"], 2000)

    def test_too_few_markers_falls_through(self):
        self.assertEqual(E._extract_positional_form("1) Alex 2) Singaporean"), {})

    def test_unparseable_values_fall_through_not_guessed(self):
        # 8+ markers but pax/lease/budget never type check -> {} so the existing
        # unparseable-form flag still fires with the raw text, never a bad guess.
        out = E._extract_positional_form(
            "1) Alex 2) Singaporean 3) Chinese 4) Male 5) SC 6) engineer 7) permanent "
            "8) many 9) soon 10) long 11) lots")
        self.assertEqual(out, {})

    def test_end_to_end_merges_into_profile(self):
        st = {"version": 1, "conversations": {}}
        jid = "6598887005@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec.update(form_sent=True)
        ev = {"jid": jid, "msg_id": "p1",
              "text": "1) Alex 2) Singaporean 3) Chinese 4) Male 5) SC 6) engineer "
                      "7) permanent 8) 1 9) next month 10) 12 11) 2000", "is_from_me": False}
        E.handle_event(st, ev)
        self.assertEqual(rec["profile"].get("name"), "Alex")
        self.assertEqual(rec["profile"].get("budget"), 2000)


# ---------------------------------------------------------------------------
# Finding 11 (P2, sc2): a third party's unavailability described mid message must never be
# read as the TENANT declining their own offered slot, and a question must veto it too.
# ---------------------------------------------------------------------------
class TestDeclineThirdPartyAndQuestionVeto(_ReqsFixtureMixin, unittest.TestCase):
    def test_third_party_unavailability_is_not_a_decline(self):
        self._reqs["r5"] = _listing("r5")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="r5", form_sent=True, viewing_asked=True, viewing_confirmed=True,
            offered_slot_label="Thu 10 Sep, 2pm to 3pm",
            profile={"name": "T", "budget": 900})
        a = E.handle_event(st, {
            "jid": jid, "msg_id": "d1",
            "text": "so is it ok if i come alone to view, she not free", "is_from_me": False})
        self.assertNotEqual((a or {}).get("type"), "ASK_TENANT_TIME")

    def test_genuine_first_person_decline_still_asks_tenant_time(self):
        self._reqs["r6"] = _listing("r6")
        st, pn, jid, rec = self._state_with_rec(
            listing_key="r6", form_sent=True, viewing_asked=True, viewing_confirmed=True,
            offered_slot_label="Thu 10 Sep, 2pm to 3pm",
            profile={"name": "T", "budget": 900})
        a = E.handle_event(st, {"jid": jid, "msg_id": "d2",
                                 "text": "sorry cannot make it, not free that day",
                                 "is_from_me": False})
        self.assertEqual(a["type"], "ASK_TENANT_TIME")


# ---------------------------------------------------------------------------
# Finding 12 (P2, sc3): a foreign currency budget must never be read as SGD.
# ---------------------------------------------------------------------------
class TestToIntCurrencyAware(unittest.TestCase):
    def test_ringgit_budget_not_read_as_sgd(self):
        self.assertIsNone(E._to_int("RM800"))
        self.assertIsNone(E._to_int("myr 800"))
        self.assertIsNone(E._to_int("usd500"))
        self.assertIsNone(E._to_int("₹15000"))

    def test_plain_sgd_budget_unaffected(self):
        self.assertEqual(E._to_int("800"), 800)
        self.assertEqual(E._to_int("$1500"), 1500)

    def test_extract_profile_drops_non_sgd_budget(self):
        p = E.extract_profile("Budget: RM800")
        self.assertNotIn("budget", p)

    def test_qualify_needs_info_on_non_sgd_budget_not_disqualified(self):
        listing = _listing("ringgit-room", budget_floor=700)
        profile = {"name": "T", "nationality": "Malaysian", "ethnicity": "Malay",
                   "gender": "Female", "pass_type": "Work Permit", "no_of_pax": 1,
                   "move_in_date": "next week", "lease_term_months": 12}
        verdict, why = E.qualify(listing, profile)
        self.assertEqual(verdict, "NEEDS_INFO")
        self.assertIn("budget", why)


# ---------------------------------------------------------------------------
# Finding 13 (P3, sc4): a sticker/location pin with no text on an already active record
# must never be fully invisible -- a quiet, non notifying log line instead.
# ---------------------------------------------------------------------------
class TestMediaNoTextNeverInvisible(_ReqsFixtureMixin, unittest.TestCase):
    def test_sticker_after_form_sent_logs_quietly(self):
        self._reqs["r7"] = _listing("r7")
        st, pn, jid, rec = self._state_with_rec(listing_key="r7", form_sent=True, profile={})
        a = E.handle_event(st, {"jid": jid, "msg_id": "s1", "text": "",
                                 "media_type": "sticker", "is_from_me": False})
        self.assertIsNotNone(a)
        self.assertFalse(a.get("notify"))
        self.assertIsNone(a.get("text"))

    def test_location_pin_after_form_sent_logs_quietly(self):
        self._reqs["r8"] = _listing("r8")
        st, pn, jid, rec = self._state_with_rec(listing_key="r8", form_sent=True, profile={})
        a = E.handle_event(st, {"jid": jid, "msg_id": "s2", "text": "",
                                 "media_type": "location", "is_from_me": False})
        self.assertIsNotNone(a)
        self.assertFalse(a.get("notify"))

    def test_sticker_before_form_sent_still_uses_loud_flag(self):
        st = {"version": 1, "conversations": {}}
        jid = "6598887006@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        a = E.handle_event(st, {"jid": jid, "msg_id": "s3", "text": "",
                                 "media_type": "sticker", "is_from_me": False})
        self.assertEqual(a["type"], "FLAG_HUMAN")
        self.assertTrue(a["notify"])


# ---------------------------------------------------------------------------
# Finding 14 (P3, sc4): "where" inside a declarative relative clause must never register as
# a question.
# ---------------------------------------------------------------------------
class TestIsQuestionRelativeClauseVeto(unittest.TestCase):
    def test_relative_clause_where_is_not_a_question(self):
        self.assertFalse(E._is_question(
            "that pin is where we currently stay, just for your reference"))

    def test_genuine_where_question_still_detected(self):
        self.assertTrue(E._is_question("where is the unit exactly"))

    def test_how_far_still_detected(self):
        self.assertTrue(E._is_question("how far MRT from here also"))


# ---------------------------------------------------------------------------
# Finding 15 (P3, hg5-06): ASK_ONE for a field the immediately preceding inbound was
# flagged on must be softened, not read as ignoring what they just said.
# ---------------------------------------------------------------------------
class TestAskOneSoftenedReask(unittest.TestCase):
    def test_gender_reask_softened_after_gender_flag(self):
        txt = E._ask_one_text(["gender"], ["your gender"], False, just_flagged_topic="gender")
        self.assertNotIn("Can I just check your gender?", txt)
        self.assertIn("your gender", txt)

    def test_gender_ask_unchanged_without_a_preceding_flag(self):
        txt = E._ask_one_text(["gender"], ["your gender"], False, just_flagged_topic=None)
        self.assertIn("Can I just check your gender", txt)

    def test_last_flag_topic_stamped_on_gender_question_flag(self):
        st = {"version": 1, "conversations": {}}
        jid = "6598887007@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec.update(form_sent=True, profile={})
        E.handle_event(st, {
            "jid": jid, "msg_id": "g1",
            "text": "I'm actually a guy but I identify as very in touch with my feminine "
                    "side lol, ladies only rule still apply?", "is_from_me": False})
        self.assertEqual(rec.get("last_flag_topic"), "gender")


if __name__ == "__main__":
    unittest.main(verbosity=2)
