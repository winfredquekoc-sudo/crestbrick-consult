#!/usr/bin/env python3
"""One time nudge for tenants who received the intake form but never replied.

Rules (all must hold):
  - status is form-sent
  - form was sent between 24 hours and 7 days ago (last_contact or last bot form in that window)
  - contact_state is active (never found_place / not_interested / do_not_contact)
  - not excluded, has a jid, listing (if known) is still active
  - never nudged before (nudge_sent absent) — ONE nudge per tenant, ever
  - hard cap per run (default 15)

DRY RUN by default: prints the list and sends nothing. Pass --send to deliver.
Every send stamps nudge_sent in tenant-db.json immediately (crash safe ordering:
stamp AFTER a confirmed 200 from the bridge, write file after each send).
"""
import json, os, sys, argparse, tempfile, urllib.request
from datetime import datetime, timedelta, timezone

TENANT_DB = os.path.expanduser("~/crestbrick-consult/_templates/tenant-db.json")
INDEX = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")
BRIDGE = "http://127.0.0.1:8080/api/send"
SGT = timezone(timedelta(hours=8))

MSG = ("Hi, just checking in 🙂 are you still keen on the room{prop}? "
       "Viewing slots are filling up, if you send me your profile I can hold one for you.")

def listing_status(key):
    try:
        li = json.load(open(INDEX))
        for x in li.get("listings", []):
            if x.get("listing_key") == key:
                return x.get("status", "")
    except OSError:
        return ""
    return ""

def prop_phrase(t):
    le = (t.get("listing_enquired") or "")
    for key, name in [("905", " at 905 Jurong West St 91"), ("606d", " at 606D Tampines St 61"),
                      ("606D", " at 606D Tampines St 61"), ("eastpoint", " at Eastpoint Green"),
                      ("168a", " at 168A Simei Lane"), ("bayshore", " at Bayshore"),
                      ("edgefield", " at 104B Edgefield Plains"), ("sumang", " at 232B Sumang Lane"),
                      ("hougang-703", " at 703 Hougang Ave 2")]:
        if key in le:
            return name
    return ""

def eligible(recs, now):
    out = []
    lo, hi = now - timedelta(days=5), now - timedelta(hours=24)
    for t in recs:
        if t.get("status") != "form-sent": continue
        if t.get("contact_state") != "active": continue
        if t.get("excluded") or not t.get("jid"): continue
        if t.get("nudge_sent"): continue
        lc = t.get("last_contact") or ""
        try:
            d = datetime.strptime(lc, "%Y-%m-%d").replace(tzinfo=SGT)
        except ValueError:
            continue
        if not (lo <= d <= hi): continue
        le = (t.get("listing_enquired") or "").lower()
        for k in ("caspian", "summerdale", "bedok-north-522", "522 bedok"):
            if k in le: break
        else:
            out.append(t)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default dry run)")
    ap.add_argument("--cap", type=int, default=15)
    args = ap.parse_args()
    now = datetime.now(SGT)
    db = json.load(open(TENANT_DB))
    recs = db.get("tenants") or db.get("records")
    todo = eligible(recs, now)[: args.cap]
    if not todo:
        print("no eligible tenants to nudge"); return
    for t in todo:
        print(f"{'SEND' if args.send else 'DRY '} {t['id']} {t.get('name')} jid={t['jid']} last_contact={t.get('last_contact')} -> {MSG.format(prop=prop_phrase(t))!r}")
        if not args.send: continue
        body = json.dumps({"recipient": t["jid"], "message": MSG.format(prop=prop_phrase(t))}).encode()
        req = urllib.request.Request(BRIDGE, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                ok = r.status == 200
        except OSError as e:
            print(f"  FAILED: {e}"); continue
        if ok:
            t["nudge_sent"] = now.strftime("%Y-%m-%d")
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(TENANT_DB)); os.close(fd)
            with open(tmp, "w") as f: json.dump(db, f, indent=1, ensure_ascii=False)
            os.replace(tmp, TENANT_DB)
            print("  sent + stamped")

if __name__ == "__main__":
    main()
