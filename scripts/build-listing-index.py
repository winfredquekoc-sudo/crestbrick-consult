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
    HOME + "/crestbrick-consult/_templates/landlord-portal-ids.json")
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
    # landlord-db full_address values routinely carry a human note in parens ("(unit TBC;
    # Bukit Batok MRT)", "(standalone studio at landed house, 430sqft)") -- strip it before
    # deriving keywords, or the whole note rides along as part of every variant (the same
    # "note paragraph becomes a keyword" symptom the portal_ids_for fix addresses elsewhere,
    # just via the address field itself rather than landlord-portal-ids.json).
    addr = re.sub(r"\([^)]*\)", " ", address or "")
    addr = re.sub(r"#.*", "", addr).strip()
    addr = re.sub(r"^\s*(blk|block)\.?\s+", "", addr, flags=re.I)
    # an internal comma ("Blk 47 Marine Crescent, Singapore 440047") glued onto the street
    # word ("crescent,") and blocked the clean "47 marine crescent" keyword a tenant would
    # actually type -- treat every comma as a plain separator, same as whitespace.
    addr = addr.replace(",", " ")
    addr = re.sub(r"\s+", " ", addr).strip()
    addr = addr.rstrip(",").strip()
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

_RE_DIGITS6 = re.compile(r"\d{6,}")

def _extract_portal_tokens(s):
    """Pull clean PropertyGuru listing ids (bare runs of 6+ digits) and 99.co codes out of a
    raw portal_ids string, which may carry surrounding prose (landlord-portal-ids.json is
    hand/LLM curated and a value like 'PropertyGuru listing 500248513 (asking $1,400/month,
    expired, to be re-listed)' is real data) -- the raw string itself is NEVER trusted as a
    keyword, only digit runs / 99.co codes found inside it."""
    out = set()
    if not isinstance(s, str):
        return out
    out.update(_RE_DIGITS6.findall(s))
    out.update(_RE_99_CODE.findall(s))
    return out

def portal_ids_for(lid, listing_key, portal_map):
    """landlord id / listing key -> set of clean portal ids, reading ONLY the record's
    'portal_ids' field. A landlord-portal-ids.json record also carries landlord_name,
    full_address, rent_by_room etc -- those must NEVER be iterated as keyword candidates
    (that bug turned landlord first names, a raw WhatsApp @lid, and free-text rent notes
    into pg_url_keywords)."""
    ids = set()
    for key in (lid, listing_key):
        v = portal_map.get(key) if key else None
        if not isinstance(v, dict):
            continue
        rec = v.get("portal_ids")
        if not rec:
            continue
        if isinstance(rec, str):
            ids |= _extract_portal_tokens(rec)
        elif isinstance(rec, (list, tuple, set)):
            for item in rec:
                if isinstance(item, str):
                    ids |= _extract_portal_tokens(item)
        elif isinstance(rec, dict):
            for vv in rec.values():
                if isinstance(vv, str):
                    ids |= _extract_portal_tokens(vv)
                elif isinstance(vv, (list, tuple, set)):
                    for item in vv:
                        if isinstance(item, str):
                            ids |= _extract_portal_tokens(item)
    return ids

_KEYWORD_STREET_WORDS = {"road", "rd", "street", "st", "ave", "avenue", "crescent", "cres",
    "drive", "dr", "lane", "link", "way", "close", "park", "heights", "terrace", "villas",
    "loft", "edge", "court", "gardens", "hill", "walk", "place", "view"}

def _is_valid_keyword(kw, address=""):
    """A generated pg_url_keyword must be specific enough not to cross-match another
    listing: a portal id / 99.co code always qualifies; any other candidate needs length
    >= 6 AND (a digit, OR a street-type word, OR it equals the address's own multi-word
    estate/condo name) -- rejects short bare tokens like a landlord's first name."""
    k = (kw or "").strip()
    if not k:
        return False
    if k.isdigit() and len(k) >= 6:
        return True                                   # PropertyGuru listing id
    if (re.fullmatch(r"[A-Za-z0-9]{6,15}", k) and any(c.isdigit() for c in k)
            and any(c.isalpha() for c in k)):
        return True                                   # 99.co code
    low = k.lower()
    if len(low) < 6:
        return False
    if any(c.isdigit() for c in low):
        return True
    words = set(re.findall(r"[a-z]+", low))
    if words & _KEYWORD_STREET_WORDS:
        return True
    addr_norm = _norm(address)
    if addr_norm and " " in low and low == addr_norm:
        return True                                   # the address's own estate/condo name
    return False

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

# ---------- A2: protected attribute gates need landlord provenance ----------
# A landlord-db free text field like "No Indian (landlord preference)" or "originally 'no
# Indian' but she has since accepted..." is Winfred's OWN paraphrase / an inferred
# observation -- not proof the landlord actually said it. Only two things count as the
# landlord's own dated statement: (1) a wa_evidence entry (an already dated, verbatim
# quoted chat line, added by the clarity audit) that is topically about the attribute, or
# (2) an inline quoted phrase WITH a date in the requirements text itself (e.g. LL104's
# gender field: 'Any (landlord confirmed "gender is ok", 19 Aug 2026)'). A bare date with
# no quote marks, or a quote with no date, is not enough.
_GATE_DATE_RE = re.compile(
    r"\[?\d{4}-\d{2}-\d{2}\]?|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{4}\b", re.I)
_GATE_QUOTE_RE = re.compile(r'["“]([^"”]{3,})["”]')
_WA_EVIDENCE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]\s*(.+)$")

_GATE_KEYWORDS = {
    "gender": ("male", "female", "gender", "guy", "girl", "man", "woman"),
    "ethnicity": ("chinese", "malay", "indian", "eurasian", "race", "ethnic"),
    "nationality": ("nationality", "foreigner", "singaporean", "malaysian", " pr ",
                    "permanent resident", "citizenship", "filipino"),
}

def _quoted_with_date(text):
    """An inline landlord quote: an actual quoted phrase AND a date in the SAME passage --
    a paraphrase ('landlord preference', 'stated 31 Aug 2026' with no quote marks) never
    has both. Returns (quote, date) or None."""
    if not text:
        return None
    qm = _GATE_QUOTE_RE.search(text)
    dm = _GATE_DATE_RE.search(text)
    if qm and dm:
        return qm.group(1).strip(), dm.group(0).strip("[]")
    return None

def _wa_evidence_quote(wa_evidence, attr):
    """A wa_evidence entry ('[DATE] verbatim chat line') topically about ATTR. wa_evidence
    is already a dated, verbatim quote by construction -- no inline quote marks needed."""
    kws = _GATE_KEYWORDS.get(attr, ())
    for entry in (wa_evidence or []):
        m = _WA_EVIDENCE_RE.match(str(entry).strip())
        if not m:
            continue
        date, txt = m.groups()
        if any(kw in txt.lower() for kw in kws):
            return txt.strip(), date
    return None

def landlord_gate_provenance(l, attr, req_field_text):
    """Returns {'quote':..., 'date':...} if the landlord record carries the landlord's OWN
    dated statement about ATTR ('gender', 'ethnicity' or 'nationality'), else None."""
    hit = _wa_evidence_quote(l.get("wa_evidence"), attr)
    if hit:
        return {"quote": hit[0], "date": hit[1]}
    hit = _quoted_with_date(req_field_text)
    if hit:
        return {"quote": hit[0], "date": hit[1]}
    # clarity.summary is Winfred's own audited paraphrase -- only counts if it ALSO happens
    # to carry an inline quote+date (rare; most summaries are narration, not verbatim).
    hit = _quoted_with_date(((l.get("clarity") or {}).get("summary")))
    if hit:
        return {"quote": hit[0], "date": hit[1]}
    return None

# ---------- A6: fail closed on EVERY existing entry, not just newly created ones ----------
# fill_missing_entry() above already downgrades an unsourced protected gate to gate_unverified
# for a brand NEW entry (a landlord with no prior index row). But an EXISTING entry -- created
# by new_entry()/main()'s own NEW-index-entry path, or hand entered, or generated by an older
# run of this script before A2 landed -- never went through that check at all: main()'s own
# reconcile loop only ever DIFF REPORTS an ethnicity/gender mismatch, it never inspects
# whether the gate has a landlord source. The reviewer found exactly this: cherryhill (LL097)
# carries ethnicity_rule exclude [Indian] with no source in the LIVE index, so it silently
# REDIRECTs a real Indian tenant today instead of flagging Winfred. This closes that gap for
# every entry, old or new, called from both main() (reconcile) and fill_missing_main() (so a
# freshly regenerated proposed index carries the fix too).

def _entry_gate_specs(req):
    """Which of THIS entry's protected attribute gates are fail closed shaped: ethnicity_rule
    / nationality_pref in exclude/only mode, or gender in female_only/male_only mode. A
    prefer-only gate is a soft nudge (never a hard REDIRECT/decline), so it stays out of
    scope here -- matches qualify()'s own split between hard and soft gender/ethnicity modes."""
    out = []
    eth = req.get("ethnicity_rule") or {}
    if eth.get("mode") in ("exclude", "only"):
        out.append(("ethnicity", eth, "ethnicity"))
    nat = req.get("nationality_pref") or {}
    if nat.get("mode") in ("exclude", "only"):
        out.append(("nationality", nat, "nationality"))
    if req.get("gender") in ("female_only", "male_only"):
        out.append(("gender", None, "gender"))
    return out

def _has_recorded_source(attr, req, gate_val):
    if attr == "gender":
        return bool(req.get("gender_source"))
    return bool((gate_val or {}).get("source"))

def enforce_gate_provenance(entries, db):
    """Fail closed pass over EVERY entry in ENTRIES (existing or brand new): any protected
    attribute gate shaped to hard reject (ethnicity_rule/nationality_pref exclude/only,
    gender female_only/male_only) that carries no landlord sourced quote gets that attribute
    added to the entry's requirements.gate_unverified -- the engine's own gate_unverified
    check (qualify() callers / _gate_unverified_offer_block in intake_engine.py) then refuses
    to silently REDIRECT or auto OFFER_VIEWING on it, flagging Winfred instead. Idempotent
    (an attribute already listed, or already carrying a recorded source, is left alone).
    Mutates the entries' requirements dicts in place; returns a list of human readable
    'listing_key: attr' change descriptions."""
    landlords = {l["id"]: l for l in db.get("landlords", [])}
    changes = []
    for e in entries:
        req = e.get("requirements")
        if not isinstance(req, dict):
            continue
        specs = _entry_gate_specs(req)
        if not specs:
            continue
        l = landlords.get(e.get("landlord_id")) or {}
        r = l.get("requirements") or {}
        gu = list(req.get("gate_unverified") or [])
        changed = False
        for attr, gate_val, field in specs:
            if attr in gu:
                continue
            if _has_recorded_source(attr, req, gate_val):
                continue
            prov = landlord_gate_provenance(l, attr, r.get(field))
            if prov:
                continue                      # a real landlord quote backs this gate -- leave it
            gu.append(attr)
            changed = True
            changes.append(f"{e.get('listing_key')}: {attr} gate has no landlord source -> gate_unverified")
        if changed:
            req["gate_unverified"] = gu
    return changes

def fill_missing_entry(l, pub_map, harvested, portal_map):
    r = l.get("requirements") or {}
    addr = l.get("full_address") or l.get("property_name") or ""
    lk = listing_key_for(l, pub_map)

    # A5: a landlord record carrying marketing_restrictions (e.g. LL017 -- never confirmed
    # a block/unit in writing, two conflicting guesses on file) must never surface an
    # address derived keyword -- portal ids / harvested ids stay (opaque, not an address).
    restricted = bool(l.get("marketing_restrictions"))
    kws = set() if restricted else set(_addr_variants(addr))
    kws |= portal_ids_for(l["id"], lk, portal_map)
    kws |= match_harvested(addr, harvested)
    kws = {k for k in kws if _is_valid_keyword(k, addr)}

    g = _SYNC.parse_gender(r.get("gender"))
    gender = g[0] if g else "any"
    couple_ok = bool(g[1]) if g else False
    couple_married = bool(g[2]) if g else False
    eth = _SYNC.parse_ethnicity(r.get("ethnicity")) or {"mode": "any", "list": []}
    nat = _SYNC.parse_ethnicity(r.get("nationality")) or {"mode": "any", "list": []}

    # A2: a protected attribute gate (gender / ethnicity_rule / nationality_pref) is only
    # ever emitted when the landlord record carries the landlord's OWN dated statement for
    # it -- otherwise it is downgraded to "any"/"any" and the entry is marked
    # gate_unverified so the engine (qualify()) refuses to silently REDIRECT or OFFER_VIEWING
    # on it. See landlord_gate_provenance() above.
    gate_unverified = []
    gender_source = None
    if gender != "any":
        prov = landlord_gate_provenance(l, "gender", r.get("gender"))
        if prov:
            gender_source = prov
        else:
            gate_unverified.append("gender")
            gender, couple_ok, couple_married = "any", False, False
    if eth.get("mode") != "any":
        prov = landlord_gate_provenance(l, "ethnicity", r.get("ethnicity"))
        if prov:
            eth = dict(eth); eth["source"] = prov
        else:
            gate_unverified.append("ethnicity")
            eth = {"mode": "any", "list": []}
    if nat.get("mode") != "any":
        prov = landlord_gate_provenance(l, "nationality", r.get("nationality"))
        if prov:
            nat = dict(nat); nat["source"] = prov
        else:
            gate_unverified.append("nationality")
            nat = {"mode": "any", "list": []}

    min_age = _SYNC.parse_min_age(r)
    max_pax = parse_pax(r.get("max_pax"))
    lease_min = parse_lease(r.get("lease_min")) or 12
    # budget_floor is DIFF REPORT ONLY territory for a reason: room-level pricing lives in
    # free text, and rent_min/rent_max on the landlord record are often a stale range across
    # several rooms (LL110: rent_min 1300 vs a current asking of 1000-1100). Only apply it
    # when the range has collapsed to a single confirmed figure -- never a guess off a range.
    rmin, rmax = l.get("rent_min"), l.get("rent_max")
    budget_floor = rmin if (isinstance(rmin, int) and isinstance(rmax, int) and rmin == rmax) else None

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
        "marketing_restrictions": l.get("marketing_restrictions") or "",
        "requirements": {
            "gender": gender, "couple_ok": couple_ok, "couple_must_be_married": couple_married,
            "gender_source": gender_source,
            "ethnicity_rule": eth, "nationality_pref": nat, "pass_type_allowed": [],
            "occupation_rule": {"mode": "any", "list": []},
            "max_pax": max_pax, "lease_min_months": lease_min, "lease_max_months": None,
            "budget_floor": budget_floor, "min_age": min_age,
            "cooking": "light", "pets_tenant_may_bring": False, "smoking": "any",
            "gate_unverified": gate_unverified,
            "notes_human": "GENERATED (fill-missing) from landlord-db " + l["id"],
        },
        "district": l.get("district", ""),
        "notes": "GENERATED (fill-missing) from landlord-db " + l["id"] + " on " + STAMP,
    }
    return entry, found, unknown

def _is_open(e):
    st = str(e.get("status", "")).lower()
    return not (st.startswith("closed") or st == "hold")

def _bare_kw(kwl):
    """Normalise a leading determiner off a keyword for cross-listing comparison ("the
    bayshore" -> "bayshore") -- a legacy hand-entered keyword carrying "the" must still be
    caught as a substring of another open listing's address the same way its bare form is."""
    return re.sub(r"^\s*the\s+", "", kwl)

def check_keyword_specificity(all_listings):
    """Every keyword of every OPEN listing checked against every OTHER entry's own address
    text -- OPEN or CLOSED (A4, Sep 2026). match_listing() scans the WHOLE index regardless
    of status, so a keyword surviving on a CLOSED row is still a live routing risk for an
    OPEN one -- the reviewer found "ang mo kio ave 3" on both a closed row and an open row.
    A closed-vs-closed pair is not checked (nobody routes a tenant onto a closed listing).
    Returns a list of (listing_key, keyword, other_listing_key) conflicts. A clean
    fill-missing run must return []; the test suite asserts exactly that."""
    conflicts = []
    for e in all_listings:
        if not _is_open(e):
            continue                          # only an OPEN entry's keyword is a live risk
        addr_self = _norm(e.get("block_address") or e.get("address") or e.get("property_name") or "")
        for kw in (e.get("pg_url_keywords") or []):
            kwl = _norm(kw)
            if not kwl or (kwl.isdigit() and len(kwl) >= 6):
                continue                      # portal ids are always specific
            kwl_bare = _bare_kw(kwl)
            for o in all_listings:
                if o is e:
                    continue
                addr_o = _norm(o.get("block_address") or o.get("address") or o.get("property_name") or "")
                if (addr_o and kwl != addr_self
                        and (kwl in addr_o or kwl_bare in addr_o)):
                    conflicts.append((e["listing_key"], kw, o["listing_key"]))
    return conflicts

def _dedupe_conflicting(new_entries, existing_open, all_entries=None):
    """Strip any keyword -- of a NEW entry OR an EXISTING open one -- that is a substring of
    ANY OTHER entry's own address, OPEN or CLOSED (A4, Sep 2026: match_listing() scans the
    whole index regardless of status, so a stale keyword left on a CLOSED row is still a
    live routing risk). Existing entries ARE mutated here (in place, via the same dict
    references idx["listings"] holds): a legacy bare keyword like "bayshore" or "the
    bayshore" on a long-standing manual entry starts stealing a brand new listing at the
    same estate ("66 Bayshore Rd") the moment that listing is created, so pruning only ever
    NEW entries would leave the older entry's blast radius live. A CLOSED entry's OWN
    keywords are never pruned here (nobody routes a tenant onto a closed listing on
    purpose) -- it is read only, as the "other side" of a collision."""
    pool = existing_open + new_entries
    others_pool = all_entries if all_entries is not None else pool
    for e in pool:
        others = [o for o in others_pool if o["listing_key"] != e["listing_key"]]
        keep = []
        for kw in e.get("pg_url_keywords", []):
            kwl = _norm(kw)
            if not kwl:
                continue
            if kwl.isdigit() and len(kwl) >= 6:
                keep.append(kw); continue     # portal id -- always specific, always kept
            kwl_bare = _bare_kw(kwl)
            addrs = [_norm(o.get("block_address") or o.get("address") or o.get("property_name") or "")
                     for o in others]
            if any(a and (kwl in a or kwl_bare in a) for a in addrs):
                continue                      # would cross match another open listing -> drop
            keep.append(kw)
        e["pg_url_keywords"] = sorted(set(keep))

def _norm_phone(p):
    return re.sub(r"\D", "", str(p or ""))

def fill_missing(idx, db, msg_db=None, pub_listings_path=None, portal_ids_path=None):
    """Returns (new_entries, report, backfilled) — report rows are
    (listing_key, address, keyword_count, status, gates_found, gates_unknown); backfilled
    rows are (existing_listing_key, old_landlord_id, new_landlord_id) for a landlord that
    already has an index entry under a DIFFERENT id, matched by phone (the same physical
    room re-entered under a new DB id -- e.g. LL088/Bayshore Park vs the long-standing
    manual "bayshore" entry filed under LL_JOHNNY_BP62, both +6593368817)."""
    existing_lids = {e.get("landlord_id") for e in idx["listings"] if e.get("landlord_id")}
    existing_by_phone = {}
    for e in idx["listings"]:
        ph = _norm_phone(e.get("landlord_phone"))
        if ph:
            existing_by_phone.setdefault(ph, []).append(e)
    pub_map = public_listing_key_map(pub_listings_path)
    harvested = harvest_portal_ids(msg_db=msg_db)
    portal_map = _load_portal_ids_file(portal_ids_path)

    new_entries, report, backfilled = [], [], []
    for l in db["landlords"]:
        if not _is_active_landlord(l):
            continue
        if l["id"] in existing_lids:
            continue
        ph = _norm_phone(l.get("phone"))
        dup_entries = existing_by_phone.get(ph) if ph else None
        if dup_entries:
            # same phone already carries an index entry under a different landlord_id --
            # this is the SAME room, not a second listing. Backfill the DB id onto the
            # existing entry (never create a duplicate) so future runs recognise it by id too.
            for e in dup_entries:
                if e.get("landlord_id") != l["id"]:
                    backfilled.append((e["listing_key"], e.get("landlord_id"), l["id"]))
                    e["landlord_id"] = l["id"]
            continue
        entry, found, unknown = fill_missing_entry(l, pub_map, harvested, portal_map)
        new_entries.append(entry)
        report.append((entry["listing_key"], entry["block_address"], len(entry["pg_url_keywords"]),
                       entry["status"], found, unknown))

    existing_open = [e for e in idx["listings"] if _is_open(e)]
    _dedupe_conflicting(new_entries, existing_open, all_entries=idx["listings"] + new_entries)
    return new_entries, report, backfilled

def fill_missing_main():
    APPLY = "--apply" in sys.argv
    idx = json.load(open(IDX))
    db = json.load(open(DB))
    n_active = sum(1 for l in db["landlords"] if _is_active_landlord(l))
    new_entries, report, backfilled = fill_missing(idx, db)
    print(f"== fill-missing: {len(new_entries)} new entries for {n_active} active/active-verify landlords "
          f"({n_active - len(new_entries)} already had an index entry) ==")
    for lk, addr, nkw, status, found, unknown in report:
        print(f"  {lk:50} | {addr[:38]:38} | kw={nkw:2} | {status:8} | found={found} unknown={unknown}")

    print("== backfilled landlord_id (same phone, pre-existing entry -- no duplicate created) ==")
    if backfilled:
        for lk, old_lid, new_lid in backfilled:
            print(f"  {lk}: landlord_id {old_lid} -> {new_lid}")
    else:
        print("  (none)")

    # A6: fail closed on EVERY entry (existing + new) whose protected attribute gate has no
    # landlord source -- not just the ones fill_missing_entry() creates fresh. See
    # enforce_gate_provenance() above (cherryhill/LL097 was the reviewer's live example: an
    # EXISTING entry, ethnicity_rule exclude with no source, never got gate_unverified).
    gate_changes = enforce_gate_provenance(idx["listings"] + new_entries, db)
    print("== gate provenance enforced (fail closed, applied) ==")
    if gate_changes:
        for c in gate_changes:
            print("  ", c)
    else:
        print("  (none)")

    conflicts = check_keyword_specificity(idx["listings"] + new_entries)
    if conflicts:
        print("\n== CROSS MATCH CONFLICTS (fatal -- nothing written) ==")
        for c in conflicts:
            print("  ", c)
        sys.exit("fill-missing: keyword specificity check failed, " + str(len(conflicts))
                 + " conflict(s) -- fix before writing the index")
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
        # Re-read under the lock (the outer idx/db may be stale by now) and recompute
        # fill_missing() against THIS read -- fill_missing mutates its idx argument in
        # place (phone backfill + _dedupe_conflicting keyword pruning), so recomputing
        # against idx_live is what makes those mutations land on the copy we are about
        # to write, instead of being silently discarded when idx_live replaced the
        # earlier (correctly mutated) idx wholesale.
        idx_live = json.load(open(IDX))
        db_live = json.load(open(DB))
        new_entries_live, _report_live, _backfilled_live = fill_missing(idx_live, db_live)
        enforce_gate_provenance(idx_live["listings"] + new_entries_live, db_live)
        conflicts_live = check_keyword_specificity(idx_live["listings"] + new_entries_live)
        if conflicts_live:
            sys.exit("fill-missing --apply: keyword specificity check failed against the "
                      "current on-disk index/db, " + str(len(conflicts_live))
                      + " conflict(s) -- nothing written; re-run without --apply to inspect")
        shutil.copy(IDX, IDX + ".bak-fillmissing-" + STAMP)
        idx_live["listings"] = idx_live["listings"] + new_entries_live
        tmp = IDX + ".tmp"
        json.dump(idx_live, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, IDX)
        print(f"\nWROTE: {IDX} (+{len(new_entries_live)} new entries)")
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

    # A6: fail closed on every entry's protected attribute gate, not just brand new ones --
    # see enforce_gate_provenance() above (cherryhill/LL097 was the reviewer's live example).
    gate_changes = enforce_gate_provenance(idx["listings"], db)
    applied.extend(gate_changes)

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
