#!/usr/bin/env python3
"""
Remove punctuation dashes from HTML text content across all public pages.

Removes:
  - " -- " (double-dash em-dash substitute, with surrounding spaces)
  - " — " (proper em-dash used as separator, with surrounding spaces)
  - " — " (HTML entity em-dash with surrounding spaces)
  - "  --  " (double-spaced variants)

Keeps:
  - CSS custom properties (--variable-name)
  - Hyphenated compound words (first-time, cash-to-close, non-refundable)
  - Numeric ranges (35-40%, 2.5-3.5%, 1%-2%)
  - HTML attributes, class names, IDs, URLs
  - JavaScript / <script> blocks
  - <style> blocks
  - HTML comments
  - Meta/title tags (handled separately — remove -- but keep hyphens)
"""

import re
import sys
from pathlib import Path

PUBLIC = Path(__file__).parent / "public"

# Patterns to remove from visible text content (between tags)
# Order matters — most specific first
TEXT_SUBS = [
    # Double-dash with surrounding spaces (various spacing)
    (re.compile(r'\s{1,2}--\s{1,2}'),  ' '),
    # Em-dash (Unicode) with surrounding spaces
    (re.compile(r'\s*—\s*'),        ' '),
    # HTML entity em-dash with spaces
    (re.compile(r'\s*&mdash;\s*'),       ' '),
    (re.compile(r'\s*&#8212;\s*'),       ' '),
]

# Same patterns for attribute values (title, meta content, og:title etc.)
# but only for -- (not single hyphens)
ATTR_SUBS = [
    (re.compile(r'\s{1,2}--\s{1,2}'),  ' '),
    (re.compile(r'\s*—\s*'),        ' '),
    (re.compile(r'\s*&mdash;\s*'),       ' '),
    (re.compile(r'\s*&#8212;\s*'),       ' '),
]

def process_file(path: Path) -> bool:
    """Return True if file was modified."""
    try:
        original = path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        print(f"  SKIP {path.name}: read error {e}")
        return False

    result = []
    i = 0
    n = len(original)
    changed = False

    while i < n:
        # Skip <script> blocks verbatim
        if original[i:i+7].lower() == '<script':
            end = original.lower().find('</script>', i)
            if end == -1:
                result.append(original[i:])
                break
            result.append(original[i:end+9])
            i = end + 9
            continue

        # Skip <style> blocks verbatim
        if original[i:i+6].lower() == '<style':
            end = original.lower().find('</style>', i)
            if end == -1:
                result.append(original[i:])
                break
            result.append(original[i:end+8])
            i = end + 8
            continue

        # Skip HTML comments verbatim
        if original[i:i+4] == '<!--':
            end = original.find('-->', i)
            if end == -1:
                result.append(original[i:])
                break
            result.append(original[i:end+3])
            i = end + 3
            continue

        # Handle HTML tags — process title/content/og attributes, skip the rest
        if original[i] == '<':
            end = original.find('>', i)
            if end == -1:
                result.append(original[i:])
                break
            tag_text = original[i:end+1]
            # Process content/title/og: meta attributes only
            # Replace -- in attribute values for title, content, og:title, og:description
            def replace_attr_dashes(m):
                attr_val = m.group(0)
                for pat, repl in ATTR_SUBS:
                    new_val = pat.sub(repl, attr_val)
                    if new_val != attr_val:
                        nonlocal changed
                        changed = True
                        attr_val = new_val
                return attr_val

            new_tag = re.sub(
                r'(?:content|title)="[^"]*"',
                replace_attr_dashes,
                tag_text,
                flags=re.IGNORECASE
            )
            result.append(new_tag)
            i = end + 1
            continue

        # Text node — find next tag boundary
        end = original.find('<', i)
        if end == -1:
            text = original[i:]
            for pat, repl in TEXT_SUBS:
                new_text = pat.sub(repl, text)
                if new_text != text:
                    changed = True
                    text = new_text
            result.append(text)
            break

        text = original[i:end]
        for pat, repl in TEXT_SUBS:
            new_text = pat.sub(repl, text)
            if new_text != text:
                changed = True
                text = new_text
        result.append(text)
        i = end

    if changed:
        path.write_text(''.join(result), encoding='utf-8')
    return changed

def main():
    html_files = list(PUBLIC.rglob('*.html'))
    modified = 0
    skipped_names = {'404.html', 'privacy.html', 'unsubscribe.html'}

    for f in sorted(html_files):
        if f.name in skipped_names:
            continue
        if process_file(f):
            print(f"  updated: {f.relative_to(PUBLIC.parent)}")
            modified += 1

    print(f"\nDone. {modified}/{len(html_files)} files updated.")

if __name__ == '__main__':
    main()
