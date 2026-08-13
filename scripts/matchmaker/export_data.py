#!/usr/bin/env python3
"""Export ONE compact JSON for the interactive matchmaker app: available listings +
still-looking tenants, with structured gates so the app can score matches for ALL
available listings (not only the 5 the engine covers). No blanks lost; PII stays local."""
import datetime, importlib.util, json, os, re, sqlite3

ROOT = os.path.expanduser("~/crestbrick-consult")

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

def src_of(l):
    return "co-broke" if "co-broke" in (l.get("contact_label_source") or "").lower() else "own"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchmaker-data.json")
land = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
ten  = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
adem = json.load(open(os.path.join(ROOT, "_templates/area-demand.json")))
DIST_AREA = {d["district"]: d["area"] for d in adem["districts"]}

# ---- WhatsApp "story so far" -- last inbound message snippets per tenant, for the
# matchmaker background card. Read only, one batched scan of messages.db (not one query
# per tenant). lid/pn resolution follows the same pattern as scripts/revival_scan.py.
MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
WA_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")

def norm_phone_digits(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 8 and d[0] in "89":
        d = "65" + d
    return d

def load_wa_story(tenants):
    """Returns {tenant_id: [{"date": "YYYY-MM-DD", "text": snippet}, ...]} for the last
    up to 3 inbound messages per tenant phone. Never touches outbound messages or writes
    anything. Missing DBs or any read error -> empty dict (feature degrades silently)."""
    phone_to_id = {}
    for t in tenants:
        d = norm_phone_digits(t.get("phone"))
        if d:
            phone_to_id[d] = t.get("id")
    story = {}
    if not phone_to_id or not os.path.exists(MSG_DB) or not os.path.exists(WA_DB):
        return story
    try:
        wc = sqlite3.connect("file:" + WA_DB + "?mode=ro", uri=True, timeout=20)
        lid2pn = dict(wc.execute("SELECT lid, pn FROM whatsmeow_lid_map"))
        wc.close()
        mc = sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=20)
        rows = mc.execute(
            "SELECT chat_jid, content, timestamp FROM messages "
            "WHERE is_from_me=0 AND content != '' "
            "AND chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@newsletter' "
            "AND chat_jid NOT LIKE '120363%' AND chat_jid NOT LIKE '%@broadcast' "
            "ORDER BY timestamp ASC").fetchall()
        mc.close()
    except Exception:
        return story
    buckets = {}
    for jid, content, ts in rows:
        base = (jid or "").split("@")[0]
        pn = lid2pn.get(base, base) if (jid or "").endswith("@lid") else base
        tid = phone_to_id.get(pn) or phone_to_id.get(base)
        if not tid:
            continue
        buckets.setdefault(tid, []).append((ts, content))
    for tid, msgs in buckets.items():
        for ts, content in msgs[-3:]:
            story.setdefault(tid, []).append({
                "date": str(ts)[:10] if ts else "",
                "text": " ".join(str(content).split())[:100],
            })
    return story

WA_STORY = load_wa_story(ten["tenants"])

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

def maps_query(addr, district):
    q = addr or DIST_AREA.get(district, district or "")
    return (str(q).strip() + " Singapore") if q else ""

# district inference from free-text location — ~46% of tenants have no structured
# district/preferred_districts at all, only a preferred_location string ("Bayshore /
# East", "Jalan Batu (Katong)"). Without this the roster sort degenerates to "no
# district" for nearly half the list, which is what looked like everyone lumped
# together. Match against each district's known area-name keywords as a best-effort.
AREA_KEYWORDS = []  # [(district, keyword), ...] longest keyword first so "bukit batok" beats "batok"
for _d, _area in DIST_AREA.items():
    for _kw in [k.strip().lower() for k in _area.split(",") if k.strip()]:
        AREA_KEYWORDS.append((_d, _kw))
AREA_KEYWORDS.sort(key=lambda x: -len(x[1]))

def infer_district(explicit_district, preferred_districts, preferred_location):
    if explicit_district: return explicit_district
    if preferred_districts: return preferred_districts[0]
    loc = (preferred_location or "").lower()
    if not loc: return ""
    for d, kw in AREA_KEYWORDS:
        if kw in loc: return d
    return ""

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

listings = []
for l in land["landlords"]:
    av = availability(l)
    if av not in ("Available","Offer pending"): continue
    r = l.get("requirements") or {}
    listings.append({
        "id": l.get("id"), "name": l.get("landlord_name"), "availability": av,
        "district": l.get("district") or "", "address": l.get("full_address") or "",
        "map_query": maps_query(l.get("full_address"), l.get("district")),
        "rent_min": (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")),num(l.get("rent_max"))),
        "rent_max": (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")),num(l.get("rent_max"))),
        "viewing": l.get("viewing_availability") or "",
        "rooms": l.get("rooms_and_rent") or "", "property_type": l.get("property_type") or "",
        "listing_key": l.get("listing_key") or "", "phone": l.get("phone") or "",
        "follow_up": l.get("follow_up") or "", "last_contact": l.get("last_contact") or "",
        "source": src_of(l),
        "cea": cea_check(l, src_of(l)),
        "commission_est": commission_est(num(l.get("rent_min")), num(l.get("rent_max"))),
        "gates": {
            "max_pax": num(r.get("max_pax")),
            "lease_min": num(r.get("lease_min")) or num(r.get("lease_term")),
            "gender": parse_gender(r.get("gender")),
            "ethnicity": parse_ethnicity(r.get("ethnicity")),
            "occupation": r.get("occupation") or "", "cooking": r.get("cooking") or "",
            "pets": r.get("pets") or "", "smoking": r.get("smoking") or "",
        },
        "req_raw": {k: v for k, v in r.items() if v},
    })

STATUS_ORDER = {"Available":0, "Offer pending":1, "Pending":2, "Taken":3, "Off market":4}
all_landlords = []
for l in land["landlords"]:
    av = availability(l)
    pd = infer_district(l.get("district") or "", [], l.get("full_address") or "")
    all_landlords.append({
        "id": l.get("id"), "name": l.get("landlord_name"), "availability": av,
        "status_raw": l.get("status") or "", "sort": STATUS_ORDER.get(av, 9),
        "district": l.get("district") or "", "primary_district": pd, "address": l.get("full_address") or "",
        "map_query": maps_query(l.get("full_address"), l.get("district")),
        "rent_min": (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")),num(l.get("rent_max"))),
        "rent_max": (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")),num(l.get("rent_max"))),
        "viewing": l.get("viewing_availability") or "",
        "rooms": l.get("rooms_and_rent") or "", "property_type": l.get("property_type") or "",
        "phone": l.get("phone") or "", "last_contact": l.get("last_contact") or "",
        "follow_up": l.get("follow_up") or "",
        "source": src_of(l),
        "cea": cea_check(l, src_of(l)),
        "commission_est": commission_est(num(l.get("rent_min")), num(l.get("rent_max"))),
        "handed_off": handed_off(l),
    })
all_landlords.sort(key=lambda r: (r["sort"], r["primary_district"] or "zzz", r["name"] or ""))

# ---- sale listings (separate track from rentals — deal_type: sale / sale-or-rent) ----
# rent_min/rent_max are NOT usable for these: they hold mis-parsed artifacts from an
# upstream extraction bug (e.g. LL009 rent_min/max = 490/490 when the actual asking
# price is $505,000, per its own rooms_and_rent text). Parse the asking price from the
# free-text field instead; keep the raw text too since parsing SG price shorthand
# ("$505k", "505,000") from freeform notes is inherently best-effort.
def parse_price(txt):
    if not txt: return None
    nums = []
    for m in re.finditer(r"\$?\s?([\d,]{3,})\s*k\b", txt, re.I):
        nums.append(int(m.group(1).replace(",", "")) * 1000)
    for m in re.finditer(r"\$\s?([\d,]{5,})(?!\s*k)", txt):
        nums.append(int(m.group(1).replace(",", "")))
    return max(nums) if nums else None

SALE_STATUS_ORDER = {"Available":0, "Pending":1, "Closed":2}
def sale_status(status_raw):
    st = (status_raw or "").lower()
    if st.startswith("closed") or st.startswith("archived"): return "Closed"
    if st in ("sale-active", "active"): return "Available"
    return "Pending"

sales = []
for l in land["landlords"]:
    if (l.get("deal_type") or "") not in ("sale", "sale-or-rent"): continue
    txt = l.get("rooms_and_rent") or ""
    ss = sale_status(l.get("status"))
    pd = infer_district(l.get("district") or "", [], l.get("full_address") or "")
    sales.append({
        "id": l.get("id"), "name": l.get("landlord_name"), "sale_status": ss,
        "sort": SALE_STATUS_ORDER.get(ss, 9), "status_raw": l.get("status") or "",
        "district": l.get("district") or "", "primary_district": pd,
        "address": l.get("full_address") or "", "map_query": maps_query(l.get("full_address"), l.get("district")),
        "asking_price": parse_price(txt), "price_text": txt,
        "property_type": l.get("property_type") or "", "phone": l.get("phone") or "",
        "last_contact": l.get("last_contact") or "", "follow_up": l.get("follow_up") or "",
        "source": src_of(l),
        "cea": cea_check(l, src_of(l)),
    })
sales.sort(key=lambda r: (r["sort"], r["primary_district"] or "zzz", r["name"] or ""))

# duplicate phone numbers — same number saved as more than one landlord/tenant record,
# almost always a data-entry collision (dup contact, or a landlord who is also a tenant
# elsewhere) worth a human glance rather than silently treated as two separate people.
_phone_owners = {}
for l in all_landlords:
    if l["phone"]: _phone_owners.setdefault(l["phone"], []).append("LL:" + (l["name"] or l["id"]))
for t in ten["tenants"]:
    p = t.get("phone") or ""
    if p: _phone_owners.setdefault(p, []).append("TN:" + (t.get("name") or t.get("id")))
duplicate_phones = [{"phone": p, "owners": owners} for p, owners in _phone_owners.items() if len(owners) > 1]

tenants = []
for t in ten["tenants"]:
    if looking(t) != "Still looking": continue
    tenants.append({
        "id": t.get("id"), "name": t.get("name"), "status": t.get("status") or "",
        "preferred_location": t.get("preferred_location") or "",
        "preferred_districts": ([str(x).strip() for x in t["preferred_districts"] if str(x).strip()]
                                 if isinstance(t.get("preferred_districts"), list)
                                 else [d.strip() for d in (t.get("preferred_districts") or "").split(",") if d.strip()]),
        "district": t.get("district") or "",
        "budget": num(t.get("budget")), "budget_min": num(t.get("budget_min")), "budget_max": num(t.get("budget_max")),
        "pax": num(t.get("no_of_pax")), "gender": t.get("gender") or "", "ethnicity": t.get("ethnicity") or "",
        "nationality": t.get("nationality") or "", "pass_type": t.get("pass_type") or "",
        "occupation": t.get("occupation") or "", "move_in": t.get("move_in_date") or "",
        "lease_months": num(t.get("lease_term_months")), "phone": t.get("phone") or "",
        "last_contact": t.get("last_contact") or "", "listing_enquired": t.get("listing_enquired") or "",
        "wa_story": WA_STORY.get(t.get("id"), []),
    })

TENANT_STATUS_ORDER = {"Still looking":0, "Found":1, "Not looking":2}
all_tenants = []
for t in ten["tenants"]:
    lk = looking(t)
    budget = num(t.get("budget")) or num(t.get("budget_max"))
    raw_pd = ([str(x).strip() for x in t["preferred_districts"] if str(x).strip()]
              if isinstance(t.get("preferred_districts"), list)
              else [d.strip() for d in (t.get("preferred_districts") or "").split(",") if d.strip()])
    pd = infer_district(t.get("district") or "", raw_pd, t.get("preferred_location") or "")
    missing = []
    if not budget: missing.append("budget")
    if not t.get("move_in_date"): missing.append("move-in")
    if not num(t.get("no_of_pax")): missing.append("pax")
    if not num(t.get("lease_term_months")): missing.append("lease")
    if not pd: missing.append("district")
    all_tenants.append({
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
# district/location is now the PRIMARY grouping (Winfred: tenants were "all lumped
# together" under status alone) — status and data-completeness are secondary within
# each area group. Unmatched location falls into a final "zzz" bucket the UI labels
# "Unspecified location".
all_tenants.sort(key=lambda r: (r["primary_district"] or "zzz", r["sort"], len(r["missing"]) == 0, r["name"] or ""))

# ---- revival board (reuse revival_board.py's own scan, do not re-implement it) ----
# revival_board.py normally runs standalone and does
# `from export_data import availability, infer_district, looking, num` -- that only
# resolves when a module literally named "export_data" is already in sys.modules.
# When this file itself runs as __main__ (exactly how build.py invokes it) no such
# name exists yet, so alias this already-executing module in under that name before
# importing revival_board -- avoids a second, wasteful re-execution of this whole file.
import sys as _sys
_sys.modules.setdefault("export_data", _sys.modules[__name__])
import revival_board as _revival_board
_avail_for_revival = [{
    "id": l.get("id"), "name": l.get("landlord_name"), "district": l.get("district") or "",
    "rent_min": num(l.get("rent_min")), "rent_max": num(l.get("rent_max")),
} for l in land["landlords"] if availability(l) == "Available"]
_revival_rows = _revival_board.build_rows(ten["tenants"], _avail_for_revival, datetime.date.today())
revival = [{
    "name": r["name"], "phone": r["phone"], "days_quiet": r["dq"], "budget": r["budget"],
    "district": r["district"], "pax": r["pax"], "tier": r["tier"],
    "match": ({"id": r["match"]["id"], "name": r["match"]["name"], "district": r["match"]["district"],
               "rent_min": r["match"]["rent_min"], "rent_max": r["match"]["rent_max"]}
              if r["match"] else None),
} for r in _revival_rows]

data = {
    "generated": "2026-07-28",
    "priority": ["availability","location","price","landlord requirements"],
    "counts": {"available_listings": len(listings), "still_looking_tenants": len(tenants),
               "all_landlords": len(all_landlords), "all_tenants": len(all_tenants), "sales": len(sales)},
    "districts": DIST_AREA,
    "area_demand": [{"district":d["district"],"area":d["area"],"unmatched_waiting":d.get("unmatched_waiting"),
                     "supply_gap":d.get("supply_gap"),"sourcing_priority":d.get("sourcing_priority")}
                    for d in adem["districts"]],
    "listings": listings, "tenants": tenants, "all_landlords": all_landlords,
    "all_tenants": all_tenants, "duplicate_phones": duplicate_phones, "sales": sales,
    "revival": revival,
}
json.dump(data, open(OUT, "w"), ensure_ascii=False)
print("wrote", OUT)
print("listings:", len(listings), "| tenants:", len(tenants))
print("revival candidates:", len(revival))
print("sample listing gates:", json.dumps(listings[0]["gates"], ensure_ascii=False))
