#!/usr/bin/env python3
"""
generate-launches.py
Reads ~/.claude/state/new-launches/watchlist.md and generates
public/new-launches-data.js for the new-launches page.

Usage: python3 scripts/generate-launches.py
"""

import os
import re
import json
from pathlib import Path

WATCHLIST_PATH = Path.home() / ".claude/state/new-launches/watchlist.md"
OUTPUT_PATH = Path(__file__).parent.parent / "public/new-launches-data.js"

# District to region mapping
DISTRICT_REGION = {
    "D1": "CCR", "D2": "CCR", "D3": "CCR", "D4": "CCR", "D5": "OCR",
    "D6": "CCR", "D7": "CCR", "D8": "CCR", "D9": "CCR", "D10": "CCR",
    "D11": "CCR", "D12": "RCR", "D13": "RCR", "D14": "RCR", "D15": "RCR",
    "D16": "OCR", "D17": "OCR", "D18": "OCR", "D19": "RCR", "D20": "RCR",
    "D21": "OCR", "D22": "OCR", "D23": "OCR", "D24": "OCR", "D25": "OCR",
    "D26": "OCR", "D27": "OCR", "D28": "OCR"
}

AREA_MAP = {
    "D1": "City", "D2": "City", "D3": "Alexandra", "D4": "Harbourfront",
    "D5": "Clementi", "D6": "City Hall", "D7": "Beach Road", "D8": "Farrer Park",
    "D9": "Orchard", "D10": "Bukit Timah", "D11": "Newton", "D12": "Toa Payoh",
    "D13": "Macpherson", "D14": "Geylang", "D15": "East Coast", "D16": "Bedok",
    "D17": "Changi", "D18": "Tampines", "D19": "Hougang", "D20": "Bishan",
    "D21": "Clementi / Buona Vista", "D22": "Boon Lay", "D23": "Bukit Batok",
    "D24": "Lim Chu Kang", "D25": "Choa Chu Kang", "D26": "Upper Thomson",
    "D27": "Sembawang", "D28": "Sengkang"
}


def slugify(name):
    """Convert project name to URL-safe slug."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s


def parse_watchlist(path):
    """Parse watchlist.md and return list of project dicts."""
    if not path.exists():
        print(f"WARNING: Watchlist not found at {path}")
        return []

    projects = []
    seen_names = set()

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip blanks, comments, headers
            if not line or line.startswith("#") or line.startswith("##"):
                continue
            if "|" not in line:
                continue

            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue

            name = parts[0]
            # Skip blank names, template placeholder rows, and section headers
            if not name or name.lower() in ("project name", "project", "example"):
                continue
            # Skip lines that are clearly template examples (lowercase with angle brackets)
            if name.startswith("<") or name.startswith("Example"):
                continue

            # Deduplicate by name
            if name in seen_names:
                continue
            seen_names.add(name)

            status_raw = parts[1].lower() if len(parts) > 1 else "upcoming"

            # Determine canonical status
            if "launched" in status_raw or "active" in status_raw:
                status = "launched"
            elif "resale" in status_raw:
                status = "launched"
            else:
                status = "upcoming"

            # Extract fields from the note column (parts[2] onward joined)
            note_combined = " | ".join(parts[2:]) if len(parts) > 2 else ""

            # Try to extract district
            district_match = re.search(r"\b(D\d{1,2})\b", note_combined, re.IGNORECASE)
            district = district_match.group(1).upper() if district_match else "TBC"

            # Try to extract PSF
            psf_match = re.search(r"([\d,]+-[\d,]+)\s*(?:psf)?", note_combined, re.IGNORECASE)
            if psf_match:
                psf = "S$" + psf_match.group(1) + " psf"
            else:
                psf = "TBC"

            # Try to extract year/quarter for launch date
            launch_match = re.search(r"\b(20\d{2})\b", note_combined)
            if launch_match:
                year = launch_match.group(1)
                q_match = re.search(r"(Q[1-4])\s*" + year, note_combined, re.IGNORECASE)
                if q_match:
                    launch = q_match.group(1) + " " + year
                else:
                    launch = year
            else:
                launch = "TBC"

            # Try to extract units
            units_match = re.search(r"([\d,]+)\s+units?", note_combined, re.IGNORECASE)
            units = units_match.group(1).replace(",", "") if units_match else "TBC"

            # Region and area from district
            region = DISTRICT_REGION.get(district, "OCR")
            area = AREA_MAP.get(district, "Singapore")

            # Clean note (strip district, PSF, units -- leave descriptive text)
            note_clean = re.sub(r"\b(D\d{1,2})\b", "", note_combined)
            note_clean = re.sub(r"[\d,]+-[\d,]+\s*(psf)?", "", note_clean, flags=re.IGNORECASE)
            note_clean = re.sub(r"[\d,]+\s+units?", "", note_clean, flags=re.IGNORECASE)
            note_clean = re.sub(r"\b20\d{2}\b", "", note_clean)
            note_clean = re.sub(r"\b(Q[1-4])\b", "", note_clean)
            note_clean = re.sub(r"\b(TOP|LH|99LH|FH)\b", "", note_clean)
            note_clean = re.sub(r"\s+[-|]+\s+", " ", note_clean)
            note_clean = re.sub(r"\s{2,}", " ", note_clean).strip(" -|,")

            project = {
                "name": name,
                "slug": slugify(name),
                "status": status,
                "district": district,
                "region": region,
                "area": area,
                "developer": "TBC",
                "psf": psf,
                "launch": launch,
                "units": units,
                "tenure": "99-year leasehold",
                "note": note_clean if note_clean else None
            }

            projects.append(project)

    return projects


def generate_js(projects, output_path):
    """Write the JS data file."""
    js_lines = [
        "// AUTO-GENERATED by scripts/generate-launches.py",
        "// Source: ~/.claude/state/new-launches/watchlist.md",
        "// Re-run: python3 scripts/generate-launches.py",
        "",
        "const NEW_LAUNCHES = " + json.dumps(projects, indent=2, ensure_ascii=False) + ";",
        ""
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(js_lines))

    print(f"Generated {len(projects)} projects -> {output_path}")
    for p in projects:
        print(f"  [{p['status']:8}] {p['name']} ({p['district']} {p['region']})")


if __name__ == "__main__":
    projects = parse_watchlist(WATCHLIST_PATH)
    if not projects:
        print("No projects parsed. Check watchlist format.")
    else:
        generate_js(projects, OUTPUT_PATH)
        print(f"\nDone. {len(projects)} projects written to {OUTPUT_PATH}")
