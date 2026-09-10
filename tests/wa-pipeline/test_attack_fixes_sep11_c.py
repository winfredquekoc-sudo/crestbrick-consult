"""
test_attack_fixes_sep11_c.py -- unittest coverage for package C of the 11 Sep 2026 attack
replay (profile parsing, lease handling, language, signoff). Each TestCase names the finding
key it guards (scratchpad/attack/flow/findings.json) and reproduces the exact (or an
equivalent minimal) failure signature from the cited harness scenario, so it fails against
the pre fix engine and passes after.

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_fixes_sep11_c.py
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
from test_attack_hardening_sep9_p2 import _listing, _ReqsFixtureMixin


def _state():
    return {"version": 1, "conversations": {}}


# ---------------------------------------------------------------------------
# fractional-lease-term-not-parsed (c1-02-2week-stay-reno-gap idx 3): "Lease term: 0.5" on a
# filled form dropped the key entirely (int(0.5) truncates to a falsy 0), so qualify() never
# ran on an otherwise complete profile.
# ---------------------------------------------------------------------------
class TestFractionalLeaseTermParsed(unittest.TestCase):
    def test_bare_fraction_parses_to_a_real_zero_not_a_missing_key(self):
        p = E.extract_profile("Lease term: 0.5")
        self.assertIn("lease_term_months", p)
        self.assertEqual(p["lease_term_months"], 0)

    def test_half_a_year_is_six_months(self):
        self.assertEqual(E.extract_profile("Lease term: half a year")["lease_term_months"], 6)
        self.assertEqual(E.extract_profile("Lease term: 0.5 year")["lease_term_months"], 6)

    def test_four_months_jan_to_apr_is_four(self):
        self.assertEqual(
            E.extract_profile("Lease term: 4 months jan to apr")["lease_term_months"], 4)

    def test_full_scenario_reaches_a_real_verdict(self):
        st = _state()
        jid = "7098722116@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "reno-room"
        text = ("Name: Priya Nair\nNationality: Singaporean\nEthnicity: Indian\nGender: "
                "Female\nPass type: Citizen\nNo. of pax: 1\nMove in date: 15 Sep\n"
                "Lease term: 0.5\nBudget: 850")
        E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": text, "is_from_me": False})
        self.assertEqual(rec["profile"].get("lease_term_months"), 0)


# ---------------------------------------------------------------------------
# short-lease-in-weeks-not-detected (c1-02-2week-stay-reno-gap idx 2): "2 weeks max" produced
# no lease note, only a generic unmatched flag -- the detector only read months.
# ---------------------------------------------------------------------------
class TestShortLeaseWeeksDetected(unittest.TestCase):
    def test_two_weeks_fires(self):
        self.assertTrue(E._short_lease_requested(
            "just need it while my reno is ongoing, 2 weeks max"))

    def test_fortnight_and_few_days_fire(self):
        self.assertTrue(E._short_lease_requested("can I get it for a fortnight only"))
        self.assertTrue(E._short_lease_requested("just need it for a few days"))

    def test_past_tense_weeks_still_vetoed(self):
        self.assertFalse(E._short_lease_requested("stayed 2 weeks at my last place"))

    def test_notice_period_weeks_still_vetoed(self):
        self.assertFalse(E._short_lease_requested("2 weeks notice period"))


# ---------------------------------------------------------------------------
# freetext-parsed-as-profile-field (c1-04-overseas-till-dec-unbound idx 2): a bare "is ok"
# leftover from "...if Dec move in is ok" got stored as move_in_date "ok".
# ---------------------------------------------------------------------------
class TestFreetextNeverPoisonsProfile(unittest.TestCase):
    def test_bare_leftover_word_not_captured_as_movein(self):
        p = E.extract_profile(
            "actually nvm just want to know in general if Dec move in is ok")
        self.assertNotIn("move_in_date", p)

    def test_genuine_movein_content_still_parses(self):
        p = E.extract_profile("hi any update, he really needs to move in tomorrow if possible")
        self.assertEqual(p.get("move_in_date"), "tomorrow if possible")
        p2 = E.extract_profile("• Intended Move in Date (e.g. 1 Aug): 15 Aug")
        self.assertEqual(p2.get("move_in_date"), "15 Aug")

    def test_name_is_x_still_works(self):
        # existing declarative-statement precedent (test_attack_hardening_sep9_cycle3.py)
        # must never regress from the fallback-match tightening.
        self.assertEqual(E.extract_profile("my name is Ruth").get("name"), "Ruth")


# ---------------------------------------------------------------------------
# lease-note-yes-not-handled (c3rm07-lowest-price-asap-short-lease-nonqualify idx 5): a bare
# "YES" to the engine's own minimum lease question produced total silence (a generic "no
# automated reply matched" flag) because the old short lease_term_months on file was never
# overwritten, so qualify() re-derived SHORT_LEASE and the one-note latch swallowed it.
# ---------------------------------------------------------------------------
class TestLeaseNoteYesHandled(_ReqsFixtureMixin, unittest.TestCase):
    def test_yes_overwrites_a_stale_short_lease_value(self):
        st, pn, jid, rec = self._state_with_rec(lease_note_sent=True, lease_note_min=12)
        rec["profile"]["lease_term_months"] = 3
        E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "YES", "is_from_me": False})
        self.assertTrue(rec.get("lease_note_resolved"))
        self.assertEqual(rec["profile"].get("lease_term_months"), 12)

    def test_full_scenario_yes_leads_to_confirm_viewing(self):
        self._reqs["tampines-room"] = _listing("tampines-room", budget_floor=1000)
        orig_slot = E.next_slot
        E.next_slot = lambda lk: {"slot_id": "s1", "label": "Sat 12 Sep, 3pm to 4pm"}
        try:
            st = _state()
            jid = "7069330737@s.whatsapp.net"
            pn = E.resolve_pn(jid)
            rec = E._rec(st, pn)
            rec["form_sent"] = True
            rec["listing_key"] = "tampines-room"
            rec["viewing_asked"] = False
            form = ("Name: Kelvin Goh\nNationality: Singaporean\nEthnicity: Chinese\nGender: "
                    "Male\nPass type: Citizen\nNo. of pax: 1\nMove in date: ASAP\n"
                    "Lease term: 3\nBudget: 1050")
            E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": form, "is_from_me": False})
            self.assertEqual(rec.get("qualify", {}).get("verdict"), "SHORT_LEASE")
            a = E.handle_event(st, {"jid": jid, "msg_id": "m2", "text": "YES",
                                     "is_from_me": False})
        finally:
            E.next_slot = orig_slot
        self.assertNotEqual(a and a.get("type"), "FLAG_HUMAN")
        self.assertEqual(rec["profile"].get("lease_term_months"), 12)


# ---------------------------------------------------------------------------
# lease-line-repeated-twice (c1-06-exchange-student-leave-anytime-handreply-resume idx 3):
# the fixed fact answer ("The owner is looking for a minimum lease of 1 year") and the
# LEASE_NOTE ("Just to share, the landlord prefers a minimum 1 year lease...") both fired in
# the same short thread -- the bot restating itself.
# ---------------------------------------------------------------------------
class TestLeaseLineNeverRepeated(_ReqsFixtureMixin, unittest.TestCase):
    def test_fact_answer_then_freetext_ask_never_double_sends(self):
        self._reqs["clementi-room"] = _listing("clementi-room")
        st = _state()
        jid = "7010711828@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "clementi-room"
        a1 = E.handle_event(st, {"jid": jid, "msg_id": "m1",
            "text": "Hi sorry but can leave anytime no penalty? Trying to plan my exchange "
                    "period and don't wanna get stuck with a fixed lease",
            "is_from_me": False})
        self.assertEqual(a1["type"], "ANSWER_QUESTION")
        self.assertTrue(rec.get("lease_fact_told"))
        a2 = E.handle_event(st, {"jid": jid, "msg_id": "m2",
            "text": "ok thanks, btw my exchange is only 4 months jan to apr",
            "is_from_me": False})
        self.assertNotEqual(a2 and a2.get("type"), "LEASE_NOTE")


# ---------------------------------------------------------------------------
# chinese-form-fields-not-parsed (c2mix05-chinese-area-busstop-qualify idx 3): extract_profile
# dropped 入住人数 (pax) and 租期 with a CJK 个月 suffix; gender was stored raw as 女.
# ---------------------------------------------------------------------------
class TestChineseFormFieldsParsed(unittest.TestCase):
    def test_pax_label_入住人数_parses(self):
        self.assertEqual(E.extract_profile("入住人数：1").get("no_of_pax"), 1)

    def test_lease_with_cjk_month_suffix_parses(self):
        self.assertEqual(E.extract_profile("租期：12个月").get("lease_term_months"), 12)
        self.assertEqual(E.extract_profile("租期：3个月").get("lease_term_months"), 3)

    def test_gender_cjk_maps_to_english(self):
        self.assertEqual(E.extract_profile("性别：女").get("gender"), "Female")
        self.assertEqual(E.extract_profile("性别：男").get("gender"), "Male")

    def test_full_chinese_form_reaches_qualified(self):
        st = _state()
        jid = "7079213712@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "bugis-room"
        listing = _listing("bugis-room", budget_floor=900)
        orig_reqs = E.listing_reqs
        orig_status = E._master_status
        E.listing_reqs = lambda: {"bugis-room": listing}
        E._master_status = lambda lk, reqs=None: "active"
        try:
            text = ("姓名：陈美玲\n国籍：马来西亚\n种族：华人\n性别：女\nPass type：EP\n"
                     "入住人数：1\n入住日期：15 十月\n租期：12个月\n预算：950")
            E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": text, "is_from_me": False})
        finally:
            E.listing_reqs = orig_reqs
            E._master_status = orig_status
        self.assertEqual(rec["profile"].get("no_of_pax"), 1)
        self.assertEqual(rec["profile"].get("lease_term_months"), 12)
        self.assertEqual(rec.get("qualify", {}).get("verdict"), "QUALIFIED")


# ---------------------------------------------------------------------------
# chinese-enquiry-gets-english-reply (c2mix02/05/07): the portal boilerplate riding as
# message 0 is always English, so "lang stamped once at first touch" locked the WHOLE
# conversation to English even though every one of the tenant's own words was Chinese.
# ---------------------------------------------------------------------------
class TestChineseEnquiryUpgradesLanguage(unittest.TestCase):
    def test_lang_upgrades_from_english_boilerplate_to_chinese(self):
        st = _state()
        jid = "7079213712@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["lang"] = "en"   # what the pure English portal boilerplate would have stamped
        E.handle_event(st, {"jid": jid, "msg_id": "m1",
            "text": "我想问一下关于房子在哪个区？", "is_from_me": False})
        self.assertEqual(rec["lang"], "zh")

    def test_lang_never_downgrades_from_chinese(self):
        st = _state()
        jid = "7079213713@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["lang"] = "zh"
        E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "ok thanks see you then",
                             "is_from_me": False})
        self.assertEqual(rec["lang"], "zh")

    def test_offer_viewing_localises_after_upgrade(self):
        st = _state()
        jid = "7079213714@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "bugis-room2"
        listing = _listing("bugis-room2", budget_floor=900)
        orig_reqs = E.listing_reqs
        orig_status = E._master_status
        orig_slot = E.next_slot
        E.listing_reqs = lambda: {"bugis-room2": listing}
        E._master_status = lambda lk, reqs=None: "active"
        E.next_slot = lambda lk: {"slot_id": "s1", "label": "Thu 17 Sep, 2pm to 3pm"}
        try:
            E.handle_event(st, {"jid": jid, "msg_id": "m0", "text": "我想问一下这个区",
                                 "is_from_me": False})
            text = ("姓名：陈美玲\n国籍：马来西亚\n种族：华人\n性别：女\nPass type：EP\n"
                     "入住人数：1\n入住日期：15 十月\n租期：12个月\n预算：950")
            a = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": text,
                                     "is_from_me": False})
        finally:
            E.listing_reqs = orig_reqs
            E._master_status = orig_status
            E.next_slot = orig_slot
        self.assertEqual(a["type"], "OFFER_VIEWING")
        self.assertNotIn("Reply YES to take this slot", a["text"])
        self.assertIn("回复YES确认这个时间", a["text"])


# ---------------------------------------------------------------------------
# signoff-thanks-closes-live-lead (c2mix07-chinese-couple-weekend-handreply-qualify idx 6): a
# bare 谢谢 one message after the couple proposed a concrete viewing time was read as a
# withdrawal and closed the record WITHDRAWN with notify=False.
# ---------------------------------------------------------------------------
class TestSignoffAfterProposedTimeNeverCloses(unittest.TestCase):
    def test_thanks_right_after_a_proposed_time_does_not_auto_close(self):
        st = _state()
        jid = "7000279652@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        rec["listing_key"] = "clementi-couple-room"
        rec["qualify"] = {"verdict": "QUALIFIED", "why": []}
        E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "好的，星期六两点可以吗？",
                             "is_from_me": False})
        a = E.handle_event(st, {"jid": jid, "msg_id": "m2", "text": "谢谢",
                                 "is_from_me": False})
        self.assertNotEqual(a and a.get("type"), "AUTO_CLOSED")
        self.assertFalse(rec.get("terminal"))

    def test_bare_thanks_with_no_open_context_still_closes(self):
        # the gate must not swallow a genuine close -- only one right after a proposed time.
        st = _state()
        jid = "6598887000@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        a = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "thanks",
                                 "is_from_me": False})
        self.assertEqual(a["type"], "AUTO_CLOSED")
        self.assertTrue(rec.get("terminal"))

    def test_english_proposed_time_also_gates_the_signoff(self):
        st = _state()
        jid = "6598887001@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["form_sent"] = True
        E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "can do Saturday 3pm",
                             "is_from_me": False})
        a = E.handle_event(st, {"jid": jid, "msg_id": "m2", "text": "thanks",
                                 "is_from_me": False})
        self.assertNotEqual(a and a.get("type"), "AUTO_CLOSED")


# ---------------------------------------------------------------------------
# form-asks-protected-fields-when-gates-any (c3rm01-waive-deposit-handreply-qualify idx 0):
# every bound scenario with gender/ethnicity/nationality ALL "any" still nudged for those
# fields, a conversion tax and an unnecessary PDPA surface.
# ---------------------------------------------------------------------------
class TestFormAsksProtectedFieldsWhenGatesAny(unittest.TestCase):
    def test_all_any_listing_never_nudges_protected_fields(self):
        listing = _listing("any-room")   # gender/ethnicity_rule/nationality_pref all "any"
        miss = E.missing_required({}, listing)
        self.assertNotIn("gender", miss)
        self.assertNotIn("ethnicity", miss)
        self.assertNotIn("nationality", miss)
        self.assertIn("name", miss)
        self.assertIn("budget", miss)
        self.assertIn("lease_term_months", miss)

    def test_a_listing_that_still_gates_on_one_keeps_asking_all_three(self):
        listing = _listing("caspian", gender="male_pref")
        listing["requirements"]["ethnicity_rule"] = {"mode": "exclude", "list": ["Indian"]}
        miss = E.missing_required({}, listing)
        self.assertIn("gender", miss)
        self.assertIn("ethnicity", miss)
        self.assertIn("nationality", miss)

    def test_unbound_profile_still_asks_everything(self):
        self.assertIn("nationality", E.missing_required({}, None))
        self.assertIn("gender", E.missing_required({}, None))

    def test_full_form_is_unchanged_still_asks_all_14_fields(self):
        # Winfred wants the full form regardless -- only the NUDGE/verdict change.
        for label in ("Nationality:", "Ethnicity:", "Gender:", "Age:"):
            self.assertIn(label, E.INTAKE_FORM)


# ---------------------------------------------------------------------------
# unsolicited-channel-plug-third-message (c1-01-3mth-lease-single-or-gf idx 0): SEND_FORM
# fired 3 auto messages back to back on first contact (unit, form, channel plug) before the
# prospect had said anything beyond the portal enquiry.
# ---------------------------------------------------------------------------
class TestChannelPlugFoldedNotThirdMessage(unittest.TestCase):
    def setUp(self):
        self._orig_reqs = E.listing_reqs
        self._orig_unit = E.listing_unit_message
        E.listing_reqs = lambda: {"lk1": _listing("lk1")}
        E.listing_unit_message = lambda lk, lang="en": "Unit info line"

    def tearDown(self):
        E.listing_reqs = self._orig_reqs
        E.listing_unit_message = self._orig_unit

    def test_send_form_is_two_messages_not_three(self):
        st = _state()
        jid = "6598887777@s.whatsapp.net"
        a = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "Hi is this available?",
                                 "is_from_me": False, "listing_key": "lk1"})
        self.assertEqual(a["type"], "SEND_FORM")
        self.assertEqual(len(a["texts"]), 2)

    def test_channel_link_still_present_folded_into_the_form_tail(self):
        st = _state()
        jid = "6598887778@s.whatsapp.net"
        a = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "Hi is this available?",
                                 "is_from_me": False, "listing_key": "lk1"})
        self.assertIn(E.CHANNEL, a["texts"][1])
        self.assertNotIn(E.CHANNEL, a["texts"][0])

    def test_chinese_send_form_also_two_messages_with_folded_channel_line(self):
        st = _state()
        jid = "6598887779@s.whatsapp.net"
        a = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": "你好，请问还有房间吗？",
                                 "is_from_me": False, "listing_key": "lk1"})
        self.assertEqual(a["type"], "SEND_FORM")
        self.assertEqual(len(a["texts"]), 2)
        self.assertIn(E.CHANNEL, a["texts"][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
