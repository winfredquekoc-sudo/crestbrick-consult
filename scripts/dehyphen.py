#!/usr/bin/env python3
"""Remove hyphens from VISIBLE prose across the site.

Visible prose only. Never touches: script/style blocks, HTML comments, tag
internals (so class names, ids, hrefs, srcs, data attributes and CSS custom
properties are all safe), or JSON-LD. Attribute values that ARE user visible
(title, meta description, og:*, alt) are handled separately and deliberately.

Default is a dry run. Pass --apply to write.
"""
import re, sys, os, html, collections, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prose_rules import KEEP, KEEP_PREFIX, JOIN, RANGE_NUM

ROOT = os.path.expanduser("~/crestbrick-consult/public")
for _i, _v in enumerate(sys.argv):
    if _v == "--root" and _i + 1 < len(sys.argv):
        ROOT = os.path.join(sys.argv[_i + 1], "public")
APPLY = "--apply" in sys.argv

# Alphanumeric compounds, so post-2022, age-75 and 15-year are caught too.
# At least one side must contain a letter, so pure number ranges fall through
# to RANGE_NUM and become "to" rather than being spaced.
TOKEN = re.compile(
    r"(?<![\w#.-])((?=[\w-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)(?![\w-])")
NUMHYPH = re.compile(r"(?<![\w/#.-])(\d+(?:\.\d+)?)-(?=[A-Za-z])")
# Visible URLs inside prose must survive untouched, so mask them first.
URL_IN_PROSE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+|[\w.-]+\.(?:com|sg|org|net|gov|co)(?:/[^\s<>\"']*)?",
    re.I)
DASHES = re.compile(r"\s*(?:—|–|&mdash;|&ndash;|&#8212;|&#8211;|--)\s*")

SKIP_BLOCK = re.compile(
    r"(<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->)", re.S | re.I)
TAG = re.compile(r"(<[^>]+>)")

# user visible attributes we DO want cleaned
VIS_ATTR = re.compile(
    r'((?:title|content|alt|aria-label)\s*=\s*)(["\'])(.*?)\2', re.I | re.S)
# but only clean content= when it belongs to a visible meta
META_VISIBLE = re.compile(
    r'name\s*=\s*["\'](description|twitter:title|twitter:description)|'
    r'property\s*=\s*["\']og:(title|description|image:alt)', re.I)

counts = collections.Counter()
examples = collections.defaultdict(set)


def fix_text(s):
    def tok(m):
        w = m.group(1)
        low = w.lower()
        if low in KEEP:
            counts["kept_propernoun"] += 1
            examples["kept"].add(w)
            return w
        if low.split("-")[0] in KEEP_PREFIX:
            counts["kept_prefix"] += 1
            examples["kept"].add(w)
            return w
        if low in JOIN:
            counts["joined"] += 1
            examples["joined"].add(f"{w} -> {JOIN[low]}")
            return JOIN[low]
        new = w.replace("-", " ")
        counts["spaced"] += 1
        examples["spaced"].add(f"{w} -> {new}")
        return new

    # Park any visible URL so slugs keep their hyphens, restore it at the end.
    parked = []

    def park(m):
        parked.append(m.group(0))
        return f"\x00{len(parked) - 1}\x00"
    s = URL_IN_PROSE.sub(park, s)

    # Ranges first, before any dash handling can turn them into commas.
    def rng(m):
        counts["ranges"] += 1
        examples["ranges"].add(m.group(0).strip() + "  ->  "
                               + f"{m.group(1).strip()} to {m.group(2).strip()}")
        return f"{m.group(1).strip()} to {m.group(2).strip()}"
    s = RANGE_NUM.sub(rng, s)

    out = TOKEN.sub(tok, s)

    def num(m):
        counts["numeric"] += 1
        examples["numeric"].add(m.group(0))
        return m.group(1) + " "
    out = NUMHYPH.sub(num, out)

    def dash(m):
        # The entity forms and the literal characters mean exactly the same
        # thing to a reader, so they must produce the same output. Treating them
        # differently is what made page HTML (which used &mdash;) and JSON-LD
        # (which used a literal em dash) disagree on the very same sentence.
        counts["dashes"] += 1
        tok = m.group(0).strip()
        canonical = {"&mdash;": "—", "&#8212;": "—",
                     "&ndash;": "–", "&#8211;": "–"}.get(tok, tok)
        return ", " if canonical in ("—", "–", "--") else " "
    out = DASHES.sub(dash, out)
    out = re.sub(r" {2,}", " ", out)
    # Restore the parked URLs exactly as they were.
    return re.sub(r"\x00(\d+)\x00", lambda m: parked[int(m.group(1))], out)


LD_BLOCK = re.compile(r'(<script[^>]+ld\+json[^>]*>)(.*?)(</script>)', re.S | re.I)
# Schema fields a human actually reads: Google renders these in rich results, so
# they must follow the same prose rule and stay in sync with the visible page.
# Everything else (@type, @id, url, image, sameAs) is machine data, left alone.
LD_TEXT_KEYS = {"name", "text", "headline", "description", "articleBody",
                "alternateName", "caption", "abstract"}


def fix_ld(block):
    """Rewrite human readable strings inside one JSON-LD block.

    Replacement is done on the raw text using each value's JSON escaped form, so
    the block's original indentation and key order survive untouched.
    """
    try:
        data = json.loads(block)
    except Exception:
        return block  # never touch a block we cannot parse

    pairs = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in LD_TEXT_KEYS and isinstance(v, str):
                    new = fix_text(v)
                    if new != v:
                        pairs.append((v, new))
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(data)

    if not pairs:
        return block

    out = block
    for old, new in pairs:
        out = out.replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])
        out = out.replace(old, new)

    # The targeted replace preserves formatting but can miss a value whose raw
    # escaping differs from json.dumps (unicode escapes, escaped slashes). Silent
    # misses are the dangerous case: visible prose changes while schema does not,
    # so the two disagree. Detect that and fall back to a full reserialise.
    try:
        check = json.loads(out)
    except Exception:
        counts["ldjson_reverted"] += 1
        return block

    remaining = []

    def verify(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in LD_TEXT_KEYS and isinstance(v, str):
                    if fix_text(v) != v:
                        remaining.append(k)
                else:
                    verify(v)
        elif isinstance(o, list):
            for x in o:
                verify(x)
    verify(check)

    if remaining:
        def rewrite(o):
            if isinstance(o, dict):
                return {k: (fix_text(v) if k in LD_TEXT_KEYS and isinstance(v, str)
                            else rewrite(v)) for k, v in o.items()}
            if isinstance(o, list):
                return [rewrite(x) for x in o]
            return o
        out = json.dumps(rewrite(data), ensure_ascii=False, indent=2)
        counts["ldjson_reserialised"] += 1

    counts["ldjson_fields"] += len(pairs)
    return out


def process(src):
    """Walk the document, cleaning only visible regions."""
    src = LD_BLOCK.sub(lambda m: m.group(1) + fix_ld(m.group(2)) + m.group(3), src)
    pieces = SKIP_BLOCK.split(src)
    for i, chunk in enumerate(pieces):
        if i % 2:  # script/style/comment, untouched
            continue
        parts = TAG.split(chunk)
        for j, part in enumerate(parts):
            if j % 2:  # inside a tag
                if VIS_ATTR.search(part):
                    def attr(m):
                        pre, q, val = m.group(1), m.group(2), m.group(3)
                        if pre.lower().startswith("content") and not META_VISIBLE.search(part):
                            return m.group(0)
                        return f"{pre}{q}{fix_text(val)}{q}"
                    parts[j] = VIS_ATTR.sub(attr, part)
            else:      # visible text node
                if part.strip():
                    parts[j] = fix_text(part)
        pieces[i] = "".join(parts)
    return "".join(pieces)


files = []
for dirpath, _, names in os.walk(ROOT):
    for n in names:
        if n.endswith(".html"):
            files.append(os.path.join(dirpath, n))

changed = 0
for p in sorted(files):
    src = open(p, encoding="utf-8", errors="replace").read()
    out = process(src)
    if out != src:
        changed += 1
        if APPLY:
            open(p, "w", encoding="utf-8").write(out)

print(f"{'APPLIED' if APPLY else 'DRY RUN'}: {changed} of {len(files)} html files would change")
for k, v in counts.most_common():
    print(f"  {k:<10} {v}")
print("\n-- sample of compound rewrites (60 of {}) --".format(len(examples['spaced'])))
for e in sorted(examples["spaced"])[:60]:
    print("   ", e)
print("\n-- joined --")
for e in sorted(examples["joined"]):
    print("   ", e)
