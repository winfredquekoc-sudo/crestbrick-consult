#!/usr/bin/env python3
"""Generate branded gradient thumbnails for insights articles."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap

OUT = Path("/Users/winfredquek/crestbrick-consult/public/img/insights")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1280, 800   # 16:10
BRONZE = (139, 111, 71)
INK    = (26, 26, 26)
INK_SOFT = (74, 74, 74)
CREAM  = (250, 248, 244)
HIGHLIGHT = (251, 246, 236)
RULE = (230, 224, 214)

# Try Fraunces / Inter; fallback to system serif/sans
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
                try:
                    return ImageFont.truetype(str(f), size)
                except Exception:
                    continue
    return ImageFont.load_default()

# macOS has Georgia (serif) and Helvetica (sans) in System fonts
FONT_TITLE = find_font(["Georgia Bold", "Georgia"], 64)
FONT_TITLE_SMALL = find_font(["Georgia Bold", "Georgia"], 52)
FONT_LABEL = find_font(["Helvetica Bold", "HelveticaNeue", "Arial Bold"], 22)
FONT_NUM = find_font(["Georgia Bold", "Georgia"], 280)
FONT_BRAND = find_font(["Helvetica Bold", "Arial Bold"], 18)

ARTICLES = [
    {
        "slug": "cooling-measures",
        "label": "POLICY · 2026",
        "title": "Reading the\nlatest cooling\nmeasures",
        "num": "01",
        "tone": "warm",
    },
    {
        "slug": "absd-explained",
        "label": "FUNDAMENTALS",
        "title": "ABSD\nexplained\n(properly)",
        "num": "02",
        "tone": "deep",
    },
    {
        "slug": "ownership-restructuring-math",
        "label": "STRUCTURING",
        "title": "The ownership\nrestructuring\nmath",
        "num": "03",
        "tone": "warm",
    },
    {
        "slug": "ura-data-quarterly",
        "label": "DATA",
        "title": "URA data,\nquarter by\nquarter",
        "num": "04",
        "tone": "cool",
    },
    {
        "slug": "district-trends",
        "label": "DISTRICT",
        "title": "District\ntrends to\nwatch",
        "num": "05",
        "tone": "deep",
    },
    {
        "slug": "new-launch-analysis",
        "label": "NEW LAUNCH",
        "title": "How I actually\nanalyze a\nnew launch",
        "num": "06",
        "tone": "warm",
    },
]

# Tone palettes (background gradient + accent)
TONE_PALETTES = {
    "warm":  {"bg_top": (250, 248, 244), "bg_bot": (240, 230, 213), "accent": BRONZE, "ink": INK},
    "deep":  {"bg_top": (38, 34, 27),    "bg_bot": (24, 22, 18),    "accent": (200, 170, 120), "ink": (245, 240, 225)},
    "cool":  {"bg_top": (244, 245, 246), "bg_bot": (220, 220, 215), "accent": BRONZE, "ink": INK},
}

def vertical_gradient(top, bot):
    img = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        c = (
            int(top[0] + (bot[0] - top[0]) * t),
            int(top[1] + (bot[1] - top[1]) * t),
            int(top[2] + (bot[2] - top[2]) * t),
        )
        draw.line([(0, y), (W, y)], fill=c)
    return img

def render(article):
    pal = TONE_PALETTES[article["tone"]]
    img = vertical_gradient(pal["bg_top"], pal["bg_bot"])
    d = ImageDraw.Draw(img)

    PAD = 64
    # Big number watermark (right side)
    num_color = (
        pal["accent"][0], pal["accent"][1], pal["accent"][2]
    )
    # Number with low opacity by drawing on overlay
    overlay = Image.new("RGBA", img.size, (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    bbox = od.textbbox((0,0), article["num"], font=FONT_NUM)
    nw = bbox[2] - bbox[0]
    nh = bbox[3] - bbox[1]
    od.text((W - PAD - nw, H - PAD - nh - 30), article["num"], font=FONT_NUM, fill=(*num_color, 50))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)

    # Top label
    d.text((PAD, PAD), article["label"], font=FONT_LABEL, fill=pal["accent"])
    # Bronze underline
    label_bbox = d.textbbox((PAD, PAD), article["label"], font=FONT_LABEL)
    line_y = label_bbox[3] + 10
    d.line([(PAD, line_y), (PAD + 50, line_y)], fill=pal["accent"], width=2)

    # Title
    title_y = PAD + 90
    title_lines = article["title"].split("\n")
    font_to_use = FONT_TITLE
    line_h = 78
    for i, line in enumerate(title_lines):
        # Some titles are long; switch to smaller
        if max(len(l) for l in title_lines) > 18:
            font_to_use = FONT_TITLE_SMALL
            line_h = 64
        d.text((PAD, title_y + i * line_h), line, font=font_to_use, fill=pal["ink"])

    # Bottom branding
    brand_y = H - PAD - 24
    d.text((PAD, brand_y), "CRESTBRICK · WINFRED QUEK", font=FONT_BRAND, fill=pal["accent"])

    out_path = OUT / f"{article['slug']}.jpg"
    img.save(out_path, "JPEG", quality=88, optimize=True)
    print(f"  → {out_path.name}  ({out_path.stat().st_size // 1024} KB)")

print(f"Rendering {len(ARTICLES)} thumbnails to {OUT}/")
for a in ARTICLES:
    render(a)
print("Done.")
