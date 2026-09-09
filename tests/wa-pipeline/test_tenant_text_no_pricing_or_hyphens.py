"""
test_tenant_text_no_pricing_or_hyphens.py -- merge review mandatory fix (9 Sep 2026, four
branch intake merge). Two standing rules, exercised across every tenant facing text builder
found in the merged tree:

  1. NEVER quote a rent/budget figure back at a tenant ("$" immediately followed by a
     digit) -- that is a live negotiation, outside the CEA role boundary. The single
     pre-existing exception is listing_unit_message()/listing_message(), which forwards
     Winfred's own per-listing template verbatim (property-templates.json) -- those
     templates can legitimately state a listing's rent, the same way a portal ad does.
  2. No hyphens or dashes anywhere in tenant facing copy (standing persona rule -- Winfred
     never types them), and no CEA registration number (a WA persona message is never
     CEA-signed).

This does not attempt to introspect the module (too many string literals are internal log
lines, file paths, or inbound classification keywords -- see wa_intake_replies.py's own
module docstring for why intake text is deliberately kept separate from those). Instead it
calls every known tenant facing builder directly with representative fixtures, the same way
TestReplyBuilders / the numbered ok() checks in test_intake_engine.py already do one by one,
and scans the results together.

Run: /usr/bin/python3 tests/wa-pipeline/test_tenant_text_no_pricing_or_hyphens.py
"""
import sys, os, re, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E
import wa_intake_replies as R

_DOLLAR_DIGIT_RE = re.compile(r"[$￡]\s*\d")
_DASH_RE = re.compile(r"[-‐‑‒–—―－]")
_CEA_RE = re.compile(r"\bR\d{6,7}[A-Z]\b", re.I)


class TestNoQuotedFigureOrHyphenInTenantText(unittest.TestCase):
    def _collect(self):
        """Every known tenant facing string, built the same way the real flow builds it."""
        texts = {}

        # ---- intake_engine.py: ask / nudge / needs-info / redirect / viewing / fallback ----
        texts["_ask_text budget+gender"] = E._ask_text(["budget", "gender"])
        texts["_nudge_text budget"] = E._nudge_text(["budget"])
        texts["_needs_info_text"] = E._needs_info_text(["your budget seems low for this unit"])
        texts["_viewing_text with slot"] = E._viewing_text(
            {"label": "Sat 13 Sep, 3pm to 5pm"})
        texts["_viewing_text no slot"] = E._viewing_text(None)
        texts["CHANNEL_PITCH"] = E.CHANNEL_PITCH
        texts["INTAKE_FORM"] = E.INTAKE_FORM
        texts["CLOSING_TEXT_NEW_PLACE"] = E.CLOSING_TEXT_NEW_PLACE
        texts["CLOSING_TEXT_GENERIC"] = E.CLOSING_TEXT_GENERIC

        # ASK_ONE -- every branch: sensitive, generic gap, borderline budget (must now fall
        # through to the SAME figure-free ask), and the "just to double check" lead.
        texts["ask_one sensitive"] = E._ask_one_text(
            ["nationality missing"], [], True)
        texts["ask_one generic budget"] = E._ask_one_text(
            ["budget missing"], ["your budget"], False)
        texts["ask_one borderline budget"] = E._ask_one_text(
            ["budget 680 just under 700"], ["your budget"], False)
        texts["ask_one double check lead"] = E._ask_one_text(
            ["budget missing"], ["your budget"], False, just_flagged_topic="your budget")

        with mock.patch.object(E, "listing_reqs", lambda: {
                "lk1": {"status": "open", "district": "D14",
                        "block_address": "1 Test Ave", "requirements": {}}}), \
             mock.patch.object(E, "_master_status", lambda lk, reqs=None: "active"), \
             mock.patch.object(E, "next_future_slot", lambda lk: None), \
             mock.patch.object(E, "_has_open_future_slot", lambda lk: False):
            texts["redirect no alt"] = E._redirect_text(
                ["house_gate:E1"], {"preferred_location": ""}, E.listing_reqs())
            texts["redirect with exclude"] = E._redirect_text(
                ["house_gate:G1"], {"preferred_location": "D14"},
                E.listing_reqs(), exclude_key="lk1")

        # category 1 factual answers, every fact type that has a real answer to give
        _fact_listing = {"requirements": {
            "cooking": "all", "pets_tenant_may_bring": True, "smoking": "no",
        }, "block_address": "1 Test Ave"}
        for q in ("is cooking allowed?", "can i bring a pet?", "smoking ok?"):
            ans = E._tenant_fact_answer(q, _fact_listing)
            if ans:
                texts[f"_tenant_fact_answer[{q!r}]"] = ans

        # ---- wa_intake_replies.py (category 2) ----
        rec = {"pn": "6590000001", "listing_key": "lk1", "profile": {},
               "form_sent": True, "category2_fired": {}}
        with mock.patch.object(E, "listing_reqs", lambda: {"lk1": {
                "status": "open", "requirements": {"max_pax": 2},
                "block_address": "1 Test Ave", "marketing_restrictions": ""}}), \
             mock.patch.object(E, "next_future_slot", lambda lk: {"label": "Sat 13 Sep, 3pm"}), \
             mock.patch.object(E, "_listing_unavailable", lambda lk: None):
            texts["reply_availability"] = R._reply_availability(rec, "still available?")
            texts["reply_photos_video"] = R._reply_photos_video(rec, "any photos?")
            texts["reply_pax_or_friends known"] = R._reply_pax_or_friends(rec, "couple ok?")
            texts["reply_address_or_unit"] = R._reply_address_or_unit(rec, "what unit?")
        with mock.patch.object(E, "listing_reqs", lambda: {"lk1": {
                "status": "open", "requirements": {}}}):
            texts["reply_pax_or_friends unknown"] = R._reply_pax_or_friends(rec, "couple ok?")
        texts["reply_follow_up_chaser"] = R._reply_follow_up_chaser(rec, "any update?")
        texts["FACT_FALLBACK"] = R._FACT_FALLBACK
        texts["ROOM_GONE_TEXT"] = R._ROOM_GONE_TEXT
        texts["PHOTO_ONLY_KNOWN_TEXT"] = R._PHOTO_ONLY_KNOWN_TEXT
        return texts

    def test_no_quoted_dollar_figure_outside_the_listing_template(self):
        texts = self._collect()
        hits = {k: v for k, v in texts.items() if _DOLLAR_DIGIT_RE.search(v or "")}
        self.assertEqual(hits, {}, f"tenant text quotes a figure: {hits}")

    def test_no_hyphen_or_dash(self):
        texts = self._collect()
        hits = {k: v for k, v in texts.items() if _DASH_RE.search(v or "")}
        self.assertEqual(hits, {}, f"tenant text has a hyphen/dash: {hits}")

    def test_no_cea_number(self):
        texts = self._collect()
        hits = {k: v for k, v in texts.items() if _CEA_RE.search(v or "")}
        self.assertEqual(hits, {}, f"tenant text carries a CEA number: {hits}")

    def test_ask_one_borderline_budget_never_quotes_the_figures(self):
        # the exact mandatory fix: a borderline budget gap used to read "is your $680 budget
        # firm, or could you stretch to $700?" -- must now read exactly like a generic gap.
        borderline = E._ask_one_text(["budget 680 just under 700"], ["your budget"], False)
        generic = E._ask_one_text(["budget missing"], ["your budget"], False)
        self.assertEqual(borderline, generic)
        self.assertNotIn("680", borderline)
        self.assertNotIn("700", borderline)

    def test_ask_one_double_check_lead_uses_a_comma_not_a_hyphen(self):
        out = E._ask_one_text(["budget missing"], ["your budget"], False,
                              just_flagged_topic="your budget")
        self.assertIn("Sorry, just to double check, ", out)
        self.assertNotIn(" - ", out)

    def test_listing_unit_message_is_the_only_allowed_dollar_figure_source(self):
        # documents the one legitimate exception -- a real per-listing rent figure baked
        # into Winfred's own property-templates.json, forwarded verbatim, never generated
        # by any of the builders scanned above.
        import inspect
        src = inspect.getsource(E.listing_unit_message)
        self.assertIn("TEMPLATES", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
