#!/usr/bin/env python3
"""I82 + I90 — generate JSON-LD schema.org markup for listings + Open Graph metadata for every page.

Reads:  public/listings.json
Writes: public/listings-schema.json (consolidated JSON-LD)
        public/listings.html injection-ready snippet at /tmp/listings-schema-snippet.html
        public/_og_index.json mapping pages -> OG image URL

Usage:  python3 scripts/_inject_schema_and_og.py
"""
import json
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUB = ROOT / "public"
SITE_BASE = "https://winfredquek.com"


def build_listings_schema():
    src = PUB / "listings.json"
    if not src.exists():
        print("listings.json missing", file=sys.stderr)
        return
    data = json.loads(src.read_text())
    items = []
    for L in data.get("listings", []):
        item = {
            "@context": "https://schema.org",
            "@type": "RealEstateListing",
            "name": L.get("title"),
            "description": L.get("description") or L.get("title"),
            "url": f"{SITE_BASE}/listings.html#{L.get('id','')}",
            "datePosted": data.get("synced_at", "").split("T")[0],
            "address": {
                "@type": "PostalAddress",
                "addressLocality": L.get("district"),
                "addressCountry": "SG",
            },
        }
        if L.get("price"):
            item["price"] = L["price"]
            item["priceCurrency"] = L.get("currency", "SGD")
        if L.get("beds"):
            item["numberOfRooms"] = L["beds"]
        if L.get("sqft"):
            item["floorSize"] = {"@type": "QuantitativeValue", "value": L["sqft"], "unitCode": "FTK"}
        items.append(item)

    out = {
        "@context": "https://schema.org",
        "@graph": items,
    }
    (PUB / "listings-schema.json").write_text(json.dumps(out, indent=2))
    snippet = '<script type="application/ld+json">' + json.dumps(out) + "</script>"
    pathlib.Path("/tmp/listings-schema-snippet.html").write_text(snippet)
    print(f"Wrote {len(items)} schema.org RealEstateListing entries")


def build_og_map():
    """Stub for I90 — the actual image generation lives elsewhere; this builds the lookup map."""
    pages = []
    for html in PUB.glob("*.html"):
        slug = html.stem
        og_url = f"{SITE_BASE}/img/og/{slug}.png"
        pages.append({"page": f"/{html.name}", "og_image": og_url, "slug": slug})
    (PUB / "_og_index.json").write_text(json.dumps(pages, indent=2))
    print(f"Wrote OG index with {len(pages)} pages")


if __name__ == "__main__":
    build_listings_schema()
    build_og_map()
