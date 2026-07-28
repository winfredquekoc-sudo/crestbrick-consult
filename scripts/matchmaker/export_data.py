#!/usr/bin/env python3
"""Export ONE compact JSON for the interactive matchmaker app: available listings +
still-looking tenants, with structured gates so the app can score matches for ALL
available listings (not only the 5 the engine covers). No blanks lost; PII stays local."""
import json, os, re

ROOT = os.path.expanduser("~/crestbrick-consult")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchmaker-data.json")
land = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
ten  = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
adem = json.load(open(os.path.join(ROOT, "_templates/area-demand.json")))
DIST_AREA = {d["district"]: d["area"] for d in adem["districts"]}

def availability(l):
    if l.get("offer_pending"): return "Offer pending"
    st = (l.get("status") or "").lower()
    if st.startswith("closed (tenanted") or st.startswith("closed (unavailable"): return "Taken"
    if st.startswith("closed") or st.startswith("archived") or st.startswith("cold"): return "Off market"
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
        "follow_up": l.get("follow_up") or "",
        "source": ("co-broke" if "co-broke" in (l.get("contact_label_source") or "").lower() else "own"),
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
    })

data = {
    "generated": "2026-07-28",
    "priority": ["availability","location","price","landlord requirements"],
    "counts": {"available_listings": len(listings), "still_looking_tenants": len(tenants)},
    "districts": DIST_AREA,
    "area_demand": [{"district":d["district"],"area":d["area"],"unmatched_waiting":d.get("unmatched_waiting"),
                     "supply_gap":d.get("supply_gap"),"sourcing_priority":d.get("sourcing_priority")}
                    for d in adem["districts"]],
    "listings": listings, "tenants": tenants,
}
json.dump(data, open(OUT, "w"), ensure_ascii=False)
print("wrote", OUT)
print("listings:", len(listings), "| tenants:", len(tenants))
print("sample listing gates:", json.dumps(listings[0]["gates"], ensure_ascii=False))
