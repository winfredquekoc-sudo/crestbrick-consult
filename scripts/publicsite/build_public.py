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
.card .ph{aspect-ratio:4/3;background:#0b1730 center/cover no-repeat;display:block}
.card .noph{aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;color:var(--mut);background:#0b1730;font-size:13px}
.card .bd{padding:12px 13px;flex:1;display:flex;flex-direction:column;gap:6px}
.card h3{margin:0;font-size:15px}.card .price{color:var(--gold);font-weight:800;font-size:17px}
.chip{display:inline-block;background:#0d1c3a;border:1px solid var(--line);border-radius:20px;padding:2px 9px;font-size:11px;color:var(--mut);margin:2px 3px 0 0}
.sec{padding:26px 0;border-top:1px solid var(--line)}
.sec h2{font-size:23px;margin:0 0 12px}
.areas{display:flex;flex-wrap:wrap;gap:8px}
.areas a{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--ink);font-size:14px}
#map{height:360px;border-radius:12px;border:1px solid var(--line);margin:8px 0}
.leaflet-tile-pane{filter:invert(1) hue-rotate(185deg) brightness(.92) contrast(.9)}
.leaflet-container{background:#0b1730}.leaflet-popup-content a{color:#1a1300}
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
    ph = ("<span class='ph' style=\"background-image:url('/%s')\"></span>" % e(l["photos"][0])) \
        if l["photos"] else "<span class='noph'>Photos on request</span>"
    price = l["rent_min"] or l["rent_max"] or 0
    cook = dict(l["rules"]).get("Cooking")
    meta = ("<span class='chip'>&#127859; %s</span>" % e(cook)) if cook else ""
    mrt = l.get("mrt")
    if mrt:
        meta += "<span class='chip'>&#128647; %s &middot; %d min</span>" % (e(mrt["station"]), mrt["walk_min"])
    newb = "<span class='new'>NEW</span> " if l.get("is_new") else ""
    return ('<a class="card" data-price="%d" data-area="%s" data-type="%s" href="/room/%s/">%s'
            '<span class="bd"><h3>%s%s in %s</h3>'
            '<span class="price">%s<span style="color:var(--mut);font-weight:400;font-size:12px">/mo</span></span>'
            '<span style="color:var(--mut);font-size:12px">%s (%s)</span>'
            '<span class="meta">%s</span></span></a>') % (
        price, e(l["area_slug"]), e(l["type_key"]), e(l["slug"]), ph,
        newb, e(l["rtype"]), e(l["area_short"]), e(l["rent_txt"]), e(l["block"]), e(l["district"]), meta)


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
        map_js(lat, lng, l["area_short"]),
        json.dumps(breadcrumb),
    )
    return page(title, desc, body, canonical, jsonld,
                og_image=("%s/%s" % (BASE_URL, l["photos"][0]) if l["photos"] else None))


def map_js(lat, lng, label, points=None):
    pts = points or [{"lat": lat, "lng": lng, "label": label, "url": ""}]
    return """
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){var m=L.map('map',{scrollWheelZoom:false}).setView([%s,%s],%s);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(m);
var pts=%s;pts.forEach(function(p){var mk=L.marker([p.lat,p.lng]).addTo(m);
mk.bindPopup(p.url?('<a href=\"'+p.url+'\">'+p.label+'</a>'):p.label);});
if(pts.length>1){m.fitBounds(pts.map(function(p){return [p.lat,p.lng];}),{padding:[30,30]});}})();
</script>""" % (lat, lng, 14 if not points else 11, json.dumps(pts))


def homepage(listings, areas):
    canonical = BASE_URL + "/"
    title = "Rooms for Rent in Singapore — HDB & Condo Room Rental | %s" % SITE_NAME
    desc = ("Find rooms for rent across Singapore — HDB and condo master rooms, common rooms and whole units. "
            "Real listings, real photos, arranged by %s (CEA %s). Enquire on WhatsApp." % (AGENT, AGENT_CEA))
    # area pins on the map
    by_area = {}
    for l in listings:
        by_area.setdefault(l["district"], []).append(l)
    pts = [{"lat": l["lat"], "lng": l["lng"],
            "label": "%s in %s &mdash; %s" % (l["rtype"], l["area_short"], l["rent_txt"]),
            "url": "/room/%s/" % l["slug"]} for l in listings if l.get("lat")]
    area_links = "".join("<a href='/rooms-in-%s/'>%s (%d)</a>" % (e(ls[0]["area_slug"]), e(ls[0]["area_short"]), len(ls))
                         for ls in by_area.values())
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
    area_opts = "".join("<option value='%s'>%s</option>" % (e(ls[0]["area_slug"]), e(ls[0]["area_short"]))
                        for ls in sorted(by_area.values(), key=lambda x: x[0]["area_short"]))
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
<section class="sec"><h2>Where the rooms are</h2>
<div id="map"></div>
<div class="areas" style="margin-top:10px">%s</div>
</section>
<section class="sec"><h2>Find a room</h2>
<div class="filters">
<select id="fArea"><option value="">All areas</option>%s</select>
<select id="fType"><option value="">All room types</option>%s</select>
<select id="fPrice"><option value="">Any price</option><option value="900">Under $900</option><option value="1200">Under $1,200</option><option value="1500">Under $1,500</option><option value="2000">Under $2,000</option></select>
</div>
<div class="grid" id="roomgrid">%s</div>
<div class="noresult" id="noresult" style="display:none">No rooms match those filters. Try widening them, or <a href="%s" rel="noopener">message Winfred</a> — new rooms come in weekly.</div>
</section>
<section class="sec faq"><h2>Room rental in Singapore — quick answers</h2>%s</section>
</div>
%s
<script>
(function(){var g=document.getElementById('roomgrid');if(!g)return;var cards=[].slice.call(g.children);
function apply(){var a=fArea.value,t=fType.value,p=parseInt(fPrice.value||0,10),n=0;
cards.forEach(function(c){var ok=(!a||c.dataset.area===a)&&(!t||c.dataset.type===t)&&(!p||(+c.dataset.price)<=p);c.style.display=ok?'':'none';if(ok)n++;});
document.getElementById('noresult').style.display=n?'none':'block';}
['fArea','fType','fPrice'].forEach(function(id){var el=document.getElementById(id);if(el)el.addEventListener('change',apply);});})();
</script>
<script type="application/ld+json">%s</script>
<script type="application/ld+json">%s</script>""" % (
        WA_LINK, len(listings), e(AGENT), e(AGENT_CEA), pills, area_links,
        area_opts, type_opts, cards, WA_LINK, faq_html,
        map_js(1.3521, 103.8198, "Singapore", pts),
        json.dumps({"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
                    "url": BASE_URL,
                    "potentialAction": {"@type": "SearchAction",
                                        "target": BASE_URL + "/?q={search_term_string}",
                                        "query-input": "required name=search_term_string"}}),
        json.dumps(faq_ld))
    return page(title, desc, body, canonical, jsonld)


def area_page(area_short, area_slug, district, listings, areas):
    canonical = "%s/rooms-in-%s/" % (BASE_URL, area_slug)
    title = "Rooms for Rent in %s (%s) Singapore — from %s | %s" % (
        area_short, district, min((l["rent_min"] or l["rent_max"] or 99999) for l in listings), SITE_NAME)
    lo = min((l["rent_min"] or l["rent_max"] or 0) for l in listings if (l["rent_min"] or l["rent_max"]))
    title = "Rooms for Rent in %s (%s) Singapore — from $%s/mo | %s" % (area_short, district, "{:,}".format(lo), SITE_NAME)
    desc = ("Rooms for rent in %s (%s), Singapore — %d available from $%s a month. HDB and condo rooms marketed by %s (CEA %s)." %
            (area_short, district, len(listings), "{:,}".format(lo), AGENT, AGENT_CEA))
    cards = "".join(card_html(l) for l in listings)
    pts = [{"lat": l["lat"], "lng": l["lng"], "label": "%s &mdash; %s" % (l["rtype"], l["rent_txt"]),
            "url": "/room/%s/" % l["slug"]} for l in listings if l.get("lat")]
    lat, lng = (pts[0]["lat"], pts[0]["lng"]) if pts else DISTRICT_LATLNG.get(district, (1.3521, 103.8198))
    body = """<div class="wrap">
<div class="crumbs"><a href="/">Home</a> › Rooms in %s</div>
<h1>Rooms for Rent in %s (%s)</h1>
<p style="color:var(--mut);max-width:660px">%d room%s available in %s, from $%s a month. Every listing is marketed by %s and you enquire directly on WhatsApp.</p>
<div id="map"></div>
<div class="grid">%s</div>
</div>%s""" % (
        e(area_short), e(area_short), e(district), len(listings), "" if len(listings) == 1 else "s",
        e(area_short), "{:,}".format(lo), e(AGENT), cards, map_js(lat, lng, area_short, pts))
    g = AREA_GUIDES.get(area_slug)
    if g:
        body += ('<div class="wrap"><section class="sec"><h2>Renting a room in %s &mdash; what to know</h2>'
                 '<p class="intro" style="max-width:720px">%s</p></section></div>') % (e(g["name"]), e(g["text"]))
    jsonld = {"@context": "https://schema.org", "@type": "ItemList",
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": "%s/room/%s/" % (BASE_URL, l["slug"])} for i, l in enumerate(listings)]}
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
    body = ('<div class="wrap"><div class="crumbs"><a href="/">Home</a> &#8250; Rooms in %s</div>'
            '<h1>Rooms for Rent in %s (%s)</h1>'
            '<p class="intro" style="max-width:720px">%s</p>'
            '<div id="map"></div>'
            '<p style="margin-top:14px">No %s rooms are listed here at this moment &mdash; new rooms come in weekly. '
            '<a href="%s" rel="noopener">Message Winfred on WhatsApp</a> and he\'ll tell you the moment one opens, '
            'or <a href="/">browse rooms in other areas</a>.</p>'
            '<div class="pills">%s</div></div>%s') % (
        e(g["name"]), e(g["name"]), e(g.get("district", "")), e(g["text"]), e(g["name"]),
        WA_LINK, nav_pills, map_js(lat, lng, g["name"]))
    return page(title, desc, body, canonical)


def write(path, content):
    full = os.path.join(DIST, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(content)


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
    # accurate pins: geocode each block via OneMap (cached + throttled); fall back
    # to the district centre only if a block can't be resolved.
    try:
        cache = json.load(open(GEOCACHE))
    except Exception:
        cache = {}
    for l in listings:
        ll = onemap(l.get("_geoq"), cache)
        if not ll:
            ll = list(DISTRICT_LATLNG.get(l["district"], (1.3521, 103.8198)))
            l["approx"] = True
        l["lat"], l["lng"] = ll[0], ll[1]
        if l.get("_geoq") not in cache or cache.get(l.get("_geoq")) is None:
            time.sleep(0.3)   # be polite to OneMap on fresh lookups
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

    # sitemap / robots / llms.txt (GEO)
    sm = "".join("<url><loc>%s</loc></url>" % u for u in urls)
    write("sitemap.xml", "<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>%s</urlset>" % sm)
    write("robots.txt", "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % BASE_URL)
    llms = ["# %s" % SITE_NAME,
            "Room rental listings across Singapore, marketed by %s (CEA %s, %s)." % (AGENT, AGENT_CEA, AGENT_FIRM),
            "Contact: WhatsApp %s." % WA, "",
            "## Available rooms"]
    for l in listings:
        llms.append("- %s in %s (%s), %s/month, %s: %s/room/%s/" % (
            l["rtype"], l["area_short"], l["district"], l["rent_txt"], l["block"], BASE_URL, l["slug"]))
    write("llms.txt", "\n".join(llms))

    print("public site built: %d rooms, %d areas -> %s" % (len(listings), len(by_area), DIST))
    print("pages:", 1 + len(listings) + len(by_area), "| sitemap urls:", len(urls))


if __name__ == "__main__":
    main()
