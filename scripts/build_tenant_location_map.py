#!/usr/bin/env python3
"""
build_tenant_location_map.py — third database for refresh-rental-dbs.

Derives each prospective tenant's preferred Singapore district(s) from their free text
preferred_location, then builds a location map + re-engagement suggestions: for tenants
who were rejected or went cold, which ACTIVE listings near their preferred area we can
introduce them to (or notify when a new rental opens there).

Deterministic only (a fixed keyword to district map), so no fabricated districts.

Usage:
  python3 build_tenant_location_map.py            # dry run, prints, writes nothing
  python3 build_tenant_location_map.py --apply    # writes the files
"""
import json, os, sys, csv, re, datetime

HOME = os.path.expanduser("~")
TDB  = os.path.join(HOME, "crestbrick-consult/_templates/tenant-db.json")
LDB  = os.path.join(HOME, "crestbrick-consult/_templates/landlord-db.json")
IDX  = os.path.join(HOME, ".claude/state/listing-templates/listing-index.json")
MAPJSON = os.path.join(HOME, "crestbrick-consult/_templates/tenant-location-map.json")
MAPCSV  = os.path.join(HOME, "crestbrick-consult/docs/tenant-location-map.csv")
APPLY = "--apply" in sys.argv

# Listings are few and known: map listing_key -> (area label, district). Authoritative.
LISTING_DISTRICT = {
    "jurong-west-905": ("Jurong West St 91, Pioneer", "D22"),
    "caspian": ("Lakeside, Jurong West", "D22"),
    "summerdale": ("Jurong West, Boon Lay", "D22"),
    "bedok-north-522": ("Bedok North", "D16"),
    "bayshore": ("Bayshore, Bedok South", "D16"),
    "tampines-855": ("Tampines St 83", "D18"),
    "tampines-201": ("Tampines St 22", "D18"),
    "tampines-west-951b": ("Tampines West, St 96", "D18"),
    "watercolours": ("Pasir Ris", "D18"),
    "pasir-ris-151": ("Pasir Ris", "D18"),
    "hougang-358": ("Hougang Ave 5", "D19"),
    "hougang-703": ("Hougang Ave 2", "D19"),
    "rivervale-185c": ("Rivervale, Sengkang", "D19"),
    "sumang-lane-232b": ("Sumang, Punggol", "D19"),
    "edgefield-104b": ("Edgefield, Punggol", "D19"),
    "sunshine-terrace": ("Sunrise Terrace, Seletar Hills", "D28"),
    "oxley-edge": ("Oxley, Orchard", "D9"),
    "farrer-park": ("Farrer Park", "D8"),
    "tampines-606d": ("Tampines St 61", "D18"),
    "simei-lane-168a": ("Simei Lane", "D18"),
    "eastpoint-green": ("Simei, Eastpoint", "D18"),
    "yishun-466a": ("Yishun Ave 6", "D27"),
}

# keyword (lowercase, matched as substring) -> list of district codes.
TOWN_DISTRICT = {
    "clementi":["D5"],"buona vista":["D5"],"west coast":["D5"],"pasir panjang":["D5"],
    "nus":["D5"],"dover":["D5"],"one-north":["D5"],"one north":["D5"],"essec":["D5"],
    "holland":["D10"],"bukit timah":["D10"],"tanglin":["D10"],"beauty world":["D21"],
    "ulu pandan":["D21"],"clementi park":["D21"],
    "newton":["D11"],"novena":["D11"],"thomson":["D11"],
    "toa payoh":["D12"],"balestier":["D12"],"kallang":["D12"],"boon keng":["D12"],
    "macpherson":["D13"],"potong pasir":["D13"],
    "geylang":["D14"],"paya lebar":["D14"],"eunos":["D14"],"aljunied":["D14"],"dakota":["D14"],
    "katong":["D15"],"marine parade":["D15"],"east coast":["D15"],"tanjong katong":["D15"],"mountbatten":["D15"],
    "bedok":["D16"],"bayshore":["D16"],"tanah merah":["D16"],"kembangan":["D16"],
    "changi":["D17"],"loyang":["D17"],"airport":["D17"],
    "tampines":["D18"],"pasir ris":["D18"],"simei":["D18"],"changi business park":["D18"],
    "hougang":["D19"],"sengkang":["D19"],"punggol":["D19"],"kovan":["D19"],"serangoon":["D19"],"buangkok":["D19"],
    "bishan":["D20"],"ang mo kio":["D20"],"amk":["D20"],"braddell":["D20"],"sin ming":["D20"],"marymount":["D20"],
    "bukit batok":["D23"],"bukit panjang":["D23"],"choa chu kang":["D23"],"cck":["D23"],"hillview":["D23"],"the warren":["D23"],
    "jurong":["D22"],"boon lay":["D22"],"lakeside":["D22"],"pioneer":["D22"],"chinese garden":["D22"],"ntu":["D22"],"caspian":["D22"],"taman jurong":["D22"],"jurong east":["D22"],"jurong west":["D22"],
    "woodlands":["D25"],"admiralty":["D25"],"marsiling":["D25"],
    "yishun":["D27"],"sembawang":["D27"],"canberra":["D27"],
    "seletar":["D28"],"yio chu kang":["D28"],
    "city hall":["D6"],"raffles":["D1"],"marina":["D1"],"chinatown":["D1"],"outram":["D1"],
    "tanjong pagar":["D2"],"shenton":["D2"],"tras":["D2"],
    "tiong bahru":["D3"],"queenstown":["D3"],"redhill":["D3"],"alexandra":["D3"],
    "harbourfront":["D4"],"sentosa":["D4"],"telok blangah":["D4"],
    "orchard":["D9"],"river valley":["D9"],"somerset":["D9"],"oxley":["D9"],"killiney":["D9"],
    "bugis":["D7"],"beach road":["D7"],"middle road":["D7"],
    "lavender":["D8"],"jellicoe":["D8"],"farrer park":["D8"],"little india":["D8"],"jalan besar":["D8"],"smu":["D6"],
    # report-driven additions 2026-06-16 (from demand-supply-gap audit)
    "pine grove":["D5","D21"],"edgefield":["D19"],"city square":["D8"],
    # db-tighten additions 2026-07-11
    "tengah":["D24"],"bidadari":["D13"],"kim keat":["D12"],"anchorvale":["D19"],
    "rivervale":["D19"],"sumang":["D19"],"eastpoint":["D18"],"terrasse":["D19"],
    "yew tee":["D23"],"rio vista":["D19"],"regent heights":["D23"],"casa spring":["D27"],
    "boathouse":["D19"],"circuit road":["D14"],"ubi":["D14"],"bendemeer":["D12"],
    "clementi west":["D5"],"sunrise terrace":["D28"],"seletar hills":["D28"],
    "haw par villa":["D5"],"mbs":["D1"],"marina bay sands":["D1"],"embassy of portugal":["D10"],
    # broad fallbacks
    "town":["D9","D10","D11"],"central":["D9","D10","D11"],
    "east":["D15","D16","D18"],"west":["D5","D22","D23"],"north":["D25","D27","D20"],
}

def districts_for(text):
    t = (text or "").lower()
    found = []
    for kw, ds in TOWN_DISTRICT.items():
        if kw in t:
            for d in ds:
                if d not in found:
                    found.append(d)
    return found

def load(p):
    with open(p) as f: return json.load(f)

def listing_entries():
    d = load(IDX)
    items = d if isinstance(d, list) else (d.get("listings") if isinstance(d.get("listings"), list) else list(d.values()))
    out = {}
    for l in items:
        if isinstance(l, dict) and l.get("listing_key"):
            out[l["listing_key"]] = l
    return out

def main():
    tdb = load(TDB)
    ldb = load(LDB)
    idx = listing_entries()

    # active listings = landlord status not closed (listings with no landlord_id treated active)
    ll_status = {x["id"]: x.get("status","") for x in ldb["landlords"]}
    active = {}
    for lk, meta in LISTING_DISTRICT.items():
        e = idx.get(lk, {})
        llid = e.get("landlord_id")
        st = ll_status.get(llid, "") if llid else ""
        # active only if it has a verified (non None) landlord and is not closed.
        # listings on hold (no verified landlord record) are NOT suggested.
        is_active = bool(llid) and not str(st).startswith("closed")
        reqs = e.get("requirements", e) if isinstance(e, dict) else {}
        active[lk] = {
            "listing_key": lk, "area": meta[0], "district": meta[1],
            "active": is_active,
            "budget_floor": (reqs or {}).get("budget_floor"),
            "rent": e.get("asking_rent") or (reqs or {}).get("budget_floor"),
        }

    REENGAGE = {"rejected","cold","form-sent","profile-received","viewed","needs_info","needs_info_unknowns"}
    rows = []
    dist_count = {}
    suggested_total = 0
    for x in tdb["tenants"]:
        pd = districts_for(x.get("preferred_location"))
        x["preferred_districts"] = pd
        x["district"] = pd[0] if pd else ""
        for d in pd: dist_count[d] = dist_count.get(d,0)+1
        if x.get("status") not in REENGAGE:
            continue
        if x.get("contact_state") in ("found_place","not_interested","do_not_contact"):
            continue   # anti-spam: never re-engage someone who found a place or opted out
        _l = x.get("lease_term_months")
        if isinstance(_l,(int,float)) and _l < 6:
            continue   # filter out sub 6 month seekers; landlords want 12 months minimum
        if x.get("match_status") in ("excluded_india","excluded_family"):
            continue   # Winfred policy: not served
        # build suggestions: active listings in a preferred district, not the one enquired,
        # not already FAILed in matches, budget compatible if both known.
        failed = {m.get("listing_key") for m in (x.get("matches") or []) if m.get("verdict")=="FAIL"}
        matched = {m.get("listing_key") for m in (x.get("matches") or []) if m.get("verdict")=="MATCH"}
        enq = (x.get("listing_enquired") or "").lower()
        budget = x.get("budget")
        sugg = []
        for lk, L in active.items():
            if not L["active"]: continue
            if not pd or L["district"] not in pd: continue
            if lk in failed: continue
            if lk in enq: continue          # already enquired on this exact listing
            bf = L["budget_floor"]
            try:
                budget_f = float(str(budget).replace('$','').replace(',','').split('/')[0].split('-')[0].strip()) if budget else None
            except (ValueError, AttributeError):
                budget_f = None
            if budget_f and bf and budget_f < float(bf): continue
            reason = "matches profile" if lk in matched else "near preferred area " + L["district"]
            sugg.append({"listing_key":lk,"area":L["area"],"district":L["district"],
                         "rent":L["rent"],"reason":reason,"priority": 0 if lk in matched else 1})
        sugg.sort(key=lambda s:(s["priority"], s["district"]))
        suggested_total += len(sugg)
        rows.append({
            "id": x.get("id"), "name": x.get("name"), "status": x.get("status"),
            "preferred_location": x.get("preferred_location"),
            "preferred_districts": ", ".join(pd),
            "budget": budget, "no_of_pax": x.get("no_of_pax"),
            "listing_enquired": x.get("listing_enquired"),
            "suggested_count": len(sugg),
            "suggestions": sugg,
        })

    rows.sort(key=lambda r:(-r["suggested_count"], r["status"]))
    with_sugg = [r for r in rows if r["suggested_count"]]

    print(f"tenants total {len(tdb['tenants'])}; re-engage candidates {len(rows)}; with a nearby suggestion {len(with_sugg)}; total suggestions {suggested_total}")
    print("district spread (preferred):", dict(sorted(dist_count.items(), key=lambda kv:-kv[1])))
    print("\nTOP re-engagement suggestions (sample):")
    for r in with_sugg[:12]:
        s = "; ".join(f"{x['listing_key']} ({x['area']}, {x['district']}) {x['reason']}" for x in r["suggestions"][:3])
        print(f"  {r['id']} {r['name']} [{r['status']}] wants {r['preferred_districts']} (bud {r['budget']}) -> {s}")

    if not APPLY:
        print("\nDRY RUN. Re-run with --apply to write tenant-db district columns + the location map.")
        return

    # update columns list
    cols = tdb.get("columns", [])
    for c in ("district","preferred_districts"):
        if c not in cols: cols.append(c)
    tdb["columns"] = cols
    tmp = TDB + ".tmp"; json.dump(tdb, open(tmp,"w"), indent=1, ensure_ascii=False); os.replace(tmp, TDB)

    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    mp = {"generated": now,
          "source": "build_tenant_location_map.py; districts derived deterministically from preferred_location",
          "re_engage_candidates": len(rows), "with_suggestion": len(with_sugg),
          "district_spread": dict(sorted(dist_count.items(), key=lambda kv:-kv[1])),
          "tenants": rows}
    tmp = MAPJSON + ".tmp"; json.dump(mp, open(tmp,"w"), indent=1, ensure_ascii=False); os.replace(tmp, MAPJSON)

    with open(MAPCSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id","name","status","preferred_location","preferred_districts","budget","no_of_pax","listing_enquired","suggested_count","suggestions"])
        for r in rows:
            s = "; ".join(f"{x['listing_key']} ({x['area']}, {x['district']}, {x['reason']})" for x in r["suggestions"])
            w.writerow([r["id"],r["name"],r["status"],r["preferred_location"],r["preferred_districts"],
                        r["budget"],r["no_of_pax"],r["listing_enquired"],r["suggested_count"],s])
    print(f"\nWROTE: {TDB} (district cols), {MAPJSON}, {MAPCSV}")

if __name__ == "__main__":
    main()
