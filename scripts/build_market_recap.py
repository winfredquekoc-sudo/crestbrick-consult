#!/usr/bin/env python3
"""
build_market_recap.py

Builds a monthly HDB resale market recap from REAL data.gov.sg data:
  - HDB resale transactions: resource_id d_8b84c4ee58e3cfc0ece0d773c8ca6abc
    (fields verified live 2026-08-05: month, town, flat_type, block, street_name,
    storey_range, floor_area_sqm, flat_model, lease_commence_date,
    remaining_lease, resale_price)

For the target month (default: the last full calendar month relative to
today), computes per HDB town: transaction count, median resale price,
median price per square metre, and the month-over-month delta versus the
prior month. Flags the top 3 towns by rise and top 3 by fall (subject to a
minimum sample size so single-transaction swings don't get reported as
trends).

Outputs:
  - public/market/YYYY-MM.html   (the recap page for the target month)
  - public/market/index.html     (archive list, rebuilt from _archive.json)
  - public/market/_archive.json  (running manifest of every month generated,
    used to render the archive without re-parsing old HTML)

Truthfulness rule: if the API is unreachable, returns no usable records for
the target month, or a town falls under the minimum sample size, this
script does NOT fabricate numbers. Insufficient towns are simply left out of
the rises/falls flags, and if the whole month has no usable data the script
exits with an error instead of writing a page.

Every observation sentence in the rendered page is generated directly from
the computed numbers -- no predictions, no advice, purely descriptive.

Usage:
  scripts/build_market_recap.py                  # last full calendar month
  scripts/build_market_recap.py --month 2026-07   # specific month
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

RESOURCE_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
API_BASE = "https://data.gov.sg/api/action/datastore_search"

MIN_SAMPLE = 5  # minimum transactions in BOTH months for a town to appear in rises/falls
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
MARKET_DIR = os.path.join(REPO_ROOT, "public", "market")
ARCHIVE_JSON = os.path.join(MARKET_DIR, "_archive.json")


def prior_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def last_full_month(today=None):
    today = today or datetime.now()
    return prior_month(f"{today.year}-{today.month:02d}")


def month_label(ym):
    return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")


def fetch_month(ym):
    """Fetch every resale record for a given YYYY-MM, with 429 backoff."""
    params = {
        "resource_id": RESOURCE_ID,
        "limit": 10000,
        "filters": json.dumps({"month": ym}),
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "winfredquek.com-market-recap/1.0"}
    )
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            if not data.get("success"):
                raise RuntimeError(f"API error for {ym}: {data.get('error')}")
            return data["result"]["records"]
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or 500 <= e.code < 600:
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(f"  fetch {ym}: HTTP {e.code}, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{MAX_RETRIES})", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"  fetch {ym}: {e}, retrying in {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {ym} after {MAX_RETRIES} attempts: {last_err}")


def median(values):
    return statistics.median(sorted(values))


def by_town_stats(records):
    """Returns {town: {"n": int, "median_price": float, "median_psm": float}}."""
    prices = defaultdict(list)
    psms = defaultdict(list)
    for r in records:
        town = (r.get("town") or "").strip()
        try:
            price = float(r.get("resale_price"))
            area = float(r.get("floor_area_sqm"))
        except (TypeError, ValueError):
            continue
        if not town or price <= 0 or area <= 0:
            continue
        prices[town].append(price)
        psms[town].append(price / area)

    out = {}
    for town in prices:
        out[town] = {
            "n": len(prices[town]),
            "median_price": median(prices[town]),
            "median_psm": median(psms[town]),
        }
    return out


def pct_delta(new, old):
    if old in (0, None) or new is None:
        return None
    return (new - old) / old * 100


def build_recap(target_month):
    prior = prior_month(target_month)
    print(f"Fetching {target_month} (target) and {prior} (prior, for MoM)...")
    target_records = fetch_month(target_month)
    prior_records = fetch_month(prior)

    if not target_records:
        raise RuntimeError(
            f"No HDB resale records returned for {target_month}. "
            "Not writing a page -- refusing to fabricate a recap from no data."
        )

    target_stats = by_town_stats(target_records)
    prior_stats = by_town_stats(prior_records)

    towns = []
    for town, s in sorted(target_stats.items()):
        p = prior_stats.get(town)
        delta_price_pct = pct_delta(s["median_price"], p["median_price"]) if p else None
        delta_psm_pct = pct_delta(s["median_psm"], p["median_psm"]) if p else None
        reliable = bool(p) and s["n"] >= MIN_SAMPLE and p["n"] >= MIN_SAMPLE
        towns.append({
            "town": town.title(),
            "n": s["n"],
            "median_price": round(s["median_price"]),
            "median_psm": round(s["median_psm"], 1),
            "prior_n": p["n"] if p else None,
            "prior_median_price": round(p["median_price"]) if p else None,
            "delta_price_pct": round(delta_price_pct, 1) if delta_price_pct is not None else None,
            "delta_psm_pct": round(delta_psm_pct, 1) if delta_psm_pct is not None else None,
            "reliable": reliable,
        })

    # island-wide headline figures computed directly over all target-month records
    all_prices = [float(r["resale_price"]) for r in target_records
                  if _is_num(r.get("resale_price"))]
    all_psms = [float(r["resale_price"]) / float(r["floor_area_sqm"])
               for r in target_records
               if _is_num(r.get("resale_price")) and _is_num(r.get("floor_area_sqm"))
               and float(r["floor_area_sqm"]) > 0]
    prior_all_prices = [float(r["resale_price"]) for r in prior_records
                        if _is_num(r.get("resale_price"))]

    headline = {
        "month": target_month,
        "month_label": month_label(target_month),
        "prior_month": prior,
        "prior_month_label": month_label(prior),
        "total_transactions": len(target_records),
        "prior_total_transactions": len(prior_records),
        "island_median_price": round(median(all_prices)) if all_prices else None,
        "island_median_psm": round(median(all_psms), 1) if all_psms else None,
        "island_prior_median_price": round(median(prior_all_prices)) if prior_all_prices else None,
        "n_towns": len(towns),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if headline["island_median_price"] and headline["island_prior_median_price"]:
        headline["island_delta_pct"] = round(
            pct_delta(headline["island_median_price"], headline["island_prior_median_price"]), 1
        )
    else:
        headline["island_delta_pct"] = None

    reliable_towns = [t for t in towns if t["reliable"] and t["delta_price_pct"] is not None]
    rises = sorted(reliable_towns, key=lambda t: t["delta_price_pct"], reverse=True)[:3]
    falls = sorted(reliable_towns, key=lambda t: t["delta_price_pct"])[:3]

    return {"headline": headline, "towns": towns, "rises": rises, "falls": falls}


def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def fmt_sgd(n):
    return f"S${n:,.0f}" if n is not None else "N/A"


def fmt_pct(n):
    if n is None:
        return "N/A"
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def build_observations(data):
    h = data["headline"]
    obs = []

    if h["island_delta_pct"] is not None:
        direction = "rose" if h["island_delta_pct"] > 0 else ("fell" if h["island_delta_pct"] < 0 else "was unchanged")
        obs.append(
            f"Island-wide, the median HDB resale price {direction} "
            f"{fmt_pct(abs(h['island_delta_pct'])).lstrip('+')} to {fmt_sgd(h['island_median_price'])} "
            f"in {h['month_label']}, across {h['total_transactions']:,} transactions in {h['n_towns']} towns, "
            f"against {fmt_sgd(h['island_prior_median_price'])} in {h['prior_month_label']}."
        )
    else:
        obs.append(
            f"{h['total_transactions']:,} HDB resale transactions were recorded across {h['n_towns']} towns "
            f"in {h['month_label']}, with an island-wide median price of {fmt_sgd(h['island_median_price'])}."
        )

    if data["rises"]:
        t = data["rises"][0]
        obs.append(
            f"{t['town']} recorded the largest median price rise among towns with at least "
            f"{MIN_SAMPLE} transactions in both months, up {abs(t['delta_price_pct']):.1f}% to "
            f"{fmt_sgd(t['median_price'])} across {t['n']} transactions, from {fmt_sgd(t['prior_median_price'])} "
            f"in {h['prior_month_label']}."
        )

    if data["falls"]:
        t = data["falls"][0]
        obs.append(
            f"{t['town']} recorded the largest median price fall among towns with at least "
            f"{MIN_SAMPLE} transactions in both months, down {abs(t['delta_price_pct']):.1f}% "
            f"to {fmt_sgd(t['median_price'])} across {t['n']} transactions, from {fmt_sgd(t['prior_median_price'])} "
            f"in {h['prior_month_label']}."
        )

    return obs


NAV_HEADER = """  <header class="topnav border-b border-[var(--rule)] bg-[var(--bg)]/90 backdrop-blur sticky top-0 z-40">
    <nav class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
      <a href="/" class="flex items-center gap-2 whitespace-nowrap">
        <span class="serif font-semibold text-lg whitespace-nowrap">Winfred Quek</span>
      </a>

      <div class="hidden xl:flex items-center gap-6">
        <a class="nav-link link-underline active" href="/">Home</a>
        <a class="nav-link link-underline" href="/start">Start here</a>
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
        <a class="nav-link" href="/start">Start here</a>
        <a class="nav-link" href="/about">About</a>
        <a class="nav-link" href="/services">Services</a>
        <a class="nav-link" href="/listings">Listings</a>
        <a class="nav-link" href="/new-launches">New Launches</a>
        <a class="nav-link" href="/districts">Districts</a>
        <a class="nav-link" href="/buyers-guide">Buyers Guide</a>
        <a class="nav-link" href="/sellers-guide">Sellers Guide</a>
        <a class="nav-link" href="/insights">Insights</a><a class="nav-link" href="/faq">FAQ</a>
        <a class="nav-link" href="/track-record">Track Record</a>
        <a class="nav-link" href="/testimonials">Testimonials</a>
        <a class="nav-link" href="/contact">Contact</a>
        <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
      </div>
    </div>
  </header>"""

FOOTER = """<footer class="divider bg-[var(--bg)]">
  <div class="max-w-6xl mx-auto px-6 py-10 flex flex-col md:flex-row justify-between gap-6 text-sm text-[var(--ink-muted)]">
    <p>&copy; 2026 Winfred Quek &middot; Crestbrick &middot; CEA R073319H</p>
    <div class="flex gap-4"><a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a><span>&middot;</span><span>Not legal or financial advice.</span></div>
  </div>
  <div style="border-top:1px solid rgba(255,255,255,0.08);padding:1.25rem 1.5rem;">
    <p style="font-size:0.72rem;line-height:1.65;color:#706c64;max-width:900px;margin:0 auto;text-align:center;">The information on this page is drawn from HDB resale transaction data published by data.gov.sg and is for informational purposes only. It does not constitute investment, financial, or professional advice, and past transaction data is not indicative of future prices. CEA R073319H. Crestbrick Pte Ltd L31010886H.</p>
  </div>
</footer>

<a id="wa-float" href="https://wa.me/6581618149?text=Hi%20Winfred%2C%20I%20have%20a%20question%20about%20the%20HDB%20resale%20market." class="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full bg-[#25D366] px-5 py-3 text-white font-medium shadow-lg" style="box-shadow:0 10px 30px -5px rgba(37,211,102,.4);">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
  <span class="hidden sm:inline">Chat</span>
</a>
<script>(function(){var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){m.classList.toggle('open');});})();</script>
</body>
</html>
"""

SHARED_STYLE = """  <style>
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
    article p{font-size:1.05rem;line-height:1.75;color:var(--ink-soft);margin-bottom:1.1em;}
    article h2{font-family:'Fraunces',Georgia,serif;font-size:1.75rem;font-weight:600;margin:2em 0 .6em;color:var(--ink);}
    .quick-answer{background:#1a1c24;border:1px solid var(--rule);border-left:3px solid var(--accent);border-radius:8px;padding:1.1rem 1.35rem;color:var(--ink);margin:0 0 1rem;font-size:1.02rem;line-height:1.7;}
    .quick-answer strong{color:var(--accent);}
    .stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin:1.5rem 0 2rem;}
    .stat-card{background:var(--highlight);border:1px solid var(--rule);border-radius:10px;padding:1.1rem 1.25rem;}
    .stat-card .label{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--ink-muted);font-weight:600;}
    .stat-card .value{font-family:'Fraunces',Georgia,serif;font-size:1.5rem;font-weight:600;color:var(--ink);margin-top:.3rem;}
    .stat-card .delta{font-size:.8rem;margin-top:.25rem;}
    .up{color:#7ec98f;} .down{color:#e08a7d;} .flat{color:var(--ink-muted);}
    table{width:100%;border-collapse:collapse;font-size:13.5px;margin:1em 0;}
    th,td{text-align:left;padding:.6rem .85rem;border-bottom:1px solid var(--rule);vertical-align:top;}
    th{color:var(--accent);text-transform:uppercase;letter-spacing:.1em;font-size:10.5px;font-weight:600;}
    td.num,th.num{text-align:right;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;}
    .table-wrap{overflow-x:auto;margin:1em 0;}
    .flag-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin:1rem 0 2rem;}
    .flag-card{background:var(--highlight);border:1px solid var(--rule);border-radius:10px;padding:1rem 1.15rem;}
    .flag-card .town{font-family:'Fraunces',Georgia,serif;font-size:1.05rem;font-weight:600;color:var(--ink);}
    .flag-card .pct{font-size:.85rem;font-weight:600;margin-top:.2rem;}
    .archive-list a{display:block;padding:1rem 1.25rem;background:var(--highlight);border:1px solid var(--rule);border-radius:10px;margin-bottom:.75rem;color:var(--ink);text-decoration:none;}
    .archive-list a:hover{border-color:var(--accent);}
    .archive-list .m{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:1.05rem;}
    .archive-list .b{color:var(--ink-muted);font-size:.85rem;margin-top:.25rem;}
    .cta-block{background:rgba(198,163,106,0.08);border:1px solid rgba(198,163,106,0.3);border-radius:12px;padding:2rem;text-align:center;margin:3rem 0;}
  </style>"""

HEAD_INCLUDES = """  <link rel="stylesheet" href="/tw.css" />
  <link rel="preload" href="/fonts/inter-latin-400.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="preload" href="/fonts/fraunces-latin-700.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/fonts/fonts.css">
""" + SHARED_STYLE + """
  <link rel="stylesheet" href="/motion.css" media="print" onload="this.media='all'"><noscript><link rel="stylesheet" href="/motion.css"></noscript>
  <script defer src="/motion.js"></script>
<link rel="stylesheet" href="/_a11y-fixes.css?v=20260805" media="print" onload="this.media='all'"><noscript><link rel="stylesheet" href="/_a11y-fixes.css?v=20260805"></noscript><script defer src="/_enhance.js?v=20260805"></script><script defer src="/_schema.js"></script><script defer src="/_nav.js"></script>"""


def delta_class(pct):
    if pct is None:
        return "flat"
    if pct > 0:
        return "up"
    if pct < 0:
        return "down"
    return "flat"


def render_recap_html(data):
    h = data["headline"]
    ym = h["month"]
    ym_url = f"https://winfredquek.com/market/{ym}"
    title = f"HDB Resale Market Recap: {h['month_label']} | Winfred Quek"
    desc = (
        f"{h['total_transactions']:,} HDB resale transactions across {h['n_towns']} towns in "
        f"{h['month_label']}. Median price, median PSM, and month over month change by town, "
        f"from data.gov.sg."
    )
    observations = build_observations(data)
    obs_html = "\n".join(f"    <p>{o}</p>" for o in observations)

    rows_html = []
    for t in sorted(data["towns"], key=lambda x: x["town"]):
        delta_txt = fmt_pct(t["delta_price_pct"]) if t["delta_price_pct"] is not None else (
            "N/A" if t["prior_n"] is None else "n/a (low sample)"
        )
        cls = delta_class(t["delta_price_pct"]) if t["reliable"] else "flat"
        flag = "" if t["reliable"] or t["prior_n"] is None else ' title="Fewer than 5 transactions in one of the two months; change shown but not flagged as a rise/fall"'
        rows_html.append(
            f'      <tr{flag}><td>{t["town"]}</td>'
            f'<td class="num">{t["n"]}</td>'
            f'<td class="num">{fmt_sgd(t["median_price"])}</td>'
            f'<td class="num">S${t["median_psm"]:,.0f}</td>'
            f'<td class="num {cls}">{delta_txt}</td></tr>'
        )
    rows_html = "\n".join(rows_html)

    def flag_cards(items, cls):
        if not items:
            return '    <p style="color:var(--ink-muted);font-size:.85rem;">No town met the minimum sample size in both months.</p>'
        cards = []
        for t in items:
            cards.append(
                f'      <div class="flag-card"><div class="town">{t["town"]}</div>'
                f'<div class="pct {cls}">{fmt_pct(t["delta_price_pct"])}</div>'
                f'<div style="color:var(--ink-muted);font-size:.8rem;margin-top:.35rem;">'
                f'{fmt_sgd(t["median_price"])} median &middot; {t["n"]} transactions</div></div>'
            )
        return '    <div class="flag-grid">\n' + "\n".join(cards) + "\n    </div>"

    island_delta_txt = fmt_pct(h["island_delta_pct"])
    island_cls = delta_class(h["island_delta_pct"])

    dataset_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"HDB resale transactions, {h['month_label']}",
        "description": f"Per-town HDB resale transaction count, median price and median price per square metre for {h['month_label']}, computed from data.gov.sg.",
        "url": ym_url,
        "temporalCoverage": ym,
        "creator": {"@type": "Person", "name": "Winfred Quek", "identifier": "CEA R073319H"},
        "distribution": {
            "@type": "DataDownload",
            "encodingFormat": "application/json",
            "contentUrl": f"https://data.gov.sg/api/action/datastore_search?resource_id={RESOURCE_ID}",
        },
        "isBasedOn": {
            "@type": "Dataset",
            "name": "HDB Resale Flat Prices",
            "url": "https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view",
            "publisher": {"@type": "Organization", "name": "data.gov.sg"},
        },
    })

    article_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"HDB resale market recap: {h['month_label']}",
        "description": desc,
        "image": "https://winfredquek.com/img/og-image.jpg",
        "author": {"@type": "Person", "name": "Winfred Quek", "url": "https://winfredquek.com/about",
                   "jobTitle": "Associate Marketing Consultant", "identifier": "CEA R073319H"},
        "publisher": {"@type": "Organization", "name": "Crestbrick Pte Ltd", "identifier": "L31010886H"},
        "datePublished": h["generated_at_utc"][:10],
        "dateModified": h["generated_at_utc"][:10],
        "mainEntityOfPage": ym_url,
    })

    breadcrumb_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
            {"@type": "ListItem", "position": 2, "name": "Market Recaps", "item": "https://winfredquek.com/market/"},
            {"@type": "ListItem", "position": 3, "name": h["month_label"], "item": ym_url},
        ],
    })

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="robots" content="index,follow,max-image-preview:large" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{desc}" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="HDB Resale Market Recap: {h['month_label']}" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:image" content="https://winfredquek.com/img/og-image.jpg" />
  <meta property="og:url" content="{ym_url}" />
  <meta property="article:published_time" content="{h['generated_at_utc'][:10]}" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="HDB Resale Market Recap: {h['month_label']}" />
  <meta name="twitter:description" content="{desc}" />
  <meta name="twitter:image" content="https://winfredquek.com/img/og-image.jpg" />
  <link rel="canonical" href="{ym_url}" />
  <script type="application/ld+json">
  {article_jsonld}
  </script>
  <script type="application/ld+json">
  {breadcrumb_jsonld}
  </script>
  <script type="application/ld+json">
  {dataset_jsonld}
  </script>
{HEAD_INCLUDES}
</head>
<body>

{NAV_HEADER}

<main>

<section style="background:var(--highlight);padding:4rem 1.5rem 3rem;border-bottom:1px solid var(--rule);">
  <div style="max-width:72rem;margin:0 auto;">
    <p class="section-label" style="margin-bottom:.5rem;"><a href="/market/" style="color:var(--accent);">Market Recaps</a></p>
    <h1 class="serif balance" style="font-size:clamp(2rem,4vw,3rem);font-weight:700;line-height:1.1;color:var(--ink);margin-bottom:.75rem;">HDB resale market recap: {h['month_label']}</h1>
    <div class="quick-answer"><strong>Quick answer:</strong> {h['total_transactions']:,} HDB resale transactions were recorded across {h['n_towns']} towns in {h['month_label']}. The island-wide median resale price was {fmt_sgd(h['island_median_price'])} ({island_delta_txt} versus {h['prior_month_label']}), with a median of S${h['island_median_psm']:,.0f} per square metre. Figures below are computed directly from data.gov.sg HDB resale transaction records.</div>
    <p style="font-size:.85rem;color:var(--ink-muted);">By Winfred Quek &middot; CEA R073319H &middot; Generated {h['generated_at_utc'][:10]} from data.gov.sg</p>
  </div>
</section>

<article class="max-w-4xl mx-auto px-6 pt-10 pb-24">

  <div class="stat-grid">
    <div class="stat-card"><div class="label">Transactions</div><div class="value">{h['total_transactions']:,}</div><div class="delta flat">{h['prior_total_transactions']:,} in {h['prior_month_label']}</div></div>
    <div class="stat-card"><div class="label">Towns</div><div class="value">{h['n_towns']}</div></div>
    <div class="stat-card"><div class="label">Median price</div><div class="value">{fmt_sgd(h['island_median_price'])}</div><div class="delta {island_cls}">{island_delta_txt} MoM</div></div>
    <div class="stat-card"><div class="label">Median PSM</div><div class="value">S${h['island_median_psm']:,.0f}</div></div>
  </div>

  <h2>Observations</h2>
{obs_html}
  <p style="font-size:.85rem;color:var(--ink-muted);">Observations are descriptive only, computed directly from the transaction counts and medians shown on this page. They are not predictions and should not be read as advice.</p>
  <p style="font-size:.85rem;color:var(--ink-muted);">Note on method: each town's median price is taken across whatever mix of flat types (2-room to executive) actually transacted that month. A town with few transactions can show a large swing simply because the mix shifted, for example more 5-room flats changing hands one month than the next, rather than because like-for-like prices moved. Treat single-month, low-volume swings with that in mind.</p>

  <h2>Largest rises</h2>
  <p style="font-size:.85rem;color:var(--ink-muted);margin-top:-.5rem;">Among towns with at least {MIN_SAMPLE} transactions in both {h['month_label']} and {h['prior_month_label']}.</p>
{flag_cards(data['rises'], 'up')}

  <h2>Largest falls</h2>
  <p style="font-size:.85rem;color:var(--ink-muted);margin-top:-.5rem;">Among towns with at least {MIN_SAMPLE} transactions in both {h['month_label']} and {h['prior_month_label']}.</p>
{flag_cards(data['falls'], 'down')}

  <h2>All towns</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Town</th><th class="num">Transactions</th><th class="num">Median price</th><th class="num">Median PSM</th><th class="num">MoM change</th></tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
  </div>
  <p style="font-size:.8rem;color:var(--ink-muted);">MoM change compares the median resale price in {h['month_label']} against {h['prior_month_label']}. Marked n/a where a town had fewer than {MIN_SAMPLE} transactions in either month &mdash; too small a sample for a reliable month over month read.</p>

  <div class="cta-block">
    <p class="serif" style="font-size:1.35rem;font-weight:600;color:var(--ink);margin-bottom:.75rem;">Want the numbers for your specific block or town?</p>
    <p style="color:var(--ink-soft);font-size:.9rem;max-width:52ch;margin:0 auto 1.5rem;">This recap is town-level. Winfred can pull the actual transactions for your block and street.</p>
    <div style="display:flex;flex-wrap:wrap;gap:.75rem;justify-content:center;">
      <a href="https://calendly.com/winfredquekoc" class="btn btn-primary">Book a free 30-min call</a>
      <a href="https://wa.me/6581618149?text=Hi%20Winfred%2C%20I%27d%20like%20the%20resale%20numbers%20for%20my%20block." class="btn btn-ghost">WhatsApp Winfred</a>
    </div>
  </div>

  <h2>Related reading</h2>
  <ul style="padding-left:1.4em;color:var(--ink-soft);list-style:disc;margin-bottom:1.1em;">
    <li><a style="color:var(--accent);text-decoration:underline;" href="/market/">All market recaps</a></li>
    <li><a style="color:var(--accent);text-decoration:underline;" href="/policy-timeline">Cooling measures timeline</a></li>
  </ul>

  <p class="text-sm" style="color:var(--ink-muted);margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--rule);">Winfred Quek is an Associate Marketing Consultant at Crestbrick Pte Ltd (CEA R073319H). Source: HDB Resale Flat Prices, data.gov.sg (resource {RESOURCE_ID}). This recap is for general information only and does not constitute investment, financial, or professional advice.</p>
</article>

</main>

{FOOTER}"""
    return html


def render_index_html(archive):
    entries = sorted(archive, key=lambda e: e["month"], reverse=True)
    latest = entries[0] if entries else None
    desc = "Monthly HDB resale market recaps for Singapore, computed from data.gov.sg transaction data: median price, median PSM, and town by town month over month change."

    list_html = []
    for e in entries:
        list_html.append(
            f'    <a href="/market/{e["month"]}"><div class="m">{e["month_label"]}</div>'
            f'<div class="b">{e["total_transactions"]:,} transactions across {e["n_towns"]} towns &middot; '
            f'median {fmt_sgd(e["island_median_price"])}</div></a>'
        )
    list_html = "\n".join(list_html) if list_html else '    <p style="color:var(--ink-muted);">No recaps published yet.</p>'

    item_list_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "HDB resale market recaps",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": e["month_label"],
             "url": f"https://winfredquek.com/market/{e['month']}"}
            for i, e in enumerate(entries)
        ],
    })

    breadcrumb_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
            {"@type": "ListItem", "position": 2, "name": "Market Recaps", "item": "https://winfredquek.com/market/"},
        ],
    })

    latest_line = (
        f"The latest recap covers {latest['month_label']}: {latest['total_transactions']:,} transactions, "
        f"island-wide median {fmt_sgd(latest['island_median_price'])}."
        if latest else "No recaps have been published yet."
    )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="robots" content="index,follow,max-image-preview:large" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HDB Resale Market Recaps, Monthly | Winfred Quek</title>
  <meta name="description" content="{desc}" />
  <meta property="og:type" content="website" />
  <meta property="og:title" content="HDB Resale Market Recaps, Monthly" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:image" content="https://winfredquek.com/img/og-image.jpg" />
  <meta property="og:url" content="https://winfredquek.com/market/" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="HDB Resale Market Recaps, Monthly" />
  <meta name="twitter:description" content="{desc}" />
  <meta name="twitter:image" content="https://winfredquek.com/img/og-image.jpg" />
  <link rel="canonical" href="https://winfredquek.com/market/" />
  <script type="application/ld+json">
  {item_list_jsonld}
  </script>
  <script type="application/ld+json">
  {breadcrumb_jsonld}
  </script>
{HEAD_INCLUDES}
</head>
<body>

{NAV_HEADER}

<main>

<section style="background:var(--highlight);padding:4rem 1.5rem 3rem;border-bottom:1px solid var(--rule);">
  <div style="max-width:72rem;margin:0 auto;">
    <p class="section-label" style="margin-bottom:.5rem;">Market Recaps</p>
    <h1 class="serif balance" style="font-size:clamp(2rem,4vw,3rem);font-weight:700;line-height:1.1;color:var(--ink);margin-bottom:.75rem;">HDB resale market recaps, month by month</h1>
    <div class="quick-answer"><strong>Quick answer:</strong> {latest_line} Each recap below is computed directly from data.gov.sg HDB resale transaction records: town by town transaction count, median price, median price per square metre, and the month over month change.</div>
    <p style="font-size:.85rem;color:var(--ink-muted);">By Winfred Quek &middot; CEA R073319H</p>
  </div>
</section>

<article class="max-w-3xl mx-auto px-6 pt-10 pb-24">
  <div class="archive-list">
{list_html}
  </div>

  <p class="text-sm" style="color:var(--ink-muted);margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--rule);">Source: HDB Resale Flat Prices, data.gov.sg (resource {RESOURCE_ID}). These recaps are for general information only and do not constitute investment, financial, or professional advice.</p>
</article>

</main>

{FOOTER}"""
    return html


def load_archive():
    if os.path.exists(ARCHIVE_JSON):
        with open(ARCHIVE_JSON) as f:
            return json.load(f)
    return []


def save_archive(archive):
    with open(ARCHIVE_JSON, "w") as f:
        json.dump(archive, f, indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Build the monthly HDB resale market recap.")
    parser.add_argument("--month", help="Target month as YYYY-MM. Defaults to the last full calendar month.")
    args = parser.parse_args()

    target_month = args.month or last_full_month()
    print(f"Target month: {target_month}")

    data = build_recap(target_month)
    h = data["headline"]

    os.makedirs(MARKET_DIR, exist_ok=True)

    recap_html = render_recap_html(data)
    recap_path = os.path.join(MARKET_DIR, f"{target_month}.html")
    with open(recap_path, "w") as f:
        f.write(recap_html)
    print(f"Wrote {recap_path}")

    archive = load_archive()
    archive = [e for e in archive if e["month"] != target_month]
    archive.append({
        "month": h["month"],
        "month_label": h["month_label"],
        "total_transactions": h["total_transactions"],
        "n_towns": h["n_towns"],
        "island_median_price": h["island_median_price"],
        "island_median_psm": h["island_median_psm"],
        "island_delta_pct": h["island_delta_pct"],
        "generated_at_utc": h["generated_at_utc"],
    })
    save_archive(archive)

    index_html = render_index_html(archive)
    index_path = os.path.join(MARKET_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(index_html)
    print(f"Wrote {index_path}")

    print("\n--- Summary ---")
    print(f"Month: {h['month_label']}")
    print(f"Transactions: {h['total_transactions']:,} (prior month: {h['prior_total_transactions']:,})")
    print(f"Towns: {h['n_towns']}")
    print(f"Island median price: {fmt_sgd(h['island_median_price'])} ({fmt_pct(h['island_delta_pct'])} MoM)")
    print(f"Island median PSM: S${h['island_median_psm']:,.1f}")
    print(f"Top rises: {[(t['town'], t['delta_price_pct']) for t in data['rises']]}")
    print(f"Top falls: {[(t['town'], t['delta_price_pct']) for t in data['falls']]}")


if __name__ == "__main__":
    main()
