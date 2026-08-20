#!/usr/bin/env python3
"""Pure assert test suite for the matchmaker data lane (export_data.py / enrich.py).
No pytest — stdlib only, runs under /usr/bin/python3:
    /usr/bin/python3 tests/matchmaker/test_export.py
Exits 1 if any check fails, 0 if everything passes.

All fixture people below are invented (SG plausible, obviously fake names/phones) —
never real tenant or landlord data. Real data lives only in the gitignored
_templates/ directory and is never read by this file.
"""
import contextlib, datetime, io, json, os, re, sys, tempfile, shutil

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
        {"phones": [], "ids": [], "name_markers": []}, None, TODAY, [])
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
    tenants = ed.build_tenants([t1, t2, t3], {"phones": [], "ids": [], "name_markers": []}, None, TODAY, [])[0]
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
        [kept, db_excluded, phone_excluded, id_excluded, marker_excluded, flagged_not_dropped], cfg, None, TODAY, [])
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
                "first_seen","days_listed","is_cobroke","dup_of","reconfirm_due","days_since_confirmed","lifecycle"]
    missing_keys = [k for k in required if k not in l]
    check("all schema v2 listing keys present", not missing_keys, f"missing {missing_keys}")
    check("units always >=1 entry", isinstance(l["units"], list) and len(l["units"]) >= 1)
    check("first_seen stamped from empty registry", l["first_seen"] == TODAY.isoformat())
    check("days_listed is 0 on first sighting", l["days_listed"] == 0)
    check("is_cobroke reflects source", l["is_cobroke"] is False)
    check("lifecycle mapped for an active listing", l["lifecycle"] == "available", f"got {l['lifecycle']}")
    check("days_since_confirmed present and numeric", isinstance(l["days_since_confirmed"], int))

    section("schema v2 shape — tenants")
    tenants, _ = ed.build_tenants([fake_tenant(id="T930")], {"phones": [], "ids": [], "name_markers": []}, None, TODAY, [])
    t = tenants[0]
    # (73) status/listing_enquired/budget_note cut from the shipped payload — zero
    # reads anywhere and no dedicated correctness test of their own (see export_data.py).
    required_t = ["id","name","preferred_location","preferred_districts","district","district_inferred",
                  "district_source","district_conflict","budget",
                  "budget_min","budget_max","budget_contradiction","pax","gender","ethnicity","nationality",
                  "pass_type","occupation","move_in","move_in_norm","lease_months","phone","last_contact",
                  "last_wa","lang","dup_group","missing","intake_complete","work_anchor",
                  "is_agent_suspect"]
    missing_keys_t = [k for k in required_t if k not in t]
    check("all schema v2 tenant keys present", not missing_keys_t, f"missing {missing_keys_t}")
    check("work_anchor always null per recon Q18 (no source field exists)", t["work_anchor"] is None)
    check("last_wa null when wa_conn is None (bridge unavailable)", t["last_wa"] is None)
    check("lang defaults en when bridge unavailable", t["lang"] == "en")
    check("complete fixture -> intake_complete True", t["intake_complete"] is True, f"missing={t['missing']}")
    check("explicit district on fixture -> district_inferred False", t["district_inferred"] is False)
    check("no WA bridge -> budget_contradiction None, never a guess", t["budget_contradiction"] is None)

    section("schema v2 shape — health block")
    health = ed.compute_health(listings, tenants)
    for k in ("tenants_missing_budget","tenants_missing_move_in","tenants_missing_pax",
              "tenants_missing_lease_months","tenants_missing_district","listings_unparsed_req_raw"):
        check(f"health has {k}", k in health)

def test_missing_and_intake_complete():
    section("missing[] / intake_complete on a sparse record")
    # district is left with genuinely nothing to infer from (no preferred_districts,
    # no preferred_location) -- otherwise build_tenants' district inference (item 1)
    # would legitimately recover it from the fixture's own defaults and this would
    # stop testing a sparse record.
    sparse = fake_tenant(id="T940", budget=None, budget_min="", budget_max=None,
                          move_in_date="", no_of_pax=None, lease_term_months=None,
                          district="", preferred_districts=[], preferred_location="")
    tenants, _ = ed.build_tenants([sparse], {"phones": [], "ids": [], "name_markers": []}, None, TODAY, [])
    t = tenants[0]
    check("missing[] flags all 5 gaps", set(t["missing"]) == {"budget","move_in","pax","lease_months","district"},
          f"got {t['missing']}")
    check("intake_complete False when fields missing", t["intake_complete"] is False)


# =========================================== district inference [item 1] ====
def test_infer_district_extensions():
    section("infer_district: the ground-truthed place map wins over a disagreeing typed district, "
            "flagged rather than silent (item 4 fix)")
    dist_area = {"D19": "Hougang, Sengkang, Punggol", "D15": "Katong, Marine Parade, East Coast"}
    kws = ed.build_area_keywords(dist_area)

    d, src, conflict = ed.infer_district("D9", [], "Cherryhill", kws)
    check("explicit district already set -> returned unchanged, no inference",
          d == "D9" and src is None and conflict is None)
    d, src, conflict = ed.infer_district("", ["D3"], "Cherryhill", kws)
    check("preferred_districts[0] wins over free text",
          d == "D3" and src == "preferred_districts" and conflict is None)

    d, src, conflict = ed.infer_district("", [], "Cherryhill (D20)", kws)
    check("'Cherryhill (D20)' -> the place map's D19 wins (Lorong Lew Lian IS D19), not the typed D20",
          d == "D19" and src == "known_place", f"got {(d, src)}")
    check("the disagreement is flagged, not silently overridden",
          conflict == {"place": "cherryhill", "place_district": "D19", "typed_district": "D20"},
          f"got {conflict}")

    d2, src2, conflict2 = ed.infer_district("", [], "Cherryhill area (D20)", kws)
    check("'Cherryhill area (D20)' -> same override, same conflict shape",
          d2 == "D19" and src2 == "known_place" and conflict2 is not None)

    d, src, conflict = ed.infer_district("", [], "Cherryhill", kws)
    check("'Cherryhill' with no typed district -> place map D19, no conflict (nothing to disagree with)",
          d == "D19" and src == "known_place" and conflict is None)
    d, src, conflict = ed.infer_district("", [], "Cherryhill / Lorong Lew Lian", kws)
    check("'Cherryhill / Lorong Lew Lian' -> D19 (longer keyword still resolves to the same district)",
          d == "D19" and src == "known_place")
    d, src, _ = ed.infer_district("", [], "Haig Road area", kws)
    check("'Haig Road area' -> D15 via place map, tagged known_place (building level)",
          d == "D15" and src == "known_place")
    d, src, _ = ed.infer_district("", [], "Jalan Batu", kws)
    check("'Jalan Batu' -> D15 via place map (ground-truthed against LL089)",
          d == "D15" and src == "known_place")
    d, src, conflict = ed.infer_district("", [], "somewhere unrecognisable", kws)
    check("no explicit district, no place match, no area keyword -> ''",
          d == "" and src is None and conflict is None)
    check("blank preferred_location -> ''", ed.infer_district("", [], "", kws)[0] == "")
    d, src, _ = ed.infer_district("", [], "near Hougang MRT", kws)
    check("existing coarse area keyword still resolves (Hougang itself, not just Cherryhill), "
          "tagged area_keyword (genuinely district-level) not known_place",
          d == "D19" and src == "area_keyword", f"got {(d, src)}")


def test_build_tenants_district_inference():
    section("build_tenants: district actually backfilled from preferred_location free text")
    dist_area = {"D19": "Hougang, Sengkang, Punggol", "D15": "Katong, Marine Parade, East Coast"}
    kws = ed.build_area_keywords(dist_area)
    recoverable = fake_tenant(id="T970", district="", preferred_districts=[],
                               preferred_location="Cherryhill (Lorong Lew Lian)")
    # TN546/TN548-shaped: typed "(D20)" disagrees with Cherryhill's real D19 (item 4)
    conflicting = fake_tenant(id="T971", district="", preferred_districts=[],
                               preferred_location="Cherryhill (D20)")
    unrecoverable = fake_tenant(id="T972", district="", preferred_districts=[],
                                 preferred_location="somewhere unrecognisable")
    already_has_district = fake_tenant(id="T973", district="D15", preferred_location="Cherryhill")
    tenants, _ = ed.build_tenants(
        [recoverable, conflicting, unrecoverable, already_has_district],
        {"phones": [], "ids": [], "name_markers": []}, None, TODAY, kws)
    by_id = {t["id"]: t for t in tenants}

    check("'Cherryhill (Lorong Lew Lian)' recovered to D19",
          by_id["T970"]["district"] == "D19", f"got {by_id['T970']['district']}")
    check("recovered district is flagged district_inferred=True", by_id["T970"]["district_inferred"] is True)
    check("recovered tenant no longer flagged missing district", "district" not in by_id["T970"]["missing"])
    check("district_source tags known_place (building level, not district-wide)",
          by_id["T970"]["district_source"] == "known_place")
    check("no conflict on a non-disagreeing recovery", by_id["T970"]["district_conflict"] is None)

    check("'Cherryhill (D20)' now resolves to the place map's D19, NOT the typed D20",
          by_id["T971"]["district"] == "D19" and by_id["T971"]["district_inferred"] is True,
          f"got {by_id['T971']['district']}")
    check("the override is flagged on the record, never silent",
          by_id["T971"]["district_conflict"] == {"place": "cherryhill", "place_district": "D19",
                                                   "typed_district": "D20"},
          f"got {by_id['T971']['district_conflict']}")

    check("unrecognisable free text -> district stays blank, still flagged missing",
          by_id["T972"]["district"] == "" and "district" in by_id["T972"]["missing"]
          and by_id["T972"]["district_inferred"] is False)
    check("no district_source/conflict when nothing was inferred",
          by_id["T972"]["district_source"] is None and by_id["T972"]["district_conflict"] is None)
    check("tenant with an explicit district already set is untouched (district_inferred False)",
          by_id["T973"]["district"] == "D15" and by_id["T973"]["district_inferred"] is False)
    check("explicit/stated district carries no source tag either (nothing was inferred)",
          by_id["T973"]["district_source"] is None)


# ======================================= budget contradiction [item 3] ======
class _FakeWaConn:
    """Minimal sqlite3-connection-shaped stub, keyed by jid, so
    find_budget_contradiction()/build_landlord_responsiveness() can be exercised
    without a real WhatsApp store. rows_by_jid: {jid: [(content, ts, is_from_me), ...]}."""
    def __init__(self, rows_by_jid):
        self._rows_by_jid = rows_by_jid
        self._last_jid = None
        self._last_sql = ""
    def execute(self, sql, params=()):
        self._last_sql = sql
        self._last_jid = params[0] if params else None
        return self

    def fetchall(self):
        # mimics real sqlite column projection: find_budget_contradiction() SELECTs
        # content only (its WHERE clause also mentions is_from_me, so check the
        # SELECT list specifically, not just substring presence anywhere in the
        # SQL); build_landlord_responsiveness() SELECTs all three columns.
        rows = self._rows_by_jid.get(self._last_jid, [])
        if self._last_sql.lstrip().startswith("SELECT content FROM"):
            return [(r[0],) for r in rows]
        return [tuple(r) for r in rows]

    def fetchone(self):
        # only enrich.fetch_wa_info() (a pre-existing, already-tested function) uses
        # fetchone(); this stub just needs to not crash when build_tenants' own
        # last_wa/lang lookup runs alongside the new budget-contradiction lookup.
        rows = self.fetchall()
        return rows[0] if rows else None


def test_budget_contradiction_detection():
    section("find_budget_contradiction: only flags with BOTH a stretch phrase AND a $ figure above budget")
    stretch = _FakeWaConn({"jid1": [("no keen thx i only want under 1200, max 1250 if it includes good utility",
                                      "2026-08-01T10:00:00+08:00", 0)]})
    result = ed.find_budget_contradiction(stretch, "jid1", 1100)
    check("real stretch phrase + figure clearing the threshold -> flagged",
          result is not None and result["mentioned"] == 1250 and result["stated_budget"] == 1100,
          f"got {result}")
    check("quote is the verbatim message, truncated to 200 chars",
          result["quote"].startswith("no keen thx"))

    bare_number = _FakeWaConn({"jid2": [("the room is $1200, nice view", "2026-08-01T10:00:00+08:00", 0)]})
    check("bare $ figure with no stretch phrase -> never flagged (could be anything)",
          ed.find_budget_contradiction(bare_number, "jid2", 900) is None)

    trivial = _FakeWaConn({"jid3": [("Budget:max 900", "2026-08-01T10:00:00+08:00", 0)]})
    check("mentioned amount too close to stated (<5%/$50) -> not flagged as noise",
          ed.find_budget_contradiction(trivial, "jid3", 870) is None)

    lower = _FakeWaConn({"jid4": [("can go up to 800", "2026-08-01T10:00:00+08:00", 0)]})
    check("mentioned amount BELOW stated budget -> not a contradiction",
          ed.find_budget_contradiction(lower, "jid4", 1000) is None)

    check("no connection (bridge unavailable) -> None, never a guess",
          ed.find_budget_contradiction(None, "jid5", 900) is None)
    check("no jid -> None", ed.find_budget_contradiction(stretch, "", 900) is None)
    check("no stated budget to compare against -> None", ed.find_budget_contradiction(stretch, "jid6", None) is None)


def test_build_tenants_budget_contradiction_integration():
    section("build_tenants wires find_budget_contradiction through using the tenant's own jid")
    conn = _FakeWaConn({"jidT980": [("no keen thx i only want under 1200, max 1250 if it includes good utility",
                                      "2026-08-01T10:00:00+08:00", 0)]})
    t = fake_tenant(id="T980", jid="jidT980", budget=1100, budget_min="", budget_max="")
    tenants, _ = ed.build_tenants([t], {"phones": [], "ids": [], "name_markers": []}, conn, TODAY, [])
    bc = tenants[0]["budget_contradiction"]
    check("budget_contradiction populated end-to-end through build_tenants",
          bc is not None and bc["mentioned"] == 1250, f"got {bc}")


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
    check("tenant_lost_ids detects a tenant present in prev but gone from cur",
          delta["tenant_lost_ids"] == ["T02"], f"got {delta['tenant_lost_ids']}")
    check("no prev -> delta is None (first run)", ed.compute_delta(None, cur_listings, cur_tenants) is None)


# ============================================= week_delta [idea 40] =========
def test_week_delta():
    section("compute_week_delta: rolls compute_delta() forward through prev, ages out past the window")
    listings = [{"id": "LL01", "name": "Listing One", "district": "D15", "first_seen": "2026-08-10"},
                {"id": "LL02", "name": "Listing Two", "district": "D16", "first_seen": "2026-07-01"}]
    delta = {"gone_listings": [{"id": "LL03", "name": "Gone One"}],
             "availability_changes": [{"id": "LL01", "from": "Available", "to": "Offer pending"}],
             "tenant_lost_ids": ["T50"]}
    wd = ed.compute_week_delta(None, delta, listings, TODAY)
    check("window_days is 7", wd["window_days"] == 7)
    check("this build's own delta shows up in gone_listings",
          wd["gone_listings"] == [{"id": "LL03", "name": "Gone One"}], f"got {wd['gone_listings']}")
    check("tenants_lost carries the id through", wd["tenants_lost"] == ["T50"], f"got {wd['tenants_lost']}")
    check("new_stock derived straight from first_seen, within the 7 day window only",
          [l["id"] for l in wd["new_stock"]] == ["LL01"], f"got {wd['new_stock']}")

    section("a second build rolls the first build's event forward and accumulates")
    prev_with_week = {"week_delta": wd}
    delta2 = {"gone_listings": [{"id": "LL04", "name": "Gone Two"}],
              "availability_changes": [], "tenant_lost_ids": ["T51"]}
    wd2 = ed.compute_week_delta(prev_with_week, delta2, listings, TODAY)
    check("both builds' gone_listings accumulate within the window",
          {g["id"] for g in wd2["gone_listings"]} == {"LL03", "LL04"}, f"got {wd2['gone_listings']}")
    check("both builds' tenants_lost accumulate", set(wd2["tenants_lost"]) == {"T50", "T51"}, f"got {wd2}")

    section("events older than the window age out")
    stale_event = {"date": (TODAY - datetime.timedelta(days=10)).isoformat(),
                    "gone_listings": [{"id": "LL99", "name": "Ancient"}],
                    "availability_changes": [], "tenant_lost_ids": ["T99"]}
    prev_stale = {"week_delta": {"events": [stale_event]}}
    wd3 = ed.compute_week_delta(prev_stale, None, listings, TODAY)
    check("a 10 day old event is aged out of a 7 day window", wd3["gone_listings"] == [], f"got {wd3}")
    check("no delta this run (delta=None) -> nothing new added, only aging applied", wd3["events"] == [])

    section("events [item 7]: repeat no-op builds collapse into ONE event instead of stacking a copy per run")
    empty_delta = {"gone_listings": [], "availability_changes": [], "tenant_lost_ids": []}
    wd_run1 = ed.compute_week_delta(None, empty_delta, listings, TODAY)
    check("first empty build stores exactly one event", len(wd_run1["events"]) == 1, f"got {wd_run1['events']}")
    prev_after_run1 = {"week_delta": wd_run1}
    wd_run2 = ed.compute_week_delta(prev_after_run1, empty_delta, listings, TODAY)
    check("a second identical no-op build (same day, same empty content) does NOT append a duplicate",
          len(wd_run2["events"]) == 1, f"got {wd_run2['events']}")
    prev_after_run2 = {"week_delta": wd_run2}
    wd_run3 = ed.compute_week_delta(prev_after_run2, empty_delta, listings, TODAY)
    wd_run4 = ed.compute_week_delta({"week_delta": wd_run3}, empty_delta, listings, TODAY)
    check("four identical no-op builds still leave exactly one event (the real bug: 4 identical "
          "empty events from one day's 4 builds)", len(wd_run4["events"]) == 1, f"got {wd_run4['events']}")

    prev_after_noop = {"week_delta": wd_run4}
    real_delta = {"gone_listings": [{"id": "LL05", "name": "Actually Gone"}],
                  "availability_changes": [], "tenant_lost_ids": []}
    wd_run5 = ed.compute_week_delta(prev_after_noop, real_delta, listings, TODAY)
    check("a build with genuinely NEW content still appends (dedup never eats real changes)",
          len(wd_run5["events"]) == 2, f"got {wd_run5['events']}")
    check("the real change is reflected in the aggregate output",
          wd_run5["gone_listings"] == [{"id": "LL05", "name": "Actually Gone"}], f"got {wd_run5}")


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


# ============================================== portfolio views (5 new keys)
def test_all_landlords_and_tenants_shape():
    section("build_all_landlords: full portfolio, every status, ported from the monolith")
    dist_area = {"D15": "East Coast, Marine Parade", "D9": "Orchard, River Valley"}
    area_keywords = ed.build_area_keywords(dist_area)
    landlords = [
        fake_landlord(id="LLA1", status="active", contact_label_source="own"),
        fake_landlord(id="LLA2", status="closed (tenanted 1 aug)"),
        # a bracketed name that will not match anything in the real
        # ~/.claude/state/cobroke-agents.json, so this stays offline/deterministic
        fake_landlord(id="LLA3", status="active",
                      contact_label_source="co-broke listing (Xqzzytestagent), added 28 Jul 2026"),
    ]
    all_landlords = ed.build_all_landlords(landlords, dist_area, area_keywords)
    check("all_landlords is non-empty", len(all_landlords) > 0)
    check("all_landlords keeps every status, unlike build_listings()", len(all_landlords) == 3,
          f"got {len(all_landlords)}")
    required_ll = ["id", "name", "availability", "status_raw", "sort", "district", "primary_district",
                   "address", "map_query", "rent_min", "rent_max", "viewing", "rooms", "property_type",
                   "phone", "last_contact", "follow_up", "source", "cea", "commission_est", "handed_off"]
    for r in all_landlords:
        missing = [k for k in required_ll if k not in r]
        check(f"all_landlords[{r['id']}] has the full field set", not missing, f"missing {missing}")
    by_id = {r["id"]: r for r in all_landlords}
    check("cea is None for a non co-broke record (never checks the landlord's own phone)",
          by_id["LLA1"]["cea"] is None)
    check("cea populated for a co-broke record — agent name extracted from the bracket, never raises",
          by_id["LLA3"]["cea"] is not None and by_id["LLA3"]["cea"].get("agent") == "Xqzzytestagent",
          f"got {by_id['LLA3']['cea']}")

    section("build_all_tenants: full portfolio, every status")
    tenants = [fake_tenant(id="TA1"), fake_tenant(id="TA2", excluded=True, exclude_reason="not interested")]
    all_tenants = ed.build_all_tenants(tenants, area_keywords)
    check("all_tenants is non-empty", len(all_tenants) > 0)
    check("all_tenants keeps every status, unlike build_tenants()", len(all_tenants) == 2, f"got {len(all_tenants)}")
    required_tn = ["id", "name", "looking", "status_raw", "sort", "preferred_location", "district",
                   "primary_district", "budget", "pax", "gender", "nationality", "occupation", "move_in",
                   "lease_months", "phone", "last_contact", "listing_enquired", "missing"]
    for r in all_tenants:
        missing = [k for k in required_tn if k not in r]
        check(f"all_tenants[{r['id']}] has the full field set", not missing, f"missing {missing}")


def test_sales_shape():
    section("build_sales: separate track for landlord records marked deal_type sale/sale-or-rent")
    dist_area = {"D15": "East Coast, Marine Parade"}
    area_keywords = ed.build_area_keywords(dist_area)
    landlords = [
        fake_landlord(id="LLS1", deal_type="sale", status="active",
                      rooms_and_rent="Asking $505k, negotiable", rent_min=490, rent_max=490),
        fake_landlord(id="LLS2", deal_type="rent"),  # not a sale record — excluded
    ]
    sales = ed.build_sales(landlords, dist_area, area_keywords)
    check("only sale/sale-or-rent deal_type records are included", [r["id"] for r in sales] == ["LLS1"],
          f"got {[r['id'] for r in sales]}")
    required_s = ["id", "name", "sale_status", "sort", "status_raw", "district", "primary_district",
                  "address", "map_query", "asking_price", "price_text", "property_type", "phone",
                  "last_contact", "follow_up", "source", "cea"]
    missing = [k for k in required_s if k not in sales[0]]
    check("sale record has the full field set", not missing, f"missing {missing}")
    check("asking_price parsed from free text, ignoring the mis-parsed rent_min/rent_max artifact",
          sales[0]["asking_price"] == 505000, f"got {sales[0]['asking_price']}")
    check("price_text carries the verbatim source text", sales[0]["price_text"] == "Asking $505k, negotiable")
    check("cea is None for a non co-broke sale record", sales[0]["cea"] is None)


def test_revival_and_duplicate_phones():
    section("build_revival: still-looking tenants past the lead cutoff, matched to a live listing")
    dist_area = {"D15": "East Coast, Marine Parade"}
    area_keywords = ed.build_area_keywords(dist_area)
    quiet_tenant = fake_tenant(id="TR1", name="Quiet Tan", phone="90000021", last_contact="2026-06-01",
                                district="D15", budget=1200, budget_max=1200, no_of_pax=1,
                                preferred_districts=["D15"])
    fresh_tenant = fake_tenant(id="TR2", name="Fresh Tan", phone="90000022", last_contact="2026-08-10")
    landlords = [fake_landlord(id="LLR1", status="active", district="D15", rent_min=1100, rent_max=1300)]
    revival = ed.build_revival([quiet_tenant, fresh_tenant], landlords, dist_area, area_keywords, TODAY)
    check("only the tenant past the 45 day lead cutoff appears", [r["name"] for r in revival] == ["Quiet Tan"],
          f"got {[r['name'] for r in revival]}")
    r = revival[0]
    required_r = ["name", "phone", "days_quiet", "budget", "district", "pax", "tier", "match"]
    missing = [k for k in required_r if k not in r]
    check("revival record has the full field set", not missing, f"missing {missing}")
    check("matched against the available listing (district + budget both line up -> good)",
          r["match"] is not None and r["tier"] == "good", f"got {r}")
    check("nested match carries exactly id/name/district/rent_min/rent_max",
          set(r["match"].keys()) == {"id", "name", "district", "rent_min", "rent_max"}, f"got {r['match']}")

    section("compute_duplicate_phones: same number saved under more than one record")
    landlords_dup = [fake_landlord(id="LLD1", landlord_name="Dup Landlord", phone="6590009999"),
                      fake_landlord(id="LLD2", landlord_name="Solo Landlord", phone="6590001111")]
    all_ll = ed.build_all_landlords(landlords_dup, dist_area, area_keywords)
    tenants_dup = [fake_tenant(id="TD1", name="Dup Tenant", phone="6590009999"),
                   fake_tenant(id="TD2", name="Solo Tenant", phone="6590002222")]
    dup = ed.compute_duplicate_phones(all_ll, tenants_dup)
    check("only the genuinely shared phone number is reported", [d["phone"] for d in dup] == ["6590009999"],
          f"got {dup}")
    check("duplicate_phones entries genuinely have more than one owner", all(len(d["owners"]) > 1 for d in dup),
          f"got {dup}")
    check("owners are tagged LL:/TN:", set(dup[0]["owners"]) == {"LL:Dup Landlord", "TN:Dup Tenant"},
          f"got {dup[0]['owners']}")


# ============================================= enrichment queue [5,6] =======
def test_enrichment_queue():
    section("unlock_value_for [item 3]: sums per-field gated counts (restricted to "
            "availability=='Available'), no longer a distinct-listing union that let "
            "district (unconditional on every listing) cap EVERY district-missing tenant "
            "at the same ceiling regardless of what else they were missing")
    listings = [
        {"id": "LL1", "name": "L1", "district": "D15", "rent_min": 1200, "available_from": "2026-09-01",
         "availability": "Available", "gates": {"max_pax": 2, "lease_min": 6}},
        {"id": "LL2", "name": "L2", "district": "D16", "rent_min": None, "available_from": None,
         "availability": "Available", "gates": {"max_pax": None, "lease_min": None}},
        # no district at all -- must NOT count toward "district" (item 3's docstring fix:
        # district only counts listings that themselves HAVE a district to be adjacent to)
        {"id": "LL3", "name": "L3", "district": "", "rent_min": 800, "available_from": None,
         "availability": "Available", "gates": {"max_pax": None, "lease_min": None}},
        # Offer pending -- must be excluded entirely; the docstring claims "currently
        # AVAILABLE listings" and this now actually enforces that instead of trusting the
        # caller
        {"id": "LL4", "name": "L4", "district": "D17", "rent_min": 1000, "available_from": "2026-09-15",
         "availability": "Offer pending", "gates": {"max_pax": 1, "lease_min": 12}},
    ]
    check("no missing fields -> 0 unlock value", ed.unlock_value_for([], listings) == 0)
    check("budget missing counts only Available listings with a price floor (LL1, LL3; LL4 excluded)",
          ed.unlock_value_for(["budget"], listings) == 2, f"got {ed.unlock_value_for(['budget'], listings)}")
    check("pax missing counts only Available listings gating on max_pax (LL1 only)",
          ed.unlock_value_for(["pax"], listings) == 1)
    check("district missing counts only Available listings that themselves have a district (LL1, LL2; LL3 excluded)",
          ed.unlock_value_for(["district"], listings) == 2, f"got {ed.unlock_value_for(['district'], listings)}")
    check("missing fields SUM independently now (LL1 gates on both budget and pax -> counted for EACH, "
          "not deduped to one listing) -- budget(2) + pax(1) = 3",
          ed.unlock_value_for(["budget", "pax"], listings) == 3,
          f"got {ed.unlock_value_for(['budget', 'pax'], listings)}")

    section("the real degenerate case (item 3): missing several gated fields must outrank missing only district")
    listings2 = [
        {"id": "LL1", "district": "D15", "rent_min": 1200, "available_from": "2026-09-01",
         "availability": "Available", "gates": {"max_pax": 2, "lease_min": 6}},
        {"id": "LL2", "district": "D16", "rent_min": 1100, "available_from": "2026-09-10",
         "availability": "Available", "gates": {"max_pax": 1, "lease_min": 3}},
    ]
    district_only = ed.unlock_value_for(["district"], listings2)
    all_four = ed.unlock_value_for(["budget", "pax", "lease_months", "move_in"], listings2)
    check("missing only district no longer automatically ties/outranks missing 4 gated fields",
          all_four > district_only, f"district_only={district_only} all_four={all_four}")

    tenants = [
        {"id": "T1", "name": "A", "phone": "1", "missing": ["budget"]},
        {"id": "T2", "name": "B", "phone": "2", "missing": ["pax"]},
        {"id": "T3", "name": "C", "phone": "3", "missing": ["district"]},
        {"id": "T4", "name": "D", "phone": "4", "missing": []},
        {"id": "T5", "name": "E", "phone": "5", "missing": ["budget", "pax"]},
    ]
    q = ed.build_enrichment_queue(tenants, listings)
    check("tenant with no missing fields is excluded from the queue",
          "T4" not in {r["id"] for r in q}, f"got {[r['id'] for r in q]}")
    check("queue has exactly the 4 tenants with gaps", len(q) == 4, f"got {len(q)}")
    check("highest unlock_value (T5, missing budget+pax, sums to 3) ranks first",
          q[0]["id"] == "T5" and q[0]["unlock_value"] == 3, f"got {q[0]}")


# ==================================== live demand / zero-stock [4,23,25] ====
def test_live_area_demand_and_zero_stock_alert():
    section("build_live_area_demand extends area_demand with THIS build's live counts")
    adem_districts = [
        {"district": "D15", "area": "Katong", "unmatched_waiting": 10, "supply_gap": 10, "sourcing_priority": "HIGH"},
        {"district": "D16", "area": "Bedok", "unmatched_waiting": 5, "supply_gap": 5, "sourcing_priority": "MED"},
    ]
    listings = [{"id": "LL1", "district": "D15"}]
    # D16's 3 waiting tenants: 2 recovered from a single named building (district_source
    # "known_place" -- e.g. all 3 named "Cherryhill"), 1 genuinely district-wide (stated
    # outright, no district_source at all). item 5: the payload must let a reader tell
    # "10 people asked about ONE building" apart from real district-wide demand.
    tenants = [{"district": "D15"}, {"district": "D15"},
               {"district": "D16", "district_source": "known_place"},
               {"district": "D16", "district_source": "known_place"},
               {"district": "D16", "district_source": None}]
    rows = ed.build_live_area_demand(adem_districts, listings, tenants)
    by_d = {r["district"]: r for r in rows}
    check("existing area_demand fields carried through unchanged",
          by_d["D15"]["unmatched_waiting"] == 10 and by_d["D15"]["sourcing_priority"] == "HIGH")
    check("live_available_listings counts THIS build's listings, not the static snapshot",
          by_d["D15"]["live_available_listings"] == 1, f"got {by_d['D15']}")
    check("live_waiting_tenants counts THIS build's tenants",
          by_d["D16"]["live_waiting_tenants"] == 3, f"got {by_d['D16']}")
    check("live_waiting_building_level isolates the known_place (single-building) subset",
          by_d["D16"]["live_waiting_building_level"] == 2, f"got {by_d['D16']}")
    check("D15 has zero building-level waiting (neither tenant carries district_source)",
          by_d["D15"]["live_waiting_building_level"] == 0)
    check("live_gap floors at 0 (never negative)",
          by_d["D15"]["live_gap"] == max(2 - 1, 0), f"got {by_d['D15']}")

    section("build_zero_stock_alert: >=3 waiting AND 0 available listings")
    alert = ed.build_zero_stock_alert(rows, min_waiting=3)
    check("D16 qualifies (3 waiting, 0 listings)", [r["district"] for r in alert] == ["D16"], f"got {alert}")
    check("D15 does not qualify (has a listing)", "D15" not in {r["district"] for r in alert})
    check("the alert row carries the building-vs-district breakdown through (item 5) -- "
          "2 of D16's 3 waiting are building-level, not genuine district-wide demand",
          alert[0]["live_waiting_building_level"] == 2, f"got {alert[0]}")


# ================================== landlord responsiveness [idea 24] =======
def test_landlord_responsiveness():
    section("_chat_asks: bursts collapse into one ask, unanswered stays open, latency measured from the last nag")
    rows_answered = [
        ("hi is the room still available", "2026-08-01T09:00:00+08:00", 1),
        ("just checking in", "2026-08-01T09:05:00+08:00", 1),          # same ask (burst)
        ("yes still available", "2026-08-01T11:05:00+08:00", 0),        # reply closes it
    ]
    asks = ed._chat_asks(rows_answered)
    check("a burst of outbound messages collapses into ONE ask", len(asks) == 1, f"got {asks}")
    check("latency measured from the LAST message of the burst (09:05) to the reply (11:05) = 2h",
          abs((asks[0]["answered_at"] - asks[0]["end"]).total_seconds() - 2 * 3600) < 1)

    rows_gap = [
        ("hello?", "2026-08-01T09:00:00+08:00", 1),
        ("following up", "2026-08-05T09:00:00+08:00", 1),  # >24h later, still no reply -- a SEPARATE ask
    ]
    asks_gap = ed._chat_asks(rows_gap)
    check("a >24h gap with no reply splits into two asks, the first left unanswered",
          len(asks_gap) == 2 and asks_gap[0]["answered_at"] is None, f"got {asks_gap}")

    rows_trailing = [("still nothing back?", "2026-08-01T09:00:00+08:00", 1)]
    asks_trailing = ed._chat_asks(rows_trailing)
    check("chat ends on an outbound message with no reply -> unanswered",
          len(asks_trailing) == 1 and asks_trailing[0]["answered_at"] is None)

    section("_chat_asks [item 1 fix]: a live back-and-forth doesn't fragment into a fake ask per turn")
    rows_live_chat = [
        ("hi is unit available", "2026-08-01T09:00:00+08:00", 1),   # first ever -> genuine ask
        ("yes", "2026-08-01T09:01:00+08:00", 0),                     # closes it, 1 min latency
        ("great can I view sat", "2026-08-01T09:02:00+08:00", 1),    # 1 min after landlord's reply --
                                                                       # continuing the SAME live exchange,
                                                                       # not a fresh ask (absorbed)
        ("sure 2pm works", "2026-08-01T09:03:00+08:00", 0),          # nothing pending to close
        ("perfect thanks", "2026-08-01T09:04:00+08:00", 1),          # still live, still absorbed
    ]
    asks_live = ed._chat_asks(rows_live_chat)
    check("5 messages, 1 real question, but only ONE timed ask (not one per turn boundary)",
          len(asks_live) == 1, f"got {asks_live}")
    check("that one ask's latency is the genuine 1 minute, not diluted/inflated by the live chatter",
          abs((asks_live[0]["answered_at"] - asks_live[0]["end"]).total_seconds() - 60) < 1)

    section("_chat_asks: a genuine ask AFTER the landlord has gone quiet a meaningful while still gets timed")
    rows_after_silence = rows_live_chat + [
        ("still there? following up on the room", "2026-08-04T09:00:00+08:00", 1),  # landlord silent since
                                                                                       # 09:03 -- 3 days later,
                                                                                       # well past the gate
        ("sorry yes still avail", "2026-08-04T09:10:00+08:00", 0),
    ]
    asks_after = ed._chat_asks(rows_after_silence)
    check("the follow-up after real silence opens a SECOND genuine, timed ask",
          len(asks_after) == 2, f"got {asks_after}")
    check("its latency is the real 10 minutes", abs((asks_after[1]["answered_at"] - asks_after[1]["end"])
          .total_seconds() - 600) < 1)

    section("build_landlord_responsiveness: minutes not hours, worst-case surfaces a vanishing landlord, "
            "re-sorted on (unanswered, worst-case, median) rather than median alone")
    conn = _FakeWaConn({
        "jidFast": [("hi", "2026-08-01T09:00:00+08:00", 1), ("yes available", "2026-08-01T09:06:00+08:00", 0)],
        "jidSlow": [("hi", "2026-08-01T09:00:00+08:00", 1), ("yes available", "2026-08-03T09:00:00+08:00", 0)],
        "jidGhost": [("hi are you there", "2026-08-01T09:00:00+08:00", 1)],
        # LL039-shaped: usually replies in a couple of minutes (3 fast, genuinely separate
        # asks, each after a real >2h gap) but ONE ask vanishes for 5 days -- exactly the
        # case a median alone hides.
        "jidChattyFlaky": [
            ("hi is unit available", "2026-08-01T09:00:00+08:00", 1),
            ("yes", "2026-08-01T09:01:00+08:00", 0),
            ("great can I view sat", "2026-08-01T09:02:00+08:00", 1),   # live chatter, absorbed
            ("sure 2pm works", "2026-08-01T09:03:00+08:00", 0),
            ("perfect thanks", "2026-08-01T09:04:00+08:00", 1),        # live chatter, absorbed
            ("hi again", "2026-08-02T09:00:00+08:00", 1),
            ("yes still avail", "2026-08-02T09:03:00+08:00", 0),
            ("checking in", "2026-08-03T09:00:00+08:00", 1),
            ("yes", "2026-08-03T09:02:00+08:00", 0),
            ("still keen to close?", "2026-08-04T09:00:00+08:00", 1),
            ("yes sorry been busy", "2026-08-09T09:00:00+08:00", 0),   # 5 days later
        ],
    })
    landlords = [
        fake_landlord(id="LLF", landlord_name="Fast Landlord", chat_jid="jidFast"),
        fake_landlord(id="LLS", landlord_name="Slow Landlord", chat_jid="jidSlow"),
        fake_landlord(id="LLG", landlord_name="Ghost Landlord", chat_jid="jidGhost"),
        fake_landlord(id="LLC", landlord_name="Chatty Flaky Landlord", chat_jid="jidChattyFlaky"),
        fake_landlord(id="LLN", landlord_name="No Chat Landlord", chat_jid=""),
    ]
    resp = ed.build_landlord_responsiveness(conn, landlords)
    by_id = {r["id"]: r for r in resp}
    check("landlord with no chat_jid is left out entirely, never a fabricated 0",
          "LLN" not in by_id, f"got {list(by_id)}")
    check("fast landlord: median/worst-case both in MINUTES (6, not 0.1h) and 0 unanswered",
          by_id["LLF"]["median_reply_minutes"] == 6 and by_id["LLF"]["worst_case_reply_minutes"] == 6
          and by_id["LLF"]["unanswered_count"] == 0, f"got {by_id['LLF']}")
    check("ghost landlord (never replied) has 1 unanswered and no median/worst-case",
          by_id["LLG"]["unanswered_count"] == 1 and by_id["LLG"]["median_reply_minutes"] is None
          and by_id["LLG"]["worst_case_reply_minutes"] is None, f"got {by_id['LLG']}")
    check("chatty-flaky landlord: only 4 real asks (not one per live-chat turn)",
          by_id["LLC"]["n_asks"] == 4, f"got {by_id['LLC']}")
    check("its median stays low (2.5 min) -- looks great on a median-only view",
          by_id["LLC"]["median_reply_minutes"] == 2.5, f"got {by_id['LLC']}")
    check("but worst_case_reply_minutes exposes the real 5 day vanish (7200 min), invisible in the median",
          by_id["LLC"]["worst_case_reply_minutes"] == 7200, f"got {by_id['LLC']}")
    check("re-sort: LLF (worst-case 6 min) now ranks ABOVE LLC (worst-case 7200 min) despite LLC's "
          "lower median -- the old median-only sort would have ranked the chattiest/flakiest landlord "
          "first, which was the bug",
          resp.index(by_id["LLF"]) < resp.index(by_id["LLC"]), f"got {[r['id'] for r in resp]}")
    check("ghost (1 unanswered) ranks last regardless of worst-case",
          resp[-1]["id"] == "LLG", f"got {[r['id'] for r in resp]}")
    check("no connection (bridge unavailable) -> empty list, never fabricated",
          ed.build_landlord_responsiveness(None, landlords) == [])


# =========================================== price vs closes [idea 26] ======
def test_price_check():
    section("build_price_check: bands AND flags suppressed below min_n, never a confident band on 1-2 points")
    landlords = [
        fake_landlord(id="LLC1", status="closed (tenanted)", district="D19", rent_min=1400, rent_max=1400),
        fake_landlord(id="LLC2", status="closed (tenanted)", district="D19", rent_min=1300, rent_max=1300),
        # only 2 closes in D22 -- must be suppressed entirely, not shown as a low-confidence band
        fake_landlord(id="LLC3", status="closed (tenanted)", district="D22", rent_min=1200, rent_max=1200),
        fake_landlord(id="LLC4", status="closed (tenanted)", district="D22", rent_min=1100, rent_max=1100),
    ]
    pc = ed.build_price_check(landlords, [], min_n=3)
    check("a district with n=2 closes is suppressed outright, not emitted as a shaky band",
          not any(b["district"] == "D22" for b in pc["bands"]), f"got {pc['bands']}")
    check("D19 with n<3 (only 2 closes) is also suppressed", pc["bands"] == [], f"got {pc['bands']}")

    landlords3 = landlords + [
        fake_landlord(id="LLC5", status="closed (tenanted)", district="D19", rent_min=1500, rent_max=1500)]
    pc3 = ed.build_price_check(landlords3, [], min_n=3)
    band = next(b for b in pc3["bands"] if b["district"] == "D19")
    check("n=3 in D19 -> a band is emitted, WITH its sample size",
          band["n"] == 3 and band["median"] == 1400, f"got {band}")

    over = {"id": "LO1", "name": "Overpriced", "district": "D19", "rent_min": 2500, "rent_max": 2500}
    under = {"id": "LO2", "name": "Underpriced", "district": "D19", "rent_min": 500, "rent_max": 500}
    fine = {"id": "LO3", "name": "In band", "district": "D19", "rent_min": 1450, "rent_max": 1450}
    no_price = {"id": "LO4", "name": "No price", "district": "D19", "rent_min": None, "rent_max": None}
    pc4 = ed.build_price_check(landlords3, [over, under, fine, no_price], min_n=3)
    flags_by_id = {f["listing_id"]: f for f in pc4["flags"]}
    check("well above the band -> flagged above_market", flags_by_id["LO1"]["direction"] == "above_market")
    check("well below the band -> flagged below_market", flags_by_id["LO2"]["direction"] == "below_market")
    check("within the band -> not flagged", "LO3" not in flags_by_id)
    check("no price data -> skipped, never guessed", "LO4" not in flags_by_id)
    check("every flag carries the band's sample size", flags_by_id["LO1"]["band_n"] == 3)

    section("build_price_check [item 2]: LL007-shaped mis-parse -- rent_min/rent_max mix a room "
            "price with an unrelated whole-unit price for the SAME record")
    mixed_room_and_whole = fake_landlord(
        id="LLC6", status="closed (tenanted)", district="D19", property_type="HDB EA flat room",
        rooms_and_rent="Common $850 (was $1,000); whole unit $5,000", rent_min=850, rent_max=5000)
    check("_room_rent_for_band prefers rent_max but rejects it as out-of-bound and falls back to "
          "the genuine in-bound rent_min",
          ed._room_rent_for_band(mixed_room_and_whole) == 850,
          f"got {ed._room_rent_for_band(mixed_room_and_whole)}")
    pc_mixed = ed.build_price_check(landlords3 + [mixed_room_and_whole], [], min_n=3)
    band_d19 = next(b for b in pc_mixed["bands"] if b["district"] == "D19")
    check("D19 band now n=4 (LLC1/2/5 + the recovered 850), max is the genuine 1500 -- "
          "the raw rent_max=5000 whole-unit price never enters the band",
          band_d19["n"] == 4 and band_d19["min"] == 850 and band_d19["max"] == 1500,
          f"got {band_d19}")

    section("build_price_check [item 2]: LL012-shaped multi-room record excluded outright, "
            "not sanity-bounded in")
    multi_room = fake_landlord(
        id="LLC7", status="closed (tenanted)", district="D22", property_type="Condo (multi-room, 2 units)",
        rooms_and_rent="Caspian: master $2,200 now/CC3 $1,100; Summerdale: master $2,000/SC5 $1,000",
        rent_min=1000, rent_max=2200)
    check("a multi-room property_type yields no usable per-room rent at all",
          ed._room_rent_for_band(multi_room) is None)
    landlords_d22 = [
        fake_landlord(id="LLC8", status="closed (tenanted)", district="D22", rent_min=1300, rent_max=1300),
        fake_landlord(id="LLC9", status="closed (tenanted)", district="D22", rent_min=1200, rent_max=1200),
        multi_room,
    ]
    pc_multi = ed.build_price_check(landlords_d22, [], min_n=3)
    check("with the multi-room record excluded, D22 has only 2 genuine room closes -- "
          "suppressed below min_n rather than emitting a band inflated by the multi-room aggregate",
          not any(b["district"] == "D22" for b in pc_multi["bands"]), f"got {pc_multi['bands']}")

    section("build_price_check: a rent outside the ROOM_RENT bound (mis-parse noise) is rejected, "
            "not just multi-room property types")
    out_of_bound = fake_landlord(id="LLC10", status="closed (tenanted)", district="D25",
                                  property_type="HDB common room", rent_min=None, rent_max=50)
    check("a rent far below any plausible room rent -> no usable band value",
          ed._room_rent_for_band(out_of_bound) is None)


# ============================================ days-to-fill [idea 27] ========
def test_days_to_fill():
    section("build_days_to_fill: suppressed to insufficient_data with 0 usable (first_seen, close) pairs")
    landlords_no_data = [fake_landlord(id="LLF1", status="closed (tenanted 9 Jul 2026)", district="D15")]
    dtf = ed.build_days_to_fill(landlords_no_data, {}, TODAY, min_n=3)
    check("no seen-registry entry for the closed listing -> 0 usable samples",
          dtf["overall"]["n"] == 0 and dtf["overall"]["status"] == "insufficient_data",
          f"got {dtf['overall']}")
    check("median_days is None when insufficient, never a guessed number",
          dtf["overall"]["median_days"] is None)
    check("by_district stays empty below min_n", dtf["by_district"] == [])

    section("with a real first_seen AND a parseable close date, a genuine sample is counted")
    landlords_with_data = [
        fake_landlord(id="LLF2", status="closed (tenanted 15 Jul 2026)", district="D15"),
        fake_landlord(id="LLF3", status="closed (tenanted 20 Jul 2026)", district="D15"),
        fake_landlord(id="LLF4", status="closed (tenanted 25 Jul 2026)", district="D15"),
    ]
    seen = {"LLF2": "2026-07-01", "LLF3": "2026-07-01", "LLF4": "2026-07-01"}  # 14/19/24 days to fill
    dtf2 = ed.build_days_to_fill(landlords_with_data, seen, TODAY, min_n=3)
    check("3 usable samples -> status ok", dtf2["overall"]["status"] == "ok", f"got {dtf2['overall']}")
    check("median_days computed from the 3 real samples", dtf2["overall"]["median_days"] == 19,
          f"got {dtf2['overall']}")
    check("by_district emits D15 once n reaches min_n", any(b["district"] == "D15" for b in dtf2["by_district"]))


# ============================================ stale landlord chase [28] =====
def test_stale_landlord_chase():
    section("build_stale_landlord_chase: overdue listings only, most overdue first")
    listings = [
        {"id": "LL1", "name": "Fresh", "district": "D15", "phone": "1", "reconfirm_due": False, "days_since_confirmed": 2},
        {"id": "LL2", "name": "Overdue A", "district": "D16", "phone": "2", "reconfirm_due": True, "days_since_confirmed": 20},
        {"id": "LL3", "name": "Overdue B", "district": "D17", "phone": "3", "reconfirm_due": True, "days_since_confirmed": 40},
    ]
    chase = ed.build_stale_landlord_chase(listings)
    check("only reconfirm_due listings appear", {r["id"] for r in chase} == {"LL2", "LL3"}, f"got {chase}")
    check("most overdue (highest days_since_confirmed) ranks first",
          [r["id"] for r in chase] == ["LL3", "LL2"], f"got {[r['id'] for r in chase]}")


# ================================================ learning block [11] =======
def test_learning_block():
    section("build_learning_block: honestly dormant, never fakes usable triples")
    lb = ed.build_learning_block()
    check("usable_triples is 0 (no local source links tenant to closed listing)",
          lb["usable_triples"] == 0, f"got {lb}")
    check("needed_for_meaningful_fit is a positive floor", lb["needed_for_meaningful_fit"] > 0)
    check("status reads as the spec's literal phrasing",
          lb["status"] == f"dormant, needs {lb['needed_for_meaningful_fit']} more closes", f"got {lb['status']}")
    check("note explains WHY (no local linkage + CRM unreachable offline), not just the number",
          "Postgres" in lb["note"] and "closed_won" in lb["note"], f"got {lb['note']}")
    check("source_wired=False [item 8]: an explicit, self-evident placeholder marker -- so "
          "usable_triples=0 can never be mistaken for a real computed zero once the CRM starts "
          "recording closes and this is still not wired up",
          lb["source_wired"] is False, f"got {lb}")


# ==================================== source availability [item 6] ==========
def test_source_availability():
    section("build_source_availability: explicit marker so 'computed, found nothing' is never "
            "confused with 'could not compute' (a WA bridge outage otherwise silently empties "
            "budget_contradictions and landlord_responsiveness with no trace)")
    check("bridge available -> wa_bridge True", ed.build_source_availability(object())["wa_bridge"] is True)
    check("bridge unavailable (None) -> wa_bridge False", ed.build_source_availability(None) == {"wa_bridge": False})


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


def test_supply_gap_chase():
    section("build_supply_gap_chase: who to call in starved districts, exclusions respected")
    tenants = ([{"preferred_districts": ["D14"]}] * 5
               + [{"preferred_districts": ["D16"]}] * 12
               + [{"preferred_districts": ["D9"]}] * 2)
    listings = [{"district": "D16"}, {"district": "D9"}, {"district": "D9"}]
    lls = [
        fake_landlord(id="LLC1", landlord_name="Dormant Gap", status="dormant", district="D14"),
        fake_landlord(id="LLC2", landlord_name="Stalled Gap", status="stalled", district="D16"),
        fake_landlord(id="LLC3", landlord_name="Active Gap", status="active", district="D14"),
        fake_landlord(id="LLC4", landlord_name="Dormant NoGap", status="dormant", district="D9"),
        fake_landlord(id="LLC5", landlord_name="DNC Gap", status="dormant", district="D14", do_not_contact=True),
        fake_landlord(id="LLC6", landlord_name="Excluded Gap", status="dormant", district="D14", phone="82890755"),
        fake_landlord(id="LLC7", landlord_name="Dropped Gap", status="closed (dropped)", district="D14"),
    ]
    rows = ed.build_supply_gap_chase(lls, listings, tenants, {"phones": ["82890755"]})
    got = [r["id"] for r in rows]
    check("dormant + stalled landlords in gap districts appear", set(got) == {"LLC1", "LLC2"}, str(got))
    check("active landlords are not chase targets (they are already live)", "LLC3" not in got)
    check("a district whose demand is met is not a gap", "LLC4" not in got, "D9 has 2 waiting / 2 live")
    check("do_not_contact never appears", "LLC5" not in got)
    check("exclusions-config phones never appear (the Anne rule)", "LLC6" not in got)
    check("closed landlords never appear", "LLC7" not in got)
    check("rows carry the demand numbers that justify the call",
          all(("waiting" in r and "live_supply" in r) for r in rows))


def test_closes_ledger_and_fee_patterns():
    section("record_closes: the available -> tenanted transition is stamped once; fee regexes")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "closes.json")
        prev = {"listings": [{"id": "LLX1"}, {"id": "LLX2"}]}
        lls = [fake_landlord(id="LLX1", status="closed (tenanted)"),
               fake_landlord(id="LLX2", status="active"),
               fake_landlord(id="LLX3", status="closed (tenanted)")]  # never seen live by prev
        ledger = ed.record_closes(lls, prev, TODAY, path=path)
        check("a listing live last build and tenanted now is stamped today",
              ledger.get("LLX1") == TODAY.isoformat(), str(ledger))
        check("a still-active listing is not stamped", "LLX2" not in ledger)
        check("a close never observed live is not guessed", "LLX3" not in ledger)
        again = ed.record_closes(lls, prev, datetime.date(2030, 1, 1), path=path)
        check("re-running never restamps an existing close (idempotent)",
              again.get("LLX1") == TODAY.isoformat(), str(again))
        dtf = ed.build_days_to_fill([fake_landlord(id="LLX1", status="closed (tenanted)")],
                                    {"LLX1": (TODAY - datetime.timedelta(days=9)).isoformat()},
                                    TODAY, min_n=1, closes=ledger)
        check("days_to_fill uses the ledger close date",
              (dtf.get("overall") or {}).get("median_days") == 9, str(dtf))

    pos = ["I am willing to pay the agent fee", "can pay commission no problem",
           "ok with the agent fee", "愿意付中介费"]
    neg = ["no agent fee right?", "can you waive the fee", "I don't want to pay agent fee",
           "looking for fee free room", "不付中介"]
    for m in pos:
        check("fee-positive: " + m[:30], bool(ed.FEE_POS_RE.search(m)) and not ed.FEE_NEG_RE.search(m))
    for m in neg:
        check("fee-negative never counts: " + m[:30],
              not (ed.FEE_POS_RE.search(m) and not ed.FEE_NEG_RE.search(m)))


def test_queue_cold_rule():
    section("queue_drafts: 45 day dead-lead rule and no signal refusal at dispatch time")
    import queue_drafts as qd  # noqa: E402
    today = datetime.date(2026, 8, 11)

    fresh = {"id": "T1", "last_contact": "2026-08-10", "jid": "9000000190001@lid"}
    # 15 days: dead under the old 5 day value, LIVE under the dead-lead rule.
    # This fixture is the regression lock for the 17 Aug widening — if the rule ever
    # drifts back below 30, this is the check that fails first.
    midband = {"id": "T2", "last_contact": "2026-07-27", "jid": "9000000290001@lid"}
    dead = {"id": "T5", "last_contact": "2026-06-25", "jid": "9000000590001@lid"}  # 47 days
    nosignal = {"id": "T3", "last_contact": "", "jid": "9000000390001@lid"}
    nojid = {"id": "T4", "last_contact": "2026-08-10"}
    by_id = {t["id"]: t for t in (fresh, midband, dead, nosignal, nojid)}

    items = [{"tenant_id": t["id"], "name": "Tan Ah Test", "phone": "90000001", "message": "hi"}
             for t in (fresh, midband, dead, nosignal, nojid)]
    items.append({"tenant_id": "T_UNKNOWN", "name": "Ghost Test", "phone": "", "message": "hi"})

    # wa_conn None exercises the documented degraded path (bridge unavailable)
    approved, refused, skipped, _dups = qd.classify_items(items, by_id, None, today)
    approved_ids = [a["tenant_id"] for a in approved]
    refused_ids = [r[0] for r in refused]
    skipped_ids = [s[0] for s in skipped]

    check("fresh tenant is approved", "T1" in approved_ids, str(approved_ids))
    check("15d tenant is approved — live under the dead-lead rule, was wrongly refused at 5",
          "T2" in approved_ids, str(approved_ids))
    check("dead >45d tenant is refused", "T5" in refused_ids, str(refused_ids))
    check("no signal tenant is refused, never guessed fresh", "T3" in refused_ids, str(refused_ids))
    check("tenant with no jid is skipped, never guessed", "T4" in skipped_ids, str(skipped_ids))
    check("unknown tenant id is skipped", "T_UNKNOWN" in skipped_ids, str(skipped_ids))
    check("approved items carry the resolved jid",
          all(a.get("jid") for a in approved), "an approved item had no jid")

    # Drive the boundary through classify_items, not freshest_days: the day arithmetic
    # being right proves nothing about where the refusal actually falls.
    def verdict(last_contact):
        t = {"id": "TB", "last_contact": last_contact, "jid": "9000000690001@lid"}
        item = [{"tenant_id": "TB", "name": "Boundary Test", "phone": "90000006", "message": "hi"}]
        ap, _rf, _sk, _dp = qd.classify_items(item, {"TB": t}, None, today)
        return "approved" if ap else "refused"

    check("45 day boundary: exactly 45 days still queues",
          verdict("2026-06-27") == "approved", verdict("2026-06-27"))
    check("45 day boundary: 46 days does not",
          verdict("2026-06-26") == "refused", verdict("2026-06-26"))
    # Named DEAD_DAYS, not COLD_DAYS: cold is a ranking signal, only dead may block
    # an action. scoring.js:63 records what conflating them cost last time.
    check("DEAD_DAYS matches the dead-lead rule (45, widened 21 Aug 2026)",
          qd.DEAD_DAYS == 45, str(qd.DEAD_DAYS))
    cfg = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                      "scripts", "matchmaker", "config.json")))
    check("DEAD_DAYS is read from config.json, the thresholds' single home",
          qd.DEAD_DAYS == cfg["dead_days"], f"{qd.DEAD_DAYS} vs config {cfg['dead_days']}")
    check("no COLD_DAYS constant survives to be conflated again",
          not hasattr(qd, "COLD_DAYS"))


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

    # 20 Aug 2026, 21:00 slot: the deploy shipped and aliased fine, but the CLI's
    # fast cache-restored build skipped the status lines carrying the dpl_ id, the
    # no-match grep failed the assignment under pipefail, and set -e killed the
    # script with zero output — BEFORE the -z guard written for exactly that case.
    frail = [l for l in sh.splitlines()
             if re.search(r'="\$\(.*\bgrep\b', l) and "|| true" not in l]
    check("every grep-parsing assignment tolerates a no-match (pipefail kills it silently otherwise)",
          frail == [], " / ".join(l.strip()[:70] for l in frail))
    check("the dpl_ id has an inspect fallback, not just the flaky CLI output parse",
          'vercel inspect "$URL"' in sh, "expected a vercel inspect fallback on the deployment URL")
    check("the deployment URL parse excludes the alias itself",
          'grep -vF "$PROD_ALIAS"' in sh,
          "inspecting the alias to verify the alias proves nothing")
    check("an unprovable alias move fails the deploy loudly",
          "an unproven deploy is a failed deploy" in sh,
          "the no-deploy-id branch must exit 1, not warn and continue")

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
    test_infer_district_extensions()
    test_build_tenants_district_inference()
    test_budget_contradiction_detection()
    test_build_tenants_budget_contradiction_integration()
    test_delta_computation()
    test_week_delta()
    test_exclusions_config_autocreate()
    test_state_files_fail_closed()
    test_key_id_safety()
    test_photo_url_join()
    test_abs_url()

    test_lifecycle_mapping()
    test_supply_overview()
    test_all_landlords_and_tenants_shape()
    test_sales_shape()
    test_revival_and_duplicate_phones()
    test_enrichment_queue()
    test_live_area_demand_and_zero_stock_alert()
    test_landlord_responsiveness()
    test_price_check()
    test_days_to_fill()
    test_stale_landlord_chase()
    test_learning_block()
    test_source_availability()
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
    test_supply_gap_chase()
    test_closes_ledger_and_fee_patterns()
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
