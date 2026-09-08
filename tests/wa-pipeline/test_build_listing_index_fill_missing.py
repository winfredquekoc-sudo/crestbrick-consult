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
        new_entries, report, _bf = BLI.fill_missing(self.idx, self.db)
        keys = {e["landlord_id"] for e in new_entries}
        self.assertEqual(keys, {"LL501", "LL502", "LL505"})

    def test_prefix_matched_active_status_variant_included(self):
        # "available (reopened, ...)" must be treated as active -- exact match alone
        # silently dropped the real LL089 (2 Jalan Batu) reopen.
        new_entries, _, _bf = BLI.fill_missing(self.idx, self.db)
        self.assertTrue(any(e["landlord_id"] == "LL505" for e in new_entries))

    def test_landlord_already_in_index_is_skipped(self):
        self.idx["listings"].append({"listing_key": "existing", "landlord_id": "LL501",
                                     "status": "open", "pg_url_keywords": []})
        new_entries, _, _bf = BLI.fill_missing(self.idx, self.db)
        self.assertNotIn("LL501", {e["landlord_id"] for e in new_entries})

    def test_entry_has_required_engine_fields(self):
        new_entries, _, _bf = BLI.fill_missing(self.idx, self.db)
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
        new_entries, _, _bf = BLI.fill_missing(idx, db)
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
        new_entries, _, _bf = BLI.fill_missing(idx, db)
        for e in new_entries:
            self.assertNotIn("jurong west", [k.lower() for k in e["pg_url_keywords"]])

    def test_parenthetical_note_never_rides_along_in_a_keyword(self):
        idx = {"listings": []}
        db = {"landlords": [_landlord(
            "LL905", "active",
            "Bukit Batok Street 25, 5 room HDB (unit TBC; Bukit Batok MRT)")]}
        new_entries, _, _bf = BLI.fill_missing(idx, db)
        for kw in new_entries[0]["pg_url_keywords"]:
            self.assertNotIn("unit tbc", kw.lower())
            self.assertNotIn("mrt", kw.lower())

    def test_distinctive_property_name_alone_is_kept(self):
        idx = {"listings": []}
        db = {"landlords": [_landlord("LL903", "active", "Melville Park")]}
        new_entries, _, _bf = BLI.fill_missing(idx, db)
        e = new_entries[0]
        self.assertIn("melville park", [k.lower() for k in e["pg_url_keywords"]])

    def test_harvested_portal_id_attaches_when_address_matches(self):
        harvested = {"93 paya lebar way": {"500256368"}}
        l = _landlord("LL904", "active", "93 Paya Lebar Way #05-01")
        entry, _, _ = BLI.fill_missing_entry(l, {}, harvested, {})
        self.assertIn("500256368", entry["pg_url_keywords"])

    def test_portal_ids_file_absence_is_tolerated(self):
        self.assertEqual(BLI._load_portal_ids_file("/no/such/file/exists.json"), {})

    def test_open_vs_closed_keyword_collision_is_caught(self):
        # A4 (Sep 2026): the reviewer found "ang mo kio ave 3" surviving on both a CLOSED
        # row and an OPEN row -- check_keyword_specificity must check an open listing's
        # keyword against EVERY other entry, not just other open ones.
        closed = {"listing_key": "amk-closed", "status": "closed (tenanted)",
                 "block_address": "123 Ang Mo Kio Ave 3 #04-05",
                 "pg_url_keywords": ["ang mo kio ave 3"]}
        opened = {"listing_key": "amk-open", "status": "open",
                 "block_address": "456 Ang Mo Kio Ave 3 #08-12",
                 "pg_url_keywords": ["ang mo kio ave 3"]}
        conflicts = BLI.check_keyword_specificity([closed, opened])
        self.assertTrue(any(a == "amk-open" and b == "amk-closed" for a, kw, b in conflicts))

    def test_closed_vs_closed_collision_is_not_flagged(self):
        c1 = {"listing_key": "c1", "status": "closed", "block_address": "1 Some Road",
              "pg_url_keywords": ["some road"]}
        c2 = {"listing_key": "c2", "status": "closed (tenanted)", "block_address": "3 Some Road",
              "pg_url_keywords": ["some road"]}
        self.assertEqual(BLI.check_keyword_specificity([c1, c2]), [])

    def test_dedupe_conflicting_prunes_a_new_entry_keyword_that_matches_a_closed_row(self):
        # a fresh fill-missing entry must never keep a keyword that would cross match a
        # CLOSED row's address either -- match_listing() would otherwise route SOME of the
        # traffic to the wrong listing depending on dict order.
        idx = {"listings": [{"listing_key": "amk-closed", "status": "closed (tenanted)",
                            "block_address": "123 Ang Mo Kio Ave 3 #04-05",
                            "pg_url_keywords": ["ang mo kio ave 3", "123 ang mo kio"]}]}
        db = {"landlords": [_landlord("LL910", "active", "456 Ang Mo Kio Ave 3 #08-12")]}
        new_entries, _, _bf = BLI.fill_missing(idx, db)
        new_kws = [k.lower() for k in new_entries[0]["pg_url_keywords"]]
        self.assertNotIn("ang mo kio ave 3", new_kws)


class TestPortalIdsForOnlyReadsPortalIdsField(unittest.TestCase):
    """Opus-review blocker #1: portal_ids_for() must never iterate the OTHER fields of a
    landlord-portal-ids.json record (landlord_name, full_address, rent_by_room) -- that bug
    turned landlord first names, a raw WhatsApp @lid, and free-text rent notes into
    pg_url_keywords."""

    def test_landlord_name_never_becomes_a_keyword(self):
        portal_map = {"LL125": {"landlord_name": "Grace", "full_address": "20 Simei Street 1",
                                 "portal_ids": [], "rent_by_room": "Room 1: $1,250/month"}}
        ids = BLI.portal_ids_for("LL125", "master-room-ll125", portal_map)
        self.assertEqual(ids, set())

    def test_raw_lid_never_becomes_a_keyword(self):
        portal_map = {"LL227": {"landlord_name": "228397905117356", "full_address": "",
                                 "portal_ids": [], "rent_by_room": ""}}
        ids = BLI.portal_ids_for("LL227", "ll227-ll227", portal_map)
        self.assertEqual(ids, set())

    def test_rent_note_paragraph_never_becomes_a_keyword(self):
        portal_map = {"LL017": {"landlord_name": "Asmah", "full_address": "851 Jurong West",
                                 "portal_ids": [],
                                 "rent_by_room": "$800 single F / $1,200 two F (confirmed 7 Aug)"}}
        ids = BLI.portal_ids_for("LL017", "room-851-jurong-west-ll017", portal_map)
        self.assertEqual(ids, set())

    def test_clean_id_inside_portal_ids_list_is_extracted(self):
        portal_map = {"LL188": {"landlord_name": "X", "full_address": "Oxley Edge",
                                 "portal_ids": ["500248513"]}}
        ids = BLI.portal_ids_for("LL188", "oxley-edge-ll188", portal_map)
        self.assertEqual(ids, {"500248513"})

    def test_id_embedded_in_prose_is_extracted_prose_is_not(self):
        portal_map = {"LL188": {"portal_ids": [
            "PropertyGuru listing 500248513 (asking $1,400/month, expired, to be re-listed)"]}}
        ids = BLI.portal_ids_for("LL188", "oxley-edge-ll188", portal_map)
        self.assertIn("500248513", ids)
        self.assertNotIn(
            "PropertyGuru listing 500248513 (asking $1,400/month, expired, to be re-listed)", ids)

    def test_full_regenerate_never_leaks_named_examples_from_review(self):
        """Regression for the exact review finding: regenerate fill-missing entries against a
        portal-ids map shaped like the real landlord-portal-ids.json (bare landlord names, a
        raw @lid, note paragraphs) and assert none of it survives into any keyword."""
        BAD = {"Dan", "Liu", "Grace", "Nicole", "Jeremy", "Relycia", "Behhhh", "Olivia",
               "228397905117356"}
        portal_map = {
            "LL125": {"landlord_name": "Grace", "portal_ids": []},
            "LL146": {"landlord_name": "Nicole", "portal_ids": []},
            "LL169": {"landlord_name": "Relycia", "portal_ids": []},
            "LL216": {"landlord_name": "Jeremy", "portal_ids": []},
            "LL218": {"landlord_name": "Dan", "portal_ids": []},
            "LL219": {"landlord_name": "Liu", "portal_ids": []},
            "LL226": {"landlord_name": "Behhhh", "portal_ids": []},
            "LL227": {"landlord_name": "228397905117356", "portal_ids": []},
        }
        db = {"landlords": [
            _landlord(lid, "active", f"{i+1} Some Road") for i, lid in enumerate(portal_map)
        ]}
        for l in db["landlords"]:
            entry, _, _ = BLI.fill_missing_entry(l, {}, {}, portal_map)
            for kw in entry["pg_url_keywords"]:
                self.assertNotIn(kw, BAD)
                self.assertFalse(any(b.lower() == kw.lower() for b in BAD))


class TestBudgetFloorNeverAutoAppliedFromARange(unittest.TestCase):
    """Opus-review blocker #2: budget_floor stays None unless rent_min == rent_max (both
    parsed ints) -- a stale rent_min/rent_max range must never walk a good lead into a false
    DISQUALIFIED (LL110: rent_min 1300, rent_max 1400, current asking 1000-1100)."""

    def test_mismatched_range_yields_no_floor(self):
        l = _landlord("LL110", "active", "Blk 47 Marine Parade", rent_min=1300)
        l["rent_max"] = 1400
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertIsNone(entry["requirements"]["budget_floor"])

    def test_equal_range_collapses_to_a_floor(self):
        l = _landlord("LL999", "active", "1 Confirmed Rent Rd", rent_min=1200)
        l["rent_max"] = 1200
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertEqual(entry["requirements"]["budget_floor"], 1200)

    def test_missing_rent_max_yields_no_floor(self):
        l = _landlord("LL998", "active", "1 No Max Rd", rent_min=1200)
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, {})
        self.assertIsNone(entry["requirements"]["budget_floor"])


class TestValidKeyword(unittest.TestCase):
    def test_short_bare_name_rejected(self):
        for bad in ("Dan", "Liu", "Grace", "Nicole", "Jeremy", "Relycia", "Behhhh"):
            self.assertFalse(BLI._is_valid_keyword(bad, ""), bad)

    def test_pure_digit_portal_id_accepted(self):
        self.assertTrue(BLI._is_valid_keyword("500248513", ""))

    def test_street_type_word_accepted(self):
        self.assertTrue(BLI._is_valid_keyword("oxley edge", "Oxley Edge, 308 River Valley Road"))

    def test_multiword_address_equal_keyword_accepted(self):
        self.assertTrue(BLI._is_valid_keyword("melville park", "Melville Park"))

    def test_short_generic_word_without_digit_or_street_word_rejected(self):
        self.assertFalse(BLI._is_valid_keyword("olivia", "1 Some Road"))


class TestValidKeywordFilterAppliedInFillMissingEntry(unittest.TestCase):
    """Opus review: _is_valid_keyword existing and being correct is not the same as it being
    APPLIED -- `kws = {k for k in kws if _is_valid_keyword(k, addr)}` in fill_missing_entry
    is the only line that actually calls it on the address-derived candidates, and every
    other test in this file exercises _is_valid_keyword directly or exercises
    portal_ids_for's own field restriction (which never reads landlord_name/portal_ids
    prose to begin with) -- neither would notice that line being deleted. Use a landlord
    whose full_address is itself a bare person name (a real data-entry placeholder before
    the real address is known) so the bad candidate can ONLY be kept out by that filter."""

    def test_bare_person_name_address_and_portal_map_raw_lid_absent_from_keywords(self):
        l = _landlord("LL801", "active", "Amy")   # placeholder address, no real street yet
        portal_map = {"LL801": {"landlord_name": "228397905117356", "portal_ids": []}}
        entry, _, _ = BLI.fill_missing_entry(l, {}, {}, portal_map)
        self.assertEqual(entry["pg_url_keywords"], [])

    def test_same_candidate_would_survive_without_the_filter(self):
        # proves the assertion above is not vacuous: _addr_variants on its own (with no
        # _is_valid_keyword filter applied) does produce the bad candidate.
        self.assertIn("amy", BLI._addr_variants("Amy"))


class TestBayshoreDuplicateDedupe(unittest.TestCase):
    """Opus-review blocker #4: LL088 (Blk 62 Bayshore Park, +6593368817) is the SAME room as
    the pre-existing manual entry "bayshore" (landlord_id LL_JOHNNY_BP62, same phone) --
    dedupe by phone as well as landlord_id, backfill LL088 onto the existing entry, and prune
    the legacy entry's bare "bayshore"/"the bayshore" keywords once a real "66 Bayshore Rd"
    listing (different landlord, different phone) exists so they stop cross-matching it."""

    def _bayshore_idx(self):
        return {"listings": [{
            "listing_key": "bayshore", "landlord_id": "LL_JOHNNY_BP62",
            "landlord_phone": "+6593368817", "status": "open",
            "block_address": "Blk 62 Bayshore Park #15-07",
            "pg_url_keywords": ["bayshore park", "bayshore", "the bayshore",
                                 "blk 62 bayshore", "500170240"],
        }]}

    def test_same_phone_different_id_is_backfilled_not_duplicated(self):
        idx = self._bayshore_idx()
        db = {"landlords": [_landlord("LL088", "active", "Blk 62 Bayshore Park #15-07")]}
        db["landlords"][0]["phone"] = "+6593368817"
        new_entries, _, backfilled = BLI.fill_missing(idx, db)
        self.assertEqual(new_entries, [])
        self.assertEqual(len(backfilled), 1)
        self.assertEqual(idx["listings"][0]["landlord_id"], "LL088")

    def test_legacy_bare_keywords_pruned_once_a_real_bayshore_rd_listing_exists(self):
        idx = self._bayshore_idx()
        db = {"landlords": [_landlord("LL173", "active", "66 Bayshore Rd #22-03")]}
        db["landlords"][0]["phone"] = "+6589772111"   # different landlord, different phone
        new_entries, _, backfilled = BLI.fill_missing(idx, db)
        self.assertEqual(backfilled, [])
        self.assertEqual(len(new_entries), 1)
        legacy_kws = [k.lower() for k in idx["listings"][0]["pg_url_keywords"]]
        self.assertNotIn("bayshore", legacy_kws)
        self.assertNotIn("the bayshore", legacy_kws)
        self.assertIn("bayshore park", legacy_kws)
        self.assertIn("blk 62 bayshore", legacy_kws)
        self.assertIn("500170240", legacy_kws)
        new_kws = [k.lower() for k in new_entries[0]["pg_url_keywords"]]
        self.assertNotIn("bayshore", new_kws)


class TestApplyPathWritesTheMutatedIndex(unittest.TestCase):
    """Opus review blocker: --apply used to re-read the index fresh under the lock
    (idx_live) and write idx_live["listings"] + new_entries, discarding the in-place
    _dedupe_conflicting prune and phone-based landlord_id backfill made to the OUTER idx
    earlier in fill_missing_main -- and it ran check_keyword_specificity against that outer
    (correctly mutated) idx rather than against what actually got written. Exercise the
    exact combined LL088/bayshore scenario from TestBayshoreDuplicateDedupe end to end
    through --apply against temp BLI_DB/BLI_IDX files, and assert the WRITTEN file (not the
    in-memory idx) carries both the backfilled landlord_id and the pruned keywords."""

    def setUp(self):
        self.idx = {"listings": [{
            "listing_key": "bayshore", "landlord_id": "LL_JOHNNY_BP62",
            "landlord_phone": "+6593368817", "status": "open",
            "block_address": "Blk 62 Bayshore Park #15-07",
            "pg_url_keywords": ["bayshore park", "bayshore", "the bayshore",
                                 "blk 62 bayshore", "500170240"],
        }]}
        self.db = {"landlords": [
            # same phone as the legacy "bayshore" entry -> backfill, no new entry
            dict(_landlord("LL088", "active", "Blk 62 Bayshore Park #15-07"),
                 phone="+6593368817"),
            # different landlord/phone, real "66 Bayshore Rd" listing -> new entry, and
            # forces the legacy entry's bare "bayshore"/"the bayshore" keywords to be pruned
            dict(_landlord("LL173", "active", "66 Bayshore Rd #22-03"),
                 phone="+6589772111"),
        ]}
        self.db_path = tempfile.mktemp(suffix=".json")
        self.idx_path = tempfile.mktemp(suffix=".json")
        self.lock_path = tempfile.mktemp(suffix=".lock")
        with open(self.db_path, "w") as f: json.dump(self.db, f)
        with open(self.idx_path, "w") as f: json.dump(self.idx, f)
        self._orig = (BLI.DB, BLI.IDX, BLI.IDX_OUT, BLI.LOCK, sys.argv)
        BLI.DB, BLI.IDX, BLI.IDX_OUT, BLI.LOCK = self.db_path, self.idx_path, None, self.lock_path
        sys.argv = ["build-listing-index.py", "--fill-missing", "--apply"]

    def tearDown(self):
        BLI.DB, BLI.IDX, BLI.IDX_OUT, BLI.LOCK, sys.argv = self._orig
        for p in (self.db_path, self.idx_path, self.lock_path):
            if os.path.exists(p):
                os.remove(p)
        for p in (self.idx_path + ".tmp",):
            if os.path.exists(p):
                os.remove(p)
        import glob
        for p in glob.glob(self.idx_path + ".bak-fillmissing-*"):
            os.remove(p)

    def test_apply_writes_backfilled_id_and_pruned_keywords(self):
        BLI.fill_missing_main()
        written = json.load(open(self.idx_path))
        legacy = next(e for e in written["listings"] if e["listing_key"] == "bayshore")
        self.assertEqual(legacy["landlord_id"], "LL088")
        legacy_kws = [k.lower() for k in legacy["pg_url_keywords"]]
        self.assertNotIn("bayshore", legacy_kws)
        self.assertNotIn("the bayshore", legacy_kws)
        self.assertIn("bayshore park", legacy_kws)
        self.assertIn("blk 62 bayshore", legacy_kws)
        self.assertIn("500170240", legacy_kws)
        self.assertEqual(len(written["listings"]), 2)   # legacy (backfilled) + LL173's new entry


class TestCheckKeywordSpecificityFatal(unittest.TestCase):
    def test_direct_conflict_is_detected(self):
        listings = [
            {"listing_key": "a", "status": "open", "block_address": "705 Jurong West Street 71",
             "pg_url_keywords": ["705 jurong west"]},
            {"listing_key": "b", "status": "open",
             "block_address": "705 Jurong West Street 71 Some Other Unit",
             "pg_url_keywords": []},
        ]
        conflicts = BLI.check_keyword_specificity(listings)
        self.assertTrue(len(conflicts) >= 1)

    def test_fill_missing_main_aborts_and_writes_nothing_on_conflict(self):
        """_dedupe_conflicting is the normal safety net that resolves a conflict before this
        check ever runs -- stub it out (simulating a conflict it missed) to prove
        fill_missing_main treats a surviving conflict as fatal: non-zero exit, no output
        file, instead of the old print-a-warning-and-write-anyway behaviour."""
        idx = {"listings": [{"listing_key": "existing", "status": "open",
                              "block_address": "705 Jurong West Street 71",
                              "pg_url_keywords": ["705 jurong west"]}]}
        db = {"landlords": [_landlord("LL999", "active", "705 Jurong West Street 71 #02-03")]}
        db_path = tempfile.mktemp(suffix=".json")
        idx_path = tempfile.mktemp(suffix=".json")
        out_path = tempfile.mktemp(suffix=".json")
        with open(db_path, "w") as f: json.dump(db, f)
        with open(idx_path, "w") as f: json.dump(idx, f)
        orig_db, orig_idx, orig_out, orig_argv = BLI.DB, BLI.IDX, BLI.IDX_OUT, sys.argv
        BLI.DB, BLI.IDX, BLI.IDX_OUT = db_path, idx_path, out_path
        sys.argv = ["build-listing-index.py", "--fill-missing"]
        try:
            from unittest import mock
            with mock.patch.object(BLI, "_dedupe_conflicting", lambda *a, **k: None):
                with self.assertRaises(SystemExit):
                    BLI.fill_missing_main()
            self.assertFalse(os.path.exists(out_path))
        finally:
            BLI.DB, BLI.IDX, BLI.IDX_OUT = orig_db, orig_idx, orig_out
            sys.argv = orig_argv
            for p in (db_path, idx_path, out_path):
                if os.path.exists(p):
                    os.remove(p)


class TestMarketingRestrictionsSuppressAddressKeywords(unittest.TestCase):
    """A5 (Sep 2026): a landlord record carrying marketing_restrictions (e.g. real LL017 --
    never confirmed a block/unit in writing, two conflicting guesses on file) must never
    surface an address derived pg_url_keyword. Portal ids stay (opaque, not an address)."""

    def test_restricted_listing_gets_no_address_keyword(self):
        l = _landlord("LL017", "active", "851 Jurong West (full addr withheld)",
                      {"gender": "Female only", "ethnicity": "No Indian"})
        l["marketing_restrictions"] = ("Landlord has never provided a written block/unit/"
                                       "postal code. Do NOT publish an address for this "
                                       "listing on any portal until the landlord confirms "
                                       "it in writing.")
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        self.assertEqual(entry["pg_url_keywords"], [])
        self.assertTrue(entry["marketing_restrictions"])

    def test_restricted_listing_keeps_a_portal_id_if_one_exists(self):
        l = _landlord("LL017x", "active", "851 Jurong West (full addr withheld)", {})
        l["marketing_restrictions"] = "Do NOT publish an address until confirmed."
        harvested = {"851 jurong west": {"500999999"}}
        entry, _, _ = BLI.fill_missing_entry(l, {}, harvested, {})
        self.assertEqual(entry["pg_url_keywords"], ["500999999"])

    def test_non_restricted_listing_unaffected(self):
        l = _landlord("LL018", "active", "47 Marine Crescent, Singapore 440047", {})
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        self.assertIn("47 marine crescent", entry["pg_url_keywords"])
        self.assertEqual(entry.get("marketing_restrictions"), "")

    def test_comma_in_address_never_blocks_the_clean_short_keyword(self):
        # real LL110 shape: "Blk 47 Marine Crescent, Singapore 440047 (...)" -- the internal
        # comma must not glue onto "crescent" and block the plain "47 marine crescent" a
        # tenant would actually type.
        variants = BLI._addr_variants("Blk 47 Marine Crescent, Singapore 440047 "
                                      "(Marine Crescent Gardens; high floor)")
        self.assertIn("47 marine crescent", variants)


class TestProtectedGateProvenance(unittest.TestCase):
    """A2 (Sep 2026): a protected attribute gate (gender / ethnicity_rule / nationality_pref)
    is only ever emitted when the landlord record carries the landlord's OWN dated
    statement for it -- a paraphrase ('landlord preference') is never enough. Fixture
    shapes copied from real landlord-db.json records (LL104, LL097, LL117, LL106)."""

    def test_paraphrase_with_no_quote_or_date_is_unverified(self):
        # real LL104 shape: "No Indian (landlord preference); Chinese preferred..." -- no
        # quote marks, no date anywhere in the field or in wa_evidence.
        l = _landlord("LL104", "active", "Blk 125 Bedok Reservoir Road",
                      {"ethnicity": "No Indian (landlord preference); Chinese preferred",
                       "gender": "Any"})
        l["wa_evidence"] = ['[2026-08-26] No use of gas as my house is open kitchen']
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        req = entry["requirements"]
        self.assertEqual(req["ethnicity_rule"], {"mode": "any", "list": []})
        self.assertIn("ethnicity", req["gate_unverified"])

    def test_summary_assertion_with_no_backing_wa_evidence_is_unverified(self):
        # real LL097 (Cherryhill) shape: the requirements text AND the clarity summary both
        # assert an exclusion, but no wa_evidence line actually says it ("no such line in
        # the chat") -- must stay unverified.
        l = _landlord("LL097", "active", "21 Lorong Lew Lian",
                      {"ethnicity": "No Indian, No Bangladesh (landlord preference)",
                       "nationality": "Exclude Indian and Bangladesh (landlord preference)"})
        l["wa_evidence"] = ["[2026-09-03] Yep 1650 for the biggg one"]
        l["clarity"] = {"summary": "No Indian or Bangladeshi tenants. Working professionals only."}
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        req = entry["requirements"]
        self.assertEqual(req["ethnicity_rule"], {"mode": "any", "list": []})
        self.assertEqual(req["nationality_pref"], {"mode": "any", "list": []})
        self.assertEqual(set(req["gate_unverified"]), {"ethnicity", "nationality"})

    def test_derived_from_an_observation_is_unverified_even_with_a_date(self):
        # real LL117 shape: "No Indian (previous tenants all Chinese, stated 31 Aug 2026)"
        # -- has a DATE but no actual quoted phrase (it's an inference, not a quote) and
        # wa_evidence is empty. Must stay unverified despite the date.
        l = _landlord("LL117", "active", "Blk 703 Jurong West",
                      {"ethnicity": "No Indian (previous tenants all Chinese, stated 31 Aug 2026)",
                       "gender": "Male preferred"})
        l["wa_evidence"] = []
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        req = entry["requirements"]
        self.assertEqual(req["ethnicity_rule"], {"mode": "any", "list": []})
        self.assertIn("ethnicity", req["gate_unverified"])
        self.assertIn("gender", req["gate_unverified"])

    def test_wa_evidence_quote_verifies_the_gate(self):
        # real LL106 shape: a genuine dated wa_evidence quote about gender.
        l = _landlord("LL106", "active", "1 Some Road", {"gender": "Female preferred"})
        l["wa_evidence"] = ["[2026-08-30] my husband is not very keen to rent out the room "
                           "to a guy who speaks the same language due to privacy issues"]
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        req = entry["requirements"]
        self.assertNotEqual(req["gender"], "any")
        self.assertNotIn("gender", req["gate_unverified"])
        self.assertEqual(req["gender_source"]["date"], "2026-08-30")

    def test_inline_quote_with_date_verifies_the_gate(self):
        # real LL104 gender shape: 'Any (landlord confirmed "gender is ok", 19 Aug 2026)' --
        # but gender there already parses to "any" so nothing to verify. Use an ethnicity
        # field with the same inline-quote-plus-date shape to exercise the "only" path.
        l = _landlord("LL501x", "active", "1 Some Road",
                      {"ethnicity": 'Chinese only (landlord confirmed "Chinese tenants only '
                                    'please", 3 Sep 2026)'})
        l["wa_evidence"] = []
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        req = entry["requirements"]
        self.assertEqual(req["ethnicity_rule"]["mode"], "only")
        self.assertNotIn("ethnicity", req["gate_unverified"])
        self.assertIn("quote", req["ethnicity_rule"]["source"])
        self.assertEqual(req["ethnicity_rule"]["source"]["date"], "3 Sep 2026")

    def test_no_gate_at_all_never_flagged_unverified(self):
        l = _landlord("LL502x", "active", "1 Some Road", {"ethnicity": "Any", "gender": "Any"})
        entry, _, _ = BLI.fill_missing_entry(l, {}, set(), {})
        self.assertEqual(entry["requirements"]["gate_unverified"], [])

    def test_engine_flags_human_instead_of_redirecting_on_an_unverified_disqualify(self):
        """A2 engine side: qualify()'s ethnicity/nationality/gender DISQUALIFIED path must
        never reach the tenant when the listing's gate for that attribute is unverified --
        it is entirely academic here since fill_missing always downgrades an unverified
        gate to 'any' (so qualify() itself can never fail on it) -- this proves that
        defense in depth: even if a listing is hand edited back to an active exclude/only
        mode while gate_unverified still names the attribute, handle_event blocks the send."""
        listing = {
            "listing_key": "unverified-eth", "status": "active",
            "requirements": {
                "gender": "any", "ethnicity_rule": {"mode": "exclude", "list": ["Indian"]},
                "nationality_pref": {"mode": "any", "list": []}, "max_pax": 2,
                "lease_min_months": 12, "budget_floor": 1000,
                "gate_unverified": ["ethnicity"],
            },
        }
        profile = {"ethnicity": "Indian", "gender": "Male", "no_of_pax": 1,
                   "lease_term_months": 12, "budget": 1200}
        verdict, why = E.qualify(listing, profile)
        self.assertEqual(verdict, "DISQUALIFIED")   # qualify() itself is unchanged
        act = E._house_gate_redirect("6591112222", {"profile": profile}, listing,
                                     {"unverified-eth": listing}, "unverified-eth",
                                     "ethnicity", why)
        self.assertEqual(act["type"], "FLAG_HUMAN")
        self.assertIsNone(act.get("text"))
        self.assertEqual(act["reason"], "house_gate:E1")
        self.assertTrue(act.get("notify") is True)

    def test_engine_offer_viewing_blocked_when_any_gate_unverified(self):
        listing = {
            "listing_key": "unverified-gender", "status": "active",
            "requirements": {"gender": "any", "ethnicity_rule": {"mode": "any", "list": []},
                             "nationality_pref": {"mode": "any", "list": []},
                             "gate_unverified": ["gender"]},
        }
        rec = {"profile": {}}
        act = E._gate_unverified_offer_block("6591112222", rec, listing)
        self.assertIsNotNone(act)
        self.assertEqual(act["type"], "FLAG_HUMAN")
        self.assertEqual(act["reason"], "house_gate:G1")
        self.assertEqual(rec["status"], "house_gate:G1")

    def test_verified_listing_never_blocks_offer(self):
        listing = {"listing_key": "clean", "status": "active",
                   "requirements": {"gender": "any", "ethnicity_rule": {"mode": "any", "list": []},
                                    "nationality_pref": {"mode": "any", "list": []},
                                    "gate_unverified": []}}
        self.assertIsNone(E._gate_unverified_offer_block("659", {"profile": {}}, listing))


if __name__ == "__main__":
    unittest.main(verbosity=2)
