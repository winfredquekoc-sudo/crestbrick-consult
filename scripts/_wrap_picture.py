#!/usr/bin/env python3
"""
Wrap <img src="...jpg|png"> with <picture><source avif><source webp><img></picture>.

- Skips imgs already inside a <picture> element.
- Skips imgs whose src points to a non-existent .avif file (i.e. we didn't convert it).
- Skips OG-style usage in <meta>, <link> (those are not <img> tags so naturally skipped).
- Preserves all original attributes including loading, fetchpriority, class, alt, width, height.
- Uses a regex-based approach but validates each output with html.parser before writing.
"""
import os, re, sys, json
from html.parser import HTMLParser

ROOT = '/Users/winfredquek/crestbrick-consult/public'

# Load list of images we converted
with open('/Users/winfredquek/crestbrick-consult/scripts/_avif-results.json') as f:
    results = json.load(f)
converted = set()
for r in results:
    if r.get('ok'):
        # store relative path under public/, normalised
        rel = os.path.relpath(r['file'], ROOT)
        converted.add('/' + rel.replace(os.sep, '/'))
# also add without the .jpg extension as key for quick lookup
print(f"Converted images: {len(converted)}")

IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
SRC_RE = re.compile(r'\bsrc="([^"]+)"', re.IGNORECASE)
PICTURE_OPEN_RE = re.compile(r'<picture\b', re.IGNORECASE)
PICTURE_CLOSE_RE = re.compile(r'</picture\s*>', re.IGNORECASE)

def is_inside_picture(text, pos):
    """Check if position pos is inside an open <picture>...</picture>."""
    # Find last <picture before pos and last </picture before pos
    before = text[:pos]
    last_open = -1
    for m in PICTURE_OPEN_RE.finditer(before):
        last_open = m.start()
    last_close = -1
    for m in PICTURE_CLOSE_RE.finditer(before):
        last_close = m.start()
    return last_open > last_close

def normalise_src(src):
    # strip query string for path lookup
    return src.split('?')[0].split('#')[0]

def process_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        original = f.read()
    text = original

    # We'll do replacements from end to start to keep positions stable
    matches = list(IMG_TAG_RE.finditer(text))
    edits = []  # list of (start, end, replacement)
    for m in matches:
        tag = m.group(0)
        src_m = SRC_RE.search(tag)
        if not src_m:
            continue
        src = src_m.group(1)
        norm = normalise_src(src)
        if norm not in converted:
            continue
        # Skip if already inside a <picture>
        if is_inside_picture(text, m.start()):
            continue
        # Build avif and webp paths (preserve query string of original)
        query = ''
        if '?' in src:
            query = '?' + src.split('?', 1)[1]
        base_no_ext = norm.rsplit('.', 1)[0]
        avif = base_no_ext + '.avif' + query
        webp = base_no_ext + '.webp' + query
        wrapped = f'<picture><source srcset="{avif}" type="image/avif"><source srcset="{webp}" type="image/webp">{tag}</picture>'
        edits.append((m.start(), m.end(), wrapped))

    if not edits:
        return False, 0

    # Apply edits in reverse order
    for start, end, repl in reversed(edits):
        text = text[:start] + repl + text[end:]

    # Validate with html.parser - ensure no exception
    try:
        HTMLParser().feed(text)
    except Exception as e:
        print(f"VALIDATION FAIL {path}: {e}", file=sys.stderr)
        return False, 0

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True, len(edits)

# Walk
total_files = 0
total_edits = 0
updated = 0
for d, ds, fs in os.walk(ROOT):
    for f in fs:
        if f.endswith('.html'):
            total_files += 1
            p = os.path.join(d, f)
            ok, n = process_file(p)
            if ok:
                updated += 1
                total_edits += n

print(f"HTML scanned: {total_files}")
print(f"HTML updated: {updated}")
print(f"img tags wrapped: {total_edits}")
