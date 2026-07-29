#!/usr/bin/env python3
"""
gen-website-listings.py — builds public/listings.json for winfredquek.com from
Winfred's OWN rental inventory (landlord database), not from PropertyGuru/99.co.
His PropertyGuru agent profile currently shows 0 active listings and portal
scraping hits bot walls, so the landlord DB — refreshed nightly by the WhatsApp
intake pipeline (see scripts/build_landlord_db.py, the refresh-rental-dbs skill,
and scripts/build-listing-index.py) — is the reliable source of truth.

SOURCE: ~/crestbrick-consult/_templates/landlord-db.json
  This file holds landlord names, phone numbers, and free-text WhatsApp notes.
  It is intentionally NEVER committed to git (untracked; contains PII), so this
  script always reads it from its fixed absolute path regardless of which repo
  checkout / worktree it is invoked from (a fresh worktree checked out from
  origin/main will not have it, by design — see publish-listings-nightly.sh).

PRIVACY / CEA RULES (hard — do not relax):
  - no landlord names
  - no phone numbers
  - no exact unit numbers (floor-unit like #05-123 -> stripped to block+street)
  - no tenant info
  - never echo raw free-text landlord fields verbatim in output — those fields
    contain names and conversational asides (e.g. "confirm with Jane"). Every
    public string is either a stripped address or a templated highlight derived
    from a keyword check, never a verbatim copy of landlord-db text.

One listing per landlord row (not per pricing tier): many rows describe a single
room at more than one occupancy price ("1 pax $900 / 2 pax $1,200") rather than
two distinct rentable rooms — collapsing those into one listing with a "From"
price avoids duplicate/phantom cards.

Usage:
  python3 scripts/gen-website-listings.py [--db PATH] [--root REPO_ROOT] [--dry-run]

Writes (under --root, default ~/crestbrick-consult):
  public/listings.json        the live file the site fetches
  public/listings.json.prev   previous version (for diff / rollback)

Prints a summary diff (added / removed / price changes) vs the previous file.
Idempotent: rerunning against an unchanged landlord DB reproduces the same
listings (only synced_at advances, by design — the site shows a fresh
"updated daily" timestamp even when the inventory itself hasn't changed).
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
from urllib.parse import quote

DEFAULT_DB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
DEFAULT_AREA_DEMAND = os.path.expanduser("~/crestbrick-consult/_templates/area-demand.json")
DEFAULT_ROOT = os.path.expanduser("~/crestbrick-consult")
WA_NUMBER = "6581618149"

NEGATIVE_MARKERS = ("rented out", "tenanted", "taken", "occupied", "not available", "closed", "unavailable")

# Order matters: first match wins. "master"/"common" checked before the bare
# "common" fallback so "common room" and "master" both resolve sensibly.
TYPE_KEYWORDS = [
    ("whole flat", "Whole Unit"),
    ("whole unit", "Whole Unit"),
    ("entire unit", "Whole Unit"),
    ("common room", "Common Room"),
    ("master", "Master Room"),
    ("studio", "Studio"),
    ("private room", "Private Room"),
    ("office", "Office Space"),
    ("common", "Common Room"),
]

OPERATOR_RE = re.compile(r"multi-property operator", re.I)


# ───────────────────────── landlord-db parsing helpers ─────────────────────────

def availability(l):
    """Mirrors scripts/matchmaker/export_data.py's availability() so the website
    pipeline and the internal matchmaker agree on what counts as live inventory."""
    if l.get("offer_pending"):
        return "Offer pending"
    st = (l.get("status") or "").lower()
    if st.startswith("closed (tenanted") or st.startswith("closed (unavailable"):
        return "Taken"
    if st.startswith("closed") or st.startswith("archived") or st.startswith("cold"):
        return "Off market"
    has_price = bool(l.get("rent_min") or l.get("rent_max"))
    cobroke = "co-broke" in (l.get("contact_label_source") or "").lower()
    if st in ("active", "channel", "active-verify") and (has_price or cobroke):
        return "Available"
    return "Pending"


def is_addressable(l):
    """Skip roster rows that aren't a specific rentable unit (e.g. a
    multi-property co-living operator row with no real address to anchor a
    public listing card to)."""
    addr = l.get("full_address") or ""
    if OPERATOR_RE.search(addr) or "multi-property" in addr.lower():
        return False
    return True


def filtered_rooms_text(rooms_and_rent):
    """Drop clauses that read as already let out, so type/price/highlight
    extraction never leans on a room that isn't actually available."""
    raw = rooms_and_rent or ""
    parts = re.split(r"[;/]", raw) if raw else []
    kept = [p.strip() for p in parts if not any(m in p.lower() for m in NEGATIVE_MARKERS)]
    return "; ".join(kept)


def classify_type(text):
    t = (text or "").lower()
    for kw, label in TYPE_KEYWORDS:
        if kw in t:
            return label
    return "Room"


def clean_address(addr):
    """Strip floor-unit numbers, postal codes, and parenthetical asides; keep
    block + street + estate name only. Parens are stripped as whole groups
    (before any comma-splitting) so an internal comma inside a note like
    '(unit TBC; ... S469983, confirm)' can't leak a stray fragment."""
    if not addr:
        return ""
    a = addr
    a = re.sub(r"#\s*\d{1,3}[A-Za-z]?\s*-\s*\d{1,5}[A-Za-z]?", "", a)   # #05-123
    a = re.sub(r"#\s*\d{1,3}[A-Za-z]?\b", "", a)                        # bare #05 (floor only)
    a = re.sub(r"\bS\s?\d{6}\b", "", a)                                  # S123456
    a = re.sub(r"\bSingapore\s+\d{6}\b", "", a, flags=re.I)              # Singapore 123456
    a = re.sub(r"\bPostal\s*\d{6}\b", "", a, flags=re.I)                 # Postal 123456
    a = re.sub(r"\(\s*\d{6}\s*\)", "", a)                                # (123456)
    a = re.sub(r"\b\d{6}\b", "", a)                                      # bare 6-digit postal
    a = re.sub(r"\([^()]*\)", "", a)                                     # any remaining parenthetical
    a = re.sub(r"\s{2,}", " ", a)
    a = re.sub(r",\s*,", ",", a)
    a = re.sub(r"^\s*,\s*|\s*,\s*$", "", a)
    return a.strip(" ,")


def pick_location(cleaned_addr):
    """Prefer a named estate/street segment over a bare block number."""
    segs = [s.strip(" ,") for s in cleaned_addr.split(",") if s.strip(" ,")]
    named = [s for s in segs if s and not re.match(r"^\d", s)]
    if named:
        return named[0]
    if segs:
        return segs[0]
    return ""


def sqft_from(*texts):
    for t in texts:
        m = re.search(r"(\d{2,5})\s*sq\s*ft|\b(\d{2,5})\s*sqft", (t or ""), re.I)
        if m:
            v = int(next(g for g in m.groups() if g))
            if 50 <= v <= 20000:
                return v
    return None


def baths_shared_from(text):
    t = (text or "").lower()
    if re.search(r"own bathroom|private bathroom|not shared", t):
        return False
    if re.search(r"share[sd]?\s+bathroom|shared bath", t):
        return True
    return None


def highlights_from(text, reserved):
    """Templated, non-verbatim highlight strings derived from keyword checks only —
    never echoes the underlying free text (which may contain landlord names)."""
    t = (text or "").lower()
    out = []
    if reserved:
        out.append("Offer pending — enquire to join the waitlist")
    if "mrt" in t:
        out.append("Near MRT")
    if re.search(r"utilit\w*.*(incl|included)|incl\w*.*utilit", t):
        out.append("Utilities included")
    if "furnished" in t:
        out.append("Furnished")
    if "aircon" in t:
        out.append("Aircon")
    if baths_shared_from(t) is False:
        out.append("Own bathroom")
    if re.search(r"available now|available immediately|\bimmediate\b", t):
        out.append("Available now")
    seen, dedup = set(), []
    for h in out:
        if h not in seen:
            dedup.append(h)
            seen.add(h)
    return dedup


# Photos: convention-based. Drop files into public/img/listings/ named by the
# listing's generated id (see listings.json "id"), e.g. "eastpoint-green-tampines-1.jpg".
# Prefix match, first hit wins; jpg/webp preferred for the <img> src.
PHOTO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "img", "listings")

def find_photo(listing_id):
    try:
        files = sorted(os.listdir(PHOTO_DIR))
    except OSError:
        return None
    for ext in (".jpg", ".jpeg", ".webp", ".png"):
        for f in files:
            if not f.lower().endswith(ext):
                continue
            base = re.sub(r"-\d+$", "", f.rsplit(".", 1)[0].lower())
            # match with or without the trailing landlord id (-llNNN)
            lid_short = re.sub(r"-ll\d+$", "", listing_id)
            if (listing_id.startswith(base) or base.startswith(listing_id)
                    or lid_short == base or lid_short.startswith(base) or base.startswith(lid_short)):
                return "/img/listings/" + f
    return None


def property_category(property_type):
    t = (property_type or "").lower()
    if "hdb" in t:
        return "HDB"
    if "condo" in t:
        return "Condo"
    if "landed" in t or "terrace" in t or "bungalow" in t:
        return "Landed"
    if "office" in t or "industrial" in t:
        return "Industrial"
    return "HDB"


def price_range(available_text, rent_min, rent_max):
    """Prefer $ amounts found in the still-available portion of rooms_and_rent
    (occupancy-tier pricing, e.g. "1 pax $900 / 2 pax $1,200"); fall back to
    the structured rent_min/rent_max fields when free text has none."""
    amounts = [int(m.replace(",", "")) for m in re.findall(r"\$\s*([\d,]{3,6})", available_text or "")]
    if amounts:
        return min(amounts), max(amounts)
    if rent_min or rent_max:
        lo = rent_min or rent_max
        hi = rent_max or rent_min
        return lo, hi
    return None, None


def wa_link(text_body):
    encoded = quote(f"Hi Winfred, asking about {text_body}.")
    return encoded, f"https://wa.me/{WA_NUMBER}?text={encoded}"


def load_dist_area(path):
    try:
        d = json.load(open(path))
        return {x["district"]: x.get("area", "") for x in d.get("districts", [])}
    except Exception:
        return {}


def build_listing(l, dist_area):
    reserved = availability(l) == "Offer pending"
    available_text = filtered_rooms_text(l.get("rooms_and_rent"))

    unit_type = classify_type(available_text)
    if unit_type == "Room":
        unit_type = classify_type(l.get("property_type"))

    cleaned_addr = clean_address(l.get("full_address"))
    location = pick_location(cleaned_addr)

    area = dist_area.get(l.get("district"), "") or ""
    area_tokens = [t.strip() for t in area.split(",") if t.strip()]
    first_area = area_tokens[0] if area_tokens else ""

    if not location:
        location = first_area or (l.get("district") or "") or "Singapore"
        title_loc = location
    else:
        title_loc = location
        # only append an area hint when the location isn't already one of the
        # district's known area names (avoids e.g. "Balestier (Toa Payoh)"
        # reading as if Balestier were inside Toa Payoh)
        already_named = any(
            tok.lower() in title_loc.lower() or title_loc.lower() in tok.lower()
            for tok in area_tokens
        )
        if first_area and not already_named:
            title_loc = f"{location} ({first_area})"

    if not title_loc:
        return None

    title = f"{unit_type} — {title_loc}".strip(" —")
    if reserved:
        title += " (Reserved)"

    lo, hi = price_range(available_text, l.get("rent_min"), l.get("rent_max"))
    if lo is None:
        price, price_label = None, "POA"
    elif lo == hi:
        price, price_label = lo, f"S${lo:,} / mo"
    else:
        price, price_label = lo, f"From S${lo:,} / mo"

    sqft = sqft_from(available_text, l.get("full_address"))
    highlights = highlights_from(" ".join([available_text, l.get("property_type") or ""]), reserved)
    encoded_wa, wa_url = wa_link(f"{unit_type} in {location}")

    landlord_id = (l.get("id") or "").lower()
    slug_src = f"{unit_type}-{location}-{landlord_id}"
    listing_id = re.sub(r"[^a-z0-9]+", "-", slug_src.lower()).strip("-")

    item = {
        "id": listing_id,
        "title": title,
        "type": property_category(l.get("property_type")),
        "transaction": "rent",
        "price": price,
        "price_label": price_label,
        "address": location,
        "district": l.get("district") or "",
        "sqft": sqft,
        "highlights": highlights,
        "image": find_photo(listing_id),
        "wa_text": encoded_wa,
        "url": wa_url,
    }
    return {k: v for k, v in item.items() if v not in (None, "", [])}


# ───────────────────────────────── main pipeline ─────────────────────────────────

def generate(db_path, area_demand_path):
    db = json.load(open(db_path))
    dist_area = load_dist_area(area_demand_path)
    listings = []
    for l in db.get("landlords", []):
        if availability(l) not in ("Available", "Offer pending"):
            continue
        if not is_addressable(l):
            continue
        item = build_listing(l, dist_area)
        if item:
            listings.append(item)
    listings.sort(key=lambda x: (x.get("district", ""), x["title"]))
    return listings


def diff_report(old_listings, new_listings):
    old_by_id = {x["id"]: x for x in old_listings}
    new_by_id = {x["id"]: x for x in new_listings}
    added = [i for i in new_by_id if i not in old_by_id]
    removed = [i for i in old_by_id if i not in new_by_id]
    price_changes = [
        (i, old_by_id[i].get("price"), new_by_id[i].get("price"))
        for i in new_by_id
        if i in old_by_id and old_by_id[i].get("price") != new_by_id[i].get("price")
    ]
    lines = [f"added: {len(added)}"]
    for i in added:
        lines.append(f"  + {new_by_id[i]['title']} ({new_by_id[i].get('price_label', 'POA')})")
    lines.append(f"removed: {len(removed)}")
    for i in removed:
        lines.append(f"  - {old_by_id[i]['title']}")
    lines.append(f"price changes: {len(price_changes)}")
    for i, old_p, new_p in price_changes:
        lines.append(f"  ~ {new_by_id[i]['title']}: {old_p} -> {new_p}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--area-demand", default=DEFAULT_AREA_DEMAND)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", default=None, help="override output path (default <root>/public/listings.json)")
    ap.add_argument("--dry-run", action="store_true", help="print the diff, do not write files")
    args = ap.parse_args()

    out_path = args.out or os.path.join(args.root, "public", "listings.json")
    prev_path = out_path + ".prev"

    if not os.path.exists(args.db):
        print(f"landlord DB not found at {args.db}; nothing to generate.", file=sys.stderr)
        return 1

    new_listings = generate(args.db, args.area_demand)

    old_listings = []
    if os.path.exists(out_path):
        try:
            old_listings = json.load(open(out_path)).get("listings", [])
        except Exception:
            old_listings = []

    print(diff_report(old_listings, new_listings))
    print(f"total available listings: {len(new_listings)}")

    if args.dry_run:
        print("(dry run — no files written)")
        return 0

    if os.path.exists(out_path):
        shutil.copy2(out_path, prev_path)

    payload = {
        "agent": "Winfred Quek (CEA R073319H)",
        "source": "landlord-db",
        "synced_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(new_listings),
        "listings": new_listings,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {out_path} ({len(new_listings)} listings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
