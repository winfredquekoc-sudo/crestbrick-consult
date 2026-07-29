#!/opt/homebrew/bin/python3
"""
build_landlord_db.py
Extracts landlord property details from WhatsApp conversations and builds
~/.claude/state/wa-agent/landlord-db.json

Run anytime after new landlord conversations:
  python3 /Users/winfredquek/crestbrick-consult/scripts/build_landlord_db.py

The agent loads this file on startup — no restart needed.
"""

import os
import json
import sqlite3
import time
import re
import sys

DB_PATH     = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
OUTPUT_PATH = os.path.expanduser("~/.claude/state/wa-agent/landlord-db.json")

# ─── Landlord roster ──────────────────────────────────────────────────────────
# Add new landlords here when onboarded.
# listing_id must match an id in property-templates.json, or "unknown" if not yet listed.

LANDLORDS = load_landlords()  # roster lives outside the repo; see scripts/_wa_seed.py

EXTRACT_PROMPT = """Extract rental property info from this WhatsApp conversation between a property agent (Winfred) and a landlord.
Return ONLY a valid JSON object. No markdown, no explanation. Use double-quoted keys and string values only.

Fields (use empty string "" if unknown):
{
  "property_address": "",
  "room_type": "",
  "asking_rent": "",
  "rent_negotiable": "",
  "available_from": "",
  "min_lease": "",
  "preferred_gender": "",
  "preferred_nationality": "",
  "preferred_occupation": "",
  "max_pax": "",
  "cooking": "",
  "pets": "",
  "smoking": "",
  "visitors": "",
  "subletting": "",
  "utilities_included": "",
  "aircon": "",
  "repairs": "",
  "house_rules": "",
  "viewing_schedule": "",
  "owner_stays": "",
  "furnishing": "",
  "notes": ""
}"""


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
                        return line.strip().split("=", 1)[1]
    return ""


def groq_extract(client, name, convo):
    from groq import Groq
    resp = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=500,
        messages=[
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user",   "content": convo[:3500]}
        ]
    )
    raw = resp.choices[0].message.content.strip()
    # Strip markdown fences if present
    if "```" in raw:
        raw = raw.split("```")[1].replace("json", "").strip()
    # Extract JSON object
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def build_db(force=False):
    key = get_api_key("GROQ_API_KEY")
    if not key:
        print("❌ No GROQ_API_KEY found")
        sys.exit(1)

    from groq import Groq
    client = Groq(api_key=key)

    # Load existing DB
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH) as f:
            existing = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    results = {}
    updated = 0
    skipped = 0

    for jid, info in LANDLORDS.items():
        name       = info["name"]
        listing_id = info["listing_id"]

        # Skip if already in DB and not forcing refresh
        if not force and jid in existing:
            results[jid] = existing[jid]
            skipped += 1
            print(f"⏭  {name:<12} — already in DB, skipping (use --force to refresh)")
            continue

        rows = conn.execute(
            "SELECT is_from_me, content FROM messages "
            "WHERE chat_jid=? AND content!='' ORDER BY timestamp ASC LIMIT 60",
            (jid,)
        ).fetchall()

        if len(rows) < 3:
            print(f"⚠️  {name:<12} — fewer than 3 messages, skipping")
            continue

        convo = "\n".join([
            f"{'Winfred' if r[0] else name}: {r[1][:200]}"
            for r in rows
        ])

        retries = 2
        for attempt in range(retries):
            try:
                data = groq_extract(client, name, convo)
                results[jid] = {
                    "jid":        jid,
                    "name":       name,
                    "listing_id": listing_id,
                    **data
                }
                addr = data.get("property_address", "") or "?"
                rent = data.get("asking_rent", "") or "?"
                gend = data.get("preferred_gender", "") or "any"
                view = data.get("viewing_schedule", "") or "?"
                print(f"✅ {name:<12} | {addr[:35]:<35} | rent: {rent:<20} | gender: {gend[:20]}")
                print(f"   viewing: {view[:60]}")
                updated += 1
                break
            except json.JSONDecodeError as e:
                print(f"   ⚠️  JSON parse error for {name} (attempt {attempt+1}): {e}")
                if attempt == retries - 1:
                    # Fall back to existing if available
                    if jid in existing:
                        results[jid] = existing[jid]
                        print(f"   → kept existing profile")
                    else:
                        print(f"   → no fallback, skipped")
            except Exception as e:
                if "429" in str(e):
                    wait = 20
                    print(f"   ⏳ Rate limit hit — waiting {wait}s...")
                    time.sleep(wait)
                    if attempt == retries - 1 and jid in existing:
                        results[jid] = existing[jid]
                else:
                    print(f"   ❌ Error for {name}: {str(e)[:80]}")
                    if jid in existing:
                        results[jid] = existing[jid]
                    break

        time.sleep(0.4)  # gentle pacing

    conn.close()

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Done. {updated} updated, {skipped} skipped, {len(results)} total saved.")
    print(f"Output: {OUTPUT_PATH}")
    print(f"{'='*60}")


def show_db():
    if not os.path.exists(OUTPUT_PATH):
        print("No database found. Run without --show first.")
        return

    with open(OUTPUT_PATH) as f:
        db = json.load(f)

    print(f"\n{'='*70}")
    print(f"LANDLORD DATABASE — {len(db)} entries")
    print(f"{'='*70}\n")

    for jid, l in db.items():
        print(f"👤 {l['name']} → listing: {l['listing_id']}")
        fields = [
            ("Address",    "property_address"),
            ("Room",       "room_type"),
            ("Rent",       "asking_rent"),
            ("Negotiable", "rent_negotiable"),
            ("Available",  "available_from"),
            ("Min lease",  "min_lease"),
            ("Gender",     "preferred_gender"),
            ("Nationality","preferred_nationality"),
            ("Occupation", "preferred_occupation"),
            ("Max pax",    "max_pax"),
            ("Cooking",    "cooking"),
            ("Pets",       "pets"),
            ("Smoking",    "smoking"),
            ("Visitors",   "visitors"),
            ("Subletting", "subletting"),
            ("Utilities",  "utilities_included"),
            ("Aircon",     "aircon"),
            ("Viewing",    "viewing_schedule"),
            ("Owner stays","owner_stays"),
            ("Rules",      "house_rules"),
            ("Notes",      "notes"),
        ]
        for label, key in fields:
            val = l.get(key, "")
            if val:
                print(f"   {label:<12}: {val[:70]}")
        print()


if __name__ == "__main__":
    force = "--force" in sys.argv
    show  = "--show"  in sys.argv

    if show:
        show_db()
    else:
        if force:
            print("Force refresh — re-extracting all landlord profiles...\n")
        else:
            print("Building landlord DB (skipping existing entries)...\n")
            print("Tip: use --force to re-extract all from scratch\n")
        build_db(force=force)
