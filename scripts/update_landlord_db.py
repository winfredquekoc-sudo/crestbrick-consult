#!/opt/homebrew/bin/python3
"""
update_landlord_db.py
Incrementally updates ~/.claude/state/wa-agent/landlord-db.json by scraping
new messages from Winfred's WhatsApp conversations with each landlord.

Unlike build_landlord_db.py (full rebuild from scratch), this script:
  - Only reads messages AFTER each landlord's last_updated timestamp
  - Extracts key CHANGES: price, availability, slots, rules, room status
  - Merges changes into the existing DB entry (never overwrites clean fields with blanks)
  - Marks listings as taken/available based on what landlords say

Run via launchd every 2 hours:
  com.crestbrick.update-landlord-db

Or manually:
  python3 /Users/winfredquek/crestbrick-consult/scripts/update_landlord_db.py
  python3 /Users/winfredquek/crestbrick-consult/scripts/update_landlord_db.py --show
  python3 /Users/winfredquek/crestbrick-consult/scripts/update_landlord_db.py --force  # re-read all history
"""

import os
import json
import sqlite3
import time
import re
import sys
from datetime import datetime, timezone

DB_PATH     = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
OUTPUT_PATH = os.path.expanduser("~/.claude/state/wa-agent/landlord-db.json")
LOG_PATH    = os.path.expanduser("~/.claude/state/whatsapp-autoreply/landlord-db-update.log")

# ─── Landlord roster ──────────────────────────────────────────────────────────
# Names and WhatsApp identifiers are personal data, so the roster lives outside
# the repo. Add new landlords to ~/.claude/state/wa-agent/landlord-seed.json;
# scripts/landlord-seed.example.json documents the schema.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _wa_seed import load_landlords

LANDLORDS = load_landlords()

# ─── Signals the LLM should detect ───────────────────────────────────────────
CHANGE_EXTRACT_PROMPT = """You are reading a WhatsApp conversation between a property agent (Winfred) and a landlord named {name}.
This is ONLY the NEW messages since the last database update — not the full history.

Extract any KEY CHANGES or NEW INFORMATION about the rental property. Return ONLY a valid JSON object.
Use empty string "" for any field with no new information. Never invent data.

Fields to extract:
{{
  "status": "",              // "available" | "taken" | "pending" | "" — set "taken" if landlord says room is rented/found tenant/taken
  "asking_rent": "",         // New rent amount if changed or mentioned (e.g. "$1,200/mo")
  "rent_negotiable": "",     // "yes" | "no" | "" if mentioned
  "available_from": "",      // New availability date if changed (e.g. "1 July", "immediate")
  "min_lease": "",           // Lease term requirement if mentioned
  "preferred_gender": "",    // Gender preference if updated
  "preferred_nationality": "",// Nationality preference if updated
  "preferred_occupation": "",// Occupation preference if updated (e.g. "no students")
  "max_pax": "",             // Max occupants if updated
  "cooking": "",             // Cooking rule if updated
  "pets": "",                // Pet rule if updated
  "smoking": "",             // Smoking rule if updated
  "visitors": "",            // Visitor rule if updated
  "utilities_included": "",  // Utilities/wifi info if updated
  "aircon": "",              // Aircon info if updated
  "viewing_schedule": "",    // NEW viewing availability (days/times landlord can show)
  "blackout_dates": "",      // Dates landlord is NOT available (e.g. "away next week")
  "house_rules": "",         // New or updated house rules
  "notes": "",               // Any other important landlord context
  "change_summary": ""       // ONE sentence summarising what changed (or "no changes" if nothing new)
}}

Context clues for "taken" — ONLY set "taken" if landlord EXPLICITLY says the room/unit is no longer available:
- "already rented", "found a tenant", "no longer available", "unit taken", "settled already",
  "deposit paid", "signed agreement", "removed listing", "taken liao", "got tenant already"
- DO NOT mark taken just because a viewing was confirmed or a tenant expressed interest.
- DO NOT mark taken based on Winfred's messages — only based on the landlord's own words.
- When in doubt, leave status as "".

Context clues for viewing schedule:
- "can view on...", "I'\''m free...", "viewing on...", "available to show..."
- "away next week", "traveling", "not available on..." → use for blackout_dates

New messages:
{messages}"""


def get_api_key(key_name):
    val = os.environ.get(key_name, "")
    if val:
        return val
    for env_file in [
        os.path.expanduser("~/.claude/state/wa-agent/.env"),
        os.path.expanduser("~/.claude/.env"),
    ]:
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith(f"{key_name}="):
                        return line.strip().split("=", 1)[1].strip('"').strip("'")
    return ""


def groq_extract(groq_client, name, messages_text):
    prompt = CHANGE_EXTRACT_PROMPT.format(name=name, messages=messages_text[:4000])
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=600,
        temperature=0.1,
        messages=[
            {"role": "system", "content": "Return only valid JSON. No markdown, no explanation."},
            {"role": "user",   "content": prompt}
        ]
    )
    raw = resp.choices[0].message.content.strip()
    if "```" in raw:
        raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def merge_entry(existing: dict, updates: dict) -> dict:
    """Merge update fields into existing entry. Never overwrite with empty string."""
    merged = dict(existing)
    for key, val in updates.items():
        if key == "change_summary":
            continue
        if val and val.strip():
            # status is special — always update even if it goes back to "available"
            if key == "status" or (val != existing.get(key, "")):
                merged[key] = val
    return merged


def timestamp_to_unix(ts_str: str) -> int:
    """Convert ISO timestamp string to unix seconds. Returns 0 on failure."""
    if not ts_str:
        return 0
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def run_update(force: bool = False):
    key = get_api_key("GROQ_API_KEY")
    if not key:
        print("❌ No GROQ_API_KEY found. Set it in ~/.claude/state/wa-agent/.env")
        sys.exit(1)

    from groq import Groq
    groq_client = Groq(api_key=key)

    # Load existing DB
    existing_db = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH) as f:
            existing_db = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    results = dict(existing_db)
    updated_count = 0
    skipped_count = 0
    taken_count   = 0

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    log_lines = [f"\n=== Landlord DB Update — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="]

    for jid, info in LANDLORDS.items():
        name       = info["name"]
        listing_id = info["listing_id"]

        # Determine cutoff — when did we last update this entry?
        existing_entry = existing_db.get(jid, {})
        last_updated   = existing_entry.get("last_updated", "")
        cutoff_unix    = 0 if (force or not last_updated) else timestamp_to_unix(last_updated)

        # Fetch new messages since last update
        rows = conn.execute(
            """SELECT is_from_me, content, timestamp
               FROM messages
               WHERE chat_jid=? AND content!='' AND timestamp > ?
               ORDER BY timestamp ASC LIMIT 80""",
            (jid, cutoff_unix)
        ).fetchall()

        if not rows:
            skipped_count += 1
            print(f"⏭  {name:<20} — no new messages since last update")
            continue

        # Format messages for the LLM
        messages_text = "\n".join([
            f"{'Winfred' if r[0] else name}: {r[1][:300]}"
            for r in rows
        ])

        print(f"🔍 {name:<20} — {len(rows)} new messages — extracting changes...")

        retries = 2
        for attempt in range(retries):
            try:
                updates = groq_extract(groq_client, name, messages_text)
                change_summary = updates.get("change_summary", "no changes")

                # Prepare base entry if not yet in DB
                if jid not in results:
                    results[jid] = {
                        "jid":        jid,
                        "name":       name,
                        "listing_id": listing_id,
                        "status":     "available",
                    }

                # Merge updates
                merged = merge_entry(results[jid], updates)
                merged["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")

                # Ensure base fields present
                merged.setdefault("name",       name)
                merged.setdefault("listing_id", listing_id)
                merged.setdefault("status",     "available")

                results[jid] = merged

                status_flag = f"  🔴 TAKEN" if merged.get("status") == "taken" else ""
                print(f"   ✅ {change_summary[:80]}{status_flag}")
                log_lines.append(f"{name}: {change_summary}{status_flag}")

                if merged.get("status") == "taken":
                    taken_count += 1

                updated_count += 1
                break

            except json.JSONDecodeError as e:
                print(f"   ⚠️  JSON parse error for {name} (attempt {attempt+1}): {e}")
                if attempt == retries - 1 and jid in existing_db:
                    results[jid] = existing_db[jid]
            except Exception as e:
                if "429" in str(e):
                    wait = 20
                    print(f"   ⏳ Rate limit — waiting {wait}s...")
                    time.sleep(wait)
                    if attempt == retries - 1 and jid in existing_db:
                        results[jid] = existing_db[jid]
                else:
                    print(f"   ❌ Error for {name}: {str(e)[:80]}")
                    if jid in existing_db:
                        results[jid] = existing_db[jid]
                    break

        time.sleep(0.4)

    conn.close()

    # Save updated DB
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    summary = f"Done. {updated_count} updated, {skipped_count} skipped, {taken_count} marked taken."
    print(f"\n{'='*60}")
    print(summary)
    print(f"Output: {OUTPUT_PATH}")
    print(f"{'='*60}")

    log_lines.append(summary)
    with open(LOG_PATH, "a") as f:
        f.write("\n".join(log_lines) + "\n")


def show_db():
    if not os.path.exists(OUTPUT_PATH):
        print("No database found. Run without --show first.")
        return

    with open(OUTPUT_PATH) as f:
        db = json.load(f)

    available = [v for v in db.values() if v.get("status") != "taken"]
    taken     = [v for v in db.values() if v.get("status") == "taken"]

    print(f"\n{'='*70}")
    print(f"LANDLORD DATABASE — {len(db)} entries ({len(available)} available, {len(taken)} taken)")
    print(f"{'='*70}\n")

    for status_group, label in [(available, "AVAILABLE"), (taken, "TAKEN / RENTED")]:
        if not status_group:
            continue
        print(f"\n── {label} ──────────────────────────────────────────────")
        for l in status_group:
            print(f"\n👤 {l.get('name','?')} → {l.get('listing_id','?')} [{l.get('status','?')}]")
            fields = [
                ("Address",      "property_address"),
                ("Room",         "room_type"),
                ("Rent",         "asking_rent"),
                ("Negotiable",   "rent_negotiable"),
                ("Available",    "available_from"),
                ("Min lease",    "min_lease"),
                ("Gender",       "preferred_gender"),
                ("Nationality",  "preferred_nationality"),
                ("Occupation",   "preferred_occupation"),
                ("Max pax",      "max_pax"),
                ("Cooking",      "cooking"),
                ("Pets",         "pets"),
                ("Smoking",      "smoking"),
                ("Visitors",     "visitors"),
                ("Utilities",    "utilities_included"),
                ("Viewing",      "viewing_schedule"),
                ("Blackout",     "blackout_dates"),
                ("Owner stays",  "owner_stays"),
                ("Rules",        "house_rules"),
                ("Last updated", "last_updated"),
                ("Notes",        "notes"),
            ]
            for label_f, key in fields:
                val = l.get(key, "")
                if val:
                    print(f"   {label_f:<12}: {str(val)[:70]}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    show  = "--show"  in sys.argv

    if show:
        show_db()
    elif force:
        print("Force refresh — re-reading full chat history for all landlords...\n")
        run_update(force=True)
    else:
        print("Incremental update — processing only new messages since last update...\n")
        run_update(force=False)
