#!/usr/bin/env python3
"""Generate 'Living near <station> MRT' property-guide pages.

Mirrors the proven _render_mrt_school_matrix.py pattern (curated data list ->
HTML renderer -> sitemap fragment), but the unit of content is a single MRT
station rather than a station+school pair. "Living near <station> MRT"
queries have higher volume and broader intent than the school-catchment
pages, so this is a distinct programmatic vein, not a replacement.

Creates pages at /public/area/living-near-{station-slug}-mrt.html.

Does NOT touch: existing /area pages, nav, sitemap.xml, insights, index.html,
tools/*, compare/*, guides/*. Emits public/_mrt-sitemap-fragment.xml instead
of splicing into the main sitemap.

Truthfulness: no invented distances, prices, or transaction figures. Commute
minutes are only stated as a specific number where that figure is already
published elsewhere on this site (Lentor ~25 min, Tampines ~35 min); every
other commute note is deliberately qualitative. District / HDB-town links
point only at files verified to exist in this repo.
"""
import json
from pathlib import Path
from html import escape

ROOT = Path(__file__).parent.parent / "public"
OUT = ROOT / "area"
OUT.mkdir(exist_ok=True)

LASTMOD = "2026-07-29"

# ---------------------------------------------------------------------------
# Curated station data. Each entry:
#   slug, name, lines (list of line codes), interchange (bool),
#   district_file (public/districts/*.html, no ext), district_label,
#   hdb_town_file (public/hdb-towns/*.html, no ext, or None), hdb_town_label,
#   area_para, housing_para, commute_para, rental_para,
#   related_pages: list of (slug, anchor text) -- all verified to exist in
#   public/area/ at generation time,
#   note: optional extra factual aside (e.g. confirmed future infra)
# ---------------------------------------------------------------------------
STATIONS = [
    dict(
        slug="lentor", name="Lentor", lines=["TEL"], interchange=False,
        district_file="d26-upper-thomson-springleaf", district_label="District 26 (Upper Thomson, Springleaf)",
        hdb_town_file="ang-mo-kio", hdb_town_label="Ang Mo Kio",
        area_para="Lentor is a fast-evolving residential pocket where the original landed-house enclave is being layered with new condo launches, Lentor Modern, Lentor Hills Residences, Hillock Green, and the Lentor Mansion line all sit within a short walk of the station. The area was largely quiet and car-dependent until the Thomson-East Coast Line (TEL) arrived in 2021.",
        housing_para="Housing stock here is being actively reshaped. The original landed enclave anchors the upper end of the market; the new wave of condo launches is adding several hundred units within walking distance of the station. HDB stock is thin in the immediate vicinity, most nearby resale flats sit further out towards the older Ang Mo Kio blocks.",
        commute_para="TEL trains put the city centre roughly 25 minutes away, one of the more direct northern commutes now that the full TEL corridor is open, without the interchange transfer older Ang Mo Kio addresses required.",
        rental_para="Expect a mix of long-settled landed-house owners and a fresh wave of condo tenants as the new launches complete their TOPs, many units will enter the rental market for the first time as owners test tenancy before deciding whether to move in themselves.",
        related_pages=[("lentor-ai-tong", "Ai Tong School"), ("lentor-anderson-primary", "Anderson Primary"), ("lentor-chij-st-nicholas-girls-primary", "CHIJ St Nicholas Girls' Primary")],
        note=None,
    ),
    dict(
        slug="mayflower", name="Mayflower", lines=["TEL"], interchange=False,
        district_file="d20-bishan-ang-mo-kio", district_label="District 20 (Bishan, Ang Mo Kio)",
        hdb_town_file="ang-mo-kio", hdb_town_label="Ang Mo Kio",
        area_para="Mayflower opened with the TEL in 2021, quiet and leafy, and within walking distance of Ai Tong School and CHIJ St Nicholas Girls' Primary. It sits inside the older, mature side of Ang Mo Kio, so the streetscape is heartland rather than showpiece.",
        housing_para="Stock is mostly mature HDB blocks from Ang Mo Kio's earlier development phases, with a scattering of low-rise private housing. There has been comparatively little new condo supply directly at the station compared with Lentor a few stops north.",
        commute_para="TEL access gives a fairly direct run into town, without the multiple transfers older Ang Mo Kio addresses used to require before the line opened.",
        rental_para="Rental demand here skews towards small families and HDB upgraders staying close to the school catchment, a quieter, less transient market than the newer estates further out.",
        related_pages=[("mayflower-ai-tong", "Ai Tong School"), ("mayflower-chij-st-nicholas-girls-primary", "CHIJ St Nicholas Girls' Primary")],
        note=None,
    ),
    dict(
        slug="bright-hill", name="Bright Hill", lines=["TEL"], interchange=False,
        district_file="d20-bishan-ang-mo-kio", district_label="District 20 (Bishan, Ang Mo Kio, Upper Thomson)",
        hdb_town_file="bishan", hdb_town_label="Bishan",
        area_para="Bright Hill sits between Bishan and Upper Thomson, quiet, low-density, and within walking distance of Ai Tong School. It's one stop from the Bishan interchange, so residents get a quiet address without giving up fast access to the rest of the rail network.",
        housing_para="Housing is mostly walk-up apartments and older HDB blocks, with the Thomson View collective-sale site a longer-horizon redevelopment story for the immediate area. LTA has confirmed Bright Hill as a future Cross Island Line (CRL) interchange, expected to open in the 2030s, a genuine long-term catalyst, not yet reflected in current transaction data.",
        commute_para="One stop from Bishan on the TEL, with an easy interchange there for anyone heading further into the CBD or across to the Circle Line.",
        rental_para="A tight, low-turnover rental pocket, walk-up apartments near Ai Tong School attract parents on shorter leases timed to the Primary 1 registration cycle.",
        related_pages=[("bright-hill-ai-tong", "Ai Tong School"), ("bright-hill-kuo-chuan-presbyterian", "Kuo Chuan Presbyterian Primary")],
        note="Bright Hill is confirmed by LTA as a future TEL/Cross Island Line interchange (Cross Island Line Phase 1, targeted for the 2030s), worth knowing if you're holding for the long term.",
    ),
    dict(
        slug="marine-parade", name="Marine Parade", lines=["TEL"], interchange=False,
        district_file="d15-east-coast-katong", district_label="District 15 (East Coast, Katong)",
        hdb_town_file="marine-parade", hdb_town_label="Marine Parade",
        area_para="Marine Parade got its TEL station in 2024, a genuinely transformational change for what was previously a car-dependent enclave close to the East Coast. Tao Nan School, Ngee Ann Primary, and CHIJ (Katong) Primary anchor a deep school grid nearby.",
        housing_para="The area mixes older condos, Mandarin Gardens and the Costa del Sol vicinity, with a newer premium cluster (Amber Park, Meyer Mansion) closer to the coastline. HDB stock is concentrated around Marine Terrace, a step inland from the station.",
        commute_para="The 2024 TEL station finally gave this stretch of the east coast a direct rail option into town, previously this was largely a bus-and-taxi commute.",
        rental_para="A long-established expat and professional rental market around the older condos, now getting a fresh look from tenants drawn by the new station and the East Coast Park lifestyle.",
        related_pages=[("marine-parade-tao-nan", "Tao Nan School"), ("marine-parade-ngee-ann-primary", "Ngee Ann Primary"), ("marine-parade-haig-girls-primary", "Haig Girls' School")],
        note=None,
    ),
    dict(
        slug="tampines", name="Tampines", lines=["EWL", "DTL"], interchange=True,
        district_file="d18-tampines-pasir-ris", district_label="District 18 (Tampines, Pasir Ris)",
        hdb_town_file="tampines", hdb_town_label="Tampines",
        area_para="Tampines is the regional centre of the east, Tampines Mall, Century Square, the IKEA-Giant retail cluster, and a strong primary-school network all sit within the town centre. It's one of the most self-contained mature estates in Singapore; residents rarely need to leave the town for daily errands.",
        housing_para="Tampines is a BTO-rich estate, with recent and upcoming launches around Tampines North and an EC pipeline along Tampines Avenue 11. Resale HDB is mature and deep; private supply leans towards Treasure at Tampines and the older Centris cluster near the town centre.",
        commute_para="EWL and DTL together put the city about 35 minutes away by rail, not the fastest commute on the network, but a direct one with no transfer needed on either line.",
        rental_para="One of the east's deepest rental markets, HDB and condo alike see steady demand from families anchored to the regional centre's jobs, schools, and malls.",
        related_pages=[("tampines-tampines-primary", "Tampines Primary"), ("tampines-poi-ching", "Poi Ching School"), ("tampines-st-hildas-primary", "St Hilda's Primary")],
        note=None,
    ),
    dict(
        slug="punggol", name="Punggol", lines=["NEL", "Punggol LRT"], interchange=True,
        district_file="d19-serangoon-hougang-punggol", district_label="District 19 (Serangoon, Hougang, Punggol)",
        hdb_town_file="punggol", hdb_town_label="Punggol",
        area_para="Punggol is Singapore's young-family magnet, Waterway Point, the LRT loop, and a fully built-out BTO ecosystem centred on the Punggol Waterway. The planned Punggol Digital District and the Cross Island Line extension will keep reshaping demand here through the 2030s.",
        housing_para="Housing supply is BTO-led, Northshore, Matilda, and the newer Punggol Coast pipeline (served by its own station, one stop from Punggol proper). Watertown condo and Piermont Grand EC are the mature private anchors along the waterway.",
        commute_para="NEL gets you into the city in a comfortable rail ride, with the Punggol LRT loop covering the estate's interior for the last mile to most blocks.",
        rental_para="A young-family rental market, heavy on newer BTO flats and waterway-facing condos, many tenants are early-career couples waiting out their own BTO's construction timeline.",
        related_pages=[("punggol-punggol-primary", "Punggol Primary"), ("punggol-edgefield-primary", "Edgefield Primary"), ("punggol-horizon-primary", "Horizon Primary"), ("punggol-coast-punggol-cove-primary", "Punggol Cove Primary (near Punggol Coast)")],
        note=None,
    ),
    dict(
        slug="sengkang", name="Sengkang", lines=["NEL", "Sengkang LRT"], interchange=True,
        district_file="d19-serangoon-hougang-punggol", district_label="District 19 (Serangoon, Hougang, Punggol)",
        hdb_town_file="sengkang", hdb_town_label="Sengkang",
        area_para="Sengkang is Punggol's older sibling, Compass One mall, the LRT loop, and a deeper, more established school catchment. Resale prices have outperformed since 2020 as the estate matured and its infrastructure filled in.",
        housing_para="Sengkang has deep BTO and resale stock, plus condos clustered along Compassvale and Anchorvale. Recent EC launches (Parc Greenwich and its North Gaia-area neighbours) anchor the upper end of local pricing.",
        commute_para="A similar NEL profile to Punggol, a straightforward rail run into town, with the Sengkang LRT covering the estate's interior.",
        rental_para="Similar to Punggol but slightly more mature, resale HDB and EC units see steady demand from families who've already outgrown a first flat elsewhere.",
        related_pages=[("sengkang-compassvale-primary", "Compassvale Primary"), ("sengkang-anchor-green-primary", "Anchor Green Primary"), ("sengkang-north-vista-primary", "North Vista Primary")],
        note=None,
    ),
    dict(
        slug="bishan", name="Bishan", lines=["NSL", "CCL"], interchange=True,
        district_file="d20-bishan-ang-mo-kio", district_label="District 20 (Bishan, Ang Mo Kio)",
        hdb_town_file="bishan", hdb_town_label="Bishan",
        area_para="Bishan is the central north's blue-chip mature estate, Catholic High School and (nearby) Raffles Institution put a permanent floor under family demand. Bishan-Ang Mo Kio Park and the Junction 8 mall anchor the town centre, and dual NSL+CCL rail access makes commute compression easy for dual-income parents.",
        housing_para="Stock here is dominated by mature HDB blocks and a tight ring of condos, Sky@Eleven, Bishan 8, and developments on the Thomson-East side. Resale HDB in Bishan tends to outperform most other mature estates on a per-square-foot basis.",
        commute_para="Sitting on both the NSL and CCL, Bishan is one of the better-connected central-north addresses, a short, direct ride to Raffles Place or Dhoby Ghaut without a transfer.",
        rental_para="A premium rental pocket for families chasing the Catholic High / Raffles Institution catchment, plus steady demand from professionals who want fast CBD access without living in the CBD.",
        related_pages=[("bishan-ai-tong", "Ai Tong School"), ("bishan-catholic-high", "Catholic High School"), ("bishan-kuo-chuan-presbyterian", "Kuo Chuan Presbyterian Primary")],
        note=None,
    ),
    dict(
        slug="toa-payoh", name="Toa Payoh", lines=["NSL"], interchange=False,
        district_file="d12-balestier-toa-payoh", district_label="District 12 (Balestier, Toa Payoh)",
        hdb_town_file="toa-payoh", hdb_town_label="Toa Payoh",
        area_para="Toa Payoh is the original new town and still one of the most liveable mature estates in Singapore. HDB Hub anchors the retail centre, and the NSL keeps the city commute short despite Toa Payoh not (yet) being a rail interchange.",
        housing_para="Toa Payoh has a steady BTO pipeline around Lorong 6 and spillover demand from the neighbouring Bidadari estate, plus mature HDB resale and a handful of condos around Lorong 4 to 8. The Bidadari development has materially shifted the catchment picture for schools like First Toa Payoh Primary.",
        commute_para="NSL access puts the city within a short, direct ride, one of the more convenient mature-estate commutes on the network, even without an interchange.",
        rental_para="A stable, heartland rental market, older HDB blocks and a handful of condos see consistent demand from long-term local tenants rather than short-stay expats.",
        related_pages=[("toa-payoh-first-toa-payoh", "First Toa Payoh Primary"), ("toa-payoh-chij-primary-toa-payoh", "CHIJ Primary (Toa Payoh)"), ("toa-payoh-pei-chun-public", "Pei Chun Public School")],
        note=None,
    ),
    dict(
        slug="queenstown", name="Queenstown", lines=["EWL"], interchange=False,
        district_file="d3-queenstown-tiong-bahru", district_label="District 3 (Queenstown, Tiong Bahru)",
        hdb_town_file="queenstown", hdb_town_label="Queenstown",
        area_para="Queenstown is Singapore's first satellite town and now one of the most desirable mature HDB estates, close to the CBD, walking distance to IKEA Alexandra, Queenstown Primary, and the newer Margaret Drive redevelopment within the same town.",
        housing_para="Housing mixes some of Singapore's most storied mature HDB blocks with the newer Dawson estate towers, SkyVille @ Dawson, SkyTerrace @ Dawson, and Alex Residences. Redevelopment around Margaret Drive is adding a fresh layer of supply within the same HDB town.",
        commute_para="EWL access gives a quick, direct run towards Raffles Place and the CBD, one of the shorter commutes among the mature estates on this list.",
        rental_para="A sought-after rental pocket for professionals working in the CBD or one-north, the Dawson estate towers in particular draw a younger, higher-income tenant base than the surrounding older blocks.",
        related_pages=[("queenstown-queenstown-primary", "Queenstown Primary")],
        note=None,
    ),
    dict(
        slug="clementi", name="Clementi", lines=["EWL"], interchange=False,
        district_file="d5-pasir-panjang-clementi", district_label="District 5 (Pasir Panjang, Clementi)",
        hdb_town_file="clementi", hdb_town_label="Clementi",
        area_para="Clementi is the west side's EWL anchor, Clementi Mall, NUS proximity, and a deep cluster of primary schools. Nan Hua Primary sits about 1.5 km out, drawing parents into the surrounding HDB and condo stock even outside the tightest catchment band.",
        housing_para="Stock ranges from older HDB blocks near the town centre to newer private developments along Clementi Avenue and towards West Coast, The Trilinq, Clement Canopy, and Clavon among them. NUS staff and student demand keeps a steady floor under rental interest here.",
        commute_para="EWL puts the city within a fairly direct ride, while NUS and one-north commutes run conveniently in the opposite direction.",
        rental_para="A rental market split between NUS-adjacent student and staff housing near the university, and more family-oriented HDB and condo units further from the campus.",
        related_pages=[("clementi-nan-hua-primary", "Nan Hua Primary"), ("clementi-qifa-primary", "Qifa Primary"), ("clementi-pei-tong-primary", "Pei Tong Primary")],
        note=None,
    ),
    dict(
        slug="jurong-east", name="Jurong East", lines=["EWL", "NSL"], interchange=True,
        district_file="d22-jurong-boon-lay", district_label="District 22 (Jurong, Boon Lay)",
        hdb_town_file="jurong-east", hdb_town_label="Jurong East",
        area_para="Jurong East is the regional centre of the west, JEM, Westgate, IMM, and the ongoing Jurong Lake District (JLD) transformation all sit within the town. Fuhua Primary and Yuhua Primary anchor the immediate school catchment.",
        housing_para="Housing is a mix of mature HDB (Teban Gardens, Yuhua) and taller private towers clustered around the JEM/Westgate precinct, such as J Gateway. Expect more private supply over time as the wider JLD masterplan build-out continues; the future Jurong Region Line (JRL) will add further connectivity.",
        commute_para="As an EWL/NSL interchange, Jurong East gives flexible routing either into town or across to the west, and the future JRL is expected to add still more options once it opens.",
        rental_para="Rental demand here is tied to the regional job cluster around JEM and Westgate, a mix of young professionals and families anchored to jobs in the west rather than the CBD.",
        related_pages=[("jurong-east-fuhua-primary", "Fuhua Primary"), ("jurong-east-yuhua-primary", "Yuhua Primary")],
        note=None,
    ),
    dict(
        slug="woodlands", name="Woodlands", lines=["NSL", "TEL"], interchange=True,
        district_file="d25-kranji-woodlands", district_label="District 25 (Kranji, Woodlands)",
        hdb_town_file="woodlands", hdb_town_label="Woodlands",
        area_para="Woodlands is the north's regional centre, Causeway Point anchors the retail hub, and the future Woodlands North RTS Link will give a direct rail connection across the Causeway to Johor Bahru. Woodgrove Primary is the leading local catchment school.",
        housing_para="Woodlands is HDB-dominant, a large, mature estate with pockets of newer BTO supply near Woodlands North and Woodlands South. Private stock is thinner here than in estates further south, though the Woodlands North Coast precinct is expected to add both public and private supply over the coming years.",
        commute_para="NSL and TEL both run through Woodlands, and the future RTS Link at Woodlands North is expected to add a direct cross-border rail option to Johor Bahru.",
        rental_para="A largely local, family-oriented rental market, HDB-heavy, with comparatively few condo units and correspondingly thinner private rental supply than the estates further south.",
        related_pages=[("woodlands-woodgrove-primary", "Woodgrove Primary")],
        note=None,
    ),
    dict(
        slug="buona-vista", name="Buona Vista", lines=["EWL", "CCL"], interchange=True,
        district_file="d5-pasir-panjang-clementi", district_label="District 5 (Buona Vista, Pasir Panjang, Clementi)",
        hdb_town_file="queenstown", hdb_town_label="Queenstown (Buona Vista falls within this HDB town)",
        area_para="Buona Vista is the gateway to the one-north precinct, research institutes, biotech firms, NUS, and the Star Vista mall all sit within a short walk or one stop away. Fairfield Methodist Primary anchors the school side.",
        housing_para="Housing near Buona Vista mixes older condos with newer one-north developments aimed at the research and biotech workforce. HDB stock is limited in the immediate vicinity, most of it sits a short ride away in the wider Queenstown HDB town that Buona Vista belongs to.",
        commute_para="EWL and CCL both stop here, giving flexible routing towards the CBD or around the island without doubling back through the city centre.",
        rental_para="A research-and-tech-worker rental market, one-north's biotech and media tenants are the dominant demand driver, alongside NUS-adjacent staff housing.",
        related_pages=[("buona-vista-fairfield-methodist-primary", "Fairfield Methodist Primary")],
        note=None,
    ),
    dict(
        slug="paya-lebar", name="Paya Lebar", lines=["EWL", "CCL"], interchange=True,
        district_file="d14-geylang-eunos", district_label="District 14 (Geylang, Eunos, Paya Lebar)",
        hdb_town_file="geylang", hdb_town_label="Geylang (part of Paya Lebar falls within this HDB town)",
        area_para="Paya Lebar is the EWL/CCL interchange anchored by Paya Lebar Quarter (PLQ), Singapore Post Centre, Paya Lebar Square, and a major office cluster all sit around the station. Geylang Methodist Primary is the natural local catchment school.",
        housing_para="Paya Lebar Quarter brought the newest private stock to the area, Park Place Residences sits directly above the interchange. Older HDB blocks line Paya Lebar Way and Circuit Road nearby, with Geylang's shophouse-and-walk-up character close by to the south.",
        commute_para="EWL and CCL interchange access makes this one of the east's better-connected addresses for a CBD-bound commute.",
        rental_para="A mixed commercial-residential rental pocket, PLQ's office cluster feeds steady demand for the newer condo units directly above the interchange, alongside more affordable HDB rental further along Paya Lebar Way.",
        related_pages=[("paya-lebar-kong-hwa", "Kong Hwa School"), ("paya-lebar-geylang-methodist-primary", "Geylang Methodist Primary")],
        note=None,
    ),
]


def line_label(lines):
    return "/".join(lines)


def build_schema(slug, name, lines, faqs):
    page_url = f"https://winfredquek.com/area/{slug}"
    obj = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "@id": page_url + "#article",
                "headline": f"Living near {name} MRT",
                "datePublished": LASTMOD,
                "dateModified": LASTMOD,
                "author": {"@type": "Person", "name": "Winfred Quek", "identifier": "CEA R073319H"},
                "publisher": {"@type": "Organization", "name": "Crestbrick Pte Ltd"},
                "url": page_url,
                "image": "https://winfredquek.com/img/og-image.jpg",
                "mainEntityOfPage": page_url,
            },
            {
                "@type": "Place",
                "@id": page_url + "#mrt",
                "name": f"{name} MRT Station",
                "addressCountry": "SG",
            },
            {
                "@type": "RealEstateAgent",
                "@id": "https://winfredquek.com/#agent",
                "name": "Winfred Quek",
                "url": "https://winfredquek.com/",
                "telephone": "+65-8161-8149",
                "identifier": {"@type": "PropertyValue", "propertyID": "CEA", "value": "R073319H"},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
                    {"@type": "ListItem", "position": 2, "name": "Districts", "item": "https://winfredquek.com/districts"},
                    {"@type": "ListItem", "position": 3, "name": f"Living near {name} MRT", "item": page_url},
                ],
            },
            {
                "@type": "FAQPage",
                "@id": page_url + "#faq",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a},
                    }
                    for q, a in faqs
                ],
            },
        ],
    }
    return json.dumps(obj, ensure_ascii=False)


def make_faqs(st):
    name = st["name"]
    lines = line_label(st["lines"])
    interchange_txt = (
        f"Yes, {name} MRT is an interchange station, served by both {lines}."
        if st["interchange"]
        else f"No, {name} MRT is currently served by a single line ({lines}), with no cross-platform interchange."
    )
    if st["note"]:
        interchange_txt += f" {st['note']}"

    school_links_txt = "; ".join(f"{label}" for _, label in st["related_pages"])
    faq4 = (
        f"There are several within a reasonable distance, {school_links_txt}. "
        f"See the school-catchment guides linked on this page for exact distance bands and Phase 2 registration timing for each."
    )

    faqs = [
        (f"What MRT line(s) serve {name}, and is it an interchange?", interchange_txt),
        (f"What's the housing mix like near {name} MRT?", st["housing_para"]),
        (f"Who typically rents near {name}?", st["rental_para"]),
        (f"Are there good primary schools within walking distance of {name} MRT?", faq4),
        (
            f"Who do I speak to about buying or renting near {name}?",
            f"Me. I'm Winfred Quek, CEA R073319H, with Crestbrick. I run a free 30-minute Property Portfolio Analysis on "
            f"Money, Timing, and Safety before recommending any move near {name}, book a call or message me on WhatsApp.",
        ),
    ]
    return faqs


PAGE_TPL = """<!doctype html>
<html lang="en-SG">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{meta_desc}" />
  <meta name="robots" content="index,follow" />
  <meta property="og:title" content="Living near {name} MRT" />
  <meta property="og:description" content="{og_desc}" />
  <meta property="og:type" content="article" />
  <meta property="og:image" content="/img/og-image.jpg" />
  <meta property="og:url" content="https://winfredquek.com/area/{slug}" />
  <meta name="twitter:card" content="summary_large_image" />
  <link rel="canonical" href="https://winfredquek.com/area/{slug}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <script defer src="/_vercel/insights/script.js"></script>
  <link rel="stylesheet" href="/tw.css" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <script type="application/ld+json">{schema_json}</script>
  <style>
    :root{{--bg:#0f1117;--ink:#f0ede6;--ink-soft:#b8b4aa;--ink-muted:#706c64;--accent:#c6a36a;--rule:#252830;}}
    html{{scroll-behavior:smooth;}}
    body{{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;}}
    .serif{{font-family:'Fraunces',Georgia,serif;}}
    .balance{{text-wrap:balance;}}
    .divider{{border-top:1px solid var(--rule);}}
    .section-label{{font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--accent);font-weight:600;}}
    .btn{{display:inline-flex;align-items:center;gap:.5rem;padding:.9rem 1.6rem;border-radius:4px;font-weight:500;font-size:15px;transition:all .15s ease;}}
    .btn-primary{{background:var(--ink);color:var(--bg);}}
    .btn-primary:hover{{background:var(--accent);}}
    .btn-ghost{{color:var(--ink);border:1px solid var(--rule);}}
    .btn-ghost:hover{{border-color:var(--ink);}}
    .topnav a.nav-link{{color:var(--ink-soft);font-size:14px;font-weight:500;}}
    .topnav a.nav-link:hover{{color:var(--ink);}}
    .chip{{display:inline-block;padding:.3rem .75rem;border-radius:999px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;background:#e6e0d6;color:#5a4a30;}}
    #mobile-menu{{display:none;}}#mobile-menu.open{{display:block;}}
  </style>
<link rel="stylesheet" href="/_a11y-fixes.css?v=20260729"><script defer src="/_enhance.js?v=20260729"></script><script defer src="/_schema.js"></script><script defer src="/_nav.js"></script></head>
<body>
  <header class="topnav border-b border-[var(--rule)] bg-[var(--bg)]/90 backdrop-blur sticky top-0 z-40">
    <nav class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
      <a href="/" class="flex items-center gap-2 whitespace-nowrap">
        <span class="serif font-semibold text-lg whitespace-nowrap">Winfred Quek</span>
      </a>

      <div class="hidden xl:flex items-center gap-6">
        <a class="nav-link link-underline active" href="/">Home</a>
        <a class="nav-link link-underline" href="/about">About</a>
        <a class="nav-link link-underline" href="/services">Services</a>
        <a class="nav-link link-underline" href="/pricing">Pricing</a>
        <a class="nav-link link-underline" href="/tools">Tools</a>
        <a class="nav-link link-underline" href="/sell">Sell</a>
        <a class="nav-link link-underline" href="/listings">Listings</a>
        <a class="nav-link link-underline" href="/insights">Insights</a>
        <a class="nav-link link-underline" href="/resources">Resources</a>
        <a class="nav-link link-underline" href="/contact">Contact</a>
        <a href="/property-portfolio-analysis" class="btn btn-ghost text-sm" style="padding:0.55rem 1.1rem;border-color:var(--accent);color:var(--accent);">Property Analysis</a>
        <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm pulse-accent" style="padding:0.55rem 1.1rem;">Book a call</a>
      </div>

      <button id="menu-toggle" class="xl:hidden p-2" aria-label="Open menu">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
    </nav>
    <div id="mobile-menu" class="xl:hidden border-t border-[var(--rule)] bg-[var(--bg)]">
      <div class="px-6 py-4 flex flex-col gap-3">
        <a class="nav-link active" href="/">Home</a>
        <a class="nav-link" href="/about.html">About</a>
        <a class="nav-link" href="/services.html">Services</a>
        <a class="nav-link" href="/listings.html">Listings</a>
        <a class="nav-link" href="/new-launches.html">New Launches</a>
        <a class="nav-link" href="/districts.html">Districts</a>
        <a class="nav-link" href="/buyers-guide.html">Buyers Guide</a>
        <a class="nav-link" href="/sellers-guide.html">Sellers Guide</a>
        <a class="nav-link" href="/insights.html">Insights</a><a class="nav-link" href="/faq.html">FAQ</a>
        <a class="nav-link" href="/track-record.html">Track Record</a>
        <a class="nav-link" href="/testimonials.html">Testimonials</a>
        <a class="nav-link" href="/contact.html">Contact</a>
        <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
      </div>
    </div>
  </header>

<main>
<section class="hero max-w-4xl mx-auto px-6 pt-12 pb-6">
  <p class="section-label mb-4"><a href="/districts" class="hover:text-[var(--ink)]">Districts &amp; areas</a></p>
  <div class="flex items-center gap-3 mb-4">
    <span class="chip">{name} MRT &middot; {lines}</span>
    <span class="chip">{interchange_chip}</span>
  </div>
  <h1 class="serif text-3xl md:text-5xl font-semibold leading-tight balance mb-4">Living near {name} MRT.</h1>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed">{hero_summary}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Getting around</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">What the commute actually looks like.</h2>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-4">{name} MRT is served by {lines_full}{interchange_sentence}</p>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-4">{commute_para}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">The area in one paragraph</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">What {name} feels like to live in.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">{area_para}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Housing stock nearby</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">HDB, condo, and landed within reach of the station.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">{housing_para}</p>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-2">If you have kids, here's the school-catchment picture for this area:</p>
  <ul class="text-[var(--ink-soft)] leading-relaxed list-disc pl-6 mb-4">
{school_links}
  </ul>
  <p class="text-[var(--ink-muted)] text-sm leading-relaxed">For specific transaction comps and BTO ballot odds in this area, message me on <a class="underline hover:text-[var(--ink)]" href="https://wa.me/6581618149">WhatsApp</a>. The math changes month-to-month, so treat the above as a starting orientation, not a valuation.</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Who rents here</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">The rental character of this address.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">{rental_para}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">District &amp; town context</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">Where {name} sits on the map.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">{name} MRT sits within <a class="underline hover:text-[var(--ink)]" href="/districts/{district_file}">{district_label}</a>{hdb_town_sentence}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">FAQ</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">Common questions about living near {name}.</h2>
  <div class="space-y-6">
{faq_html}
  </div>
</section>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Before you commit</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">Money, Timing, and Safety near {name}.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">Wherever you buy near {name}, run the same three checks: the <strong class="text-[var(--ink)]">Money</strong> math (affordability, ABSD exposure, loan quantum), the <strong class="text-[var(--ink)]">Timing</strong> sequencing (school registration windows, BTO wait times, MOP dates if you're upgrading), and the <strong class="text-[var(--ink)]">Safety</strong> margin (holding capacity if rates move or a tenant leaves). A good address does not fix a bad sequence.</p>
</section>

<section class="max-w-4xl mx-auto px-6 py-16 text-center">
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">Thinking about buying or renting near {name}?</h2>
  <p class="text-[var(--ink-soft)] max-w-xl mx-auto mb-8">The free 30-minute Property Portfolio Analysis runs the Money, Timing, and Safety math before you commit to an address.</p>
  <div class="flex flex-wrap justify-center gap-3">
    <a href="https://calendly.com/winfredquekoc" class="btn btn-primary">Book the Property Portfolio Analysis &rarr;</a>
    <a href="/tools" class="btn btn-ghost">Run the tools first</a>
  </div>
</section>
</main>

<footer class="divider bg-[var(--bg)]">
  <div class="max-w-6xl mx-auto px-6 py-14 grid md:grid-cols-3 gap-10">
    <div>
      <p class="serif font-semibold text-lg mb-3">Winfred Quek</p>
      <p class="text-sm text-[var(--ink-soft)] leading-relaxed mb-3">Investor minded property advisor, Singapore.</p>
      <p class="text-xs text-[var(--ink-muted)] leading-relaxed">Salesperson of <strong class="text-[var(--ink-soft)]">Crestbrick Pte Ltd</strong> (CEA Licence No. L31010886H)<br/>CEA Registration No.: <strong class="text-[var(--ink-soft)]">R073319H</strong></p>
    </div>
    <div>
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Quick links</p>
      <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
        <li><a href="/about" class="hover:text-[var(--ink)]">About Winfred</a></li>
        <li><a href="/tools" class="hover:text-[var(--ink)]">Tools</a></li>
        <li><a href="/insights" class="hover:text-[var(--ink)]">Insights</a></li>
        <li><a href="/districts" class="hover:text-[var(--ink)]">Districts</a></li>
      </ul>
    </div>
    <div>
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Contact</p>
      <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
        <li><a href="https://wa.me/6581618149" class="hover:text-[var(--ink)]">WhatsApp &middot; +65 8161 8149</a></li>
        <li><a href="mailto:winfredquekoc@gmail.com" class="hover:text-[var(--ink)]">winfredquekoc@gmail.com</a></li>
        <li><a href="https://calendly.com/winfredquekoc" class="hover:text-[var(--ink)]">Book via Calendly</a></li>
      </ul>
    </div>
  </div>
  <div class="border-t border-[var(--rule)]">
    <div class="max-w-6xl mx-auto px-6 py-6 flex flex-col md:flex-row justify-between gap-3 text-xs text-[var(--ink-muted)]">
      <p>&copy; 2026 Winfred Quek &middot; Crestbrick &middot; CEA R073319H &middot; All rights reserved.</p>
      <div class="flex gap-4"><a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a><span>&middot;</span><span>Information on this site is general and not legal or financial advice.</span></div>
    </div>
  </div>

  <!-- Disclaimer -->
  <div class="disclaimer-block" style="border-top:1px solid rgba(255,255,255,0.08); margin-top:2rem; padding:1.25rem 1.5rem;">
    <p style="font-size:0.72rem; line-height:1.65; color:#706c64; max-width:900px; margin:0 auto; text-align:center;">
      The information and insights provided on this page are for informational purposes only and are based on Winfred's independent research and views. While we strive to ensure accuracy and reliability, we do not guarantee the completeness, correctness, or timeliness of the data presented. Real estate investments are subject to various risks, including but not limited to market fluctuations, changes in economic conditions, interest rate volatility, regulatory shifts, liquidity constraints, and unforeseen property-specific risks. Past performance is not indicative of future results, and investment outcomes may vary. This page does not constitute investment, financial, or professional advice and should not be relied upon as such. Investors should conduct their own due diligence and seek advice from qualified professionals before making any investment decisions.
    </p>
  </div>
</footer>
<script>
(function(){{var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){{m.classList.toggle('open');}});}})();
</script>
</body>
</html>
"""


def make_title(name):
    full = f"Living near {name} MRT: Property Guide | Winfred Quek"
    if len(full) <= 60:
        return full
    short = f"Living near {name} MRT | Winfred Quek"
    return short


def make_meta_desc(st):
    name = st["name"]
    lines = line_label(st["lines"])
    desc = (
        f"Living near {name} MRT: {lines} access, housing stock, rental profile, and nearby school catchments. "
        f"By Winfred Quek, CEA R073319H, Crestbrick."
    )
    if len(desc) > 160:
        desc = f"Living near {name} MRT: {lines} access, housing, rental profile & schools nearby. By Winfred Quek, CEA R073319H."
    return desc


def main():
    sitemap_lines = []
    count = 0
    for st in STATIONS:
        slug = f"living-near-{st['slug']}-mrt"
        name = st["name"]
        lines = line_label(st["lines"])
        lines_full = " and ".join(st["lines"]) if len(st["lines"]) <= 2 else ", ".join(st["lines"][:-1]) + f", and {st['lines'][-1]}"

        faqs = make_faqs(st)
        schema = build_schema(slug, name, st["lines"], faqs)

        interchange_chip = "Interchange station" if st["interchange"] else "Single-line station"
        interchange_sentence = (
            " as an interchange station, so you can change lines without leaving the paid gates."
            if st["interchange"]
            else ", with no cross-platform interchange at this station."
        )

        hero_summary = (
            f"{st['area_para'].split('.')[0]}. This station is served by {lines_full}"
            f"{', an interchange,' if st['interchange'] else ''} "
            f"in {st['district_label']}."
        )

        school_links = "\n".join(
            f'    <li><a class="underline hover:text-[var(--ink)]" href="/area/{s}">{escape(label)}</a></li>'
            for s, label in st["related_pages"]
        )

        faq_html_parts = []
        for q, a in faqs:
            faq_html_parts.append(
                f'    <div>\n      <p class="serif font-semibold text-[var(--ink)] mb-2">{escape(q)}</p>\n'
                f'      <p class="text-[var(--ink-soft)] leading-relaxed">{escape(a)}</p>\n    </div>'
            )
        faq_html = "\n".join(faq_html_parts)

        hdb_town_sentence = ""
        if st.get("hdb_town_file"):
            hdb_town_sentence = (
                f", within the <a class=\"underline hover:text-[var(--ink)]\" href=\"/hdb-towns/{st['hdb_town_file']}\">"
                f"{escape(st['hdb_town_label'])}</a> HDB town."
            )
        else:
            hdb_town_sentence = "."

        title = make_title(name)
        meta_desc = make_meta_desc(st)
        og_desc = f"{st['area_para'][:150].rsplit(' ', 1)[0]}…" if len(st["area_para"]) > 150 else st["area_para"]

        html = PAGE_TPL.format(
            title=escape(title),
            meta_desc=escape(meta_desc),
            og_desc=escape(og_desc),
            slug=escape(slug),
            name=escape(name),
            lines=escape(lines),
            lines_full=escape(lines_full),
            interchange_chip=interchange_chip,
            interchange_sentence=escape(interchange_sentence),
            hero_summary=escape(hero_summary),
            commute_para=escape(st["commute_para"]),
            area_para=escape(st["area_para"]),
            housing_para=escape(st["housing_para"]),
            rental_para=escape(st["rental_para"]),
            school_links=school_links,
            district_file=escape(st["district_file"]),
            district_label=escape(st["district_label"]),
            hdb_town_sentence=hdb_town_sentence,
            faq_html=faq_html,
            schema_json=schema,
        )

        out_path = OUT / f"{slug}.html"
        out_path.write_text(html, encoding="utf-8")
        sitemap_lines.append(
            f"  <url><loc>https://winfredquek.com/area/{slug}</loc><lastmod>{LASTMOD}</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>"
        )
        count += 1
        print(f"  wrote {out_path.name}  (title len={len(title)}, meta len={len(meta_desc)})")

    print(f"\nWrote {count} MRT living-guide pages to {OUT}")

    frag = ROOT / "_mrt-sitemap-fragment.xml"
    frag.write_text("\n".join(sitemap_lines) + "\n", encoding="utf-8")
    print(f"Wrote sitemap fragment ({len(sitemap_lines)} lines) to {frag}")


if __name__ == "__main__":
    main()
