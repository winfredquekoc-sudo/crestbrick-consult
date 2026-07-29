#!/usr/bin/env python3
"""
build_area_demand.py — fourth list for refresh-rental-dbs: demand by area.

Ranks Singapore districts by unmet tenant demand versus current active supply, so
Winfred knows WHERE to source new landlords. Demand counts only tenants who are still
contactable (contact_state active) and still looking (re-engageable status). Joins to
the other three lists through the shared `district` key.

Usage: build_area_demand.py [--apply]
"""
import json, os, sys, csv, importlib.util, statistics

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "crestbrick-consult")
spec = importlib.util.spec_from_file_location("blm", os.path.join(ROOT, "scripts/build_tenant_location_map.py"))
blm = importlib.util.module_from_spec(spec); spec.loader.exec_module(blm)
APPLY = "--apply" in sys.argv

AREA = {
 "D1":"City, Marina, Raffles","D2":"Tanjong Pagar, Shenton","D3":"Tiong Bahru, Queenstown, Redhill",
 "D4":"Harbourfront, Sentosa, Telok Blangah","D5":"Clementi, Buona Vista, NUS, Dover","D6":"City Hall",
 "D7":"Bugis, Beach Road","D8":"Lavender, Farrer Park, Jellicoe, Little India","D9":"Orchard, River Valley",
 "D10":"Bukit Timah, Holland, Tanglin","D11":"Newton, Novena, Thomson","D12":"Toa Payoh, Balestier, Kallang",
 "D13":"Macpherson, Potong Pasir","D14":"Geylang, Paya Lebar, Eunos","D15":"Katong, Marine Parade, East Coast",
 "D16":"Bedok, Bayshore","D17":"Changi, Loyang","D18":"Tampines, Pasir Ris, Simei",
 "D19":"Hougang, Sengkang, Punggol","D20":"Bishan, Ang Mo Kio, Upper Thomson","D21":"Clementi Park, Ulu Pandan",
 "D22":"Jurong, Boon Lay, Lakeside","D23":"Bukit Batok, Choa Chu Kang, Hillview","D24":"Tengah, Lim Chu Kang",
 "D25":"Woodlands, Admiralty","D26":"Upper Thomson, Lentor, Mandai","D27":"Yishun, Sembawang","D28":"Seletar, Yio Chu Kang",
}
REENGAGE = {"rejected","cold","form-sent","profile-received","viewed","needs_info"}

def main():
    tdb = json.load(open(os.path.join(ROOT,"_templates/tenant-db.json")))
    ldb = json.load(open(os.path.join(ROOT,"_templates/landlord-db.json")))
    idx = blm.listing_entries()
    ll_status = {x["id"]: str(x.get("status","")).lower() for x in ldb["landlords"]}

    # active listings per district (verified landlord, not closed)
    supply = {}
    for lk,(area,dist) in blm.LISTING_DISTRICT.items():
        e = idx.get(lk,{}); llid = e.get("landlord_id")
        if llid and not ll_status.get(llid,"").startswith("closed"):
            supply[dist] = supply.get(dist,0)+1

    rows_by_d = {}
    for x in tdb["tenants"]:
        if x.get("excluded"): continue
        _l = x.get("lease_term_months")
        active_looker = (x.get("status") in REENGAGE) and (x.get("contact_state")=="active") \
                        and not (isinstance(_l,(int,float)) and _l < 6) \
                        and x.get("match_status") not in ("excluded_india","excluded_family")
        for d in (x.get("preferred_districts") or []):
            r = rows_by_d.setdefault(d, {"waiting":[], "total":0})
            r["total"] += 1
            if active_looker:
                r["waiting"].append(x)

    # who is already served = has at least one fitting active listing (a suggestion in the map)
    mp = json.load(open(os.path.join(ROOT,"_templates/tenant-location-map.json")))
    served = {r["id"]: r.get("suggested_count",0) for r in mp.get("tenants",[])}

    rows = []
    for d, r in rows_by_d.items():
        waiting = r["waiting"]
        unmatched = sum(1 for t in waiting if served.get(t.get("id"),0) == 0)
        budgets = sorted(b for b in (t.get("budget") for t in waiting) if isinstance(b,(int,float)))
        band = f"{budgets[0]} to {budgets[-1]} (median {int(statistics.median(budgets))})" if budgets else ""
        sup = supply.get(d,0); nwait = len(waiting)
        # priority by UNMET demand (waiting tenants with NO fitting active listing), not
        # just whether a listing exists. A district can hold listings and still be a strong
        # sourcing target if those listings do not fit the queue (Jurong: Caspian is 1 pax
        # non Indian only, Summerdale floor is high, so most of the 25 stay unmatched).
        if unmatched >= 5: pri = "HIGH source now"
        elif unmatched >= 3: pri = "MEDIUM"
        elif unmatched >= 1: pri = "LOW"
        elif nwait > 0: pri = "served"
        else: pri = "no active demand"
        names = [t.get("name") or t.get("id") for t in sorted(waiting, key=lambda t:-(t.get("budget") or 0)) if served.get(t.get("id"),0)==0][:4]
        rows.append({"district":d,"area":AREA.get(d,d),"waiting_active":nwait,"unmatched_waiting":unmatched,
                     "total_interested":r["total"],"active_listings":sup,"supply_gap":nwait-sup,"budget_band":band,
                     "sourcing_priority":pri,"sample_tenants":", ".join(str(n) for n in names)})

    order = {"HIGH source now":0,"MEDIUM":1,"LOW":2,"served":3,"no active demand":4}
    rows.sort(key=lambda r:(order.get(r["sourcing_priority"],9), -r["unmatched_waiting"]))

    print(f"{'PRI':<16}{'DIST':<5}{'UNMET':>6}{'WAIT':>5}{'SUPP':>5}  AREA / budget")
    for r in rows[:14]:
        print(f"{r['sourcing_priority']:<16}{r['district']:<5}{r['unmatched_waiting']:>6}{r['waiting_active']:>5}{r['active_listings']:>5}  {r['area']} | {r['budget_band']}")

    if not APPLY:
        print("\nDRY RUN. Re-run with --apply to write the area demand list.")
        return rows
    out = {"source":"build_area_demand.py; demand = contactable, still-looking tenants per preferred district",
           "districts": rows}
    tmp = os.path.join(ROOT,"_templates/area-demand.json")
    json.dump(out, open(tmp+".tmp","w"), indent=1, ensure_ascii=False); os.replace(tmp+".tmp", tmp)
    with open(os.path.join(ROOT,"docs/area-demand.csv"),"w",newline="") as f:
        cols=("district","area","sourcing_priority","unmatched_waiting","waiting_active","active_listings","total_interested","budget_band","sample_tenants")
        w = csv.writer(f); w.writerow(cols)
        for r in rows: w.writerow([r[k] for k in cols])
    print(f"\nWROTE _templates/area-demand.json and docs/area-demand.csv ({len(rows)} districts)")
    return rows

if __name__ == "__main__":
    main()
