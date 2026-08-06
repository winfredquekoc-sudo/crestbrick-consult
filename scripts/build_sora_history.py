#!/usr/bin/env python3
"""
build_sora_history.py — 24 month history of 3-month Compounded SORA for the
/tools/mortgage-rates page chart.

Source: MAS eServices "Domestic Interest Rates" tool, which is a classic
ASP.NET WebForms postback (not a JSON API — MAS does not currently expose one
for this dataset). We GET the query page for viewstate/eventvalidation
tokens, POST back the same form with the "3-month Compounded SORA" checkbox
selected and a 24 month date range, then parse the resulting HTML table.

  https://eservices.mas.gov.sg/statistics/dir/domesticinterestrates.aspx

Output: public/data/sora-history.json — one row per calendar month (average
of that month's daily 3M compounded SORA readings), plus a `latest` block
with the most recent single daily reading for the page's headline figure.

Refresh model: lazy / on demand only, per standing house rule — rerun this
script by hand (or from a content-refresh skill) when the page needs newer
data. Do NOT wire this into cron/launchd; SORA moves slowly enough that a
fixed nightly job would burn a request for no reason most nights.

Usage:
    python3 scripts/build_sora_history.py

Network failures are handled gracefully: the script exits non-zero without
overwriting the existing JSON, so a bad run never wipes good data out from
under the live page.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor
import http.cookiejar

SOURCE_URL = "https://eservices.mas.gov.sg/statistics/dir/domesticinterestrates.aspx"
OUT_PATH = Path(__file__).resolve().parent.parent / "public" / "data" / "sora-history.json"
MONTHS_BACK = 24
TIMEOUT = 25

MONTH_NAMES = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _hidden_field(html: str, field_id: str) -> str:
    m = re.search(r'id="' + re.escape(field_id) + r'"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def _fetch(opener, url: str, data: dict | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; winfredquek.com SORA history builder)",
        "Referer": url,
        "Origin": "https://eservices.mas.gov.sg",
    }
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, headers=headers)
    with opener.open(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_daily_rows(start: date, end: date) -> list[tuple[date, float]]:
    """Returns [(value_date, sora_3m_pct), ...] for the given date range."""
    cj = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cj))

    html = _fetch(opener, SOURCE_URL)

    post_data = {
        "__VIEWSTATE": _hidden_field(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden_field(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden_field(html, "__EVENTVALIDATION"),
        "ctl00$ContentPlaceHolder1$StartYearDropDownList": str(start.year),
        "ctl00$ContentPlaceHolder1$StartMonthDropDownList": str(start.month),
        "ctl00$ContentPlaceHolder1$EndYearDropDownList": str(end.year),
        "ctl00$ContentPlaceHolder1$EndMonthDropDownList": str(end.month),
        # Checkbox index 16 in ColumnsCheckBoxList = "3-month Compounded SORA"
        "ctl00$ContentPlaceHolder1$ColumnsCheckBoxList$16": "on",
        "ctl00$ContentPlaceHolder1$Button1": "Display",
    }
    result_html = _fetch(opener, SOURCE_URL, post_data)

    # Table rows look like:
    #   <tr><td>2024</td><td>Aug</td><td>01</td>
    #       <td class="msbData" ...>02 Aug 2024</td>
    #       <td class="msbData" ...>3.6384</td></tr>
    # Year/month cells go blank (&nbsp;) on subsequent days in the same
    # month, so we carry the last seen year/month forward.
    row_re = re.compile(
        r"<tr>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>(\d{1,2})</td>\s*"
        r'<td class="msbData"[^>]*>[^<]*</td>\s*'
        r'<td class="msbData"[^>]*>([\d.]+)</td>\s*</tr>',
        re.IGNORECASE,
    )

    rows: list[tuple[date, float]] = []
    cur_year, cur_month = None, None
    for year_cell, month_cell, day_cell, value_cell in row_re.findall(result_html):
        year_cell = year_cell.strip().replace("&nbsp;", "")
        month_cell = month_cell.strip().replace("&nbsp;", "")
        if year_cell:
            cur_year = int(year_cell)
        if month_cell and month_cell in MONTH_NAMES:
            cur_month = MONTH_NAMES[month_cell]
        if cur_year is None or cur_month is None:
            continue
        try:
            d = date(cur_year, cur_month, int(day_cell))
            v = float(value_cell)
        except ValueError:
            continue
        rows.append((d, v))

    return rows


def to_monthly_averages(rows: list[tuple[date, float]]) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for d, v in rows:
        buckets[f"{d.year:04d}-{d.month:02d}"].append(v)

    out = []
    for key in sorted(buckets):
        vals = buckets[key]
        out.append({
            "month": key,
            "avg_3m_sora_pct": round(sum(vals) / len(vals), 4),
            "reading_count": len(vals),
        })
    return out


def main() -> int:
    today = date.today()
    # Go back MONTHS_BACK full months from the current month.
    start_month_index = (today.year * 12 + (today.month - 1)) - MONTHS_BACK
    start = date(start_month_index // 12, start_month_index % 12 + 1, 1)

    try:
        rows = fetch_daily_rows(start, today)
    except Exception as exc:  # network / parsing failure — never clobber good data
        print(f"build_sora_history.py: fetch failed ({exc}); leaving existing JSON untouched.", file=sys.stderr)
        return 1

    if not rows:
        print("build_sora_history.py: no rows parsed from MAS response; leaving existing JSON untouched.", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: r[0])
    monthly = to_monthly_averages(rows)
    latest_date, latest_value = rows[-1]

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M SGT"),
        "source": "MAS eServices — Domestic Interest Rates, 3-Month Compounded SORA",
        "source_url": SOURCE_URL,
        "unit": "percent",
        "range_months": MONTHS_BACK,
        "latest": {
            "date": latest_date.isoformat(),
            "value_pct": latest_value,
        },
        "months": monthly,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH} — {len(monthly)} months, latest {latest_date.isoformat()} = {latest_value}%")
    page = OUT_PATH.parent.parent / "tools" / "mortgage-rates.html"
    try:
        html = page.read_text(encoding="utf-8")
        stamped = re.sub(
            r"<!-- SORA-STATIC:START -->.*?<!-- SORA-STATIC:END -->",
            f'<!-- SORA-STATIC:START --><p style="margin:8px 0"><strong>3 Month Compounded SORA: {round(latest_value, 2)}%</strong> (as of {latest_date.isoformat()}, source MAS). Down from 3.60% in August 2024.</p><!-- SORA-STATIC:END -->',
            html, flags=re.S)
        if stamped != html:
            page.write_text(stamped, encoding="utf-8")
            print("stamped static SORA line on mortgage-rates.html")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

