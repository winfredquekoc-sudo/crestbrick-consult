#!/usr/bin/env python3
"""
clean_intake_state_landlords.py — purge non-tenant conversations from the live tenant intake
state (intake-state.json), and (in --quiet mode) emit a funnel-health summary for the nightly
Telegram digest.

Why: the intake state accumulates landlords (Winfred messaging them first latched
manual_takeover BEFORE the exclusion gate) plus the odd agent. They are not prospective tenants
and should never sit in tenant intake. The engine now blocks KNOWN landlords at source; this
script is the recurring sweep that also catches landlords not yet in the landlord DB (detected
by Winfred's own landlord-side outbound phrasing) and reports them so they can be saved.

Safe on the LIVE runner:
  - acquires the SAME flock the runner uses, so there is no read/modify/write race; the
    runner's next 120s tick simply skips while we hold it (exit 2 if the runner holds it now).
  - backs up intake-state.json before writing.
  - removal criteria are HIGH PRECISION only:
      1. pn (or lid) in the authoritative landlord DB
      2. status starts with 'excluded:'  (agent/colleague/landlord already flagged)
      3. Winfred's OWN outbound contains a landlord-side phrase a tenant message never has
         ('screen tenants', 'your property details', 'photos of your room', 'help me market'...)

Modes:
  (default)        DRY RUN, verbose: lists every conversation that would be removed.
  --apply          Commit the removal (with backup).
  --quiet          Suppress the per-row list; print a short funnel-health + purge summary
                   (intended for the nightly Telegram digest). Combine with --apply nightly.
"""
import os, re, json, sys, fcntl, sqlite3, datetime, importlib.util, collections

WAPIPE = os.path.expanduser("~/crestbrick-consult/src/wa-pipeline")
_spec = importlib.util.spec_from_file_location("E", os.path.join(WAPIPE, "intake_engine.py"))
E = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(E)

STATE = os.path.expanduser("~/.claude/state/listing-templates/intake-state.json")
LOCK  = os.path.expanduser("~/.claude/state/listing-templates/.wa-intake.lock")
MSG   = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
WA    = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")

APPLY = "--apply" in sys.argv
QUIET = "--quiet" in sys.argv

# Phrases Winfred only ever says to a LANDLORD (never to a tenant). Deliberately does NOT match
# the bot's "send your profile to the landlord" (that contains "profile", not "property").
LANDLORD_OUT_RE = re.compile(
    r"screen (?:the |my )?tenant"
    r"|your property details|property details to my"
    r"|photos? of your room|pictures? of your room|more photos of your"
    r"|help me (?:to )?market|market(?:ing)? (?:the|your) propert"
    r"|could you help me answer these questions"
    r"|send (?:me )?your property",
    re.I)

def pn_to_lid(pn):
    try:
        c = sqlite3.connect(WA, timeout=10); c.execute("PRAGMA busy_timeout=10000")
        r = c.execute("SELECT lid FROM whatsmeow_lid_map WHERE pn=?", (pn,)).fetchone(); c.close()
        return r[0] if r else None
    except Exception:
        return None

def outbound_landlord_signal(pn):
    """Return the matching outbound snippet if Winfred's OWN messages to this person carry a
    landlord-side phrase, else None."""
    jids = []
    lid = pn_to_lid(pn)
    if lid: jids.append(lid + "@lid")
    jids.append(pn + "@s.whatsapp.net")
    try:
        mc = sqlite3.connect(MSG, timeout=10); mc.execute("PRAGMA busy_timeout=10000")
    except Exception:
        return None
    try:
        for j in jids:
            for (content,) in mc.execute(
                    "SELECT content FROM messages WHERE chat_jid=? AND is_from_me=1", (j,)):
                if content and LANDLORD_OUT_RE.search(content):
                    return content.strip().replace("\n", " ")[:80]
        return None
    finally:
        mc.close()

def funnel_health(keep):
    """Read-only snapshot of the ACTIVE tenant funnel (post-purge), for the nightly digest."""
    active = len(keep)
    manual = sum(1 for r in keep.values() if r.get("manual_takeover"))
    qualified = sum(1 for r in keep.values() if (r.get("qualify") or {}).get("verdict") == "QUALIFIED")
    viewing = sum(1 for r in keep.values() if r.get("status") in
                  ("viewing_offered", "viewing_confirmed", "viewing_time_proposed"))
    incomplete = sum(1 for r in keep.values()
                     if r.get("status") == "incomplete" or r.get("stage") == "PROFILE_PENDING")
    forms = sum(1 for r in keep.values() if r.get("form_sent"))
    pct = round(100 * manual / active) if active else 0
    return dict(active=active, manual_pct=pct, qualified=qualified,
                viewing=viewing, incomplete=incomplete, forms=forms)

def classify(conv):
    landlord_set = E._landlord_pn_set()
    remove = {}  # pn -> reason
    for pn, r in conv.items():
        st = str(r.get("status", ""))
        if pn in landlord_set:
            remove[pn] = "landlord_db"; continue
        if st.startswith("excluded:"):
            remove[pn] = "status_" + st; continue
        sig = outbound_landlord_signal(pn)
        if sig:
            remove[pn] = "landlord_conversation: " + sig; continue
    keep = {pn: r for pn, r in conv.items() if pn not in remove}
    return remove, keep

def main():
    lf = open(LOCK, "a+")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("runner holds the lock right now; retry in a few seconds"); sys.exit(2)

    s = json.load(open(STATE))
    conv = s.get("conversations", {})
    remove, keep = classify(conv)

    bak = None
    if APPLY:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = STATE + ".bak-" + ts
        with open(bak, "w") as f:
            json.dump(s, f, indent=1, ensure_ascii=False)
        s["conversations"] = keep
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=1, ensure_ascii=False)
        os.replace(tmp, STATE)

    rc = collections.Counter(v.split(":")[0] for v in remove.values())
    unlisted = sum(1 for v in remove.values() if v.startswith("landlord_conversation"))

    if QUIET:
        # compact summary for the nightly Telegram digest
        h = funnel_health(keep)
        verb = "Purged" if APPLY else "Would purge"
        lines = [
            "Tenant funnel auto-maintenance:",
            f"Active: {h['active']} | Manual: {h['manual_pct']}% | Qualified: {h['qualified']} | "
            f"Viewing: {h['viewing']} | Incomplete: {h['incomplete']} | Forms sent: {h['forms']}",
            f"{verb} {len(remove)} non-tenant records "
            f"(landlord_db {rc.get('landlord_db', 0)}, conversation {rc.get('landlord_conversation', 0)}, "
            f"excluded {rc.get('status_excluded', 0)}).",
        ]
        if unlisted:
            lines.append(f"{unlisted} landlord(s) detected by conversation are NOT in landlord-db.json "
                         f"— save them as 'Landlord' contacts so the nightly refresh adds them.")
        print("\n".join(lines))
        return

    # verbose (manual/dry-run) output
    print(f"TOTAL conversations : {len(conv)}")
    print(f"TO REMOVE           : {len(remove)}")
    print(f"KEEP                : {len(keep)}")
    print("removal reasons     :", dict(rc))
    print()
    for pn, why in sorted(remove.items()):
        nm = (conv[pn].get("profile", {}) or {}).get("name") or ""
        label = ("[" + nm + "]") if nm else ""
        print(f"  - {pn:16} {label:24} {why[:92]}")
    if not APPLY:
        print("\nDRY RUN (no changes written). Re-run with --apply to commit.")
        return
    print(f"\nAPPLIED. backup: {bak}")
    print(f"removed {len(remove)}, kept {len(keep)}")

if __name__ == "__main__":
    main()
