#!/usr/bin/env python3
"""
ingest-markdown-clients.py

Walks ~/real-estate-agent/clients/{prospects,active,closed,closing,tenant-*}/
and produces a CSV at /tmp/client-ingest.csv whose columns map directly to
the Postgres `clients` table.

DRY-RUN by default — does NOT write to the database. The user runs:

  psql "$DATABASE_URL" -c "\\copy clients(...) FROM '/tmp/client-ingest.csv' \
       WITH (FORMAT csv, HEADER true)"

Dedupes on E.164 phone (first occurrence wins; later folders add a note).

Frontmatter expected (best-effort — fields are optional):
---
name: <display name>
phone: +6591234567        # any format; normalised to E.164
email: foo@bar.com
source: <referral_source string>
date_added: 2026-04-22
stage: cold|lead|qualified|engaged|transacting|closed|nurture|lost
icp: hdb_upgrader|investor|decoupler|family_office|expat|seller|not_fit|unknown
priority: hot|warm|cold
nationality: SC|PR|Foreigner|Malaysian|...
---

If frontmatter is missing, falls back to the first H1 as display_name and
uses the directory name as slug.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

CLIENTS_ROOT = Path.home() / "real-estate-agent" / "clients"
OUT_CSV      = Path("/tmp/client-ingest.csv")

STAGE_MAP = {
    "cold": "lead", "warm": "lead", "hot": "qualified",
    "lead": "lead", "qualified": "qualified", "engaged": "engaged",
    "transacting": "transacting", "closed": "closed",
    "nurture": "nurture", "lost": "lost",
}
ICP_VALID = {"hdb_upgrader","investor","decoupler","family_office","expat","seller","not_fit"}
CITIZENSHIP_MAP = {
    "sc": "SC", "singaporean": "SC", "singapore citizen": "SC",
    "pr": "PR", "permanent resident": "PR",
    "foreigner": "Foreigner", "expat": "Foreigner",
    "malaysian": "Foreigner", "indonesian": "Foreigner",
    "chinese": "Foreigner", "indian": "Foreigner",
}

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+?)$", re.MULTILINE)


def normalize_phone(raw: str | None) -> str | None:
    if not raw or raw.strip().lower() in {"unknown", "none", "n/a", ""}:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return None
    if digits.startswith("+"):
        return digits
    # Heuristics: 8 digits → SG (+65); else assume already country-coded
    if len(digits) == 8:
        return "+65" + digits
    if digits.startswith("65") and len(digits) == 10:
        return "+" + digits
    if digits.startswith("60") and len(digits) >= 10:
        return "+" + digits
    return "+" + digits


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip().lower()] = v.strip().strip('"').strip("'")
    return out


def extract_h1(text: str) -> str | None:
    m = H1_RE.search(text)
    if not m:
        return None
    return re.sub(r"\s*\(.*?\)\s*", "", m.group(1)).strip() or None


def map_stage(raw: str | None) -> str:
    if not raw:
        return "lead"
    return STAGE_MAP.get(raw.strip().lower(), "lead")


def map_icp(raw: str | None) -> str | None:
    if not raw:
        return None
    v = raw.strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in ICP_VALID else None


def map_citizenship(raw: str | None) -> str | None:
    if not raw:
        return None
    return CITIZENSHIP_MAP.get(raw.strip().lower())


def map_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def walk_clients() -> list[dict]:
    rows: list[dict] = []
    seen_phones: dict[str, str] = {}

    if not CLIENTS_ROOT.exists():
        print(f"WARN: {CLIENTS_ROOT} not found", file=sys.stderr)
        return rows

    profile_files = sorted(CLIENTS_ROOT.glob("*/*/profile.md"))
    for profile in profile_files:
        bucket_dir = profile.parent.parent.name      # prospects | active | closed | tenant-*
        slug       = profile.parent.name
        try:
            text = profile.read_text(encoding="utf-8")
        except Exception as e:
            print(f"SKIP {profile}: {e}", file=sys.stderr)
            continue

        fm = parse_frontmatter(text)
        display_name = fm.get("name") or extract_h1(text) or slug.replace("-", " ").title()
        display_name = re.sub(r'^"|"$', "", display_name).strip()

        phone = normalize_phone(fm.get("phone"))
        if phone and phone in seen_phones:
            print(f"DEDUP {phone}: keeping {seen_phones[phone]}, dropping {slug}", file=sys.stderr)
            continue
        if phone:
            seen_phones[phone] = slug

        rows.append({
            "slug":              slug,
            "display_name":      display_name,
            "phone":             phone or "",
            "email":             fm.get("email", ""),
            "citizenship":       map_citizenship(fm.get("nationality") or fm.get("citizenship")) or "",
            "icp_bucket":        map_icp(fm.get("icp")) or "",
            "referral_source":   fm.get("source", ""),
            "stage":             map_stage(fm.get("stage")),
            "lead_temperature":  (fm.get("priority", "Cold").title()),
            "lead_source":       fm.get("source", "Unknown") or "Unknown",
            "first_contact_at":  map_date(fm.get("date_added")) or datetime.utcnow().date().isoformat(),
            "_bucket":           bucket_dir,    # ignored on insert; for debugging
        })
    return rows


def main() -> int:
    rows = walk_clients()
    if not rows:
        print("No client profiles found.", file=sys.stderr)
        return 1

    cols = ["slug","display_name","phone","email","citizenship","icp_bucket",
            "referral_source","stage","lead_temperature","lead_source",
            "first_contact_at"]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"DRY-RUN: wrote {len(rows)} client rows to {OUT_CSV}")
    print("To load:")
    print(f'  psql "$DATABASE_URL" -c "\\\\copy clients({",".join(cols)}) '
          f"FROM '{OUT_CSV}' WITH (FORMAT csv, HEADER true)\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
