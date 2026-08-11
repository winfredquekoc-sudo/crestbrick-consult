#!/usr/bin/env python3
"""Pure assert test suite for the matchmaker data lane (export_data.py / enrich.py).
No pytest — stdlib only, runs under /usr/bin/python3:
    /usr/bin/python3 tests/matchmaker/test_export.py
Exits 1 if any check fails, 0 if everything passes.

All fixture people below are invented (SG plausible, obviously fake names/phones) —
never real tenant or landlord data. Real data lives only in the gitignored
_templates/ directory and is never read by this file.
"""
import contextlib, datetime, io, json, os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "scripts", "matchmaker"))
sys.path.insert(0, SCRIPTS)
import enrich          # noqa: E402
import export_data as ed  # noqa: E402
import build as bld    # noqa: E402

FAILURES = []
TODAY = datetime.date(2026, 8, 11)


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)

def section(title):
    print(f"\n== {title} ==")


# ---------------------------------------------------------------- fixtures
def fake_tenant(**over):
    base = {
        "id": "T900", "name": "Tan Ah Test", "phone": "90000001", "jid": "9000000190001@lid",
        "gender": "male", "ethnicity": "chinese", "nationality": "Singaporean", "pass_type": "Citizen",
        "occupation": "engineer", "no_of_pax": 1, "district": "D15",
        "preferred_location": "East Coast", "preferred_districts": ["D15"],
        "budget": 1300, "budget_min": "", "budget_max": 1300,
        "move_in_date": "2026-09-01", "lease_term_months": 12,
        "last_contact": "2026-08-10", "listing_enquired": "",
        "excluded": False, "exclude_reason": "",
        "match_status": "", "contact_state": "", "status": "",
    }
    base.update(over)
    return base

def fake_landlord(**over):
    base = {
        "id": "LL900", "landlord_name": "Ong Ah Test", "phone": "90000002",
        "status": "active", "district": "D15", "full_address": "1 Test Ave, Singapore",
        "rent_min": 1200, "rent_max": 1200, "property_type": "HDB common room",
        "rooms_and_rent": "Common $1,200", "listing_key": "test-listing",
        "contact_label_source": "own", "viewing_availability": "weekends",
        "viewing_availability_updated": "2026-08-05", "last_contact": "2026-08-05",
        "follow_up": "", "offer_pending": False,
        "requirements": {"max_pax": 2, "gender": "any", "ethnicity": "no preference"},
    }
    base.update(over)
    return base


# ================================================================== dates
def test_date_normalization():
    section("date normalization")
    check("YYYY-MM-DD passthrough", enrich.norm_date("2026-08-11") == "2026-08-11")
    check("ISO timestamp -> date part", enrich.norm_date("2026-08-11T04:12:41+08:00") == "2026-08-11")
    check("space separated timestamp -> date part", enrich.norm_date("2026-08-11 04:12:41+08:00") == "2026-08-11")
    check("DD/MM/YYYY -> normalized", enrich.norm_date("05/09/2026") == "2026-09-05")
    check("DD-MM-YYYY -> normalized", enrich.norm_date("05-09-2026") == "2026-09-05")
    check("empty string -> None", enrich.norm_date("") is None)
    check("None -> None", enrich.norm_date(None) is None)
    check("garbage text -> None, not NaN/raise", enrich.norm_date("ASAP") is None)
    check("invalid calendar date -> None", enrich.norm_date("2026-13-40") is None)

def test_available_from():
    section("available_from free text parser")
    check("'available from 1 July'", enrich.find_available_from(["available from 1 July"], TODAY) == "2026-07-01")
    check("'avail 7/8 Aug' takes first day", enrich.find_available_from(["avail 7/8 Aug"], TODAY) == "2026-08-07")
    check("'available August 2026' defaults day 1", enrich.find_available_from(["available August 2026"], TODAY) == "2026-08-01")
    check("plain 'from 1 Sep'", enrich.find_available_from(["Master room, from 1 Sep"], TODAY) == "2026-09-01")
    check("no date phrasing -> None", enrich.find_available_from(["Not stated (rent TBC)"], TODAY) is None)
    check("first field wins over second", enrich.find_available_from(["from 1 Sep", "avail 1 Jun"], TODAY) == "2026-09-01")
    check("empty list -> None", enrich.find_available_from([], TODAY) is None)

def test_move_in_normalization():
    section("norm_move_in: tenant move_in free text -> YYYY-MM-DD (real tenant-db phrasings)")
    nmi = enrich.norm_move_in
    check("already clean YYYY-MM-DD passes through norm_date", nmi("2026-09-01", TODAY) == "2026-09-01")
    check("already clean DD/MM/YYYY passes through norm_date", nmi("05/09/2026", TODAY) == "2026-09-05")

    section("immediately/asap/now/anytime -> the build date")
    for phrase in ("Immediately", "immediately", "asap", "ASAP", "now", "anytime", "Anytime"):
        check(f"{phrase!r} -> today", nmi(phrase, TODAY) == TODAY.isoformat(), f"got {nmi(phrase, TODAY)!r}")

    section("early/mid/late <month> [year] (start/middle/end accepted synonyms)")
    check("'Early Sep 2026' -> 5th", nmi("Early Sep 2026", TODAY) == "2026-09-05")
    check("'mid Aug 2026' -> 15th", nmi("mid Aug 2026", TODAY) == "2026-08-15")
    check("'mid Aug' (no year) -> 15th, this year", nmi("mid Aug", TODAY) == "2026-08-15")
    check("'late Sep 2026' -> 25th", nmi("late Sep 2026", TODAY) == "2026-09-25")
    check("'End of September 2026' (end-of synonym) -> 25th", nmi("End of September 2026", TODAY) == "2026-09-25")
    check("'end aug' (no year) -> 25th", nmi("end aug", TODAY) == "2026-08-25")

    section("explicit 'D Month [Year]' -- just needs the month name, day is never defaulted")
    for phrase, expected in [
        ("3 aug", "2026-08-03"), ("12 Sep 2026", "2026-09-12"), ("1 Aug 2026", "2026-08-01"),
        ("9 Aug", "2026-08-09"), ("20 Aug 2026", "2026-08-20"), ("30 September 2026", "2026-09-30"),
        ("15 Sep 2026", "2026-09-15"), ("1 sept", "2026-09-01"), ("5 August 2026", "2026-08-05"),
        ("10 August 2026", "2026-08-10"), ("15 Aug", "2026-08-15"), ("1 September 2026", "2026-09-01"),
    ]:
        check(f"{phrase!r} -> {expected}", nmi(phrase, TODAY) == expected, f"got {nmi(phrase, TODAY)!r}")

    section("bare 'Month Year' / bare 'Month' / bare 'YYYY-MM' -- no day given -> the 15th")
    for phrase, expected in [
        ("October 2026", "2026-10-15"), ("Next week (Jul 2026)", "2026-07-15"),
        ("September", "2026-09-15"), ("August", "2026-08-15"), ("august", "2026-08-15"),
        ("2026-08", "2026-08-15"), ("2026-10", "2026-10-15"), ("2026-06", "2026-06-15"),
    ]:
        check(f"{phrase!r} -> {expected}", nmi(phrase, TODAY) == expected, f"got {nmi(phrase, TODAY)!r}")

    section("year omitted -> rolls to next year only when this year's reading is far in the past")
    check("'3 Jan' read in November -> next year (321d in the past otherwise)",
          nmi("3 Jan", datetime.date(2026, 11, 20)) == "2027-01-03")
    check("'3 Jan' read in the same January -> this year, not rolled",
          nmi("3 Jan", datetime.date(2026, 1, 10)) == "2026-01-03")

    section("unparseable -> None, never a guess (caller keeps the raw string + current default behavior)")
    for phrase in ("flexible", "TBC", "not sure yet", "-", "N/A", "", None):
        check(f"{phrase!r} -> None", nmi(phrase, TODAY) is None, f"got {nmi(phrase, TODAY)!r}")


def test_move_in_norm_export_integration():
    section("build_tenants: move_in stays verbatim for display, move_in_norm is the new normalized field")
    immediate = fake_tenant(id="T960", move_in_date="Immediately")
    explicit_day = fake_tenant(id="T961", move_in_date="15 Sep 2026")
    unparseable = fake_tenant(id="T962", move_in_date="flexible, need to check with family")
    already_clean = fake_tenant(id="T963", move_in_date="2026-09-01")
    blank = fake_tenant(id="T964", move_in_date="")
    tenants, _ = ed.build_tenants(
        [immediate, explicit_day, unparseable, already_clean, blank],
        {"phones": [], "ids": [], "name_markers": []}, None, TODAY)
    by_id = {t["id"]: t for t in tenants}

    check("'Immediately' -> move_in kept verbatim for display", by_id["T960"]["move_in"] == "Immediately")
    check("'Immediately' -> move_in_norm is the build date", by_id["T960"]["move_in_norm"] == TODAY.isoformat())
    check("'15 Sep 2026' -> move_in_norm parses the explicit day", by_id["T961"]["move_in_norm"] == "2026-09-15")
    check("unparseable -> move_in_norm None, move_in still the raw text (current default behavior preserved)",
          by_id["T962"]["move_in_norm"] is None and by_id["T962"]["move_in"] == "flexible, need to check with family")
    check("already-clean date -> move_in_norm matches move_in", by_id["T963"]["move_in_norm"] == by_id["T963"]["move_in"] == "2026-09-01")
    check("blank move_in -> move_in_norm None, missing[] still flags it",
          by_id["T964"]["move_in_norm"] is None and "move_in" in by_id["T964"]["missing"])
    check("move_in_norm present in schema v2 tenant shape", "move_in_norm" in by_id["T960"])


# ================================================================= budget
def test_budget_recovery():
    section("budget text recovery")
    lo, hi, note = enrich.recover_budget(["813 Jellicoe Road Room S$1,200/mo"])
    check("single $ figure recovered", (lo, hi) == (1200, 1200), f"got {(lo, hi)}")
    check("single figure note format", note == "parsed from $1,200", f"got {note!r}")

    lo, hi, note = enrich.recover_budget(["Budget S$1.2k to S$1.5k works"])
    check("range recovered (k shorthand)", (lo, hi) == (1200, 1500), f"got {(lo, hi)}")
    check("range note matches spec's exact example phrasing", note == "parsed from 1.2k to 1.5k", f"got {note!r}")

    lo, hi, note = enrich.recover_budget(["703 Hougang Ave 2"])
    check("address block number NOT mistaken for a budget (no $ sign present)", (lo, hi, note) == (None, None, None))

    lo, hi, note = enrich.recover_budget(["$50 finder's fee only"])
    check("implausibly low $ figure rejected by sanity bounds", (lo, hi, note) == (None, None, None))

    lo, hi, note = enrich.recover_budget(["", None, "Caspian Room S$1,100/mo"])
    check("scans fields in order, skips blanks", (lo, hi) == (1100, 1100), f"got {(lo, hi)}")


# =================================================================== units
def test_units_parser():
    section("units[] parser")
    u = enrich.parse_units("Master $1,500 (1pax), $1,400 couple", "HDB (master, MOP not met)", 1400, 1500)
    types = {x["unit_type"] for x in u}
    check("labeled text -> master unit recognized", "master" in types, f"got {types}")
    m = next(x for x in u if x["unit_type"] == "master")
    check("pax variant prices become a range", (m["rent_min"], m["rent_max"]) == (1400, 1500), f"got {m}")

    u2 = enrich.parse_units("Common 1pax $1,400 / 2pax $1,500; Master 1pax $1,900 / 2pax $2,000",
                             "Condo (master + common rooms)", 1400, 2000)
    types2 = {x["unit_type"] for x in u2}
    check("two labeled unit types both recognized", types2 == {"common", "master"}, f"got {types2}")

    u3 = enrich.parse_units("Not stated", "HDB room", 1000, 1200)
    check("unparseable text -> exactly one fallback unit", len(u3) == 1, f"got {u3}")
    check("fallback mirrors listing rent range", (u3[0]["rent_min"], u3[0]["rent_max"]) == (1000, 1200))
    check("fallback unit_type from property_type heuristic", u3[0]["unit_type"] == "room", f"got {u3[0]}")

    u4 = enrich.parse_units("", "Whole unit 4 bedroom", 5000, 5000)
    check("blank rooms text + whole unit property_type -> whole fallback", u4[0]["unit_type"] == "whole", f"got {u4}")

    always_one = enrich.parse_units(None, None, None, None)
    check("always returns >=1 entry even with nothing at all", len(always_one) == 1, f"got {always_one}")


# =============================================================== agent flag
def test_agent_suspect():
    section("is_agent_suspect")
    check("plain name -> not suspect", enrich.is_agent_suspect("Tan Ah Test") is False)
    check("'PropNex Agent' -> suspect", enrich.is_agent_suspect("Wendy PropNex Agent") is True)
    check("license number pattern -> suspect", enrich.is_agent_suspect("John Tan R012345A") is True)
    check("substring false positive avoided ('Sera' contains 'era')", enrich.is_agent_suspect("Sera Tan") is False)
    check("custom marker list respected", enrich.is_agent_suspect("Bob Realtor", markers=["realtor"]) is True)
    check("custom marker list excludes what's not listed", enrich.is_agent_suspect("Bob Agent", markers=["realtor"]) is False)


# =================================================================== phone
def test_phone_normalize():
    section("normalize_phone")
    check("8 digit local -> +65 prefixed", enrich.normalize_phone("90000001") == "6590000001")
    check("already has 65 -> unchanged shape", enrich.normalize_phone("6590000001") == "6590000001")
    check("spaces and +  stripped", enrich.normalize_phone("+65 9000 0001") == "6590000001")
    check("empty -> empty string not None", enrich.normalize_phone("") == "")
    check("None -> empty string", enrich.normalize_phone(None) == "")


# ==================================================================== lang
def test_lang_detect():
    section("detect_lang")
    check("no messages -> en", enrich.detect_lang([]) == "en")
    check("majority CJK -> zh", enrich.detect_lang(["你好，请问房间还有吗", "好的谢谢", "in stock, ok?"]) == "zh")
    check("majority English -> en", enrich.detect_lang(["is the room still available", "ok thanks", "你好"]) == "en")


# ============================================================ address/rent
def test_address_and_rent_overlap():
    section("normalize_address / rent_overlaps")
    check("case + punctuation insensitive", enrich.normalize_address("1 Test Ave, Singapore") ==
          enrich.normalize_address("1 TEST AVE Singapore"))
    check("overlapping ranges", enrich.rent_overlaps(1000, 1300, 1200, 1500) is True)
    check("non overlapping ranges", enrich.rent_overlaps(1000, 1100, 1400, 1500) is False)
    check("missing bounds on one side leans on address match", enrich.rent_overlaps(None, None, 1000, 1200) is True)


# =============================================================== dup logic
def test_dup_groups_and_dup_of():
    section("dup detection")
    t1 = fake_tenant(id="T901", name="Tan Ah Test", phone="90000001")
    t2 = fake_tenant(id="T902", name="Tan Ah Test Two", phone="+65 9000 0001")  # same phone, different formatting
    t3 = fake_tenant(id="T903", name="Lim Ah Test", phone="90000099")           # unique phone
    tenants = ed.build_tenants([t1, t2, t3], {"phones": [], "ids": [], "name_markers": []}, None, TODAY)[0]
    groups = ed.apply_tenant_dup_groups(tenants)
    by_id = {t["id"]: t for t in tenants}
    check("two tenants sharing a normalized phone get the same dup_group",
          by_id["T901"]["dup_group"] is not None and by_id["T901"]["dup_group"] == by_id["T902"]["dup_group"])
    check("unrelated tenant has no dup_group", by_id["T903"]["dup_group"] is None)
    check("apply_tenant_dup_groups reports 1 group", groups == 1, f"got {groups}")

    l1 = {"id": "LL901", "address": "1 Test Ave, Singapore", "rent_min": 1200, "rent_max": 1200, "dup_of": None}
    l2 = {"id": "LL902", "address": "1 TEST AVE, Singapore", "rent_min": 1150, "rent_max": 1250, "dup_of": None}
    l3 = {"id": "LL903", "address": "9 Other Road, Singapore", "rent_min": 1200, "rent_max": 1200, "dup_of": None}
    n = ed.apply_listing_dup_of([l1, l2, l3])
    check("same address + overlapping rent -> later dup_of points at earlier", l2["dup_of"] == "LL901", f"got {l2}")
    check("different address -> no dup_of", l3["dup_of"] is None)
    check("apply_listing_dup_of reports 1 pair", n == 1, f"got {n}")


# ============================================================== exclusions
def test_exclusion_filter():
    section("exclusion filter (two tier: config = hard drop, suspect flag = kept)")
    kept = fake_tenant(id="T910", name="Tan Ah Test", phone="90000010", excluded=False)
    db_excluded = fake_tenant(id="T911", name="Lim Ah Test", phone="90000011", excluded=True, exclude_reason="not interested")
    phone_excluded = fake_tenant(id="T912", name="Wee Ah Test", phone="90000012")
    id_excluded = fake_tenant(id="T913", name="Koh Ah Test", phone="90000013")
    marker_excluded = fake_tenant(id="T914", name="Wendy PropNex Test", phone="90000014")
    flagged_not_dropped = fake_tenant(id="T915", name="Some Agent Person", phone="90000015")  # 'agent' not in cfg markers below

    cfg = {"phones": ["+65 9000 0012"], "ids": ["T913"], "name_markers": ["propnex"]}
    tenants, excl_counts = ed.build_tenants(
        [kept, db_excluded, phone_excluded, id_excluded, marker_excluded, flagged_not_dropped], cfg, None, TODAY)
    ids_out = {t["id"] for t in tenants}

    check("normal tenant kept", "T910" in ids_out)
    check("tenant-db excluded=True dropped", "T911" not in ids_out)
    check("db exclusion counted", excl_counts["db"] == 1, f"got {excl_counts}")
    check("config phone (normalized) dropped", "T912" not in ids_out)
    check("config phone exclusion counted", excl_counts["config_phone"] == 1, f"got {excl_counts}")
    check("config id dropped", "T913" not in ids_out)
    check("config id exclusion counted", excl_counts["config_id"] == 1, f"got {excl_counts}")
    check("config name_marker dropped", "T914" not in ids_out)
    check("config name_marker exclusion counted", excl_counts["config_name_marker"] == 1, f"got {excl_counts}")
    check("marker not in config -> kept, not dropped", "T915" in ids_out)
    by_id = {t["id"]: t for t in tenants}
    check("kept-but-flagged tenant IS flagged is_agent_suspect (fixed default marker list, independent of config)",
          by_id["T915"]["is_agent_suspect"] is True)
    check("clean tenant NOT flagged", by_id["T910"]["is_agent_suspect"] is False)


# =================================================================== schema
def test_schema_v2_shape():
    section("schema v2 shape — listings")
    landlords = [fake_landlord(id="LL920"), fake_landlord(id="LL921", status="closed (tenanted)")]
    listings = ed.build_listings(landlords, {"D15": "East Coast"}, {}, {}, {}, TODAY)
    check("closed listing filtered out, only 1 remains", len(listings) == 1, f"got {len(listings)}")
    l = listings[0]
    # (73) listing_key/follow_up/confirmed_at cut from the shipped payload — zero
    # reads anywhere and no dedicated correctness test of their own (see export_data.py).
    required = ["id","name","availability","district","address","map_query","rent_min","rent_max","viewing",
                "rooms","property_type","phone","source","gates","req_raw",
                "units","available_from","fixed_viewing","photos","listing_url",
                "first_seen","days_listed","is_cobroke","dup_of","reconfirm_due","lifecycle"]
    missing_keys = [k for k in required if k not in l]
    check("all schema v2 listing keys present", not missing_keys, f"missing {missing_keys}")
    check("units always >=1 entry", isinstance(l["units"], list) and len(l["units"]) >= 1)
    check("first_seen stamped from empty registry", l["first_seen"] == TODAY.isoformat())
    check("days_listed is 0 on first sighting", l["days_listed"] == 0)
    check("is_cobroke reflects source", l["is_cobroke"] is False)
    check("lifecycle mapped for an active listing", l["lifecycle"] == "available", f"got {l['lifecycle']}")

    section("schema v2 shape — tenants")
    tenants, _ = ed.build_tenants([fake_tenant(id="T930")], {"phones": [], "ids": [], "name_markers": []}, None, TODAY)
    t = tenants[0]
    # (73) status/listing_enquired/budget_note cut from the shipped payload — zero
    # reads anywhere and no dedicated correctness test of their own (see export_data.py).
    required_t = ["id","name","preferred_location","preferred_districts","district","budget",
                  "budget_min","budget_max","pax","gender","ethnicity","nationality","pass_type","occupation",
                  "move_in","move_in_norm","lease_months","phone","last_contact",
                  "last_wa","lang","dup_group","missing","intake_complete","work_anchor",
                  "is_agent_suspect"]
    missing_keys_t = [k for k in required_t if k not in t]
    check("all schema v2 tenant keys present", not missing_keys_t, f"missing {missing_keys_t}")
    check("work_anchor always null per recon Q18 (no source field exists)", t["work_anchor"] is None)
    check("last_wa null when wa_conn is None (bridge unavailable)", t["last_wa"] is None)
    check("lang defaults en when bridge unavailable", t["lang"] == "en")
    check("complete fixture -> intake_complete True", t["intake_complete"] is True, f"missing={t['missing']}")

    section("schema v2 shape — health block")
    health = ed.compute_health(listings, tenants)
    for k in ("tenants_missing_budget","tenants_missing_move_in","tenants_missing_pax",
              "tenants_missing_lease_months","tenants_missing_district","listings_unparsed_req_raw"):
        check(f"health has {k}", k in health)

def test_missing_and_intake_complete():
    section("missing[] / intake_complete on a sparse record")
    sparse = fake_tenant(id="T940", budget=None, budget_min="", budget_max=None,
                          move_in_date="", no_of_pax=None, lease_term_months=None, district="")
    tenants, _ = ed.build_tenants([sparse], {"phones": [], "ids": [], "name_markers": []}, None, TODAY)
    t = tenants[0]
    check("missing[] flags all 5 gaps", set(t["missing"]) == {"budget","move_in","pax","lease_months","district"},
          f"got {t['missing']}")
    check("intake_complete False when fields missing", t["intake_complete"] is False)


# ===================================================================== delta
def test_delta_computation():
    section("delta computation")
    prev = {
        "generated": "2026-08-04",
        "listings": [{"id": "LL01", "name": "Old Listing", "availability": "Available"},
                     {"id": "LL02", "name": "Gone Listing", "availability": "Available"}],
        "tenants": [{"id": "T01"}, {"id": "T02"}],
    }
    cur_listings = [{"id": "LL01", "availability": "Offer pending"}, {"id": "LL03", "availability": "Available"}]
    cur_tenants = [{"id": "T01"}, {"id": "T03"}]
    delta = ed.compute_delta(prev, cur_listings, cur_tenants)
    check("prev_generated carried through", delta["prev_generated"] == "2026-08-04")
    check("new tenant detected", delta["new_tenant_ids"] == ["T03"], f"got {delta['new_tenant_ids']}")
    check("new listing detected", delta["new_listing_ids"] == ["LL03"], f"got {delta['new_listing_ids']}")
    check("gone listing detected", delta["gone_listings"] == [{"id": "LL02", "name": "Gone Listing"}],
          f"got {delta['gone_listings']}")
    check("availability change detected",
          delta["availability_changes"] == [{"id": "LL01", "from": "Available", "to": "Offer pending"}],
          f"got {delta['availability_changes']}")
    check("no prev -> delta is None (first run)", ed.compute_delta(None, cur_listings, cur_tenants) is None)


# ============================================================ config files
def test_exclusions_config_autocreate():
    section("load_exclusions_config auto-create")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        path = os.path.join(tmp_dir, "matchmaker-exclusions.json")
        check("file absent before first call", not os.path.exists(path))
        cfg = ed.load_exclusions_config(path)
        check("file created", os.path.exists(path))
        check("default shape has phones/ids/name_markers", set(cfg.keys()) == {"phones", "ids", "name_markers"})
        check("default name_markers matches AGENT_MARKERS_DEFAULT",
              cfg["name_markers"] == list(enrich.AGENT_MARKERS_DEFAULT))
        cfg2 = ed.load_exclusions_config(path)
        check("second call reads back same content, does not reset it", cfg2 == cfg)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_state_files_fail_closed():
    section("hand editable state files fail CLOSED, never silently reset")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        # ---- exclusions: this file is how CEA agents are kept out of the tenant
        # pipeline. A stray comma used to be caught, the user's file overwritten
        # with the default, and the build reported success with every hand added
        # phone/id gone — agents flowing straight back in as prospects.
        excl = os.path.join(tmp_dir, "matchmaker-exclusions.json")
        hand_written = '{"phones": ["+6590000001"], "ids": ["T555"], "name_markers": ["realty"],}'  # trailing comma
        open(excl, "w").write(hand_written)
        raised = False
        try:
            ed.load_exclusions_config(excl)
        except ed.StateFileError:
            raised = True
        check("malformed exclusions file raises instead of defaulting", raised)
        check("malformed exclusions file is left exactly as the user wrote it",
              open(excl).read() == hand_written)

        open(excl, "w").write('["not", "an", "object"]')
        raised = False
        try:
            ed.load_exclusions_config(excl)
        except ed.StateFileError:
            raised = True
        check("a JSON array where an object belongs also fails closed", raised)

        open(excl, "w").write('{"phones": "+6590000001"}')
        raised = False
        try:
            ed.load_exclusions_config(excl)
        except ed.StateFileError:
            raised = True
        check("a scalar where a list belongs fails closed (would silently exclude nobody)", raised)

        open(excl, "w").write('{"phones": ["+6590000001"], "ids": [], "name_markers": []}')
        cfg = ed.load_exclusions_config(excl)
        check("a valid hand edited file is read back untouched", cfg["phones"] == ["+6590000001"])

        # ---- seen registry: the only record of when each unit was first seen.
        # Returning {} for a corrupt file re-stamped every listing as first seen
        # today, zeroing days_listed, clearing every reconfirm_due flag, and then
        # writing that reset over the original.
        seen = os.path.join(tmp_dir, "matchmaker-seen.json")
        check("missing seen registry -> empty dict (first run bootstrap)", ed.load_seen_registry(seen) == {})
        original = '{"LL900": "2026-07-01", broken'
        open(seen, "w").write(original)
        raised = False
        try:
            ed.load_seen_registry(seen)
        except ed.StateFileError:
            raised = True
        check("corrupt seen registry raises instead of resetting every first_seen", raised)
        check("corrupt seen registry is not overwritten", open(seen).read() == original)

        ed.save_seen_registry(seen, {"LL900": "2026-07-01"})
        check("save_seen_registry writes atomically and reads back", ed.load_seen_registry(seen) == {"LL900": "2026-07-01"})
        check("no .tmp file survives an atomic write", not os.path.exists(seen + ".tmp"))

        # The consequence the fail-closed path protects: a reset registry silently
        # clears the 14 day reconfirm prompt on every listing.
        land = [fake_landlord(id="LL901", viewing_availability_updated="", last_contact="")]
        aged = ed.build_listings(land, {}, {}, {}, {"LL901": "2026-06-01"}, TODAY)
        reset = ed.build_listings(land, {}, {}, {}, {}, TODAY)
        check("with a real first_seen the listing is 71 days old and due for reconfirmation",
              aged[0]["days_listed"] == 71 and aged[0]["reconfirm_due"] is True, f"got {aged[0]['days_listed']}")
        check("with a wiped registry the same listing silently reads as brand new",
              reset[0]["days_listed"] == 0 and reset[0]["reconfirm_due"] is False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_key_id_safety():
    section("listing/tenant ids stay safe for the app's cbk_<listing>_<tenant> keys")
    ok_l = [{"id": "LL900"}, {"id": "LL-900"}]
    ok_t = [{"id": "TN900"}, {"id": "TMPabc123"}, {"id": "TN_900"}]
    check("real LL###/TN### ids pass", ed.validate_key_ids(ok_l, ok_t) == [])
    check("a tenant id may hold an underscore (it is the trailing key segment)",
          ed.validate_key_ids([], [{"id": "TN_900"}]) == [])

    # "cbk_LL_1_TN1" parses back as listing "LL" + tenant "1_TN1" — the mark is
    # written under one pair and read under another.
    bad = ed.validate_key_ids([{"id": "LL_1"}], [])
    check("a listing id with an underscore is rejected", len(bad) == 1 and "LL_1" in bad[0], f"got {bad}")
    check("an empty listing id is rejected", len(ed.validate_key_ids([{"id": ""}], [])) == 1)
    check("a None listing id is rejected", len(ed.validate_key_ids([{"id": None}], [])) == 1)
    check("a listing id with a colon is rejected (the reveal log's own separator)",
          len(ed.validate_key_ids([{"id": "LL:1"}], [])) == 1)
    check("listing id 'offer' is rejected — cbk_offer_<tenant> is a reserved key",
          len(ed.validate_key_ids([{"id": "offer"}], [])) == 1)
    check("listing id 'backup' is rejected — the daily auto backup would overwrite the mark",
          len(ed.validate_key_ids([{"id": "BACKUP"}], [])) == 1)
    check("a tenant id with a colon is rejected", len(ed.validate_key_ids([], [{"id": "TN:1"}])) == 1)
    check("every offending id is reported, not just the first",
          len(ed.validate_key_ids([{"id": "LL_1"}, {"id": "LL_2"}], [{"id": "TN 3"}])) == 3)


def test_photo_url_join():
    section("load_photo_url_index join by landlord id suffix")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        path = os.path.join(tmp_dir, "listings.json")
        json.dump({"listings": [
            {"id": "common-room-testville-ll930", "image": "/img/listings/testville-1.jpg", "url": "https://wa.me/6590000000"},
            {"id": "master-room-testville-ll930", "image": "/img/listings/testville-2.jpg", "url": "https://wa.me/6590000000"},
        ]}, open(path, "w"))
        idx = enrich.load_photo_url_index(path)
        check("joins on landlord id suffix (case insensitive)", "LL930" in idx)
        check("photos collected across all matching rows, absolute URLs", idx["LL930"]["photos"] ==
              ["https://winfredquek.com/img/listings/testville-1.jpg",
               "https://winfredquek.com/img/listings/testville-2.jpg"], f"got {idx['LL930']}")
        check("listing_url passed through unchanged when already absolute",
              idx["LL930"]["listing_url"] == "https://wa.me/6590000000")
        check("unmatched landlord id -> not in index", "LL999" not in idx)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_abs_url():
    section("abs_url")
    check("relative path prefixed", enrich.abs_url("/img/x.jpg") == "https://winfredquek.com/img/x.jpg")
    check("absolute https passthrough", enrich.abs_url("https://wa.me/123") == "https://wa.me/123")
    check("empty -> None", enrich.abs_url("") is None)
    check("None -> None", enrich.abs_url(None) is None)


# ============================================================ batch 3: data
def test_lifecycle_mapping():
    section("lifecycle status mapping (65)")
    check("active -> available", ed.lifecycle({"status": "active"}) == "available")
    check("channel -> available", ed.lifecycle({"status": "channel"}) == "available")
    check("active-verify -> available", ed.lifecycle({"status": "active-verify"}) == "available")
    check("offer_pending flag -> offer_pending", ed.lifecycle({"status": "active", "offer_pending": True}) == "offer_pending")
    check("closed (tenanted...) -> tenanted", ed.lifecycle({"status": "closed (tenanted 1 aug)"}) == "tenanted")
    check("closed (unavailable...) -> paused", ed.lifecycle({"status": "closed (unavailable)"}) == "paused")
    check("plain closed -> paused", ed.lifecycle({"status": "closed"}) == "paused")
    check("archived -> paused", ed.lifecycle({"status": "archived"}) == "paused")
    check("cold -> renewal_watch", ed.lifecycle({"status": "cold"}) == "renewal_watch")
    check("blank status -> unknown", ed.lifecycle({"status": ""}) == "unknown")
    check("unrecognised status -> unknown", ed.lifecycle({"status": "something else"}) == "unknown")
    check("closed status outranks offer_pending flag (matches availability()'s own priority)",
          ed.lifecycle({"status": "closed", "offer_pending": True}) == "paused")


def test_supply_overview():
    section("build_supply_overview: every landlord kept regardless of status (65)")
    landlords = [
        fake_landlord(id="LL950", status="active"),
        fake_landlord(id="LL951", status="closed (tenanted 1 aug)"),
        fake_landlord(id="LL952", status="cold"),
    ]
    overview = ed.build_supply_overview(landlords)
    check("none dropped, unlike listings[] which filters to available+offer_pending",
          {s["id"] for s in overview} == {"LL950", "LL951", "LL952"}, f"got {[s['id'] for s in overview]}")
    by_id = {s["id"]: s for s in overview}
    check("lifecycle correctly mapped per entry",
          by_id["LL950"]["lifecycle"] == "available" and by_id["LL951"]["lifecycle"] == "tenanted"
          and by_id["LL952"]["lifecycle"] == "renewal_watch", f"got {by_id}")

    section("build_listings: unaffected — still filtered to available+offer_pending only")
    listings = ed.build_listings(landlords, {"D15": "East Coast"}, {}, {}, {}, TODAY)
    check("only the active landlord appears in the app's own listings[]",
          [l["id"] for l in listings] == ["LL950"], f"got {[l['id'] for l in listings]}")


def test_load_busy_blocks():
    section("load_busy_blocks: optional passthrough, never validated (68)")
    check("missing file -> None", ed.load_busy_blocks("/nonexistent/path/matchmaker-test-busy-blocks.json") is None)
    check("blank path -> None", ed.load_busy_blocks("") is None)
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        payload = {"blocks": [{"start": "2026-08-12T10:00:00+08:00", "end": "2026-08-12T12:00:00+08:00"}]}
        path = os.path.join(tmp_dir, "busy-blocks.json")
        json.dump(payload, open(path, "w"))
        check("valid file passed through unchanged (shape not our concern)", ed.load_busy_blocks(path) == payload)
        bad_path = os.path.join(tmp_dir, "bad.json")
        open(bad_path, "w").write("{not valid json")
        check("invalid JSON -> None, never raises", ed.load_busy_blocks(bad_path) is None)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_build_history_entry_shape():
    section("build_history_entry shape (62)")
    data = {
        "generated_ts": "2026-08-11T09:00:00+08:00",
        "counts": {"available_listings": 12, "still_looking_tenants": 30},
        "health": {"tenants_missing_budget": 2, "tenants_missing_move_in": 1, "tenants_missing_pax": 0,
                   "tenants_missing_lease_months": 0, "tenants_missing_district": 1, "listings_unparsed_req_raw": 3},
    }
    computed = {"worklist_size": 18, "top_matches": []}
    now = datetime.datetime(2026, 8, 11, 9, 0, 0)
    entry = bld.build_history_entry(data, computed, now)
    check("exactly the 5 expected keys", set(entry.keys()) == {"ts", "listings", "tenants", "worklist_size", "health_total"},
          f"got {set(entry.keys())}")
    check("ts carried from generated_ts", entry["ts"] == "2026-08-11T09:00:00+08:00")
    check("listings from counts", entry["listings"] == 12)
    check("tenants from counts", entry["tenants"] == 30)
    check("worklist_size from computed", entry["worklist_size"] == 18)
    check("health_total sums the health dict", entry["health_total"] == 7, f"got {entry['health_total']}")
    check("computed=None -> worklist_size None, never raises",
          bld.build_history_entry(data, None, now)["worklist_size"] is None)
    check("json serialisable (what actually gets appended to the jsonl)",
          isinstance(json.dumps(entry), str))


def test_build_history_tail_roundtrip():
    section("build history JSONL append + last-7 tail read (62)")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        path = os.path.join(tmp_dir, "matchmaker-build-history.jsonl")
        check("missing file -> empty list, not an error", bld.read_build_history_tail(path, 7) == [])
        for i in range(10):
            bld.append_build_history(path, {"ts": f"2026-08-{i+1:02d}", "listings": i, "tenants": i,
                                             "worklist_size": i, "health_total": 0})
        check("file created", os.path.exists(path))
        tail = bld.read_build_history_tail(path, 7)
        check("tail returns exactly 7 of 10 appended", len(tail) == 7, f"got {len(tail)}")
        check("tail is the LAST 7 in append order (oldest first, newest last)",
              [e["listings"] for e in tail] == list(range(3, 10)), f"got {[e['listings'] for e in tail]}")
        with open(path, "a") as f:
            f.write("not json\n")
        check("a corrupt trailing line is dropped, never raises — asking for the last 2 lines still yields the 1 good one",
              bld.read_build_history_tail(path, 2) == [{"ts": "2026-08-10", "listings": 9, "tenants": 9,
                                                          "worklist_size": 9, "health_total": 0}])
        check("asking for exactly the corrupt line alone -> empty, not an exception",
              bld.read_build_history_tail(path, 1) == [])
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_atomic_writes():
    section("build side writes are atomic — a kill mid write never eats the last good file")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        p = os.path.join(tmp_dir, "matchmaker.html")
        bld.atomic_write_text(p, "<html>good</html>")
        check("writes the file", open(p).read() == "<html>good</html>")

        # A failure part way through must leave the PREVIOUS content in place —
        # open(p, "w") truncates first, so it could not honour that.
        raised = False
        try:
            bld.atomic_write_text(p, object())   # f.write() raises on a non string
        except TypeError:
            raised = True
        check("a failed write raises", raised)
        check("the previous good file is byte for byte intact", open(p).read() == "<html>good</html>")
        check("no partial file is left behind",
              [f for f in os.listdir(tmp_dir) if f != "matchmaker.html"] == [],
              f"got {os.listdir(tmp_dir)}")

        bld.atomic_write_text(p, "<html>newer</html>")
        check("a later write replaces it cleanly", open(p).read() == "<html>newer</html>")

        # snapshot_prev/restore_prev copy the payload the delta is computed
        # against; a truncated copy there silently kills the next build's delta.
        src, dst = os.path.join(tmp_dir, "a.json"), os.path.join(tmp_dir, "b.json")
        open(src, "w").write('{"generated":"2026-08-11"}')
        bld.atomic_copy(src, dst)
        check("atomic_copy round trips", open(dst).read() == '{"generated":"2026-08-11"}')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_build_history_append_durability():
    section("build history JSONL survives a truncated tail")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        path = os.path.join(tmp_dir, "state", "matchmaker-build-history.jsonl")
        bld.append_build_history(path, {"ts": "2026-08-09", "listings": 1, "tenants": 1, "worklist_size": 1, "health_total": 0})
        bld.append_build_history(path, {"ts": "2026-08-10", "listings": 2, "tenants": 2, "worklist_size": 2, "health_total": 1})
        check("appends one line per build", len(bld.read_build_history_tail(path)) == 2)

        # A run killed part way through its write leaves a line with no newline.
        # Appending straight onto it would glue two records into one unreadable
        # line and lose BOTH; the leading newline keeps the damage to the one
        # record that was actually interrupted.
        with open(path, "a") as f:
            f.write('{"ts": "2026-08-10T23:59", "listi')
        bld.append_build_history(path, {"ts": "2026-08-11", "listings": 3, "tenants": 3, "worklist_size": 3, "health_total": 2})
        tail = bld.read_build_history_tail(path)
        check("the two intact records before the truncated one survive",
              [t["ts"] for t in tail[:2]] == ["2026-08-09", "2026-08-10"], f"got {[t.get('ts') for t in tail]}")
        check("the record written after the truncated line is readable",
              tail[-1]["ts"] == "2026-08-11", f"got {tail[-1]}")
        check("only the interrupted line is lost", len(tail) == 3, f"got {len(tail)}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _sandbox_build(tmp_dir, payloads, inline_fails_on=()):
    """Drive bld.main() against a temp sandbox with the slow/external steps
    stubbed, once per payload in `payloads`. Returns a list of per run records:
    {aborted, prev_seen_by_export, out_generated}. `inline_fails_on` is a set of
    run indexes where inline_and_write should abort the way a real app.js syntax
    error or a missing template placeholder does."""
    saved = {k: getattr(bld, k) for k in
             ("OUT", "PREV", "ROOT", "BUILD_HISTORY_PATH", "STATS_PATH",
              "run_tests", "run_export", "compute_scoring_stats", "inline_and_write", "write_digest")}
    saved_argv = sys.argv
    runs = []
    try:
        bld.OUT = os.path.join(tmp_dir, "matchmaker-data.json")
        bld.PREV = os.path.join(tmp_dir, "matchmaker-data.prev.json")
        bld.ROOT = tmp_dir
        bld.BUILD_HISTORY_PATH = os.path.join(tmp_dir, "state", "build-history.jsonl")
        bld.STATS_PATH = os.path.join(tmp_dir, "state", "stats.json")
        bld.run_tests = lambda: None
        bld.compute_scoring_stats = lambda data: {"top_matches": [], "worklist_size": 1}
        bld.write_digest = lambda data, root: None
        sys.argv = ["build.py"]

        for i, payload in enumerate(payloads):
            rec = {"aborted": False, "prev_seen_by_export": None}

            def run_export(_p=payload, _rec=rec):
                # export_data.py reads PREV to compute its delta — capture what
                # it would have seen, then write the new payload the way the real
                # export does (atomically, same directory).
                if os.path.exists(bld.PREV):
                    _rec["prev_seen_by_export"] = json.load(open(bld.PREV)).get("generated")
                json.dump(_p, open(bld.OUT, "w"))

            def inline_and_write(data, now, _i=i):
                if _i in inline_fails_on:
                    bld.fail("simulated: app.js is not valid JavaScript, refusing to ship")
                return data, os.path.join(tmp_dir, "matchmaker.html"), 1.0

            bld.run_export = run_export
            bld.inline_and_write = inline_and_write
            # main() narrates to stdout/stderr; a sandbox run's narration would
            # read as though the real build had run inside the test output.
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    bld.main()
            except SystemExit as e:
                rec["aborted"] = e.code != 0
            rec["out_generated"] = json.load(open(bld.OUT)).get("generated") if os.path.exists(bld.OUT) else None
            rec["history"] = bld.read_build_history_tail(bld.BUILD_HISTORY_PATH, 50)
            runs.append(rec)
        return runs
    finally:
        for k, v in saved.items():
            setattr(bld, k, v)
        sys.argv = saved_argv


def _payload(generated, now, listings=3):
    # generated_ts has to be genuinely fresh (validate_payload rejects anything
    # older than 5 minutes), so runs are told apart by their listing count
    # instead of by their stamp.
    return {
        "schema_version": 2, "generated": generated,
        "generated_ts": now.isoformat(timespec="seconds"), "build_id": "test" + generated[-2:],
        "counts": {"available_listings": listings, "still_looking_tenants": 4},
        "source_counts": {}, "health": {"tenants_missing_budget": 1},
        "delta": None, "listings": [{"id": "LL900"}], "tenants": [{"id": "T900"}],
    }


def test_aborted_build_never_advances_the_delta_baseline():
    section("an aborted build leaves the last GOOD payload as the delta baseline (10)")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        runs = _sandbox_build(
            tmp_dir,
            [_payload("2026-08-09", now, listings=3),
             _payload("2026-08-10", now, listings=99),   # the run that dies
             _payload("2026-08-11", now, listings=5)],
            inline_fails_on={1},          # the middle build dies after a valid export
        )
        check("run 1 ships", runs[0]["aborted"] is False)
        check("run 2 aborts at inlining", runs[1]["aborted"] is True)
        check("run 3 ships", runs[2]["aborted"] is False)

        check("the aborted run restores the last shipped payload rather than leaving its own",
              runs[1]["out_generated"] == "2026-08-09", f"got {runs[1]['out_generated']}")
        # This is the whole point: the delta in the NEXT artifact must cover
        # everything since the last build Winfred actually received, not since a
        # payload that was exported and thrown away.
        check("the next build's delta compares against the last SHIPPED build",
              runs[2]["prev_seen_by_export"] == "2026-08-09", f"got {runs[2]['prev_seen_by_export']}")

        # and the history log only records builds that were really shipped —
        # the aborted run's distinctive listing count (99) never appears
        counts = [h["listings"] for h in runs[2]["history"]]
        check("the aborted build writes no history entry", counts == [3, 5], f"got {counts}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_successful_build_history_is_unchanged():
    section("a shipped build still records itself, and still shows itself in the artifact")
    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        captured = {}
        saved_inline = bld.inline_and_write
        try:
            runs = _sandbox_build(tmp_dir, [_payload(f"2026-08-{d:02d}", now) for d in range(4, 13)])
        finally:
            bld.inline_and_write = saved_inline
        check("every run ships", all(not r["aborted"] for r in runs))
        check("one history line per shipped build", len(runs[-1]["history"]) == 9, f"got {len(runs[-1]['history'])}")
        check("history file is append only, oldest first",
              [h["listings"] for h in runs[-1]["history"]] == [3] * 9)
        check("the tail the artifact carries is still capped at BUILD_HISTORY_TAIL",
              len(bld.read_build_history_tail(os.path.join(tmp_dir, "state", "build-history.jsonl"))) == bld.BUILD_HISTORY_TAIL)
        check("stats file was written for the shipped build", os.path.exists(os.path.join(tmp_dir, "state", "stats.json")))
        _ = captured
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ring_same_minute_rebuild():
    section("artifact ring: two builds in the same minute keep two copies (61)")
    a = bld.ring_filename(datetime.datetime(2026, 8, 11, 9, 30, 12))
    b = bld.ring_filename(datetime.datetime(2026, 8, 11, 9, 30, 47))
    check("same minute rebuilds produce different filenames", a != b, f"{a} vs {b}")
    check("both are recognised as ring copies", bool(bld.RING_RE.match(a)) and bool(bld.RING_RE.match(b)))
    check("seconds are part of the name", a == "matchmaker-20260811-093012.html", f"got {a}")

    # Copies written by earlier builds are HHMM only. They must still be
    # prunable (otherwise they accumulate forever, unrecognised) and must sort
    # into the right chronological slot next to the new HHMMSS names.
    old = "matchmaker-20260811-0930.html"
    check("an older HHMM name is still recognised", bool(bld.RING_RE.match(old)))
    kept, pruned = bld.select_ring_prune([b, old, a, "matchmaker-20260810-2359.html", "matchmaker.html"], keep=3)
    check("mixed width names sort oldest to newest",
          kept == ["matchmaker-20260811-0930.html", a, b], f"got {kept}")
    check("the oldest is the one pruned", pruned == ["matchmaker-20260810-2359.html"], f"got {pruned}")

    tmp_dir = tempfile.mkdtemp(prefix="matchmaker-test-")
    try:
        bld.update_artifact_ring(tmp_dir, datetime.datetime(2026, 8, 11, 9, 30, 12), "<html>first</html>")
        bld.update_artifact_ring(tmp_dir, datetime.datetime(2026, 8, 11, 9, 30, 47), "<html>second</html>")
        names = sorted(f for f in os.listdir(tmp_dir) if bld.RING_RE.match(f))
        check("a rebuild within the same minute adds a copy instead of replacing one", len(names) == 2, f"got {names}")
        check("the first build's artifact is still readable", open(os.path.join(tmp_dir, names[0])).read() == "<html>first</html>")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_anomaly_guard_math():
    section("anomaly guard: >30% swing detection (63)")
    prev = {"available_listings": 20, "still_looking_tenants": 40}
    check("small swing -> no warnings", bld.check_anomalies(prev, {"available_listings": 21, "still_looking_tenants": 41}) == [])
    warn = bld.check_anomalies(prev, {"available_listings": 5, "still_looking_tenants": 41})
    check("30%+ DROP in listings flagged, tenants untouched", len(warn) == 1 and "listings" in warn[0], f"got {warn}")
    warn2 = bld.check_anomalies(prev, {"available_listings": 20, "still_looking_tenants": 90})
    check("30%+ RISE in tenants flagged, listings untouched", len(warn2) == 1 and "tenants" in warn2[0], f"got {warn2}")
    check("exactly at the 30% boundary NOT flagged (strictly greater than)",
          bld.check_anomalies({"available_listings": 100, "still_looking_tenants": 100},
                               {"available_listings": 130, "still_looking_tenants": 100}) == [])
    check("no prev counts (first run) -> no warnings, never blocks the build",
          bld.check_anomalies({}, {"available_listings": 5, "still_looking_tenants": 5}) == [])
    check("prev count of 0 with a new nonzero count -> flagged (inf swing), never raises",
          len(bld.check_anomalies({"available_listings": 0, "still_looking_tenants": 10},
                                   {"available_listings": 3, "still_looking_tenants": 10})) == 1)
    check("prev 0, new 0 -> not flagged (nothing changed)",
          bld.check_anomalies({"available_listings": 0, "still_looking_tenants": 10},
                               {"available_listings": 0, "still_looking_tenants": 10}) == [])


def test_ring_pruning():
    section("artifact ring pruning: keep newest 3 (61)")
    existing = ["matchmaker-20260805-0900.html", "matchmaker-20260806-0900.html",
                "matchmaker-20260807-0900.html", "matchmaker-20260808-0900.html",
                "matchmaker.html", "matchmaker-data.json", "matchmaker-data.prev.json",
                "matchmaker-digest.html"]
    kept, pruned = bld.select_ring_prune(existing, keep=3)
    check("non ring shaped filenames never touched",
          all(f not in kept and f not in pruned for f in
              ("matchmaker.html", "matchmaker-data.json", "matchmaker-data.prev.json", "matchmaker-digest.html")))
    check("keeps exactly the 3 newest, oldest-to-newest order",
          kept == ["matchmaker-20260806-0900.html", "matchmaker-20260807-0900.html", "matchmaker-20260808-0900.html"],
          f"got {kept}")
    check("prunes exactly the 1 oldest beyond the ring size",
          pruned == ["matchmaker-20260805-0900.html"], f"got {pruned}")

    kept2, pruned2 = bld.select_ring_prune(["matchmaker-20260805-0900.html"], keep=3)
    check("fewer entries than the ring size -> nothing pruned", kept2 == ["matchmaker-20260805-0900.html"] and pruned2 == [])

    check("ring_filename() produces a name select_ring_prune() recognizes",
          bool(bld.RING_RE.match(bld.ring_filename(datetime.datetime(2026, 8, 11, 9, 30)))))


def test_digest_renders():
    section("render_digest_html: smoke test, build side data only (64)")
    data = {
        "generated": "2026-08-11",
        "counts": {"available_listings": 3, "still_looking_tenants": 5},
        "health": {"tenants_missing_budget": 1},
        "delta": None,
        "area_demand": [{"district": "D15", "area": "East Coast", "unmatched_waiting": 4, "sourcing_priority": "high"}],
        "supply_overview": [{"id": "LL1", "lifecycle": "available"}, {"id": "LL2", "lifecycle": "tenanted"}],
        "listings": [{"id": "LL1", "name": "Test Listing", "district": "D15", "days_listed": 20,
                      "rent_min": 1200, "rent_max": 1200, "reconfirm_due": True}],
    }
    html = bld.render_digest_html(data)
    check("produces non empty HTML", isinstance(html, str) and len(html) > 500)
    check("carries the noindex/nofollow robots meta like the main app",
          'name="robots"' in html and "noindex" in html and "nofollow" in html)
    check("states the funnel limitation in the footer", "browser storage" in html.lower())
    check("stale listing (reconfirm_due) shows up", "Test Listing" in html)
    check("does not crash on an empty/first-build dataset", len(bld.render_digest_html({})) > 500)


def test_js_safe_json():
    section("js_safe_json: payload cannot break out of its <script> tag")
    # last_wa.snippet is verbatim WhatsApp text a stranger typed and req_raw is
    # verbatim landlord text — both land inside <script>const DATA = ...</script>.
    # A literal </script> in either used to close the tag early and let the rest
    # of the value run as markup.
    hostile = {
        "tenants": [{
            "id": "T900", "name": "Tan Ah Test",
            "last_wa": {"snippet": "call me </script><img src=x onerror=alert(1)>"},
        }],
        "listings": [{"id": "LL900", "req_raw": {"gender": "girls only </SCRIPT><svg onload=alert(2)>"}}],
    }
    payload = bld.js_safe_json(hostile)
    check("no literal </script> survives (lowercase)", "</script>" not in payload)
    check("no literal </SCRIPT> survives (uppercase)", "</SCRIPT>" not in payload)
    check("no literal </ survives at all", "</" not in payload)
    check("payload still parses as JSON", json.loads(payload) == hostile,
          "escaping changed a value")

    # \/ is a valid JSON escape for /, so ordinary text with slashes is untouched
    plain = {"note": "a/b c", "url": "https://example.com/x.jpg", "date": "2026-08-11"}
    round_tripped = json.loads(bld.js_safe_json(plain))
    check("ordinary slashes round trip unchanged", round_tripped == plain, str(round_tripped))
    check("spaces are not mangled", " " in bld.js_safe_json({"a": "x y"}))

    # U+2028/U+2029 are legal JSON but were line terminators inside JS string
    # literals before ES2019 — escape them rather than emit them raw.
    seps = bld.js_safe_json({"s": "a b c"})
    check("U+2028 escaped, not raw", " " not in seps and "\\u2028" in seps)
    check("U+2029 escaped, not raw", " " not in seps and "\\u2029" in seps)
    check("separator payload still parses", json.loads(seps)["s"] == "a b c")


def test_js_syntax_gate():
    section("check_js_syntax: a broken app.js must never overwrite a good artifact")
    tmp = tempfile.mkdtemp()
    try:
        good = os.path.join(tmp, "good.js")
        bad = os.path.join(tmp, "bad.js")
        open(good, "w").write("function ok() { return 1; }\n")
        open(bad, "w").write("function broken( { return ;;; \n")

        orig_scoring, orig_appjs = bld.SCORING, bld.APPJS
        try:
            bld.SCORING, bld.APPJS = good, good
            ok_passed = True
            try:
                bld.check_js_syntax()
            except SystemExit:
                ok_passed = False
            check("valid JS passes the gate", ok_passed)

            bld.SCORING, bld.APPJS = good, bad
            aborted = False
            try:
                bld.check_js_syntax()
            except SystemExit:
                aborted = True
            check("a syntax error in app.js aborts the build (fail closed)", aborted)
        finally:
            bld.SCORING, bld.APPJS = orig_scoring, orig_appjs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_queue_cold_rule():
    section("queue_drafts: 5 day rule and no signal refusal at dispatch time")
    import queue_drafts as qd  # noqa: E402
    today = datetime.date(2026, 8, 11)

    fresh = {"id": "T1", "last_contact": "2026-08-10", "jid": "9000000190001@lid"}
    cold = {"id": "T2", "last_contact": "2026-07-27", "jid": "9000000290001@lid"}
    nosignal = {"id": "T3", "last_contact": "", "jid": "9000000390001@lid"}
    nojid = {"id": "T4", "last_contact": "2026-08-10"}
    by_id = {t["id"]: t for t in (fresh, cold, nosignal, nojid)}

    items = [{"tenant_id": t["id"], "name": "Tan Ah Test", "phone": "90000001", "message": "hi"}
             for t in (fresh, cold, nosignal, nojid)]
    items.append({"tenant_id": "T_UNKNOWN", "name": "Ghost Test", "phone": "", "message": "hi"})

    # wa_conn None exercises the documented degraded path (bridge unavailable)
    approved, refused, skipped, _dups = qd.classify_items(items, by_id, None, today)
    approved_ids = [a["tenant_id"] for a in approved]
    refused_ids = [r[0] for r in refused]
    skipped_ids = [s[0] for s in skipped]

    check("fresh tenant is approved", approved_ids == ["T1"], str(approved_ids))
    check("cold >5d tenant is refused", "T2" in refused_ids, str(refused_ids))
    check("no signal tenant is refused, never guessed fresh", "T3" in refused_ids, str(refused_ids))
    check("tenant with no jid is skipped, never guessed", "T4" in skipped_ids, str(skipped_ids))
    check("unknown tenant id is skipped", "T_UNKNOWN" in skipped_ids, str(skipped_ids))
    check("approved items carry the resolved jid",
          all(a.get("jid") for a in approved), "an approved item had no jid")
    check("5 day boundary: exactly 5 days still queues",
          qd.freshest_days({"last_contact": "2026-08-06"}, None, today) == 5)
    check("5 day boundary: 6 days does not",
          qd.freshest_days({"last_contact": "2026-08-05"}, None, today) == 6)


def test_deploy_auth_and_cache_posture():
    section("deploy/: the auth wall fails closed and the PWA cache cannot pin one build")
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "scripts", "matchmaker", "deploy")
    mw = open(os.path.join(here, "middleware.js")).read()
    sw = open(os.path.join(here, "sw.js")).read()
    sh = open(os.path.join(here, "deploy.sh")).read()

    # Interpolating two undefined env vars yields a fixed, publicly derivable
    # password — a wall that still answers 401 to a plain visitor while letting
    # in anyone who sends btoa('undefined:undefined').
    check("middleware refuses to serve when MM_USER/MM_PASS are unset",
          "!user || !pass" in mw, "no fail closed guard found")
    check("the unconfigured response is not a 200",
          "status: 503" in mw, "expected an explicit non-success status")
    check("the credential check reads the guarded locals, not process.env inline",
          "btoa(`${user}:${pass}`)" in mw, "expected the guarded values to feed the comparison")
    check("no credential literal lives in the file",
          "MM_PASS" in mw and "process.env.MM_PASS" in mw and "'Basic '" in mw)
    check("deploy.sh explains a 503 rather than leaving it unexplained",
          '"$STATUS" = "503"' in sh, "deploy.sh should name the unset env var case")

    # A data-only redeploy never changes sw.js, so nothing re-runs install() —
    # cache-first with no revalidation would serve the first artifact forever.
    check("the service worker revalidates in the background rather than pinning a build",
          "cached || fresh" in sw, "expected stale-while-revalidate, not plain cache first")
    check("a failed response never overwrites a good cached artifact",
          "res && res.ok" in sw, "the auth wall's 401 must not be cached")
    check("going offline still serves the cached copy",
          ".catch(" in sw and "return cached;" in sw)
    check("the cache name was bumped alongside the logic change",
          "matchmaker-cache-v2" in sw, "stale clients would keep running the old logic")
    check("the no-store vs Cache Storage contradiction is written down",
          "no-store" in sw and "PRIVACY NOTE" in sw,
          "vercel.json's no-store does not stop the Cache API persisting this artifact")


def test_queue_no_double_send():
    section("queue_drafts: an already queued recipient is never queued twice")
    import queue_drafts as qd  # noqa: E402
    today = datetime.date(2026, 8, 11)
    a = {"id": "T1", "last_contact": "2026-08-10", "jid": "9000000190001@lid"}
    b = {"id": "T2", "last_contact": "2026-08-10", "jid": "9000000290001@lid"}
    by_id = {t["id"]: t for t in (a, b)}
    mk = lambda tid, msg: {"tenant_id": tid, "name": "Tan Ah Test", "phone": "90000001", "message": msg}

    # The real regression: the app's dispatch drawer keeps showing every Queued
    # pair after an export, so the second export re-carries the first batch.
    first, _r, _s, dups1 = qd.classify_items([mk("T1", "hi")], by_id, None, today)
    check("first export queues the tenant", [x["tenant_id"] for x in first] == ["T1"])
    check("nothing is flagged duplicate on a clean queue", dups1 == [], str(dups1))

    queued_ids = {x["tenant_id"] for x in first}
    queued_jids = {x["jid"] for x in first}
    second, _r2, _s2, dups2 = qd.classify_items(
        [mk("T1", "hi"), mk("T2", "hi")], by_id, None, today, queued_ids, queued_jids)
    check("re-exported tenant is not queued a second time",
          [x["tenant_id"] for x in second] == ["T2"], str([x["tenant_id"] for x in second]))
    check("the re-export is reported as a duplicate, not silently dropped",
          [d[0] for d in dups2] == ["T1"], str(dups2))

    # Same tenant against two listings in ONE export: different message text,
    # same human at 08:00.
    twice, _r3, _s3, dups3 = qd.classify_items([mk("T1", "listing X"), mk("T1", "listing Y")], by_id, None, today)
    check("one message per recipient within a single batch", len(twice) == 1, str(twice))
    check("the within-batch repeat is reported too", [d[0] for d in dups3] == ["T1"], str(dups3))

    # A jid match alone is enough — the recipient is what matters, not the id.
    byjid, _r4, _s4, dups4 = qd.classify_items([mk("T1", "hi")], by_id, None, today, set(), {"9000000190001@lid"})
    check("a jid already in the queue blocks the send even under a new tenant id",
          byjid == [] and [d[0] for d in dups4] == ["T1"], str((byjid, dups4)))

    # pending_recipients reads the real queue file shape.
    tmp = tempfile.mkdtemp()
    try:
        qpath = os.path.join(tmp, "morning-dispatch-queue.json")
        check("a missing queue file yields no pending recipients",
              qd.pending_recipients(qpath) == (set(), set()))
        json.dump({"created": "2026-08-11T07:00:00+08:00",
                   "items": [{"jid": "9000000190001@lid", "tag": "matchmaker",
                              "message": "hi", "tenant_id": "T1"}]}, open(qpath, "w"))
        ids, jids = qd.pending_recipients(qpath)
        check("pending recipients are read back from the queue file",
              ids == {"T1"} and jids == {"9000000190001@lid"}, str((ids, jids)))
        # Another producer's item counts too — one human, one 08:00 batch.
        json.dump({"created": "2026-08-11T07:00:00+08:00",
                   "items": [{"jid": "9000000290001@lid", "tag": "followup", "message": "hi"}]}, open(qpath, "w"))
        ids2, jids2 = qd.pending_recipients(qpath)
        check("another producer's queued recipient is counted as pending",
              jids2 == {"9000000290001@lid"} and ids2 == set(), str((ids2, jids2)))
        open(qpath, "w").write("{not json")
        check("a corrupt queue file degrades to empty instead of raising",
              qd.pending_recipients(qpath) == (set(), set()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_queue_format_fidelity():
    section("queue_drafts: morning dispatch queue format and created timestamp")
    import queue_drafts as qd  # noqa: E402
    tmp = tempfile.mkdtemp()
    try:
        qpath = os.path.join(tmp, "morning-dispatch-queue.json")
        batch = [{"tenant_id": "T1", "name": "Tan Ah Test", "phone": "90000001",
                  "message": "hi", "jid": "9000000190001@lid"}]

        qd.merge_into_queue(batch, qpath)
        q1 = json.load(open(qpath))
        check("creates the file when absent", os.path.exists(qpath))
        check("top level shape is {created, items}", set(q1) == {"created", "items"}, str(set(q1)))
        check("item carries exactly jid/tag/message/tenant_id",
              set(q1["items"][0]) == {"jid", "tag", "message", "tenant_id"}, str(set(q1["items"][0])))
        check("tag is matchmaker", q1["items"][0]["tag"] == "matchmaker")
        # The queue carries no standalone phone/name fields — the jid is the
        # routing address (it embeds the number by design) and the message is
        # the text being sent, which legitimately greets the tenant by name.
        check("no separate phone/name fields are written into the queue",
              "phone" not in q1["items"][0] and "name" not in q1["items"][0])

        created_first = q1["created"]
        batch2 = [dict(batch[0], tenant_id="T2", jid="9000000290001@lid")]
        qd.merge_into_queue(batch2, qpath)
        q2 = json.load(open(qpath))
        check("appends rather than replacing", len(q2["items"]) == 2, str(len(q2["items"])))
        check("preserves the original created timestamp on append",
              q2["created"] == created_first, f"{created_first} -> {q2['created']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================= run
def main():
    test_date_normalization()
    test_available_from()
    test_move_in_normalization()
    test_move_in_norm_export_integration()
    test_budget_recovery()
    test_units_parser()
    test_agent_suspect()
    test_phone_normalize()
    test_lang_detect()
    test_address_and_rent_overlap()
    test_dup_groups_and_dup_of()
    test_exclusion_filter()
    test_schema_v2_shape()
    test_missing_and_intake_complete()
    test_delta_computation()
    test_exclusions_config_autocreate()
    test_state_files_fail_closed()
    test_key_id_safety()
    test_photo_url_join()
    test_abs_url()

    test_lifecycle_mapping()
    test_supply_overview()
    test_load_busy_blocks()
    test_build_history_entry_shape()
    test_build_history_tail_roundtrip()
    test_atomic_writes()
    test_build_history_append_durability()
    test_aborted_build_never_advances_the_delta_baseline()
    test_successful_build_history_is_unchanged()
    test_ring_same_minute_rebuild()
    test_anomaly_guard_math()
    test_ring_pruning()
    test_digest_renders()

    test_js_safe_json()
    test_js_syntax_gate()
    test_queue_cold_rule()
    test_deploy_auth_and_cache_posture()
    test_queue_no_double_send()
    test_queue_format_fidelity()

    print(f"\n{'='*60}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        sys.exit(1)
    print("all checks passed")
    sys.exit(0)

if __name__ == "__main__":
    main()
