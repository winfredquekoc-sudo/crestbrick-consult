"""
test_owner_text_no_hyphens.py -- item 5 (9 Sep 2026 merge review): "3-5 photos" ->
"3 to 5 photos", "high-quality" -> "high quality", "well-lit" -> "well lit", "24-48 hours"
-> "24 to 48 hours", found by an AST scan of every string constant in the client facing
modules (excluding docstrings and regex literals, which are internal, never sent to anyone).
The AST scan itself is not re-run here as a generic sweep -- a blind hyphen ban across every
string constant in intake_engine.py also flags internal reason/log strings that go to
Winfred's own Telegram, never to a tenant or landlord, and would need a permanent, drifting
allowlist to stay quiet. This file instead locks in the four fixes the scan actually found,
directly against the real landlord facing constants/messages (never a re-typed copy), plus a
narrow no-hyphen scan restricted to just those.

Run: /usr/bin/python3 tests/wa-pipeline/test_owner_text_no_hyphens.py
"""
import sys, os, re, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E

_DASH_RE = re.compile(r"[-‐‑‒–—―－]")


class TestFixedLandlordConstants(unittest.TestCase):
    def test_photo_ask_says_3_to_5_not_3dash5(self):
        self.assertIn("3 to 5 photos", E.LANDLORD_FOLLOW_UP_DAY_5_PHOTOS)
        self.assertNotIn("3-5", E.LANDLORD_FOLLOW_UP_DAY_5_PHOTOS)

    def test_carousell_objection_says_high_quality_and_well_lit(self):
        self.assertIn("high quality", E.LANDLORD_CAROUSELL_OBJECTION)
        self.assertIn("well lit", E.LANDLORD_CAROUSELL_OBJECTION)
        self.assertNotIn("high-quality", E.LANDLORD_CAROUSELL_OBJECTION)
        self.assertNotIn("well-lit", E.LANDLORD_CAROUSELL_OBJECTION)

    def test_no_hyphen_in_any_landlord_followup_or_objection_constant(self):
        for name in ("LANDLORD_FOLLOW_UP_DAY_3", "LANDLORD_FOLLOW_UP_DAY_5_PHOTOS",
                    "LANDLORD_FOLLOW_UP_DAY_7", "LANDLORD_CAROUSELL_OBJECTION"):
            text = getattr(E, name)
            with self.subTest(constant=name):
                self.assertIsNone(_DASH_RE.search(text), f"{name} still has a hyphen: {text!r}")


class TestNinetyNineCoConfirmationSaysDaysToNotDash(unittest.TestCase):
    """The '24 to 48 hours' fix lives inline inside on_landlord_form_completed's
    SEND_CONFIRMATION message, not a top level constant -- drive the real function with
    both dependency modules stubbed out so it reaches that message without needing a real
    matcher/lister or live landlord data."""

    def test_send_confirmation_text_has_no_hyphen_and_says_24_to_48(self):
        fake_matcher = mock.Mock()
        fake_matcher.on_landlord_form_completed = mock.Mock(return_value=[])
        fake_lister = mock.Mock()
        fake_lister.on_landlord_form_completed_for_99co = mock.Mock(return_value={
            "success": True, "url": "https://99.co/rooms/testlisting",
            "listing_key": "testlisting",
        })
        state = {"version": 1, "conversations": {}}
        with mock.patch.object(E, "_try_import_matcher", return_value=fake_matcher), \
             mock.patch.object(E, "_try_import_99co_lister", return_value=fake_lister):
            actions = E.on_landlord_form_completed(state, "6598887777", "form text",
                                                    landlord_name="Test Landlord")
        confirmations = [a for a in actions if a.get("type") == "SEND_CONFIRMATION"]
        self.assertEqual(len(confirmations), 1, actions)
        text = confirmations[0]["text"]
        self.assertIn("24 to 48 hours", text)
        self.assertNotIn("24-48", text)
        self.assertIsNone(_DASH_RE.search(text), f"SEND_CONFIRMATION text has a hyphen: {text!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
