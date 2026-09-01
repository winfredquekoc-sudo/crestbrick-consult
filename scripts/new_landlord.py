#!/usr/bin/env python3
"""
new_landlord.py — manual "quick-add a landlord" for landlords onboarded OUTSIDE
WhatsApp (phone call, portal app walk-in, in person).

Why: the refresh-rental-dbs skill only ever discovers landlords who message
Winfred on WhatsApp (saved contact label or chat content). A landlord he signs
up any other way is invisible to that pipeline until someone hand-adds them.
This script does that in seconds, deterministically — no LLM, no WhatsApp read.

What it does:
  - allocates the next collision-safe LL### id, taking the max over BOTH the
    live DB and the shared id-watermark (~/.claude/state/id-watermark.json),
    mirroring the same max-across-both logic scripts/guard_db_ids.py uses so a
    freed/tombstoned id is never handed to a new landlord
  - appends one correctly-shaped record to _templates/landlord-db.json,
    preserving every sibling key in the file (it is a dict with ~17-18 sibling
    keys plus the landlords[] array — this NEVER dumps the bare list)
  - stamps contact_label_source with the non-WhatsApp origin, e.g.
    "manual quick-add (phone call)"
  - writes atomically (tmp file + os.replace) with a dated backup, matching
    the convention in scripts/tighten_rental_dbs.py and sanitize_landlord_db.py
  - refuses (does not silently merge) when the phone number already exists —
    a shared phone can legitimately be two household members, so it surfaces
    the existing record(s) and requires --allow-duplicate-phone to proceed

CEA boundary: template-based data entry ONLY. This never sends a WhatsApp
message, never drafts client-facing copy, never gives advice, never negotiates.

Usage:
  python3 scripts/new_landlord.py \\
      --name "Jane Tan" --phone "91234567" \\
      --address "123 Example Street #01-01 S123456" \\
      --room-type "HDB 4-room, common room" --rent 1200 \\
      --district D19 --deal-type rent --source "phone call" \\
      --notes "prefers female tenant, no cooking"

Flags:
  --name            landlord name (required)
  --phone           landlord phone, any format (required)
  --address         property / unit address (required)
  --room-type       property_type / room description, e.g. "HDB 4-room, common room"
  --rent            rent, "1200" or a range "1200-1500"
  --rent-max        overrides the top of a --rent range if given separately
  --rooms-and-rent  free-text override for the rooms_and_rent field
  --district        D1-D28; auto-derived from a postal code in --address if omitted
  --deal-type       "rent" (default) or "sale"
  --status          record status (default "active")
  --source          how this landlord was onboarded, e.g. "phone call", "portal
                     app", "in person" (default "unspecified non-WhatsApp origin")
  --notes           free text, stored under requirements.other
  --follow-up       optional follow_up field text
  --allow-duplicate-phone   proceed even if the phone already exists in the DB
  --dry-run         print the record and exit; writes NOTHING (DB or watermark)
  --db PATH         target a different landlord-db.json (e.g. a scratch copy)
  --watermark PATH  target a different id-watermark.json (e.g. a scratch copy)

Defaults to WRITING _templates/landlord-db.json. Use --dry-run, or --db pointed
at a temp copy, to verify without touching the real files.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys

ROOT = os.path.expanduser("~/crestbrick-consult")
DEFAULT_LDB = os.path.join(ROOT, "_templates/landlord-db.json")
DEFAULT_WATERMARK = os.path.expanduser("~/.claude/state/id-watermark.json")

# SG postal sector (first 2 digits) -> district, same table as tighten_rental_dbs.py
_SECTORS = {
    "D1": "01 02 03 04 05 06", "D2": "07 08", "D3": "14 15 16", "D4": "09 10",
    "D5": "11 12 13", "D6": "17", "D7": "18 19", "D8": "20 21", "D9": "22 23",
    "D10": "24 25 26 27", "D11": "28 29 30", "D12": "31 32 33", "D13": "34 35 36 37",
    "D14": "38 39 40 41", "D15": "42 43 44 45", "D16": "46 47 48", "D17": "49 50 81",
    "D18": "51 52", "D19": "53 54 55 82", "D20": "56 57", "D21": "58 59",
    "D22": "60 61 62 63 64", "D23": "65 66 67 68", "D24": "69 70 71",
    "D25": "72 73", "D26": "77 78", "D27": "75 76", "D28": "79 80",
}
_SECTOR_TO_DISTRICT = {s: d for d, ss in _SECTORS.items() for s in ss.split()}


def district_from_address(text):
    m = re.search(r"\b[Ss]?(\d{6})\b", str(text or ""))
    return _SECTOR_TO_DISTRICT.get(m.group(1)[:2]) if m else None


def norm_phone(p):
    """Last 8 digits, matching guard_db_ids.py's norm_phone — the SG mobile length."""
    return "".join(ch for ch in str(p or "") if ch.isdigit())[-8:]


def parse_rent(rent_arg, rent_max_arg):
    if not rent_arg:
        return None, None
    s = str(rent_arg).replace("$", "").replace(",", "").strip()
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    elif s.isdigit():
        lo = hi = int(s)
    else:
        raise ValueError(f"could not parse --rent {rent_arg!r}; use a number or 'MIN-MAX'")
    if rent_max_arg is not None:
        hi = int(rent_max_arg)
    return lo, hi


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def next_landlord_id(ldb, watermark):
    top = int(watermark.get("landlord_max") or 0)
    for r in ldb.get("landlords", []):
        rid = str(r.get("id") or "")
        if rid.startswith("LL") and rid[2:].isdigit():
            top = max(top, int(rid[2:]))
    return top + 1, f"LL{top + 1:03d}"


def find_by_phone(ldb, phone):
    target = norm_phone(phone)
    if not target:
        return []
    return [r for r in ldb.get("landlords", []) if norm_phone(r.get("phone")) == target]


def build_record(args, new_id):
    rent_min, rent_max = parse_rent(args.rent, args.rent_max)

    rooms_and_rent = args.rooms_and_rent
    if not rooms_and_rent:
        parts = []
        if args.room_type:
            parts.append(args.room_type)
        if rent_min is not None:
            if rent_max and rent_max != rent_min:
                parts.append(f"${rent_min}-${rent_max}")
            else:
                parts.append(f"${rent_min}")
        rooms_and_rent = ", ".join(parts)

    district = args.district or district_from_address(args.address) or ""
    today = datetime.date.today().isoformat()
    origin = args.source.strip() if args.source else "unspecified non-WhatsApp origin"

    requirements = {}
    if args.notes:
        requirements["other"] = args.notes

    return {
        "id": new_id,
        "landlord_name": args.name,
        "phone": args.phone,
        "chat_jid": "",  # no WhatsApp thread — onboarded outside WhatsApp
        "full_address": args.address,
        "property_type": args.room_type or "",
        "deal_type": args.deal_type,
        "rooms_and_rent": rooms_and_rent,
        "requirements": requirements,
        "status": args.status,
        "follow_up": args.follow_up or "",
        "last_contact": today,
        "contact_label_source": f"manual quick-add ({origin})",
        "viewing_availability": "",
        "viewing_availability_updated": "",
        "last_refreshed": today,
        "rent_min": rent_min,
        "rent_max": rent_max,
        "district": district,
        "listing_key": "",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--phone", required=True)
    p.add_argument("--address", required=True)
    p.add_argument("--room-type", default="")
    p.add_argument("--rent")
    p.add_argument("--rent-max")
    p.add_argument("--rooms-and-rent")
    p.add_argument("--district")
    p.add_argument("--deal-type", default="rent", choices=["rent", "sale"])
    p.add_argument("--status", default="active")
    p.add_argument("--source", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--follow-up", default="")
    p.add_argument("--allow-duplicate-phone", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--db", default=DEFAULT_LDB)
    p.add_argument("--watermark", default=DEFAULT_WATERMARK)
    args = p.parse_args()

    if not args.name.strip():
        sys.exit("ERROR: --name cannot be blank")
    if not norm_phone(args.phone):
        sys.exit("ERROR: --phone has no digits")
    if not args.address.strip():
        sys.exit("ERROR: --address cannot be blank")

    try:
        parse_rent(args.rent, args.rent_max)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")

    ldb = load_json(args.db, {"landlords": []})
    if "landlords" not in ldb or not isinstance(ldb["landlords"], list):
        sys.exit(f"ERROR: {args.db} does not look like a landlord-db.json (no landlords[] array)")

    watermark = load_json(args.watermark, {})

    dupes = find_by_phone(ldb, args.phone)
    if dupes and not args.allow_duplicate_phone:
        print(f"REFUSING: phone already exists on {len(dupes)} record(s) — a shared phone CAN be legitimate")
        print("(e.g. a couple both texting from one line), so this will not silently merge or add.")
        for r in dupes:
            print(f"  {r.get('id')}: {r.get('landlord_name') or '(no name)'} — status: {r.get('status')} — {r.get('full_address') or '(no address)'}")
        print("\nRe-run with --allow-duplicate-phone if this really is a second, distinct landlord.")
        sys.exit(1)

    new_num, new_id = next_landlord_id(ldb, watermark)
    record = build_record(args, new_id)

    print(f"New record ({new_id}):")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if dupes:
        print(f"\nNOTE: proceeding despite {len(dupes)} existing record(s) on this phone (--allow-duplicate-phone).")

    if args.dry_run:
        print("\nDRY RUN — nothing written (DB or watermark untouched).")
        return

    ldb["landlords"].append(record)
    ldb["total"] = len(ldb["landlords"])
    if "last_updated" in ldb:
        ldb["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0800")
    if isinstance(ldb.get("metadata"), dict):
        ldb["metadata"]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0800")

    stamp = datetime.date.today().strftime("%Y%m%d")
    if os.path.exists(args.db):
        shutil.copy(args.db, args.db + f".bak-newlandlord-{stamp}")
    tmp = args.db + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ldb, f, indent=1, ensure_ascii=False)
    os.replace(tmp, args.db)

    watermark["landlord_max"] = new_num
    watermark.setdefault("landlord_known", {})[new_id] = norm_phone(args.phone)
    wm_tmp = args.watermark + ".tmp"
    os.makedirs(os.path.dirname(args.watermark), exist_ok=True)
    with open(wm_tmp, "w") as f:
        json.dump(watermark, f, indent=1)
    os.replace(wm_tmp, args.watermark)

    print(f"\nWROTE {new_id} to {args.db} (backup: {args.db}.bak-newlandlord-{stamp})")
    print(f"Watermark bumped: landlord_max={new_num} ({args.watermark})")


if __name__ == "__main__":
    main()
