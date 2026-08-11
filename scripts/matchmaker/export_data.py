#!/usr/bin/env python3
"""Export ONE compact JSON (schema v2) for the interactive matchmaker app: available
listings + still-looking tenants, with structured gates so the app can score matches
for ALL available listings. No blanks lost; PII stays local (gitignored output).

Pure-ish builder functions (build_listings, build_tenants, compute_*, apply_*) take
already-loaded data and do no file I/O themselves, so tests/matchmaker/test_export.py
can exercise them directly with fixtures. Only main() touches real paths.
"""
import json, os, re, sys, hashlib, datetime
import enrich

ROOT = os.path.expanduser("~/crestbrick-consult")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "matchmaker-data.json")
PREV = os.path.join(HERE, "matchmaker-data.prev.json")
EXCLUSIONS_PATH = os.path.expanduser("~/.claude/state/matchmaker-exclusions.json")
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
    if ms in ("found","in_deal") or cs=="found" or stt=="tenanted": return "Found"
    if (t.get("excluded") or cs in ("do_not_contact","not_interested")
        or ms in ("do_not_contact","excluded_india","excluded_family") or ms.startswith("stale") or stt=="excluded"):
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
def build_listings(landlords, dist_area, fixed_viewing_index, photo_url_index, seen_registry, today):
    today_str = today.isoformat()
    out = []
    for l in landlords:
        av = availability(l)
        if av not in ("Available", "Offer pending"): continue
        r = l.get("requirements") or {}
        lid = l.get("id")
        rent_min = (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")), num(l.get("rent_max")))
        rent_max = (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")), num(l.get("rent_max")))
        source = "co-broke" if "co-broke" in (l.get("contact_label_source") or "").lower() else "own"
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
            reconfirm_due = (today - datetime.date.fromisoformat(confirmed_at)).days >= 14
        else:
            reconfirm_due = days_listed >= 14

        photo_info = photo_url_index.get((lid or "").upper(), {})

        out.append({
            "id": lid, "name": l.get("landlord_name"), "availability": av,
            "district": l.get("district") or "", "address": l.get("full_address") or "",
            "map_query": maps_query(l.get("full_address"), l.get("district"), dist_area),
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
            "req_raw": {k: v for k, v in r.items() if v},
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
            "photos": photo_info.get("photos"),
            "listing_url": photo_info.get("listing_url"),
            "first_seen": fseen, "days_listed": days_listed,
            "is_cobroke": source == "co-broke",
            "dup_of": None,
            "reconfirm_due": reconfirm_due,
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


# -------------------------------------------------------------- tenants ---
def build_tenants(tenants_raw, exclusions_cfg, wa_conn, today):
    out = []
    excl_counts = {"db": 0, "config_phone": 0, "config_id": 0, "config_name_marker": 0}
    phones_cfg = {enrich.normalize_phone(p) for p in (exclusions_cfg.get("phones") or [])}
    ids_cfg = set(exclusions_cfg.get("ids") or [])
    markers_cfg = exclusions_cfg.get("name_markers") or []

    for t in tenants_raw:
        if looking(t) != "Still looking":
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
        district = t.get("district") or ""
        preferred_location = t.get("preferred_location") or ""
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

        # (73) status/listing_enquired/budget_note deliberately NOT exported as of
        # cycle 7: zero reads in app.js/scoring.js/template.html/build.py and no
        # dedicated test asserts their value (only generic required-keys
        # membership, updated alongside this cut) — ~10.8KB across 140 tenants.
        # listing_enquired's own INPUT use above (recover_budget) is untouched;
        # only the redundant verbatim pass-through into the shipped payload goes.
        # Contrast with intake_complete (kept — has its own dedicated correctness
        # test) and is_agent_suspect (kept — looks like a dormant agent-exclusion
        # signal per standing agent-exclusion policy, not dead code).
        out.append({
            "id": t.get("id"), "name": name,
            "preferred_location": preferred_location,
            "preferred_districts": ([str(x).strip() for x in t["preferred_districts"] if str(x).strip()]
                                     if isinstance(t.get("preferred_districts"), list)
                                     else [d.strip() for d in (t.get("preferred_districts") or "").split(",") if d.strip()]),
            "district": district,
            "budget": budget, "budget_min": budget_min, "budget_max": budget_max,
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


# -------------------------------------------------------------- delta -----
def compute_delta(prev, listings, tenants):
    if not prev: return None
    prev_listings = {l["id"]: l for l in prev.get("listings") or []}
    prev_tenant_ids = {t["id"] for t in prev.get("tenants") or []}
    cur_listing_ids = {l["id"] for l in listings}
    new_tenant_ids = [t["id"] for t in tenants if t["id"] not in prev_tenant_ids]
    new_listing_ids = [l["id"] for l in listings if l["id"] not in prev_listings]
    gone_listings = [{"id": pid, "name": pl.get("name")} for pid, pl in prev_listings.items() if pid not in cur_listing_ids]
    availability_changes = []
    for l in listings:
        pl = prev_listings.get(l["id"])
        if pl and pl.get("availability") != l["availability"]:
            availability_changes.append({"id": l["id"], "from": pl.get("availability"), "to": l["availability"]})
    return {
        "prev_generated": prev.get("generated"),
        "new_tenant_ids": new_tenant_ids, "new_listing_ids": new_listing_ids,
        "gone_listings": gone_listings, "availability_changes": availability_changes,
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

    wa_conn = enrich.open_wa_bridge(WA_DB_PATH)
    if wa_conn is None:
        print("warning: WhatsApp bridge unavailable/locked — last_wa and lang left null/default for all tenants")

    listings = build_listings(land["landlords"], dist_area, fixed_viewing_index, photo_url_index, seen_registry, today)
    dedup_listing_pairs = apply_listing_dup_of(listings)
    save_seen_registry(SEEN_PATH, seen_registry)
    supply_overview = build_supply_overview(land["landlords"])  # [65] all statuses, digest only

    tenants_all_statuses = len(ten["tenants"])
    tenants, excl_counts = build_tenants(ten["tenants"], exclusions_cfg, wa_conn, today)
    dedup_tenant_groups = apply_tenant_dup_groups(tenants)
    if wa_conn: wa_conn.close()

    key_problems = validate_key_ids(listings, tenants)
    if key_problems:
        raise StateFileError("unsafe id(s) for the app's localStorage mark keys:\n  " + "\n  ".join(key_problems))

    health = compute_health(listings, tenants)
    busy_blocks = load_busy_blocks(BUSY_BLOCKS_PATH)  # [68] optional, dormant until a UI clash check exists

    prev = None
    if os.path.exists(PREV):
        try: prev = json.load(open(PREV))
        except (OSError, ValueError): prev = None
    delta = compute_delta(prev, listings, tenants)

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
        "source_counts": source_counts, "tenants_all_statuses": tenants_all_statuses,
        "delta": delta, "health": health,
        "districts": dist_area,
        "area_demand": [{"district":d["district"],"area":d["area"],"unmatched_waiting":d.get("unmatched_waiting"),
                         "supply_gap":d.get("supply_gap"),"sourcing_priority":d.get("sourcing_priority")}
                        for d in adem["districts"]],
        "supply_overview": supply_overview, "busy_blocks": busy_blocks,
        "listings": listings, "tenants": tenants,
    }
    # Same directory as OUT (so os.replace stays an atomic same filesystem
    # rename) but named matchmaker-data.tmp.json, not matchmaker-data.json.tmp:
    # the gitignore rule is "matchmaker-data*.json" (must END in .json), so the
    # old ".tmp" suffix was a real tenant/landlord PII file with no ignore
    # coverage for however briefly it exists between this write and the rename.
    tmp = os.path.join(HERE, "matchmaker-data.tmp.json")
    json.dump(data, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, OUT)  # atomic — a crash mid write never leaves a truncated OUT
    print("wrote", OUT)
    print("listings:", len(listings), "| tenants:", len(tenants))
    print("source_counts:", json.dumps(source_counts, ensure_ascii=False))
    print("health:", json.dumps(health, ensure_ascii=False))
    print("delta:", "null (first run)" if delta is None else
          f"{len(delta['new_tenant_ids'])} new tenants, {len(delta['new_listing_ids'])} new listings, "
          f"{len(delta['gone_listings'])} gone, {len(delta['availability_changes'])} availability changes")

if __name__ == "__main__":
    # A fail closed state file is an expected, actionable outcome, not a crash:
    # print the one line that says what to do and exit non zero so build.py
    # restores the previous artifact instead of shipping a half state.
    try:
        main()
    except StateFileError as e:
        print("EXPORT ABORTED: " + str(e), file=sys.stderr)
        sys.exit(1)
