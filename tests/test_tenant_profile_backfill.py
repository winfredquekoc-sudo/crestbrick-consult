#!/usr/bin/env python3
"""tests/test_tenant_profile_backfill.py — scripts/tenant_profile_backfill.py's
pure parsing functions only (parse_message/_to_int/_clean_value/_norm_gender/
_parse_budget/_is_missing/candidate filter). No sqlite3, no WhatsApp bridge,
never reads real chat history or _templates/tenant-db.json — this script is
NOT run against live data, per the investigation's instruction.

Pure assert test suite, stdlib only, repo convention:
    /usr/bin/python3 tests/test_tenant_profile_backfill.py

All chat text below is synthetic, written for this test.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tenant_profile_backfill as B  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n== {title} ==")


def test_to_int():
    section("_to_int")
    check("plain", B._to_int("1200") == 1200)
    check("comma", B._to_int("1,200") == 1200)
    check("k suffix", B._to_int("1.2k") == 1200)
    check("m suffix", B._to_int("1.2m") == 1200000)
    check("million word", B._to_int("1.2 million") == 1200000)
    check("none input", B._to_int(None) is None)
    check("no digits", B._to_int("asap") is None)


def test_clean_value():
    section("_clean_value")
    check("strips bullet", B._clean_value("• Female") == "Female")
    check("drops format hint", B._clean_value("(e.g. Male) Female") == "Female")
    check("na values collapse to none", B._clean_value("n/a") is None and B._clean_value("-") is None)
    check("nil collapses to none", B._clean_value("nil") is None)
    check("real value kept", B._clean_value("  Nurse ") == "Nurse")


def test_norm_gender():
    section("_norm_gender")
    check("f -> Female", B._norm_gender("F") == "Female")
    check("female -> Female", B._norm_gender("female") == "Female")
    check("m -> Male", B._norm_gender("M") == "Male")
    check("couple kept verbatim", B._norm_gender("Couple") == "Couple")


def test_parse_budget():
    section("_parse_budget")
    out = {}
    B._parse_budget("1200", out)
    check("plain figure", out.get("budget") == 1200, out)

    out = {}
    B._parse_budget("1.2", out)
    check("bare decimal means thousands", out.get("budget") == 1200, out)

    out = {}
    B._parse_budget("$500 per pax", out)
    check("per-pax pricing NOT read as whole budget", "budget" not in out, out)
    check("per-pax pricing kept as a note", out.get("budget_note") == "$500 per pax", out)

    out = {}
    B._parse_budget("50", out)
    check("implausibly low rejected", "budget" not in out, out)


def test_is_missing():
    section("_is_missing")
    check("none is missing", B._is_missing("budget", None))
    check("empty string is missing", B._is_missing("name", ""))
    check("placeholder Unknown name is missing", B._is_missing("name", "Unknown (+65 group)"))
    check("real value not missing", not B._is_missing("name", "Tan Ah Test"))
    check("real number not missing", not B._is_missing("budget", 1200))


def test_parse_message_keyed_lines():
    section("parse_message: keyed lines")
    text = (
        "Name: Test Tenant Bb\n"
        "Nationality: Singaporean\n"
        "Ethnicity / Race: Chinese\n"
        "Gender: Female\n"
        "Pass: Citizen\n"
        "No. of Pax: 2\n"
        "Move in Date: 2026-08-01\n"
        "Lease term: 12 months\n"
        "Budget: $1,200\n"
    )
    out = B.parse_message(text)
    check("name", out.get("name") == "Test Tenant Bb", out)
    check("nationality", out.get("nationality") == "Singaporean", out)
    check("ethnicity via race key", out.get("ethnicity") == "Chinese", out)
    check("gender normalized", out.get("gender") == "Female", out)
    check("pass_type", out.get("pass_type") == "Citizen", out)
    check("no_of_pax", out.get("no_of_pax") == 2, out)
    check("move_in_date", out.get("move_in_date") == "2026-08-01", out)
    check("lease_term_months (already months)", out.get("lease_term_months") == 12, out)
    check("budget", out.get("budget") == 1200, out)


def test_parse_message_lease_years():
    section("parse_message: lease term year handling")
    check("bare '1' with no unit means 1 year -> 12 months",
          B.parse_message("Lease term: 1").get("lease_term_months") == 12)
    check("'1 year' -> 12 months",
          B.parse_message("Lease: 1 year").get("lease_term_months") == 12)
    check("'6 months' stays 6",
          B.parse_message("Lease term: 6 months").get("lease_term_months") == 6)


def test_parse_message_boilerplate_stripped():
    section("parse_message: portal boilerplate never read as tenant profile")
    text = "RENT - 606D Tampines Ave, Room, S$ 3,700 /mo\nHi Winfred, I am interested in this listing"
    out = B.parse_message(text)
    check("no budget leaked from boilerplate", "budget" not in out, out)
    check("no name leaked from boilerplate", "name" not in out, out)


def test_parse_message_zero_width_chars():
    section("parse_message: zero width chars stripped before matching")
    text = "Ethnicity⁠ / Nationality: Indian"
    out = B.parse_message(text)
    check("field still recognized with zero width char present", out.get("nationality") == "Indian", out)


def test_parse_message_prose_fallbacks():
    section("parse_message: prose fallbacks")
    check("pax prose fallback", B.parse_message("We are 3 pax looking for a room").get("no_of_pax") == 3)
    check("pass type prose fallback",
          B.parse_message("Only Employment Pass holders need apply").get("pass_type") == "Employment Pass")


def test_parse_message_na_values_not_filled():
    section("parse_message: NA style answers never fill a field")
    text = "Name: Test Tenant Cc\nBudget: n/a\nMove in Date: -"
    out = B.parse_message(text)
    check("real field filled", out.get("name") == "Test Tenant Cc", out)
    check("na budget not filled", "budget" not in out, out)
    check("dash move_in_date not filled", "move_in_date" not in out, out)


def test_candidate_filter_loosened_to_one_gap():
    section("candidate filter: single missing field now qualifies")
    tenant_one_gap = {"status": "open", "name": "Test Tenant Dd", "nationality": "Singaporean",
                       "pass_type": "Citizen", "no_of_pax": 1, "move_in_date": "2026-08-01",
                       "budget": None}
    tenant_zero_gap = {"status": "open", "name": "Test Tenant Ee", "nationality": "Singaporean",
                        "pass_type": "Citizen", "no_of_pax": 1, "move_in_date": "2026-08-01",
                        "budget": 1200}
    gaps_one = sum(1 for f in B.GAP_FIELDS if B._is_missing(f, tenant_one_gap.get(f)))
    gaps_zero = sum(1 for f in B.GAP_FIELDS if B._is_missing(f, tenant_zero_gap.get(f)))
    check("one gap counted", gaps_one == 1, gaps_one)
    check("one gap tenant is now a candidate (>=1 floor)", gaps_one >= 1)
    check("zero gap tenant is still never a candidate", gaps_zero == 0)


def main():
    test_to_int()
    test_clean_value()
    test_norm_gender()
    test_parse_budget()
    test_is_missing()
    test_parse_message_keyed_lines()
    test_parse_message_lease_years()
    test_parse_message_boilerplate_stripped()
    test_parse_message_zero_width_chars()
    test_parse_message_prose_fallbacks()
    test_parse_message_na_values_not_filled()
    test_candidate_filter_loosened_to_one_gap()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
