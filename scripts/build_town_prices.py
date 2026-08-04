#!/usr/bin/env python3
"""build_town_prices.py — generate /hdb-prices/ programmatic SEO pages.

Reads public/data/valuation-hdb.json (26 HDB towns x flat types x storey
bands, built from data.gov.sg resale transactions) and writes one static
page per town plus an index, aimed at sellers searching "[town] HDB resale
prices". Every page funnels to the instant valuation tool (/tools/valuation)
and the seller landing page (/sell-with-winfred).

Usage:
    python3 scripts/build_town_prices.py [--help]

Refresh flow (LAZY, on demand only — this script does NOT wire up any
launchd/cron job, and never should):
    1. python3 scripts/build_valuation_data.py   # refreshes the source JSON
    2. python3 scripts/build_town_prices.py      # regenerates these pages

The generator is idempotent and deterministic: given the same
valuation-hdb.json, it produces byte-identical output on every run. It does
not read the wall clock — all dates in the output come from the JSON's own
"generated" and "month_range" fields.

Aggregation method (see AGGREGATION_NOTE below for the exact wording used
on every page): the source JSON gives a median price, median psm, and
transaction count per (town, flat type, storey band) cell. There is no
raw per-transaction data to compute a true pooled median from, so every
"typical price" and "typical psm" figure on these pages is the
transaction-count-weighted average of the per-band median figures for that
group. This is stated explicitly wherever the number appears — it is
never called a "median" on its own.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "public" / "data" / "valuation-hdb.json"
OUT_DIR = ROOT / "public" / "hdb-prices"
SITE = "https://winfredquek.com"
ASSET_VERSION = "20260805"

MONTH_NAMES = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}

AGGREGATION_NOTE = (
    "“Typical price” is the transaction-count weighted average of the "
    "median resale price recorded in each floor band (01 to 06, 07 to 12, "
    "13 and above) for that group. It is not a pooled median across "
    "individual transactions — the source data only provides a median "
    "per floor band, not a full transaction list."
)


def slugify(town):
    s = town.strip().lower().replace("/", "-").replace(" ", "-")
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def town_label(town):
    # "KALLANG/WHAMPOA" -> "Kallang/Whampoa", "BUKIT BATOK" -> "Bukit Batok"
    return town.title()


def flat_type_label(ft):
    if ft == "EXECUTIVE":
        return "Executive"
    if ft == "MULTI-GENERATION":
        return "Multi-Generation"
    num, word = ft.split(" ", 1)
    return f"{num}-{word.title()}"


def money(n):
    return f"${round(n):,}"


def psm(n):
    return f"${round(n):,} psm"


def month_label(ym):
    y, m = ym.split("-")
    return f"{MONTH_NAMES[m]} {y}"


def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def weighted(cells):
    """cells: list of cell dicts (non-null). Returns (price, psm, count)."""
    total = sum(c["count"] for c in cells)
    price = sum(c["median_price"] * c["count"] for c in cells) / total
    psm_v = sum(c["median_psm"] * c["count"] for c in cells) / total
    return price, psm_v, total


def build_town_records(data):
    """Returns list of town records in data['towns'] order, each with
    per-flat-type aggregates (in data['flat_types'] order) and a town-wide
    weighted aggregate."""
    cells = data["cells"]
    flat_types_order = data["flat_types"]
    band_order = list(data["storey_bands"].keys())
    band_display = data["storey_bands"]

    records = []
    for town in data["towns"]:
        flat_type_rows = []
        all_town_cells = []
        for ft in flat_types_order:
            band_cells = []
            bands_present = []
            for band in band_order:
                key = f"{town}|{ft}|{band}"
                cell = cells.get(key)
                if cell is None:
                    continue
                band_cells.append(cell)
                bands_present.append(band)
            if not band_cells:
                continue
            price, psm_v, count = weighted(band_cells)
            all_town_cells.extend(band_cells)
            flat_type_rows.append({
                "flat_type": ft,
                "label": flat_type_label(ft),
                "price": price,
                "psm": psm_v,
                "count": count,
                "bands": [band_display[b] for b in bands_present],
                "band_keys": bands_present,
            })
        if not flat_type_rows:
            continue
        town_price, town_psm, town_count = weighted(all_town_cells)
        records.append({
            "town": town,
            "label": town_label(town),
            "slug": slugify(town),
            "flat_types": flat_type_rows,
            "overall_price": town_price,
            "overall_psm": town_psm,
            "overall_count": town_count,
        })
    return records


def observations(rec):
    fts = rec["flat_types"]
    town = rec["label"]
    obs = []

    most = max(fts, key=lambda r: r["count"])
    obs.append(
        f"The {most['label']} flat type was the most transacted in {town} "
        f"from {{month_from}} to {{month_to}}, with {most['count']} resale "
        f"transactions."
    )

    by_price = sorted(fts, key=lambda r: r["price"])
    lo, hi = by_price[0], by_price[-1]
    if lo is not hi:
        obs.append(
            f"Typical resale prices in {town} ranged from {money(lo['price'])} "
            f"for a {lo['label']} flat to {money(hi['price'])} for a "
            f"{hi['label']} flat over the same period."
        )

    by_psm = sorted(fts, key=lambda r: r["psm"])
    lo_p, hi_p = by_psm[0], by_psm[-1]
    if lo_p is not hi_p:
        obs.append(
            f"{hi_p['label']} flats recorded the highest median price per "
            f"square metre in {town} at {psm(hi_p['psm'])}, against "
            f"{psm(lo_p['psm'])} for {lo_p['label']} flats."
        )
    return obs[:3]


def quick_answer(rec, month_from, month_to):
    fts = rec["flat_types"]
    by_price = sorted(fts, key=lambda r: r["price"])
    lo, hi = by_price[0], by_price[-1]
    town = rec["label"]
    if lo is hi:
        range_sentence = (
            f"The only flat type with enough recorded transactions was "
            f"{lo['label']}, at {money(lo['price'])}."
        )
    else:
        range_sentence = (
            f"Prices ranged from {money(lo['price'])} for a {lo['label']} "
            f"flat to {money(hi['price'])} for a {hi['label']} flat, based "
            f"on {rec['overall_count']} resale transactions."
        )
    return (
        f"In {town}, HDB resale flats typically sold for "
        f"{money(rec['overall_price'])} across all flat types, weighted by "
        f"transaction volume, from {month_from} to {month_to}. "
        f"{range_sentence} Source: data.gov.sg HDB Resale Flat Prices."
    )


NAV = (ROOT / "_templates" / "nav.html").read_text(encoding="utf-8").rstrip("\n")

STYLE_BLOCK = """
  <style>
    :root{--bg:#0f1117;--highlight:#1a1c24;--ink:#f0ede6;--ink-soft:#b8b4aa;--ink-muted:#706c64;--accent:#c6a36a;--rule:#252830;}
    html{scroll-behavior:smooth;}
    body{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;}
    .serif{font-family:'Fraunces',Georgia,serif;}
    .balance{text-wrap:balance;}
    .divider{border-top:1px solid var(--rule);}
    .section-label{font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--accent);font-weight:600;}
    .btn{display:inline-flex;align-items:center;gap:.5rem;padding:.9rem 1.6rem;border-radius:4px;font-weight:500;font-size:15px;transition:all .15s ease;}
    .btn-primary{background:var(--ink);color:var(--bg);}
    .btn-primary:hover{background:var(--accent);}
    .btn-ghost{border:1px solid rgba(198,163,106,.5);color:var(--ink);}
    .btn-ghost:hover{border-color:var(--accent);color:var(--accent);}
    #mobile-menu{display:none;}#mobile-menu.open{display:block;}
    .topnav a.nav-link{color:var(--ink-soft);font-size:14px;font-weight:500;}
    .topnav a.nav-link:hover{color:var(--ink);}
    article p{font-size:1rem;line-height:1.75;color:var(--ink-soft);margin-bottom:1em;}
    article h2{font-family:'Fraunces',Georgia,serif;font-size:1.5rem;font-weight:600;margin:2.5em 0 .8em;color:var(--ink);}
    .sub-note{font-size:.9rem;color:var(--ink-muted);margin:0 0 1rem;}
    .price-table{width:100%;border-collapse:collapse;margin:1rem 0 .5rem;font-size:.92rem;}
    .price-table th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);font-weight:600;padding:.6rem .8rem;border-bottom:1px solid var(--rule);}
    .price-table td{padding:.75rem .8rem;border-bottom:1px solid var(--rule);color:var(--ink-soft);}
    .price-table td.num{color:var(--ink);font-variant-numeric:tabular-nums;}
    .price-table tr:last-child td{border-bottom:none;}
    .table-wrap{overflow-x:auto;border:1px solid var(--rule);border-radius:8px;}
    .table-note{font-size:.78rem;color:var(--ink-muted);margin:.6rem 0 1.5rem;line-height:1.6;}
    .obs-list{margin:0 0 1.5rem;padding:0;list-style:none;}
    .obs-list li{position:relative;padding-left:1.4rem;margin-bottom:.85rem;color:var(--ink-soft);line-height:1.7;}
    .obs-list li::before{content:'\\2022';position:absolute;left:0;color:var(--accent);}
    .cta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;margin:2rem 0;}
    .cta-card{background:var(--highlight);border:1px solid var(--rule);border-radius:10px;padding:1.5rem;}
    .cta-card p{font-size:.88rem;color:var(--ink-soft);margin:.5rem 0 1rem;line-height:1.6;}
    .cta-card a{display:inline-block;}
    .town-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.75rem;margin:1.5rem 0 2.5rem;}
    .town-card{background:var(--highlight);border:1px solid var(--rule);border-radius:8px;padding:1rem 1.2rem;transition:border-color .15s ease;}
    .town-card:hover{border-color:var(--accent);}
    .town-card a{font-weight:600;font-size:.95rem;color:var(--ink);text-decoration:none;}
    .town-card a:hover{color:var(--accent);}
    .town-card .price{display:block;font-size:1.15rem;font-weight:600;color:var(--accent);margin-top:.3rem;}
    .town-card .meta{font-size:.75rem;color:var(--ink-muted);margin-top:.2rem;}
  </style>""".rstrip("\n")

HEAD_ASSETS = f"""  <link rel="stylesheet" href="/tw.css" />
  <link rel="preload" href="/fonts/inter-latin-400.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="preload" href="/fonts/fraunces-latin-700.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/fonts/fonts.css">
{STYLE_BLOCK}
  <link rel="stylesheet" href="/motion.css" media="print" onload="this.media='all'"><noscript><link rel="stylesheet" href="/motion.css"></noscript>
  <script defer src="/motion.js"></script>
<link rel="stylesheet" href="/_a11y-fixes.css?v={ASSET_VERSION}" media="print" onload="this.media='all'"><noscript><link rel="stylesheet" href="/_a11y-fixes.css?v={ASSET_VERSION}"></noscript><script defer src="/_enhance.js?v={ASSET_VERSION}"></script><script defer src="/_schema.js"></script><script defer src="/_nav.js"></script>"""

FOOTER = """<footer class="divider bg-[var(--bg)]">
  <div class="max-w-6xl mx-auto px-6 py-10 flex flex-col md:flex-row justify-between gap-6 text-sm text-[var(--ink-muted)]">
    <p>© 2026 Winfred Quek · Crestbrick · CEA R073319H</p>
    <div class="flex gap-4"><a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a><span>·</span><span>Not legal or financial advice.</span></div>
  </div>
  <div style="border-top:1px solid rgba(255,255,255,0.08);padding:1.25rem 1.5rem;">
    <p style="font-size:0.72rem;line-height:1.65;color:#706c64;max-width:900px;margin:0 auto;text-align:center;">The information and insights provided on this page are for informational purposes only. Real estate investments are subject to various risks including market fluctuations, regulatory changes, and property-specific risks. This page does not constitute investment, financial, or professional advice. CEA R073319H. Crestbrick Pte Ltd L31010886H.</p>
  </div>
</footer>

<a id="wa-float" href="https://wa.me/6581618149?text=Hi%20Winfred%2C%20I%27d%20like%20a%20free%20valuation%20for%20my%20flat." class="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full bg-[#25D366] px-5 py-3 text-white font-medium shadow-lg" style="box-shadow:0 10px 30px -5px rgba(37,211,102,.4);">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
  <span class="hidden sm:inline">Chat</span>
</a>
<script>(function(){var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){m.classList.toggle('open');});})();</script>"""

CTA_BLOCK = """  <div class="cta-grid">
    <div class="cta-card">
      <span class="section-label">Free tool</span>
      <p class="serif" style="font-size:1.1rem;font-weight:600;color:var(--ink);margin-top:.5rem;">Get an instant estimate for your flat</p>
      <p>Enter your town, flat type, floor area and storey band for an indicative price range in seconds.</p>
      <a href="/tools/valuation" class="btn btn-primary" style="padding:.7rem 1.4rem;font-size:.88rem;">Get an instant estimate</a>
    </div>
    <div class="cta-card">
      <span class="section-label">Free, within 24 hours</span>
      <p class="serif" style="font-size:1.1rem;font-weight:600;color:var(--ink);margin-top:.5rem;">Thinking of selling?</p>
      <p>Winfred prepares a detailed valuation report free within 24 hours, personally reviewed against comparable transactions for your exact unit.</p>
      <a href="/sell-with-winfred" class="btn btn-ghost" style="padding:.7rem 1.4rem;font-size:.88rem;">Request your valuation report</a>
    </div>
  </div>"""


def page_head(title, description, canonical_path, json_ld_blocks):
    canonical_url = f"{SITE}{canonical_path}"
    ld = "\n".join(
        f'<script type="application/ld+json">{json.dumps(b, separators=(",", ":"))}</script>'
        for b in json_ld_blocks
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="robots" content="index,follow,max-image-preview:large" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{description}" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{description}" />
  <meta property="og:image" content="{SITE}/img/og-image.jpg" />
  <meta property="og:url" content="{canonical_url}" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{title}" />
  <meta name="twitter:description" content="{description}" />
  <meta name="twitter:image" content="{SITE}/img/og-image.jpg" />
  <link rel="canonical" href="{canonical_url}" />
{ld}
{HEAD_ASSETS}</head>
<body>

{NAV}

<main>
"""


def page_tail():
    return f"""</main>

{FOOTER}
</body>
</html>
"""


def render_town_page(rec, data):
    town = rec["label"]
    slug = rec["slug"]
    month_from = month_label(data["month_range"]["from"])
    month_to = month_label(data["month_range"]["to"])
    updated = data["generated"]
    canonical_path = f"/hdb-prices/{slug}"

    qa = quick_answer(rec, month_from, month_to)
    obs = [o.replace("{month_from}", month_from).replace("{month_to}", month_to) for o in observations(rec)]

    title = f"{town} HDB Resale Prices 2026 | Winfred Quek"
    description = (
        f"{town} HDB resale prices {month_from} to {month_to}: typical "
        f"price {money(rec['overall_price'])} across {rec['overall_count']} "
        f"resale transactions, by flat type, from data.gov.sg."
    )

    # ---- table ----
    rows = []
    for r in rec["flat_types"]:
        bands = ", ".join(r["bands"])
        rows.append(
            f"""            <tr>
              <td>{r['label']}</td>
              <td class="num">{money(r['price'])}</td>
              <td class="num">{psm(r['psm'])}</td>
              <td class="num">{r['count']}</td>
              <td>{bands}</td>
            </tr>"""
        )
    table_html = "\n".join(rows)

    obs_html = "\n".join(f"            <li>{o}</li>" for o in obs)

    # ---- JSON-LD ----
    dataset_ld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"HDB Resale Flat Prices — {town}, {month_from} to {month_to}",
        "description": f"Median resale price, price per square metre and transaction count by flat type and storey band for {town}, Singapore, {month_from} to {month_to}.",
        "url": f"{SITE}{canonical_path}",
        "temporalCoverage": f"{data['month_range']['from']}/{data['month_range']['to']}",
        "spatialCoverage": {"@type": "Place", "name": f"{town}, Singapore"},
        "creator": {"@type": "Organization", "name": "Singapore Government (data.gov.sg)"},
        "citation": data["source"],
        "distribution": {
            "@type": "DataDownload",
            "encodingFormat": "text/html",
            "contentUrl": f"{SITE}{canonical_path}",
        },
        "publisher": {"@type": "Organization", "name": "Crestbrick Pte Ltd", "identifier": "L31010886H"},
        "dateModified": updated,
    }
    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "HDB Resale Prices", "item": f"{SITE}/hdb-prices"},
            {"@type": "ListItem", "position": 3, "name": town, "item": f"{SITE}{canonical_path}"},
        ],
    }
    most = max(rec["flat_types"], key=lambda r: r["count"])
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f"What is the typical HDB resale price in {town}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"Across all flat types in {town}, the transaction-count weighted typical resale price was {money(rec['overall_price'])} from {month_from} to {month_to}, based on {rec['overall_count']} resale transactions recorded on data.gov.sg.",
                },
            },
            {
                "@type": "Question",
                "name": f"Which HDB flat type sells most often in {town}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"The {most['label']} flat type had the most resale transactions in {town} from {month_from} to {month_to}, with {most['count']} recorded transactions and a typical price of {money(most['price'])}.",
                },
            },
            {
                "@type": "Question",
                "name": f"What is the price per square metre for a {most['label']} flat in {town}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"A {most['label']} flat in {town} had a typical price of {psm(most['psm'])} from {month_from} to {month_to}, the transaction-count weighted average across the floor bands with sufficient data ({', '.join(most['bands'])}).",
                },
            },
        ],
    }

    faq_items = faq_ld["mainEntity"]
    faq_html_parts = []
    for q in faq_items:
        faq_html_parts.append(
            f"""    <div style="border-top:1px solid #252830;padding:1rem 0;">
      <h3 class="geo-faq-q" style="font-size:1.08rem;font-weight:600;margin-bottom:.4rem;color:#f0ede6;">{q['name']}</h3>
      <p style="color:#b8b4aa;line-height:1.7;margin:0;">{q['acceptedAnswer']['text']}</p>
    </div>"""
        )
    faq_html = "\n".join(faq_html_parts)

    body = f"""<section style="background:var(--highlight);padding:4rem 1.5rem 3rem;border-bottom:1px solid var(--rule);">
  <div style="max-width:72rem;margin:0 auto;">
    <p class="section-label" style="margin-bottom:.5rem;"><a href="/hdb-prices" style="color:var(--accent);">HDB Resale Prices</a></p>
    <h1 class="serif balance" style="font-size:clamp(2rem,4vw,3rem);font-weight:700;line-height:1.1;color:var(--ink);margin-bottom:.75rem;">{town} HDB resale prices 2026</h1>
    <div class="geo-quick-answer" style="max-width:65ch;margin:0 0 1.25rem;padding:1rem 1.25rem;background:#1a1c24;border:1px solid #252830;border-left:3px solid #c6a36a;border-radius:8px;color:#f0ede6;font-size:1.02rem;line-height:1.7;"><strong style="color:#c6a36a;">Quick answer:</strong> {qa}</div>
    <p style="font-size:.85rem;color:var(--ink-muted);">By Winfred Quek · CEA R073319H · Data updated {updated}</p>
  </div>
</section>

<article class="max-w-4xl mx-auto px-6 pt-10 pb-24">

  <h2>Resale prices by flat type</h2>
  <p class="sub-note">{month_from} to {month_to}, data.gov.sg HDB resale transactions.</p>
  <div class="table-wrap">
    <table class="price-table">
      <thead>
        <tr><th>Flat type</th><th>Typical price</th><th>Typical price psm</th><th>Transactions</th><th>Floor bands</th></tr>
      </thead>
      <tbody>
{table_html}
      </tbody>
    </table>
  </div>
  <p class="table-note">{AGGREGATION_NOTE}</p>

  <h2>What the numbers show</h2>
  <ul class="obs-list">
{obs_html}
  </ul>

{CTA_BLOCK}

  <section class="geo-faq" style="max-width:48rem;margin:2.5rem auto;padding:0;">
    <h2 style="font-family:'Fraunces',Georgia,serif;font-size:1.6rem;margin-bottom:1rem;">Frequently asked questions</h2>
{faq_html}
  </section>

  <p class="text-sm" style="color:var(--ink-muted);margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--rule);">Source: {data['source']}, {month_from} to {month_to}. Winfred Quek is an Associate Marketing Consultant at Crestbrick Pte Ltd (CEA R073319H). This page is for general information only and does not constitute financial, investment, or legal advice. Figures are historical resale transaction data, not a valuation of any specific unit or a prediction of future prices.</p>
</article>
"""

    html = page_head(title, description, canonical_path, [dataset_ld, breadcrumb_ld, faq_ld]) + body + page_tail()
    return html


def render_index_page(records, data):
    month_from = month_label(data["month_range"]["from"])
    month_to = month_label(data["month_range"]["to"])
    updated = data["generated"]
    canonical_path = "/hdb-prices"

    title = "HDB Resale Prices by Town, 2026 | Winfred Quek"
    description = (
        f"HDB resale prices across all 26 Singapore towns, {month_from} to "
        f"{month_to}, from data.gov.sg resale transactions. Typical "
        f"price by flat type for every town."
    )

    sorted_records = sorted(records, key=lambda r: r["label"])
    town_cards = "\n".join(
        f"""    <div class="town-card">
      <a href="/hdb-prices/{r['slug']}">{r['label']}</a>
      <span class="price">{money(r['overall_price'])}</span>
      <span class="meta">{r['overall_count']} transactions · {len(r['flat_types'])} flat types</span>
    </div>"""
        for r in sorted_records
    )

    overall_low = min(records, key=lambda r: r["overall_price"])
    overall_high = max(records, key=lambda r: r["overall_price"])

    qa = (
        f"Across all 26 HDB towns tracked, typical resale prices from "
        f"{month_from} to {month_to} ran from {money(overall_low['overall_price'])} "
        f"in {overall_low['label']} to {money(overall_high['overall_price'])} in "
        f"{overall_high['label']}, weighted by transaction volume within each town. "
        f"Source: data.gov.sg HDB Resale Flat Prices."
    )

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "HDB Resale Prices", "item": f"{SITE}{canonical_path}"},
        ],
    }
    collection_ld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": f"{SITE}{canonical_path}",
        "dateModified": updated,
        "publisher": {"@type": "Organization", "name": "Crestbrick Pte Ltd", "identifier": "L31010886H"},
        "hasPart": [
            {
                "@type": "Dataset",
                "name": f"HDB Resale Flat Prices — {r['label']}",
                "url": f"{SITE}/hdb-prices/{r['slug']}",
            }
            for r in sorted_records
        ],
    }

    body = f"""<section style="background:var(--highlight);padding:4rem 1.5rem 3rem;border-bottom:1px solid var(--rule);">
  <div style="max-width:72rem;margin:0 auto;">
    <p class="section-label" style="margin-bottom:.5rem;">HDB Resale Prices</p>
    <h1 class="serif balance" style="font-size:clamp(2rem,4vw,3rem);font-weight:700;line-height:1.1;color:var(--ink);margin-bottom:.75rem;">HDB resale prices by town, 2026</h1>
    <div class="geo-quick-answer" style="max-width:65ch;margin:0 0 1.25rem;padding:1rem 1.25rem;background:#1a1c24;border:1px solid #252830;border-left:3px solid #c6a36a;border-radius:8px;color:#f0ede6;font-size:1.02rem;line-height:1.7;"><strong style="color:#c6a36a;">Quick answer:</strong> {qa}</div>
    <p style="color:var(--ink-soft);max-width:65ch;margin-bottom:1rem;">Pick your town for a full breakdown by flat type, backed by real data.gov.sg resale transactions, not a guess.</p>
    <p style="font-size:.85rem;color:var(--ink-muted);">By Winfred Quek · CEA R073319H · Data updated {updated}</p>
  </div>
</section>

<article class="max-w-4xl mx-auto px-6 pt-10 pb-24">
  <div class="town-grid">
{town_cards}
  </div>

{CTA_BLOCK}

  <p class="text-sm" style="color:var(--ink-muted);margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--rule);">Source: {data['source']}, {month_from} to {month_to}. Each town's typical price is the transaction-count weighted average of median resale prices across flat types and floor bands — see each town page for the full breakdown. Winfred Quek is an Associate Marketing Consultant at Crestbrick Pte Ltd (CEA R073319H). This page is for general information only and does not constitute financial, investment, or legal advice.</p>
</article>
"""

    html = page_head(title, description, canonical_path, [collection_ld, breadcrumb_ld]) + body + page_tail()
    return html


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate /hdb-prices/ programmatic SEO pages from "
            "public/data/valuation-hdb.json. Refresh flow: rerun "
            "scripts/build_valuation_data.py, then this script. Lazy "
            "refresh by design — no cron/launchd wiring is created."
        )
    )
    parser.parse_args()

    data = load_data()
    records = build_town_records(data)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for rec in records:
        html = render_town_page(rec, data)
        out_path = OUT_DIR / f"{rec['slug']}.html"
        out_path.write_text(html, encoding="utf-8")

    index_html = render_index_page(records, data)
    (OUT_DIR / "index.html").write_text(index_html, encoding="utf-8")

    print(f"Generated {len(records)} town pages + index in {OUT_DIR}")


if __name__ == "__main__":
    main()
