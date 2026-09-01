#!/usr/bin/env python3
"""discovery_candidates.py — deterministic replacement for the refresh-rental-dbs skill's
Part A step 5b: a raw SQL scan of WhatsApp messages.db for chats that might be a new,
not-yet-labelled landlord.

Why this exists: step 5b currently asks the HEADLESS MODEL (running on Haiku, no MCP,
against a pre-extracted delta file) to author its own SQL against messages.db, resolve
lid to phone, dedupe against both databases and the exclusion list, and only then
classify. That is a lot of exact, unforgiving plumbing for a cheap model working from a
prompt, and since the 27 Aug 2026 Haiku switch this has intermittently produced
landlords_scanned=0 for days with no alert (nobody was watching that field). This script
takes over EVERYTHING deterministic — the SQL, the lid resolution, the dedupe against
landlord-db.json / tenant-db.json / dm-followup-exclude.json — and emits a narrowed
candidate list. A model (or a human) is still needed to CLASSIFY each surviving
candidate as landlord / tenant / agent / colleague / ambiguous; this script never
authors SQL for that and never guesses a classification itself.

Read only on WhatsApp. Never writes to landlord-db.json, tenant-db.json, or any other
live database, and never sends a message. PII (phone numbers, names, message text)
only ever lands in the --out JSON file, which defaults to a path outside git.

CLI:
    python3 discovery_candidates.py [--since-days 14] [--out PATH]
        [--messages-db PATH] [--whatsapp-db PATH]
        [--landlord-db PATH] [--tenant-db PATH] [--exclude-file PATH]

Importable:
    from discovery_candidates import find_candidates
    candidates = find_candidates(messages_db=..., whatsapp_db=..., since_days=14,
                                  landlord_db=..., tenant_db=..., exclude_file=...)
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

DEFAULT_MESSAGES_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
DEFAULT_WHATSAPP_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")
DEFAULT_LANDLORD_DB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
DEFAULT_TENANT_DB = os.path.expanduser("~/crestbrick-consult/_templates/tenant-db.json")
DEFAULT_EXCLUDE_FILE = os.path.expanduser("~/.claude/state/dm-followup-exclude.json")
DEFAULT_OUT = os.path.expanduser("~/.claude/state/discovery-candidates.json")

DEFAULT_SINCE_DAYS = 14
SNIPPET_LIMIT = 6          # a "few recent message snippets" per candidate
SNIPPET_TEXT_CHARS = 300   # truncate any one message before it lands in the output

# Mirrors the SKILL.md step 5b SQL exactly: candidates are direct chats (never groups,
# broadcasts, or newsletters) whose FIRST EVER message, across BOTH the live messages
# table and the archive (recent rows rotate into the archive so reading only `messages`
# misses chats), falls inside the window. Timestamps are stored as plain SGT strings
# ("YYYY-MM-DD HH:MM:SS+08:00"); lexical string comparison against a YYYY-MM-DD prefix
# is correct without any timezone conversion (see SKILL.md's timezone note).
CANDIDATE_SQL = """
    SELECT chat_jid, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts, COUNT(*) AS n
    FROM (SELECT chat_jid, timestamp FROM messages
          UNION ALL
          SELECT chat_jid, timestamp FROM messages_archive)
    WHERE chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@broadcast'
      AND chat_jid NOT LIKE '%@newsletter'
    GROUP BY chat_jid
    HAVING MIN(timestamp) >= ?
    ORDER BY first_ts DESC
"""

SNIPPET_SQL = """
    SELECT is_from_me, timestamp, content FROM (
        SELECT is_from_me, timestamp, content FROM messages WHERE chat_jid = ?
        UNION ALL
        SELECT is_from_me, timestamp, content FROM messages_archive WHERE chat_jid = ?
    )
    WHERE content IS NOT NULL AND content != ''
    ORDER BY timestamp ASC
"""


def _ro(path):
    """Open a sqlite3 db strictly read only. Raises if the file does not exist."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=20)


def normalize_phone(raw):
    """Same convention as merge_duplicate_landlords.py / merge_duplicate_tenants.py /
    guard_db_ids.py: digits only, last 8 (SG local number length)."""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-8:] if len(digits) >= 8 else digits


def _bare_id(jid):
    return (jid or "").split("@")[0]


def _load_lid_map(whatsapp_db):
    """lid -> real phone number (pn), from whatsmeow_lid_map in whatsapp.db."""
    try:
        con = _ro(whatsapp_db)
    except FileNotFoundError:
        return {}
    try:
        return dict(con.execute("SELECT lid, pn FROM whatsmeow_lid_map"))
    finally:
        con.close()


def _load_contact_names(whatsapp_db):
    """bare jid id -> best-effort display name, from whatsmeow_contacts in whatsapp.db."""
    try:
        con = _ro(whatsapp_db)
    except FileNotFoundError:
        return {}
    try:
        rows = con.execute(
            "SELECT their_jid, full_name, push_name, first_name FROM whatsmeow_contacts"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    names = {}
    for their_jid, full_name, push_name, first_name in rows:
        name = full_name or push_name or first_name or ""
        if name:
            names[_bare_id(their_jid)] = name
    return names


def resolve_phone(jid, lid_map):
    """@lid jids resolve through whatsmeow_lid_map; @s.whatsapp.net jids carry the
    phone directly in the local part. Falls back to the bare id if unresolvable."""
    bare = _bare_id(jid)
    if jid.endswith("@lid"):
        return lid_map.get(bare, "")
    if jid.endswith("@s.whatsapp.net"):
        return bare
    return bare


def _load_known_phones_and_jids(db_path, records_key_candidates):
    """Every phone (normalized) and chat_jid already present in a landlord/tenant DB,
    at ANY status (including closed) — SKIP is unconditional per SKILL.md step 5b."""
    phones, jids = set(), set()
    if not os.path.exists(db_path):
        return phones, jids
    try:
        data = json.load(open(db_path))
    except (OSError, json.JSONDecodeError):
        return phones, jids
    rows = []
    for key in records_key_candidates:
        val = data.get(key)
        if isinstance(val, list):
            rows = val
            break
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = normalize_phone(row.get("phone"))
        if p:
            phones.add(p)
        j = row.get("chat_jid") or row.get("jid")
        if j:
            jids.add(j)
    return phones, jids


def _load_excluded_phones(exclude_file):
    """dm-followup-exclude.json's non_clients[].phone list — covers the Golden Rules
    colleagues (Wanni, Shaw, Madeleine, Darren, Amanda) and Don Chuang by phone, matched
    on full digits OR last 8 per that file's own documented convention."""
    excluded = set()
    if not os.path.exists(exclude_file):
        return excluded
    try:
        data = json.load(open(exclude_file))
    except (OSError, json.JSONDecodeError):
        return excluded
    for row in data.get("non_clients", []) or []:
        raw = str(row.get("phone") or "")
        digits = re.sub(r"\D", "", raw)
        if digits:
            excluded.add(digits)
            excluded.add(normalize_phone(digits))
    return excluded


def find_candidates(messages_db=DEFAULT_MESSAGES_DB, whatsapp_db=DEFAULT_WHATSAPP_DB,
                     since_days=DEFAULT_SINCE_DAYS, landlord_db=DEFAULT_LANDLORD_DB,
                     tenant_db=DEFAULT_TENANT_DB, exclude_file=DEFAULT_EXCLUDE_FILE,
                     today=None):
    """Pure-ish core: takes explicit paths (and an optional injected `today` date for
    tests), returns a list of candidate dicts. Never writes anything."""
    today = today or datetime.date.today()
    window_start = (today - datetime.timedelta(days=since_days)).isoformat()

    con = _ro(messages_db)
    try:
        raw_candidates = con.execute(CANDIDATE_SQL, (window_start,)).fetchall()
        chat_names = dict(con.execute("SELECT jid, name FROM chats"))

        lid_map = _load_lid_map(whatsapp_db)
        contact_names = _load_contact_names(whatsapp_db)

        known_phones, known_jids = set(), set()
        for db_path, keys in (
            (landlord_db, ["landlords"]),
            (tenant_db, ["tenants", "records"]),
        ):
            p, j = _load_known_phones_and_jids(db_path, keys)
            known_phones |= p
            known_jids |= j
        excluded_phones = _load_excluded_phones(exclude_file)

        candidates = []
        for chat_jid, first_ts, last_ts, n in raw_candidates:
            phone = resolve_phone(chat_jid, lid_map)
            norm = normalize_phone(phone)

            if chat_jid in known_jids:
                continue
            if norm and (norm in known_phones or norm in excluded_phones):
                continue
            if not norm and _bare_id(chat_jid) in excluded_phones:
                continue

            bare = _bare_id(chat_jid)
            name = contact_names.get(bare, "") or chat_names.get(chat_jid, "") or ""

            snippet_rows = con.execute(SNIPPET_SQL, (chat_jid, chat_jid)).fetchall()
            first_snips = snippet_rows[: SNIPPET_LIMIT // 2]
            last_snips = snippet_rows[-(SNIPPET_LIMIT - len(first_snips)):]
            seen_ts = set()
            snippets = []
            for is_from_me, ts, content in first_snips + last_snips:
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                snippets.append({
                    "ts": ts,
                    "from_me": bool(is_from_me),
                    "text": (content or "")[:SNIPPET_TEXT_CHARS],
                })
            snippets.sort(key=lambda s: s["ts"])

            candidates.append({
                "jid": chat_jid,
                "phone": phone,
                "contact_name": name,
                "first_message_date": first_ts,
                "last_message_date": last_ts,
                "message_count": n,
                "snippets": snippets,
            })
        return candidates
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--messages-db", default=DEFAULT_MESSAGES_DB)
    ap.add_argument("--whatsapp-db", default=DEFAULT_WHATSAPP_DB)
    ap.add_argument("--landlord-db", default=DEFAULT_LANDLORD_DB)
    ap.add_argument("--tenant-db", default=DEFAULT_TENANT_DB)
    ap.add_argument("--exclude-file", default=DEFAULT_EXCLUDE_FILE)
    args = ap.parse_args()

    try:
        candidates = find_candidates(
            messages_db=args.messages_db,
            whatsapp_db=args.whatsapp_db,
            since_days=args.since_days,
            landlord_db=args.landlord_db,
            tenant_db=args.tenant_db,
            exclude_file=args.exclude_file,
        )
    except FileNotFoundError as e:
        print(f"discovery_candidates: missing db {e} — nothing written", file=sys.stderr)
        sys.exit(1)

    out = {
        "generated": datetime.datetime.now().isoformat(),
        "since_days": args.since_days,
        "candidates_found": len(candidates),
        "candidates": candidates,
    }
    tmp = args.out + ".tmp"
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, args.out)
    print(f"discovery_candidates: {len(candidates)} candidate(s) since {args.since_days}d ago -> {args.out}")


if __name__ == "__main__":
    main()
