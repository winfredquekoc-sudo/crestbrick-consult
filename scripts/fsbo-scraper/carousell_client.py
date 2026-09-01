"""Carousell fetch layer -- curl_cffi (Cloudflare-safe) + embedded JSON extraction.

The old requests+BeautifulSoup fetch in scraper.py never got past Cloudflare
(403 every time). curl_cffi with impersonate="chrome" does. Carousell has no
__NEXT_DATA__; each page carries exactly one <script type="application/json">
island holding the whole Redux store, and Carousell escapes every "/" in it
as \\u002F specifically so raw string content can never contain a literal
</script> -- so a non-greedy regex extraction is safe, not just lucky.

Two different shapes matter here:
  - Search page: SearchListing.searchCache.<requestKey>.results[].listingCard
    has title/price for the grid, but its aboveFold bump timestamps
    (active_bump / expired_bump) are promotion activity, NOT the post date.
  - Detail page (/p/<id>/): Listing.listingsMap[id] is fully populated,
    including the true top-level time_created -- this is the only reliable
    age signal, confirmed against a listing whose time_created (Dec 2024)
    matched its photo upload dates while the search card's bump timestamp
    for the same listing showed a few days ago.
"""

import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from curl_cffi import requests as cr

JSON_ISLAND_RE = re.compile(r'<script type="application/json">(.*?)</script>', re.DOTALL)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?65[\s\-]?)?([89]\d{3}[\s\-]?\d{4})(?!\d)")

SG_TOWNS = [
    "ang mo kio", "bedok", "bishan", "bukit batok", "bukit merah", "bukit panjang",
    "bukit timah", "choa chu kang", "clementi", "geylang", "hougang", "jurong east",
    "jurong west", "kallang", "whampoa", "marine parade", "pasir ris", "punggol",
    "queenstown", "sembawang", "sengkang", "serangoon", "tampines", "toa payoh",
    "woodlands", "yishun", "novena", "outram", "river valley", "tanglin",
    "tiong bahru", "boon lay", "dover", "west coast",
]
STREET_SUFFIX = (
    r"(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Close|Cl|Crescent|Cres|Walk|Lane|Ln|"
    r"Place|Pl|Link|Terrace|Grove|Park|Way|Hill|Ring|Loop|Central|North|South|East|West)"
)
BLOCK_STREET_RE = re.compile(
    rf"\b(\d{{1,4}}[A-Za-z]?\s+[A-Za-z][A-Za-z'\s]{{1,25}}?\s{STREET_SUFFIX}\b(?:\s\d{{1,3}})?)",
    re.IGNORECASE,
)
BLOCK_ONLY_RE = re.compile(r"\bblk?\.?\s*(\d{1,4}[A-Za-z]?)\b", re.IGNORECASE)


def extract_address_hint(*texts: str) -> Optional[str]:
    blob = " ".join(t for t in texts if t)
    m = BLOCK_STREET_RE.search(blob)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    m = BLOCK_ONLY_RE.search(blob)
    block = m.group(0).strip() if m else None
    town = next((t.title() for t in SG_TOWNS if t in blob.lower()), None)
    if block and town:
        return f"{block}, {town}"
    return block or town


def extract_phone(*texts: str) -> Optional[str]:
    """SG mobile regex over description + seller display name/username.

    Sellers commonly embed a WhatsApp number in their Carousell display name
    (observed live: lastName "whatsapp 8911XXXX", masked here) -- treat that
    the same as a number in the listing description. Tolerates one internal
    space/hyphen separator ("9123 4567", "9123-4567"), stripped out before
    normalizing.
    """
    blob = " ".join(t for t in texts if t)
    m = PHONE_RE.search(blob)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return f"+65{digits}"


def compute_listing_age(listing: Dict):
    """Returns (days_old, date_unknown) from the detail page's time_created."""
    ts = listing.get("time_created")
    if not ts:
        return None, True
    try:
        created = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, True
    return (datetime.now(timezone.utc) - created).days, False


class CarousellClient:
    """curl_cffi fetch with retry-once-on-non-200 + randomized delay. Never raises."""

    def __init__(self, search_base_url: str, logger, delay_range=(2.0, 4.0), timeout=25):
        self.search_base_url = search_base_url.rstrip("/")
        self.logger = logger
        self.delay_range = delay_range
        self.timeout = timeout
        self.fetch_count = 0

    def _get(self, url: str) -> Optional[str]:
        time.sleep(random.uniform(*self.delay_range))
        for attempt in (1, 2):
            try:
                r = cr.get(url, impersonate="chrome", timeout=self.timeout)
                self.fetch_count += 1
                if r.status_code == 200:
                    return r.text
                self.logger.warning(f"HTTP {r.status_code} on attempt {attempt}: {url}")
            except Exception as e:
                self.logger.warning(f"Fetch error attempt {attempt} for {url}: {e}")
            if attempt == 1:
                time.sleep(2.0)
        self.logger.error(f"Giving up on {url} after 2 attempts")
        return None

    def _extract_json(self, html: str) -> Optional[dict]:
        m = JSON_ISLAND_RE.search(html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON island parse failed: {e}")
            return None

    def search(self, query: str) -> List[Dict]:
        """Page 1 of one search query. Returns [{id, title}, ...]."""
        url = f"{self.search_base_url}/{quote(query)}"
        html = self._get(url)
        if not html:
            return []
        data = self._extract_json(html)
        if not data:
            self.logger.warning(f"No JSON island on search page for '{query}'")
            return []
        try:
            cache = data["SearchListing"]["searchCache"]
            entry = next(iter(cache.values()))
            results = entry.get("results", [])
        except (KeyError, StopIteration, TypeError) as e:
            self.logger.warning(f"Unexpected search JSON shape for '{query}': {e}")
            return []

        out = []
        for r in results:
            lc = r.get("listingCard") or {}
            lid = lc.get("id")
            if not lid:
                continue
            below = lc.get("belowFold", [])
            title = next(
                (b.get("stringContent") for b in below if b.get("component") == "header_1"), ""
            )
            out.append({"id": str(lid), "title": title})
        return out

    def fetch_detail(self, listing_id: str) -> Optional[Dict]:
        """Fetch /p/<id>/ and return the raw dict from Listing.listingsMap[id]."""
        url = f"https://www.carousell.sg/p/{listing_id}/"
        html = self._get(url)
        if not html:
            return None
        data = self._extract_json(html)
        if not data:
            return None
        try:
            listing = data["Listing"]["listingsMap"].get(str(listing_id))
        except (KeyError, TypeError):
            listing = None
        if not listing or not listing.get("title"):
            self.logger.warning(f"Empty/missing listing data for {listing_id}")
            return None
        return listing
