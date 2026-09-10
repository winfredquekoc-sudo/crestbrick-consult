#!/usr/bin/env python3
"""CI-only wrapper around scripts/content-rulecheck.py's per-file rule check.

content-rulecheck.py only knows how to scan a whole directory (main() takes a root
dir and globs *.html in it); the gate.yml "content-qa" job needs to check just the
specific public/insights/*.html files that changed in a PR. This loads the real
checker module in place (so the two never drift) and calls its check() function
per changed file instead of re-globbing everything.

Usage: check_content_qa.py <file1.html> [<file2.html> ...]
       (called with no files means nothing changed -> nothing to check -> exit 0)
"""
import sys
import os
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULECHECK_PATH = os.path.join(ROOT, "scripts", "content-rulecheck.py")


def load_rulecheck():
    spec = importlib.util.spec_from_file_location("content_rulecheck", RULECHECK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    files = [f for f in sys.argv[1:] if f.strip()]
    if not files:
        print("check_content_qa: no changed public/insights/*.html files, nothing to check")
        return 0

    crc = load_rulecheck()
    # insights articles are the only ones expected to carry the Sources + dark theme
    # + dual CTA rules (see content-rulecheck.py main()'s own root-name heuristic)
    crc.EXPECT_SOURCES = True
    crc.EXPECT_DARK = True

    bad = {}
    for f in files:
        if not os.path.isfile(f):
            print(f"  (skipping {f}, not present at this ref — likely a deletion)")
            continue
        defects = crc.check(f)
        if defects:
            bad[f] = defects

    if bad:
        print(f"content QA FAILED on {len(bad)} of {len(files)} changed file(s):")
        for f, defects in bad.items():
            print(f"  {f}")
            for d in defects:
                print(f"    {d}")
        return 1

    print(f"content QA passed on {len(files)} changed article(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
