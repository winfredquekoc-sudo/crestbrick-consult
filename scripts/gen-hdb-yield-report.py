#!/usr/bin/env python3
"""
gen-hdb-yield-report.py

Builds the HDB Town Rental Yield Ranking report from REAL data.gov.sg data:
  - HDB resale prices   : resource_id d_8b84c4ee58e3cfc0ece0d773c8ca6abc
  - HDB rental (Renting Out of Flats, raw transactions, from Jan 2021)
                        : resource_id d_c9f57187485a850908655db0e8cfe651

For each HDB town, computes:
  - median resale price over the most recent complete ~12 months
  - median monthly rent over the same window
  - gross annual rental yield = (median_rent * 12) / median_resale_price * 100

Outputs:
  - public/reports/hdb-rental-yield-ranking.json  (data payload)
  - public/reports/hdb-rental-yield-ranking.html  (rendered report page)

Truthfulness rule: if either data source is unreachable or returns no usable
records, this script does NOT fabricate numbers. It writes a clearly marked
"DATA PENDING" placeholder page instead and prints a blocker to stderr.

No system-clock dependency for the reported period: the "as of" period is
derived entirely from the max month/quarter actually present in the fetched
data, not from datetime.now(). (datetime is only used for the page's
generated-on stamp, which is cosmetic, not analytical.)
"""

import json
import statistics
import sys
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

RESALE_RESOURCE_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
RENTAL_RESOURCE_ID = "d_c9f57187485a850908655db0e8cfe651"
API_BASE = "https://data.gov.sg/api/action/datastore_search"

WINDOW_MONTHS = 12
MIN_SAMPLE = 20  # minimum transactions on each side (resale + rental) to include a town

REPO_ROOT = "/Users/winfredquek/crestbrick-consult"
JSON_OUT = f"{REPO_ROOT}/public/reports/hdb-rental-yield-ranking.json"
HTML_OUT = f"{REPO_ROOT}/public/reports/hdb-rental-yield-ranking.html"


def fetch_page(resource_id, sort_field, limit=5000, offset=0):
    params = {
        "resource_id": resource_id,
        "limit": limit,
        "offset": offset,
        "sort": f"{sort_field} desc",
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "winfredquek.com-report-generator/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if not data.get("success"):
        raise RuntimeError(f"API error for {resource_id}: {data.get('error')}")
    return data["result"]


def fetch_recent_months(resource_id, month_field, months_needed, page_limit=5000, max_pages=20):
    """Fetch records sorted desc by month_field until we have >= months_needed
    distinct month values, or we run out of pages. Returns (records, months_seen_desc)."""
    all_records = []
    months_seen = []
    offset = 0
    for _ in range(max_pages):
        result = fetch_page(resource_id, month_field, limit=page_limit, offset=offset)
        recs = result["records"]
        if not recs:
            break
        all_records.extend(recs)
        for r in recs:
            m = r.get(month_field)
            if m and m not in months_seen:
                months_seen.append(m)
        offset += page_limit
        # stop once we have comfortably more distinct months than needed
        if len(months_seen) >= months_needed + 2:
            break
        if offset >= result.get("total", 0):
            break
    return all_records, months_seen


def pick_window(months_seen_desc, records, month_field, window_size):
    """Drop a leading partial month (heuristically: current-month data that is
    still accumulating, identified by having far fewer rows than its neighbours),
    then take the next `window_size` distinct months."""
    if not months_seen_desc:
        return [], []
    counts = defaultdict(int)
    for r in records:
        counts[r.get(month_field)] += 1

    months_sorted = sorted(months_seen_desc, reverse=True)
    usable = months_sorted[:]
    if len(usable) >= 2:
        latest, second = usable[0], usable[1]
        if counts[latest] < 0.5 * counts[second]:
            usable = usable[1:]  # drop partial trailing month

    window = usable[:window_size]
    window_set = set(window)
    filtered = [r for r in records if r.get(month_field) in window_set]
    return filtered, window


def median(values):
    vals = sorted(values)
    return statistics.median(vals)


def build_report():
    blockers = []

    # ---- Resale price ----
    try:
        resale_records, resale_months_seen = fetch_recent_months(
            RESALE_RESOURCE_ID, "month", WINDOW_MONTHS
        )
    except Exception as e:
        blockers.append(f"HDB resale fetch failed: {e}")
        resale_records, resale_months_seen = [], []

    resale_window, resale_months = pick_window(
        resale_months_seen, resale_records, "month", WINDOW_MONTHS
    )

    # ---- Rental (raw transactions) ----
    try:
        rental_records, rental_months_seen = fetch_recent_months(
            RENTAL_RESOURCE_ID, "rent_approval_date", WINDOW_MONTHS
        )
    except Exception as e:
        blockers.append(f"HDB rental fetch failed: {e}")
        rental_records, rental_months_seen = [], []

    rental_window, rental_months = pick_window(
        rental_months_seen, rental_records, "rent_approval_date", WINDOW_MONTHS
    )

    if not resale_window or not rental_window:
        blockers.append("Insufficient data returned from data.gov.sg to compute yields.")
        return None, blockers

    # ---- Aggregate resale by town ----
    resale_by_town = defaultdict(list)
    for r in resale_window:
        town = r.get("town", "").strip()
        try:
            price = float(r.get("resale_price", 0))
        except (TypeError, ValueError):
            continue
        if town and price > 0:
            resale_by_town[town].append(price)

    # ---- Aggregate rental by town ----
    rental_by_town = defaultdict(list)
    for r in rental_window:
        town = r.get("town", "").strip()
        try:
            rent = float(r.get("monthly_rent", 0))
        except (TypeError, ValueError):
            continue
        if town and rent > 0:
            rental_by_town[town].append(rent)

    rows = []
    all_towns = sorted(set(resale_by_town.keys()) & set(rental_by_town.keys()))
    for town in all_towns:
        prices = resale_by_town[town]
        rents = rental_by_town[town]
        if len(prices) < MIN_SAMPLE or len(rents) < MIN_SAMPLE:
            continue
        med_price = median(prices)
        med_rent = median(rents)
        if med_price <= 0:
            continue
        gross_yield = (med_rent * 12) / med_price * 100
        rows.append({
            "town": town.title(),
            "median_resale_price": round(med_price),
            "median_monthly_rent": round(med_rent),
            "median_annual_rent": round(med_rent * 12),
            "gross_yield_pct": round(gross_yield, 2),
            "n_resale": len(prices),
            "n_rental": len(rents),
        })

    if not rows:
        blockers.append("No towns met the minimum sample size threshold.")
        return None, blockers

    rows.sort(key=lambda r: r["gross_yield_pct"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    period_start = min(resale_months[-1], rental_months[-1]) if resale_months and rental_months else "?"
    period_end_resale = resale_months[0] if resale_months else "?"
    period_end_rental = rental_months[0] if rental_months else "?"

    report = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resale_data_window": {
            "resource_id": RESALE_RESOURCE_ID,
            "months": resale_months,
        },
        "rental_data_window": {
            "resource_id": RENTAL_RESOURCE_ID,
            "months": rental_months,
        },
        "as_of_period_label": f"{resale_months[-1]} to {resale_months[0]}"
            if resale_months == rental_months
            else f"resale {resale_months[-1]}–{resale_months[0]}, rental {rental_months[-1]}–{rental_months[0]}",
        "methodology": (
            "Gross rental yield = (median monthly rent x 12) / median resale price, "
            "computed per HDB town over the trailing ~12 months of transactions. "
            "Median resale price and median monthly rent are each computed independently "
            "across all flat types transacted in that town in the window (not matched "
            "unit-for-unit), so this is a town-level approximation, not a same-unit yield. "
            "Gross yield excludes property tax, MCST/conservancy charges, maintenance, "
            "agent commission, and vacancy periods -- actual net yield is materially lower."
        ),
        "min_sample_size": MIN_SAMPLE,
        "towns": rows,
    }
    return report, blockers


def render_placeholder_html(blockers):
    blocker_text = "; ".join(blockers) if blockers else "Unknown data availability issue."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>HDB Rental Yield Ranking -- Data Pending | Winfred Quek</title>
<meta name="robots" content="noindex" />
</head>
<body style="background:#0f1117;color:#f0ede6;font-family:sans-serif;padding:4rem 2rem;text-align:center;">
<h1>DATA PENDING</h1>
<p>The HDB Rental Yield Ranking report could not be generated because live data
from data.gov.sg was not available at generation time.</p>
<p style="color:#c6a36a;">{blocker_text}</p>
<p>Re-run <code>scripts/gen-hdb-yield-report.py</code> once data.gov.sg is reachable.</p>
</body>
</html>
"""


def fmt_sgd(n):
    return f"S${n:,.0f}"


def render_html(report):
    towns = report["towns"]
    period_label = report["as_of_period_label"]
    generated = report["generated_at_utc"]
    resale_rid = report["resale_data_window"]["resource_id"]
    rental_rid = report["rental_data_window"]["resource_id"]

    top5 = towns[:5]
    top_town = towns[0]
    bottom_town = towns[-1]

    key_finding = (
        f"{top_town['town']} leads {len(towns)} ranked HDB towns with a gross rental yield of "
        f"{top_town['gross_yield_pct']:.2f}%, against a low of {bottom_town['gross_yield_pct']:.2f}% "
        f"in {bottom_town['town']}. Gross yield is annual rent divided by resale price -- it excludes "
        f"property tax, maintenance, agent commission and vacancy, so real net yield will be lower."
    )

    rows_html = []
    for r in towns:
        rows_html.append(f"""            <tr>
              <td data-sort="{r['rank']}">{r['rank']}</td>
              <td data-sort="{r['town']}">{r['town']}</td>
              <td data-sort="{r['median_resale_price']}">{fmt_sgd(r['median_resale_price'])}</td>
              <td data-sort="{r['median_annual_rent']}">{fmt_sgd(r['median_annual_rent'])}</td>
              <td data-sort="{r['gross_yield_pct']}" class="accent">{r['gross_yield_pct']:.2f}%</td>
              <td data-sort="{r['n_resale'] + r['n_rental']}">{r['n_resale']} resale / {r['n_rental']} rental</td>
            </tr>""")
    rows_html = "\n".join(rows_html)

    dataset_json_ld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"HDB Rental Yield Ranking by Town (Singapore, {period_label})",
        "description": (
            "Gross rental yield for every Singapore HDB town, ranked, computed from median "
            "resale prices and median rents over the trailing 12 months of HDB transactions."
        ),
        "url": "https://winfredquek.com/reports/hdb-rental-yield-ranking",
        "keywords": ["HDB", "rental yield", "Singapore", "resale price", "median rent", "property investment"],
        "creator": {"@type": "Person", "name": "Winfred Quek", "identifier": "CEA R073319H"},
        "publisher": {"@type": "Organization", "name": "Crestbrick Pte Ltd", "identifier": "L31010886H"},
        "temporalCoverage": period_label,
        "license": "https://data.gov.sg/open-data-licence",
        "isBasedOn": [
            {"@type": "Dataset", "name": "HDB Resale Prices", "identifier": resale_rid, "url": f"https://data.gov.sg/datasets/{resale_rid}/view"},
            {"@type": "Dataset", "name": "Renting Out of Flats", "identifier": rental_rid, "url": f"https://data.gov.sg/datasets/{rental_rid}/view"},
        ],
        "distribution": {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": "https://winfredquek.com/reports/hdb-rental-yield-ranking.json"},
    }
    article_json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"HDB Rental Yield Ranking by Town (Singapore, {period_label})",
        "description": key_finding,
        "image": "https://winfredquek.com/img/og-image.jpg",
        "author": {"@type": "Person", "name": "Winfred Quek", "url": "https://winfredquek.com/about", "jobTitle": "Associate Marketing Consultant", "identifier": "CEA R073319H"},
        "publisher": {"@type": "Organization", "name": "Crestbrick Pte Ltd", "identifier": "L31010886H"},
        "datePublished": generated[:10],
        "dateModified": generated[:10],
        "mainEntityOfPage": "https://winfredquek.com/reports/hdb-rental-yield-ranking",
    }
    breadcrumb_json_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
            {"@type": "ListItem", "position": 2, "name": "Reports", "item": "https://winfredquek.com/reports"},
            {"@type": "ListItem", "position": 3, "name": "HDB Rental Yield Ranking", "item": "https://winfredquek.com/reports/hdb-rental-yield-ranking"},
        ],
    }

    import re as _re
    _years = _re.findall(r"\d{4}", period_label)
    _year = max(_years) if _years else ""  # latest year in the data window
    title = f"HDB Rental Yield Ranking by Town (Singapore {_year}) | Winfred Quek"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta property="og:title" content="{title}" />
  <meta name="description" content="HDB towns ranked by gross rental yield using real data.gov.sg resale and rental data, {period_label}. See which towns offer the best rent-to-price ratio in Singapore." />
  <link rel="canonical" href="https://winfredquek.com/reports/hdb-rental-yield-ranking" />
  <link rel="stylesheet" href="/tw.css" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
  <meta name="theme-color" content="#0f1117" />
  <style>
    :root {{
      --bg:#0f1117;--highlight:#1a1c24;
      --ink:#f0ede6;
      --ink-soft:#b8b4aa;
      --ink-muted:#706c64;
      --accent:#c6a36a;
      --rule:#252830;
    }}
    html {{ scroll-behavior: smooth; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg);
      color: var(--ink);
      font-feature-settings: 'ss01', 'kern';
      -webkit-font-smoothing: antialiased;
    }}
    .serif {{ font-family: 'Fraunces', Georgia, serif; font-feature-settings: 'ss01'; }}
    .balance {{ text-wrap: balance; }}
    .divider {{ border-top: 1px solid var(--rule); }}
    .section-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--accent);
      font-weight: 600;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.9rem 1.6rem;
      border-radius: 4px;
      font-weight: 500;
      font-size: 15px;
      transition: all 0.15s ease;
    }}
    .btn-primary {{ background: var(--ink); color: var(--bg); }}
    .btn-primary:hover {{ background: var(--accent); }}
    .btn-ghost {{ color: var(--ink); border: 1px solid var(--rule); }}
    .btn-ghost:hover {{ border-color: var(--ink); }}
    .topnav a.nav-link {{ color: var(--ink-soft); font-size: 14px; font-weight: 500; transition: color .15s ease; }}
    .topnav a.nav-link:hover {{ color: var(--ink); }}
    .topnav a.nav-link.active {{ color: var(--ink); }}
    .topnav a.nav-link.active::after {{ content:""; display:block; height:1px; background: var(--accent); margin-top:4px; }}
    #mobile-menu {{ display: none; }}
    #mobile-menu.open {{ display: block; }}
    .report-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .report-table th {{
      text-align: left;
      padding: 10px 14px;
      background: rgba(255,255,255,0.04);
      color: var(--ink-soft);
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    .report-table th:hover {{ color: var(--ink); }}
    .report-table th.sorted::after {{ content: " \\25BE"; color: var(--accent); }}
    .report-table td {{ padding: 9px 14px; color: var(--ink-soft); border-bottom: 1px solid var(--rule); }}
    .report-table tr:hover td {{ background: rgba(255,255,255,0.02); }}
    .report-table td.accent {{ color: var(--accent); font-weight: 600; }}
    .report-table tr:first-child td {{ color: var(--ink); }}
    .cta-strip {{
      background: linear-gradient(135deg, #14161c 0%, #1e2030 100%);
      border: 1px solid var(--rule);
      border-radius: 12px;
      padding: 2rem;
      text-align: center;
    }}
    .citation-box {{
      background: rgba(198,163,106,0.08);
      border: 1px solid rgba(198,163,106,0.25);
      border-radius: 8px;
      padding: 1rem 1.25rem;
      font-size: 13px;
      color: var(--ink-soft);
      line-height: 1.6;
    }}
    .methodology-box {{
      background: #14161c;
      border: 1px solid var(--rule);
      border-radius: 10px;
      padding: 1.5rem 1.75rem;
      font-size: 14px;
      color: var(--ink-soft);
      line-height: 1.7;
    }}
    @keyframes wa-pulse {{
      0%, 100% {{ box-shadow: 0 10px 30px -5px rgba(37,211,102,0.4), 0 0 0 0 rgba(37,211,102,0.55); }}
      50%       {{ box-shadow: 0 10px 30px -5px rgba(37,211,102,0.4), 0 0 0 14px rgba(37,211,102,0); }}
    }}
    #wa-float.wa-pulsing {{ animation: wa-pulse 1s ease-out 2; }}
  </style>
  <meta property="og:image" content="https://winfredquek.com/img/og-image.jpg" />
<link rel="stylesheet" href="/_a11y-fixes.css"><script defer src="/_enhance.js"></script><script defer src="/_schema.js"></script><script defer src="/_nav.js"></script>  <!-- tool-schema (auto) -->
  <script type="application/ld+json">
{json.dumps(breadcrumb_json_ld)}
  </script>
  <script type="application/ld+json">
{json.dumps(dataset_json_ld)}
  </script>
  <script type="application/ld+json">
{json.dumps(article_json_ld)}
  </script>
</head>
<body>

  <!-- TOP NAV -->
  <header class="topnav border-b border-[var(--rule)] bg-[var(--bg)]/90 backdrop-blur sticky top-0 z-40">
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
        <a class="nav-link link-underline active" href="/insights">Insights</a>
        <a class="nav-link link-underline" href="/resources">Resources</a>
        <a class="nav-link link-underline" href="/contact">Contact</a>
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
        <a class="nav-link" href="/insights">Insights</a>
        <a class="nav-link" href="/contact">Contact</a>
        <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
      </div>
    </div>
  </header>

  <main>
    <!-- Hero -->
    <section style="padding: 4rem 1.5rem 2rem;">
      <div class="max-w-3xl mx-auto text-center">
        <p class="section-label mb-3">Singapore Property Data Report</p>
        <h1 class="serif" style="font-size:clamp(1.8rem,4.5vw,2.75rem); font-weight:700; line-height:1.2; margin-bottom:1rem;">
          HDB Rental Yield Ranking by Town
        </h1>
        <p style="color:var(--ink-soft); font-size:1.05rem; line-height:1.7; max-width:640px; margin:0 auto;">
          {key_finding}
        </p>
      </div>
    </section>

    <!-- Table -->
    <section style="padding: 0 1.5rem 3rem;">
      <div class="max-w-5xl mx-auto" style="overflow-x:auto; background:#14161c; border:1px solid var(--rule); border-radius:12px; padding:1.5rem;">
        <p class="section-label mb-3">Ranked by gross rental yield &middot; click a column to sort</p>
        <table class="report-table" id="yield-table">
          <thead>
            <tr>
              <th data-key="rank" class="sorted">Rank</th>
              <th data-key="town">Town</th>
              <th data-key="price">Median Resale Price</th>
              <th data-key="rent">Median Annual Rent</th>
              <th data-key="yield">Gross Yield</th>
              <th data-key="n">Sample Size</th>
            </tr>
          </thead>
          <tbody>
{rows_html}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Methodology -->
    <section style="padding: 0 1.5rem 2rem;">
      <div class="max-w-5xl mx-auto methodology-box">
        <p class="section-label mb-3">Methodology</p>
        <p>{report['methodology']}</p>
        <p style="margin-top:0.75rem;">Minimum sample size: {report['min_sample_size']} transactions on each side (resale and rental) per town, over the window shown below. Towns below this threshold are excluded from the ranking rather than shown with an unreliable figure.</p>
      </div>
    </section>

    <!-- Citation -->
    <section style="padding: 0 1.5rem 3rem;">
      <div class="max-w-5xl mx-auto citation-box">
        Source: data.gov.sg (HDB) &mdash; HDB Resale Flat Prices (dataset ID <code>{resale_rid}</code>) and Renting Out of Flats (dataset ID <code>{rental_rid}</code>). As of {period_label}. Report generated {generated}.
      </div>
    </section>

    <!-- CTA Strip -->
    <section style="padding: 0 1.5rem 5rem;">
      <div class="max-w-3xl mx-auto">
        <div class="cta-strip">
          <p class="section-label mb-3">Yield is one input, not the whole picture</p>
          <h3 class="serif" style="font-size:1.5rem; font-weight:600; margin-bottom:0.75rem;">
            See how yield fits your portfolio &mdash; book the free Property Portfolio Analysis
          </h3>
          <p style="color:var(--ink-soft); font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;">
            A high headline yield does not automatically mean a good investment for your situation.
            The Property Portfolio Analysis looks at financing, CPF, tax, and exit strategy alongside yield.
          </p>
          <a href="https://calendly.com/winfredquekoc" class="btn btn-primary" style="font-size:15px;">
            Book the free Property Portfolio Analysis
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
          </a>
        </div>
      </div>
    </section>
  </main>

  <!-- FOOTER -->
  <footer class="divider bg-[var(--bg)]">
    <div class="max-w-6xl mx-auto px-6 py-14 grid md:grid-cols-3 gap-10">
      <div>
        <p class="serif font-semibold text-lg mb-3">Winfred Quek</p>
        <p class="text-sm text-[var(--ink-soft)] leading-relaxed mb-3">
          Investor minded property advisor, Singapore. I help professionals, families,
          and investors build property portfolios not just buy units.
        </p>
        <p class="text-xs text-[var(--ink-muted)] leading-relaxed">
          Salesperson of <strong class="text-[var(--ink-soft)]">Crestbrick Pte Ltd</strong> (CEA Licence No. L31010886H)<br />
          CEA Registration No.: <strong class="text-[var(--ink-soft)]">R073319H</strong>
        </p>
      </div>
      <div>
        <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Quick links</p>
        <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
          <li><a href="/about" class="hover:text-[var(--ink)]">About Winfred</a></li>
          <li><a href="/services" class="hover:text-[var(--ink)]">Services</a></li>
          <li><a href="/listings" class="hover:text-[var(--ink)]">Listings</a></li>
          <li><a href="/insights" class="hover:text-[var(--ink)]">Insights</a></li>
          <li><a href="/contact" class="hover:text-[var(--ink)]">Contact</a></li>
        </ul>
      </div>
      <div>
        <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Contact</p>
        <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
          <li><a href="https://wa.me/6581618149" class="hover:text-[var(--ink)]">WhatsApp &middot; +65 8161 8149</a></li>
          <li><a href="tel:+6581618149" class="hover:text-[var(--ink)]">Phone &middot; +65 8161 8149</a></li>
          <li><a href="mailto:winfredquekoc@gmail.com" class="hover:text-[var(--ink)]">winfredquekoc@gmail.com</a></li>
          <li><a href="https://calendly.com/winfredquekoc" class="hover:text-[var(--ink)]">Book via Calendly</a></li>
        </ul>
      </div>
    </div>
    <div class="border-t border-[var(--rule)]">
      <div class="max-w-6xl mx-auto px-6 py-6 flex flex-col md:flex-row justify-between gap-3 text-xs text-[var(--ink-muted)]">
        <p>&copy; 2026 Winfred Quek &middot; Crestbrick &middot; CEA R073319H &middot; All rights reserved.</p>
        <div class="flex gap-4">
          <a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a>
          <span>&middot;</span>
          <span>Winfred Quek (CEA R073319H) is an Associate Marketing Consultant with Crestbrick Pte Ltd (CEA Licence No. L31010886H) and is not a licensed financial adviser or mortgage broker. This report is a factual presentation of public data and does not constitute financial, investment, or property advice.</span>
        </div>
      </div>
    </div>

    <!-- Disclaimer -->
    <div class="disclaimer-block" style="border-top:1px solid rgba(255,255,255,0.08); margin-top:2rem; padding:1.25rem 1.5rem;">
      <p style="font-size:0.72rem; line-height:1.65; color:#706c64; max-width:900px; margin:0 auto; text-align:center;">
        Figures on this page are computed from public HDB transaction data published by data.gov.sg and are provided for informational purposes only.
        Gross rental yield excludes property tax, MCST/conservancy charges, maintenance, agent commission, and vacancy periods, and is not a forecast
        of future returns. Past transaction data is not indicative of future performance. This report does not constitute investment, financial, or
        professional advice and should not be relied upon as such. Readers should conduct their own due diligence and seek advice from qualified
        professionals before making any investment decisions.
      </p>
    </div>
  </footer>

  <!-- Floating WhatsApp CTA -->
  <a id="wa-float"
    href="https://wa.me/6581618149?text=Hi%20Winfred%2C%20I%20saw%20the%20HDB%20rental%20yield%20ranking%20report."
    aria-label="Message Winfred on WhatsApp"
    class="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full bg-[#25D366] px-5 py-3 text-white font-medium shadow-lg hover:shadow-xl hover:scale-[1.03] transition-all duration-200"
    style="box-shadow: 0 10px 30px -5px rgba(37, 211, 102, 0.4);">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5">
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
    </svg>
    <span class="hidden sm:inline">Chat</span>
  </a>

  <script>
    (function(){{
      var t = document.getElementById('menu-toggle');
      var m = document.getElementById('mobile-menu');
      if(t && m){{ t.addEventListener('click', function(){{ m.classList.toggle('open'); }}); }}
    }})();
    (function(){{
      var btn = document.getElementById('wa-float');
      if (!btn) return;
      var fired = false;
      function pulse() {{
        if (fired) return; fired = true;
        btn.classList.add('wa-pulsing');
        setTimeout(function(){{ btn.classList.remove('wa-pulsing'); }}, 2200);
        window.removeEventListener('scroll', pulse);
      }}
      window.addEventListener('scroll', pulse, {{ passive: true }});
    }})();

    // Sortable table
    (function(){{
      var table = document.getElementById('yield-table');
      if (!table) return;
      var ths = table.querySelectorAll('th');
      var tbody = table.querySelector('tbody');
      var colIndex = {{ rank:0, town:1, price:2, rent:3, yield:4, n:5 }};
      var dirState = {{ rank: 1 }};
      ths.forEach(function(th){{
        th.addEventListener('click', function(){{
          var key = th.getAttribute('data-key');
          var idx = colIndex[key];
          var dir = dirState[key] === 1 ? -1 : 1;
          Object.keys(dirState).forEach(function(k){{ delete dirState[k]; }});
          dirState[key] = dir;
          ths.forEach(function(h){{ h.classList.remove('sorted'); }});
          th.classList.add('sorted');
          var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
          rows.sort(function(a, b){{
            var av = a.children[idx].getAttribute('data-sort');
            var bv = b.children[idx].getAttribute('data-sort');
            var an = parseFloat(av), bn = parseFloat(bv);
            if (!isNaN(an) && !isNaN(bn)) return dir * (an - bn);
            return dir * String(av).localeCompare(String(bv));
          }});
          rows.forEach(function(r){{ tbody.appendChild(r); }});
        }});
      }});
    }})();
  </script>
</body>
</html>
"""


def main():
    report, blockers = build_report()

    if report is None:
        html = render_placeholder_html(blockers)
        with open(HTML_OUT, "w") as f:
            f.write(html)
        print("BLOCKED:", "; ".join(blockers), file=sys.stderr)
        sys.exit(1)

    with open(JSON_OUT, "w") as f:
        json.dump(report, f, indent=2)

    html = render_html(report)
    with open(HTML_OUT, "w") as f:
        f.write(html)

    print(f"Wrote {JSON_OUT}")
    print(f"Wrote {HTML_OUT}")
    print(f"As of: {report['as_of_period_label']}")
    print("Top 5 by gross yield:")
    for r in report["towns"][:5]:
        print(f"  {r['rank']}. {r['town']}: {r['gross_yield_pct']:.2f}% "
              f"(median price {fmt_sgd(r['median_resale_price'])}, "
              f"median annual rent {fmt_sgd(r['median_annual_rent'])}, "
              f"n={r['n_resale']}/{r['n_rental']})")
    if blockers:
        print("Non-fatal notices:", "; ".join(blockers), file=sys.stderr)


if __name__ == "__main__":
    main()
