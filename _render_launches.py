#!/usr/bin/env python3
"""Generate branded gradient thumbnails for new launch cards."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path("/Users/winfredquek/crestbrick-consult/public/img/launches")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1280, 800
BRONZE = (139, 111, 71)

def find_font(names, size):
    candidates = [
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
    ]
    for cand in candidates:
        if not cand.exists(): continue
        for f in cand.rglob("*"):
            if f.is_file() and any(n.lower() in f.name.lower() for n in names) and f.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                try: return ImageFont.truetype(str(f), size)
                except: continue
    return ImageFont.load_default()

FONT_TITLE = find_font(["Georgia Bold", "Georgia"], 60)
FONT_SUB = find_font(["Georgia", "GeorgiaItalic"], 28)
FONT_LABEL = find_font(["Helvetica Bold", "Arial Bold"], 20)
FONT_SPEC = find_font(["Helvetica", "Arial"], 22)
FONT_BRAND = find_font(["Helvetica Bold", "Arial Bold"], 16)

import json, textwrap as tw
LAUNCHES_DATA = json.loads(Path("/Users/winfredquek/crestbrick-consult/public/launches.json").read_text())["launches"]

# Tone rotation by region for visual variety
REGION_TONE = {"CCR": "deep", "RCR": "warm", "OCR": "cool", "EC": "warm"}

def title_line(name):
    # Word-wrap title to 2-3 lines, max ~14 chars
    words = name.split()
    out, line = [], ""
    for w in words:
        if len(line) + len(w) + 1 > 16 and line:
            out.append(line.strip()); line = w
        else:
            line = (line + " " + w).strip()
    if line: out.append(line)
    return "\n".join(out[:3])

LAUNCHES = []
for item in LAUNCHES_DATA:
    spec_parts = []
    if item.get("units") and str(item["units"]) != "TBC": spec_parts.append(f"{item['units']} units")
    if item.get("tenure") and item["tenure"] != "TBC": spec_parts.append(item["tenure"])
    if item.get("developer"): spec_parts.append(item["developer"])
    line1 = " · ".join(spec_parts)
    line2_parts = []
    if item.get("psf") and item["psf"] != "TBC": line2_parts.append(item["psf"])
    if item.get("launch"): line2_parts.append(item["launch"])
    line2 = " · ".join(line2_parts)
    LAUNCHES.append({
        "slug": item["slug"],
        "label": f"{item.get('district','')} · {item.get('area','').upper()} · {item.get('region','')}",
        "title": title_line(item["name"]),
        "spec": f"{line1}\n{line2}",
        "tone": REGION_TONE.get(item.get("region",""), "warm"),
    })

TONE = {
    "warm": {"top": (250, 248, 244), "bot": (235, 223, 200), "ink": (26, 26, 26), "accent": BRONZE, "spec": (74, 74, 74)},
    "deep": {"top": (32, 28, 22),    "bot": (18, 16, 14),    "ink": (245, 240, 225), "accent": (210, 175, 120), "spec": (200, 195, 180)},
    "cool": {"top": (240, 242, 244), "bot": (215, 218, 215), "ink": (26, 26, 26), "accent": BRONZE, "spec": (74, 74, 74)},
}

def vgrad(t, b):
    img = Image.new("RGB", (W, H), t)
    d = ImageDraw.Draw(img)
    for y in range(H):
        f = y / (H - 1)
        c = tuple(int(t[i] + (b[i] - t[i]) * f) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    return img

def render(item):
    pal = TONE[item["tone"]]
    img = vgrad(pal["top"], pal["bot"])
    d = ImageDraw.Draw(img)
    PAD = 64

    # Top label
    d.text((PAD, PAD), item["label"], font=FONT_LABEL, fill=pal["accent"])
    bb = d.textbbox((PAD, PAD), item["label"], font=FONT_LABEL)
    d.line([(PAD, bb[3] + 10), (PAD + 60, bb[3] + 10)], fill=pal["accent"], width=2)

    # Title (multi-line)
    title_y = PAD + 90
    title_lines = item["title"].split("\n")
    line_h = 70
    for i, line in enumerate(title_lines):
        d.text((PAD, title_y + i * line_h), line, font=FONT_TITLE, fill=pal["ink"])

    # Spec block (bottom area)
    spec_y = H - PAD - 90
    for i, line in enumerate(item["spec"].split("\n")):
        d.text((PAD, spec_y + i * 32), line, font=FONT_SPEC, fill=pal["spec"])

    # Branding (corner)
    d.text((W - PAD - 280, H - PAD - 24), "CRESTBRICK · WINFRED QUEK", font=FONT_BRAND, fill=pal["accent"])

    out = OUT / f"{item['slug']}.jpg"
    img.save(out, "JPEG", quality=88, optimize=True)
    print(f"  → {out.name} ({out.stat().st_size // 1024} KB)")

print(f"Rendering {len(LAUNCHES)} launch cards to {OUT}/")
for l in LAUNCHES:
    render(l)
print("Done.")
