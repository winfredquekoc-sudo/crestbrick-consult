"""
test_extract_viewing_windows.py -- a viewing slot on every open listing (11 Sep 2026).
Covers scripts/extract_viewing_windows.py's parser: the exact WHY example ("Viewing weekday
after 3.30pm, Saturday 9 to 11am, Sunday 9am to noon"), ambiguous text that must never become
a guessed window, one off dated log entries (never auto confirmed even when parsed), and
build_proposals' listing/landlord filtering (open only, active landlord only, skip a listing
that already has fixed_viewing).

Run: /usr/bin/python3 tests/scripts/test_extract_viewing_windows.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import extract_viewing_windows as X


class TestParseViewingAvailability(unittest.TestCase):
    def test_the_why_example(self):
        text = "Viewing weekday after 3.30pm, Saturday 9 to 11am, Sunday 9am to noon"
        parsed, unparsed = X.parse_viewing_availability(text)
        self.assertEqual(unparsed, [])
        by_day = {c["weekday"]: c for c in parsed if c["weekday"] in ("sat", "sun")}
        self.assertEqual(by_day["sat"]["start"], "09:00")
        self.assertEqual(by_day["sat"]["end"], "11:00")
        self.assertEqual(by_day["sat"]["confidence"], "high")
        self.assertEqual(by_day["sun"]["start"], "09:00")
        self.assertEqual(by_day["sun"]["end"], "12:00")
        # "weekday" expands to all 5 weekdays, each "after 3.30pm", medium confidence (a class,
        # not one named day)
        weekdays = [c for c in parsed if c["weekday"] in ("mon", "tue", "wed", "thu", "fri")]
        self.assertEqual(len(weekdays), 5)
        for c in weekdays:
            self.assertEqual(c["start"], "15:30")
            self.assertIsNone(c["end"])
            self.assertEqual(c["confidence"], "medium")

    def test_range_with_am_pm_only_on_the_second_half(self):
        parsed, unparsed = X.parse_viewing_availability("Sat 9-11am")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], "09:00")
        self.assertEqual(parsed[0]["end"], "11:00")
        self.assertEqual(parsed[0]["confidence"], "high")

    def test_day_without_time_is_unparsed(self):
        parsed, unparsed = X.parse_viewing_availability("was offering every Tue, needs fresh dates")
        self.assertEqual(parsed, [])
        self.assertTrue(unparsed)

    def test_time_without_day_is_unparsed(self):
        parsed, unparsed = X.parse_viewing_availability("Available after 8pm")
        self.assertEqual(parsed, [])
        self.assertTrue(unparsed)

    def test_afternoon_never_matches_noon_as_a_substring(self):
        # regression: "afternoon" contains the letters "noon" -- must never be read as a time
        parsed, unparsed = X.parse_viewing_availability("Sat afternoon onwards preferred")
        self.assertEqual(parsed, [])
        self.assertTrue(unparsed)

    def test_vague_text_is_unparsed_never_guessed(self):
        for text in ("Anytime", "Accompanied viewings preferred", "", None):
            parsed, unparsed = X.parse_viewing_availability(text)
            self.assertEqual(parsed, [], f"must never invent a window from {text!r}")

    def test_dated_log_entry_downgraded_to_low_never_auto_confirmed(self):
        parsed, unparsed = X.parse_viewing_availability(
            "Tue 25 Aug 3:15pm (confirmed by landlord 24 Aug 2026; Phontharit viewing)")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["confidence"], "low")


class TestBuildProposals(unittest.TestCase):
    def setUp(self):
        self.index = {"listings": [
            {"listing_key": "openroom", "status": "open"},
            {"listing_key": "closedroom", "status": "closed (tenanted)"},
            {"listing_key": "hasfixed", "status": "open", "fixed_viewing": {"weekday": "sat"}},
        ]}
        self.landlords = {"landlords": [
            {"id": "LL1", "listing_key": "openroom", "landlord_name": "A", "status": "active",
             "viewing_availability": "Saturday 9 to 11am"},
            {"id": "LL2", "listing_key": "closedroom", "landlord_name": "B", "status": "active",
             "viewing_availability": "Saturday 9 to 11am"},
            {"id": "LL3", "listing_key": "hasfixed", "landlord_name": "C", "status": "active",
             "viewing_availability": "Saturday 9 to 11am"},
            {"id": "LL4", "listing_key": "openroom2", "landlord_name": "D",
             "status": "closed (unavailable)", "viewing_availability": "Saturday 9 to 11am"},
        ]}
        self.index["listings"].append({"listing_key": "openroom2", "status": "open"})

    def test_only_active_landlord_with_open_listing_and_no_existing_fixed_viewing(self):
        proposals = X.build_proposals(self.index, self.landlords)
        self.assertIn("openroom", proposals)
        self.assertNotIn("closedroom", proposals)   # listing not open
        self.assertNotIn("hasfixed", proposals)     # already has a fixed_viewing slot
        self.assertNotIn("openroom2", proposals)    # landlord status closed

    def test_default_confirm_on_the_single_high_confidence_candidate(self):
        proposals = X.build_proposals(self.index, self.landlords)
        candidates = proposals["openroom"]["parsed"]
        confirmed = [c for c in candidates if c["confirm"]]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["confidence"], "high")

    def test_missing_viewing_availability_text_is_unparsed_not_crashed(self):
        landlords = {"landlords": [
            {"id": "LL5", "listing_key": "openroom", "landlord_name": "E", "status": "active"},
        ]}
        proposals = X.build_proposals(self.index, landlords)
        self.assertEqual(proposals["openroom"]["parsed"], [])
        self.assertTrue(proposals["openroom"]["unparsed"])


if __name__ == "__main__":
    unittest.main()
