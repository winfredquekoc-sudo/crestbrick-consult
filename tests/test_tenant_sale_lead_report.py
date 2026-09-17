#!/usr/bin/env python3
"""tests/test_tenant_sale_lead_report.py — scripts/tenant_sale_lead_report.py.
Pure assert test suite, stdlib only, repo convention:
    /usr/bin/python3 tests/test_tenant_sale_lead_report.py

All fixture people are invented (obviously fake ids/phones) — never real
tenant data.
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tenant_sale_lead_report as R  # noqa: E402

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
        "id": "T900", "name": "Test Tenant Aa", "phone": "6590000001", "jid": "9000000190001@lid",
        "status": "open", "contact_state": "active", "match_status": "active", "excluded": False,
        "listing_enquired": "", "preferred_location": "",
    }
    base.update(over)
    return base


def _mk_messages_db(path, rows_by_jid):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE messages (chat_jid TEXT, content TEXT, timestamp TEXT)")
    for jid, contents in rows_by_jid.items():
        for i, c in enumerate(contents):
            con.execute("INSERT INTO messages VALUES (?, ?, ?)", (jid, c, f"2026-08-{i+1:02d}"))
    con.commit()
    con.close()


# ------------------------------------------------------------------ markers
def test_marker_detection():
    section("marker detection")
    check("buy", R.has_sale_markers("looking to buy a resale flat"))
    check("purchase", R.has_sale_markers("planning a condo purchase next year"))
    check("bto", R.has_sale_markers("waiting on our BTO to complete"))
    check("resale flat", R.has_sale_markers("interested in a resale flat instead of renting"))
    check("plain rental text has no marker", not R.has_sale_markers("looking for a room near Tampines, budget $1200"))
    check("empty/none has no marker", not R.has_sale_markers("") and not R.has_sale_markers(None))


def test_amount_threshold():
    section("$ amount threshold")
    check("under threshold not flagged", not R.amount_over_threshold("budget is $1,200/mo"))
    check("over threshold flagged", R.amount_over_threshold("offer accepted at $650,000"))
    check("k suffix over threshold flagged (800k = 800,000)", R.amount_over_threshold("resale unit going for $800k"))
    check("k suffix under threshold not flagged (80k = 80,000)", not R.amount_over_threshold("deposit was $80k"))
    check("no dollar sign never matches", not R.amount_over_threshold("650000 is a lot of money"))


# ------------------------------------------------------------------ still looking filter
def test_only_still_looking_scanned():
    section("only Still looking tenants are scanned")
    rows = [
        fake_tenant(id="T901", status="closed (stale)", listing_enquired="want to buy a condo"),
        fake_tenant(id="T902", excluded=True, match_status="do_not_contact", listing_enquired="planning to buy BTO"),
        fake_tenant(id="T903", contact_state="found_place", match_status="found", listing_enquired="buying a resale flat"),
        fake_tenant(id="T904", listing_enquired="looking to buy a resale flat"),  # active, should flag
    ]
    still_looking, flagged = R.run_report(rows, None)
    check("only the active row counted as still looking", still_looking == 1, still_looking)
    check("only the active row flagged", flagged == ["T904"], flagged)


def test_profile_text_flagging():
    section("profile field flagging (no chat db)")
    rows = [
        fake_tenant(id="T905", preferred_location="Tampines, want to buy a resale flat here eventually"),
        fake_tenant(id="T906", preferred_location="Tampines, rental only"),
    ]
    still_looking, flagged = R.run_report(rows, None)
    check("still looking counts both", still_looking == 2, still_looking)
    check("only the sale-marker row flagged", flagged == ["T905"], flagged)


def test_chat_text_flagging():
    section("chat scan flags a tenant with no profile markers but sale talk in chat")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "fake_messages.db")
        _mk_messages_db(db_path, {
            "9000000190001@lid": ["hi, looking for a room to rent", "actually we decided to buy a BTO instead"],
            "9000000290002@lid": ["hi, looking for a room to rent, budget $1000"],
        })
        con = sqlite3.connect(db_path)
        rows = [
            fake_tenant(id="T907", jid="9000000190001@lid"),
            fake_tenant(id="T908", jid="9000000290002@lid"),
        ]
        still_looking, flagged = R.run_report(rows, con)
        con.close()
        check("both counted as still looking", still_looking == 2, still_looking)
        check("only the BTO chat flagged", flagged == ["T907"], flagged)


def test_no_chat_db_falls_back_gracefully():
    section("missing/unreadable messages db never crashes, just skips chat")
    con = R.open_msg_db("/nonexistent/path/messages.db")
    check("open_msg_db returns None for a bad path", con is None)
    rows = [fake_tenant(id="T909", listing_enquired="")]
    still_looking, flagged = R.run_report(rows, con)
    check("no crash, nothing flagged", flagged == [], flagged)


def test_output_never_includes_pii():
    section("summary carries only counts and ids")
    rows = [fake_tenant(id="T910", preferred_location="planning to buy a resale flat")]
    still_looking, flagged = R.run_report(rows, None)
    check("flagged list is ids only (strings), no name/phone leaked",
          flagged == ["T910"] and all(isinstance(x, str) and "Test Tenant" not in x for x in flagged), flagged)


def main():
    test_marker_detection()
    test_amount_threshold()
    test_only_still_looking_scanned()
    test_profile_text_flagging()
    test_chat_text_flagging()
    test_no_chat_db_falls_back_gracefully()
    test_output_never_includes_pii()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
