#!/usr/bin/env python3
"""Report and roll the year stamped into page titles and headings.

248 pages carry a year in the <title> or <h1>. On 1 January every one of them
reads stale, which costs clicks on exactly the queries where freshness matters.

Rolling every "2026" to "2027" would be wrong: some pages are genuinely about a
specific year (cooling measure histories, "Budget 2026", MOP cohort pages) and
rewriting those makes them lie. So pages are split:

  evergreen  - the year is decorative. An ABSD guide is about the current rules,
               and the stamp is there for search appeal. Safe to roll.
  anchored   - the body cites several distinct years, or says things like
               "as at", so the year is load bearing. Reported, never rolled.

Only visible text is touched: <title>, <h1>, meta description, og:title and
twitter:title. Never the URL, slug, canonical, or any date in JSON-LD, because
changing those breaks links and lies about publication dates.

Usage:
  year-rollover.py                          report only
  year-rollover.py --from 2026 --to 2027    dry run of the roll
  year-rollover.py --from 2026 --to 2027 --apply
"""
import os
import re
import sys

FIELDS = [
    ("title", re.compile(r'(<title[^>]*>)(.*?)(</title>)', re.S | re.I)),
    ("h1", re.compile(r'(<h1[^>]*>)(.*?)(</h1>)', re.S | re.I)),
    ("meta description", re.compile(
        r'(<meta[^>]+name=["\']description["\'][^>]+content=["\'])([^"\']*)(["\'])', re.I)),
    ("og:title", re.compile(
        r'(<meta[^>]+property=["\']og:title["\'][^>]+content=["\'])([^"\']*)(["\'])', re.I)),
    ("twitter:title", re.compile(
        r'(<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\'])([^"\']*)(["\'])', re.I)),
]

YEAR_RX = re.compile(r'\b20(?:2[0-9]|3[0-9])\b')
# Phrases that make a year a statement of fact rather than a freshness stamp.
ANCHOR_RX = re.compile(
    r'\bas at\b|\bas of\b|\bhistory\b|\btimeline\b|\bbudget\b|\bcohort\b'
    r'|\bannounced in\b|\bintroduced in\b|\bsince\b', re.I)


def visible_text(html):
    stripped = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S | re.I)
    return re.sub(r'<[^>]+>', ' ', stripped)


def classify(html, year):
    """Return 'evergreen' or 'anchored'."""
    body = visible_text(html)
    years = set(YEAR_RX.findall(body))
    if len(years) > 3:
        return "anchored"
    if ANCHOR_RX.search(body):
        return "anchored"
    return "evergreen"


def scan(root, year=None):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
        for fn in filenames:
            if not fn.endswith(".html"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            stamped = []
            for name, rx in FIELDS[:2]:          # only title/h1 decide inclusion
                m = rx.search(t)
                if m and YEAR_RX.search(m.group(2)):
                    stamped.append(name)
            if not stamped:
                continue
            if year and year not in YEAR_RX.findall(visible_text(t)):
                continue
            found.append((p, stamped, classify(t, year), t))
    return found


def roll(text, old, new):
    """Replace old->new inside visible title-ish fields only."""
    out, n = text, 0
    for _name, rx in FIELDS:
        def sub(m):
            nonlocal n
            if old not in m.group(2):
                return m.group(0)
            n += 1
            return m.group(1) + m.group(2).replace(old, new) + m.group(3)
        out = rx.sub(sub, out, count=1)
    return out, n


def main():
    argv = sys.argv[1:]
    apply = "--apply" in argv
    old = new = None
    for i, a in enumerate(argv):
        if a == "--from":
            old = argv[i + 1]
        if a == "--to":
            new = argv[i + 1]
    root = "public"

    found = scan(root, old)
    ever = [f for f in found if f[2] == "evergreen"]
    anch = [f for f in found if f[2] == "anchored"]
    print(f"  {len(found)} pages carry a year in <title> or <h1>")
    print(f"    evergreen (safe to roll): {len(ever)}")
    print(f"    anchored  (needs a human): {len(anch)}")

    if not (old and new):
        print("\n  report only. Pass --from YYYY --to YYYY to roll, then --apply.")
        print("\n  anchored examples, do not roll these blindly:")
        for p, _f, _c, _t in anch[:10]:
            print(f"    {p}")
        return 0

    changed = fields = 0
    for p, _f, cls, t in ever:
        out, n = roll(t, old, new)
        if n:
            changed += 1
            fields += n
            if apply:
                open(p, "w", encoding="utf-8").write(out)
    verb = "rolled" if apply else "would roll"
    print(f"\n  {verb} {old} -> {new} on {changed} evergreen page(s), {fields} field(s)")
    print(f"  left {len(anch)} anchored page(s) untouched")
    if not apply:
        print("  dry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
