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
    r'|already\s+(?:found|rented|booked|secured|taken|got|settled|signed)\b'
    r'|secured\s+(?:a\s+)?(?:place|room|unit)'
    r'|got\s+(?:a\s+|the\s+|my\s+|another\s+)?(?:place|room|unit)\b'
    r'|settled\s+(?:already|le|liao|down)|moved\s+in\s+already'
    r'|renting\s+(?:another|elsewhere|somewhere\s+else)'
    r'|signed\s+(?:the\s+|a\s+)?(?:lease|ta|tenancy)'
    # Chinese: 已经找到/租到/租好/定好 (already found/rented/settled), 找到(了)房/房间/地方
    r'|已经\s*(?:找到|租到|租好|定好|订好)|(?:找到|租到|租好)\s*(?:了|房|房间|房子|地方|别的))',
    re.I,
)
REJECT_OFF_RE = re.compile(
    r'(not\s+interested|no\s+longer\s+(?:interested|looking|need|require)'
    r'|no\s+(?:more\s+)?need\s+(?:already|anymore|now|le|liao|alr)'
    r'|no\s+more\s+need(?:ed)?|don.?t\s+need\s+(?:already|anymore|it\s+anymore)'
    r'|please\s+stop|stop\s+(?:messaging|sending|texting)|unsubscribe'
    r'|remove\s+me|not\s+anymore|changed\s+my\s+mind|decided\s+not\s+to'
    # Chinese: 不需要了/不用了/不找了/不租了 (no longer need / stopped looking)
    r'|不\s*(?:需要|用|找|租)\s*了)',
    re.I,
)
# Guard: if the same inbound clearly continues the search (asks for alternatives
# or viewing), do NOT reject. Lookbehinds keep "no longer looking for" and
# "not looking for" out of the still-looking guard they would otherwise trip.
STILL_LOOKING_RE = re.compile(
    r'(any\s+other|anything\s+else|cheaper|lower\s+budget|still\s+looking'
    r'|(?<!longer\s)(?<!not\s)looking\s+for|other\s+option|other\s+listing'
    r'|something\s+(?:else|cheaper|smaller)'
    r'|can\s+(?:i|we)\s+view|when\s+can|like\s+to\s+view|interested\s+in\s+view'
    r'|可以看|想看|约看|还在找)',
    re.I,
)
# Pleasantry noise the decision scan may step over: "ok thanks 🙏" after
# "found a room already" must not hide the decision two messages up.
NOISE_RE = re.compile(
    r'^\W*(?:ok(?:ay)*|okie+|noted|thanks?(?:\s+(?:you|a\s+lot|so\s+much))?|thank\s+you'
    r'|thx|tq|sure|alright|alr|got\s+it|welcome|no\s+problem|np|cool|nice|great'
    r'|good\s+(?:morning|afternoon|evening|night|day)|bye|see\s+you|cheers|sorry'
    r'|好的?|谢谢您?|收到|嗯+|行)\W*$',
    re.I,
)

def is_noise(msg):
    m = (msg or "").strip()
    if len(m) <= 3:
        return True
    return bool(NOISE_RE.match(m))

def classify_one(msg):
    """('budget'|'found'|'off') for one rejecting message, 'active' for one that
    clearly continues the search, else None (neutral)."""
    if not msg:
        return None
    if STILL_LOOKING_RE.search(msg):
        return "active"
    if REJECT_OFF_RE.search(msg):
        return "off"
    if REJECT_FOUND_RE.search(msg):
        return "found"
    if REJECT_BUDGET_RE.search(msg):
        return "budget"
    return None

def reject_kind(inbounds, depth=10):
    """Walk the tenant's inbound messages newest-first and return the most recent
    DECISION: ('budget'|'found'|'off') for a rejection, else None.

    Testing only the literal last inbound missed most real rejections — chats end
    with "ok thanks 🙏", not with the decision (found 21 Aug 2026: 78 of 251
    rostered tenants sat at rejected/found in the DB or their chats). Pleasantry
    noise is stepped over; the first substantive message settles it either way —
    a neutral substantive message means their latest word is NOT a rejection, so
    an older "found a place" must not reject them (they may have resumed looking;
    a resumed search re-enters through Part B, not through this reconciler).
    """
    for msg in list(reversed(inbounds or []))[:depth]:
        kind = classify_one(msg)
        if kind == "active":
            return None
        if kind:
            return kind
        if not is_noise(msg):
            return None
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
                "SELECT id, is_from_me, content, timestamp FROM messages "
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
            rows.append((r["timestamp"] or "", int(r["is_from_me"] or 0), r["content"]))
    # a tenant whose chat spans multiple jids (@s.whatsapp.net + @lid) must still
    # read in true time order — "latest word wins" is wrong otherwise
    rows.sort(key=lambda r: r[0])
    return [(m, c) for _ts, m, c in rows]

# ── Analysis ──────────────────────────────────────────────────────────────────

# Engine sends the bridge sometimes stores as is_from_me=0 (same quirk the form
# scan already handles). Without this guard "the room is already taken" or the
# blank form itself would count as the TENANT speaking and poison the rejection
# scan ("already taken" matches REJECT_FOUND_RE).
OUR_ECHO_RE = re.compile(
    r'(More rooms available on my rental channel'
    r'|already\s+(?:taken|rented\s+out)\b.*(?:room|unit)|room\s+is\s+(?:already\s+)?taken'
    r'|I will send the unit number)',
    re.I,
)

def analyze(rows, jid_in_conv):
    """From a tenant's chat rows (ASC), derive WA evidence."""
    had_form = jid_in_conv          # conversation-state.json is the primary form-sent signal
    had_profile = False
    inbounds = []
    for is_me, content in rows:
        if not content:
            continue
        filled = is_filled_profile(content)
        if is_me == 0:
            if (ALREADY_SENT_RE.search(content) and not filled) or OUR_ECHO_RE.search(content):
                # our own send mis-stored as inbound -> form marker, never tenant speech
                had_form = True
                continue
            inbounds.append(content)
            if filled:
                had_profile = True
        elif ALREADY_SENT_RE.search(content) and not filled:
            # outbound blank form (manual phone send OR bot send) -> form was sent
            had_form = True
    return had_form, had_profile, inbounds

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
        # "never reopens closed" was documented but never enforced: closed statuses are
        # multi-word ("closed (stale)") so the exact-match SKIP set missed them and
        # RANK.get(closed,0)=0 let an old returned profile promote them back to
        # profile-received — the stale sweep then re-closed the same ~167 rows every
        # run for weeks (found 20 Aug 2026 via the double "closed 167" log). A closed
        # tenant reopens only by hand or by NEW inbound through Part B, never by this
        # whole-history reconciler.
        # An already-rejected row with contact_state still "active" carries no
        # sub-kind — mostly Part B AI writes that set status without one. Those
        # rows are immortal (close_stale treats rejected as terminal) AND
        # indistinguishable from a re-engageable budget objection, which is how
        # 78 found/not-interested tenants sat on the match roster on 21 Aug 2026.
        # Re-derive ONLY the sub-kind for them: found -> found_place, off ->
        # not_interested; budget/none stays active. Status itself never changes.
        reclassify_only = (cur == "rejected"
                           and (x.get("contact_state") or "active") == "active")
        if not reclassify_only and (cur in SKIP or str(cur or "").lower().startswith("closed")):
            continue
        jids = candidate_jids(x, pn_to_lid)
        if not jids:
            continue
        jid_in_conv = any(j in conv_jids for j in jids)
        rows = fetch_rows(con, jids)
        if not rows and not jid_in_conv:
            continue
        had_form, had_profile, inbounds = analyze(rows, jid_in_conv)

        if reclassify_only:
            rk = reject_kind(inbounds)
            new_cs = {"off": "not_interested", "found": "found_place"}.get(rk)
            if new_cs and x.get("contact_state") != new_cs:
                rec = {"id": x.get("id"), "name": x.get("name"),
                       "from": cur, "to": cur,
                       "contact_state": f"{x.get('contact_state')} -> {new_cs}"}
                x["contact_state"] = new_cs
                x["contact_state_updated"] = TODAY_SGT
                counts[f"rejected contact_state -> {new_cs}"] += 1
                changes.append(rec)
            continue

        new_status = None
        new_cs = None
        rk = reject_kind(inbounds)
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
