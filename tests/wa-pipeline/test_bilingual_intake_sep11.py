"""
test_bilingual_intake_sep11.py -- coverage for the Chinese tenant first touch (Winfred, 11
Sep 2026): a Chinese enquiry gets the Chinese form instead of the English one, the field
label "Location" became "Preferred location" (old label still parses), and the channel pitch
now names the room count with a floor guard ("more than 30" / "many").

Run: /usr/bin/python3 tests/wa-pipeline/test_bilingual_intake_sep11.py
"""
import sys, os, re, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E
import wa_intake_echo as ECHO
from wa_intake_send import _prelatch_decision

_DASH_RE = re.compile(r"[-‐‑‒–—―－]")

_LISTING = {"listing_key": "lk1", "status": "open", "district": "D14",
            "block_address": "1 Test Ave", "requirements": {}}


def _reqs(n_open):
    """n_open listings marked open, the rest closed -- for the 30 rooms guard."""
    out = {}
    for i in range(max(n_open, 1) + 5):
        key = f"lk{i}"
        out[key] = dict(_LISTING, listing_key=key,
                        status="open" if i < n_open else "closed (tenanted)")
    return out


class TestFormSnapshots(unittest.TestCase):
    def test_english_form_last_field_is_preferred_location(self):
        self.assertTrue(E.INTAKE_FORM.endswith("• Preferred location:"))
        self.assertNotIn("• Location:", E.INTAKE_FORM)

    def test_chinese_form_exact_snapshot(self):
        expected = (
            "请填写以下资料，方便我把您的资料发给房东 :)\n"
            "• 邮箱 Email address:\n"
            "• 姓名 Name:\n"
            "• 国籍 Nationality:\n"
            "• 种族 Ethnicity:\n"
            "• 性别 Gender:\n"
            "• 年龄 Age:\n"
            "• 准证类型 Pass type (SC/PR/EP/S Pass/STP etc):\n"
            "• 职业 Occupation:\n"
            "• 雇佣类型 Employment type (permanent / fixed term / variable):\n"
            "• 入住人数 No. of pax:\n"
            "• 入住日期 Move in date:\n"
            "• 租期 Lease term:\n"
            "• 预算 Budget:\n"
            "• 首选地点 Preferred location:"
        )
        self.assertEqual(E.CHINESE_INTAKE_FORM, expected)

    def test_chinese_form_has_all_14_bilingual_fields(self):
        for x in ("Email address:", "Name:", "Nationality:", "Ethnicity:", "Gender:", "Age:",
                  "Pass type", "Occupation:", "Employment type", "No. of pax:",
                  "Move in date:", "Lease term:", "Budget:", "Preferred location:"):
            self.assertIn(x, E.CHINESE_INTAKE_FORM)


class TestLanguageDetection(unittest.TestCase):
    def setUp(self):
        self._hold = E.listing_reqs
        E.listing_reqs = lambda: _reqs(40)

    def tearDown(self):
        E.listing_reqs = self._hold

    def _send_form(self, text, listing_key="lk1"):
        st = {"version": 1, "conversations": {}}
        jid = "6590000001@s.whatsapp.net"
        a = E.handle_event(st, {"jid": jid, "msg_id": "m1", "text": text, "is_from_me": 0,
                                "listing_key": listing_key})
        rec = st["conversations"]["6590000001"]
        return a, rec

    def test_chinese_enquiry_gets_chinese_form(self):
        a, rec = self._send_form("你好，请问还有房间吗？")
        self.assertEqual(rec["lang"], "zh")
        self.assertTrue(any(E.CHINESE_INTAKE_FORM in t for t in a["texts"]))
        self.assertFalse(any("Pls fill this in" in t for t in a["texts"]))

    def test_english_enquiry_gets_english_form(self):
        a, rec = self._send_form("Hi is this room still available?")
        self.assertEqual(rec["lang"], "en")
        self.assertTrue(any(E.INTAKE_FORM in t for t in a["texts"]))

    def test_english_portal_boilerplate_with_chinese_greeting_gets_chinese_form(self):
        # portal boilerplate in English, but the tenant's own opening words are Chinese --
        # any CJK in the first inbound is enough (Winfred's own wording).
        text = "Hi Winfred, I am interested in: lk1\n你好，还有房间吗"
        a, rec = self._send_form(text)
        self.assertEqual(rec["lang"], "zh")

    def test_lang_stamped_once_and_reused_on_later_messages(self):
        a, rec = self._send_form("你好，还有房间吗")
        self.assertEqual(rec["lang"], "zh")
        # a later, purely English reply must not flip the language mid conversation
        a2 = E.handle_event({"version": 1, "conversations": {"6590000001": rec}},
                             {"jid": "6590000001@s.whatsapp.net", "msg_id": "m2",
                              "text": "Name: Alex", "is_from_me": 0})
        self.assertEqual(rec["lang"], "zh")


class TestMatchersRecogniseChineseForm(unittest.TestCase):
    def test_blank_chinese_form_is_engine_outbound(self):
        self.assertTrue(E.is_engine_outbound(E.CHINESE_INTAKE_FORM))

    def test_blank_chinese_form_is_pasted_blank_intake_form(self):
        self.assertTrue(E.is_pasted_blank_intake_form(E.CHINESE_INTAKE_FORM))

    def test_blank_chinese_form_is_bot_message(self):
        self.assertTrue(E.is_bot_message(E.CHINESE_INTAKE_FORM))

    def test_blank_chinese_form_is_our_echo_never_a_takeover_latch(self):
        self.assertTrue(ECHO._is_our_echo(E.CHINESE_INTAKE_FORM))

    def test_hand_pasted_blank_chinese_form_is_form_pasted_not_latch(self):
        self.assertEqual(_prelatch_decision(E.CHINESE_INTAKE_FORM), "FORM_PASTED")

    def test_filled_chinese_form_is_never_our_echo(self):
        filled = E.CHINESE_INTAKE_FORM.replace("Name:", "Name: Zhang San")
        self.assertFalse(ECHO._is_our_echo(filled))

    def test_filled_chinese_form_no_header_still_latches_as_a_human_forward(self):
        # Winfred forwarding a prospect's filled profile to a landlord by hand never retypes
        # the "请填写以下资料" header -- just the field lines, exactly the existing English
        # precedent (test_intake_engine.py section 21: "FILLED profile, no header -> still
        # human"). _prelatch_decision must say LATCH, same as the English case.
        filled_no_header = E.CHINESE_INTAKE_FORM.split("\n", 1)[1].replace(
            "Name:", "Name: Zhang San")
        self.assertEqual(_prelatch_decision(filled_no_header), "LATCH")
        self.assertFalse(E.is_engine_outbound(filled_no_header))

    def test_filled_chinese_form_with_header_reads_as_engine_send(self):
        # documents existing, deliberate header-prefix behaviour (identical for the English
        # header): retaining the literal header line, even with values filled in, still
        # matches the header prefix in _ENGINE_PREFIXES -- exactly how a filled ENGLISH form
        # that keeps its "Pls fill this in..." header already behaved before this change.
        filled_with_header = E.CHINESE_INTAKE_FORM.replace("Name:", "Name: Zhang San")
        filled_en_with_header = E.INTAKE_FORM.replace("Name:", "Name: Alex Tan")
        self.assertEqual(E.is_engine_outbound(filled_with_header),
                         E.is_engine_outbound(filled_en_with_header))

    def test_extract_profile_on_filled_chinese_form(self):
        filled = (E.CHINESE_INTAKE_FORM
                  .replace("Email address:", "Email address: alex@x.com")
                  .replace("Name:", "Name: Zhang San")
                  .replace("Nationality:", "Nationality: Singaporean")
                  .replace("Ethnicity:", "Ethnicity: Chinese")
                  .replace("Gender:", "Gender: Male")
                  .replace("Age:", "Age: 28")
                  .replace("etc):", "etc): EP")
                  .replace("Occupation:", "Occupation: Engineer")
                  .replace("variable):", "variable): Permanent")
                  .replace("No. of pax:", "No. of pax: 1")
                  .replace("Move in date:", "Move in date: 1 Oct")
                  .replace("Lease term:", "Lease term: 12 months")
                  .replace("Budget:", "Budget: 1500")
                  .replace("Preferred location:", "Preferred location: Bishan"))
        p = E.extract_profile(filled)
        self.assertEqual(p.get("name"), "Zhang San")
        self.assertEqual(p.get("nationality"), "Singaporean")
        self.assertEqual(p.get("ethnicity"), "Chinese")
        self.assertEqual(p.get("gender"), "Male")
        self.assertEqual(p.get("age"), 28)
        self.assertEqual(p.get("pass_type"), "EP")
        self.assertEqual(p.get("no_of_pax"), 1)
        self.assertEqual(p.get("lease_term_months"), 12)
        self.assertEqual(p.get("budget"), 1500)
        self.assertEqual(p.get("preferred_location"), "Bishan")
        self.assertEqual(p.get("email"), "alex@x.com")

    def test_extract_profile_on_old_location_label_still_works(self):
        self.assertEqual(E.extract_profile("Location: Tampines").get("preferred_location"),
                         "Tampines")


class Test30RoomsGuard(unittest.TestCase):
    def setUp(self):
        self._hold = E.listing_reqs

    def tearDown(self):
        E.listing_reqs = self._hold

    def test_thirty_or_more_open_says_more_than_30(self):
        E.listing_reqs = lambda: _reqs(35)
        self.assertEqual(E.channel_pitch("en"), E.CHANNEL_PITCH_EN_30)
        self.assertEqual(E.channel_pitch("zh"), E.CHANNEL_PITCH_ZH_30)
        self.assertIn("more than 30", E.channel_pitch("en"))
        self.assertIn("超过30间", E.channel_pitch("zh"))

    def test_fewer_than_thirty_open_says_many(self):
        E.listing_reqs = lambda: _reqs(12)
        self.assertEqual(E.channel_pitch("en"), E.CHANNEL_PITCH_EN_MANY)
        self.assertEqual(E.channel_pitch("zh"), E.CHANNEL_PITCH_ZH_MANY)
        self.assertIn("many rooms", E.channel_pitch("en"))
        self.assertIn("很多房间", E.channel_pitch("zh"))
        self.assertNotIn("30", E.channel_pitch("en"))

    def test_both_variants_carry_the_channel_link_and_no_dash(self):
        for txt in (E.CHANNEL_PITCH_EN_30, E.CHANNEL_PITCH_EN_MANY,
                    E.CHANNEL_PITCH_ZH_30, E.CHANNEL_PITCH_ZH_MANY):
            self.assertIn(E.CHANNEL, txt)
            self.assertFalse(_DASH_RE.search(txt.split("https://")[0]))


class TestChineseTenantTextNoHyphens(unittest.TestCase):
    def test_no_dash_in_any_new_chinese_text(self):
        texts = [
            E.CHINESE_INTAKE_FORM, E.VIEWING_TICKET_PREFIX_ZH,
            E._viewing_text({"label": "Sat 13 Sep"}, "zh"), E._viewing_text(None, "zh"),
            E._nudge_text(["budget", "name"], "zh"), E._lease_note_text("zh"),
            E.CLOSING_TEXT_NEW_PLACE_ZH, E.CLOSING_TEXT_GENERIC_ZH,
        ]
        hits = {t for t in texts if _DASH_RE.search(t)}
        self.assertEqual(hits, set())

    def test_no_dollar_figure_in_any_new_chinese_text(self):
        _DOLLAR_DIGIT_RE = re.compile(r"[$￡]\s*\d")
        texts = [E._nudge_text(["budget"], "zh"), E._viewing_text({"label": "x"}, "zh")]
        hits = {t for t in texts if _DOLLAR_DIGIT_RE.search(t)}
        self.assertEqual(hits, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
