#!/usr/bin/env python3
"""Insert a FAQPage JSON-LD block into pages with visible FAQ pairs but no schema.

Imports faq-schema-parity.py (hyphenated filename, so importlib.util rather
than a normal import) and reuses its own extract_visible_pairs/build_entries
so the generator can never disagree with the checker about what a "pair" is.

Placement and formatting are not invented here -- they are copied from the
44 insights pages that already carry a same-shaped block (git blame: prior
publish passes), matched by which of two known head templates the file
uses. A file matching neither template is reported as an anomaly, never
guessed at.

  <path> [<path> ...]  explicit files (repo-relative or absolute)
  --files-list PATH    file of paths, one per line (repeatable)
  --root DIR           repo root for relative paths (default: parent of scripts/)
  --json PATH          write a machine readable run report
"""
import argparse, collections, importlib.util, json, os, sys
from pathlib import Path

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_parity():
    spec = importlib.util.spec_from_file_location(
        "faq_schema_parity", Path(__file__).with_name("faq-schema-parity.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


parity = _load_parity()

# The only two head templates observed across the 68 NO_SCHEMA/61 twin
# targets. LIVE already has a manifest link right after _nav.js; TWIN (a
# pre-SEO-wave staged template) runs straight into </head> with no
# separating whitespace at all -- each gets the suffix that matches its own
# existing tail style, never a newline invented out of nothing.
ANCHOR_LIVE = '<script defer src="/_nav.js"></script>  <link rel="manifest"'
ANCHOR_TWIN = '<script defer src="/_nav.js"></script></head>'
_LIVE_PREFIX = '<script defer src="/_nav.js"></script>  '
_TWIN_PREFIX = '<script defer src="/_nav.js"></script>'


def locate_insertion(src):
    """(pos, suffix) right after the recognized anchor, or (None, None)."""
    if src.count(ANCHOR_LIVE) == 1 and ANCHOR_TWIN not in src:
        return src.index(ANCHOR_LIVE) + len(_LIVE_PREFIX), "\n  "
    if src.count(ANCHOR_TWIN) == 1 and ANCHOR_LIVE not in src:
        return src.index(ANCHOR_TWIN) + len(_TWIN_PREFIX), ""
    return None, None


def build_block(entries):
    faq = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entries}
    body = json.dumps(faq, separators=(",", ":"), ensure_ascii=False)
    if "</script" in body.lower():
        body = body.replace("/", "\\/")  # never let answer text close the element early
    return '<script type="application/ld+json">\n  ' + body + '\n  </script><!-- faq-geo -->'


def process(path, display):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        src = f.read()

    if "FAQPage" in src:
        return {"path": display, "status": "SKIPPED_HAS_SCHEMA"}

    visible_pairs = parity.extract_visible_pairs(src)
    if not visible_pairs:
        return {"path": display, "status": "ANOMALY_NO_PAIRS"}

    pos, suffix = locate_insertion(src)
    if pos is None:
        return {"path": display, "status": "ANOMALY_NO_ANCHOR"}

    entries = parity.build_entries(visible_pairs, [], [])
    block = build_block(entries) + suffix
    new_src = src[:pos] + block + src[pos:]

    stripped = new_src[:pos] + new_src[pos + len(block):]
    assert stripped == src, "byte drift outside the inserted block -- refusing to write"

    pairs2, _, _, bad2, nblocks2 = parity.extract_schema(new_src)
    assert not bad2 and nblocks2 == 1 and len(pairs2) == len(entries), \
        "post-build re-extraction mismatch -- refusing to write"

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_src)
    return {"path": display, "status": "WRITTEN", "pairs": len(entries)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--files-list", action="append", default=[])
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--json", dest="json_path", default=None)
    a = ap.parse_args()

    files = list(a.paths)
    for lp in a.files_list:
        files += [l.strip() for l in Path(lp).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not files:
        ap.error("no input files (pass paths and/or --files-list)")

    results = []
    for rel in files:
        abs_path = rel if os.path.isabs(rel) else os.path.join(a.root, rel)
        try:
            r = process(abs_path, rel)
        except Exception as e:
            r = {"path": rel, "status": "ERROR", "detail": repr(e)}
        results.append(r)
        print(f"{r['status']:22s} {rel}")

    counts = collections.Counter(r["status"] for r in results)
    print("\n==== summary ====")
    for k, v in counts.most_common():
        print(f"  {v:4d}  {k}")

    if a.json_path:
        Path(a.json_path).write_text(json.dumps(results, indent=1), encoding="utf-8")

    failing = counts["ERROR"] + counts["ANOMALY_NO_PAIRS"] + counts["ANOMALY_NO_ANCHOR"]
    sys.exit(1 if failing else 0)


if __name__ == "__main__":
    main()
