#!/usr/bin/env python3
"""Build public/data/valuation-hdb.json — aggregated HDB resale medians for
the /tools/valuation instant valuation tool.

Pulls the last 6 full months of HDB resale transactions from data.gov.sg
(dataset d_8b84c4ee58e3cfc0ece0d773c8ca6abc, "Resale flat prices based on
registration date"), aggregates to (town, flat_type, storey_band) cells with
median resale price, median price per sqm, p25/p75 price per sqm (used by
the tool to scale by floor area), and transaction count. Cells with fewer
than MIN_TXNS transactions are nulled out rather than shown.

No raw transactions are written to the output — aggregates only, kept well
under 300KB.

Usage: python3 scripts/build_valuation_data.py
"""
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

RESOURCE_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
API = "https://data.gov.sg/api/action/datastore_search"
PAGE_SIZE = 1000
MIN_TXNS = 5
OUT_PATH = "public/data/valuation-hdb.json"


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


def fetch_month(month, max_retries=5):
    """Paginate datastore_search for one month, retrying on rate limits."""
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
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                wait = 12
                print(f"  HTTP {e.code} on {month} offset {offset}, retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                data = None
        else:
            raise RuntimeError(f"Failed to fetch {month} offset {offset} after {max_retries} retries")

        if not data or not data.get("success"):
            code = (data or {}).get("code")
            if code == 24:  # TOO_MANY_REQUESTS came back success:false shaped differently
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


def storey_band(storey_range):
    try:
        low = int(storey_range.strip().split(" TO ")[0])
    except (ValueError, AttributeError, IndexError):
        return None
    if low <= 6:
        return "low"
    if low <= 12:
        return "mid"
    return "high"


def percentile(sorted_vals, pct):
    """Simple linear-interpolation percentile, sorted_vals must be sorted."""
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


def main():
    months = last_n_full_months(6)
    print(f"Fetching HDB resale transactions for: {', '.join(months)}")

    all_records = []
    for month in months:
        print(f"Fetching {month}...")
        recs = fetch_month(month)
        print(f"  {len(recs)} records")
        all_records.extend(recs)

    print(f"\nTotal records fetched: {len(all_records)}")

    # cell key: (town, flat_type, band) -> list of (resale_price, psm)
    cells = defaultdict(list)
    dropped_bad_rows = 0
    for r in all_records:
        try:
            price = float(r["resale_price"])
            area = float(r["floor_area_sqm"])
            if area <= 0:
                dropped_bad_rows += 1
                continue
            band = storey_band(r["storey_range"])
            if band is None:
                dropped_bad_rows += 1
                continue
            psm = price / area
        except (KeyError, ValueError, TypeError):
            dropped_bad_rows += 1
            continue
        key = (r["town"], r["flat_type"], band)
        cells[key].append((price, psm))

    if dropped_bad_rows:
        print(f"Dropped {dropped_bad_rows} rows with malformed price/area/storey fields")

    month_min, month_max = min(months), max(months)
    cells_out = {}
    kept, dropped_small = 0, 0
    for (town, flat_type, band), rows in cells.items():
        n = len(rows)
        key = f"{town}|{flat_type}|{band}"
        if n < MIN_TXNS:
            cells_out[key] = None
            dropped_small += 1
            continue
        prices = sorted(p for p, _ in rows)
        psms = sorted(s for _, s in rows)
        cells_out[key] = {
            "median_price": round(statistics.median(prices)),
            "median_psm": round(statistics.median(psms), 1),
            "p25_psm": round(percentile(psms, 0.25), 1),
            "p75_psm": round(percentile(psms, 0.75), 1),
            "count": n,
        }
        kept += 1

    towns = sorted({t for (t, _, _) in cells.keys()})
    flat_types = sorted({ft for (_, ft, _) in cells.keys()})

    out = {
        "generated": date.today().isoformat(),
        "source": "data.gov.sg — HDB Resale Flat Prices (dataset d_8b84c4ee58e3cfc0ece0d773c8ca6abc)",
        "month_range": {"from": month_min, "to": month_max},
        "min_transactions_per_cell": MIN_TXNS,
        "towns": towns,
        "flat_types": flat_types,
        "storey_bands": {"low": "01 to 06", "mid": "07 to 12", "high": "13 and above"},
        "cells": cells_out,
    }

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    import os
    size_kb = os.path.getsize(OUT_PATH) / 1024

    print(f"\nWrote {OUT_PATH} ({size_kb:.1f} KB)")
    print(f"Towns: {len(towns)}, flat types: {len(flat_types)}, cells kept: {kept}, cells dropped (<{MIN_TXNS} txns): {dropped_small}")
    print(f"Month range covered: {month_min} to {month_max}")


if __name__ == "__main__":
    main()
