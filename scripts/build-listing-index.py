#!/usr/bin/env python3
"""Sync listing-index.json from the landlord master DB (one source of truth).

Contract (v1, deliberately conservative — wrong gates reject real tenants):
  AUTO APPLIED
    - status: landlord tenanted/closed/archived -> index entry closed (never auto REOPENS)
    - lease_min_months: numeric landlord lease_min
    - max_pax: bare int or "N per room" only
    - listing_key backfilled onto the landlord record (via index landlord_id)
  NEW ENTRY created when a landlord record carries a listing_key missing from the index
    (requirements parsed conservatively and printed IN FULL for review; pg_url_keywords
    start from address tokens — extend by hand or via /sync-listings)
  DIFF REPORT ONLY (never auto applied): gender gate, ethnicity/nationality rules,
    budget_floor (room-level pricing lives in free text; a wrong floor walks a $1,300
    budget into a $2,300 room or rejects a good lead)
  PRESERVED untouched on existing entries: pg_url_keywords, portal_url, fixed_viewing,
    open_intake, notes, manual closes.
  RETRO BIND: conversations <7 days old with no listing_key whose recent text matches a
    listing's keywords are bound under the engine flock (no messages sent; binding just
    lets the next inbound qualify).

Run after any landlord DB edit:  python3 scripts/build-listing-index.py [--dry-run]
"""
import json, re, sys, os, shutil, fcntl, datetime, sqlite3

HOME = os.path.expanduser("~")
DB = os.environ.get("BLI_DB", HOME + "/crestbrick-consult/_templates/landlord-db.json")
IDX = os.environ.get("BLI_IDX", HOME + "/.claude/state/listing-templates/listing-index.json")
STATE = os.environ.get("BLI_STATE", HOME + "/.claude/state/listing-templates/intake-state.json")
LOCK = HOME + "/.claude/state/listing-templates/.wa-intake.lock"
MSG_DB = HOME + "/whatsapp-mcp/whatsapp-bridge/store/messages.db"
DRY = "--dry-run" in sys.argv
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

def parse_gender(t):
    t = (t or "").lower()
    if not t: return None
    # both genders mentioned together, or an explicit "any", means no gate — check FIRST
    # ("Any (male/female)" must never parse as a female gate)
    if t.strip().startswith("any") or re.search(r"male\s*/\s*female|female\s*/\s*male|male or female|female or male", t):
        return "any"
    if re.search(r"\bno males?\b", t): return "female_only"
    if re.search(r"\bno females?\b", t): return "male_only"
    only = "only" in t or re.search(r"\bsingle\b", t)
    pref = "prefer" in t
    f = "female" in t or re.fullmatch(r"f", t.strip())
    m = bool(re.search(r"\bmale\b", t)) and "female" not in t
    if f and only: return "female_only"
    if f and pref: return "female_pref"
    if m and only: return "male_only"
    if m and pref: return "male_pref"
    if f: return "female_pref"
    if m: return "male_pref"
    return "any"

def parse_eth(t):
    t = (t or "").strip()
    low = t.lower()
    if not t or re.search(r"^any\b|^all\b|all races|any race|no pref|no preference|welcome", low):
        return {"mode": "any", "list": []}
    groups = ["Chinese", "Malay", "Indian", "Eurasian", "Korean", "Japanese", "Sinhalese"]
    lst = [g for g in groups if g.lower() in low]
    if re.search(r"\bno\b|\bexclude\b", low) and lst:
        # "Chinese only (no Indian/Malay)" is an only-rule, not an exclude
        if "only" in low:
            keep = [g for g in lst if not re.search(r"no[^,;(]*" + g.lower(), low)]
            if keep: return {"mode": "only", "list": keep}
        excl = [g for g in lst if re.search(r"no[^,;(]*" + g.lower(), low)]
        if excl: return {"mode": "exclude", "list": excl}
    if "only" in low and lst: return {"mode": "only", "list": lst}
    if "pref" in low and lst: return {"mode": "prefer", "list": lst}
    return None   # unparseable -> report, never guess

def parse_pax(v):
    if isinstance(v, int): return v
    m = re.fullmatch(r"\s*(\d+)\s*(?:pax)?\s*(?:per room)?\s*", str(v or "").lower())
    return int(m.group(1)) if m else None

def parse_lease(v):
    if isinstance(v, int): return v
    m = re.search(r"\d+", str(v or ""))
    return int(m.group(0)) if m else None

def yesno(v, yes="no"):
    low = str(v or "").strip().lower()
    if low.startswith("no"): return "no"
    if low in ("yes", "allowed", "ok", "any"): return "any"
    if "light" in low: return "light"
    return None

def new_entry(l):
    r = l.get("requirements") or {}
    lk = l["listing_key"]
    addr = l.get("full_address") or ""
    toks = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", addr)[:4]]
    req = {
        "gender": parse_gender(r.get("gender")) or "any",
        "couple_ok": "couple" in str(r.get("other", "")).lower() and "no couple" not in str(r.get("other", "")).lower(),
        "ethnicity_rule": parse_eth(r.get("ethnicity")) or {"mode": "any", "list": []},
        "nationality_pref": parse_eth(r.get("nationality")) or {"mode": "any", "list": []},
        "occupation_rule": {"mode": "any", "list": []},
        "max_pax": parse_pax(r.get("max_pax")),
        "lease_min_months": parse_lease(r.get("lease_min")) or 12,
        "budget_floor": l.get("rent_min"),
        "cooking": yesno(r.get("cooking")) or "light",
        "pets_tenant_may_bring": yesno(r.get("pets")) != "any",
        "smoking": yesno(r.get("smoking")) or "no",
    }
    return {
        "listing_key": lk, "landlord_id": l["id"], "landlord_phone": l.get("phone", ""),
        "status": "active", "deal_type": l.get("deal_type", "rent"),
        "property_type": l.get("property_type", ""), "address": addr,
        "postal": (l.get("requirements") or {}).get("postal", ""),
        "pg_url_keywords": [" ".join(toks[:2]), " ".join(toks[1:4])] if toks else [],
        "notes": "GENERATED from landlord-db " + l["id"] + " on " + STAMP + " — review gates + keywords",
        "requirements": req,
        "district": l.get("district", ""),
    }

def main():
    db = json.load(open(DB))
    idx = json.load(open(IDX))
    landlords = {l["id"]: l for l in db["landlords"]}
    report, applied = [], []

    for e in idx["listings"]:
        lid = e.get("landlord_id")
        l = landlords.get(lid)
        if not l: continue
        lk = e["listing_key"]
        # backfill the join onto the landlord record. A landlord can own SEVERAL listings
        # (LL012 has caspian AND summerdale), so the full set lives in listing_keys;
        # the scalar listing_key stays as the first-known key and never flip-flops.
        keys = set(l.get("listing_keys") or ([l["listing_key"]] if l.get("listing_key") else []))
        if lk not in keys:
            keys.add(lk)
            l["listing_keys"] = sorted(keys)
            if not l.get("listing_key"):
                l["listing_key"] = lk
            applied.append(f"{lk}: recorded on {lid} (listing_keys={sorted(keys)})")
        elif len(keys) > 1 and l.get("listing_keys") != sorted(keys):
            l["listing_keys"] = sorted(keys)
        st_l = str(l.get("status", "")).lower()
        st_e = str(e.get("status", "")).lower()
        if any(w in st_l for w in ("tenanted", "closed", "archived")) and not (st_e.startswith("closed") or st_e == "hold"):
            e["status"] = "closed (landlord db: " + l.get("status", "") + ", synced " + STAMP + ")"
            applied.append(f"{lk}: closed (landlord status '{l.get('status')}')")
        r = l.get("requirements") or {}
        req = e.get("requirements") or {}
        lm = parse_lease(r.get("lease_min"))
        if lm and req.get("lease_min_months") != lm:
            applied.append(f"{lk}: lease_min_months {req.get('lease_min_months')} -> {lm}")
            req["lease_min_months"] = lm
        px = parse_pax(r.get("max_pax")) if isinstance(r.get("max_pax"), int) else None
        active = not (str(e.get("status", "")).lower().startswith("closed") or e.get("status") == "hold")
        if px and active and req.get("max_pax") not in (None, px):
            applied.append(f"{lk}: max_pax {req.get('max_pax')} -> {px}")
            req["max_pax"] = px
        elif (pv := parse_pax(r.get("max_pax"))) and req.get("max_pax") not in (None, pv) and not isinstance(r.get("max_pax"), int):
            report.append(f"{lk}: max_pax index={req.get('max_pax')} vs landlord text '{r.get('max_pax')}' (per room semantics — verify)")
        # judgment calls: report only
        g = parse_gender(r.get("gender"))
        if g and req.get("gender") and g != req.get("gender"):
            report.append(f"{lk}: gender gate index='{req.get('gender')}' vs landlord text '{r.get('gender')}' (parsed {g})")
        if l.get("rent_min") and req.get("budget_floor") and l["rent_min"] != req["budget_floor"]:
            report.append(f"{lk}: budget_floor index={req['budget_floor']} vs landlord rent_min={l['rent_min']} (room level pricing — verify)")
        eth = parse_eth(r.get("ethnicity"))
        if eth is None and r.get("ethnicity"):
            report.append(f"{lk}: ethnicity text unparseable: '{str(r.get('ethnicity'))[:60]}'")
        elif eth and eth != (req.get("ethnicity_rule") or {"mode": "any", "list": []}):
            report.append(f"{lk}: ethnicity_rule index={req.get('ethnicity_rule')} vs landlord '{r.get('ethnicity')}'")

    known = {e["listing_key"] for e in idx["listings"]}
    for l in db["landlords"]:
        for lk in (l.get("listing_keys") or ([l["listing_key"]] if l.get("listing_key") else [])):
            if lk in known: continue
            l = dict(l, listing_key=lk)
            e = new_entry(l)
            idx["listings"].append(e)
            applied.append(f"{lk}: NEW index entry created from {l['id']} — REVIEW:")
            applied.append(json.dumps(e, indent=1))

    if DRY:
        print("DRY RUN — nothing written")
    else:
        shutil.copy(IDX, IDX + ".bak-gen-" + STAMP)
        json.dump(idx, open(IDX, "w"), indent=1, ensure_ascii=False)
        shutil.copy(DB, DB + ".bak-gen-" + STAMP)
        json.dump(db, open(DB, "w"), indent=2, ensure_ascii=False)

    print("== applied ==");  [print(" ", a) for a in applied] or print("  (nothing)")
    print("== review (NOT applied) ==");  [print(" ", r) for r in report] or print("  (clean)")

    # ---- retro bind ----
    sys.path.insert(0, HOME + "/crestbrick-consult/src/wa-pipeline")
    import intake_engine as E
    reqs = {e["listing_key"]: e for e in idx["listings"]}
    now = datetime.datetime.now(datetime.timezone.utc)
    con = sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=15)
    lf = open(LOCK, "a+"); fcntl.flock(lf, fcntl.LOCK_EX)
    try:
        st = json.load(open(STATE))
        bound = []
        for pn, rec in st.get("conversations", {}).items():
            if rec.get("listing_key") or rec.get("terminal"): continue
            rows = con.execute(
                "SELECT content FROM messages WHERE chat_jid LIKE ? AND is_from_me=0 "
                "AND datetime(timestamp) > datetime('now','-7 days') ORDER BY rowid DESC LIMIT 15",
                ("%" + pn + "%",)).fetchall()
            blob = " ".join((r[0] or "").lower() for r in rows)
            if not blob: continue
            for lk, e in reqs.items():
                stx = str(e.get("status", "")).lower()
                if stx.startswith("closed") or stx == "hold": continue
                if any(k.lower() in blob for k in e.get("pg_url_keywords", []) if len(k) > 3):
                    rec["listing_key"] = lk
                    if rec.get("status") == "needs_listing": rec["status"] = "form_sent"
                    bound.append(pn + " -> " + lk)
                    break
        if bound and not DRY:
            shutil.copy(STATE, STATE + ".bak-retrobind-" + STAMP)
            tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w"), ensure_ascii=False)
            shutil.move(tmp, STATE)
        print("== retro bound ==");  [print(" ", b) for b in bound] or print("  (none)")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)

if __name__ == "__main__":
    main()
