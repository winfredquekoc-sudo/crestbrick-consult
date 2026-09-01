#!/usr/bin/env python3
"""Build the PUBLIC room-rental site (separate from the private Matchmaker).

Static HTML for SEO + GEO: a homepage, one page per room, one page per area,
sitemap.xml, robots.txt and llms.txt. Data is redacted at build time — the
private bits are never written into the pages:

  PUBLIC:  area, district, block + street (no unit), rent, room type, photos,
           and only neutral rules (cooking, pets, max pax, lease, smoking,
           utilities, furnishing, available date). Contact is always Winfred.
  NEVER:   landlord name or phone, unit number, postal, race / gender /
           nationality preference, the free-text notes (they carry names and
           commission), and every tenant — no client of Winfred's appears here.

Reads scripts/matchmaker/matchmaker-data.json; writes scripts/publicsite/dist/.
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))   # scripts/
import mrt_stations
DATA = os.path.join(REPO, "scripts", "matchmaker", "matchmaker-data.json")
DIST = os.path.join(HERE, "dist")
PHOTOS_SRC = os.path.join(REPO, "scripts", "matchmaker", "deploy", "photos")

SITE_NAME = "Singapore Room Rental"
BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://singapore-room-rental.vercel.app").rstrip("/")
AGENT = "Winfred Quek"
AGENT_CEA = "R073319H"
AGENT_FIRM = "Crestbrick Pte Ltd (L31010886H)"
WA = "6581618149"   # Winfred's WhatsApp — the ONLY contact on the site
WA_LINK = "https://wa.me/%s" % WA
MAIN_SITE = "https://winfredquek.com"   # cross-site: renters → future buyers
INDEXNOW_KEY = "8f3c1e6a2b9d4f70a5c8e1b3d6f2a9c4"   # PUBLIC IndexNow ownership token, hosted at /<key>.txt — not a secret — gitleaks:allow

# Rough district-centre coordinates for the map (good enough for area pins).
DISTRICT_LATLNG = {
    "D1": (1.283, 103.851), "D2": (1.276, 103.845), "D3": (1.287, 103.83),
    "D4": (1.267, 103.82), "D5": (1.291, 103.784), "D6": (1.289, 103.851),
    "D7": (1.303, 103.858), "D8": (1.311, 103.856), "D9": (1.306, 103.833),
    "D10": (1.31, 103.81), "D11": (1.324, 103.84), "D12": (1.327, 103.858),
    "D13": (1.336, 103.874), "D14": (1.318, 103.892), "D15": (1.305, 103.905),
    "D16": (1.323, 103.93), "D17": (1.359, 103.95), "D18": (1.354, 103.943),
    "D19": (1.362, 103.882), "D20": (1.357, 103.836), "D21": (1.338, 103.78),
    "D22": (1.333, 103.742), "D23": (1.38, 103.762), "D24": (1.4, 103.72),
    "D25": (1.43, 103.78), "D26": (1.38, 103.83), "D27": (1.42, 103.83),
    "D28": (1.4, 103.87),
}


# Programmatic-SEO landing pages: by price and by room type.
PRICE_BUCKETS = [("under-900", "Under $900", 900), ("under-1200", "Under $1,200", 1200),
                 ("under-1500", "Under $1,500", 1500), ("under-2000", "Under $2,000", 2000)]
ROOM_TYPE_PAGES = [("master", "Master Rooms", "master-rooms-for-rent-singapore", "master room"),
                   ("common", "Common Rooms", "common-rooms-for-rent-singapore", "common room"),
                   ("whole", "Whole Units", "whole-units-for-rent-singapore", "whole unit"),
                   ("studio", "Studio Units", "studio-for-rent-singapore", "studio")]


GEOCACHE = os.path.join(HERE, "geocode-cache.json")


def geo_query_from(addr, block, area):
    """Best geocode query for a listing: postal code if present, else block+street."""
    m = re.search(r"(\d{6})\b", str(addr or ""))
    if m:
        return m.group(1)
    b = re.sub(r"withheld|full addr|unit.*?tbc|confirm|reference.*|blk", "", str(block or ""), flags=re.I).strip(" ,")
    return b or area


def onemap(q, cache):
    """Geocode a Singapore address/postal via OneMap (free, no key). Cached + throttled."""
    import time
    import urllib.request
    import urllib.parse
    if not q:
        return None
    if q in cache:
        return cache[q]
    url = "https://www.onemap.gov.sg/api/common/elastic/search?" + urllib.parse.urlencode(
        {"searchVal": q, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1})
    for attempt in range(3):
        try:
            r = json.load(urllib.request.urlopen(url, timeout=20))
            res = r.get("results") or []
            cache[q] = [float(res[0]["LATITUDE"]), float(res[0]["LONGITUDE"])] if res else None
            return cache[q]
        except Exception:
            time.sleep(1.5)
    return None


def _load_content(name, default):
    try:
        return json.load(open(os.path.join(HERE, "content", name)))
    except Exception:
        return default
AREA_GUIDES = _load_content("area-guides.json", {})   # slug -> {name, district, text}
FAQS = _load_content("faqs.json", [])                 # [{q, a}]
GUIDE_FOOTER = "".join(' &middot; <a href="/rooms-in-%s/">%s</a>' % (s, html.escape(g["name"]))
                       for s, g in list(AREA_GUIDES.items())[:8])


def e(s):
    return html.escape(str(s if s is not None else ""))


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(s or "").lower())).strip("-")


def strip_unit(addr):
    a = re.sub(r"#\d+-\d+[A-Za-z]?", "", str(addr or ""))
    a = re.sub(r"\bS\d{6}\b", "", a)          # drop postal
    a = re.sub(r"\bSingapore\s*\d{6}\b", "", a, flags=re.I)
    a = re.sub(r"\(.*?\)", "", a)             # drop parenthetical notes
    a = re.sub(r"\s{2,}", " ", a).replace(" ,", ",").strip(" ,")
    return a


def room_type_label(units):
    types = [u.get("unit_type") for u in (units or []) if u.get("unit_type")]
    if "whole" in types:
        return "Whole unit"
    if "master" in types and "common" in types:
        return "Rooms"
    if "master" in types:
        return "Master room"
    if "studio" in types:
        return "Studio"
    if "common" in types:
        return "Common room"
    return "Room"


def rent_text(lo, hi):
    if lo and hi and lo != hi:
        return "$%s to $%s" % ("{:,}".format(lo), "{:,}".format(hi))
    v = lo or hi
    return "$%s" % "{:,}".format(v) if v else "Ask"


def neutral_rules(reqs, cooking):
    """Only the non-discriminatory rules are public."""
    r = reqs or {}
    out = []
    if cooking and cooking != "Ask landlord":
        out.append(("Cooking", cooking))
    if r.get("pets"):
        out.append(("Pets", r["pets"]))
    if r.get("max_pax"):
        out.append(("Max occupants", str(r["max_pax"])))
    lo, hi = r.get("lease_min"), r.get("lease_max")
    if lo and hi:
        out.append(("Lease", "%s to %s months" % (lo, hi)))
    elif lo:
        out.append(("Lease", "%s months min" % lo))
    if r.get("smoking"):
        out.append(("Smoking", r["smoking"]))
    if r.get("utilities"):
        out.append(("Utilities", r["utilities"]))
    return out


def load_public_listings():
    d = json.load(open(DATA))
    areas = d.get("districts", {})
    out = []
    for l in d.get("listings", []):
        if l.get("availability") != "Available":
            continue
        dist = l.get("district") or ""
        area = areas.get(dist, dist)
        block = strip_unit(l.get("address"))
        if not block:
            block = area
        rtype = room_type_label(l.get("units"))
        utypes = [u.get("unit_type") for u in (l.get("units") or [])]
        type_key = ("whole" if "whole" in utypes else "studio" if "studio" in utypes
                    else "master" if "master" in utypes else "common" if "common" in utypes else "room")
        lid = l.get("id")
        photos = l.get("photos") or []
        # only local (login-free) room photos are safe to republish; website
        # photos already public are fine too, but keep it to our harvested set.
        photos = [p for p in photos if str(p).startswith("photos/")]
        # PUBLIC_PHOTOS=0 launches the site WITHOUT landlord room photos, pending
        # Winfred's explicit ok to publish them (28 Aug 2026).
        if os.environ.get("PUBLIC_PHOTOS", "1") == "0":
            photos = []
        out.append({
            "id": lid,
            "district": dist,
            "area": area,
            "area_slug": slugify(area.split(",")[0]) or slugify(dist),
            "area_short": area.split(",")[0].strip(),
            "block": block,
            "_geoq": geo_query_from(l.get("address"), block, area),
            "days_listed": l.get("days_listed"),
            "is_new": (l.get("days_listed") is not None and l.get("days_listed") <= 7),
            "rtype": rtype,
            "type_key": type_key,
            "rent_min": l.get("rent_min"),
            "rent_max": l.get("rent_max"),
            "rent_txt": rent_text(l.get("rent_min"), l.get("rent_max")),
            "rules": neutral_rules(l.get("reqs"), l.get("cooking")),
            "photos": photos,
            "lat": l.get("lat"),
            "lng": l.get("lng"),
            "geo_src": l.get("geo_src"),
            "available_from": l.get("available_from"),
            "slug": "%s-%s-%s" % (slugify(area.split(",")[0]) or slugify(dist),
                                  slugify(rtype), slugify(lid)),
        })
    return out, areas


# ------------------------------------------------------------------ HTML ----
CSS = """
:root{--navy:#0f1e3d;--navy2:#152a52;--gold:#c8a24a;--ink:#eef2fb;--mut:#9fb0d0;--line:#26385f;--card:#132546}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--navy);color:var(--ink);line-height:1.55}
a{color:var(--gold);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 18px}
header.site{border-bottom:1px solid var(--line);background:var(--navy2)}
.nav{display:flex;align-items:center;justify-content:space-between;padding:14px 0;flex-wrap:wrap;gap:8px}
.brand{font-weight:800;font-size:19px;color:var(--ink)}.brand b{color:var(--gold)}
.cta{background:var(--gold);color:#1a1300;padding:9px 16px;border-radius:8px;font-weight:700;display:inline-block}
.cta:hover{text-decoration:none;filter:brightness(1.06)}
.hero{padding:46px 0 30px;text-align:center}
.hero h1{font-size:34px;margin:0 0 10px;line-height:1.2}
.hero p{color:var(--mut);font-size:17px;max-width:640px;margin:0 auto 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px;margin:22px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card .cardlink{display:flex;flex-direction:column;flex:1;color:inherit}
.card .ph{aspect-ratio:4/3;object-fit:cover;width:100%;background:#0b1730;display:block}
.card .noph{aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;color:var(--mut);background:#0b1730;font-size:13px}
.card .bd{padding:12px 13px;flex:1;display:flex;flex-direction:column;gap:6px}
.card h3{margin:0;font-size:15px}.card .price{color:var(--gold);font-weight:800;font-size:17px}
.card .wabtn{display:flex;align-items:center;justify-content:center;gap:6px;background:#1e9d5a;color:#fff;font-weight:700;font-size:13px;padding:10px;border-top:1px solid var(--line)}
.card .wabtn:hover{filter:brightness(1.08);text-decoration:none}
.navlinks{display:flex;gap:16px;font-size:14px;flex:1 1 auto;justify-content:center}
.navlinks a{color:var(--ink)}
.chip{display:inline-block;background:#0d1c3a;border:1px solid var(--line);border-radius:20px;padding:2px 9px;font-size:11px;color:var(--mut);margin:2px 3px 0 0}
.sec{padding:26px 0;border-top:1px solid var(--line)}
.sec h2{font-size:23px;margin:0 0 12px}
.areas{display:flex;flex-wrap:wrap;gap:8px}
.areas a{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--ink);font-size:14px}
#map{height:360px;border-radius:12px;border:1px solid var(--line);margin:8px 0;background:#0b1730}
.leaflet-tile-pane{filter:invert(1) hue-rotate(185deg) brightness(.92) contrast(.9)}
.leaflet-container{background:#0b1730}
.leaflet-popup-content{font-size:13px;line-height:1.5}
.leaflet-popup-content a{color:#1a1300;font-weight:700}
.srrpin{background:none;border:none}.srrpin svg{filter:drop-shadow(0 2px 2px rgba(0,0,0,.45))}
.srrtown{background:none;border:none}
.srrtownlbl{position:absolute;transform:translate(-50%,-165%);white-space:nowrap;background:rgba(11,23,48,.85);color:var(--ink);border:1px solid var(--gold);border-radius:11px;padding:1px 9px;font-size:11px;font-weight:700;letter-spacing:.2px;box-shadow:0 1px 4px rgba(0,0,0,.55);pointer-events:none}
.srrtownlbl b{color:var(--gold);margin-left:1px}
.srrmapfilter{display:flex;flex-wrap:wrap;gap:7px;margin:8px 0 2px}
.srrchip{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:6px 13px;font-size:13px;font-weight:600;cursor:pointer;transition:background .12s,border-color .12s}
.srrchip:hover{border-color:var(--gold)}
.srrchip.on{background:var(--gold);color:#1a1300;border-color:var(--gold)}
.detail{display:grid;grid-template-columns:1.4fr 1fr;gap:24px;padding:24px 0}
@media(max-width:760px){.detail{grid-template-columns:1fr}.hero h1{font-size:27px}}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.gallery img{width:100%;border-radius:10px;border:1px solid var(--line)}
.rules{list-style:none;padding:0;margin:10px 0}.rules li{padding:6px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px}
.rules .k{color:var(--mut)}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.faq details{border-top:1px solid var(--line);padding:10px 0}.faq summary{cursor:pointer;font-weight:600}
footer.site{border-top:1px solid var(--line);color:var(--mut);font-size:13px;padding:22px 0;margin-top:20px}
.crumbs{font-size:13px;color:var(--mut);padding:14px 0}.crumbs a{color:var(--mut)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0;align-items:center}
.filters select,.filters input{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:14px}
.card .meta{display:flex;flex-wrap:wrap;gap:4px}
.stickycta{position:fixed;left:0;right:0;bottom:0;background:var(--gold);color:#1a1300;text-align:center;padding:13px;font-weight:800;display:none;z-index:60}
.stickycta:hover{text-decoration:none}
@media(max-width:760px){.stickycta{display:block}body{padding-bottom:54px}}
.trust{display:flex;flex-wrap:wrap;gap:20px;justify-content:center;color:var(--mut);font-size:14px;margin:6px 0 2px}.trust b{color:var(--ink)}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.pills a{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:6px 13px;font-size:13px;color:var(--ink)}
.noresult{color:var(--mut);padding:24px 0;text-align:center}
.intro{color:var(--mut);max-width:680px;margin:0 0 14px}
.new{background:#2ea06a;color:#04120b;font-size:9px;font-weight:800;padding:1px 6px;border-radius:5px;vertical-align:middle;letter-spacing:.4px}
.age{color:var(--mut);font-size:10px}
.card{transition:transform .15s ease,box-shadow .15s ease}
.card:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(0,0,0,.35)}
.resultcount{color:var(--mut);font-size:13px;margin:2px 0 10px}
.townlist{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin:18px 0}
.towncard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;color:inherit}
.towncard:hover{text-decoration:none;border-color:var(--gold)}
.towncard h3{margin:0 0 4px;font-size:16px}
.towncard .n{color:var(--mut);font-size:13px}
.towncard .stock{display:inline-block;margin-top:6px;font-size:11px;padding:2px 8px;border-radius:10px;background:#0d1c3a;color:var(--mut)}
.towncard .stock.live{color:#2ea06a;border:1px solid #2ea06a}
@media(max-width:760px){header.site .cta{display:none}}
@media(max-width:480px){.brand{font-size:17px}.navlinks{font-size:13px;gap:10px}}
"""


def page(title, desc, body, canonical, jsonld=None, og_image=None):
    ld = ("<script type='application/ld+json'>%s</script>" % json.dumps(jsonld)) if jsonld else ""
    og = "\n".join([
        "<meta property='og:title' content='%s'>" % e(title),
        "<meta property='og:description' content='%s'>" % e(desc),
        "<meta property='og:type' content='website'>",
        "<meta property='og:url' content='%s'>" % e(canonical),
        ("<meta property='og:image' content='%s'>" % e(og_image)) if og_image else "",
        "<meta name='twitter:card' content='summary_large_image'>",
    ])
    return """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<link rel="canonical" href="%s">
<meta name="robots" content="index,follow">
<meta name="theme-color" content="#0f1e3d">
%s
%s
<style>%s</style>%s
</head><body>
<header class="site"><div class="wrap nav">
<a class="brand" href="/">Singapore <b>Room Rental</b></a>
<nav class="navlinks"><a href="/areas/">Areas</a><a href="/faq/">FAQ</a></nav>
<a class="cta" href="%s" rel="noopener">WhatsApp %s</a>
</div></header>
%s
<footer class="site"><div class="wrap">
<div style="margin:0 0 10px"><a href="/">Home</a> &middot; <a href="/faq/">FAQ</a>%s</div>
Listings marketed by %s, CEA %s, %s. Enquiries go directly to Winfred.
Room availability and terms are subject to change and confirmation.
</div></footer>
<a class="stickycta" href="%s" rel="noopener">&#128172; WhatsApp Winfred about a room</a>
</body></html>""" % (e(title), e(desc), e(canonical), FAVICON, ld, CSS, og, WA_LINK, WA,
                     body, GUIDE_FOOTER, e(AGENT), e(AGENT_CEA), e(AGENT_FIRM), WA_LINK)


FAVICON = ("<link rel=\"icon\" href=\"data:image/svg+xml,"
           "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
           "<text y='.9em' font-size='90'>&#127968;</text></svg>\">")


def card_html(l):
    ph = ("<img class='ph' loading='lazy' decoding='async' src='/%s' alt='%s in %s, %s'>" %
          (e(l["photos"][0]), e(l["rtype"]), e(l["area_short"]), e(l["district"]))) \
        if l["photos"] else "<span class='noph'>Photos on request</span>"
    price = l["rent_min"] or l["rent_max"] or 0
    cook = dict(l["rules"]).get("Cooking")
    meta = ("<span class='chip'>&#127859; %s</span>" % e(cook)) if cook else ""
    mrt = l.get("mrt")
    if mrt:
        meta += "<span class='chip'>&#128647; %s &middot; %d min</span>" % (e(mrt["station"]), mrt["walk_min"])
    newb = "<span class='new'>NEW</span> " if l.get("is_new") else ""
    days_listed = l.get("days_listed")
    age = ("<span class='age'>Listed %d day%s ago</span>" % (days_listed, "" if days_listed == 1 else "s")) \
        if (days_listed is not None and not l.get("is_new")) else ""
    wa_msg = e("Hi Winfred, I saw the %s in %s (%s) on your site, is it still available" %
               (l["rtype"], l["area_short"], l["district"]))
    return ('<div class="card" data-price="%d" data-area="%s" data-type="%s" data-listed="%d">'
            '<a class="cardlink" href="/room/%s/">%s'
            '<span class="bd"><h3>%s%s in %s</h3>'
            '<span class="price">%s<span style="color:var(--mut);font-weight:400;font-size:12px">/mo</span></span>'
            '<span style="color:var(--mut);font-size:12px">%s (%s)</span>'
            '<span class="meta">%s</span>%s</span></a>'
            '<a class="wabtn" href="%s?text=%s" rel="noopener" aria-label="WhatsApp Winfred about this room">&#128172; WhatsApp</a>'
            '</div>') % (
        price, e(l["area_slug"]), e(l["type_key"]), (days_listed if days_listed is not None else 9999),
        e(l["slug"]), ph,
        newb, e(l["rtype"]), e(l["area_short"]), e(l["rent_txt"]), e(l["block"]), e(l["district"]), meta, age,
        WA_LINK, wa_msg)


def room_page(l):
    title = "%s for Rent in %s (%s) — %s/month | %s" % (l["rtype"], l["area_short"], l["district"], l["rent_txt"], SITE_NAME)
    desc = "%s for rent at %s, %s. %s per month. Enquire with %s on WhatsApp." % (
        l["rtype"], l["block"], l["area"], l["rent_txt"], AGENT)
    canonical = "%s/room/%s/" % (BASE_URL, l["slug"])
    gallery = "".join("<img loading='lazy' src='/%s' alt='%s in %s'>" % (e(p), e(l["rtype"]), e(l["area_short"])) for p in l["photos"])
    mrt = l.get("mrt")
    mrt_li = ("<li><span class='k'>Nearest MRT</span><span>%s MRT &middot; %d min walk (%dm)</span></li>"
              % (e(mrt["station"]), mrt["walk_min"], mrt["distance_m"])) if mrt else ""
    rules = mrt_li + "".join("<li><span class='k'>%s</span><span>%s</span></li>" % (e(k), e(v)) for k, v in l["rules"])
    lat, lng = l.get("lat", 1.3521), l.get("lng", 103.8198)
    jsonld = {
        "@context": "https://schema.org", "@type": "Accommodation",
        "name": "%s in %s" % (l["rtype"], l["area_short"]),
        "description": desc,
        "url": canonical,
        "accommodationCategory": l["rtype"],
        "address": {"@type": "PostalAddress", "streetAddress": l["block"],
                    "addressLocality": l["area_short"], "addressRegion": "Singapore",
                    "addressCountry": "SG"},
        "geo": {"@type": "GeoCoordinates", "latitude": lat, "longitude": lng},
        "image": ["%s/%s" % (BASE_URL, p) for p in l["photos"]],
        "numberOfRooms": 1,
        "offers": {"@type": "Offer", "price": l["rent_min"] or l["rent_max"] or "",
                   "priceCurrency": "SGD", "availability": "https://schema.org/InStock",
                   "url": canonical, "seller": {"@type": "RealEstateAgent", "name": AGENT}},
    }
    breadcrumb = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE_URL + "/"},
        {"@type": "ListItem", "position": 2, "name": "Rooms in " + l["area_short"], "item": "%s/rooms-in-%s/" % (BASE_URL, l["area_slug"])},
        {"@type": "ListItem", "position": 3, "name": l["rtype"], "item": canonical}]}
    body = """<div class="wrap">
<div class="crumbs"><a href="/">Home</a> › <a href="/rooms-in-%s/">%s</a> › %s</div>
<div class="detail">
<div>
<h1>%s for Rent in %s (%s)</h1>
<p style="color:var(--mut)">%s</p>
%s
<h2>Room details</h2>
<ul class="rules">
<li><span class="k">Rent</span><span style="color:var(--gold);font-weight:700">%s / month</span></li>
<li><span class="k">Type</span><span>%s</span></li>
<li><span class="k">Location</span><span>%s (%s)</span></li>
%s
%s
</ul>
</div>
<aside>
<div class="box">
<div style="font-size:22px;font-weight:800;color:var(--gold)">%s<span style="font-size:13px;color:var(--mut);font-weight:400">/mo</span></div>
<p style="color:var(--mut);font-size:14px">Enquire directly with %s. Viewing arranged on request.</p>
<a class="cta" style="display:block;text-align:center" href="%s?text=%s" rel="noopener">WhatsApp about this room</a>
<p style="font-size:12px;color:var(--mut);margin-top:10px">%s · CEA %s</p>
</div>
<div id="map" style="margin-top:14px"></div>
</aside>
</div>
</div>
%s
<script type="application/ld+json">%s</script>""" % (
        e(l["area_slug"]), e(l["area_short"]), e(l["rtype"]),
        e(l["rtype"]), e(l["area_short"]), e(l["district"]),
        e("%s at %s, %s." % (l["rtype"], l["block"], l["area"])),
        ("<div class='gallery'>%s</div>" % gallery) if gallery else "",
        e(l["rent_txt"]), e(l["rtype"]), e(l["block"]), e(l["district"]),
        rules,
        ("<li><span class='k'>Available</span><span>%s</span></li>" % e(l["available_from"])) if l.get("available_from") else "",
        e(l["rent_txt"]), e(AGENT),
        WA_LINK, e("Hi Winfred, I saw the %s in %s (%s) on your site, is it still available" % (l["rtype"], l["area_short"], l["district"])),
        e(AGENT), e(AGENT_CEA),
        map_js(lat, lng, l["area_short"], points=[_map_point(l, link=False)]),
        json.dumps(breadcrumb),
    )
    # Cross-site funnel: today's renter is tomorrow's buyer — soft bridge to the
    # main agent site's buyer content.
    body += ('<div class="wrap"><section class="sec"><h2>Renting now, buying later?</h2>'
             '<p class="intro" style="max-width:680px">When you are ready to buy your own place in Singapore, '
             'Winfred can run the numbers with you &mdash; affordability, stamp duty, loan options, and what your '
             'budget actually gets you. <a href="%s/buyers-guide.html" rel="noopener">See the buyer\'s guide &rsaquo;</a>'
             '</p></section></div>') % MAIN_SITE
    return page(title, desc, body, canonical, jsonld,
                og_image=("%s/%s" % (BASE_URL, l["photos"][0]) if l["photos"] else None))


def _map_point(l, link=True):
    """One pin's worth of public data: area, rent, room type, and (optionally) a
    link to that room's own page. approx=True means this pin is a district-centre
    fallback, not the block's real position — never an exact unit."""
    return {
        "lat": l["lat"], "lng": l["lng"],
        "area": e(l["area_short"]), "rtype": e(l["rtype"]), "rent": e(l["rent_txt"]),
        "url": ("/room/%s/" % l["slug"]) if link else "",
        "approx": bool(l.get("approx")),
        "tkey": l.get("type_key", ""),
    }


def map_js(lat, lng, label, points=None):
    """Self-contained Leaflet map: vendored (no CDN) leaflet.css/js under /vendor/,
    one SVG teardrop drop-pin per listing (gold = geocoded, grey = approximate
    district-centre fallback), popup = area + rent + room type + a link to the
    room page. The JS bundle is only fetched once #map scrolls near the
    viewport, so it never blocks first paint of the listing cards above it."""
    pts = points or [{"lat": lat, "lng": lng, "area": e(label), "rtype": "", "rent": "", "url": "", "approx": False}]
    zoom = 11 if len(pts) > 1 else 14
    return """
<link rel="stylesheet" href="/vendor/leaflet.css">
<script>
(function(){
var el=document.getElementById('map'); if(!el) return;
var pts=%s, lat=%s, lng=%s, zoom=%s;
function pinIcon(approx){
  var color=approx?'#7a8aa8':'#c8a24a';
  var svg='<svg width="24" height="34" viewBox="0 0 24 34" xmlns="http://www.w3.org/2000/svg">'+
    '<path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 22 12 22s12-13 12-22C24 5.4 18.6 0 12 0z" fill="'+color+
    '" stroke="#0b1730" stroke-width="1.5"/><circle cx="12" cy="12" r="4.4" fill="#0b1730" fill-opacity=".55"/></svg>';
  return L.divIcon({html:svg,className:'srrpin',iconSize:[24,34],iconAnchor:[12,34],popupAnchor:[0,-30]});
}
function boot(){
  var m=L.map('map',{scrollWheelZoom:false}).setView([lat,lng],zoom);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(m);
  var markers=[], townLayer=L.layerGroup().addTo(m), curKey='';
  pts.forEach(function(p){
    var mk=L.marker([p.lat,p.lng],{icon:pinIcon(p.approx)});
    var body='<b>'+(p.rtype?p.rtype+' in ':'')+p.area+'</b>'+(p.rent?'<br>'+p.rent+'/mo':'')+
      (p.approx?'<br><span style="color:#7a8aa8;font-size:11px">approximate location</span>':'')+
      (p.url?'<br><a href="'+p.url+'">View room &rsaquo;</a>':'');
    mk.bindPopup(body);
    markers.push({mk:mk, key:(p.tkey||''), p:p});
  });
  // Township labels rebuilt from whatever pins are currently visible, so counts
  // stay honest when the room-type filter narrows the map.
  function drawTowns(vp){
    townLayer.clearLayers();
    if(vp.length<2) return;
    var towns={};
    vp.forEach(function(p){ if(!p.area) return; (towns[p.area]=towns[p.area]||[]).push(p); });
    Object.keys(towns).forEach(function(name){
      var g=towns[name], la=0, lo=0;
      g.forEach(function(p){ la+=p.lat; lo+=p.lng; });
      var lbl=L.divIcon({className:'srrtown', iconSize:[0,0], iconAnchor:[0,0],
        html:'<div class="srrtownlbl">'+name+' <b>'+g.length+'</b></div>'});
      L.marker([la/g.length, lo/g.length],{icon:lbl, interactive:false, keyboard:false, zIndexOffset:1000}).addTo(townLayer);
    });
  }
  function apply(key){
    curKey=key; var vp=[];
    markers.forEach(function(x){
      if(!key||x.key===key){ x.mk.addTo(m); vp.push(x.p); } else { m.removeLayer(x.mk); }
    });
    drawTowns(vp);
    if(vp.length){try{m.fitBounds(vp.map(function(p){return [p.lat,p.lng];}),{padding:[38,38]});}catch(e){}}
    var bar=document.getElementById('mapfilter');
    if(bar){ Array.prototype.forEach.call(bar.children,function(b){ b.className='srrchip'+(b.getAttribute('data-k')===key?' on':''); }); }
  }
  // Room-type filter chips at the map (Master / Common / Whole unit / ...), only
  // when there is more than one type to choose between.
  var cats={}; markers.forEach(function(x){ if(x.key) cats[x.key]=(cats[x.key]||0)+1; });
  var order=['master','common','whole','studio','room'];
  var labels={master:'Master',common:'Common',whole:'Whole unit',studio:'Studio',room:'Room'};
  var present=order.filter(function(k){ return cats[k]; });
  if(pts.length>1 && present.length>1){
    var bar=document.createElement('div'); bar.id='mapfilter'; bar.className='srrmapfilter';
    var mk0=function(k,txt,on){ var b=document.createElement('button'); b.type='button'; b.className='srrchip'+(on?' on':''); b.setAttribute('data-k',k); b.textContent=txt; b.onclick=function(){ apply(k); }; return b; };
    bar.appendChild(mk0('','All ('+markers.length+')',true));
    present.forEach(function(k){ bar.appendChild(mk0(k,labels[k]+' ('+cats[k]+')',false)); });
    el.parentNode.insertBefore(bar, el);
  }
  markers.forEach(function(x){ x.mk.addTo(m); });
  drawTowns(pts);
  if(pts.length>1){try{m.fitBounds(pts.map(function(p){return [p.lat,p.lng];}),{padding:[38,38]});}catch(e){}}
  setTimeout(function(){try{m.invalidateSize();}catch(e){}},60);
}
function loadLeaflet(cb){
  if(window.L){cb();return;}
  var s=document.createElement('script');
  s.src='/vendor/leaflet.js'; s.onload=cb; document.head.appendChild(s);
}
if('IntersectionObserver' in window){
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(en){ if(en.isIntersecting){ io.disconnect(); loadLeaflet(boot); } });
  },{rootMargin:'250px'});
  io.observe(el);
} else { loadLeaflet(boot); }
})();
</script>""" % (json.dumps(pts), lat, lng, zoom)


def homepage(listings, areas):
    canonical = BASE_URL + "/"
    title = "Rooms for Rent in Singapore — HDB & Condo Room Rental | %s" % SITE_NAME
    desc = ("Find rooms for rent across Singapore — HDB and condo master rooms, common rooms and whole units. "
            "Real listings, real photos, arranged by %s (CEA %s). Enquire on WhatsApp." % (AGENT, AGENT_CEA))
    # area pins on the map
    by_area = {}
    for l in listings:
        by_area.setdefault(l["district"], []).append(l)
    # town-level grouping (by area_slug, not district) — a district can hold
    # several distinct towns (e.g. D18 = Tampines + Pasir Ris), and grouping by
    # district alone would silently drop or mislabel the others.
    by_town = {}
    for l in listings:
        by_town.setdefault(l["area_slug"], []).append(l)
    pts = [_map_point(l) for l in listings if l.get("lat") is not None]
    area_links = "".join("<a href='/rooms-in-%s/'>%s (%d)</a>" % (e(slug), e(ls[0]["area_short"]), len(ls))
                         for slug, ls in sorted(by_town.items(), key=lambda kv: kv[1][0]["area_short"]))
    other_towns = sorted(((s, g["name"]) for s, g in AREA_GUIDES.items() if s not in by_town), key=lambda x: x[1])
    area_links += "".join("<a href='/rooms-in-%s/'>%s</a>" % (e(s), e(name)) for s, name in other_towns)
    area_links += "<a href='/areas/' style='font-weight:700;color:var(--gold)'>See all towns &rsaquo;</a>"
    cards = "".join(card_html(l) for l in listings[:24])
    faq = [
        ("How much is a room rental in Singapore?",
         "Common rooms typically start from about $800 to $1,200 a month, master rooms from about $1,300, depending on the area, the block and what is included. Each listing on this site shows its own price."),
        ("Do I rent through an agent?",
         "Yes. Every room here is marketed by %s, a CEA-registered salesperson (%s). You enquire on WhatsApp and he arranges the viewing directly." % (AGENT, AGENT_CEA)),
        ("Can I cook in the room?",
         "It depends on the unit. Each listing states whether cooking is allowed, light cooking only, or not allowed."),
        ("How do I view a room?",
         "Message %s on WhatsApp from any listing and he will arrange a viewing that fits your schedule." % AGENT),
    ]
    faq_html = "".join("<details><summary>%s</summary><p>%s</p></details>" % (e(q), e(a)) for q, a in faq)
    jsonld = {"@context": "https://schema.org", "@type": "RealEstateAgent",
              "name": AGENT, "url": BASE_URL,
              "areaServed": "Singapore",
              "description": "Room rental listings across Singapore, marketed by %s (CEA %s)." % (AGENT, AGENT_CEA),
              "makesOffer": [{"@type": "Offer", "itemOffered": {"@type": "Accommodation",
                              "name": "%s in %s" % (l["rtype"], l["area_short"])},
                              "price": l["rent_min"] or l["rent_max"] or "", "priceCurrency": "SGD",
                              "url": "%s/room/%s/" % (BASE_URL, l["slug"])} for l in listings[:20]]}
    faq_ld = {"@context": "https://schema.org", "@type": "FAQPage",
              "mainEntity": [{"@type": "Question", "name": q,
                              "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
    # filter dropdown options
    area_opts = "".join("<option value='%s'>%s</option>" % (e(slug), e(ls[0]["area_short"]))
                        for slug, ls in sorted(by_town.items(), key=lambda kv: kv[1][0]["area_short"]))
    tlabel = {"master": "Master room", "common": "Common room", "whole": "Whole unit", "studio": "Studio", "room": "Room"}
    types_present = [k for k in ["master", "common", "whole", "studio", "room"] if any(x["type_key"] == k for x in listings)]
    type_opts = "".join("<option value='%s'>%s</option>" % (k, e(tlabel[k])) for k in types_present)
    # quick-link pills to the landing pages (SEO internal links)
    pills = "".join("<a href='/rooms-%s/'>%s</a>" % (slug, e(label)) for slug, label, cap in PRICE_BUCKETS
                    if any((x["rent_min"] or x["rent_max"] or 0) <= cap for x in listings))
    pills += "".join("<a href='/%s/'>%s</a>" % (url, e(label)) for k, label, url, _ in ROOM_TYPE_PAGES
                     if any(x["type_key"] == k for x in listings))
    cards = "".join(card_html(l) for l in listings)   # all rooms, filtered client-side
    body = """<div class="wrap">
<section class="hero">
<h1>Rooms for Rent in Singapore</h1>
<p>HDB and condo rooms across the island — master rooms, common rooms and whole units. Real photos, honest details, one contact.</p>
<a class="cta" href="%s" rel="noopener">Message Winfred on WhatsApp</a>
<div class="trust"><span><b>%d</b> rooms available</span><span><b>Islandwide</b> — HDB &amp; condo</span><span>Marketed by <b>%s</b> · CEA %s</span></div>
</section>
<div class="pills">%s</div>
<section class="sec" id="areas"><h2>Where the rooms are</h2>
<div id="map"></div>
<div class="areas" style="margin-top:10px">%s</div>
</section>
<section class="sec"><h2>Find a room</h2>
<div class="filters">
<select id="fArea" aria-label="Filter by area"><option value="">All areas</option>%s</select>
<select id="fType" aria-label="Filter by room type"><option value="">All room types</option>%s</select>
<select id="fPrice" aria-label="Filter by max price"><option value="">Any price</option><option value="900">Under $900</option><option value="1200">Under $1,200</option><option value="1500">Under $1,500</option><option value="2000">Under $2,000</option></select>
<select id="fSort" aria-label="Sort"><option value="">Sort: Relevance</option><option value="price-asc">Price: Low to High</option><option value="price-desc">Price: High to Low</option><option value="newest">Newest first</option></select>
</div>
<div class="resultcount" id="resultcount">Showing all %d rooms</div>
<div class="grid" id="roomgrid">%s</div>
<div class="noresult" id="noresult" style="display:none">No rooms match those filters. Try widening them, or <a href="%s" rel="noopener">message Winfred</a> — new rooms come in weekly.</div>
</section>
<section class="sec faq"><h2>Room rental in Singapore — quick answers</h2>%s</section>
</div>
%s
<script>
(function(){var g=document.getElementById('roomgrid');if(!g)return;var cards=[].slice.call(g.children);
function apply(){
var a=fArea.value,t=fType.value,p=parseInt(fPrice.value||0,10),s=fSort.value,n=0;
var arr=cards.slice();
if(s==='price-asc')arr.sort(function(x,y){return (+x.dataset.price)-(+y.dataset.price);});
else if(s==='price-desc')arr.sort(function(x,y){return (+y.dataset.price)-(+x.dataset.price);});
else if(s==='newest')arr.sort(function(x,y){return (+x.dataset.listed)-(+y.dataset.listed);});
arr.forEach(function(c){var ok=(!a||c.dataset.area===a)&&(!t||c.dataset.type===t)&&(!p||(+c.dataset.price)<=p);
c.style.display=ok?'':'none';if(ok)n++;g.appendChild(c);});
document.getElementById('noresult').style.display=n?'none':'block';
var rc=document.getElementById('resultcount');
if(rc)rc.textContent=(a||t||p)?('Showing '+n+' of '+cards.length+' rooms'):('Showing all '+cards.length+' rooms');}
['fArea','fType','fPrice','fSort'].forEach(function(id){var el=document.getElementById(id);if(el)el.addEventListener('change',apply);});})();
</script>
<script type="application/ld+json">%s</script>
<script type="application/ld+json">%s</script>""" % (
        WA_LINK, len(listings), e(AGENT), e(AGENT_CEA), pills, area_links,
        area_opts, type_opts, len(listings), cards, WA_LINK, faq_html,
        map_js(1.3521, 103.8198, "Singapore", pts),
        json.dumps({"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
                    "url": BASE_URL,
                    "potentialAction": {"@type": "SearchAction",
                                        "target": BASE_URL + "/?q={search_term_string}",
                                        "query-input": "required name=search_term_string"}}),
        json.dumps(faq_ld))
    return page(title, desc, body, canonical, jsonld)


def _centroid(district):
    return DISTRICT_LATLNG.get(district or "")


def nearby_guide_towns(slug, n=4):
    """Nearest other area-guide towns by district-centre distance — real
    coordinates, no invented data. Used for internal 'nearby towns' links."""
    g = AREA_GUIDES.get(slug)
    here = _centroid(g.get("district")) if g else None
    if not here:
        return []
    scored = []
    for s2, g2 in AREA_GUIDES.items():
        if s2 == slug:
            continue
        there = _centroid(g2.get("district"))
        if not there:
            continue
        d = (here[0] - there[0]) ** 2 + (here[1] - there[1]) ** 2
        scored.append((d, s2, g2["name"]))
    scored.sort(key=lambda x: x[0])
    return [(s2, name) for _, s2, name in scored[:n]]


def nearby_towns_html(slug):
    ng = nearby_guide_towns(slug)
    if not ng:
        return ""
    links = "".join("<a href='/rooms-in-%s/'>%s</a>" % (e(s2), e(name)) for s2, name in ng)
    return "<section class='sec'><h2>Nearby towns</h2><div class='pills'>%s</div></section>" % links


def area_quick_faq(name):
    """Small, generic, non-invented FAQ set reused per town — mirrors the
    homepage FAQ copy so answers stay factual and consistent site-wide."""
    return [
        ("How much does a room cost in %s?" % name,
         "Typical prices for %s are covered in the guide above &mdash; message %s on WhatsApp for the latest rooms and current pricing." % (name, AGENT)),
        ("Do I rent through an agent in %s?" % name,
         "Yes. Every room here is marketed by %s, a CEA-registered salesperson (%s). You enquire on WhatsApp and he arranges the viewing directly." % (AGENT, AGENT_CEA)),
        ("How do I view a room in %s?" % name,
         "Message %s on WhatsApp and he will arrange a viewing that fits your schedule." % AGENT),
    ]


def area_faq_html(name):
    items = area_quick_faq(name)
    body = "".join("<details><summary>%s</summary><p>%s</p></details>" % (e(q), e(a)) for q, a in items)
    return "<section class='sec faq'><h2>%s &mdash; quick answers</h2>%s</section>" % (e(name), body)


def area_faq_ld(name):
    items = area_quick_faq(name)
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q,
                            "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items]}


def breadcrumb_ld(items):
    """items = [(name, url), ...] in order from Home to the current page."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u}
                                for i, (n, u) in enumerate(items)]}


def place_ld(name, canonical, description, lat, lng):
    return {"@context": "https://schema.org", "@type": "Place",
            "name": name, "description": description, "url": canonical,
            "geo": {"@type": "GeoCoordinates", "latitude": lat, "longitude": lng},
            "containedInPlace": {"@type": "AdministrativeArea", "name": "Singapore"}}


def area_page(area_short, area_slug, district, listings, areas):
    canonical = "%s/rooms-in-%s/" % (BASE_URL, area_slug)
    title = "Rooms for Rent in %s (%s) Singapore — from %s | %s" % (
        area_short, district, min((l["rent_min"] or l["rent_max"] or 99999) for l in listings), SITE_NAME)
    lo = min((l["rent_min"] or l["rent_max"] or 0) for l in listings if (l["rent_min"] or l["rent_max"]))
    title = "Rooms for Rent in %s (%s) Singapore — from $%s/mo | %s" % (area_short, district, "{:,}".format(lo), SITE_NAME)
    desc = ("Rooms for rent in %s (%s), Singapore — %d available from $%s a month. HDB and condo rooms marketed by %s (CEA %s)." %
            (area_short, district, len(listings), "{:,}".format(lo), AGENT, AGENT_CEA))
    cards = "".join(card_html(l) for l in listings)
    pts = [_map_point(l) for l in listings if l.get("lat") is not None]
    lat, lng = (pts[0]["lat"], pts[0]["lng"]) if pts else DISTRICT_LATLNG.get(district, (1.3521, 103.8198))
    body = """<div class="wrap">
<div class="crumbs"><a href="/">Home</a> › <a href="/areas/">Areas</a> › Rooms in %s</div>
<h1>Rooms for Rent in %s (%s)</h1>
<p style="color:var(--mut);max-width:660px">%d room%s available in %s, from $%s a month. Every listing is marketed by %s and you enquire directly on WhatsApp.</p>
<a class="cta" href="%s" rel="noopener">Message %s on WhatsApp</a>
<div id="map" style="margin-top:16px"></div>
<div class="grid">%s</div>
</div>%s""" % (
        e(area_short), e(area_short), e(district), len(listings), "" if len(listings) == 1 else "s",
        e(area_short), "{:,}".format(lo), e(AGENT), WA_LINK, e(AGENT), cards, map_js(lat, lng, area_short, pts))
    g = AREA_GUIDES.get(area_slug)
    if g:
        body += ('<div class="wrap"><section class="sec"><h2>Renting a room in %s &mdash; what to know</h2>'
                 '<p class="intro" style="max-width:720px">%s</p></section></div>') % (e(g["name"]), e(g["text"]))
    guide_name = g["name"] if g else area_short
    body += '<div class="wrap">%s%s</div>' % (nearby_towns_html(area_slug), area_faq_html(guide_name))
    jsonld = {"@context": "https://schema.org", "@type": "ItemList",
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": "%s/room/%s/" % (BASE_URL, l["slug"])} for i, l in enumerate(listings)]}
    extra_ld = "".join(
        "<script type='application/ld+json'>%s</script>" % json.dumps(x)
        for x in [
            breadcrumb_ld([("Home", BASE_URL + "/"), ("Areas", BASE_URL + "/areas/"),
                           ("Rooms in %s" % area_short, canonical)]),
            place_ld(guide_name, canonical, (g["text"] if g else desc), lat, lng),
            area_faq_ld(guide_name),
        ])
    body += extra_ld
    return page(title, desc, body, canonical, jsonld)


def _grid_page(title, desc, canonical, h1, intro, matches, nav_pills):
    cards = "".join(card_html(l) for l in matches)
    body = ('<div class="wrap"><div class="crumbs"><a href="/">Home</a> &#8250; %s</div>'
            '<h1>%s</h1><p class="intro">%s</p><div class="pills">%s</div>'
            '<div class="grid">%s</div></div>') % (e(h1), e(h1), e(intro), nav_pills, cards)
    jsonld = {"@context": "https://schema.org", "@type": "ItemList",
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": "%s/room/%s/" % (BASE_URL, l["slug"])} for i, l in enumerate(matches)]}
    return page(title, desc, body, canonical, jsonld)


def price_page(slug, label, matches, nav_pills):
    canonical = "%s/rooms-%s/" % (BASE_URL, slug)
    title = "Rooms for Rent %s in Singapore — %d Available | %s" % (label, len(matches), SITE_NAME)
    desc = ("%d rooms for rent %s a month in Singapore — HDB and condo rooms islandwide, "
            "marketed by %s (CEA %s). Enquire on WhatsApp." % (len(matches), label.lower(), AGENT, AGENT_CEA))
    intro = ("Rooms for rent %s per month in Singapore. %d available right now across HDB and condo units "
             "islandwide — message %s on WhatsApp to arrange a viewing." % (label.lower(), len(matches), AGENT))
    matches = sorted(matches, key=lambda x: (x["rent_min"] or x["rent_max"] or 0))
    return _grid_page(title, desc, canonical, "Rooms for Rent %s in Singapore" % label, intro, matches, nav_pills)


def type_page(url, label, noun, matches, nav_pills):
    canonical = "%s/%s/" % (BASE_URL, url)
    title = "%s for Rent in Singapore — %d Available | %s" % (label, len(matches), SITE_NAME)
    desc = ("%d %s for rent across Singapore — HDB and condo, marketed by %s (CEA %s). Enquire on WhatsApp."
            % (len(matches), label.lower(), AGENT, AGENT_CEA))
    intro = ("%s for rent in Singapore. %d available islandwide, each marketed by %s (CEA %s). "
             "Message on WhatsApp to arrange a viewing." % (label, len(matches), AGENT, AGENT_CEA))
    return _grid_page(title, desc, canonical, "%s for Rent in Singapore" % label, intro, matches, nav_pills)


def faq_page(nav_pills):
    canonical = BASE_URL + "/faq/"
    title = "Room Rental in Singapore — FAQ (HDB rules, prices, process) | %s" % SITE_NAME
    desc = ("Answers to common Singapore room-rental questions: prices, HDB rules for renting a bedroom, "
            "deposits, eligibility for foreigners and students, and how to avoid scams. By %s (CEA %s)." % (AGENT, AGENT_CEA))
    items = "".join("<details><summary>%s</summary><p>%s</p></details>" % (e(f["q"]), e(f["a"])) for f in FAQS)
    body = ('<div class="wrap"><div class="crumbs"><a href="/">Home</a> &#8250; FAQ</div>'
            '<h1>Singapore Room Rental FAQ</h1>'
            '<p class="intro" style="max-width:720px">Straight answers to the questions renters ask most &mdash; '
            'prices, HDB rules, deposits, eligibility, and staying safe. Marketed by %s, CEA %s.</p>'
            '<div class="pills">%s</div><section class="sec faq">%s</section></div>') % (e(AGENT), e(AGENT_CEA), nav_pills, items)
    jsonld = {"@context": "https://schema.org", "@type": "FAQPage",
              "mainEntity": [{"@type": "Question", "name": f["q"],
                              "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in FAQS]}
    return page(title, desc, body, canonical, jsonld)


def guide_page(slug, g, nav_pills):
    canonical = "%s/rooms-in-%s/" % (BASE_URL, slug)
    title = "Rooms for Rent in %s (%s) Singapore — Prices & Guide | %s" % (g["name"], g.get("district", ""), SITE_NAME)
    desc = ("Renting a room in %s, Singapore: typical prices, MRT and commute, who it suits. Rooms marketed by %s "
            "(CEA %s) — enquire on WhatsApp." % (g["name"], AGENT, AGENT_CEA))
    lat, lng = DISTRICT_LATLNG.get(g.get("district", ""), (1.3521, 103.8198))
    body = ('<div class="wrap"><div class="crumbs"><a href="/">Home</a> &#8250; <a href="/areas/">Areas</a> &#8250; Rooms in %s</div>'
            '<h1>Rooms for Rent in %s (%s)</h1>'
            '<p class="intro" style="max-width:720px">%s</p>'
            '<a class="cta" href="%s" rel="noopener">Message %s on WhatsApp</a>'
            '<div id="map" style="margin-top:16px"></div>'
            '<p style="margin-top:14px;color:var(--mut)">No %s rooms are listed here at this moment &mdash; new rooms come in weekly. '
            '<a href="%s" rel="noopener">Message Winfred on WhatsApp</a> and he\'ll tell you the moment one opens, '
            'or check the nearby towns below.</p>'
            '<div class="pills">%s</div></div>') % (
        e(g["name"]), e(g["name"]), e(g.get("district", "")), e(g["text"]),
        WA_LINK, e(AGENT), e(g["name"]),
        WA_LINK, nav_pills)
    body += map_js(lat, lng, g["name"], points=[{"lat": lat, "lng": lng, "area": e(g["name"]),
                                                 "rtype": "", "rent": "", "url": "", "approx": True}])
    body += '<div class="wrap">%s%s</div>' % (nearby_towns_html(slug), area_faq_html(g["name"]))
    extra_ld = "".join(
        "<script type='application/ld+json'>%s</script>" % json.dumps(x)
        for x in [
            breadcrumb_ld([("Home", BASE_URL + "/"), ("Areas", BASE_URL + "/areas/"),
                           ("Rooms in %s" % g["name"], canonical)]),
            place_ld(g["name"], canonical, g["text"], lat, lng),
            area_faq_ld(g["name"]),
        ])
    body += extra_ld
    return page(title, desc, body, canonical)


def areas_index_page(by_area):
    """Town-based browsing hub: every area-guide town, live-stock count where
    there is one, guide-only where there isn't. One canonical entry per town,
    same /rooms-in-<slug>/ URLs used everywhere else on the site."""
    canonical = BASE_URL + "/areas/"
    title = "Browse Rooms by Town — All Singapore Areas | %s" % SITE_NAME
    desc = ("Every town %s covers for room rentals in Singapore, from Tampines to Bukit Timah — "
            "typical prices, MRT and commute, and live listings where available." % AGENT)
    rows = []
    seen = set()
    for slug, ls in by_area.items():
        name = ls[0]["area_short"]
        district = ls[0]["district"]
        rows.append((name, district, slug, len(ls)))
        seen.add(slug)
    for slug, g in AREA_GUIDES.items():
        if slug in seen:
            continue
        rows.append((g["name"], g.get("district", ""), slug, 0))
        seen.add(slug)
    rows.sort(key=lambda r: r[0])
    cards = "".join(
        "<a class='towncard' href='/rooms-in-%s/'><h3>%s</h3><div class='n'>%s</div>"
        "<span class='stock%s'>%s</span></a>" % (
            e(slug), e(name), e(district),
            " live" if count else "",
            ("%d room%s available" % (count, "" if count == 1 else "s")) if count else "Guide &middot; message for openings")
        for name, district, slug, count in rows)
    body = ('<div class="wrap"><div class="crumbs"><a href="/">Home</a> &#8250; Areas</div>'
            '<h1>Browse Rooms by Town</h1>'
            '<p class="intro" style="max-width:720px">%d towns across Singapore, each with typical prices, MRT '
            'and commute notes, and live listings where there is current stock. Every room is marketed by %s '
            '(CEA %s) &mdash; message on WhatsApp for anything not shown here.</p>'
            '<div class="townlist">%s</div></div>') % (len(rows), e(AGENT), e(AGENT_CEA), cards)
    jsonld = {"@context": "https://schema.org", "@type": "ItemList",
              "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": name,
                                   "url": "%s/rooms-in-%s/" % (BASE_URL, slug)}
                                  for i, (name, district, slug, count) in enumerate(rows)]}
    return page(title, desc, body, canonical, jsonld)


def write(path, content):
    full = os.path.join(DIST, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(content)


def copy_vendor_assets():
    """Self-hosted Leaflet (no CDN) — same vendored copy the private Matchmaker
    app inlines, just served as static files here since this build produces
    many separate pages rather than one single-file app."""
    import shutil
    src = os.path.join(REPO, "scripts", "matchmaker", "vendor")
    dst = os.path.join(DIST, "vendor")
    os.makedirs(dst, exist_ok=True)
    for fn in ("leaflet.css", "leaflet.js"):
        shutil.copy(os.path.join(src, fn), os.path.join(dst, fn))


def copy_photos(listings):
    import shutil
    dst = os.path.join(DIST, "photos")
    for l in listings:
        for p in l["photos"]:
            src = os.path.join(REPO, "scripts", "matchmaker", "deploy", p)  # p = photos/LLxxx/n.jpg
            if os.path.exists(src):
                d = os.path.join(DIST, p)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy(src, d)


def main():
    import time
    listings, areas = load_public_listings()
    # Per-listing pins: prefer the lat/lng Matchmaker already geocoded (carried
    # in matchmaker-data.json as lat/lng/geo_src — "exact" or "approx"), since
    # that pipeline is already vetted and needs no extra network calls here.
    # Only fall back to OneMap (cached + throttled), then the district centre,
    # for a listing Matchmaker hasn't geocoded yet.
    try:
        cache = json.load(open(GEOCACHE))
    except Exception:
        cache = {}
    for l in listings:
        if l.get("lat") is not None and l.get("lng") is not None:
            l["approx"] = (l.get("geo_src") == "approx")
        else:
            ll = onemap(l.get("_geoq"), cache)
            if not ll:
                ll = list(DISTRICT_LATLNG.get(l["district"], (1.3521, 103.8198)))
                l["approx"] = True
            else:
                l["approx"] = False
            l["lat"], l["lng"] = ll[0], ll[1]
            if l.get("_geoq") not in cache or cache.get(l.get("_geoq")) is None:
                time.sleep(0.3)   # be polite to OneMap on fresh lookups
        l.pop("geo_src", None)
    json.dump(cache, open(GEOCACHE, "w"))
    # nearest MRT per listing (shared module, its own cached station coords)
    mrt_cache = mrt_stations.ensure_cache()
    for l in listings:
        l["mrt"] = mrt_stations.nearest_mrt(l.get("lat"), l.get("lng"), mrt_cache)
    import shutil
    if os.path.isdir(DIST):
        for name in os.listdir(DIST):
            if name == ".vercel":     # keep the Vercel project link across rebuilds
                continue
            p = os.path.join(DIST, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.makedirs(DIST, exist_ok=True)

    # homepage
    write("index.html", homepage(listings, areas))
    # room pages
    for l in listings:
        write("room/%s/index.html" % l["slug"], room_page(l))
    # area pages
    by_area = {}
    for l in listings:
        by_area.setdefault(l["area_slug"], []).append(l)
    urls = ["%s/" % BASE_URL]
    for slug, ls in by_area.items():
        write("rooms-in-%s/index.html" % slug, area_page(ls[0]["area_short"], slug, ls[0]["district"], ls, areas))
        urls.append("%s/rooms-in-%s/" % (BASE_URL, slug))
    # town-based browsing hub — every area-guide town, live or guide-only
    write("areas/index.html", areas_index_page(by_area))
    urls.append("%s/areas/" % BASE_URL)
    # by-price + by-room-type landing pages (programmatic SEO), cross-linked
    price_sets = [(s, lb, [l for l in listings if (l["rent_min"] or l["rent_max"] or 0) <= cap])
                  for s, lb, cap in PRICE_BUCKETS]
    price_sets = [(s, lb, m) for s, lb, m in price_sets if m]
    type_sets = [(url, lb, noun, [l for l in listings if l["type_key"] == key])
                 for key, lb, url, noun in ROOM_TYPE_PAGES]
    type_sets = [(u, lb, n, m) for u, lb, n, m in type_sets if m]
    nav_pills = "<a href='/'>All rooms</a>"
    nav_pills += "".join("<a href='/rooms-%s/'>%s</a>" % (s, e(lb)) for s, lb, _ in price_sets)
    nav_pills += "".join("<a href='/%s/'>%s</a>" % (u, e(lb)) for u, lb, _, _ in type_sets)
    nav_pills += "<a href='/faq/'>FAQ</a>"
    for s, lb, m in price_sets:
        write("rooms-%s/index.html" % s, price_page(s, lb, m, nav_pills))
        urls.append("%s/rooms-%s/" % (BASE_URL, s))
    for u, lb, n, m in type_sets:
        write("%s/index.html" % u, type_page(u, lb, n, m, nav_pills))
        urls.append("%s/%s/" % (BASE_URL, u))
    # "rooms near [MRT]" pages (long-tail SEO — one per station that has rooms)
    by_mrt = {}
    for l in listings:
        if l.get("mrt"):
            by_mrt.setdefault(l["mrt"]["station"], []).append(l)
    for station, ls in by_mrt.items():
        ss = slugify(station)
        t = "Rooms for Rent near %s MRT — %d Available | %s" % (station, len(ls), SITE_NAME)
        d = ("%d rooms for rent within walking distance of %s MRT, Singapore — HDB and condo, "
             "marketed by %s (CEA %s)." % (len(ls), station, AGENT, AGENT_CEA))
        intro = ("Rooms for rent near %s MRT. %d available within walking distance, marketed by %s. "
                 "Message on WhatsApp to arrange a viewing." % (station, len(ls), AGENT))
        write("rooms-near-%s-mrt/index.html" % ss,
              _grid_page(t, d, "%s/rooms-near-%s-mrt/" % (BASE_URL, ss),
                         "Rooms for Rent near %s MRT" % station, intro, ls, nav_pills))
        urls.append("%s/rooms-near-%s-mrt/" % (BASE_URL, ss))
    # FAQ hub (GEO/SEO with FAQPage schema)
    write("faq/index.html", faq_page(nav_pills))
    urls.append("%s/faq/" % BASE_URL)
    # Standalone area-guide pages for towns with no live stock right now, so
    # "room for rent [town]" still lands somewhere (live area pages already carry
    # their guide inline). Reuses the /rooms-in-<slug>/ URL pattern.
    live_area_slugs = set(by_area.keys())
    for slug, g in AREA_GUIDES.items():
        if slug in live_area_slugs:
            continue
        write("rooms-in-%s/index.html" % slug, guide_page(slug, g, nav_pills))
        urls.append("%s/rooms-in-%s/" % (BASE_URL, slug))
    for l in listings:
        urls.append("%s/room/%s/" % (BASE_URL, l["slug"]))
    copy_photos(listings)
    copy_vendor_assets()

    # sitemap / robots / llms.txt (GEO)
    sm = "".join("<url><loc>%s</loc></url>" % u for u in urls)
    write("sitemap.xml", "<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>%s</urlset>" % sm)
    write("robots.txt", "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % BASE_URL)
    write("%s.txt" % INDEXNOW_KEY, INDEXNOW_KEY)   # IndexNow ownership proof
    globals()["_LAST_URLS"] = urls                 # for the post-deploy IndexNow ping
    llms = ["# %s" % SITE_NAME,
            "Room rental listings across Singapore, marketed by %s (CEA %s, %s)." % (AGENT, AGENT_CEA, AGENT_FIRM),
            "Contact: WhatsApp %s." % WA, "",
            "## Available rooms"]
    for l in listings:
        llms.append("- %s in %s (%s), %s/month, %s: %s/room/%s/" % (
            l["rtype"], l["area_short"], l["district"], l["rent_txt"], l["block"], BASE_URL, l["slug"]))
    write("llms.txt", "\n".join(llms))

    print("public site built: %d rooms, %d areas with stock, %d area guides -> %s" % (
        len(listings), len(by_area), len(AREA_GUIDES), DIST))
    print("pages:", len(urls), "| sitemap urls:", len(urls))


def indexnow_ping():
    """Tell Bing/Yandex/etc. (via the IndexNow aggregator) the live URLs changed.
    Run AFTER deploy so every URL and the key file are already reachable."""
    import re
    import urllib.request
    sm = open(os.path.join(DIST, "sitemap.xml")).read()
    urls = re.findall(r"<loc>([^<]+)</loc>", sm)
    host = BASE_URL.split("//", 1)[1]
    payload = json.dumps({"host": host, "key": INDEXNOW_KEY,
                          "keyLocation": "%s/%s.txt" % (BASE_URL, INDEXNOW_KEY),
                          "urlList": urls}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", payload,
                                 {"Content-Type": "application/json; charset=utf-8"})
    try:
        r = urllib.request.urlopen(req, timeout=25)
        print("IndexNow: HTTP %s submitted %d URLs" % (getattr(r, "status", r.getcode()), len(urls)))
    except Exception as ex:
        print("IndexNow ping failed:", ex)


if __name__ == "__main__":
    if "--ping" in sys.argv:
        indexnow_ping()
    else:
        main()
