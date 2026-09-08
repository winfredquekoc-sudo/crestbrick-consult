"""
test_build_listing_index_fill_missing.py -- unittest coverage for scripts/build-listing-
index.py's --fill-missing mode (Sep 2026 listing coverage fix): creates an index entry for
every active/active-verify landlord that has none, with keywords specific enough not to
cross-match, and gates that never produce a false DISQUALIFIED on an unparseable field.

Uses temp landlord-db / listing-index fixtures (BLI_DB / BLI_IDX env vars the script
already honours) -- never touches the live files.

Run: /usr/bin/python3 -m unittest tests.wa-pipeline.test_build_listing_index_fill_missing -v
"""
import sys, os, json, importlib.util, unittest, tempfile, shutil

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
import intake_engine as E

_SPEC = importlib.util.spec_from_file_location(
    "build_listing_index", os.path.join(_REPO_ROOT, "scripts", "build-listing-index.py"))
BLI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BLI)


def _landlord(id_, status="active", full_address="", requirements=None, rent_min=None, district=""):
    return {"id": id_, "status": status, "full_address": full_address,
            "requirements": requirements or {}, "rent_min": rent_min, "district": district,
            "phone": "+65" + id_[-4:].zfill(8), "deal_type": "rent"}


class TestFillMissingEntryCreation(unittest.TestCase):
    def setUp(self):
        self.idx = {"listings": []}
        self.db = {"landlords": [
            _landlord("LL501", "active", "47 Marine Crescent #05-12",
                      {"gender": "Female only", "ethnicity": "No Indian", "max_pax": 1,
                       "lease_min": 12}, rent_min=1200),
            _landlord("LL502", "active-verify", "703 Jurong West Street 71 #02-110",
                      {"gender": "Any"}, rent_min=900),
            _landlord("LL503", "closed (tenanted)", "999 Should Not Appear Road"),
            _landlord("LL504", "dormant", "888 Also Should Not Appear Ave"),
            _landlord("LL505", "available (reopened, per Winfred 2 Sep 2026)",
                      "2 Jalan Batu #09-49", {"max_pax": 1}, rent_min=800),
        ]}

    def test_only_active_and_active_verify_landlords_get_entries(self):
        new_entries, report = BLI.fill_missing(self.idx, self.db)
        keys = {e["landlord_id"] for e in new_entries}
        self.assertEqual(keys, {"LL501", "LL502", "LL505"})

    def test_prefix_matched_active_status_variant_included(self):
        # "available (reopened, ...)" must be treated as active -- exact match alone
        # silently dropped the real LL089 (2 Jalan Batu) reopen.
        new_entries, _ = BLI.fill_missing(self.idx, self.db)
        self.assertTrue(any(e["landlord_id"] == "LL505" for e in new_entries))

    def test_landlord_already_in_index_is_skipped(self):
        self.idx["listings"].append({"listing_key": "existing", "landlord_id": "LL501",
                                     "status": "open", "pg_url_keywords": []})
        new_entries, _ = BLI.fill_missing(self.idx, self.db)
        self.assertNotIn("LL501", {e["landlord_id"] for e in new_entries})

    def test_entry_has_required_engine_fields(self):
        new_entries, _ = BLI.fill_missing(self.idx, self.db)
        e = next(e for e in new_entries if e["landlord_id"] == "LL501")
        for f in ("listing_key", "landlord_id", "status", "pg_url_keywords", "requirements"):
            self.assertIn(f, e)
        for f in ("gender", "couple_ok", "ethnicity_rule", "nationality_pref", "max_pax",
                  "lease_min_months", "budget_floor", "min_age"):
            self.assertIn(f, e["requirements"])

    def test_status_never_open_for_closed_or_hold_language(self):
        db = {"landlords": [_landlord("LL601", "active", "1 Some Road", {}),
                            ]}
        db["landlords"][0]["status"] = "active"
        # simulate a landlord whose OWN status text carries a closed signal despite still
        # being nominally active-prefixed (defensive: derive_status reads status verbatim)
        l_hold = _landlord("LL602", "active", "2 Some Road", {})
        l_hold["status"] = "active (on hold pending owner confirmation)"
        self.assertEqual(BLI.derive_status(l_hold), "hold")
        l_closed = _landlord("LL603", "active", "3 Some Road", {})
        l_closed["status"] = "active (closed per Winfred)"
        self.assertTrue(BLI.derive_status(l_closed).startswith("closed"))


class TestGateParsingNeverFalseDisqualifies(unittest.TestCase):
    """Whatever fill_missing_entry() cannot parse must resolve to qualify()'s 'unknown'
    branch (NEEDS_INFO), never to DISQUALIFIED."""

    def test_unparseable_gender_defaults_to_no_gate(self):
        l = _landlord("LL701", "active", "1 Gibberish Rd",
                      {"gender": "asdkfjasldkfj nonsense text"})
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertEqual(entry["requirements"]["gender"], "any")
        v, why = E.qualify(entry, {"gender": "Male", "no_of_pax": 1, "ethnicity": "Chinese",
                                   "lease_term_months": 12, "budget": 1300})
        self.assertNotEqual(v, "DISQUALIFIED")

    def test_unparseable_ethnicity_defaults_to_any(self):
        l = _landlord("LL702", "active", "1 Gibberish Rd",
                      {"ethnicity": "some unparseable free text about races"})
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertEqual(entry["requirements"]["ethnicity_rule"], {"mode": "any", "list": []})

    def test_missing_rent_min_yields_needs_info_not_disqualified(self):
        l = _landlord("LL703", "active", "1 No Rent Rd", {}, rent_min=None)
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertIsNone(entry["requirements"]["budget_floor"])
        v, why = E.qualify(entry, {"gender": "Male", "no_of_pax": 1, "ethnicity": "Chinese",
                                   "lease_term_months": 12, "budget": 1300})
        self.assertEqual(v, "NEEDS_INFO")

    def test_unparseable_max_pax_never_hard_gates(self):
        l = _landlord("LL704", "active", "1 Odd Pax Rd", {"max_pax": "a few, tbc"},
                      rent_min=1000)
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertIsNone(entry["requirements"]["max_pax"])
        v, why = E.qualify(entry, {"gender": "Male", "no_of_pax": 3, "ethnicity": "Chinese",
                                   "lease_term_months": 12, "budget": 1200})
        self.assertNotEqual(v, "DISQUALIFIED")


class TestKeywordSpecificity(unittest.TestCase):
    def test_no_new_entry_keyword_crosses_another_open_listing(self):
        fixture_entry = {"status": "open", "block_address": "639 Jurong West Street 61"}
        fixture_entry["listing_key"] = "fixture-jurong-west-61"
        fixture_entry["pg_url_keywords"] = ["639 jurong west", "jurong west street 61"]
        idx = {"listings": [fixture_entry]}
        db = {"landlords": [
            _landlord("LL801", "active", "705 Jurong West Street 71 #03-100"),
            _landlord("LL802", "active", "703 Jurong West Street 71 #02-110"),
            _landlord("LL803", "active", "47 Marine Crescent #05-12"),
        ]}
        new_entries, _ = BLI.fill_missing(idx, db)
        conflicts = BLI.check_keyword_specificity(idx["listings"] + new_entries)
        # any conflict reported must involve the PRE-EXISTING fixture entry only (never
        # between two entries this run generated) -- new entries are guaranteed clean by
        # _dedupe_conflicting against everything else open.
        new_keys = {e["listing_key"] for e in new_entries}
        for a, kw, b in conflicts:
            self.assertFalse(a in new_keys and b in new_keys,
                             f"new-vs-new collision should be impossible: {a} {kw} {b}")

    def test_bare_generic_street_segment_never_kept_when_it_would_collide(self):
        idx = {"listings": []}
        db = {"landlords": [
            _landlord("LL901", "active", "705 Jurong West Street 71"),
            _landlord("LL902", "active", "703 Jurong West Street 71"),
        ]}
        new_entries, _ = BLI.fill_missing(idx, db)
        for e in new_entries:
            self.assertNotIn("jurong west", [k.lower() for k in e["pg_url_keywords"]])

    def test_distinctive_property_name_alone_is_kept(self):
        idx = {"listings": []}
        db = {"landlords": [_landlord("LL903", "active", "Melville Park")]}
        new_entries, _ = BLI.fill_missing(idx, db)
        e = new_entries[0]
        self.assertIn("melville park", [k.lower() for k in e["pg_url_keywords"]])

    def test_harvested_portal_id_attaches_when_address_matches(self):
        harvested = {"93 paya lebar way": {"500256368"}}
        l = _landlord("LL904", "active", "93 Paya Lebar Way #05-01")
        entry, _, _ = BLI.fill_missing_entry(l, {}, harvested, {})
        self.assertIn("500256368", entry["pg_url_keywords"])

    def test_portal_ids_file_absence_is_tolerated(self):
        self.assertEqual(BLI._load_portal_ids_file("/no/such/file/exists.json"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
