#!/usr/bin/env python3
"""Build public/mop/index.html + public/mop/<town-slug>.html — the MOP
(Minimum Occupation Period) wave pages.

WHY: owners whose HDB flats crossed the 5-year MOP are the biggest
predictable seller wave in Singapore. A flat with lease_commence_date in
2020-2021 hits MOP roughly 2025-2027. This script does not guess who is
selling — it pulls the last 6 full months of actual HDB resale
TRANSACTIONS from data.gov.sg for exactly that lease-commence cohort.
Those resales ARE the post MOP wave: real, dated evidence, not a forecast.

DATA SOURCE: data.gov.sg dataset d_8b84c4ee58e3cfc0ece0d773c8ca6abc
("Resale flat prices based on registration date"), via the
datastore_search API. Filtered client-side to lease_commence_date in
{2020, 2021} because the API's `filters` param only supports equality,
not a range.

TOWN SELECTION IS DATA-DRIVEN: every town is aggregated, then ranked by
transaction count for the 2020-2021 lease-commence cohort. The top 6-8
towns by volume become the page list — the data chooses which towns get
a page, not an assumption made in advance.

REFRESH MODEL (lazy, on demand — no cron/launchd wiring):
  Re-run this script whenever the MOP pages need fresh numbers:
      python3 scripts/build_mop_pages.py
  It re-fetches from data.gov.sg, recomputes every aggregate, and
  overwrites public/mop/index.html and public/mop/<town-slug>.html in
  place. Nothing here schedules itself — per project convention,
  reference/content data refreshes lazily on invocation, not on a timer.

Usage: python3 scripts/build_mop_pages.py
"""
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

RESOURCE_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
API = "https://data.gov.sg/api/action/datastore_search"
PAGE_SIZE = 1000
LEASE_COMMENCE_YEARS = {2020, 2021}
MIN_TOWN_TXNS = 8        # a town needs at least this many matching txns to qualify for a page
MIN_FLAT_TYPE_TXNS = 3   # flat-type rows below this are folded into "Other flat types"
TOWN_PAGE_MIN = 6
TOWN_PAGE_MAX = 8
OUT_DIR = "public/mop"

SITE = "https://winfredquek.com"
WA_NUMBER = "6581618149"


def last_n_full_months(n, today=None):
    """Return the n most recent FULL calendar months as 'YYYY-MM' strings,
    oldest first. Today's own (partial) month is excluded."""
    today = today or date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")
    return list(reversed(months))


def fetch_month(month, max_retries=6):
    """Paginate datastore_search for one month, with exponential backoff on 429s."""
    records = []
    offset = 0
    while True:
        params = {
            "resource_id": RESOURCE_ID,
            "filters": json.dumps({"month": month}),
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        url = API + "?" + urllib.parse.urlencode(params)
        data = None
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = min(60, 5 * (2 ** attempt))
                    print(f"  429 on {month} offset {offset}, backing off {wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
                raise
        else:
            raise RuntimeError(f"Failed to fetch {month} offset {offset} after {max_retries} retries (rate limited)")

        if not data or not data.get("success"):
            code = (data or {}).get("code")
            if code == 24:  # TOO_MANY_REQUESTS shaped as success:false
                time.sleep(12)
                continue
            raise RuntimeError(f"API error for {month} offset {offset}: {data}")

        result = data["result"]
        batch = result["records"]
        records.extend(batch)
        total = result.get("total", len(records))
        offset += len(batch)
        time.sleep(0.6)  # be polite, stay under the anonymous rate limit
        if len(batch) == 0 or offset >= total:
            break
    return records


def percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def slugify(town):
    return town.strip().lower().replace("/", "-").replace(" ", "-")


def town_display(town):
    return town.strip().title()


def fetch_all(months):
    all_records = []
    for month in months:
        print(f"Fetching {month}...")
        recs = fetch_month(month)
        print(f"  {len(recs)} records")
        all_records.extend(recs)
    return all_records


def filter_mop_cohort(records):
    """Keep only rows whose lease_commence_date falls in LEASE_COMMENCE_YEARS."""
    kept = []
    dropped = 0
    for r in records:
        try:
            lease_year = int(str(r.get("lease_commence_date", "")).strip())
            price = float(r["resale_price"])
            area = float(r["floor_area_sqm"])
            if area <= 0:
                dropped += 1
                continue
        except (ValueError, TypeError, KeyError):
            dropped += 1
            continue
        if lease_year not in LEASE_COMMENCE_YEARS:
            continue
        kept.append({
            "town": r["town"],
            "flat_type": r["flat_type"],
            "price": price,
            "psm": price / area,
            "lease_year": lease_year,
        })
    print(f"\n{len(kept)} transactions match lease_commence_date in {sorted(LEASE_COMMENCE_YEARS)} "
          f"({dropped} rows dropped for malformed fields)")
    return kept


def aggregate_by_town(rows):
    by_town = defaultdict(list)
    for row in rows:
        by_town[row["town"]].append(row)

    towns = {}
    for town, town_rows in by_town.items():
        prices = sorted(r["price"] for r in town_rows)
        psms = sorted(r["psm"] for r in town_rows)

        by_ft = defaultdict(list)
        for r in town_rows:
            by_ft[r["flat_type"]].append(r)

        flat_types = []
        other_rows = []
        for ft, ft_rows in by_ft.items():
            if len(ft_rows) >= MIN_FLAT_TYPE_TXNS:
                ft_prices = sorted(r["price"] for r in ft_rows)
                ft_psms = sorted(r["psm"] for r in ft_rows)
                flat_types.append({
                    "flat_type": ft,
                    "count": len(ft_rows),
                    "median_price": round(statistics.median(ft_prices)),
                    "median_psm": round(statistics.median(ft_psms)),
                })
            else:
                other_rows.extend(ft_rows)
        flat_types.sort(key=lambda x: -x["count"])
        if other_rows:
            other_prices = sorted(r["price"] for r in other_rows)
            other_psms = sorted(r["psm"] for r in other_rows)
            flat_types.append({
                "flat_type": "Other flat types",
                "count": len(other_rows),
                "median_price": round(statistics.median(other_prices)),
                "median_psm": round(statistics.median(other_psms)),
            })

        top_flat_type = max(by_ft.items(), key=lambda kv: len(kv[1]))[0]

        towns[town] = {
            "town": town,
            "display": town_display(town),
            "slug": slugify(town),
            "count": len(town_rows),
            "median_price": round(statistics.median(prices)),
            "median_psm": round(statistics.median(psms)),
            "min_price": round(min(prices)),
            "max_price": round(max(prices)),
            "p25_price": round(percentile(prices, 0.25)),
            "p75_price": round(percentile(prices, 0.75)),
            "top_flat_type": top_flat_type,
            "flat_types": flat_types,
        }
    return towns


def select_towns(towns):
    qualifying = [t for t in towns.values() if t["count"] >= MIN_TOWN_TXNS]
    qualifying.sort(key=lambda t: -t["count"])
    selected = qualifying[:TOWN_PAGE_MAX]
    if len(selected) < TOWN_PAGE_MIN:
        # not enough towns clear the volume floor — fall back to top-N overall
        all_sorted = sorted(towns.values(), key=lambda t: -t["count"])
        selected = all_sorted[:TOWN_PAGE_MIN]
    return selected


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

HEAD_ASSETS = """  <link rel="stylesheet" href="/tw.css" />
  <link rel="preload" href="/fonts/inter-latin-400.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="preload" href="/fonts/fraunces-latin-700.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/fonts/fonts.css">
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
    .btn-ghost{color:var(--ink);border:1px solid var(--rule);}
    .btn-ghost:hover{border-color:var(--ink);}
    #mobile-menu{display:none;}#mobile-menu.open{display:block;}
    .topnav a.nav-link{color:var(--ink-soft);font-size:14px;font-weight:500;}
    .topnav a.nav-link:hover{color:var(--ink);}
    .topnav a.nav-link.active{color:var(--ink);}
    .topnav a.nav-link.active::after{content:"";display:block;height:1px;background:var(--accent);margin-top:4px;}
    article p{font-size:1.02rem;line-height:1.75;color:var(--ink-soft);margin-bottom:1.1em;}
    article h2{font-family:'Fraunces',Georgia,serif;font-size:1.5rem;font-weight:600;margin:2.2em 0 .6em;color:var(--ink);}
    article ul{padding-left:1.4em;color:var(--ink-soft);margin-bottom:1.1em;list-style:disc;}
    article ul li{margin-bottom:.35em;}
    .quick-answer{background:#1a1c24;border:1px solid var(--rule);border-left:3px solid var(--accent);border-radius:8px;padding:1.1rem 1.35rem;color:var(--ink);margin:0 0 1.5rem;font-size:1.02rem;line-height:1.7;}
    .quick-answer strong{color:var(--accent);}
    .data-table{width:100%;border-collapse:collapse;margin:0 0 1.5rem;font-size:.92rem;}
    .data-table th{text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-muted);font-weight:600;padding:.6rem .75rem;border-bottom:1px solid var(--rule);}
    .data-table td{padding:.7rem .75rem;border-bottom:1px solid var(--rule);color:var(--ink-soft);}
    .data-table tr:last-child td{border-bottom:none;}
    .stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.9rem;margin:0 0 1.75rem;}
    .stat-card{background:var(--highlight);border:1px solid var(--rule);border-radius:10px;padding:1rem 1.1rem;}
    .stat-card .label{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-muted);font-weight:600;}
    .stat-card .value{font-family:'Fraunces',Georgia,serif;font-size:1.35rem;color:var(--ink);margin-top:.3rem;}
    .town-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem;margin:0 0 1.5rem;}
    .town-card{background:var(--highlight);border:1px solid var(--rule);border-radius:10px;padding:1.25rem;transition:border-color .15s ease;}
    .town-card:hover{border-color:var(--accent);}
    .town-card a{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:1.1rem;color:var(--ink);text-decoration:none;}
    .town-card a:hover{color:var(--accent);}
    .town-card p{font-size:.85rem;color:var(--ink-muted);margin:.5rem 0 0;line-height:1.5;}
    .article-cta{background:#fbf6ec;border:1px solid #e6e0d6;border-radius:12px;padding:2rem;margin:2.5rem 0;text-align:center;}
    .cta-row{display:flex;flex-wrap:wrap;gap:.75rem;justify-content:center;margin-top:1.25rem;}
    .cta-row a{display:inline-block;padding:.85rem 1.6rem;border-radius:4px;font-weight:600;text-decoration:none;font-size:.92rem;}
    .cta-primary{background:#c6a36a;color:#fff;}
    .cta-secondary{background:#fff;color:#1a1a1a;border:1px solid #d8d2c4;}
    .cta-tertiary{background:#25D366;color:#fff;}
  </style>
  <link rel="stylesheet" href="/motion.css" media="print" onload="this.media='all'">
  <noscript><link rel="stylesheet" href="/motion.css"></noscript>
  <script defer src="/motion.js"></script>
  <link rel="stylesheet" href="/animations.css" media="print" onload="this.media='all'">
  <noscript><link rel="stylesheet" href="/animations.css"></noscript>
  <script defer src="/animations.js"></script>
  <link rel="stylesheet" href="/_a11y-fixes.css?v=20260805" media="print" onload="this.media='all'">
  <noscript><link rel="stylesheet" href="/_a11y-fixes.css?v=20260805"></noscript>
  <script defer src="/_enhance.js?v=20260805"></script><script defer src="/_schema.js"></script><script defer src="/_nav.js"></script>"""

NAV_HTML = """  <header class="topnav border-b border-[var(--rule)] bg-[var(--bg)]/90 backdrop-blur sticky top-0 z-40">
    <nav class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
      <a href="/" class="flex items-center gap-2 whitespace-nowrap">
        <span class="serif font-semibold text-lg whitespace-nowrap">Winfred Quek</span>
      </a>

      <div class="hidden xl:flex items-center gap-6">
        <a class="nav-link link-underline" href="/">Home</a>
        <a class="nav-link link-underline" href="/about">About</a>
        <a class="nav-link link-underline" href="/services">Services</a>
        <a class="nav-link link-underline" href="/pricing">Pricing</a>
        <a class="nav-link link-underline" href="/tools">Tools</a>
        <a class="nav-link link-underline" href="/sell">Sell</a>
        <a class="nav-link link-underline" href="/listings">Listings</a>
        <a class="nav-link link-underline" href="/insights">Insights</a>
        <a class="nav-link link-underline" href="/answers">Answers</a>
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
        <a class="nav-link" href="/">Home</a>
        <a class="nav-link" href="/about">About</a>
        <a class="nav-link" href="/services">Services</a>
        <a class="nav-link" href="/listings">Listings</a>
        <a class="nav-link" href="/new-launches">New Launches</a>
        <a class="nav-link" href="/districts">Districts</a>
        <a class="nav-link" href="/buyers-guide">Buyers Guide</a>
        <a class="nav-link" href="/sellers-guide">Sellers Guide</a>
        <a class="nav-link" href="/insights">Insights</a><a class="nav-link" href="/answers">Answers</a>
        <a class="nav-link" href="/glossary">Glossary</a>
        <a class="nav-link" href="/faq">FAQ</a>
        <a class="nav-link" href="/track-record">Track Record</a>
        <a class="nav-link" href="/testimonials">Testimonials</a>
        <a class="nav-link" href="/contact">Contact</a>
        <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
      </div>
    </div>
  </header>"""


def footer_html(wa_text):
    wa_href = f"https://wa.me/{WA_NUMBER}?text={urllib.parse.quote(wa_text)}"
    return f"""<footer class="divider bg-[var(--bg)]">
  <div class="max-w-6xl mx-auto px-6 py-10 flex flex-col md:flex-row justify-between gap-6 text-sm text-[var(--ink-muted)]">
    <p>&#169; 2026 Winfred Quek &middot; Crestbrick &middot; CEA R073319H</p>
    <div class="flex gap-4"><a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a><span>&middot;</span><span>Not legal or financial advice.</span></div>
  </div>
  <div style="border-top:1px solid rgba(255,255,255,0.08);padding:1.25rem 1.5rem;">
    <p style="font-size:0.72rem;line-height:1.65;color:#706c64;max-width:900px;margin:0 auto;text-align:center;">The information and insights provided on this page are for informational purposes only and reflect Winfred's independent research and views. Real estate decisions carry risk, including market fluctuations, interest rate changes, and regulatory shifts. Past pricing and policy are not indicative of future results. This page is not investment, financial, or professional advice. Verify current rules directly with IRAS, HDB, CPF Board, or MAS, and seek qualified advice, before any purchasing decision.</p>
  </div>
</footer>

<a id="wa-float" href="{wa_href}" class="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full bg-[#25D366] px-5 py-3 text-white font-medium shadow-lg" style="box-shadow:0 10px 30px -5px rgba(37,211,102,.4);">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
  <span class="hidden sm:inline">Chat</span>
</a>
<script>(function(){{var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){{m.classList.toggle('open');}});}})();</script>
</body>
</html>
"""


def money(n):
    return f"S${n:,.0f}"


def cta_block(wa_text):
    wa_href = f"https://wa.me/{WA_NUMBER}?text={urllib.parse.quote(wa_text)}"
    return f"""  <div class="article-cta">
    <p style="font-family:'Fraunces',Georgia,serif;font-size:1.2rem;font-weight:600;margin-bottom:0.5rem;color:#1a1a1a;">See what your own flat could fetch</p>
    <p style="color:#4a4a4a;margin-bottom:0;">These numbers are the market, not a quote on your unit. Get your own number two ways.</p>
    <div class="cta-row">
      <a href="/tools/valuation" class="cta-secondary">Instant estimate (free)</a>
      <a href="/sell-with-winfred" class="cta-primary">Detailed report in 24 hours</a>
      <a href="{wa_href}" class="cta-tertiary">WhatsApp Winfred</a>
    </div>
  </div>
"""


def render_index(selected, month_min, month_max, generated, total_matching):
    total_txns = sum(t["count"] for t in selected)
    cards = "\n".join(
        f"""    <div class="town-card">
      <a href="/mop/{t['slug']}">{t['display']}</a>
      <p>{t['count']} post MOP resales &middot; median {money(t['median_price'])} &middot; most sold: {t['top_flat_type'].title()}</p>
    </div>"""
        for t in selected
    )

    town_list_ld = ",".join(
        f'{{"@type":"ListItem","position":{i+1},"name":"{t["display"]}","url":"{SITE}/mop/{t["slug"]}"}}'
        for i, t in enumerate(selected)
    )

    faq_items = [
        (
            "How were these towns chosen?",
            f"By transaction volume, not assumption. Every Singapore town was aggregated from data.gov.sg HDB resale "
            f"transactions ({month_min} to {month_max}) filtered to flats with a lease commencing in 2020 or 2021, "
            f"the cohort whose 5 year Minimum Occupation Period falls due roughly 2025 to 2027. The towns on this "
            f"page are simply the {len(selected)} with the highest transaction count in that cohort over the period."
        ),
        (
            "How many post MOP HDB resales does this cover in total?",
            f"{total_txns} resale transactions across the {len(selected)} towns listed here, all with a lease "
            f"commencing in 2020 or 2021, transacted between {month_min} and {month_max} (data.gov.sg, HDB Resale "
            f"Flat Prices)."
        ),
        (
            "Does crossing MOP mean I have to sell?",
            "No. MOP only removes the restriction, it does not create an obligation. Once MOP is crossed an owner "
            "can sell on the open market, sublet the whole flat, or buy private property, but staying put remains "
            "the default for most owners."
        ),
    ]
    faq_ld = ",".join(
        f'{{"@type":"Question","name":"{q}","acceptedAnswer":{{"@type":"Answer","text":"{a}"}}}}'
        for q, a in faq_items
    )
    faq_html = "\n".join(
        f"""    <div style="border-top:1px solid #252830;padding:1rem 0;">
      <h3 style="font-size:1.05rem;font-weight:600;margin-bottom:.4rem;color:#f0ede6;">{q}</h3>
      <p style="color:#b8b4aa;line-height:1.7;margin:0;">{a}</p>
    </div>"""
        for q, a in faq_items
    )

    wa_text = "Hi Winfred, I'd like to know what my HDB flat could fetch after MOP."

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="robots" content="index,follow,max-image-preview:large" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>The MOP Wave: HDB Resales After Minimum Occupation Period (2026) | Winfred Quek</title>
  <meta name="description" content="{total_txns} real HDB resale transactions from flats that crossed MOP, {month_min} to {month_max}, ranked by town. Data from data.gov.sg, not a forecast." />
  <meta property="og:type" content="website" />
  <meta property="og:title" content="The MOP Wave: HDB Resales After Minimum Occupation Period (2026)" />
  <meta property="og:description" content="{total_txns} real HDB resale transactions from flats that crossed MOP, {month_min} to {month_max}, ranked by town." />
  <meta property="og:image" content="{SITE}/img/og-image.jpg" />
  <meta property="og:url" content="{SITE}/mop" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="The MOP Wave: HDB Resales After Minimum Occupation Period (2026)" />
  <meta name="twitter:description" content="{total_txns} real HDB resale transactions from flats that crossed MOP, {month_min} to {month_max}." />
  <meta name="twitter:image" content="{SITE}/img/og-image.jpg" />
  <link rel="canonical" href="{SITE}/mop" />
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"CollectionPage","name":"The MOP Wave: HDB Resales After Minimum Occupation Period","description":"An index of Singapore towns ranked by post MOP HDB resale transaction volume, grounded in transaction data, {month_min} to {month_max}.","url":"{SITE}/mop","author":{{"@type":"Person","name":"Winfred Quek","identifier":"CEA R073319H","url":"{SITE}/about"}},"publisher":{{"@type":"Organization","name":"Crestbrick Pte Ltd","identifier":"L31010886H"}},"datePublished":"{generated}","dateModified":"{generated}","mainEntityOfPage":"{SITE}/mop"}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[{{"@type":"ListItem","position":1,"name":"Home","item":"{SITE}/"}},{{"@type":"ListItem","position":2,"name":"MOP Wave","item":"{SITE}/mop"}}]}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"ItemList","name":"Towns ranked by post MOP HDB resale volume","itemListElement":[{town_list_ld}]}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{faq_ld}]}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"Dataset","name":"Singapore HDB resale transactions, post MOP cohort (lease commence 2020-2021)","description":"HDB resale transactions from flats with a lease commencing in 2020 or 2021, aggregated by town, {month_min} to {month_max}.","url":"{SITE}/mop","temporalCoverage":"{month_min}/{month_max}","spatialCoverage":"Singapore","provider":{{"@type":"Organization","name":"Winfred Quek, Crestbrick"}},"citation":"data.gov.sg — HDB Resale Flat Prices (dataset d_8b84c4ee58e3cfc0ece0d773c8ca6abc)"}}
  </script>
{HEAD_ASSETS}
</head>
<body>

{NAV_HTML}

<main>

<section style="background:var(--highlight);padding:4rem 1.5rem 3rem;border-bottom:1px solid var(--rule);">
  <div style="max-width:56rem;margin:0 auto;">
    <p class="section-label" style="margin-bottom:.5rem;">MOP Wave</p>
    <h1 class="serif balance" style="font-size:clamp(1.9rem,4vw,3rem);font-weight:700;line-height:1.15;color:var(--ink);margin-bottom:.75rem;max-width:22ch;">The MOP wave: what the resale data actually shows</h1>
    <div class="quick-answer"><strong>Quick answer:</strong> {total_txns} HDB resale transactions from {month_min} to {month_max} came from flats with a lease commencing in 2020 or 2021, the cohort whose 5 year Minimum Occupation Period falls due roughly 2025 to 2027. Ranked by volume, these are the {len(selected)} towns where that cohort is transacting most. Source: data.gov.sg.</div>
    <p style="color:var(--ink-muted);font-size:.85rem;">By Winfred Quek &middot; CEA R073319H &middot; Data as of {generated}</p>
  </div>
</section>

<article class="max-w-3xl mx-auto px-6 pt-12 pb-6">

  <h2 style="margin-top:0;">Why the MOP wave matters</h2>
  <p>Minimum Occupation Period, <a href="/glossary/mop">MOP</a>, is the 5 year period an HDB flat owner must live in their unit before they can sell it on the open market, sublet the whole flat, or buy private property. Flats bought or built with a lease commencing in 2020 or 2021 are now, in 2026, either at or approaching that 5 year mark. Owners in that cohort have just become legally free to sell for the first time and many are actively doing so, which is exactly what shows up in the resale transaction data below.</p>

  <p>This is not a prediction about who will sell next. It is a read of who already did: {total_matching} matching resale transactions nationwide over the past six months, from flats whose lease commenced in 2020 or 2021. Every town below is ranked purely by how many of those transactions happened there.</p>

  <h2>Towns ranked by post MOP resale volume</h2>
  <p style="color:var(--ink-muted);font-size:.85rem;margin-top:-.5rem;margin-bottom:1rem;">{month_min} to {month_max}. A town needed at least {MIN_TOWN_TXNS} matching transactions to qualify.</p>
  <div class="town-grid">
{cards}
  </div>

  <h2>What crossing MOP changes</h2>
  <p>Before MOP completes, an owner generally cannot sell on the open market, cannot sublet the whole flat, and cannot buy private property while keeping the HDB flat. There are four narrow, HDB approved paths to exit early: financial hardship, medical reasons, a court order following matrimonial breakdown, and compulsory acquisition such as SERS. Outside those, the flat simply is not sellable until MOP completes. Once it does, none of those restrictions apply and the flat enters the open resale market like any other.</p>

  <p>A separate, unrelated change also affects who can buy a resale flat right now: in July 2026 HDB removed the 15 month wait out period that had applied to private property owners buying an HDB resale flat. That widens the pool of eligible buyers for every resale flat on the market, including ones that just crossed MOP. See <a href="/insights/hdb-15-month-wait-out-period-removed-2026">what the 15 month wait out removal changed</a> for the full detail.</p>

  <p>For the mechanics of listing, pricing, and closing a sale once you decide to, see <a href="/insights/how-to-sell-hdb-resale-singapore">the step by step HDB resale process</a>.</p>

{cta_block(wa_text)}
  <section class="geo-faq" style="max-width:48rem;margin:2.5rem auto 0;padding:0;">
    <h2 style="font-family:'Fraunces',Georgia,serif;font-size:1.5rem;margin-bottom:1rem;">Frequently asked questions</h2>
{faq_html}
  </section>

  <p class="text-sm" style="color:var(--ink-muted);margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--rule);">Winfred Quek is an Associate Marketing Consultant at Crestbrick Pte Ltd (CEA Licence No. L31010886H). CEA R073319H. This page reports transaction data from data.gov.sg and does not constitute financial, investment, or legal advice. Verify current figures with HDB or IRAS before making any decision. Data as of {generated}.</p>

</article>

</main>

{footer_html(wa_text)}"""


def render_town(t, month_min, month_max, generated):
    display = t["display"]
    slug = t["slug"]
    wa_text = f"Hi Winfred, I'd like to know what my {display} flat could fetch after MOP."

    ft_rows = "\n".join(
        f"""        <tr><td>{ft['flat_type'].title()}</td><td>{ft['count']}</td><td>{money(ft['median_price'])}</td><td>{money(ft['median_psm'])}/sqm</td></tr>"""
        for ft in t["flat_types"]
    )

    faq_items = [
        (
            f"How many {display} HDB resale flats crossed MOP recently?",
            f"{t['count']} resale transactions were recorded in {display} between {month_min} and {month_max}, "
            f"among flats with a lease commencing in 2020 or 2021, the cohort whose 5 year MOP falls due roughly "
            f"2025 to 2027 (source: data.gov.sg, HDB Resale Flat Prices)."
        ),
        (
            f"What is the median resale price for a post MOP {display} flat?",
            f"The median resale price across those {t['count']} transactions was {money(t['median_price'])}, with "
            f"a median of {money(t['median_psm'])} per square metre. Prices ranged from {money(t['min_price'])} to "
            f"{money(t['max_price'])} depending on flat type, floor, and remaining lease."
        ),
        (
            f"What flat type sells most after MOP in {display}?",
            f"{t['top_flat_type'].title()} flats accounted for the largest share of the {t['count']} transactions "
            f"in this cohort. See the table above for the median price and price per square metre by flat type."
        ),
    ]
    faq_ld = ",".join(
        f'{{"@type":"Question","name":"{q}","acceptedAnswer":{{"@type":"Answer","text":"{a}"}}}}'
        for q, a in faq_items
    )
    faq_html = "\n".join(
        f"""    <div style="border-top:1px solid #252830;padding:1rem 0;">
      <h3 style="font-size:1.05rem;font-weight:600;margin-bottom:.4rem;color:#f0ede6;">{q}</h3>
      <p style="color:#b8b4aa;line-height:1.7;margin:0;">{a}</p>
    </div>"""
        for q, a in faq_items
    )

    title = f"Selling your {display} BTO after MOP: what the data says (2026) | Winfred Quek"
    desc = (
        f"{t['count']} real {display} HDB resale transactions from flats that crossed MOP, {month_min} to "
        f"{month_max}. Median {money(t['median_price'])}. Data from data.gov.sg, not a forecast."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="robots" content="index,follow,max-image-preview:large" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{desc}" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="Selling your {display} BTO after MOP: what the data says (2026)" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:image" content="{SITE}/img/og-image.jpg" />
  <meta property="og:url" content="{SITE}/mop/{slug}" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="Selling your {display} BTO after MOP: what the data says (2026)" />
  <meta name="twitter:description" content="{desc}" />
  <meta name="twitter:image" content="{SITE}/img/og-image.jpg" />
  <link rel="canonical" href="{SITE}/mop/{slug}" />
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"Article","headline":"Selling your {display} BTO after MOP: what the data says (2026)","description":"{desc}","image":"{SITE}/img/og-image.jpg","author":{{"@type":"Person","name":"Winfred Quek","url":"{SITE}/about","jobTitle":"Associate Marketing Consultant","identifier":"CEA R073319H"}},"publisher":{{"@type":"Organization","name":"Crestbrick Pte Ltd","identifier":"L31010886H"}},"datePublished":"{generated}","dateModified":"{generated}","mainEntityOfPage":"{SITE}/mop/{slug}","speakable":{{"@type":"SpeakableSpecification","cssSelector":[".quick-answer","h1","h2"]}}}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[{{"@type":"ListItem","position":1,"name":"Home","item":"{SITE}/"}},{{"@type":"ListItem","position":2,"name":"MOP Wave","item":"{SITE}/mop"}},{{"@type":"ListItem","position":3,"name":"{display}","item":"{SITE}/mop/{slug}"}}]}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{faq_ld}]}}
  </script>
  <script type="application/ld+json">
  {{"@context":"https://schema.org","@type":"Dataset","name":"{display} HDB resale transactions, post MOP flats (lease commence 2020-2021)","description":"HDB resale transactions in {display} from flats with a lease commencing in 2020 or 2021, {month_min} to {month_max}.","url":"{SITE}/mop/{slug}","temporalCoverage":"{month_min}/{month_max}","spatialCoverage":"{display}, Singapore","provider":{{"@type":"Organization","name":"Winfred Quek, Crestbrick"}},"citation":"data.gov.sg — HDB Resale Flat Prices (dataset d_8b84c4ee58e3cfc0ece0d773c8ca6abc)"}}
  </script>
{HEAD_ASSETS}
</head>
<body>

{NAV_HTML}

<main>

<section style="background:var(--highlight);padding:4rem 1.5rem 3rem;border-bottom:1px solid var(--rule);">
  <div style="max-width:56rem;margin:0 auto;">
    <p class="section-label" style="margin-bottom:.5rem;"><a href="/mop" style="color:var(--accent);">MOP Wave</a></p>
    <h1 class="serif balance" style="font-size:clamp(1.75rem,4vw,2.75rem);font-weight:700;line-height:1.15;color:var(--ink);margin-bottom:.75rem;max-width:26ch;">Selling your {display} BTO after MOP: what the data says (2026)</h1>
    <div class="quick-answer"><strong>Quick answer:</strong> {t['count']} {display} HDB resale transactions, {month_min} to {month_max}, from flats with a lease commencing in 2020 or 2021 (post MOP cohort). Median resale price {money(t['median_price'])}, median {money(t['median_psm'])} per square metre. Source: data.gov.sg, HDB Resale Flat Prices.</div>
    <p style="color:var(--ink-muted);font-size:.85rem;">By Winfred Quek &middot; CEA R073319H &middot; Data as of {generated}</p>
  </div>
</section>

<article class="max-w-3xl mx-auto px-6 pt-12 pb-6">

  <div class="stat-grid">
    <div class="stat-card"><div class="label">Transactions</div><div class="value">{t['count']}</div></div>
    <div class="stat-card"><div class="label">Median price</div><div class="value">{money(t['median_price'])}</div></div>
    <div class="stat-card"><div class="label">Median $/sqm</div><div class="value">{money(t['median_psm'])}</div></div>
    <div class="stat-card"><div class="label">Price range</div><div class="value" style="font-size:1.05rem;">{money(t['min_price'])}&#8211;{money(t['max_price'])}</div></div>
  </div>

  <p>{t['count']} {display} HDB resale transactions between {month_min} and {month_max} came from flats with a lease commencing in 2020 or 2021, the cohort whose 5 year <a href="/glossary/mop">Minimum Occupation Period</a> falls due roughly 2025 to 2027. These are real, completed transactions pulled from data.gov.sg, not an estimate of who might sell next.</p>

  <h2>{display} post MOP resales by flat type</h2>
  <table class="data-table">
    <thead><tr><th>Flat type</th><th>Transactions</th><th>Median price</th><th>Median PSM</th></tr></thead>
    <tbody>
{ft_rows}
    </tbody>
  </table>
  <p style="color:var(--ink-muted);font-size:.82rem;margin-top:-1rem;">Flat types with fewer than {MIN_FLAT_TYPE_TXNS} transactions are grouped under &ldquo;Other flat types&rdquo; to keep each row statistically meaningful.</p>

  <h2>What crossing MOP means for a {display} owner</h2>
  <p>Before MOP completes, an owner cannot sell on the open market, sublet the whole flat, or buy private property while keeping the flat, outside four narrow HDB approved exceptions: financial hardship, medical reasons, a court order following matrimonial breakdown, or compulsory acquisition such as SERS. Once MOP completes, none of those restrictions apply. The transactions above are {display} owners who reached that point and chose to sell.</p>

  <p>A separate factual change also affects who can buy: in July 2026 HDB removed the 15 month wait out period that had applied to private property owners buying an HDB resale flat. That widens the buyer pool for every resale flat on the market right now, {display} included. Read <a href="/insights/hdb-15-month-wait-out-period-removed-2026">what the 15 month wait out removal changed</a> for the full detail.</p>

  <h2>The resale process, start to finish</h2>
  <p>Crossing MOP only removes the restriction, it does not start the clock on a sale. For the actual steps, pricing strategy, the HDB resale portal, and the CPF refund mechanics, see <a href="/insights/how-to-sell-hdb-resale-singapore">the full step by step HDB resale guide</a>.</p>

{cta_block(wa_text)}
  <section class="geo-faq" style="max-width:48rem;margin:2.5rem auto 0;padding:0;">
    <h2 style="font-family:'Fraunces',Georgia,serif;font-size:1.5rem;margin-bottom:1rem;">Frequently asked questions</h2>
{faq_html}
  </section>

  <p class="text-sm" style="color:var(--ink-muted);margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--rule);">Winfred Quek is an Associate Marketing Consultant at Crestbrick Pte Ltd (CEA Licence No. L31010886H). CEA R073319H. This page reports transaction data from data.gov.sg and does not constitute financial, investment, or legal advice. Verify current figures with HDB or IRAS before making any decision. Data as of {generated}.</p>

  <p style="font-size:.85rem;"><a href="/mop" style="color:var(--accent);">&larr; See every town in the MOP wave</a></p>

</article>

</main>

{footer_html(wa_text)}"""


def main():
    import os

    months = last_n_full_months(6)
    print(f"Fetching HDB resale transactions for: {', '.join(months)}")
    all_records = fetch_all(months)
    print(f"\nTotal records fetched: {len(all_records)}")

    mop_rows = filter_mop_cohort(all_records)
    towns = aggregate_by_town(mop_rows)
    selected = select_towns(towns)

    print(f"\n{len(selected)} towns selected for pages, ranked by post MOP transaction volume:")
    for t in selected:
        print(f"  {t['display']:<20} {t['count']:>4} txns   median {money(t['median_price'])}")

    generated = date.today().isoformat()
    month_min, month_max = min(months), max(months)

    os.makedirs(OUT_DIR, exist_ok=True)

    index_html = render_index(selected, month_min, month_max, generated, len(mop_rows))
    with open(os.path.join(OUT_DIR, "index.html"), "w") as f:
        f.write(index_html)
    print(f"\nWrote {OUT_DIR}/index.html")

    for t in selected:
        page_html = render_town(t, month_min, month_max, generated)
        path = os.path.join(OUT_DIR, f"{t['slug']}.html")
        with open(path, "w") as f:
            f.write(page_html)
        print(f"Wrote {path}")

    print(f"\nDone. {len(selected) + 1} pages written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
