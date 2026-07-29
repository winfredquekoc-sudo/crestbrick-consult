#!/usr/bin/env python3
"""Generate Winfred Brain vault notes from the live Crestbrick data files.
Rerunnable: regenerates Listings/ and Landlords/ from the DBs (notes carry a
GENERATED marker; hand edits belong in the ## Notes section which is preserved)."""
import json, os, re, datetime, sqlite3

V = "/Users/winfredquek/Desktop/Real Estate Related/Winfred Brain"
MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
WA_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")

def chat_tail(phone=None, chat_jid=None, n=6):
    """Last n meaningful messages for a contact, from the local bridge store."""
    try:
        mc = sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=10)
        jids = set()
        if chat_jid: jids.add(chat_jid)
        if phone:
            pn = re.sub(r"\D", "", phone)
            if pn:
                jids.add(pn + "@s.whatsapp.net")
                wc = sqlite3.connect("file:" + WA_DB + "?mode=ro", uri=True, timeout=10)
                for (lid,) in wc.execute("SELECT lid FROM whatsmeow_lid_map WHERE pn=?", (pn,)):
                    jids.add(lid + "@lid")
        rows = []
        for j in jids:
            rows += mc.execute(
                "SELECT timestamp, is_from_me, content FROM messages WHERE chat_jid=? "
                "AND content != '' ORDER BY rowid DESC LIMIT ?", (j, n)).fetchall()
        rows = sorted(rows, key=lambda r: r[0])[-n:]
        if not rows: return ""
        out = ["", "## Recent WhatsApp (generated)"]
        for ts, ifm, ct in rows:
            who = "Me" if ifm else "Them"
            out.append(f"- {str(ts)[:16]} {who}: {' '.join((ct or '').split())[:140]}")
        return "\n".join(out)
    except Exception:
        return ""
IDX = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")
LDB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
TODAY = datetime.date.today().isoformat()

idx = json.load(open(IDX))["listings"]
lls = {l["id"]: l for l in json.load(open(LDB))["landlords"]}

def keep_notes(path):
    try:
        old = open(path).read()
        m = re.search(r"## Notes\n(.*)", old, re.S)
        return m.group(1).strip() if m else ""
    except FileNotFoundError:
        return ""

def w(path, body, notes_default=""):
    kept = keep_notes(path) or notes_default
    open(path, "w").write(body.rstrip() + "\n\n## Notes\n" + kept + "\n")

active = [e for e in idx if not (str(e.get("status","")).lower().startswith("closed") or e.get("status")=="hold")]
names = {}
for e in idx:
    lk = e["listing_key"]
    nm = e.get("property_name") or e.get("address") or lk
    names[lk] = nm

for e in active:
    lk = e["listing_key"]; r = e.get("requirements") or {}
    ll = lls.get(e.get("landlord_id") or "")
    llname = (ll or {}).get("landlord_name") or "unknown"
    fv = e.get("fixed_viewing")
    fvline = f"Fixed viewing: every {fv['weekday']} {fv['time_label']}" if fv else "Viewing: ask tenant availability, confirm with landlord"
    gates = []
    if r.get("gender") not in (None,"any"): gates.append("gender " + str(r.get("gender")).replace("_"," "))
    er = r.get("ethnicity_rule") or {}
    if er.get("mode") not in (None,"any") and er.get("list"): gates.append(er["mode"] + " " + ", ".join(er["list"]))
    if r.get("max_pax"): gates.append(f"max {r['max_pax']} pax")
    if r.get("budget_floor"): gates.append(f"floor ${r['budget_floor']}")
    gates.append(f"min lease {max(12, r.get('lease_min_months') or 12)} months")
    body = f"""---
listing_key: {lk}
status: {e.get('status')}
district: {e.get('district','')}
updated: {TODAY}
tags: [listing]
---
GENERATED from listing index + landlord DB — rerun gen_vault.py to refresh. Hand notes go under Notes.

# {names[lk]}

Address: {e.get('address') or e.get('block_address') or ''}
Landlord: [[{llname}]] ({e.get('landlord_id')})
Portal: {e.get('portal_url','') or 'not on portal'}
{fvline}

Bot gates: {"; ".join(gates)}
Bot handles enquiries end to end (form, qualify, offer the slot). You get Telegram pings at every step."""
    if ll: body += chat_tail(ll.get("phone"), ll.get("chat_jid"))
    w(os.path.join(V, "Listings", names[lk].replace("/", " ") + ".md"), body)

seen = set()
for e in active:
    ll = lls.get(e.get("landlord_id") or "")
    if not ll or ll["id"] in seen: continue
    seen.add(ll["id"])
    r = ll.get("requirements") or {}
    lknames = ", ".join("[[" + names.get(k, k) + "]]" for k in (ll.get("listing_keys") or [ll.get("listing_key")]) if k)
    body = f"""---
landlord_id: {ll['id']}
phone: "{ll.get('phone','')}"
status: {ll.get('status')}
updated: {TODAY}
tags: [landlord]
---
GENERATED from landlord DB — rerun gen_vault.py to refresh. Hand notes go under Notes.

# {ll.get('landlord_name')}

Phone: {ll.get('phone') or 'unknown, use WhatsApp chat'}
Property: {ll.get('full_address','')} ({ll.get('property_type','')})
Listings: {lknames}
Rooms and rent: {ll.get('rooms_and_rent','')}
Commission: {r.get('commission','')}
Last contact: {ll.get('last_contact','')}
Follow up: {ll.get('follow_up','')}"""
    body += chat_tail(ll.get("phone"), ll.get("chat_jid"))
    w(os.path.join(V, "Landlords", str(ll.get("landlord_name","unknown")).replace("/", " ") + ".md"), body)

print("generated", len(active), "listing notes,", len(seen), "landlord notes")

# People notes: any hand written note with a phone in frontmatter gets a live chat tail
import glob
np = 0
for path in glob.glob(os.path.join(V, "People", "*.md")):
    s = open(path).read()
    m = re.search(r'^phone:\s*"?(\+?[\d ]+)"?', s, re.M)
    if not m: continue
    tail = chat_tail(m.group(1))
    if not tail: continue
    s = re.sub(r"\n## Recent WhatsApp \(generated\)\n(?:- [^\n]*\n?)*", "\n", s)
    i = s.find("## Notes")
    s = (s[:i].rstrip() + "\n" + tail.lstrip("\n") + "\n\n" + s[i:]) if i >= 0 else s.rstrip() + "\n" + tail + "\n"
    open(path, "w").write(s)
    np += 1
print("people notes with chat tails:", np)
