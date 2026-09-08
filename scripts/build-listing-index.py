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
MSG_DB = os.environ.get("BLI_MSG_DB", HOME + "/whatsapp-mcp/whatsapp-bridge/store/messages.db")
PUB_LISTINGS = os.environ.get("BLI_PUBLIC_LISTINGS", HOME + "/crestbrick-consult/public/listings.json")
PORTAL_IDS_FILE = os.environ.get("BLI_PORTAL_IDS",
    "/private/tmp/claude-501/-Users-winfredquek-crestbrick-consult/92b405a9-4a65-471d-bd8e-97356c5a5b42"
    "/scratchpad/landlord-portal-ids.json")
IDX_OUT = os.environ.get("BLI_IDX_OUT")   # when set, --fill-missing writes the FULL proposed
                                          # index (existing + new) here instead of the live IDX
DRY = "--dry-run" in sys.argv
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_listing_index_from_landlords as _SYNC   # reuse its gender/ethnicity/age parsers

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

# ---------- fill-missing mode: every active/active-verify landlord that HAS NO index
# entry at all (not only those that already carry a listing_key — the bug that left 51 of
# 53 active landlords with zero matcher coverage). Conservative like new_entry() above: an
# unparseable gate is left "any"/None the same way qualify() treats an unknown gate, so a
# bad parse can only ever produce NEEDS_INFO, never a false DISQUALIFIED.

_ST_ABBR = {"street": "st", "road": "rd", "avenue": "ave", "drive": "dr",
            "crescent": "cres", "close": "cl", "place": "pl", "terrace": "ter"}

def _addr_variants(address):
    """Candidate pg_url_keywords for an address. Keeps the block/unit number attached to the
    street wherever the street itself carries no distinguishing trailing number, so a bare
    generic segment ('jurong west') is never emitted on its own — cross listing collisions
    are then caught and stripped by _dedupe_conflicting()."""
    addr = re.sub(r"#.*", "", address or "").strip()
    addr = re.sub(r"^\s*(blk|block)\.?\s+", "", addr, flags=re.I)
    addr = re.sub(r"\s+", " ", addr).strip()
    if not addr:
        return []
    low = addr.lower()
    m = re.match(r"(\d+[a-z]?)\s+(.+)", low)
    variants = {low}
    if not m:
        return sorted(variants)              # property name only, e.g. "oxley edge"
    num, rest = m.group(1), m.group(2)
    words = rest.split()
    variants.add(num + " " + rest)            # "703 jurong west street 71"
    variants.add("blk " + num + " " + rest)
    if re.search(r"\d$", rest):
        # the street itself is numbered ("jurong west street 71") -> specific on its own
        variants.add(rest)
        for full, abbr in _ST_ABBR.items():
            if re.search(r"\b" + full + r"\b", rest):
                variants.add(re.sub(r"\b" + full + r"\b", abbr, rest))
        if len(words) >= 2:
            variants.add(num + " " + " ".join(words[:2]))   # "703 jurong west"
    else:
        # no trailing number on the street -> only ever emit it WITH the block number, so a
        # generic street name shared by several blocks never becomes a bare keyword
        if len(words) >= 1:
            variants.add(num + " " + words[0])
    return sorted(v for v in variants if v)

_RE_RENT_ADDR = re.compile(r"RENT - ([^\n]+)")
_RE_INTERESTED = re.compile(r"interested in:\s*([^\n]+)", re.I)
_RE_99_STAR = re.compile(r"\bin \*([^*]+)\*")
_RE_99_ROOMRENT = re.compile(r"Room Rent:\s*([^.\n]+)", re.I)
_RE_PG_ID = re.compile(r"propertyguru\.com\.sg/l/(\d+)")
_RE_99_CODE = re.compile(r"99\.co/e/([A-Za-z0-9]+)")

def harvest_portal_ids(days=60, msg_db=None):
    """Address text -> set of PropertyGuru listing ids / 99.co codes, parsed from tenant
    enquiry messages in the last DAYS days. Read only (uri=ro); any failure (bridge down,
    db locked, table missing) returns {} rather than raising -- this is best effort
    enrichment, never a hard requirement for the entries to be usable."""
    out = {}
    try:
        con = sqlite3.connect("file:" + (msg_db or MSG_DB) + "?mode=ro", uri=True, timeout=15)
    except Exception:
        return out
    try:
        rows = con.execute(
            "SELECT content FROM messages WHERE is_from_me=0 AND datetime(timestamp) > "
            "datetime('now', ?) AND (content LIKE '%propertyguru.com.sg/l/%' "
            "OR content LIKE '%99.co/e/%')", ("-" + str(days) + " days",)).fetchall()
    except Exception:
        return out
    finally:
        con.close()
    for (content,) in rows:
        if not content:
            continue
        addr = None
        for rx in (_RE_RENT_ADDR, _RE_INTERESTED, _RE_99_STAR, _RE_99_ROOMRENT):
            mm = rx.search(content)
            if mm:
                addr = mm.group(1); break
        if not addr:
            continue
        addr = addr.strip().rstrip(".").lower()
        ids = set(_RE_PG_ID.findall(content)) | set(_RE_99_CODE.findall(content))
        if not ids:
            continue
        out.setdefault(addr, set()).update(ids)
    return out

def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()

def match_harvested(address, harvested):
    a = _norm(address)
    out = set()
    for haddr, ids in (harvested or {}).items():
        h = _norm(haddr)
        if not h or not a:
            continue
        if h in a or a in h:
            out |= ids
    return out

def _load_portal_ids_file(path=None):
    try:
        d = json.load(open(path or PORTAL_IDS_FILE))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def portal_ids_for(lid, listing_key, portal_map):
    ids = set()
    for key in (lid, listing_key):
        v = portal_map.get(key) if key else None
        if not v:
            continue
        if isinstance(v, str):
            ids.add(v)
        elif isinstance(v, (list, tuple, set)):
            ids.update(str(x) for x in v if x)
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str):
                    ids.add(vv)
                elif isinstance(vv, (list, tuple, set)):
                    ids.update(str(x) for x in vv if x)
    return ids

def _slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return re.sub(r"-+", "-", s)

def public_listing_key_map(path=None):
    """{landlord id -> public/listings.json id} for landlords already synced to the website,
    so a generated listing_key matches that style verbatim (e.g. common-room-93-paya-lebar-
    way-ll136) instead of drifting into a second naming scheme."""
    out = {}
    try:
        d = json.load(open(path or PUB_LISTINGS))
    except Exception:
        return out
    for it in d.get("listings", []) or []:
        i = it.get("id") or ""
        mm = re.search(r"-([a-z]{2}\d+)$", i)
        if mm:
            out[mm.group(1).upper()] = i
    return out

def listing_key_for(l, pub_map):
    pub = pub_map.get(l["id"])
    if pub:
        return pub
    addr = l.get("full_address") or l.get("property_name") or l["id"]
    return _slugify(addr) + "-" + l["id"].lower()

_CLOSED_WORDS = ("closed", "tenanted", "archived", "dropped", "duplicate",
                 "unregistered", "unqualified", "sale-active", "cold", "channel")

def _is_active_landlord(l):
    """active/active-verify PLUS any status carrying a parenthetical note on top of one of
    those two (e.g. 'active (re marketed...)', 'available (reopened, per Winfred 2 Sep
    2026)') -- an exact-match check silently dropped LL089 (Jalan Batu, reopened 2 Sep) and
    would drop any future re-marketed landlord the same way."""
    st = str(l.get("status", "")).strip().lower()
    return st.startswith("active") or st.startswith("available")

def derive_status(l):
    st = str(l.get("status", "")).lower()
    if "hold" in st:
        return "hold"
    if any(w in st for w in _CLOSED_WORDS):
        return "closed (landlord db: " + l.get("status", "") + ")"
    return "open"

def fill_missing_entry(l, pub_map, harvested, portal_map):
    r = l.get("requirements") or {}
    addr = l.get("full_address") or l.get("property_name") or ""
    lk = listing_key_for(l, pub_map)

    kws = set(_addr_variants(addr))
    kws |= portal_ids_for(l["id"], lk, portal_map)
    kws |= match_harvested(addr, harvested)

    g = _SYNC.parse_gender(r.get("gender"))
    gender = g[0] if g else "any"
    couple_ok = bool(g[1]) if g else False
    couple_married = bool(g[2]) if g else False
    eth = _SYNC.parse_ethnicity(r.get("ethnicity")) or {"mode": "any", "list": []}
    nat = _SYNC.parse_ethnicity(r.get("nationality")) or {"mode": "any", "list": []}
    min_age = _SYNC.parse_min_age(r)
    max_pax = parse_pax(r.get("max_pax"))
    lease_min = parse_lease(r.get("lease_min")) or 12
    budget_floor = l.get("rent_min") if isinstance(l.get("rent_min"), int) else None

    found, unknown = [], []
    for name, val in (("gender", g), ("ethnicity", _SYNC.parse_ethnicity(r.get("ethnicity"))),
                      ("min_age", min_age), ("max_pax", max_pax),
                      ("lease_min", parse_lease(r.get("lease_min"))), ("budget_floor", budget_floor)):
        (found if val not in (None, False) else unknown).append(name)

    entry = {
        "listing_key": lk, "landlord_id": l["id"], "landlord_phone": l.get("phone", ""),
        "property_name": l.get("property_name") or addr, "block_address": addr,
        "postal": r.get("postal", ""),
        "deal_type": l.get("deal_type", "rent"),
        "status": derive_status(l),
        "pg_url_keywords": sorted(kws),
        "requirements": {
            "gender": gender, "couple_ok": couple_ok, "couple_must_be_married": couple_married,
            "ethnicity_rule": eth, "nationality_pref": nat, "pass_type_allowed": [],
            "occupation_rule": {"mode": "any", "list": []},
            "max_pax": max_pax, "lease_min_months": lease_min, "lease_max_months": None,
            "budget_floor": budget_floor, "min_age": min_age,
            "cooking": "light", "pets_tenant_may_bring": False, "smoking": "any",
            "notes_human": "GENERATED (fill-missing) from landlord-db " + l["id"],
        },
        "district": l.get("district", ""),
        "notes": "GENERATED (fill-missing) from landlord-db " + l["id"] + " on " + STAMP,
    }
    return entry, found, unknown

def _is_open(e):
    st = str(e.get("status", "")).lower()
    return not (st.startswith("closed") or st == "hold")

def check_keyword_specificity(all_listings):
    """Every keyword of every OPEN listing checked against every OTHER open listing's own
    address text. Returns a list of (listing_key, keyword, other_listing_key) conflicts. A
    clean fill-missing run must return []; the test suite asserts exactly that."""
    open_l = [e for e in all_listings if _is_open(e)]
    conflicts = []
    for e in open_l:
        addr_self = _norm(e.get("block_address") or e.get("address") or e.get("property_name") or "")
        for kw in (e.get("pg_url_keywords") or []):
            kwl = _norm(kw)
            if not kwl or (kwl.isdigit() and len(kwl) >= 6):
                continue                      # portal ids are always specific
            for o in open_l:
                if o is e:
                    continue
                addr_o = _norm(o.get("block_address") or o.get("address") or o.get("property_name") or "")
                if addr_o and kwl in addr_o and kwl != addr_self:
                    conflicts.append((e["listing_key"], kw, o["listing_key"]))
    return conflicts

def _dedupe_conflicting(new_entries, existing_open):
    """Strip any keyword of a NEW entry that is a substring of another OPEN listing's own
    address (existing or newly generated) -- existing entries are never mutated."""
    pool = existing_open + new_entries
    for e in new_entries:
        others = [o for o in pool if o["listing_key"] != e["listing_key"] and _is_open(o)]
        keep = []
        for kw in e.get("pg_url_keywords", []):
            kwl = _norm(kw)
            if not kwl:
                continue
            if kwl.isdigit() and len(kwl) >= 6:
                keep.append(kw); continue     # portal id -- always specific, always kept
            addrs = [_norm(o.get("block_address") or o.get("address") or o.get("property_name") or "")
                     for o in others]
            if any(kwl and kwl in a for a in addrs if a):
                continue                      # would cross match another open listing -> drop
            keep.append(kw)
        e["pg_url_keywords"] = sorted(set(keep))

def fill_missing(idx, db, msg_db=None, pub_listings_path=None, portal_ids_path=None):
    """Returns (new_entries, report) — report rows are
    (listing_key, address, keyword_count, status, gates_found, gates_unknown)."""
    existing_lids = {e.get("landlord_id") for e in idx["listings"] if e.get("landlord_id")}
    pub_map = public_listing_key_map(pub_listings_path)
    harvested = harvest_portal_ids(msg_db=msg_db)
    portal_map = _load_portal_ids_file(portal_ids_path)

    new_entries, report = [], []
    for l in db["landlords"]:
        if not _is_active_landlord(l):
            continue
        if l["id"] in existing_lids:
            continue
        entry, found, unknown = fill_missing_entry(l, pub_map, harvested, portal_map)
        new_entries.append(entry)
        report.append((entry["listing_key"], entry["block_address"], len(entry["pg_url_keywords"]),
                       entry["status"], found, unknown))

    existing_open = [e for e in idx["listings"] if _is_open(e)]
    _dedupe_conflicting(new_entries, existing_open)
    for lk, addr, _n, status, found, unknown in report:
        pass  # report kept as originally computed keyword count (pre dedupe), for visibility
    return new_entries, report

def fill_missing_main():
    APPLY = "--apply" in sys.argv
    idx = json.load(open(IDX))
    db = json.load(open(DB))
    n_active = sum(1 for l in db["landlords"] if _is_active_landlord(l))
    new_entries, report = fill_missing(idx, db)
    print(f"== fill-missing: {len(new_entries)} new entries for {n_active} active/active-verify landlords "
          f"({n_active - len(new_entries)} already had an index entry) ==")
    for lk, addr, nkw, status, found, unknown in report:
        print(f"  {lk:50} | {addr[:38]:38} | kw={nkw:2} | {status:8} | found={found} unknown={unknown}")

    conflicts = check_keyword_specificity(idx["listings"] + new_entries)
    if conflicts:
        print("\n== CROSS MATCH WARNINGS (should be empty) ==")
        for c in conflicts:
            print("  ", c)
    else:
        print("\n== keyword specificity: clean, no open listing keyword crosses another ==")

    if IDX_OUT:
        merged = dict(idx); merged["listings"] = idx["listings"] + new_entries
        tmp = IDX_OUT + ".tmp"
        json.dump(merged, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, IDX_OUT)
        print(f"\nWROTE proposed full index to {IDX_OUT} ({len(merged['listings'])} total listings)")
        return
    if "--dry-run" in sys.argv or not APPLY:
        print("\nDRY RUN -- nothing written. Re-run with --apply to write listing-index.json, "
              "or set BLI_IDX_OUT to write the full proposed index elsewhere.")
        print(json.dumps(new_entries, indent=1, ensure_ascii=False))
        return
    lf = open(LOCK, "a+"); fcntl.flock(lf, fcntl.LOCK_EX)
    try:
        idx_live = json.load(open(IDX))
        shutil.copy(IDX, IDX + ".bak-fillmissing-" + STAMP)
        idx_live["listings"] = idx_live["listings"] + new_entries
        tmp = IDX + ".tmp"
        json.dump(idx_live, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, IDX)
        print(f"\nWROTE: {IDX} (+{len(new_entries)} new entries)")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)

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
    if "--fill-missing" in sys.argv:
        fill_missing_main()
    else:
        main()
