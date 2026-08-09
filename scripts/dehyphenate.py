#!/usr/bin/env python3
"""De-hyphenate visible prose across the staged insights corpus.

Only touches: HTML text nodes, <title> text, meta description/title content
attributes, and prose-valued strings inside application/ld+json.

Never touches: hrefs, src, class, id, style, any other attribute, CSS, JS,
JSON-LD url/@id/item/identifier fields, or anything inside <script>/<style>.

Usage: hyphen_fix.py <dir> [--apply]     (default is a dry run)
"""
import re, sys, json, html
from pathlib import Path

# --- allowlist: hyphens that STAY -------------------------------------------
KEEP_EXACT = {
    # place, line and precinct names
    "one-north", "thomson-east", "north-south", "east-west", "kallang-bugis",
    "north-east", "downtown-line",
    # official scheme / product names
    "build-to-order", "multi-generation", "half-housing", "e-stamping",
    "in-principle", "loan-to-value", "cash-on-cash", "s-reits",
    "anti-avoidance", "99-1",
    # standard compounds carried on Winfred's keep list
    "sub-sale", "en-bloc", "by-law", "by-laws", "semi-detached", "non-landed",
}
KEEP_PREFIX = ("co-", "sub-", "e-", "x-")

HYPHEN_TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")

stats = {"tokens": 0, "files": 0}


def keep(tok):
    low = tok.lower()
    if low in KEEP_EXACT:
        return True
    return any(low.startswith(p) for p in KEEP_PREFIX)


def dehyphenate(text):
    """Replace hyphens with spaces in non-allowlisted tokens."""
    def sub(m):
        tok = m.group(0)
        if keep(tok):
            return tok
        stats["tokens"] += 1
        return tok.replace("-", " ")

    return HYPHEN_TOKEN.sub(sub, text)


# --- JSON-LD ----------------------------------------------------------------
PROSE_KEYS = {
    "name", "text", "headline", "description", "articleBody", "abstract",
    "alternateName", "caption", "disambiguatingDescription",
}
SKIP_KEYS = {"url", "@id", "item", "identifier", "sameAs", "image", "logo",
             "@type", "@context", "contentUrl", "thumbnailUrl"}


def fix_ldjson(blob):
    """Rewrite prose fields in place, preserving the block's original formatting.

    Re-dumping the parsed JSON would reflow every block and churn the whole
    corpus, so collect the strings that actually change and splice those exact
    substrings back into the raw text instead.
    """
    try:
        data = json.loads(blob)
    except Exception:
        return blob  # unparseable: leave completely alone

    edits = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if key in SKIP_KEYS or key not in PROSE_KEYS:
                return
            new = dehyphenate(node)
            if new != node:
                edits.append((node, new))

    walk(data)
    for old, new in edits:
        # splice the JSON-encoded form so escaping stays intact
        enc_old, enc_new = json.dumps(old)[1:-1], json.dumps(new)[1:-1]
        blob = blob.replace(enc_old, enc_new)
    return blob


LDJSON_BLOCK = re.compile(
    r'(<script[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)', re.S | re.I
)
SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
META_CONTENT = re.compile(
    r'(<meta[^>]*(?:name|property)=["\'](?:description|og:description|twitter:description|og:title|twitter:title)["\'][^>]*content=["\'])([^"\']*)(["\'])',
    re.I,
)


def process(src):
    # 1. JSON-LD blocks: fix prose fields, then stash so step 3 cannot touch them
    ld_store = []

    def stash_ld(m):
        fixed = fix_ldjson(m.group(2))
        ld_store.append(m.group(1) + fixed + m.group(3))
        return f"\x00LD{len(ld_store)-1}\x00"

    src = LDJSON_BLOCK.sub(stash_ld, src)

    # 2. remaining <script>/<style>: stash untouched
    other_store = []

    def stash_other(m):
        other_store.append(m.group(0))
        return f"\x00SC{len(other_store)-1}\x00"

    src = SCRIPT_STYLE.sub(stash_other, src)

    # 3. meta content attributes (inside tags, so handle before text nodes)
    src = META_CONTENT.sub(lambda m: m.group(1) + dehyphenate(m.group(2)) + m.group(3), src)

    # 4. text nodes only: everything outside < ... >
    out, pos = [], 0
    for m in re.finditer(r"<[^>]*>", src):
        out.append(dehyphenate(src[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(dehyphenate(src[pos:]))
    src = "".join(out)

    # 5. restore
    src = re.sub(r"\x00SC(\d+)\x00", lambda m: other_store[int(m.group(1))], src)
    src = re.sub(r"\x00LD(\d+)\x00", lambda m: ld_store[int(m.group(1))], src)
    return src


def main():
    target = Path(sys.argv[1])
    apply = "--apply" in sys.argv
    files = sorted(target.rglob("*.html"))
    changed = []
    for f in files:
        src = f.read_text(encoding="utf-8")
        new = process(src)
        if new != src:
            changed.append(f)
            if apply:
                f.write_text(new, encoding="utf-8")
    stats["files"] = len(changed)
    print(f"{'APPLIED' if apply else 'DRY RUN'}: {len(changed)}/{len(files)} files, "
          f"{stats['tokens']} hyphen tokens rewritten")
    for f in changed[:15]:
        print("  ", f.name)


if __name__ == "__main__":
    main()
