#!/usr/bin/env python3
"""
apply_tenant_lifecycle.py — sets each tenant's match_status so the active list only
holds tenants Winfred wants to serve and can match.

match_status:
  found            -> already has a place (contact_state found_place or status tenanted). Archived.
  stale_closed     -> auto closed after 30+ days of silence (close_stale_prospects.py). Off the pool.
  do_not_contact   -> opted out or excluded non tenant.
  excluded_india   -> nationality is India. Excluded from service per Winfred's policy.
  excluded_family  -> has a child or baby. Excluded from service per Winfred's policy.
  in_deal          -> deposit pending, mid transaction.
  short_lease      -> wants less than 6 months. Filtered out (landlords want 12 months).
  below_target     -> 6 to 11 months. Matchable, flagged below the 12 month aim.
  active           -> 12 months or more (or unknown). Primary matching pool.

Only `active` and `below_target` are in the matchable pool. Everything else is off it.
Nationality test is on NATIONALITY (from India), not ethnicity, so a Singaporean or PR
of Indian ethnicity is NOT excluded.
"""
import json, os, re, sqlite3
ROOT = os.path.expanduser("~/crestbrick-consult")
P = os.path.join(ROOT, "_templates/tenant-db.json")
MSG = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")

KID = re.compile(r"\b(baby|babies|infant|toddler|newborn|pregnan\w*|child|children|kid|kids)\b", re.I)
KIDPHRASE = ("my son","my daughter","with my son","with my daughter","with a child","with kid",
             "with kids","1 child","2 child","one child","two child","schooling kid","family with child")

def has_children(con, jid):
    if not jid: return False
    try:
        rows = con.execute("SELECT content FROM messages WHERE chat_jid=? AND is_from_me=0", (jid,)).fetchall()
    except Exception:
        return False
    txt = " ".join((r[0] or "").lower() for r in rows)
    if any(p in txt for p in KIDPHRASE): return True
    return bool(KID.search(txt))

def status_for(x, kids):
    if x.get("excluded"): return "do_not_contact"
    cs = x.get("contact_state"); st = x.get("status")
    if cs == "found_place" or st == "tenanted": return "found"
    if (st or "").startswith("closed"): return "stale_closed"  # auto closed by close_stale_prospects.py (>30d silent); off the pool
    if cs in ("not_interested","do_not_contact"): return "do_not_contact"
    nat = (x.get("nationality") or "").strip().lower()
    if nat in ("indian","india","indian (india)"): return "excluded_india"
    if kids: return "excluded_family"
    if st == "deposit-pending": return "in_deal"
    l = x.get("lease_term_months")
    if isinstance(l,(int,float)):
        if l < 6: return "short_lease"
        if l < 12: return "below_target"
    return "active"

def priority_for(x, ms):
    # serve 12 months or more first, then 6 to 11 months, then lease unknown.
    if ms not in ("active","below_target","in_deal"): return ""
    l = x.get("lease_term_months")
    if isinstance(l,(int,float)):
        if l >= 12: return 1
        if l >= 6:  return 2
    return 3

def main():
    t = json.load(open(P))
    con = sqlite3.connect(MSG)
    import collections
    c = collections.Counter()
    for x in t["tenants"]:
        kids = has_children(con, x.get("jid"))
        x["has_children"] = kids
        ms = status_for(x, kids); x["match_status"] = ms; c[ms] += 1
        x["priority"] = priority_for(x, ms)
    for col in ("match_status","has_children","priority"):
        if col not in t["columns"]: t["columns"].append(col)
    json.dump(t, open(P+".tmp","w"), indent=1, ensure_ascii=False); os.replace(P+".tmp", P)
    print("match_status:", dict(c))

if __name__ == "__main__":
    main()
