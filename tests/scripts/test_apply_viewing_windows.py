"""
test_apply_viewing_windows.py -- a viewing slot on every open listing (11 Sep 2026).
Covers scripts/apply_viewing_windows.py: only confirm:true candidates are written (engine
schema fields only, provenance dropped), --confirm refuses the real live listing index
outright, --dry-run never writes, and a fixture write is atomic with a backup.

Run: /usr/bin/python3 tests/scripts/test_apply_viewing_windows.py
"""
import json, os, sys, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_SANDBOX"] = "1"
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import apply_viewing_windows as A  # noqa: E402


PROPOSED = {
    "cherryhill": {
        "landlord": "A", "id": "LL1", "status": "active", "raw_text": "Sat 9 to 11am",
        "parsed": [
            {"weekday": "sat", "start": "09:00", "end": "11:00", "time_label": "9 to 11am",
             "recurring": True, "date": None, "source_quote": "Sat 9 to 11am",
             "confidence": "high", "confirm": True},
        ],
        "unparsed": [],
    },
    "bayshore": {
        "landlord": "B", "id": "LL2", "status": "active", "raw_text": "Anytime",
        "parsed": [], "unparsed": [{"raw": "Anytime"}],
    },
    "notconfirmed": {
        "landlord": "C", "id": "LL3", "status": "active", "raw_text": "Tue 3pm",
        "parsed": [{"weekday": "tue", "start": "15:00", "end": None, "time_label": "3pm",
                    "recurring": True, "date": None, "source_quote": "Tue 3pm",
                    "confidence": "low", "confirm": False}],
        "unparsed": [],
    },
}


class TestConfirmedUpdates(unittest.TestCase):
    def test_only_confirm_true_candidates_are_selected(self):
        updates, warnings = A._confirmed_updates(PROPOSED)
        self.assertEqual(set(updates.keys()), {"cherryhill"})
        self.assertEqual(warnings, [])

    def test_engine_schema_fields_only_provenance_dropped(self):
        updates, _ = A._confirmed_updates(PROPOSED)
        fv = updates["cherryhill"]
        self.assertEqual(set(fv.keys()), {"weekday", "start", "end", "time_label"})
        self.assertNotIn("confidence", fv)
        self.assertNotIn("source_quote", fv)

    def test_multiple_confirmed_candidates_for_one_listing_warns_and_uses_first(self):
        proposed = {"x": {"parsed": [
            {"weekday": "sat", "start": "09:00", "end": None, "time_label": "9am", "confirm": True},
            {"weekday": "sun", "start": "10:00", "end": None, "time_label": "10am", "confirm": True},
        ]}}
        updates, warnings = A._confirmed_updates(proposed)
        self.assertEqual(updates["x"]["weekday"], "sat")
        self.assertTrue(warnings)


class TestMainDryRunAndConfirm(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.index_path = os.path.join(self._tmp.name, "listing-index.json")
        json.dump({"listings": [
            {"listing_key": "cherryhill", "status": "open"},
            {"listing_key": "bayshore", "status": "open"},
        ]}, open(self.index_path, "w"))
        self.json_path = os.path.join(self._tmp.name, "proposed.json")
        json.dump(PROPOSED, open(self.json_path, "w"))

    def test_dry_run_never_writes(self):
        before = open(self.index_path).read()
        rc = A.main(["--dry-run", self.json_path, "--index", self.index_path])
        self.assertEqual(rc, 0)
        self.assertEqual(open(self.index_path).read(), before)
        self.assertFalse(os.path.exists(self.index_path + ".bak"))

    def test_confirm_writes_fixture_with_backup(self):
        rc = A.main(["--confirm", self.json_path, "--index", self.index_path])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.index_path + ".bak"))
        data = json.load(open(self.index_path))
        by_key = {l["listing_key"]: l for l in data["listings"]}
        self.assertEqual(by_key["cherryhill"]["fixed_viewing"]["weekday"], "sat")
        self.assertNotIn("fixed_viewing", by_key["bayshore"])

    def test_confirm_refuses_the_real_live_index_path(self):
        with mock.patch.object(A, "_REAL_LIVE_INDEX", self.index_path):
            rc = A.main(["--confirm", self.json_path, "--index", self.index_path])
        self.assertEqual(rc, 1)
        # refused before any write -- listing index on disk is unchanged, no backup made
        data = json.load(open(self.index_path))
        by_key = {l["listing_key"]: l for l in data["listings"]}
        self.assertNotIn("fixed_viewing", by_key["cherryhill"])
        self.assertFalse(os.path.exists(self.index_path + ".bak"))

    def test_index_argument_is_required(self):
        with self.assertRaises(SystemExit):
            A.main(["--confirm", self.json_path])


if __name__ == "__main__":
    unittest.main()
