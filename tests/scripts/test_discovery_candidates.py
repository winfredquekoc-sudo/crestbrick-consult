#!/usr/bin/env python3
"""Self-test for scripts/discovery_candidates.py — builds a throwaway messages.db /
whatsapp.db / landlord-db.json / tenant-db.json / exclude-file on disk (sqlite3's
read-only URI mode needs a real file) and asserts the candidate list matches exactly
what SKILL.md's step 5b rules would keep: in-window direct chats, not a group /
broadcast / newsletter, not already in either database (any status) or the exclusion
list, phone resolved through the lid map, name pulled from whatsmeow_contacts.

Run directly:  python3 tests/scripts/test_discovery_candidates.py
"""
import datetime
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import discovery_candidates as DC

TODAY = datetime.date(2026, 9, 1)


def _mk_messages_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT,
            timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT, filename TEXT,
            url TEXT, media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB,
            file_length INTEGER, PRIMARY KEY (id, chat_jid));
        CREATE TABLE messages_archive (id TEXT, chat_jid TEXT, sender TEXT, content TEXT,
            timestamp NUM, is_from_me NUM, media_type TEXT, filename TEXT, url TEXT,
            media_key, file_sha256, file_enc_sha256, file_length INT);
    """)

    def add(jid, name, rows):
        con.execute("INSERT INTO chats VALUES (?, ?, ?)", (jid, name, rows[-1][1]))
        for from_me, ts, content in rows:
            con.execute(
                "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, jid, jid, content, ts, from_me),
            )

    # 1. A genuinely NEW unlabelled chat inside the 14 day window -> should surface.
    add("6591234567@lid", "Unknown", [
        (0, "2026-08-25 09:00:00+08:00", "hi is the room still available"),
        (1, "2026-08-25 09:05:00+08:00", "yes it is, want to view?"),
        (0, "2026-08-30 10:00:00+08:00", "yes please, this saturday"),
    ])

    # 2. A chat whose FIRST message is outside the window (old chat, recent reply
    #    only) -> must NOT surface even though it has recent activity.
    add("6598765432@s.whatsapp.net", "Old Contact", [
        (0, "2026-06-01 09:00:00+08:00", "hello"),
        (1, "2026-08-31 09:00:00+08:00", "still around?"),
    ])

    # 3. A group chat inside the window -> excluded by chat_jid pattern.
    add("120363000000000000@g.us", "Some Group", [
        (0, "2026-08-28 09:00:00+08:00", "group message"),
    ])

    # 4. A chat inside the window whose phone is ALREADY in landlord-db.json
    #    (closed status even) -> must be skipped as already known.
    add("6511112222@s.whatsapp.net", "Known Landlord", [
        (0, "2026-08-26 09:00:00+08:00", "unit still closed already right"),
    ])

    # 5. A chat inside the window whose phone is on the exclusion list -> skipped.
    add("6533334444@s.whatsapp.net", "Colleague Person", [
        (0, "2026-08-27 09:00:00+08:00", "hey it's me"),
    ])

    con.commit()
    con.close()


def _mk_whatsapp_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT);
        CREATE TABLE whatsmeow_contacts (our_jid TEXT, their_jid TEXT, first_name TEXT,
            full_name TEXT, push_name TEXT, business_name TEXT, redacted_phone TEXT);
    """)
    con.execute("INSERT INTO whatsmeow_lid_map VALUES ('6591234567', '6591234567')")
    con.execute(
        "INSERT INTO whatsmeow_contacts (their_jid, full_name, push_name) VALUES (?, ?, ?)",
        ("6591234567", "", "Jamie Test"),
    )
    con.commit()
    con.close()


def main():
    tmp = tempfile.mkdtemp(prefix="discovery-candidates-test-")
    messages_db = os.path.join(tmp, "messages.db")
    whatsapp_db = os.path.join(tmp, "whatsapp.db")
    landlord_db = os.path.join(tmp, "landlord-db.json")
    tenant_db = os.path.join(tmp, "tenant-db.json")
    exclude_file = os.path.join(tmp, "exclude.json")

    _mk_messages_db(messages_db)
    _mk_whatsapp_db(whatsapp_db)

    json.dump({"landlords": [
        {"id": "LL001", "phone": "6511112222", "status": "closed (tenanted)"},
    ]}, open(landlord_db, "w"))
    json.dump({"tenants": []}, open(tenant_db, "w"))
    json.dump({"non_clients": [
        {"phone": "6533334444", "label": "Colleague Person"},
    ]}, open(exclude_file, "w"))

    candidates = DC.find_candidates(
        messages_db=messages_db, whatsapp_db=whatsapp_db, since_days=14,
        landlord_db=landlord_db, tenant_db=tenant_db, exclude_file=exclude_file,
        today=TODAY,
    )

    jids = sorted(c["jid"] for c in candidates)
    assert jids == ["6591234567@lid"], f"expected exactly the one new chat, got {jids}"

    c = candidates[0]
    assert c["phone"] == "6591234567", f"lid should resolve to phone, got {c['phone']!r}"
    assert c["contact_name"] == "Jamie Test", f"name should come from whatsmeow_contacts, got {c['contact_name']!r}"
    assert c["first_message_date"] == "2026-08-25 09:00:00+08:00"
    assert c["message_count"] == 3
    assert len(c["snippets"]) >= 2, "expected a few snippets, not zero"
    assert all(len(s["text"]) <= DC.SNIPPET_TEXT_CHARS for s in c["snippets"])

    # normalize_phone: SG local convention, last 8 digits.
    assert DC.normalize_phone("+65 9123 4567") == "91234567"
    assert DC.normalize_phone("") == ""
    assert DC.normalize_phone(None) == ""

    print("test_discovery_candidates: OK (1 candidate surfaced, 4 correctly excluded)")


if __name__ == "__main__":
    main()
