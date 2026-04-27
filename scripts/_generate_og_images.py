#!/usr/bin/env python3
"""I90 — generate Open Graph images for every public/*.html page.

Two strategies:
  1. PIL fallback (always works) — text on a branded canvas.
  2. Higgsfield path (preferred) — write a CSV brief that visual-director consumes.

Output: public/img/og/<slug>.png

Usage:
  python3 scripts/_generate_og_images.py            # PIL fallback for missing slugs
  python3 scripts/_generate_og_images.py --brief    # write Higgsfield brief CSV only
"""
import sys
import json
import csv
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUB = ROOT / "public"
OG_DIR = PUB / "img" / "og"
OG_DIR.mkdir(parents=True, exist_ok=True)
BRIEF_CSV = ROOT / "design" / "og-brief.csv"
BRIEF_CSV.parent.mkdir(parents=True, exist_ok=True)


def extract_meta(html_path):
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    m_title = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    m_desc = re.search(r'<meta\s+name="description"\s+content="(.*?)"', text, re.I)
    return {
        "slug": html_path.stem,
        "title": (m_title.group(1).strip() if m_title else html_path.stem.replace("-", " ").title()),
        "description": (m_desc.group(1).strip() if m_desc else ""),
    }


def write_brief():
    pages = sorted(PUB.glob("*.html"))
    rows = [extract_meta(p) for p in pages]
    with BRIEF_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "title", "description", "og_path"])
        w.writeheader()
        for r in rows:
            r["og_path"] = f"/img/og/{r['slug']}.png"
            w.writerow(r)
    print(f"Wrote brief: {BRIEF_CSV} ({len(rows)} pages)")
    print("Next: feed this CSV to visual-director for Higgsfield Soul Quiet-Luxury renders, 1200x630.")


def pil_fallback():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("PIL not installed — pip install pillow. Skipping fallback rendering.")
        return
    pages = sorted(PUB.glob("*.html"))
    fonts_dir = ROOT / "design" / "fonts"
    title_font = None
    body_font = None
    for f in (fonts_dir.glob("*.ttf") if fonts_dir.exists() else []):
        if "Fraunces" in f.name and not title_font:
            title_font = ImageFont.truetype(str(f), 64)
        if "Inter" in f.name and not body_font:
            body_font = ImageFont.truetype(str(f), 28)
    title_font = title_font or ImageFont.load_default()
    body_font = body_font or ImageFont.load_default()

    rendered = 0
    for p in pages:
        slug = p.stem
        out = OG_DIR / f"{slug}.png"
        if out.exists():
            continue
        meta = extract_meta(p)
        img = Image.new("RGB", (1200, 630), (250, 246, 238))  # warm cream
        d = ImageDraw.Draw(img)
        # Brand stripe
        d.rectangle([(0, 0), (1200, 8)], fill=(38, 64, 50))  # deep forest accent
        # Title
        title = meta["title"][:80]
        d.text((80, 180), title, font=title_font, fill=(20, 20, 20))
        # Description
        desc = (meta["description"] or "")[:140]
        d.text((80, 380), desc, font=body_font, fill=(80, 80, 80))
        # Footer
        d.text((80, 540), "Winfred Quek · Crestbrick · CEA R073319H", font=body_font, fill=(120, 120, 120))
        img.save(out)
        rendered += 1
    print(f"PIL fallback rendered {rendered} images into {OG_DIR}")


if __name__ == "__main__":
    if "--brief" in sys.argv:
        write_brief()
    else:
        write_brief()
        pil_fallback()
