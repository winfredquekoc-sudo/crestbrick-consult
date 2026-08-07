#!/usr/bin/env python3
"""Remove hyphens from VISIBLE prose across the site.

Visible prose only. Never touches: script/style blocks, HTML comments, tag
internals (so class names, ids, hrefs, srcs, data attributes and CSS custom
properties are all safe), or JSON-LD. Attribute values that ARE user visible
(title, meta description, og:*, alt) are handled separately and deliberately.

Default is a dry run. Pass --apply to write.
"""
import re, sys, os, html, collections, json

ROOT = os.path.expanduser("~/crestbrick-consult/public")
for _i, _v in enumerate(sys.argv):
    if _v == "--root" and _i + 1 < len(sys.argv):
        ROOT = os.path.join(sys.argv[_i + 1], "public")
APPLY = "--apply" in sys.argv

# Hyphens that must survive: they are proper names, technical terms, or the
# hyphen carries meaning that a space would destroy.
KEEP = {
    "e-mail", "wi-fi", "t-junction", "x-ray", "u-turn", "k-12",
    "co-op", "re-sign", "re-cover", "re-creation", "re-form", "re-lease",
    "vis-a-vis", "cul-de-sac", "so-called",
    # Singapore proper nouns. Getting these wrong is a factual error, not a
    # style choice: school names, official MRT line names, planning areas,
    # and the statutory name of the HDB scheme.
    "anglo-chinese", "thomson-east", "one-north", "kallang-bugis",
    "north-south", "east-west", "north-east", "bishan-ang",
    "build-to-order", "india-singapore", "marine-parade",
    "mon-sat", "mon-fri", "sat-sun", "tues-thurs",
    "newton-novena", "farrer-holland", "queenstown-redhill",
}

# Prefixes where replacing the hyphen with a space breaks the grammar.
# "self-employed" must not become "self employed".
KEEP_PREFIX = {
    "non", "self", "co", "ex", "anti", "semi", "quasi", "pseudo",
    "inter", "intra", "ultra", "counter", "sub", "vice", "all",
}

# Compounds that read better closed up than spaced.
JOIN_EXTRA = {
    "by-laws": "bylaws", "by-law": "bylaw", "add-ons": "addons",
    "add-on": "addon", "on-going": "ongoing", "under-writing": "underwriting",
}
# Joined instead of spaced: the space form would be wrong or ugly.
JOIN = {
    "e-application": "eApplication", "e-service": "eService",
    "e-services": "eServices", "e-map": "eMap", "e-appointment": "eAppointment",
    "co-ordinate": "coordinate", "co-ordinated": "coordinated",
    "co-operate": "cooperate", "co-operation": "cooperation",
    "pre-empt": "preempt", "re-enter": "reenter",
}

TOKEN = re.compile(r"(?<![\w/#.-])([A-Za-z]+(?:-[A-Za-z]+)+)(?![\w/-])")
NUMHYPH = re.compile(r"(?<![\w/#.-])(\d+(?:\.\d+)?)-(?=[A-Za-z])")
# A dash between two numbers is a RANGE and must read "to", never a comma.
# Covers $1.2m-$1.6m, 18-36 months, 35%-40%, 2.5–3.5.
RANGE_NUM = re.compile(
    r"(?<![\w/#-])((?:S?\$)?\d[\d,]*(?:\.\d+)?\s*(?:%|m|k|bn|psf|sqft|sqm)?)"
    r"\s*(?:-|–|&ndash;|&#8211;)\s*"
    r"((?:S?\$)?\d[\d,]*(?:\.\d+)?\s*(?:%|m|k|bn|psf|sqft|sqm)?)(?![\w/-])",
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
        if low in JOIN_EXTRA:
            counts["joined"] += 1
            examples["joined"].add(f"{w} -> {JOIN_EXTRA[low]}")
            return JOIN_EXTRA[low]
        if low in JOIN:
            counts["joined"] += 1
            examples["joined"].add(f"{w} -> {JOIN[low]}")
            return JOIN[low]
        new = w.replace("-", " ")
        counts["spaced"] += 1
        examples["spaced"].add(f"{w} -> {new}")
        return new

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
        counts["dashes"] += 1
        return ", " if m.group(0).strip() in ("—", "–", "--") else " "
    out = DASHES.sub(dash, out)
    return re.sub(r" {2,}", " ", out)


def process(src):
    """Walk the document, cleaning only visible regions."""
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
