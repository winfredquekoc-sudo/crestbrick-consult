#!/usr/bin/env python3
"""tests/test_tenant_intake_join.py — scripts/tenant_intake_join.py.
Pure assert test suite, stdlib only, repo convention (see tests/matchmaker/test_export.py):
    /usr/bin/python3 tests/test_tenant_intake_join.py

All fixture people are invented (obviously fake names/phones, e.g. 9000000x) —
never real tenant data. Real data lives only in the gitignored _templates/
directory and is never read by this file.
"""
import copy
import datetime
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tenant_intake_join as J  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n== {title} ==")


def fake_tenant(**over):
    base = {
        "id": "T900", "name": "Test Tenant Aa", "phone": "+6590000001", "jid": "9000000190001@lid",
        "gender": "", "ethnicity": "", "nationality": "", "pass_type": "",
        "occupation": "", "no_of_pax": None, "move_in_date": "", "lease_term_months": None,
        "budget": None, "budget_min": "", "budget_max": "", "preferred_location": "",
        "status": "open", "contact_state": "active", "match_status": "active", "excluded": False,
    }
    base.update(over)
    return base


def fake_profile(**over):
    base = {
        "name": "Test Tenant Aa", "move_in_date": "2026-08-07", "pass_type": "Work Permit",
        "no_of_pax": 2, "nationality": "Singaporean", "gender": "Female",
        "lease_term_months": 12, "budget": 1200, "age": "27", "ethnicity": "Chinese",
        "occupation": "Nurse", "preferred_location": "Tampines", "employment_type": "Full time",
        "email": "test.tenant@example.com",
    }
    base.update(over)
    return base


def db_with(*tenants):
    return {"columns": ["id", "name", "phone", "budget"], "tenants": [copy.deepcopy(t) for t in tenants]}


def intake_with(*pairs):
    """pairs: (phone_key, profile_dict)."""
    return {"conversations": {pn: {"profile": prof} for pn, prof in pairs}}


# ------------------------------------------------------------------ fills
def test_fills_null_only():
    section("fills null fields only")
    db = db_with(fake_tenant())
    intake = intake_with(("6590000001", fake_profile()))
    summary = J.run_join(db, intake)
    t = db["tenants"][0]
    check("budget filled", t["budget"] == 1200, t)
    check("budget_max mirrors single figure", t["budget_max"] == 1200, t)
    check("move_in_date filled", t["move_in_date"] == "2026-08-07", t)
    check("no_of_pax filled", t["no_of_pax"] == 2, t)
    check("lease_term_months filled", t["lease_term_months"] == 12, t)
    check("preferred_location filled", t["preferred_location"] == "Tampines", t)
    check("nationality filled", t["nationality"] == "Singaporean", t)
    check("gender filled", t["gender"] == "Female", t)
    check("ethnicity filled", t["ethnicity"] == "Chinese", t)
    check("occupation filled", t["occupation"] == "Nurse", t)
    check("pass_type filled", t["pass_type"] == "Work Permit", t)
    check("age filled (new column)", t.get("age") == "27", t)
    check("email filled (new column)", t.get("email") == "test.tenant@example.com", t)
    check("employment_type NOT copied (not a mapped field)", "employment_type" not in t, t)
    check("district untouched (not this script's job)", "district" not in t or not t.get("district"), t)
    check("provenance recorded", "budget" in t.get("_backfill", {}) and t["_backfill"]["budget"]["src"] == "intake", t)
    check("columns metadata untouched by apply (age/email never appended)",
          db["columns"] == ["id", "name", "phone", "budget"], db["columns"])
    check("summary counts", summary["rows_matched"] == 1 and summary["rows_filled"] == 1, summary)


def test_never_overwrites():
    section("never overwrites an existing value")
    db = db_with(fake_tenant(budget=999, budget_max=999, move_in_date="2026-01-01", no_of_pax=5,
                             lease_term_months=6, nationality="Malaysian"))
    intake = intake_with(("6590000001", fake_profile()))
    J.run_join(db, intake)
    t = db["tenants"][0]
    check("existing budget kept", t["budget"] == 999, t)
    check("existing move_in_date kept", t["move_in_date"] == "2026-01-01", t)
    check("existing no_of_pax kept", t["no_of_pax"] == 5, t)
    check("existing lease_term_months kept", t["lease_term_months"] == 6, t)
    check("existing nationality kept", t["nationality"] == "Malaysian", t)
    # fields that WERE blank on this row still get filled
    check("blank field still filled", t["gender"] == "Female", t)


# ------------------------------------------------------------------ skip logic
def test_skips_closed_excluded_donotcontact_agent():
    section("skips closed / excluded / do_not_contact / agent rows")
    rows = [
        fake_tenant(id="T901", phone="+6590000002", status="closed (stale)"),
        fake_tenant(id="T902", phone="+6590000003", excluded=True, match_status="do_not_contact"),
        fake_tenant(id="T903", phone="+6590000004", contact_state="do_not_contact", match_status="do_not_contact"),
        fake_tenant(id="T904", phone="+6590000005", excluded=True, match_status="do_not_contact"),  # co-broke agent shape
        fake_tenant(id="T905", phone="+6590000006"),  # control: normal active row, should fill
    ]
    db = db_with(*rows)
    intake = intake_with(
        ("6590000002", fake_profile()), ("6590000003", fake_profile()),
        ("6590000004", fake_profile()), ("6590000005", fake_profile()),
        ("6590000006", fake_profile()),
    )
    summary = J.run_join(db, intake)
    by_id = {t["id"]: t for t in db["tenants"]}
    check("closed row untouched", by_id["T901"]["budget"] is None, by_id["T901"])
    check("excluded row untouched", by_id["T902"]["budget"] is None, by_id["T902"])
    check("do_not_contact row untouched", by_id["T903"]["budget"] is None, by_id["T903"])
    check("agent-shaped row untouched", by_id["T904"]["budget"] is None, by_id["T904"])
    check("control active row filled", by_id["T905"]["budget"] == 1200, by_id["T905"])
    check("skipped_status counts 4", summary["rows_skipped_status"] == 4, summary)
    check("only 1 matched (the control)", summary["rows_matched"] == 1, summary)


# ------------------------------------------------------------------ budget parsing
def test_budget_parsing_variants():
    section("budget parsing variants")
    check("plain int", J.parse_budget(1200) == {"budget": 1200, "budget_min": "", "budget_max": 1200})
    check("dollar sign + comma", J.parse_budget("$1,200") == {"budget": 1200, "budget_min": "", "budget_max": 1200})
    check("bare digits string", J.parse_budget("1200") == {"budget": 1200, "budget_min": "", "budget_max": 1200})
    check("k suffix", J.parse_budget("1.2k") == {"budget": 1200, "budget_min": "", "budget_max": 1200})
    check("range", J.parse_budget("1000 to 1500") == {"budget": "", "budget_min": 1000, "budget_max": 1500})
    check("range reversed order still sorted", J.parse_budget("1500 - 1000") == {"budget": "", "budget_min": 1000, "budget_max": 1500})
    check("implausibly low rejected", J.parse_budget(50) is None)
    check("implausibly high (looks like a sale price) rejected", J.parse_budget(650000) is None)
    check("empty/none rejected", J.parse_budget("") is None and J.parse_budget(None) is None)
    check("garbage text rejected", J.parse_budget("negotiable") is None)


def test_budget_group_never_partially_filled():
    section("budget: group is all-or-nothing")
    db = db_with(fake_tenant())
    intake = intake_with(("6590000001", fake_profile(budget="negotiable")))
    J.run_join(db, intake)
    t = db["tenants"][0]
    check("unparseable budget leaves all three blank", J.budget_blank(t), t)


# ------------------------------------------------------------------ date normalization
def test_date_normalization():
    section("move_in_date normalization")
    check("clean ISO passes through", J.parse_move_in("2026-08-07") == "2026-08-07")
    check("DD/MM/YYYY normalized", J.parse_move_in("07/08/2026") == "2026-08-07")
    check("label 'in Date:' stripped then parsed", J.parse_move_in("in Date: 2026-08-07") == "2026-08-07")
    check("label 'date:' stripped then parsed", J.parse_move_in("date:2026-08-07") == "2026-08-07")
    check("fuzzy 'asap' deliberately left unparsed", J.parse_move_in("in Date: asap") is None)
    check("fuzzy 'next month' deliberately left unparsed", J.parse_move_in("next month") is None)
    check("free text with no date deliberately left unparsed",
          J.parse_move_in("Responsible, trustworthy, working singaporean female.") is None)
    check("empty/none unparsed", J.parse_move_in("") is None and J.parse_move_in(None) is None)


# ------------------------------------------------------------------ phone matching
def test_phone_matching_variants():
    section("phone matching variants (with/without 65, @s.whatsapp.net, @lid)")
    db = db_with(
        fake_tenant(id="TA", phone="90000010", jid=""),                      # bare 8 digit local
        fake_tenant(id="TB", phone="+65 9000 0011", jid=""),                 # spaced, plussed
        fake_tenant(id="TC", phone="", jid="90000012@s.whatsapp.net"),       # jid only, s.whatsapp.net
        fake_tenant(id="TD", phone="", jid="6590000013@lid"),                # jid only, @lid, already 65-prefixed
    )
    intake = intake_with(
        ("6590000010", fake_profile()),
        ("6590000011", fake_profile()),
        ("6590000012", fake_profile()),
        ("6590000013", fake_profile()),
    )
    summary = J.run_join(db, intake)
    by_id = {t["id"]: t for t in db["tenants"]}
    for tid in ("TA", "TB", "TC", "TD"):
        check(f"{tid} matched via phone/jid normalisation", by_id[tid]["budget"] == 1200, by_id[tid])
    check("all 4 matched", summary["rows_matched"] == 4, summary)


# ------------------------------------------------------------------ sanitize_value: junk rejection
def test_sanitize_rejects_na_and_extended_junk():
    section("sanitize_value: NA + extended junk vocabulary rejected")
    for v in ["", "-", "n/a", "na", "tbc", "nil", "none", "nan", "?",
              "null", "unknown", "no", "yes", "as well",
              "N/A", "TBC", " Unknown "]:
        check(f"{v!r} rejected", J.sanitize_value("occupation", v) is None)
    check("real value still accepted", J.sanitize_value("occupation", "Nurse") == "Nurse")


def test_sanitize_rejects_leading_junk_chars():
    section("sanitize_value: leading [ . * rejected")
    check("leading [ rejected", J.sanitize_value("occupation", "[Not answered]") is None)
    check("leading . rejected", J.sanitize_value("occupation", ".changed") is None)
    check("leading * rejected", J.sanitize_value("occupation", "*redacted*") is None)
    check("mid string . kept", J.sanitize_value("occupation", "R&D Engineer") == "R&D Engineer")


def test_sanitize_rejects_question_mark():
    section("sanitize_value: any question mark rejected")
    check("trailing ? rejected", J.sanitize_value("occupation", "Engineer?") is None)
    check("embedded ? rejected", J.sanitize_value("occupation", "not sure?") is None)


def test_sanitize_rejects_cjk():
    section("sanitize_value: CJK script rejected")
    check("Chinese rejected", J.sanitize_value("occupation", "工程师") is None)
    check("Japanese kana rejected", J.sanitize_value("occupation", "エンジニア") is None)
    check("Korean hangul rejected", J.sanitize_value("occupation", "엔지니어") is None)
    check("latin text kept", J.sanitize_value("occupation", "Engineer") == "Engineer")


def test_sanitize_length_cap():
    section("sanitize_value: 40 char cap on capped fields")
    ok_40 = "a" * 40
    too_long = "a" * 41
    check("40 chars accepted", J.sanitize_value("occupation", ok_40) == ok_40)
    check("41 chars rejected", J.sanitize_value("occupation", too_long) is None)
    check("cap applies to nationality too", J.sanitize_value("nationality", too_long) is None)
    check("cap applies to name too (also clears the >=3 char floor)",
          J.sanitize_value("name", too_long) is None)
    check("preferred_location NOT length-capped (not in the named list)",
          J.sanitize_value("preferred_location", too_long) == too_long)


def test_sanitize_gender_whitelist():
    section("sanitize_value: gender whitelisted to male/female vocabulary")
    check("male -> Male", J.sanitize_value("gender", "male") == "Male")
    check("m -> Male", J.sanitize_value("gender", "M") == "Male")
    check("man -> Male", J.sanitize_value("gender", "man") == "Male")
    check("female -> Female", J.sanitize_value("gender", "female") == "Female")
    check("f -> Female", J.sanitize_value("gender", "F") == "Female")
    check("woman -> Female", J.sanitize_value("gender", "woman") == "Female")
    check("out of vocabulary rejected", J.sanitize_value("gender", "couple") is None)
    check("nonbinary text rejected (not whitelisted)", J.sanitize_value("gender", "prefer not to say") is None)


def test_sanitize_name_min_length():
    section("sanitize_value: name shorter than 3 chars rejected")
    check("2 char name rejected", J.sanitize_value("name", "Al") is None)
    check("3 char name accepted", J.sanitize_value("name", "Ali") == "Ali")
    check("min length does not apply to other fields", J.sanitize_value("occupation", "DJ") == "DJ")


def test_rejected_junk_counted_and_field_left_blank():
    section("junk rejection surfaces in rejected_junk and leaves the field blank")
    db = db_with(fake_tenant(name=""))
    intake = intake_with(("6590000001", fake_profile(occupation="unknown", gender="couple", name="Al")))
    summary = J.run_join(db, intake)
    t = db["tenants"][0]
    check("occupation left blank", blank_str(t.get("occupation")), t)
    check("gender left blank", blank_str(t.get("gender")), t)
    check("name left blank", blank_str(t.get("name")), t)
    check("rejected_junk counts occupation/gender/name",
          summary["rejected_junk"].get("occupation") == 1
          and summary["rejected_junk"].get("gender") == 1
          and summary["rejected_junk"].get("name") == 1,
          summary["rejected_junk"])
    check("no _backfill entries for the rejected fields",
          not any(f in t.get("_backfill", {}) for f in ("occupation", "gender", "name")), t)


def blank_str(v):
    return v is None or v == ""


# ------------------------------------------------------------------ shared phones
def test_shared_phone_skipped_never_crossfilled():
    section("shared phone: skip rows where >1 non protected tenant shares a phone key")
    rows = [
        fake_tenant(id="SA", phone="+6590000020", jid=""),
        fake_tenant(id="SB", phone="+6590000020", jid=""),  # same phone, second household member
        fake_tenant(id="SC", phone="+6590000021", jid=""),  # control: unique phone, should fill
    ]
    db = db_with(*rows)
    intake = intake_with(("6590000020", fake_profile()), ("6590000021", fake_profile()))
    summary = J.run_join(db, intake)
    by_id = {t["id"]: t for t in db["tenants"]}
    check("SA untouched", by_id["SA"]["budget"] is None, by_id["SA"])
    check("SB untouched", by_id["SB"]["budget"] is None, by_id["SB"])
    check("SC (unique phone) filled", by_id["SC"]["budget"] == 1200, by_id["SC"])
    check("skipped_shared_phone counts both shared rows", summary["rows_skipped_shared_phone"] == 2, summary)
    check("only the control matched", summary["rows_matched"] == 1, summary)


def test_shared_phone_ignores_protected_rows():
    section("shared phone: a protected row sharing a phone does not poison the count")
    rows = [
        fake_tenant(id="PA", phone="+6590000030", jid="", excluded=True, match_status="do_not_contact"),
        fake_tenant(id="PB", phone="+6590000030", jid=""),  # only non-protected owner of this phone
    ]
    db = db_with(*rows)
    intake = intake_with(("6590000030", fake_profile()))
    summary = J.run_join(db, intake)
    by_id = {t["id"]: t for t in db["tenants"]}
    check("PB (sole non-protected owner) filled", by_id["PB"]["budget"] == 1200, by_id["PB"])
    check("no shared phone skip triggered", summary["rows_skipped_shared_phone"] == 0, summary)


# ------------------------------------------------------------------ blank() extra tokens
def test_blank_treats_placeholder_tokens_as_blank():
    section("blank(): TBC/unknown/to confirm/null/na/n-a/-/? treated as blank (fill only)")
    for token in ["TBC", "tbc", "Unknown", "To Confirm", "NULL", "na", "N/A", "-", "?", "  tbc  "]:
        check(f"{token!r} is blank", J.blank(token), token)
    check("a real value is not blank", not J.blank("Tampines"))
    check("None is blank", J.blank(None))
    check("empty string is blank", J.blank(""))


def test_blank_token_gets_filled_never_overwrites_real_value():
    section("blank(): placeholder token in tenant-db gets filled by a real intake answer")
    db = db_with(fake_tenant(preferred_location="TBC", nationality="unknown"))
    intake = intake_with(("6590000001", fake_profile()))
    J.run_join(db, intake)
    t = db["tenants"][0]
    check("preferred_location TBC replaced with real answer", t["preferred_location"] == "Tampines", t)
    check("nationality unknown replaced with real answer", t["nationality"] == "Singaporean", t)


# ------------------------------------------------------------------ idempotency
def test_idempotent_second_run():
    section("idempotency: second run changes nothing")
    db = db_with(fake_tenant())
    intake = intake_with(("6590000001", fake_profile()))
    J.run_join(db, intake)
    after_first = copy.deepcopy(db)
    summary2 = J.run_join(db, intake)
    check("no further fields filled on second run", summary2["rows_filled"] == 0, summary2)
    check("state identical after second run", db == after_first)


# ------------------------------------------------------------------ CLI: dry run / apply / backup
def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def test_cli_dry_run_writes_nothing():
    section("CLI --dry-run (default) writes nothing")
    with tempfile.TemporaryDirectory() as tmp:
        tdb_path = os.path.join(tmp, "fake_tenant_db.json")
        intake_path = os.path.join(tmp, "fake_intake_state.json")
        db = db_with(fake_tenant())
        _write(tdb_path, db)
        _write(intake_path, intake_with(("6590000001", fake_profile())))
        before = open(tdb_path).read()

        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "tenant_intake_join.py"),
             "--tenant-db", tdb_path, "--intake-state", intake_path, "--json"],
            capture_output=True, text=True,
        )
        check("exits 0", r.returncode == 0, r.stderr)
        after = open(tdb_path).read()
        check("file byte-identical after dry run", before == after)
        out = json.loads(r.stdout)
        check("reports mode dry_run", out["mode"] == "dry_run", out)
        check("reports the fill it WOULD make", out["rows_filled"] == 1, out)
        check("no backup created on dry run", not any(f.startswith("fake_tenant_db.json.bak-") for f in os.listdir(tmp)))


def test_cli_apply_writes_atomically_with_backup():
    section("CLI --apply writes atomically and leaves a backup")
    with tempfile.TemporaryDirectory() as tmp:
        tdb_path = os.path.join(tmp, "fake_tenant_db.json")
        intake_path = os.path.join(tmp, "fake_intake_state.json")
        db = db_with(fake_tenant())
        _write(tdb_path, db)
        _write(intake_path, intake_with(("6590000001", fake_profile())))

        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "tenant_intake_join.py"),
             "--tenant-db", tdb_path, "--intake-state", intake_path, "--apply", "--json"],
            capture_output=True, text=True,
        )
        check("exits 0", r.returncode == 0, r.stderr)
        out = json.loads(r.stdout)
        check("reports mode apply", out["mode"] == "apply", out)
        backups = [f for f in os.listdir(tmp) if f.startswith("fake_tenant_db.json.bak-")]
        check("exactly one backup written", len(backups) == 1, backups)
        backup_db = json.load(open(os.path.join(tmp, backups[0])))
        check("backup holds the PRE-apply (unfilled) state",
              backup_db["tenants"][0]["budget"] is None, backup_db)
        new_db = json.load(open(tdb_path))
        check("live file now holds the filled state", new_db["tenants"][0]["budget"] == 1200, new_db)
        check("no leftover .tmp file", not os.path.exists(tdb_path + ".tmp"))

        # second --apply run is idempotent end to end (fresh backup, but no field changes)
        r2 = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "tenant_intake_join.py"),
             "--tenant-db", tdb_path, "--intake-state", intake_path, "--apply", "--json"],
            capture_output=True, text=True,
        )
        out2 = json.loads(r2.stdout)
        check("second apply fills nothing new", out2["rows_filled"] == 0, out2)


def test_cli_dry_run_and_apply_together_errors():
    section("CLI --dry-run --apply together is a usage error")
    with tempfile.TemporaryDirectory() as tmp:
        tdb_path = os.path.join(tmp, "fake_tenant_db.json")
        intake_path = os.path.join(tmp, "fake_intake_state.json")
        _write(tdb_path, db_with(fake_tenant()))
        _write(intake_path, intake_with(("6590000001", fake_profile())))
        before = open(tdb_path).read()

        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "tenant_intake_join.py"),
             "--tenant-db", tdb_path, "--intake-state", intake_path, "--dry-run", "--apply"],
            capture_output=True, text=True,
        )
        check("nonzero exit", r.returncode != 0, r.returncode)
        check("usage error on stderr", "not allowed with argument" in r.stderr or "mutually exclusive" in r.stderr, r.stderr)
        after = open(tdb_path).read()
        check("file untouched", before == after)
        check("no backup created", not any(f.startswith("fake_tenant_db.json.bak-") for f in os.listdir(tmp)))


def test_atomic_write_ends_with_trailing_newline():
    section("atomic_write_with_backup writes a trailing newline")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "db.json")
        _write(path, db_with(fake_tenant()))
        J.atomic_write_with_backup(path, {"a": 1})
        check("file ends with a newline", open(path).read().endswith("\n"))


def main():
    test_fills_null_only()
    test_never_overwrites()
    test_skips_closed_excluded_donotcontact_agent()
    test_budget_parsing_variants()
    test_budget_group_never_partially_filled()
    test_date_normalization()
    test_phone_matching_variants()
    test_sanitize_rejects_na_and_extended_junk()
    test_sanitize_rejects_leading_junk_chars()
    test_sanitize_rejects_question_mark()
    test_sanitize_rejects_cjk()
    test_sanitize_length_cap()
    test_sanitize_gender_whitelist()
    test_sanitize_name_min_length()
    test_rejected_junk_counted_and_field_left_blank()
    test_shared_phone_skipped_never_crossfilled()
    test_shared_phone_ignores_protected_rows()
    test_blank_treats_placeholder_tokens_as_blank()
    test_blank_token_gets_filled_never_overwrites_real_value()
    test_idempotent_second_run()
    test_cli_dry_run_writes_nothing()
    test_cli_apply_writes_atomically_with_backup()
    test_cli_dry_run_and_apply_together_errors()
    test_atomic_write_ends_with_trailing_newline()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
