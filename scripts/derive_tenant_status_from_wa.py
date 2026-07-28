#!/usr/bin/env python3
"""
derive_tenant_status_from_wa.py — corrects each prospective tenant's `status` from the
WhatsApp message DB, the authoritative record of what actually happened.

Why this exists
---------------
The pipeline state files (intake-state.json, pg-sop-sent.json, wa-pipeline/state.json)
only know about forms the AUTOMATION sent. They do NOT capture the profile/intake form
Winfred sends MANUALLY from his phone, nor a profile a tenant typed back, nor a tenant
who self rejected ("over budget", "found a place"). So tenants sit at `open`/`cold` in
tenant-db.json long after they were actually handled, and the match board / re-engagement
map keep surfacing them as if Winfred still has to action them.

This reads each tenant's real chat in messages.db and re-derives status:
  1. Winfred (or the bot) already sent the profile form  -> at least `form-sent`
  2. The tenant typed a filled profile back              -> `profile-received`
  3. The tenant's LAST inbound message is a rejection     -> `rejected`
     (sub typed into contact_state so the re-engagement map treats an "over budget"
      rejection as still re-engageable, but "found a place" / "not interested" as off.)

Form detection reuses the same two tier logic as pg-enquiry-sop.sh's
winfred_already_replied(): conversation-state.json is primary, a message scan is the
fallback (the WA bridge stores some bot sends as is_from_me=0, so direction alone is not
enough). ALREADY_SENT_RE is the same shape as the SOP's.

Safety rules (this is a SILENT data fix, same contract as refresh-rental-dbs)
  - Never DOWNGRADE a status (a `viewed` tenant is never pulled back to `form-sent`).
  - Never touch a deal stage or terminal record: viewing-set, viewed, deposit-pending,
    tenanted, found_place, excluded, or an already `rejected` tenant are left untouched.
  - Never blank a field. Only ADD/UPGRADE status, and set contact_state on a rejection.
  - Idempotent: re-running changes nothing once statuses are correct.

Usage:
  python3 derive_tenant_status_from_wa.py            # dry run, prints what WOULD change
  python3 derive_tenant_status_from_wa.py --apply     # writes tenant-db.json atomically
  python3 derive_tenant_status_from_wa.py --apply --date 2026-06-17   # contact_state_updated date
"""
import json, os, re, sys, sqlite3, datetime, collections

ROOT = os.path.expanduser("~/crestbrick-consult")
TDB  = os.path.join(ROOT, "_templates/tenant-db.json")
MSG  = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
WA_STORE = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")
CONV = os.path.expanduser("~/.claude/state/listing-templates/conversation-state.json")

APPLY = "--apply" in sys.argv

def arg_date():
    if "--date" in sys.argv:
        i = sys.argv.index("--date")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    # real Asia/Singapore date from the system clock (never invented)
    sgt = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(sgt).strftime("%Y-%m-%d")

TODAY_SGT = arg_date()

# ── Detection patterns ─────────────────────────────────────────────────────────

# The blank profile form Winfred / the bot sends (same shape as pg-enquiry-sop's
# ALREADY_SENT_RE). Matches the template, not a casual enquiry.
ALREADY_SENT_RE = re.compile(
    r'(fill\s+this\s+in|send\s+your\s+profile\s+to\s+the\s+landlord'
    r'|help\s+me\s+match\s+you|to\s+help\s+me\s+match\s+you'
    r'|profile\s+information|type\s+of\s+pass|no\.?\s*of\s*pax'
    r'|nationality\s*:|ethnicity\s*:)',
    re.I,
)

# Form field labels — used to recognise a tenant typing a profile back.
FIELD_LABEL_RE = re.compile(
    r'(name|nationality|ethnicity|gender|age|pass|pax|occupation|budget'
    r'|move.?in|lease|email)\s*[:：]',
    re.I,
)
# At least one core field carries a real value after the colon (so a blank form
# forwarded back is not mistaken for a completed one).
FILLED_VALUE_RE = re.compile(
    r'(name|nationality|ethnicity|gender|age|pass|occupation|email)\s*[:：]\s*\S',
    re.I,
)

def is_filled_profile(content):
    """True when an inbound message is a completed profile (>=3 distinct field
    labels AND at least one filled value)."""
    if not content:
        return False
    labels = {m.group(1).lower() for m in FIELD_LABEL_RE.finditer(content)}
    if len(labels) < 3:
        return False
    return bool(FILLED_VALUE_RE.search(content))

# Rejection signals, only ever tested against the tenant's LAST inbound message.
# Grouped so an "over budget"/"too expensive" rejection stays re-engageable (find them
# something cheaper) while "found a place"/"not interested" takes them off the list.
REJECT_BUDGET_RE = re.compile(
    r'(over\s+(?:my\s+|the\s+)?budget|out\s+of\s+(?:my\s+)?budget|too\s+expensive'
    r'|too\s+(?:high|pricey|costly)|exceed\s+(?:my\s+)?budget|beyond\s+(?:my\s+)?budget'
    r'|not\s+suitable|doesn.?t\s+suit|does\s+not\s+suit)',
    re.I,
)
REJECT_FOUND_RE = re.compile(
    r'(found\s+(?:a\s+|another\s+|the\s+)?(?:place|room|unit|apartment|flat|somewhere|house)'
    r'|already\s+(?:found|rented|booked|secured|taken|got)\b'
    r'|secured\s+(?:a\s+)?(?:place|room|unit)|got\s+(?:a\s+)?(?:place|room|unit)\b'
    r'|signed\s+(?:the\s+|a\s+)?(?:lease|ta|tenancy))',
    re.I,
)
REJECT_OFF_RE = re.compile(
    r'(not\s+interested|no\s+longer\s+(?:interested|looking|need)'
    r'|no\s+need\s+(?:already|anymore|now)|don.?t\s+need\s+(?:already|anymore|it\s+anymore)'
    r'|please\s+stop|stop\s+(?:messaging|sending|texting)|unsubscribe'
    r'|remove\s+me|not\s+anymore|changed\s+my\s+mind|decided\s+not\s+to)',
    re.I,
)
# Guard: if the same last inbound clearly continues the search, do NOT reject.
STILL_LOOKING_RE = re.compile(
    r'(any\s+other|anything\s+else|cheaper|lower\s+budget|still\s+looking'
    r'|other\s+option|other\s+listing|something\s+(?:else|cheaper|smaller))',
    re.I,
)

def reject_kind(last_inbound):
    """Returns ('budget'|'found'|'off', None) for a rejecting last inbound, else None."""
    if not last_inbound:
        return None
    if STILL_LOOKING_RE.search(last_inbound):
        return None  # they are still asking for alternatives — not a rejection
    if REJECT_OFF_RE.search(last_inbound):
        return "off"
    if REJECT_FOUND_RE.search(last_inbound):
        return "found"
    if REJECT_BUDGET_RE.search(last_inbound):
        return "budget"
    return None

# ── Status model ────────────────────────────────────────────────────────────────

RANK = {
    "open": 0, "cold": 0, "needs_info": 0, "needs_info_unknowns": 0,
    "form-sent": 1, "profile-received": 2,
}
# Never modify these — deal stage (human managed) or terminal.
SKIP = {"viewing-set", "viewed", "deposit-pending", "tenanted",
        "found_place", "excluded", "rejected"}

# ── WhatsApp resolution helpers ──────────────────────────────────────────────────

def load_conv_jids():
    try:
        d = json.loads(open(CONV).read())
        return set(d.get("conversations", {}).keys())
    except Exception:
        return set()

def load_lid_map():
    lid_to_pn, pn_to_lid = {}, {}
    try:
        wc = sqlite3.connect(WA_STORE, timeout=30)
        wc.execute("PRAGMA busy_timeout=30000")
        for lid, pn in wc.execute("SELECT lid, pn FROM whatsmeow_lid_map"):
            p = (pn or "").lstrip("+")
            lid_to_pn[lid] = p
            pn_to_lid[p] = lid
        wc.close()
    except Exception:
        pass
    return lid_to_pn, pn_to_lid

def candidate_jids(tenant, pn_to_lid):
    """All chat_jids worth querying for this tenant: stored jid first, then phone derived."""
    out = []
    jid = (tenant.get("jid") or "").strip()
    if jid:
        out.append(jid)
    phone = (tenant.get("phone") or "").lstrip("+").replace(" ", "").replace("-", "")
    if phone:
        for cand in (f"{phone}@s.whatsapp.net", f"{phone}@lid"):
            if cand not in out:
                out.append(cand)
        lid = pn_to_lid.get(phone)
        if lid:
            for cand in (f"{lid}@lid", lid):
                if cand not in out:
                    out.append(cand)
    return out

def fetch_rows(con, jids):
    rows = []
    seen_ids = set()
    for jid in jids:
        try:
            cur = con.execute(
                "SELECT id, is_from_me, content FROM messages "
                "WHERE chat_jid = ? AND content IS NOT NULL AND content != '' "
                "ORDER BY timestamp",
                (jid,),
            )
        except Exception:
            continue
        for r in cur.fetchall():
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            rows.append((int(r["is_from_me"] or 0), r["content"]))
    return rows

# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze(rows, jid_in_conv):
    """From a tenant's chat rows (ASC), derive WA evidence."""
    had_form = jid_in_conv          # conversation-state.json is the primary form-sent signal
    had_profile = False
    last_inbound = None
    for is_me, content in rows:
        if not content:
            continue
        filled = is_filled_profile(content)
        if is_me == 0:
            last_inbound = content
            if filled:
                had_profile = True
        elif ALREADY_SENT_RE.search(content) and not filled:
            # outbound blank form (manual phone send OR bot send) -> form was sent
            had_form = True
    return had_form, had_profile, last_inbound

def main():
    t = json.load(open(TDB))
    con = sqlite3.connect(MSG, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row

    conv_jids = load_conv_jids()
    _lid_to_pn, pn_to_lid = load_lid_map()

    changes = []
    counts = collections.Counter()
    for x in t["tenants"]:
        # excluded non tenants (co-broke agents, landlords, colleagues) are out of the
        # tenant pipeline entirely — never promote them to an actionable status.
        if x.get("excluded"):
            continue
        cur = x.get("status")
        if cur in SKIP:
            continue
        jids = candidate_jids(x, pn_to_lid)
        if not jids:
            continue
        jid_in_conv = any(j in conv_jids for j in jids)
        rows = fetch_rows(con, jids)
        if not rows and not jid_in_conv:
            continue
        had_form, had_profile, last_inbound = analyze(rows, jid_in_conv)

        new_status = None
        new_cs = None
        rk = reject_kind(last_inbound)
        if rk:
            new_status = "rejected"
            new_cs = {"off": "not_interested", "found": "found_place",
                      "budget": "active"}[rk]
        elif had_profile and RANK.get(cur, 0) < RANK["profile-received"]:
            new_status = "profile-received"
        elif had_form and RANK.get(cur, 0) < RANK["form-sent"]:
            new_status = "form-sent"

        if not new_status:
            continue

        # never downgrade
        if new_status != "rejected" and RANK.get(new_status, 0) <= RANK.get(cur, 0):
            continue

        rec = {"id": x.get("id"), "name": x.get("name"), "from": cur, "to": new_status}
        x["status"] = new_status
        counts[f"{cur} -> {new_status}"] += 1
        if new_cs and x.get("contact_state") != new_cs:
            # only tighten contact_state on a hard rejection; an "over budget" rejection
            # keeps contact_state active so the re-engagement map can still serve them.
            if new_cs != "active":
                rec["contact_state"] = f"{x.get('contact_state')} -> {new_cs}"
                x["contact_state"] = new_cs
                x["contact_state_updated"] = TODAY_SGT
        changes.append(rec)

    con.close()

    print(f"tenants scanned: {len(t['tenants'])} | status corrections: {len(changes)}")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    if changes:
        print("\nsample (first 20):")
        for c in changes[:20]:
            cs = f"  contact_state {c['contact_state']}" if "contact_state" in c else ""
            print(f"  {c['id']} {c.get('name')}: {c['from']} -> {c['to']}{cs}")

    if not APPLY:
        print("\nDRY RUN. Re-run with --apply to write tenant-db.json.")
        return

    # recompute top level open/closed counts to match new statuses
    OPEN = {"open", "cold", "needs_info", "needs_info_unknowns",
            "form-sent", "profile-received", "viewing-set", "viewed"}
    t["open"] = sum(1 for x in t["tenants"] if x.get("status") in OPEN)
    t["closed"] = sum(1 for x in t["tenants"]
                      if x.get("status") in ("rejected", "tenanted", "deposit-pending"))

    tmp = TDB + ".tmp"
    json.dump(t, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, TDB)
    print(f"\nWROTE: {TDB} ({len(changes)} status corrections)")

if __name__ == "__main__":
    main()
