#!/usr/bin/env python3
"""Generate public/sitemap-images.xml (Google image sitemap namespace).

Scans every indexable HTML page under public/ (guides/, tools/, answers/
included — read only, this script does not touch those pages) for local
<img> elements, resolves each src to a real file under public/, and emits
one <url> block per page with an <image:image> entry per qualifying image.

Skips: noindex pages (noindex string in first 4KB), external/data: image
srcs, and small icon/logo/favicon assets (<5KB on disk, or "logo"/"icon" in
the filename).

Special case: listings.html renders its cards client-side from
listings.json, so no <img> tags exist in the static markup. The listing
photos that DO live on disk under public/img/listings/ are mapped to the
/listings page URL directly (one canonical URL per photo, preferring .jpg,
then .webp, then .avif).

Run: python3 scripts/gen-image-sitemap.py
"""
import os
import re
import sys
from html.parser import HTMLParser
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_ROOT = os.path.join(REPO_ROOT, "public")
OUT_PATH = os.path.join(PUBLIC_ROOT, "sitemap-images.xml")
BASE_URL = "https://winfredquek.com"

MIN_IMAGE_BYTES = 5 * 1024
SKIP_NAME_PATTERN = re.compile(r"logo|icon", re.I)


# ---------------------------------------------------------------------------
# URL derivation (matches vercel.json cleanUrls: true, trailingSlash: false)
# ---------------------------------------------------------------------------

def page_url_for(html_path):
    rel = os.path.relpath(html_path, PUBLIC_ROOT).replace(os.sep, "/")
    if rel == "index.html":
        return BASE_URL + "/"
    if rel.endswith("/index.html"):
        return BASE_URL + "/" + rel[: -len("/index.html")]
    if rel.endswith(".html"):
        return BASE_URL + "/" + rel[: -len(".html")]
    return BASE_URL + "/" + rel


def image_url_for(local_path):
    rel = os.path.relpath(local_path, PUBLIC_ROOT).replace(os.sep, "/")
    return BASE_URL + "/" + rel


# ---------------------------------------------------------------------------
# <img src> extraction, ignoring <script>/<style> and HTML comments
# ---------------------------------------------------------------------------

class ImgSrcFinder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.skip_depth = 0
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        if tag_l in ("script", "style"):
            self.skip_depth += 1
        if tag_l == "img" and self.skip_depth == 0:
            for k, v in attrs:
                if k.lower() == "src" and v:
                    self.srcs.append(v)
                    break

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style"):
            self.skip_depth = max(0, self.skip_depth - 1)


def resolve_local_image(src, html_path):
    clean = src.split("?", 1)[0].split("#", 1)[0]
    if not clean or clean.startswith(("http://", "https://", "data:", "//")):
        return None
    if clean.startswith("/"):
        target = os.path.join(PUBLIC_ROOT, clean.lstrip("/"))
    else:
        target = os.path.normpath(os.path.join(os.path.dirname(html_path), clean))
    target = os.path.normpath(target)
    if not target.startswith(PUBLIC_ROOT):
        return None  # path traversal guard
    if os.path.isfile(target):
        return target
    return None


def qualifies(local_path):
    basename = os.path.basename(local_path)
    if SKIP_NAME_PATTERN.search(basename):
        return False
    try:
        if os.path.getsize(local_path) < MIN_IMAGE_BYTES:
            return False
    except OSError:
        return False
    return True


def is_indexable(html_path):
    try:
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
    except OSError:
        return False
    return "noindex" not in head.lower()


def iter_html_files():
    for root, dirs, files in os.walk(PUBLIC_ROOT):
        for fn in files:
            if fn.endswith(".html"):
                yield os.path.join(root, fn)


# ---------------------------------------------------------------------------
# Special case: listings.html photo gallery lives under public/img/listings/
# but is rendered client-side from listings.json (no static <img> tags).
# ---------------------------------------------------------------------------

def listings_special_case():
    listings_dir = os.path.join(PUBLIC_ROOT, "img", "listings")
    if not os.path.isdir(listings_dir):
        return []
    by_stem = {}
    for fn in os.listdir(listings_dir):
        path = os.path.join(listings_dir, fn)
        if not os.path.isfile(path):
            continue
        stem, ext = os.path.splitext(fn)
        ext = ext.lower().lstrip(".")
        by_stem.setdefault(stem, {})[ext] = path
    chosen = []
    for stem, variants in sorted(by_stem.items()):
        for ext in ("jpg", "jpeg", "webp", "avif"):
            if ext in variants and qualifies(variants[ext]):
                chosen.append(variants[ext])
                break
    return chosen


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pages = {}  # page_url -> list of image_url (order-preserving, deduped)
    stats = {"files_scanned": 0, "skipped_noindex": 0, "images_skipped_small_or_icon": 0}

    for html_path in sorted(iter_html_files()):
        stats["files_scanned"] += 1
        if not is_indexable(html_path):
            stats["skipped_noindex"] += 1
            continue

        parser = ImgSrcFinder()
        try:
            with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            parser.feed(text)
        except Exception as e:
            print(f"  ! parse error {html_path}: {e}", file=sys.stderr)
            continue

        if not parser.srcs:
            continue

        page_url = page_url_for(html_path)
        seen = set()
        img_urls = []
        for src in parser.srcs:
            local_path = resolve_local_image(src, html_path)
            if not local_path:
                continue
            if not qualifies(local_path):
                stats["images_skipped_small_or_icon"] += 1
                continue
            img_url = image_url_for(local_path)
            if img_url not in seen:
                seen.add(img_url)
                img_urls.append(img_url)

        if img_urls:
            pages.setdefault(page_url, [])
            for u in img_urls:
                if u not in pages[page_url]:
                    pages[page_url].append(u)

    # --- listings.html special case ---
    listings_url = page_url_for(os.path.join(PUBLIC_ROOT, "listings.html"))
    listings_photos = listings_special_case()
    listings_photo_urls = [image_url_for(p) for p in listings_photos]
    if listings_photo_urls:
        existing = pages.setdefault(listings_url, [])
        for u in listings_photo_urls:
            if u not in existing:
                existing.append(u)

    # --- emit sitemap-images.xml ---
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">',
    ]
    total_images = 0
    for page_url in sorted(pages.keys()):
        img_urls = pages[page_url]
        if not img_urls:
            continue
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(page_url)}</loc>")
        for img_url in img_urls:
            lines.append("    <image:image>")
            lines.append(f"      <image:loc>{escape(img_url)}</image:loc>")
            lines.append("    </image:image>")
            total_images += 1
        lines.append("  </url>")
    lines.append("</urlset>")
    lines.append("")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("gen-image-sitemap.py run complete")
    print(f"  html files scanned: {stats['files_scanned']}")
    print(f"  skipped (noindex): {stats['skipped_noindex']}")
    print(f"  images skipped (small/icon/logo): {stats['images_skipped_small_or_icon']}")
    print(f"  pages with images: {len(pages)}")
    print(f"  total image entries: {total_images}")
    print(f"  listings.html photos included: {len(listings_photo_urls)}")
    print(f"  wrote: {OUT_PATH}")


if __name__ == "__main__":
    main()
