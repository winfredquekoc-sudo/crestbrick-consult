#!/usr/bin/env python3
"""Export ONE compact JSON (schema v2) for the interactive matchmaker app: available
listings + still-looking tenants, with structured gates so the app can score matches
for ALL available listings. No blanks lost; PII stays local (gitignored output).

Pure-ish builder functions (build_listings, build_tenants, compute_*, apply_*) take
already-loaded data and do no file I/O themselves, so tests/matchmaker/test_export.py
can exercise them directly with fixtures. Only main() touches real paths.
"""
import json, os, re, sys, hashlib, datetime, importlib.util, sqlite3, statistics, time
import enrich
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # scripts/
import mrt_stations
_MRT_CACHE = mrt_stations._load()   # station coords (already geocoded); no network at import

ROOT = os.path.expanduser("~/crestbrick-consult")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "matchmaker-data.json")
PREV = os.path.join(HERE, "matchmaker-data.prev.json")
EXCLUSIONS_PATH = os.path.expanduser("~/.claude/state/matchmaker-exclusions.json")
FEE_WILLING_PATH = os.path.expanduser("~/.claude/state/tenant-fee-willing.json")
PRIORITY_PATH = os.path.expanduser("~/.claude/state/tenant-priority.json")  # hand kept, same {phones, ids} schema as fee willing (Winfred, 21 Aug 2026)
GEOCACHE_PATH = os.path.expanduser("~/.claude/state/matchmaker-geocache.json")

# Approximate postal-district centroids (fallback when geocoding is unavailable). Good to
# a neighbourhood, not a block — pins from this table are marked approx and drawn hollow.
DISTRICT_CENTROIDS = {
 "D1":(1.284,103.851),"D2":(1.276,103.846),"D3":(1.290,103.805),"D4":(1.271,103.820),
 "D5":(1.293,103.770),"D6":(1.290,103.850),"D7":(1.302,103.856),"D8":(1.311,103.856),
 "D9":(1.305,103.832),"D10":(1.315,103.807),"D11":(1.325,103.837),"D12":(1.327,103.855),
 "D13":(1.331,103.878),"D14":(1.318,103.890),"D15":(1.303,103.902),"D16":(1.323,103.930),
 "D17":(1.357,103.988),"D18":(1.352,103.944),"D19":(1.368,103.890),"D20":(1.360,103.840),
 "D21":(1.335,103.776),"D22":(1.339,103.707),"D23":(1.377,103.763),"D24":(1.398,103.700),
 "D25":(1.437,103.786),"D26":(1.398,103.823),"D27":(1.428,103.835),"D28":(1.397,103.873)}


def stage_photos(photos):
    """For each harvested photo, if photo_stage.py has written a "<n>_staged.jpg"
    sibling next to it (deploy/photos/LLxxx/n_staged.jpg), list the staged version
    FIRST so cards/galleries show it before the original, and return whether any
    staging was found so the caller can set photos_staged. Original stays in the
    list -- nothing here removes it, staging is additive."""
    if not photos:
        return photos, False
    out, staged = [], False
    for p in photos:
        if str(p).startswith("photos/"):
            base, ext = os.path.splitext(p)
            sib = "%s_staged%s" % (base, ext)
            if os.path.exists(os.path.join(HERE, "deploy", sib)):
                out.append(sib)
                staged = True
        out.append(p)
    return out, staged


def _load_geocache():
    try:
        with open(GEOCACHE_PATH) as f: return json.load(f)
    except (OSError, ValueError): return {}

_GEOCACHE = _load_geocache()
_GEOCACHE_DIRTY = False
_GEOCODE_BUDGET = 20   # max live lookups per export; the rest fall back to cache/centroid

def geocode(query, district):
    """(lat, lng, src) for a listing. Cache -> OneMap (budgeted, silent on failure) ->
    district centroid. src is 'exact' or 'approx' so the UI can draw approx pins hollow."""
    global _GEOCACHE_DIRTY, _GEOCODE_BUDGET
    key = (query or "").strip().lower()
    if key and key in _GEOCACHE:
        c = _GEOCACHE[key]
        if c: return c["lat"], c["lng"], "exact"
    elif key and _GEOCODE_BUDGET > 0:
        _GEOCODE_BUDGET -= 1
        import urllib.request, urllib.parse
        # raw DB addresses carry noise OneMap can't match ("(full addr withheld)", unit
        # numbers) — try the postal code first, then a de-noised street, then the raw text
        cands = []
        pm = re.search(r"[sS]?(\d{6})\b", key)
        if pm: cands.append(pm.group(1))
        street = re.sub(r"\(.*?\)|#\d+-\d+[a-z]?|\bs\d{6}\b|\bblk\b", " ", key)
        street = re.sub(r"[,;].*$", "", street).strip()
        if street and street not in cands: cands.append(street[:80])
        if key[:80] not in cands: cands.append(key[:80])
        hit = None
        for cand in cands:
            # OneMap throttles rapid-fire requests (observed: 2 hits then straight
            # refusals on 20 Aug 2026) — pace every call and retry once per candidate
            for attempt in (1, 2):
                try:
                    time.sleep(0.6)
                    u = ("https://www.onemap.gov.sg/api/common/elastic/search?returnGeom=Y"
                         "&getAddrDetails=N&searchVal=" + urllib.parse.quote(cand))
                    req = urllib.request.Request(u, headers={"User-Agent": "crestbrick-matchmaker/1.0"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        res = (json.load(r).get("results") or [])
                    if res:
                        hit = {"lat": float(res[0]["LATITUDE"]), "lng": float(res[0]["LONGITUDE"])}
                    break
                except Exception:
                    if attempt == 2: pass
            if hit: break
        _GEOCACHE[key] = hit    # negative-cache misses so they never re-query
        _GEOCACHE_DIRTY = True
        if hit:
            return hit["lat"], hit["lng"], "exact"
    lat, lng = DISTRICT_CENTROIDS.get(district or "", (1.352, 103.82))
    return lat, lng, "approx"

def _save_geocache():
    if not _GEOCACHE_DIRTY: return
    try:
        tmp = GEOCACHE_PATH + ".tmp"
        with open(tmp, "w") as f: json.dump(_GEOCACHE, f, indent=1)
        os.replace(tmp, GEOCACHE_PATH)
    except OSError: pass
SEEN_PATH = os.path.expanduser("~/.claude/state/matchmaker-seen.json")
LISTING_INDEX_PATH = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")
LISTINGS_JSON_PATH = os.path.join(ROOT, "public/listings.json")
WA_DB_PATH = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
BUSY_BLOCKS_PATH = os.path.expanduser("~/.claude/state/busy-blocks.json")


# ---------------------------------------------------------- field parsing --
def availability(l):
    # A closed status outranks offer_pending: the flag is set when an offer comes in
    # and is not always cleared once the unit closes, so checking it first
    # republishes let units as live inventory.
    st = (l.get("status") or "").lower()
    if st.startswith("closed (tenanted") or st.startswith("closed (unavailable"): return "Taken"
    if st.startswith("closed") or st.startswith("archived") or st.startswith("cold"): return "Off market"
    if l.get("offer_pending"): return "Offer pending"
    has_price = bool(l.get("rent_min") or l.get("rent_max"))
    cobroke = "co-broke" in (l.get("contact_label_source") or "").lower()
    if st in ("active", "channel", "active-verify") and (has_price or cobroke): return "Available"
    return "Pending"

def lifecycle(l):
    # [65] Coarse status bucket exported on EVERY landlord record (unlike availability()
    # below, which the app uses to filter its own listings[] down to Available/Offer
    # pending only for matching). Same closed-family-before-offer_pending priority as
    # availability() so the two never disagree about a record that is closed.
    st = (l.get("status") or "").lower()
    if st.startswith("closed (tenanted"): return "tenanted"
    if st.startswith("cold"): return "renewal_watch"
    if st.startswith("closed") or st.startswith("archived"): return "paused"  # incl. "closed (unavailable..."
    if l.get("offer_pending"): return "offer_pending"
    if st in ("active", "channel", "active-verify"): return "available"
    return "unknown"

def looking(t):
    ms = (t.get("match_status") or "").lower(); cs = (t.get("contact_state") or "").lower()
    stt = (t.get("status") or "").lower()
    # cs "found_place" is what derive_tenant_status_from_wa.py actually writes — the
    # bare "found" here matched nothing, so tenants who said "found a room already"
    # kept exporting as Still looking (31% of the roster by 21 Aug 2026).
    if (ms in ("found","in_deal") or cs in ("found","found_place")
        or stt=="tenanted" or stt.startswith("closed (tenanted")): return "Found"
    if (t.get("excluded") or cs in ("do_not_contact","not_interested")
        or ms in ("do_not_contact","excluded_india","excluded_family") or ms.startswith("stale")
        or stt=="excluded" or stt.startswith("closed") or stt=="archived"):
        return "Not looking"
    return "Still looking"

def num(v):
    if v is None or v == "": return None
    try: return int(float(str(v).replace(",","").replace("$","").strip()))
    except: return None

def parse_gender(txt):
    t = (txt or "").lower()
    if not t: return "any"
    if "female only" in t or "single female" in t or ("female" in t and "only" in t): return "female_only"
    if "male only" in t or ("male" in t and "only" in t and "female" not in t): return "male_only"
    if "female" in t and "pref" in t: return "female_pref"
    if "male" in t and "pref" in t: return "male_pref"
    if "female" in t: return "female_pref"
    if "male" in t: return "male_pref"
    return "any"

RACES = ["indian","chinese","malay","filipino","myanmar","burmese","korean","japanese","pakistani","caucasian","local"]
def parse_ethnicity(txt):
    t = (txt or "").lower()
    if not t or "no pref" in t or "no race" in t or "any" in t: return {"rule":"any","races":[]}
    found = [r for r in RACES if r in t]
    if "no " in t or "not " in t or "except" in t or "exclude" in t:
        excl = [r for r in RACES if re.search(r"no[t]?\s+"+r, t) or ("no "+r in t)]
        if excl: return {"rule":"exclude","races":excl}
    if "only" in t and found: return {"rule":"only","races":found}
    if "pref" in t and found: return {"rule":"prefer","races":found}
    if found: return {"rule":"prefer","races":found}
    return {"rule":"note","races":[], "raw":(txt or "")[:80]}

def maps_query(addr, district, dist_area):
    q = addr or dist_area.get(district, district or "")
    return (str(q).strip() + " Singapore") if q else ""


# ------------------------------------------------- portfolio view helpers --
# Supporting fields for the full-portfolio views (all_landlords/all_tenants/sales/
# revival/duplicate_phones) below, ported from the monolith lineage
# (matchmaker/crm-cloud-backend:scripts/matchmaker/export_data.py).

def src_of(l):
    return "co-broke" if "co-broke" in (l.get("contact_label_source") or "").lower() else "own"


# Condo/street-level names volunteered in tenant free text that are more specific
# than area-demand.json's broad neighbourhood keywords (e.g. "Cherryhill"/"Lorong
# Lew Lian" for Hougang) and would otherwise never match anything in
# build_area_keywords()'s coarser list. Kept separate from area-demand.json (a
# generated file) so it survives regeneration. Ground-truthed against this
# dataset's own records where one exists (Jalan Batu -> D15 per landlord LL089's
# own district field); Cherryhill/Lorong Lew Lian -> D19 per URA postal sector 53
# (Hougang) -- note some tenant free text says "Cherryhill (D20)", which is a
# DISAGREEING typed district in the SAME free text; infer_district() now trusts
# this ground-truthed place map over that typed figure (see infer_district()'s
# own docstring -- a prior version let the typed figure win, silently filing 4
# of 17 Cherryhill tenants into D20 where no Cherryhill listing could ever match
# them). "Building"/"street" level matches like this are also NARROWER than a
# genuine district-wide signal -- see the "known_place" vs "area_keyword" source
# tag below, consumed by build_live_area_demand()/build_zero_stock_alert() so a
# sourcing signal built entirely from one building's name doesn't read as
# district-wide demand.
KNOWN_PLACE_DISTRICTS = {
    "lorong lew lian": "D19",
    "cherryhill": "D19",
    "jalan batu": "D15",
    "haig road": "D15",
}

EXPLICIT_DISTRICT_RE = re.compile(r"\bd\s?-?\s?(\d{1,2})\b", re.I)


def build_area_keywords(dist_area):
    """[district, keyword] pairs sorted longest-keyword-first, so e.g. "bukit batok"
    matches before the shorter "batok" (and "lorong lew lian" before "cherryhill").
    Feeds infer_district()."""
    kws = []
    for d, area in dist_area.items():
        for kw in [k.strip().lower() for k in (area or "").split(",") if k.strip()]:
            kws.append((d, kw))
    for kw, d in KNOWN_PLACE_DISTRICTS.items():
        kws.append((d, kw))
    kws.sort(key=lambda x: -len(x[1]))
    return kws


def infer_district(explicit_district, preferred_districts, preferred_location, area_keywords):
    """District inference from free-text location -- ~46% of tenants have no
    structured district/preferred_districts at all, only a preferred_location
    string ("Bayshore / East", "Jalan Batu (Katong)"). Without this the roster
    sort degenerates to "no district" for nearly half the list, which is what
    looked like everyone lumped together. Match against each district's known
    area-name keywords as a best-effort.

    Returns (district, source, conflict):
      district -- the resolved code, or "" if nothing recognisable.
      source   -- how it was resolved: None (explicit_district/preferred_districts
                  were already given -- no inference happened), "preferred_districts",
                  "typed_in_text" (an explicit "(D##)"/"D##" in the free text),
                  "known_place" (KNOWN_PLACE_DISTRICTS -- a single named
                  building/street, e.g. Cherryhill or Jalan Batu, ground-truthed
                  to one district but BUILDING level, not district-wide demand),
                  or "area_keyword" (area-demand.json's own broad neighbourhood
                  keyword list -- genuinely district-level).
      conflict -- None, or a dict describing a KNOWN_PLACE_DISTRICTS ground truth
                  that disagreed with an explicit "(D##)" typed in the SAME free
                  text (e.g. "Cherryhill (D20)" when Cherryhill is actually D19).
                  The place name wins the district (it is ground-truthed against
                  this dataset's own records; a typed district in casual free
                  text is not -- see KNOWN_PLACE_DISTRICTS above), but the
                  override is never silent: conflict carries both values so a
                  reader can see exactly what was overridden and why.
    """
    if explicit_district: return explicit_district, None, None
    if preferred_districts: return preferred_districts[0], "preferred_districts", None
    loc = (preferred_location or "").lower()
    if not loc: return "", None, None

    keyword_district, keyword_kw = None, None
    for d, kw in area_keywords:
        if kw in loc:
            keyword_district, keyword_kw = d, kw
            break

    typed_district = None
    m = EXPLICIT_DISTRICT_RE.search(loc)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 28: typed_district = f"D{n}"

    if keyword_kw in KNOWN_PLACE_DISTRICTS and typed_district and typed_district != keyword_district:
        conflict = {"place": keyword_kw, "place_district": keyword_district, "typed_district": typed_district}
        return keyword_district, "known_place", conflict
    if typed_district:
        return typed_district, "typed_in_text", None
    if keyword_district:
        source = "known_place" if keyword_kw in KNOWN_PLACE_DISTRICTS else "area_keyword"
        return keyword_district, source, None
    return "", None, None


STATUS_ORDER = {"Available": 0, "Offer pending": 1, "Pending": 2, "Taken": 3, "Off market": 4}
TENANT_STATUS_ORDER = {"Still looking": 0, "Found": 1, "Not looking": 2}
SALE_STATUS_ORDER = {"Available": 0, "Pending": 1, "Closed": 2}


def sale_status(status_raw):
    st = (status_raw or "").lower()
    if st.startswith("closed") or st.startswith("archived"): return "Closed"
    if st in ("sale-active", "active"): return "Available"
    return "Pending"


# rent_min/rent_max are NOT usable for sale records: they hold mis-parsed artifacts
# from an upstream extraction bug (e.g. rent_min/max = 490/490 when the actual
# asking price is $505,000, per its own rooms_and_rent text). Parse the asking
# price from the free-text field instead; keep the raw text too since parsing SG
# price shorthand ("$505k", "505,000") from freeform notes is inherently best-effort.
def parse_price(txt):
    if not txt: return None
    nums = []
    for m in re.finditer(r"\$?\s?([\d,]{3,})\s*k\b", txt, re.I):
        nums.append(int(m.group(1).replace(",", "")) * 1000)
    for m in re.finditer(r"\$\s?([\d,]{5,})(?!\s*k)", txt):
        nums.append(int(m.group(1).replace(",", "")))
    return max(nums) if nums else None


def commission_est(rent_min, rent_max):
    # Rough proxy only (1 month's rent, the common SG co-broke convention) — actual
    # terms vary per listing (0.5mth/1yr, 1mth, 1% non-exclusive etc. are all seen in
    # follow_up notes) and aren't captured as a structured field. Label as an estimate.
    r = rent_max or rent_min
    return r or None


HANDED_OFF_MARKERS = ["handed to", "co-broke leads", "ziing", "stepped back"]
def handed_off(l):
    txt = ((l.get("follow_up") or "") + " " + (l.get("status") or "")).lower()
    return any(m in txt for m in HANDED_OFF_MARKERS)


_REQ_NOISE = {"tbc", "-", "n/a", "na", "none", "nil", "not stated", "unknown", "no info", "?"}
def _short(v, n=26):
    s = str(v or "").strip()
    return s[:n] if s else ""
def _clean(v, n=40):
    """Drop placeholder noise (TBC, N/A, blanks) so the list stays readable."""
    s = str(v or "").strip()
    if not s or s.lower() in _REQ_NOISE or s.lower().startswith("tbc"):
        return ""
    return s[:n]

def race_label(eth):
    """One-line race/ethnicity preference from a parsed ethnicity dict."""
    rule = (eth or {}).get("rule"); races = (eth or {}).get("races") or []
    if rule == "any": return "Any race"
    if rule == "exclude" and races: return "No " + "/".join(r.title() for r in races)
    if rule == "only" and races: return "/".join(r.title() for r in races) + " only"
    if rule == "prefer" and races: return "Prefers " + "/".join(r.title() for r in races)
    if rule == "note":
        raw = ((eth or {}).get("raw") or "").lower()
        if any(w in raw for w in ("no restriction", "no pref", "open to all", "any race", "all races")):
            return "Any race"
        return _short((eth or {}).get("raw"), 40)
    return ""

def gender_label(g):
    s = (g or "").strip().lower()
    if not s or s in ("any", "no", "none", "both", "-") or s in _REQ_NOISE: return ""
    if "female" in s or s == "f": return "Female only"
    if "male" in s or s == "m": return "Male only"
    return _short(g, 20)

def owner_stays_flag(raw):
    """Tri state Yes/No/unknown from the free text owner_on_site field. Landlords
    are typed in inconsistent prose ("Yes (owner + 2 children)", "No (co-living
    operator)", "TBC"), so this only reads the leading Yes/No token and throws
    away everything else — nobody downstream should ever see who else lives
    there, only whether the landlord does. Returns True, False, or None."""
    s = (raw or "").strip().lower()
    if s.startswith("yes"):
        return True
    if s.startswith("no"):
        return False
    return None


def req_details(req):
    """Full, uniform landlord requirement block for the landlord list + detail —
    same keys on listings and all_landlords so one renderer handles both. As much
    as the record holds (Winfred 27 Aug 2026: 'as detailed as possible')."""
    req = req or {}
    return {
        "race": race_label(parse_ethnicity(req.get("ethnicity"))),
        "gender": gender_label(req.get("gender")),
        "nationality": _clean(req.get("nationality"), 30),
        "max_pax": num(req.get("max_pax")),
        "lease_min": num(req.get("lease_min")) or num(req.get("lease_term")),
        "lease_max": num(req.get("lease_max")),
        "occupation": _clean(req.get("occupation"), 30),
        "pets": _clean(req.get("pets"), 24),
        "smoking": _clean(req.get("smoking"), 24),
        "owner_on_site": _clean(req.get("owner_on_site"), 20),
        "owner_stays": owner_stays_flag(req.get("owner_on_site")),
        "visitors": _clean(req.get("overnight_visitors") or req.get("visitors"), 24),
        "subletting": _clean(req.get("subletting"), 16),
        "utilities": _clean(req.get("utilities"), 40),
        "other": _short(req.get("other"), 240),
    }

def cooking_norm(raw):
    """Landlords type cooking rules very inconsistently ('light only', 'induction
    (no gas)', 'no cooking', 'tbc'). Collapse to one clear label; the raw text
    stays in req_raw for the detail. Light is checked first so 'light cooking
    allowed' reads as Light, not Allowed."""
    s = (raw or "").strip().lower()
    if not s or s.startswith("tbc") or "not stated" in s or s == "negotiable":
        return "Ask landlord"
    if any(w in s for w in ("light", "induction", "maggi", "boil", "airfryer",
                            "air fryer", "microwave", "simple", "no full", "no gas")):
        return "Light only"
    if "no cooking" in s or "not allowed" in s or "cooking not" in s or s == "no":
        return "Not allowed"
    if any(w in s for w in ("allowed", "yes", "ok", "permitted", "full cooking", "can cook")):
        return "Allowed"
    return "Ask landlord"


# ---- CEA register check for co-broke counterparties only ----------------------------
# Winfred, 13 Aug 2026: verify the agent by CONTACT NUMBER (CEA's own anti scam advice),
# and only for co-broke sources — an ordinary landlord is not a salesperson. Every lookup
# is cached in ~/.claude/state/cea-phone-cache.json so a number is hit once, not per build.
_CEA_MOD = None
def _cea():
    global _CEA_MOD
    if _CEA_MOD is None:
        p = os.path.expanduser("~/.claude/bin/cea-phone-check.py")
        spec = importlib.util.spec_from_file_location("cea_phone_check", p)
        _CEA_MOD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CEA_MOD)
    return _CEA_MOD

_COBROKE_AGENTS = None
def _cobroke_agent_phone(name):
    """Phone for a co-broke agent by name, from ~/.claude/state/cobroke-agents.json."""
    global _COBROKE_AGENTS
    if _COBROKE_AGENTS is None:
        try:
            with open(os.path.expanduser("~/.claude/state/cobroke-agents.json")) as f:
                _COBROKE_AGENTS = json.load(f).get("agents", [])
        except Exception:
            _COBROKE_AGENTS = []
    n = (name or "").strip().lower()
    if not n:
        return ""
    for a in _COBROKE_AGENTS:
        an = (a.get("name") or "").strip().lower()
        if an and (an == n or an.startswith(n) or n.startswith(an.split()[0])):
            return re.sub(r"\D", "", a.get("jid") or a.get("phone") or "")
    return ""

def cea_check(l, source):
    """Verify the CO-BROKE AGENT on a co-broke listing — never the landlord.

    contact_label_source reads like "co-broke listing (Denise), added 28 Jul 2026": the
    name in brackets is the counterpart agent, while the record's own phone belongs to the
    OWNER. Checking the record phone flagged two ordinary landlords as unregistered agents
    (Winfred, 13 Aug 2026). Never raises: an offline build degrades to 'unknown'.
    """
    if source != "co-broke":
        return None
    label = l.get("contact_label_source") or ""
    m = re.search(r"co-?broke[^()]*\(([^)]+)\)", label, re.I)
    agent = (m.group(1).strip() if m else "")
    if not agent:
        return {"status": "agent_unknown"}
    phone = _cobroke_agent_phone(agent)
    if not phone:
        return {"status": "agent_unknown", "agent": agent}
    try:
        matches, _ = _cea().lookup(phone)
    except Exception:
        return {"status": "unknown", "agent": agent}
    if not matches:
        return {"status": "not_registered", "agent": agent}
    r = matches[0]
    return {"status": "active" if r.get("active") else "expired", "agent": agent,
            "name": r.get("name", ""), "reg_no": r.get("reg_no", ""),
            "agency": r.get("agency", ""), "valid_until": r.get("valid_until", ""),
            "disciplinary": bool(r.get("disciplinary"))}


# -------------------------------------------------------------- state io --
class StateFileError(Exception):
    """A hand editable state file exists but cannot be trusted. Raised instead of
    silently defaulting: both files below are the ONLY copy of state nothing else
    can reconstruct, and both fail in a direction that looks like success."""


def _atomic_json_dump(path, obj, indent=1):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent)
    os.replace(tmp, path)


def load_exclusions_config(path):
    """Missing -> bootstrap a default and write it. Present but unreadable ->
    FAIL CLOSED, never overwrite. The old code caught the parse error and wrote
    the default straight over the user's hand edited file: every phone/id he had
    added was destroyed with no backup, the build still reported success, and
    the agents those entries exist to keep out of the tenant pipeline silently
    flowed back in as prospects. A hand edited file with a stray comma has to
    stop the build, not quietly empty itself."""
    if os.path.exists(path):
        try:
            cfg = json.load(open(path))
        except (OSError, ValueError) as e:
            raise StateFileError(
                f"{path} exists but is not readable JSON ({e}). Refusing to overwrite it or to "
                "build without the exclusions it holds — fix the file, or delete it to start fresh.")
        if not isinstance(cfg, dict):
            raise StateFileError(f"{path} must be a JSON object with phones/ids/name_markers, got {type(cfg).__name__}.")
        for key in ("phones", "ids", "name_markers"):
            if key in cfg and not isinstance(cfg[key], list):
                raise StateFileError(f"{path}: '{key}' must be a list, got {type(cfg[key]).__name__}.")
        return cfg
    default = {"phones": [], "ids": [], "name_markers": list(enrich.AGENT_MARKERS_DEFAULT)}
    _atomic_json_dump(path, default, indent=2)
    return default

def load_seen_registry(path):
    """Missing -> {} (first run). Present but unreadable -> FAIL CLOSED. Returning
    {} for a corrupt file re-stamped first_seen=today on EVERY listing, which
    zeroes days_listed, silently clears every reconfirm_due flag (the 14 day
    stale listing prompt just empties out), and then save_seen_registry writes
    that reset over the only record of when each unit was first seen."""
    if os.path.exists(path):
        try:
            reg = json.load(open(path))
        except (OSError, ValueError) as e:
            raise StateFileError(
                f"{path} exists but is not readable JSON ({e}). Refusing to reset every listing's "
                "first_seen date — fix the file, or delete it to start the age clock over.")
        if not isinstance(reg, dict):
            raise StateFileError(f"{path} must be a JSON object of id -> first seen date, got {type(reg).__name__}.")
        return reg
    return {}

def save_seen_registry(path, registry):
    _atomic_json_dump(path, registry)

def load_busy_blocks(path):
    # [68] Optional external calendar blocks file for a future batch viewing clash
    # check. We don't own or define its internal shape, so it is passed through
    # completely unvalidated -- dormant plumbing until a UI reads it. Missing or
    # unparseable -> None, never raises, never fails the build.
    if not path or not os.path.exists(path):
        return None
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        print(f"warning: {path} exists but is not valid JSON -- DATA.busy_blocks will be null")
        return None


# ------------------------------------------------------------- listings ---
def build_listings(landlords, dist_area, fixed_viewing_index, photo_url_index, seen_registry, today, harvested_photos=None):
    harvested_photos = harvested_photos or {}
    today_str = today.isoformat()
    out = []
    for l in landlords:
        av = availability(l)
        if av not in ("Available", "Offer pending"): continue
        r = l.get("requirements") or {}
        lid = l.get("id")
        rent_min = (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")), num(l.get("rent_max")))
        rent_max = (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")), num(l.get("rent_max")))
        source = src_of(l)
        listing_key = l.get("listing_key") or ""

        fseen = seen_registry.get(lid)
        if not fseen:
            fseen = today_str
            seen_registry[lid] = fseen
        try:
            days_listed = (today - datetime.date.fromisoformat(fseen)).days
        except ValueError:
            days_listed = 0

        confirmed_at = enrich.norm_date(l.get("viewing_availability_updated")) or enrich.norm_date(l.get("last_contact"))
        # >=14, not >14: every user facing label for this flag reads "14d+"
        # (app.js's reconfirm badges, this build's own digest heading) — that
        # copy means "at 14 days, it is due", so the code has to agree at the
        # boundary rather than only firing from day 15.
        if confirmed_at:
            days_since_confirmed = (today - datetime.date.fromisoformat(confirmed_at)).days
        else:
            days_since_confirmed = days_listed
        reconfirm_due = days_since_confirmed >= 14

        photo_info = photo_url_index.get((lid or "").upper(), {})
        _photos, _photos_staged = stage_photos(
            (harvested_photos.get((lid or "").upper()) or []) + (photo_info.get("photos") or []))

        _glat, _glng, _gsrc = geocode(l.get("full_address") or l.get("rooms_and_rent") or "",
                                      l.get("district") or "")
        out.append({
            "id": lid, "name": l.get("landlord_name"), "availability": av,
            "district": l.get("district") or "", "address": l.get("full_address") or "",
            "map_query": maps_query(l.get("full_address"), l.get("district"), dist_area),
            "lat": _glat, "lng": _glng, "geo_src": _gsrc,
            "mrt": mrt_stations.nearest_mrt(_glat, _glng, _MRT_CACHE) if _gsrc == "exact" else None,
            "rent_min": rent_min, "rent_max": rent_max,
            "viewing": l.get("viewing_availability") or "",
            "rooms": l.get("rooms_and_rent") or "", "property_type": l.get("property_type") or "",
            "phone": l.get("phone") or "",
            "source": source,
            "gates": {
                "max_pax": num(r.get("max_pax")),
                "lease_min": num(r.get("lease_min")) or num(r.get("lease_term")),
                "gender": parse_gender(r.get("gender")),
                "ethnicity": parse_ethnicity(r.get("ethnicity")),
                "occupation": r.get("occupation") or "", "cooking": r.get("cooking") or "",
                "pets": r.get("pets") or "", "smoking": r.get("smoking") or "",
            },
            "cooking": cooking_norm(r.get("cooking")),
            "reqs": req_details(r),
            "req_raw": {k: (v[:300] if isinstance(v, str) else v) for k, v in r.items() if v},
            "units": enrich.parse_units(l.get("rooms_and_rent"), l.get("property_type"), rent_min, rent_max),
            "available_from": enrich.find_available_from([l.get("follow_up"), l.get("rooms_and_rent")], today),
            # (73) listing_key/follow_up/confirmed_at deliberately NOT exported as of
            # cycle 7: zero reads in app.js/scoring.js/template.html/build.py and no
            # dedicated test asserts their value (only generic required-keys
            # membership, updated alongside this cut) — the LOCAL variables above
            # (listing_key, confirmed_at) still exist and still feed fixed_viewing /
            # reconfirm_due, which the UI does read; only the redundant verbatim
            # pass-through into the shipped payload was removed. ~4.1KB across 14
            # listings. Contrast with first_seen/intake_complete (kept — each has
            # its own dedicated correctness test) and is_agent_suspect (kept —
            # looks like a dormant agent-exclusion signal, not dead code).
            "fixed_viewing": fixed_viewing_index.get(listing_key),
            # Landlord's own WhatsApp room photos (locally vetted, NRIC/docs
            # filtered out) come FIRST, then any website listing photos. Staged
            # versions (photo_stage.py) are listed ahead of their originals.
            "photos": _photos or None,
            "photos_staged": _photos_staged,
            "listing_url": photo_info.get("listing_url"),
            "first_seen": fseen, "days_listed": days_listed,
            "is_cobroke": source == "co-broke",
            "dup_of": None,
            "reconfirm_due": reconfirm_due,
            "days_since_confirmed": days_since_confirmed,
            "lifecycle": lifecycle(l),
        })
    return out

def apply_listing_dup_of(listings):
    """Same normalized address + overlapping rent -> later entries point dup_of at
    the first one encountered (array order, i.e. landlord-db order — no listed_at
    timestamp exists yet to establish true chronology)."""
    groups = {}
    for l in listings:
        key = enrich.normalize_address(l["address"])
        if not key: continue  # never group listings with a blank address together
        groups.setdefault(key, []).append(l)
    count = 0
    for group in groups.values():
        if len(group) < 2: continue
        base = group[0]
        for other in group[1:]:
            if enrich.rent_overlaps(base.get("rent_min"), base.get("rent_max"), other.get("rent_min"), other.get("rent_max")):
                other["dup_of"] = base["id"]
                count += 1
    return count


# [idea 28] "available" is only trustworthy as of the landlord's last confirmation
# -- build_listings() already computes days_since_confirmed/reconfirm_due per
# listing; this just selects and ranks the overdue ones into their own list so
# the UI doesn't have to filter listings[] itself before offering a room.
def build_stale_landlord_chase(listings):
    rows = [l for l in listings if l.get("reconfirm_due")]
    rows.sort(key=lambda l: -(l.get("days_since_confirmed") or 0))
    return [{"id": l["id"], "name": l["name"], "district": l["district"], "phone": l["phone"],
             "days_since_confirmed": l["days_since_confirmed"]} for l in rows]


def build_supply_overview(landlords):
    # [65] Every landlord record regardless of status, reduced to the fields a
    # portfolio wide "supply view" needs (see build.py's digest generator). The
    # app's own listings[] above stays filtered to Available/Offer pending only
    # (unchanged contract -- the app still filters to available+offer_pending
    # for matching) so this is a purely additive top level field, not a
    # replacement, and carries no risk to existing matching/worklist logic.
    out = []
    for l in landlords:
        rent_min = (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")), num(l.get("rent_max")))
        rent_max = (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")), num(l.get("rent_max")))
        out.append({
            "id": l.get("id"), "name": l.get("landlord_name"),
            "district": l.get("district") or "",
            "lifecycle": lifecycle(l), "availability": availability(l),
            "rent_min": rent_min, "rent_max": rent_max,
        })
    return out


def build_all_landlords(landlords, dist_area, area_keywords, harvested_photos=None):
    harvested_photos = harvested_photos or {}
    """Full landlord portfolio view (every status, unlike build_listings()/
    build_supply_overview() which have their own narrower purposes) for the app's
    roster screen. Ported from the monolith lineage."""
    out = []
    for l in landlords:
        av = availability(l)
        pd, _pd_source, _pd_conflict = infer_district(l.get("district") or "", [], l.get("full_address") or "", area_keywords)
        rent_min = (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")), num(l.get("rent_max")))
        rent_max = (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")), num(l.get("rent_max")))
        source = src_of(l)
        address = l.get("full_address") or ""
        mq = maps_query(l.get("full_address"), l.get("district"), dist_area)
        follow_up = l.get("follow_up") or ""
        # Closed rows keep only a short note stub in the roster — the full
        # history stays in the source DB. Token-slim, 22 Aug 2026.
        if av in ("Taken", "Off market") and len(follow_up) > 200:
            follow_up = follow_up[:200]
        row = {
            "id": l.get("id"), "name": l.get("landlord_name"), "availability": av,
            "status_raw": l.get("status") or "", "sort": STATUS_ORDER.get(av, 9),
            "district": l.get("district") or "", "primary_district": pd,
            "address": address,
            "rent_min": rent_min, "rent_max": rent_max,
            "viewing": l.get("viewing_availability") or "",
            "rooms": l.get("rooms_and_rent") or "", "property_type": l.get("property_type") or "",
            "phone": l.get("phone") or "", "last_contact": l.get("last_contact") or "",
            "follow_up": follow_up, "lifecycle": lifecycle(l),
            "source": source, "cea": cea_check(l, source),
            "commission_est": commission_est(num(l.get("rent_min")), num(l.get("rent_max"))),
            "handed_off": handed_off(l),
            "cooking": cooking_norm((l.get("requirements") or {}).get("cooking")),
            "reqs": req_details(l.get("requirements")),
        }
        row["photos"], row["photos_staged"] = stage_photos(harvested_photos.get((l.get("id") or "").upper()) or [])
        row["photos"] = row["photos"] or None
        # map_query only when it adds something over the plain address the app
        # already falls back to (mapLink uses map_query || address). Token-slim.
        if mq and mq != address:
            row["map_query"] = mq
        out.append(row)
    out.sort(key=lambda r: (r["sort"], r["primary_district"] or "zzz", r["name"] or ""))
    return out


# Phrases that signal the tenant is stating a HIGHER ceiling than their intake
# budget, in their own words -- kept intentionally narrow. A bare "$1,200" mention
# with none of these phrases is never enough on its own (could be a listing price
# someone quoted them, a unit number, a friend's budget); see
# find_budget_contradiction() for the full evidence requirement.
BUDGET_STRETCH_RE = re.compile(
    r"(?:can (?:go|stretch|increase|pay|afford)|willing to (?:go|pay)|budget (?:can|could) go|"
    r"max(?:imum)?(?: budget)?(?: is| of)?)\s*(?:up to|to)?\s*\$?\s?(\d[\d,]*\.?\d*\s?k?)\b",
    re.I)
BUDGET_CONTRADICTION_MIN_RATIO = 1.05  # mentioned $ must clear stated budget by >=5%...
BUDGET_CONTRADICTION_MIN_DELTA = 50    # ...AND by >=$50, so a same-figure restatement in a
                                        # filled-in intake form ("budget 870" vs their own
                                        # "Budget:max 900") reads as rounding, not a real signal.


def find_budget_contradiction(conn, jid, stated_budget):
    """Scan this tenant's own inbound WA messages for a self-stated higher budget
    ceiling than their intake record shows. Requires BOTH a recognisable phrase
    (BUDGET_STRETCH_RE) AND a parsed $ figure meaningfully above stated_budget --
    never just a bare number. No match, no bridge, or no stated_budget to compare
    against -> None, never a guess. conn may be None (bridge unavailable/closed);
    never raises."""
    if not conn or not jid or stated_budget is None:
        return None
    try:
        rows = conn.execute(
            "SELECT content FROM messages WHERE chat_jid=? AND is_from_me=0 "
            "AND content IS NOT NULL AND content!='' ORDER BY timestamp DESC LIMIT 200",
            (jid,)).fetchall()
    except sqlite3.Error:
        return None
    for (content,) in rows:
        if not content:
            continue
        m = BUDGET_STRETCH_RE.search(content)
        if not m:
            continue
        n = enrich._tok_to_num(m.group(1))
        if n is None:
            continue
        if n > stated_budget * BUDGET_CONTRADICTION_MIN_RATIO and (n - stated_budget) >= BUDGET_CONTRADICTION_MIN_DELTA:
            return {"quote": content.strip()[:200], "stated_budget": stated_budget, "mentioned": n}
    return None


# -------------------------------------------------------------- tenants ---
# --- URGENT segment (Winfred, 20 Aug 2026) -------------------------------------
# Two independent signals, both required:
#   (a) they gave us real information  -> >=10 of the 14 intake fields present
#   (b) they said they will pay the agent fee -> tenant-fee-willing.json
# (b) lives in its own state file rather than tenant-db.json because that DB is
# rebuilt from WhatsApp nightly and hand-added fields do not survive the rebuild.
# Nothing populates (b) automatically: the intake form has never asked, so every
# entry is Winfred marking it after the fact.
_PROFILE_FIELDS = ("name","nationality","ethnicity","gender","age","pass_type","occupation",
                   "employment_type","no_of_pax","move_in_date","lease_term_months","budget",
                   "preferred_location","email")
INFO_RICH_MIN = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")))["info_rich_min"]

def load_fee_willing(path):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return set(), set()
    except (OSError, ValueError) as e:
        raise SystemExit("export_data.py: %s is unreadable (%s). Fix or delete it — refusing to "
                         "build a roster that silently drops the URGENT segment." % (path, e))
    return ({enrich.normalize_phone(p) for p in (cfg.get("phones") or [])},
            set(cfg.get("ids") or []))

# A message must clear the positive pattern AND not trip the negative one --
# "can you waive the agent fee" and "no agent fee right?" are the OPPOSITE
# signal and are exactly how these phrases usually appear in chats.
FEE_POS_RE = re.compile(
    r"(willing\s+to\s+pay.{0,24}(?:fee|commission)"
    r"|can\s+pay.{0,18}(?:agent\s*)?(?:fee|commission)"
    r"|(?:ok|okay|fine|no\s+problem|alright)\s+(?:with\s+)?(?:paying\s+)?(?:the\s+)?agent\s*fee"
    r"|agent\s*fee\s+is\s+(?:ok|okay|fine|no\s+problem)"
    r"|i(?:'|’)?ll\s+pay\s+(?:the\s+)?(?:agent\s*)?(?:fee|commission)"
    r"|愿意付中介|可以付中介|中介费没问题|包中介费)",
    re.I,
)
FEE_NEG_RE = re.compile(
    r"(no\s+agent\s*fee|without\s+(?:agent\s*)?fee|waive.{0,12}fee"
    r"|(?:don.?t|won.?t|not|cannot|can.?t|unable\s+to)\s+(?:want\s+to\s+)?pay"
    r"|fee\s*free|zero\s+(?:agent\s*)?fee|不付中介|免中介)",
    re.I,
)

def detect_fee_willing_from_chats(wa_conn, tenants_raw, today, path=FEE_WILLING_PATH):
    """Scan each still-looking tenant's recent inbound for an explicit
    willing-to-pay-agent-fee statement and record the ids into the fee-willing
    state file (the URGENT segment's input). Additive only: manual entries are
    never touched, an id is never removed, and any failure leaves the file as
    it was -- the segment must not flap on a scan bug."""
    if wa_conn is None:
        return 0
    try:
        try:
            cfg = json.load(open(path))
        except (OSError, ValueError):
            cfg = {}
        ids = set(cfg.get("ids") or [])
        detected = cfg.get("chat_detected") or {}
        added = 0
        for t in tenants_raw:
            tid, jid = t.get("id"), (t.get("jid") or "").strip()
            if not tid or not jid or tid in ids:
                continue
            if looking(t) != "Still looking":
                continue
            try:
                rows = wa_conn.execute(
                    "SELECT content FROM messages WHERE chat_jid=? AND is_from_me=0 "
                    "AND content IS NOT NULL AND content!='' ORDER BY timestamp DESC LIMIT 40",
                    (jid,)).fetchall()
            except Exception:
                continue
            for (c,) in rows:
                if FEE_POS_RE.search(c) and not FEE_NEG_RE.search(c):
                    ids.add(tid)
                    detected[tid] = {"date": today.isoformat(), "snippet": c[:120]}
                    added += 1
                    break
        if added:
            cfg["ids"] = sorted(ids)
            cfg["chat_detected"] = detected
            cfg.setdefault("phones", cfg.get("phones") or [])
            tmp = path + ".tmp"
            json.dump(cfg, open(tmp, "w"), indent=1, ensure_ascii=False)
            os.replace(tmp, path)
        return added
    except Exception as e:
        print("warning: fee-willing chat scan failed (%s) — URGENT segment uses the file as is" % e)
        return 0

def profile_filled(t):
    return sum(1 for f in _PROFILE_FIELDS if str(t.get(f) or "").strip())

def tenant_segment(t, filled, fee_willing):
    if fee_willing and filled >= INFO_RICH_MIN:
        return "URGENT"
    if fee_willing:
        return "FEE WILLING"
    if filled >= INFO_RICH_MIN:
        return "INFO RICH"
    return ""

# Persona tag (Winfred 27 Aug 2026): a plain who-is-this-tenant label, separate
# from `segment` (which is sales readiness). Lets the roster be sorted and the
# message worded by type. Best effort from pass type / occupation / group size.
_PROFESSIONAL_WORDS = ("engineer", "manager", "analyst", "consultant", "doctor",
                       "nurse", "teacher", "executive", "developer", "accountant",
                       "lawyer", "architect", "designer", "banker", "professional")
def tenant_persona(t):
    pt = (t.get("pass_type") or "").lower()
    occ = (t.get("occupation") or "").lower()
    try:
        paxn = int(str(t.get("pax")).strip())
    except (TypeError, ValueError):
        paxn = None
    if "student" in pt or "student" in occ:
        return "student"
    if paxn is not None and paxn >= 3:
        return "family"
    if "ep" in pt or "employment pass" in pt or "s pass" in pt or "spass" in pt:
        return "professional"
    if any(w in occ for w in _PROFESSIONAL_WORDS):
        return "professional"
    if "work permit" in pt or pt == "wp":
        return "work permit"
    if paxn == 2:
        return "couple or pair"
    return ""

def build_tenants(tenants_raw, exclusions_cfg, wa_conn, today, area_keywords):
    out = []
    fee_phones, fee_ids = load_fee_willing(FEE_WILLING_PATH)
    prio_phones, prio_ids = load_fee_willing(PRIORITY_PATH)
    excl_counts = {"db": 0, "config_phone": 0, "config_id": 0, "config_name_marker": 0}
    phones_cfg = {enrich.normalize_phone(p) for p in (exclusions_cfg.get("phones") or [])}
    ids_cfg = set(exclusions_cfg.get("ids") or [])
    markers_cfg = exclusions_cfg.get("name_markers") or []

    for t in tenants_raw:
        if looking(t) != "Still looking":
            # Priority override (Winfred, 21 Aug 2026): a tenant on the hand kept
            # priority list stays in the pool even when the blanket excluded_india
            # nationality rule would drop them (first case: TN615 Vivek, EP, viewing
            # booked). Priority NEVER overrides found / do_not_contact / stale /
            # closed — only the nationality exclusion.
            _ms = (t.get("match_status") or "").lower()
            _prio_hit = (enrich.normalize_phone(t.get("phone")) in prio_phones) or (t.get("id") in prio_ids)
            if not (_prio_hit and _ms == "excluded_india" and looking({**t, "match_status": "active"}) == "Still looking"):
                if t.get("excluded"):
                    excl_counts["db"] += 1
                continue

        phone_norm = enrich.normalize_phone(t.get("phone"))
        name = t.get("name") or ""
        name_l = name.lower()
        if phone_norm and phone_norm in phones_cfg:
            excl_counts["config_phone"] += 1; continue
        if t.get("id") in ids_cfg:
            excl_counts["config_id"] += 1; continue
        if any(mk and re.search(r"\b" + re.escape(mk.lower()) + r"\b", name_l) for mk in markers_cfg):
            excl_counts["config_name_marker"] += 1; continue

        listing_enquired = t.get("listing_enquired") or ""
        budget = num(t.get("budget")); budget_min = num(t.get("budget_min")); budget_max = num(t.get("budget_max"))
        budget_note = None
        if budget is None and budget_min is None and budget_max is None:
            # listing_enquired is the only free text field observed to reliably carry
            # a $ figure (e.g. "813 Jellicoe Road Room S$1,200/mo"); other free text
            # fields (preferred_location, status) contain block/unit numbers that would
            # false-positive as a budget, so they are deliberately not scanned here.
            rb_min, rb_max, rb_note = enrich.recover_budget([listing_enquired])
            if rb_note:
                budget_min, budget_max, budget_note = rb_min, rb_max, rb_note

        raw_move_in = t.get("move_in_date") or ""
        move_in = enrich.norm_date(raw_move_in) or raw_move_in
        # move_in stays the tenant's verbatim text for display; move_in_norm is
        # export time best effort YYYY-MM-DD for scoring/urgency (handles free
        # text like "Immediately"/"asap"/"early Sep 2026"/"2026-08"/"mid Aug"
        # that neither this file's norm_date() nor scoring.js's parseDate() can
        # read). None when nothing recognisable -- scoring then falls back to
        # today's existing default behavior exactly as before this field existed.
        move_in_norm = enrich.norm_move_in(raw_move_in, today)
        raw_last_contact = t.get("last_contact") or ""
        last_contact = enrich.norm_date(raw_last_contact) or raw_last_contact
        pax = num(t.get("no_of_pax")); lease_months = num(t.get("lease_term_months"))
        preferred_location = t.get("preferred_location") or ""
        preferred_districts = ([str(x).strip() for x in t["preferred_districts"] if str(x).strip()]
                                if isinstance(t.get("preferred_districts"), list)
                                else [d.strip() for d in (t.get("preferred_districts") or "").split(",") if d.strip()])
        # ~113 of 218 still-looking tenants have a blank district field but DO name a
        # place in preferred_location free text ("Cherryhill (Lorong Lew Lian)",
        # "Haig Road area") -- infer_district() already exists for this (built for the
        # portfolio-view "primary_district" field) but was never applied to the
        # "district" field this app actually gates/matches on. district_inferred flags
        # which rows came from inference so the UI can badge them distinctly from a
        # tenant-stated district. district_source/district_conflict carry infer_district()'s
        # own provenance through -- see its docstring: "known_place" rows are one named
        # building (e.g. all 17 "Cherryhill" tenants), not genuine district-wide demand,
        # and a conflict means a KNOWN_PLACE_DISTRICTS ground truth overrode a disagreeing
        # typed "(D##)" in the tenant's own free text.
        district_raw = t.get("district") or ""
        district = district_raw
        district_inferred = False
        district_source = None
        district_conflict = None
        if not district:
            inferred, district_source, district_conflict = infer_district(
                "", preferred_districts, preferred_location, area_keywords)
            if inferred:
                district = inferred
                district_inferred = True
        gender = t.get("gender") or ""; nationality = t.get("nationality") or ""; pass_type = t.get("pass_type") or ""
        occupation = t.get("occupation") or ""

        missing = []
        if budget is None and budget_min is None and budget_max is None: missing.append("budget")
        if not move_in: missing.append("move_in")
        if pax is None: missing.append("pax")
        if lease_months is None: missing.append("lease_months")
        if not district: missing.append("district")

        # spec's literal named checklist (budget-or-min/max counts once); see report
        # for the "11 of 14" arithmetic note in the source spec vs this 10 item list.
        intake_fields = [name, gender, nationality, pass_type, occupation, pax, move_in,
                          lease_months, (budget if budget is not None else (budget_min if budget_min is not None else budget_max)),
                          preferred_location]
        intake_complete = all(v is not None and v != "" for v in intake_fields)

        jid = t.get("jid") or ""
        last_wa, lang = enrich.fetch_wa_info(wa_conn, jid)
        budget_contradiction = find_budget_contradiction(
            wa_conn, jid, budget if budget is not None else budget_max)

        # (73) status/listing_enquired/budget_note deliberately NOT exported as of
        # cycle 7: zero reads in app.js/scoring.js/template.html/build.py and no
        # dedicated test asserts their value (only generic required-keys
        # membership, updated alongside this cut) — ~10.8KB across 140 tenants.
        # listing_enquired's own INPUT use above (recover_budget) is untouched;
        # only the redundant verbatim pass-through into the shipped payload goes.
        # Contrast with intake_complete (kept — has its own dedicated correctness
        # test) and is_agent_suspect (kept — looks like a dormant agent-exclusion
        # signal per standing agent-exclusion policy, not dead code).
        _filled = profile_filled(t)
        _fee = (phone_norm in fee_phones) or (t.get("id") in fee_ids)
        _prio = (phone_norm in prio_phones) or (t.get("id") in prio_ids)
        out.append({
            "id": t.get("id"), "name": name,
            "profile_filled": _filled, "profile_total": len(_PROFILE_FIELDS),
            "pays_agent_fee": bool(_fee),
            "pinned": bool(_prio),
            "segment": tenant_segment(t, _filled, _fee),
            "persona": tenant_persona(t),
            "preferred_location": preferred_location,
            "preferred_districts": preferred_districts,
            "district": district, "district_inferred": district_inferred,
            "district_source": district_source, "district_conflict": district_conflict,
            "budget": budget, "budget_min": budget_min, "budget_max": budget_max,
            "budget_contradiction": budget_contradiction,
            "pax": pax, "gender": gender, "ethnicity": t.get("ethnicity") or "",
            "nationality": nationality, "pass_type": pass_type,
            "occupation": occupation, "move_in": move_in, "move_in_norm": move_in_norm,
            "lease_months": lease_months, "phone": t.get("phone") or "",
            "last_contact": last_contact,
            "last_wa": last_wa, "lang": lang,
            "dup_group": None, "missing": missing,
            "intake_complete": intake_complete, "work_anchor": None,
            "is_agent_suspect": enrich.is_agent_suspect(name),
        })
    return out, excl_counts

def apply_tenant_dup_groups(tenants):
    by_phone = {}
    for t in tenants:
        p = enrich.normalize_phone(t.get("phone"))
        if p: by_phone.setdefault(p, []).append(t)
    gid = 0
    for group in by_phone.values():
        if len(group) < 2: continue
        gid += 1
        for t in group: t["dup_group"] = gid
    return gid


def build_all_tenants(tenants_raw, area_keywords):
    """Full tenant portfolio view (every status, unlike build_tenants() above which
    filters to still-looking only) for the app's roster screen. Ported from the
    monolith lineage. district/location is the PRIMARY sort grouping (Winfred:
    tenants were "all lumped together" under status alone) — status and
    data-completeness are secondary within each area group. Unmatched location
    falls into a final "zzz" bucket the UI labels "Unspecified location"."""
    out = []
    for t in tenants_raw:
        lk = looking(t)
        budget = num(t.get("budget")) or num(t.get("budget_max"))
        raw_pd = ([str(x).strip() for x in t["preferred_districts"] if str(x).strip()]
                  if isinstance(t.get("preferred_districts"), list)
                  else [d.strip() for d in (t.get("preferred_districts") or "").split(",") if d.strip()])
        pd, _pd_source, _pd_conflict = infer_district(t.get("district") or "", raw_pd, t.get("preferred_location") or "", area_keywords)
        # Not-looking tenants (found a place / no longer looking / closed) are
        # kept as slim tombstones: the roster still groups + shows them and the
        # id->name/phone fallback lookups still resolve, but the heavy detail
        # (budget, pax, occupation, move-in, enquiry, missing-flags) is dropped
        # since you never re-qualify a closed lead. Token-slim, 22 Aug 2026 —
        # this was ~144 KB, the bulk of the payload. Still-looking rows are full.
        if lk != "Still looking":
            out.append({
                "id": t.get("id"), "name": t.get("name"), "looking": lk,
                "status_raw": t.get("status") or "", "sort": TENANT_STATUS_ORDER.get(lk, 9),
                "district": t.get("district") or "", "primary_district": pd,
                "phone": t.get("phone") or "", "last_contact": t.get("last_contact") or "",
            })
            continue
        missing = []
        if not budget: missing.append("budget")
        if not t.get("move_in_date"): missing.append("move-in")
        if not num(t.get("no_of_pax")): missing.append("pax")
        if not num(t.get("lease_term_months")): missing.append("lease")
        if not pd: missing.append("district")
        out.append({
            "id": t.get("id"), "name": t.get("name"), "looking": lk,
            "status_raw": t.get("status") or "", "sort": TENANT_STATUS_ORDER.get(lk, 9),
            "preferred_location": t.get("preferred_location") or "", "district": t.get("district") or "",
            "primary_district": pd,
            "budget": budget, "pax": num(t.get("no_of_pax")), "gender": t.get("gender") or "",
            "nationality": t.get("nationality") or "", "occupation": t.get("occupation") or "",
            "move_in": t.get("move_in_date") or "", "lease_months": num(t.get("lease_term_months")),
            "phone": t.get("phone") or "", "last_contact": t.get("last_contact") or "",
            "listing_enquired": t.get("listing_enquired") or "", "missing": missing,
        })
    out.sort(key=lambda r: (r["primary_district"] or "zzz", r["sort"], len(r.get("missing") or []) == 0, r["name"] or ""))
    return out


# --------------------------------------------------------------- sales ----
def build_sales(landlords, dist_area, area_keywords):
    """Separate track from rentals (deal_type: sale / sale-or-rent). Ported from
    the monolith lineage."""
    out = []
    for l in landlords:
        if (l.get("deal_type") or "") not in ("sale", "sale-or-rent"): continue
        txt = l.get("rooms_and_rent") or ""
        ss = sale_status(l.get("status"))
        pd, _pd_source, _pd_conflict = infer_district(l.get("district") or "", [], l.get("full_address") or "", area_keywords)
        source = src_of(l)
        out.append({
            "id": l.get("id"), "name": l.get("landlord_name"), "sale_status": ss,
            "sort": SALE_STATUS_ORDER.get(ss, 9), "status_raw": l.get("status") or "",
            "district": l.get("district") or "", "primary_district": pd,
            "address": l.get("full_address") or "",
            "map_query": maps_query(l.get("full_address"), l.get("district"), dist_area),
            "asking_price": parse_price(txt), "price_text": txt,
            "property_type": l.get("property_type") or "", "phone": l.get("phone") or "",
            "last_contact": l.get("last_contact") or "", "follow_up": l.get("follow_up") or "",
            "source": source, "cea": cea_check(l, source),
        })
    out.sort(key=lambda r: (r["sort"], r["primary_district"] or "zzz", r["name"] or ""))
    return out


# ---------------------------------------------------- duplicate phones ----
def compute_duplicate_phones(all_landlords, tenants_raw):
    """Same number saved as more than one landlord/tenant record — almost always a
    data-entry collision (dup contact, or a landlord who is also a tenant
    elsewhere) worth a human glance rather than silently treated as two separate
    people. Ported from the monolith lineage. Deliberately compares raw phone
    strings as stored (not normalized) to match records the way they were entered."""
    owners = {}
    for l in all_landlords:
        if l["phone"]: owners.setdefault(l["phone"], []).append("LL:" + (l["name"] or l["id"]))
    for t in tenants_raw:
        p = t.get("phone") or ""
        if p: owners.setdefault(p, []).append("TN:" + (t.get("name") or t.get("id")))
    return [{"phone": p, "owners": o} for p, o in owners.items() if len(o) > 1]


# -------------------------------------------------------------- revival ---
def build_revival(tenants_raw, landlords, dist_area, area_keywords, today):
    """Reuse revival_board.py's own scan rather than re-implementing it — see that
    module's own docstring for the lead-cutoff / match-tier logic. revival_board.py
    normally runs standalone and does `from export_data import ...`, which only
    resolves when a module literally named "export_data" is already in
    sys.modules. When this file runs as __main__ (exactly how build.py invokes it)
    no such name exists yet, so alias this already-executing module in under that
    name before importing revival_board -- avoids a second, wasteful re-execution
    of this whole file. When this module is imported normally (e.g. under test as
    `import export_data as ed`) sys.modules already carries the right name and the
    setdefault is a no-op."""
    sys.modules.setdefault("export_data", sys.modules[__name__])
    import revival_board as _revival_board
    avail_for_revival = [{
        "id": l.get("id"), "name": l.get("landlord_name"), "district": l.get("district") or "",
        "rent_min": num(l.get("rent_min")), "rent_max": num(l.get("rent_max")),
    } for l in landlords if availability(l) == "Available"]
    rows = _revival_board.build_rows(tenants_raw, avail_for_revival, today, area_keywords)
    return [{
        "name": r["name"], "phone": r["phone"], "days_quiet": r["dq"], "budget": r["budget"],
        "district": r["district"], "pax": r["pax"], "tier": r["tier"],
        "match": ({"id": r["match"]["id"], "name": r["match"]["name"], "district": r["match"]["district"],
                   "rent_min": r["match"]["rent_min"], "rent_max": r["match"]["rent_max"]}
                  if r["match"] else None),
    } for r in rows]


# ------------------------------------------------------------- health -----
def compute_health(listings, tenants):
    def unparsed(l):
        g = l["gates"]
        return bool(l["req_raw"]) and g["gender"] == "any" and g["ethnicity"]["rule"] in ("any", "note") \
            and g["max_pax"] is None and g["lease_min"] is None \
            and not g["cooking"] and not g["pets"] and not g["smoking"]
    return {
        "tenants_missing_budget": sum(1 for t in tenants if "budget" in t["missing"]),
        "tenants_missing_move_in": sum(1 for t in tenants if "move_in" in t["missing"]),
        "tenants_missing_pax": sum(1 for t in tenants if "pax" in t["missing"]),
        "tenants_missing_lease_months": sum(1 for t in tenants if "lease_months" in t["missing"]),
        "tenants_missing_district": sum(1 for t in tenants if "district" in t["missing"]),
        "listings_unparsed_req_raw": sum(1 for l in listings if unparsed(l)),
    }


# ------------------------------------------- enrichment queue [ideas 5-7] --
def unlock_value_for(missing, listings):
    """Sum, across the tenant's own missing intake fields, of how many CURRENTLY
    AVAILABLE listings that specific field's gate applies to -- filtered here to
    availability=="Available" rather than trusting the caller, so the docstring's
    own claim is actually enforced. This used to be a distinct-listing UNION
    ("blocked by AT LEAST ONE missing field") rather than a sum, but "district"
    applies to literally every listing unconditionally (adjacency scoring is
    universal) -- under a union that makes district alone hit the ceiling
    (=len(listings)) for EVERY district-missing tenant regardless of what else
    they're missing, since the union with an unconditional field can never
    exceed and never differ from "every listing". On the real book that was 91
    of 218 tenants tied at the exact same top score, with a tenant missing only
    district outranking (via the fewest-missing-fields tie-break) one missing
    budget+pax+lease+move_in. Summing means a tenant missing several gated
    fields accumulates each field's own count -- budget/pax/lease_months/move_in
    only count a listing when that listing actually gates on the field (has a
    price floor / a max_pax / a lease_min / a known available_from); district
    still counts every listing WITH a district set (location scoring applies
    universally, but only where there's something to be adjacent to)."""
    if not missing:
        return 0
    avail = [l for l in listings if l.get("availability") == "Available"]
    value = 0
    for l in avail:
        g = l.get("gates") or {}
        if "budget" in missing and l.get("rent_min") is not None: value += 1
        if "pax" in missing and g.get("max_pax") is not None: value += 1
        if "lease_months" in missing and g.get("lease_min") is not None: value += 1
        if "move_in" in missing and l.get("available_from") is not None: value += 1
        if "district" in missing and l.get("district"): value += 1
    return value


def build_enrichment_queue(tenants, listings):
    """Ranked "which 5 minutes of asking unlocks the most" list: every still-looking
    tenant with >=1 missing intake field, sorted by unlock_value descending (ties
    broken by fewest missing fields first -- the quicker ask)."""
    rows = []
    for t in tenants:
        missing = t.get("missing") or []
        if not missing:
            continue
        rows.append({
            "id": t["id"], "name": t["name"], "phone": t.get("phone") or "",
            "missing": missing, "unlock_value": unlock_value_for(missing, listings),
        })
    rows.sort(key=lambda r: (-r["unlock_value"], len(r["missing"])))
    return rows


# ------------------------------------------ live demand/supply [23,24,25] --
def build_live_area_demand(adem_districts, listings, tenants):
    """Extends v2's existing area_demand rows with counts computed from THIS
    build's own listings[]/tenants[] (post district-inference), rather than only
    carrying the separately scheduled build_area_demand.py snapshot's precomputed
    active_listings/supply_gap through unchanged -- additive, none of the existing
    keys are removed or recalculated.

    live_waiting_building_level is a SUBSET of live_waiting_tenants: tenants whose
    district came from infer_district()'s "known_place" source (a single named
    building/street, e.g. all 17 "Cherryhill" tenants -- see KNOWN_PLACE_DISTRICTS)
    rather than a genuine district-wide area_keyword or a stated district. On the
    real book every one of the 22 district-inference "recoveries" came from
    KNOWN_PLACE_DISTRICTS, none from the broader area-demand keyword lists -- so
    without this breakdown, D15/D19's live_waiting_tenants reads as district-wide
    sourcing demand when it is really "10 people asked about ONE building." A
    reader can now tell live_waiting_tenants - live_waiting_building_level for the
    genuinely district-wide count."""
    live_listings, live_waiting, live_waiting_building = {}, {}, {}
    for l in listings:
        d = l.get("district") or ""
        if d: live_listings[d] = live_listings.get(d, 0) + 1
    for t in tenants:
        d = t.get("district") or ""
        if not d: continue
        live_waiting[d] = live_waiting.get(d, 0) + 1
        if t.get("district_source") == "known_place":
            live_waiting_building[d] = live_waiting_building.get(d, 0) + 1

    rows = []
    for d in adem_districts:
        district = d["district"]
        avail = live_listings.get(district, 0)
        waiting = live_waiting.get(district, 0)
        rows.append({
            "district": district, "area": d["area"],
            "unmatched_waiting": d.get("unmatched_waiting"), "supply_gap": d.get("supply_gap"),
            "sourcing_priority": d.get("sourcing_priority"),
            "live_available_listings": avail, "live_waiting_tenants": waiting,
            "live_waiting_building_level": live_waiting_building.get(district, 0),
            "live_gap": max(waiting - avail, 0),
        })
    return rows


def build_zero_stock_alert(live_area_demand, min_waiting=3):
    """[idea 25] Areas with real waiting demand and literally nothing live to show
    them -- Winfred's sourcing signal. Sorted by waiting count descending."""
    rows = [r for r in live_area_demand
            if r["live_waiting_tenants"] >= min_waiting and r["live_available_listings"] == 0]
    rows.sort(key=lambda r: -r["live_waiting_tenants"])
    return rows


def build_supply_gap_chase(landlords, listings, tenants, exclusions_cfg):
    """Landlords worth a call in the districts where demand starves supply.
    The map's demand-vs-supply table names the gap; this names WHO to chase:
    dormant/stalled landlords (relationships that exist but went quiet) sitting
    in a gap district. Never anyone closed, do_not_contact, or in the
    exclusions config -- a gap is not licence to ring someone who said no.
    Gap = zero live supply with >=3 waiting, or demand at least 3x supply with
    >=10 waiting (the D16 shape: 41 waiting on 5 rooms)."""
    demand, supply = {}, {}
    for t in tenants:
        for d in (t.get("preferred_districts") or []):
            demand[d] = demand.get(d, 0) + 1
    for l in listings:
        d = l.get("district") or ""
        if d:
            supply[d] = supply.get(d, 0) + 1
    gaps = [d for d, n in demand.items()
            if (supply.get(d, 0) == 0 and n >= 3) or (n >= 10 and n >= 3 * max(supply.get(d, 0), 1))]
    gaps.sort(key=lambda d: -(demand.get(d, 0) - supply.get(d, 0)))
    phones_cfg = {enrich.normalize_phone(p) for p in (exclusions_cfg.get("phones") or [])}
    out = []
    for l in landlords:
        st = str(l.get("status") or "").strip().lower()
        if not (st.startswith("dormant") or st.startswith("stalled")):
            continue
        if l.get("do_not_contact"):
            continue
        d = l.get("district") or ""
        if d not in gaps:
            continue
        if enrich.normalize_phone(l.get("phone")) in phones_cfg:
            continue
        out.append({"id": l.get("id"), "name": l.get("landlord_name"), "phone": l.get("phone"),
                    "district": d, "status": st, "last_contact": str(l.get("last_contact") or "")[:10],
                    "waiting": demand.get(d, 0), "live_supply": supply.get(d, 0)})
    out.sort(key=lambda r: r["last_contact"], reverse=True)
    out.sort(key=lambda r: gaps.index(r["district"]))
    return out


# ------------------------------------- landlord responsiveness [idea 24] --
ASK_GAP_HOURS = 24  # a same-sender follow-up within this window counts as part of
                     # the same "ask" (a nudge/reminder), not a fresh unanswered prompt

# A burst of Winfred's own messages only STARTS a genuinely-timed "ask" if the
# landlord had been silent at least this long beforehand. Without this, every
# turn boundary in a live back-and-forth (both sides replying within seconds)
# becomes its own "ask" with a latency near zero -- on the real book LL039's 118
# messages produced 33 fake sub-minute "asks" and a median that rounded to 0.0h,
# hiding a genuine 5.7-DAY wait buried in the same chat. 2 hours is long enough
# that no ordinary live exchange crosses it mid-conversation, short enough that a
# genuine same-day follow-up question still gets timed.
MIN_SILENCE_BEFORE_ASK_HOURS = 2

def _parse_wa_ts(raw):
    if not raw:
        return None
    s = str(raw).strip()
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    s = s.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _chat_asks(rows):
    """rows: (content, ts_raw, is_from_me) ASC by timestamp. An "ask" is a run of
    Winfred's own messages with no landlord reply and no gap over ASK_GAP_HOURS; it
    closes on the landlord's first reply after it (latency = reply time minus the
    LAST message of the ask -- how long the landlord made Winfred wait after he
    stopped talking) or stays open ("unanswered") if a new ask starts, or the chat
    ends, before any reply ever comes.

    A fresh burst is only opened as a timed ask when the landlord's own last
    message (if any) was at least MIN_SILENCE_BEFORE_ASK_HOURS ago -- otherwise
    it's just Winfred's half of an active live exchange (he just got a reply and
    is continuing the conversation), not a fresh prompt awaiting one. That burst
    is silently absorbed: not timed, not counted as unanswered either."""
    asks = []
    current = None
    last_landlord_ts = None

    def genuine_ask(ts):
        return last_landlord_ts is None or (ts - last_landlord_ts).total_seconds() >= MIN_SILENCE_BEFORE_ASK_HOURS * 3600

    for _content, ts_raw, is_from_me in rows:
        ts = _parse_wa_ts(ts_raw)
        if ts is None:
            continue
        if is_from_me:
            if current is None:
                if genuine_ask(ts):
                    current = {"start": ts, "end": ts}
            elif (ts - current["end"]).total_seconds() > ASK_GAP_HOURS * 3600:
                asks.append({**current, "answered_at": None})
                current = {"start": ts, "end": ts} if genuine_ask(ts) else None
            else:
                current["end"] = ts
        else:
            if current is not None:
                asks.append({**current, "answered_at": ts})
                current = None
            last_landlord_ts = ts
    if current is not None:
        asks.append({**current, "answered_at": None})
    return asks


def build_landlord_responsiveness(conn, landlords):
    """Per landlord: median AND worst-case reply latency in MINUTES (not rounded
    hours -- a real reply can be seconds, and rounding to hours loses that), plus
    unanswered count, read only from the WhatsApp store's own timestamps (never
    written to). Landlords with no chat_jid, no chat history, or a closed/
    unavailable bridge are simply left out (nothing to compute) rather than given
    a fabricated 0.

    worst_case_reply_minutes (the slowest single answered ask) is reported
    alongside the median because a median alone hides exactly the case this
    field exists for: a landlord who usually replies in minutes but occasionally
    vanishes for days. Chose worst-case (max) over an interpolated p90 because
    per-landlord sample sizes here are small (a handful of asks each) -- a p90
    on n<10 is not a meaningful percentile, whereas max is exact and honest.

    Ranked (unanswered_count, worst_case_reply_minutes, median_reply_minutes)
    ascending -- fewest ghosted asks first, then the lowest worst-case wait, then
    fastest typical reply. The old sort (fastest median first) ranked whichever
    landlord had the most rapid live back-and-forth chatter first, since that
    inflated their "asks" count with near-zero fake latencies and dragged the
    median down -- it was ranking chattiness, not reliability."""
    if not conn:
        return []
    out = []
    for l in landlords:
        jid = l.get("chat_jid") or ""
        if not jid:
            continue
        try:
            rows = conn.execute(
                "SELECT content, timestamp, is_from_me FROM messages WHERE chat_jid=? "
                "ORDER BY timestamp ASC", (jid,)).fetchall()
        except sqlite3.Error:
            continue
        if not rows:
            continue
        asks = _chat_asks(rows)
        lat_minutes = [(a["answered_at"] - a["end"]).total_seconds() / 60 for a in asks if a["answered_at"]]
        unanswered = sum(1 for a in asks if a["answered_at"] is None)
        if not lat_minutes and not unanswered:
            continue
        out.append({
            "id": l.get("id"), "name": l.get("landlord_name"),
            "n_asks": len(asks), "n_answered": len(lat_minutes),
            "median_reply_minutes": round(statistics.median(lat_minutes), 1) if lat_minutes else None,
            "worst_case_reply_minutes": round(max(lat_minutes), 1) if lat_minutes else None,
            "unanswered_count": unanswered,
        })
    out.sort(key=lambda r: (r["unanswered_count"],
                             r["worst_case_reply_minutes"] is None, r["worst_case_reply_minutes"] or 0,
                             r["median_reply_minutes"] is None, r["median_reply_minutes"] or 0))
    return out


# --------------------------------------- price vs closes honesty [idea 26] --
# A single ROOM's plausible SG monthly rent. Room-rental data in this book runs
# $850-$1,700 in practice; widened well beyond that on both ends (co-living/
# premium rooms, off-season markdowns) so this only screens obvious mis-parses,
# not legitimate outliers -- see build_price_check()'s own docstring for why
# rent_min/rent_max cannot be trusted raw for this.
ROOM_RENT_MIN = 400
ROOM_RENT_MAX = 3000

# property_type text that means the record can't represent a single room's
# price at all: a landlord record covering multiple rooms across >1 physical
# unit (e.g. "Condo (multi-room, 2 units)") mixes several different rooms'
# rents into one rent_min/rent_max pair, or "whole flat/unit/house" records are
# priced as one lump sum, not a per-room rate.
MULTI_ROOM_PROPERTY_RE = re.compile(r"\bmulti[- ]?room\b|\bwhole\s*(?:flat|unit|house)\b", re.I)

CLOSED_PRICE_MIN_N = 3


def _room_rent_for_band(l):
    """Best-effort single ROOM rent for build_price_check()'s same-district
    bands. rent_min/rent_max are NOT trustworthy raw for this: the same upstream
    mis-parse the file already warns about for sale records (~line 178) also
    hits rentals whose free text lists MORE THAN ONE price -- e.g. LL007's
    "Common $850 (was $1,000); whole unit $5,000" parses to rent_min=850/
    rent_max=5000, so the raw rent_max is a DIFFERENT unit's (the whole flat's)
    price, not this room's. Prefers rent_max, falling back to rent_min, but only
    accepts a candidate within the ROOM_RENT_MIN..ROOM_RENT_MAX sanity bound --
    catches LL007 (5000 rejected, falls back to the genuine 850) without needing
    to re-parse rooms_and_rent. Records whose property_type itself says
    multi-room/whole-unit (e.g. LL012's "Condo (multi-room, 2 units)", which
    bundles rents from TWO different physical units into one rent_min/rent_max
    pair) are excluded outright -- no single number in that record represents
    ONE room's price, sane-bounded or not."""
    if MULTI_ROOM_PROPERTY_RE.search(l.get("property_type") or ""):
        return None
    for candidate in (num(l.get("rent_max")), num(l.get("rent_min"))):
        if candidate is not None and ROOM_RENT_MIN <= candidate <= ROOM_RENT_MAX:
            return candidate
    return None


def build_price_check(landlords, listings, min_n=CLOSED_PRICE_MIN_N):
    """Compares each currently AVAILABLE listing's rent against a same-district
    band of Winfred's own past closes (lifecycle()=="tenanted"). With ~20 total
    closes spread across up to 28 districts, most districts land at n=1 or n=2 --
    a "band" from one data point is worse than no band, so both the band AND any
    flag built on it are suppressed outright below min_n. Per-close rent comes
    from _room_rent_for_band() (see its own docstring) rather than raw rent_max/
    rent_min, which can silently mix a room price with an unrelated whole-unit
    or multi-room price for the SAME record; closes with no sane per-room price
    at all are skipped, never guessed."""
    closed_by_district = {}
    for l in landlords:
        if lifecycle(l) != "tenanted":
            continue
        d = l.get("district") or ""
        r = _room_rent_for_band(l)
        if not d or r is None:
            continue
        closed_by_district.setdefault(d, []).append(r)

    bands = {}
    for d, vals in closed_by_district.items():
        if len(vals) < min_n:
            continue
        bands[d] = {"district": d, "n": len(vals), "min": min(vals), "max": max(vals),
                    "median": statistics.median(vals)}

    flags = []
    for l in listings:
        band = bands.get(l.get("district") or "")
        if not band:
            continue
        rep = l.get("rent_max") or l.get("rent_min")
        if rep is None:
            continue
        if rep > band["max"] * 1.15:
            direction = "above_market"
        elif rep < band["min"] * 0.85:
            direction = "below_market"
        else:
            continue
        flags.append({"listing_id": l["id"], "name": l["name"], "district": l["district"],
                       "listing_rent": rep, "band_median": band["median"], "band_n": band["n"],
                       "direction": direction})
    return {"min_n": min_n, "bands": sorted(bands.values(), key=lambda b: b["district"]), "flags": flags}


# ------------------------------------------- days-to-fill honesty [idea 27] --
DAYS_TO_FILL_MIN_N = 3
CLOSES_PATH = os.path.expanduser("~/.claude/state/matchmaker-closes.json")

def load_closes(path=CLOSES_PATH):
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def record_closes(landlords, prev, today, path=CLOSES_PATH):
    """The reason days_to_fill sat at 0 samples: statuses read "closed (tenanted)"
    with no date, so no (first_seen, close) pair ever completed. This ledger stamps
    the close date at the moment of TRANSITION -- a listing that was in the
    previous build's live listings[] and is tenanted now closed today, as far as
    this app can observe. Append-only per listing id; re-runs change nothing."""
    closes = load_closes(path)
    prev_live = {x.get("id") for x in ((prev or {}).get("listings") or [])}
    added = 0
    for l in landlords:
        lid = l.get("id")
        if not lid or lid in closes or lifecycle(l) != "tenanted":
            continue
        if lid in prev_live:
            closes[lid] = today.isoformat()
            added += 1
    if added:
        tmp = path + ".tmp"
        json.dump(closes, open(tmp, "w"), indent=1)
        os.replace(tmp, path)
    return closes

def _extract_close_date(status_raw, last_contact, today):
    # reuse the same D-Month-Year free text parser used for tenant move_in dates --
    # status text carries the same "9 Jul 2026" shape ("closed (tenanted 9 Jul 2026)")
    d = enrich.norm_move_in(status_raw, today)
    if d:
        return d
    return enrich.norm_date(last_contact)


def build_days_to_fill(landlords, seen_registry, today, min_n=DAYS_TO_FILL_MIN_N, closes=None):
    """Days between a listing's first_seen (the seen registry -- the same file
    build_listings() stamps) and its close date, grouped by district. Needs BOTH
    ends for the SAME listing id. None of the currently closed records have both:
    the seen registry only started tracking first_seen once this app began
    watching a listing, and every current close either predates that or was never
    captured while still available -- reported honestly as 0 usable samples
    (status "insufficient_data") rather than guessed. Will fill in as listings
    that ARE being tracked eventually close."""
    samples_by_district, all_samples = {}, []
    for l in landlords:
        if lifecycle(l) != "tenanted":
            continue
        fseen = seen_registry.get(l.get("id"))
        if not fseen:
            continue
        close = ((closes or {}).get(l.get("id"))
                 or _extract_close_date(l.get("status") or "", l.get("last_contact") or "", today))
        if not close:
            continue
        try:
            days = (datetime.date.fromisoformat(close) - datetime.date.fromisoformat(fseen)).days
        except ValueError:
            continue
        if not (0 <= days <= 365):
            continue
        all_samples.append(days)
        d = l.get("district") or ""
        if d:
            samples_by_district.setdefault(d, []).append(days)

    by_district = [{"district": d, "n": len(v), "median_days": statistics.median(v)}
                   for d, v in samples_by_district.items() if len(v) >= min_n]
    overall = {"n": len(all_samples),
               "status": "ok" if len(all_samples) >= min_n else "insufficient_data",
               "median_days": statistics.median(all_samples) if len(all_samples) >= min_n else None}
    return {"min_n": min_n, "overall": overall, "by_district": sorted(by_district, key=lambda b: b["district"]),
            "note": ("needs a first_seen date (seen registry) AND a parseable close date for the SAME "
                     "listing; suppressed to insufficient_data below min_n rather than guessed")}


# --------------------------------------------- outcome capture [idea 11] --
LEARNING_MIN_TRIPLES = 30  # heuristic floor (not a rigorous power calc): enough rows
                            # that a handful of scoring dimensions (budget, district,
                            # gender, ethnicity, pax/lease) each get a few observations
                            # per outcome class. Meant as "clearly not yet", not a claim
                            # that 30 is exactly the right number.

def build_learning_block(min_triples=LEARNING_MIN_TRIPLES):
    """(tenant, listing, outcome) triples usable for weight-fitting the scoring
    model. Currently 0, always: the CRM's crm_match_status table (status=
    closed_won) is the intended future source once it accumulates real data, but
    it lives in Postgres behind the deployed app and this offline build has no
    credentials or network path to read it. Neither local database
    (landlord-db.json / tenant-db.json) has a field linking a specific tenant to
    the specific listing they actually closed on -- a tenant's own "matches"/
    "suggested_new" are this build's scoring CANDIDATES, not a recorded outcome.
    Never fabricated -- stays 0 until a real linked source exists.

    usable_triples=0 is honest TODAY (there is genuinely no source at all, not
    just a source that happens to be empty), but a bare 0 sitting in an integer
    field is indistinguishable from a real computed zero once the CRM DOES start
    recording closes and this block is still not wired up -- it would then read
    0 forever, silently, with nothing to say it was never actually computed.
    source_wired=False is the explicit, self-evident marker: any reader can
    check it instead of trusting usable_triples' magnitude to mean "nothing to
    learn from" versus "not even measuring yet". Flip it to True only once this
    function is actually reading a real linked source."""
    usable = 0
    needed = max(min_triples - usable, 0)
    return {
        "usable_triples": usable, "needed_for_meaningful_fit": min_triples,
        "source_wired": False,
        "status": f"dormant, needs {needed} more closes",
        "note": ("0 usable (tenant, listing, outcome) triples exist locally. crm_match_status "
                 "(status=closed_won) is the intended source once it accumulates data, but it lives "
                 "in Postgres behind the deployed app -- unreachable from this offline build. Neither "
                 "landlord-db.json nor tenant-db.json links a tenant to the listing they actually "
                 "closed on. source_wired=False marks this as never-computed, not a computed zero -- "
                 "check that flag rather than trusting usable_triples' value alone."),
    }


# -------------------------------------------------------------- delta -----
def compute_delta(prev, listings, tenants):
    if not prev: return None
    prev_listings = {l["id"]: l for l in prev.get("listings") or []}
    prev_tenant_ids = {t["id"] for t in prev.get("tenants") or []}
    cur_listing_ids = {l["id"] for l in listings}
    cur_tenant_ids = {t["id"] for t in tenants}
    new_tenant_ids = [t["id"] for t in tenants if t["id"] not in prev_tenant_ids]
    new_listing_ids = [l["id"] for l in listings if l["id"] not in prev_listings]
    gone_listings = [{"id": pid, "name": pl.get("name")} for pid, pl in prev_listings.items() if pid not in cur_listing_ids]
    # symmetric to gone_listings: a still-looking tenant last build who is no longer
    # still-looking this build (found/excluded/went stale) -- feeds week_delta's
    # "tenants lost" without any new mechanism of its own.
    tenant_lost_ids = [tid for tid in prev_tenant_ids if tid not in cur_tenant_ids]
    availability_changes = []
    for l in listings:
        pl = prev_listings.get(l["id"])
        if pl and pl.get("availability") != l["availability"]:
            availability_changes.append({"id": l["id"], "from": pl.get("availability"), "to": l["availability"]})
    return {
        "prev_generated": prev.get("generated"),
        "new_tenant_ids": new_tenant_ids, "new_listing_ids": new_listing_ids,
        "gone_listings": gone_listings, "availability_changes": availability_changes,
        "tenant_lost_ids": tenant_lost_ids,
    }


WEEK_DELTA_WINDOW_DAYS = 7

def compute_week_delta(prev, delta, listings, today):
    """Rolling WEEK_DELTA_WINDOW_DAYS-day view built from the SAME delta/seen
    machinery compute_delta()/build_listings() already use -- no separate archive.
    Each build's own compute_delta() output becomes one dated event, carried
    forward through matchmaker-data.prev.json (the same roll-forward this file
    already does for `delta` itself) and aged out once older than the window.
    new_stock is read directly off this build's own first_seen stamps (the seen
    registry), which is exact; gone_listings/availability_changes/tenants_lost
    accuracy is bounded by how often the build actually runs (several times a day
    in practice, per matchmaker-build-history.jsonl) since each is only sampled
    at build time, not tracked continuously.

    events grew one entry per build with no dedup -- several no-op builds in the
    same day (nothing changed since the last one) each appended an identical
    empty event, so a busy day's events[] filled up with copies carrying zero new
    information. When this run's content (gone_listings/availability_changes/
    tenant_lost_ids) is IDENTICAL to the most recently stored event, that event
    is refreshed in place (its date bumped to today) instead of appended again --
    a genuinely new event (different content) still always appends, so the
    week's real history of changes is preserved."""
    cutoff = (today - datetime.timedelta(days=WEEK_DELTA_WINDOW_DAYS)).isoformat()
    prev_week = (prev or {}).get("week_delta") or {}
    events = [e for e in (prev_week.get("events") or []) if (e.get("date") or "") >= cutoff]
    if delta:
        new_event = {
            "date": today.isoformat(),
            "gone_listings": delta["gone_listings"],
            "availability_changes": delta["availability_changes"],
            "tenant_lost_ids": delta["tenant_lost_ids"],
        }
        same_as_last = events and all(
            events[-1].get(k) == new_event[k]
            for k in ("gone_listings", "availability_changes", "tenant_lost_ids"))
        if same_as_last:
            events[-1] = new_event
        else:
            events.append(new_event)

    gone_by_id, tenants_lost, avail_changes = {}, set(), []
    for e in events:
        for g in e.get("gone_listings") or []:
            gone_by_id[g["id"]] = g
        for tid in e.get("tenant_lost_ids") or []:
            tenants_lost.add(tid)
        avail_changes.extend(e.get("availability_changes") or [])

    new_stock = [{"id": l["id"], "name": l["name"], "district": l["district"], "first_seen": l["first_seen"]}
                 for l in listings if l.get("first_seen") and l["first_seen"] >= cutoff]

    return {
        "window_days": WEEK_DELTA_WINDOW_DAYS, "since": cutoff,
        "gone_listings": list(gone_by_id.values()), "availability_changes": avail_changes,
        "tenants_lost": sorted(tenants_lost), "new_stock": new_stock,
        "events": events,  # raw log carried forward next run; UI can ignore this
    }


# ------------------------------------------------------- key safety [5] ---
# The app stores every mark under localStorage key "cbk_<listing id>_<tenant id>"
# and parses it back by splitting on the FIRST underscore, so a listing id
# containing "_" would round trip as a different pair (and "cbk_LL_1_T9" is the
# same key as listing "LL" + tenant "1_T9"). Two listing ids are worse than
# ambiguous: "offer" and "backup" would collide with the app's own reserved
# cbk_offer_* / cbk_backup_* keys — the mark would be unexportable and the auto
# backup would overwrite it. Tenant ids are the trailing segment and may hold
# "_" safely, but neither id may contain ":" (the reveal log's own separator).
# Real ids are LL###/TN###; this is a cheap guard so a future id shape cannot
# corrupt somebody's saved marks silently.
ID_SAFE_LISTING = re.compile(r"^[A-Za-z0-9-]+$")
ID_SAFE_TENANT = re.compile(r"^[A-Za-z0-9_-]+$")
RESERVED_LISTING_IDS = {"offer", "backup"}

def validate_key_ids(listings, tenants):
    """Pure — returns a list of human readable problems (empty = safe)."""
    problems = []
    for l in listings:
        lid = l.get("id")
        if not isinstance(lid, str) or not ID_SAFE_LISTING.match(lid):
            problems.append(f"listing id {lid!r} is not [A-Za-z0-9-]+ — it would corrupt the app's mark keys")
        elif lid.lower() in RESERVED_LISTING_IDS:
            problems.append(f"listing id {lid!r} collides with the app's reserved cbk_{lid.lower()}_* keys")
    for t in tenants:
        tid = t.get("id")
        if not isinstance(tid, str) or not ID_SAFE_TENANT.match(tid):
            problems.append(f"tenant id {tid!r} is not [A-Za-z0-9_-]+ — it would corrupt the app's mark keys")
    return problems


# ---------------------------------------------------- source availability --
def build_source_availability(wa_conn):
    """Explicit signal so the UI (and any human reading the payload) can tell
    "computed this and genuinely found nothing" from "could not compute at
    all". A WhatsApp bridge outage silently empties BOTH budget_contradictions
    (find_budget_contradiction returns None for every tenant) AND
    landlord_responsiveness (build_landlord_responsiveness returns []) with no
    marker anywhere in the payload -- "0 contradictions" then reads identically
    whether Winfred genuinely has none right now, or the bridge was locked when
    this build ran. wa_bridge=False means both those blocks are not to be
    trusted as "checked and clean" for this build."""
    return {"wa_bridge": wa_conn is not None}


# --------------------------------------------------------------- main -----
def main():
    # today MUST be derived from the same explicit +08:00 `now` that stamps
    # generated_ts, not a separate datetime.date.today() call. This app is
    # built on Winfred's own SGT machine, but datetime.date.today() reads
    # whatever system TZ the process actually runs under -- if that ever drifts
    # from SGT (a misconfigured launchd env, a one-off run on a non-SGT
    # machine), an independent today would silently disagree with generated_ts
    # by a day, and every date computed against `today` below (days_listed,
    # reconfirm_due, move_in_norm's year rollover, available_from) would be
    # off by exactly the same day relative to what the artifact CLAIMS it was
    # built as. Deriving both from one `now` makes that impossible by
    # construction, matching build.py's own generated/generated_ts fix.
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    today = now.date()
    generated_ts = now.isoformat(timespec="seconds")
    build_id = hashlib.sha256(generated_ts.encode()).hexdigest()[:8]

    land = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
    ten = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
    adem = json.load(open(os.path.join(ROOT, "_templates/area-demand.json")))
    dist_area = {d["district"]: d["area"] for d in adem["districts"]}

    exclusions_cfg = load_exclusions_config(EXCLUSIONS_PATH)
    seen_registry = load_seen_registry(SEEN_PATH)
    fixed_viewing_index = enrich.load_fixed_viewing_index(LISTING_INDEX_PATH)
    photo_url_index = enrich.load_photo_url_index(LISTINGS_JSON_PATH)
    harvested_photos = {}
    try:
        harvested_photos = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos-harvested.json")))
    except Exception:
        pass

    wa_conn = enrich.open_wa_bridge(WA_DB_PATH)
    source_availability = build_source_availability(wa_conn)  # [item 6] before any close() below
    if wa_conn is None:
        print("warning: WhatsApp bridge unavailable/locked — last_wa and lang left null/default for all tenants, "
              "budget_contradictions/landlord_responsiveness cannot be computed this build (see source_availability)")

    area_keywords = build_area_keywords(dist_area)  # built before build_tenants -- it needs
                                                     # this for district inference (item 1)

    listings = build_listings(land["landlords"], dist_area, fixed_viewing_index, photo_url_index, seen_registry, today, harvested_photos)
    dedup_listing_pairs = apply_listing_dup_of(listings)
    save_seen_registry(SEEN_PATH, seen_registry)
    supply_overview = build_supply_overview(land["landlords"])  # [65] all statuses, digest only
    stale_landlord_chase = build_stale_landlord_chase(listings)  # [idea 28]

    tenants_all_statuses = len(ten["tenants"])
    fee_detected = detect_fee_willing_from_chats(wa_conn, ten["tenants"], today)
    if fee_detected:
        print(f"fee-willing: {fee_detected} tenant(s) newly detected from chat — feeding the URGENT segment")
    tenants, excl_counts = build_tenants(ten["tenants"], exclusions_cfg, wa_conn, today, area_keywords)
    dedup_tenant_groups = apply_tenant_dup_groups(tenants)
    districts_recovered = sum(1 for t in tenants if t.get("district_inferred"))
    budget_contradictions = [
        {"tenant_id": t["id"], "name": t["name"], "phone": t.get("phone") or "", **t["budget_contradiction"]}
        for t in tenants if t.get("budget_contradiction")
    ]

    # landlord_responsiveness also needs the WA bridge, so keep wa_conn open until
    # this is done -- it used to close right after build_tenants.
    landlord_responsiveness = build_landlord_responsiveness(wa_conn, land["landlords"])  # [idea 24]
    if wa_conn: wa_conn.close()

    all_landlords = build_all_landlords(land["landlords"], dist_area, area_keywords, harvested_photos)
    all_tenants = build_all_tenants(ten["tenants"], area_keywords)
    sales = build_sales(land["landlords"], dist_area, area_keywords)
    duplicate_phones = compute_duplicate_phones(all_landlords, ten["tenants"])
    revival = build_revival(ten["tenants"], land["landlords"], dist_area, area_keywords, today)

    # prev hoisted above the analytics blocks: record_closes() needs last build's
    # live listings to see an available -> tenanted transition. compute_delta()
    # below reuses this same load.
    prev = None
    if os.path.exists(PREV):
        try: prev = json.load(open(PREV))
        except (OSError, ValueError): prev = None

    enrichment_queue = build_enrichment_queue(tenants, listings)  # [ideas 5,6]
    live_area_demand = build_live_area_demand(adem["districts"], listings, tenants)  # [ideas 4,23]
    zero_stock_alert = build_zero_stock_alert(live_area_demand)  # [idea 25]
    supply_gap_chase = build_supply_gap_chase(land["landlords"], listings, tenants, exclusions_cfg)
    price_check = build_price_check(land["landlords"], listings)  # [idea 26]
    closes_ledger = record_closes(land["landlords"], prev, today)
    days_to_fill = build_days_to_fill(land["landlords"], seen_registry, today, closes=closes_ledger)  # [idea 27]
    learning = build_learning_block()  # [idea 11]

    key_problems = validate_key_ids(listings, tenants)
    if key_problems:
        raise StateFileError("unsafe id(s) for the app's localStorage mark keys:\n  " + "\n  ".join(key_problems))

    health = compute_health(listings, tenants)
    busy_blocks = load_busy_blocks(BUSY_BLOCKS_PATH)  # [68] optional, dormant until a UI clash check exists

    delta = compute_delta(prev, listings, tenants)
    week_delta = compute_week_delta(prev, delta, listings, today)  # [idea 40]

    source_counts = {
        "listings_available": len(listings), "tenants_looking": len(tenants),
        "excluded_agents": excl_counts["config_phone"] + excl_counts["config_id"] + excl_counts["config_name_marker"],
        "dedup_tenant_groups": dedup_tenant_groups, "dedup_listing_pairs": dedup_listing_pairs,
    }

    data = {
        "schema_version": 2,
        "generated": today.isoformat(), "generated_ts": generated_ts, "build_id": build_id,
        "priority": ["availability","location","price","landlord requirements"],
        "counts": {"available_listings": len(listings), "still_looking_tenants": len(tenants)},
        "source_counts": source_counts, "source_availability": source_availability,
        "tenants_all_statuses": tenants_all_statuses,
        "delta": delta, "health": health,
        "districts": dist_area,
        "area_demand": live_area_demand,
        "busy_blocks": busy_blocks,
        "listings": listings, "tenants": tenants,
        "all_landlords": all_landlords, "all_tenants": all_tenants, "sales": sales,
        "revival": revival, "duplicate_phones": duplicate_phones,
        "enrichment_queue": enrichment_queue,
        "supply_gap_chase": supply_gap_chase,
        "stale_landlord_chase": stale_landlord_chase,
        # Re-added 26 Aug 2026 for the landlord-reliability score, but slim: name
        # dropped (the app has it by id via all_landlords/listings), so this is
        # ~half the old footprint. id -> reply-latency + ghosted-ask counts.
        "landlord_responsiveness": [
            {k: v for k, v in r.items() if k != "name"} for r in landlord_responsiveness],
    }
    # Dropped from the serialized payload (token-slim, 22 Aug 2026): week_delta,
    # supply_overview (app now derives it from all_landlords via lifecycle),
    # price_check, days_to_fill, budget_contradictions, zero_stock_alert, learning
    # — none had an app or digest consumer. Their on-disk ledgers (closes, price
    # bands, learning) are untouched and keep accumulating; only the unread copies
    # in this file are removed.
    # Same directory as OUT (so os.replace stays an atomic same filesystem
    # rename) but named matchmaker-data.tmp.json, not matchmaker-data.json.tmp:
    # the gitignore rule is "matchmaker-data*.json" (must END in .json), so the
    # old ".tmp" suffix was a real tenant/landlord PII file with no ignore
    # coverage for however briefly it exists between this write and the rename.
    tmp = os.path.join(HERE, "matchmaker-data.tmp.json")
    json.dump(data, open(tmp, "w"), ensure_ascii=False)
    _save_geocache()
    os.replace(tmp, OUT)  # atomic — a crash mid write never leaves a truncated OUT
    print("wrote", OUT)
    print("listings:", len(listings), "| tenants:", len(tenants))
    print("all_landlords:", len(all_landlords), "| all_tenants:", len(all_tenants), "| sales:", len(sales),
          "| revival:", len(revival), "| duplicate_phones:", len(duplicate_phones))
    print("source_counts:", json.dumps(source_counts, ensure_ascii=False))
    print("source_availability:", json.dumps(source_availability, ensure_ascii=False))
    print("health:", json.dumps(health, ensure_ascii=False))
    print("delta:", "null (first run)" if delta is None else
          f"{len(delta['new_tenant_ids'])} new tenants, {len(delta['new_listing_ids'])} new listings, "
          f"{len(delta['gone_listings'])} gone, {len(delta['availability_changes'])} availability changes")
    print(f"districts recovered by inference: {districts_recovered}")
    print(f"enrichment_queue: {len(enrichment_queue)} | budget_contradictions: {len(budget_contradictions)} "
          f"| zero_stock_alert: {len(zero_stock_alert)} | landlord_responsiveness: {len(landlord_responsiveness)}")
    print(f"price_check bands: {len(price_check['bands'])} | flags: {len(price_check['flags'])} "
          f"| days_to_fill: {days_to_fill['overall']} | stale_landlord_chase: {len(stale_landlord_chase)}")
    print(f"learning: {learning['status']}")

if __name__ == "__main__":
    # A fail closed state file is an expected, actionable outcome, not a crash:
    # print the one line that says what to do and exit non zero so build.py
    # restores the previous artifact instead of shipping a half state.
    try:
        main()
    except StateFileError as e:
        print("EXPORT ABORTED: " + str(e), file=sys.stderr)
        sys.exit(1)
