#!/usr/bin/env python3
"""Review gate for site changes before they ship.

Checks every file the agents touched, against the rules they were given.
Exits nonzero if anything blocking is found.
"""
import json, os, re, subprocess, sys, html, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prose_rules import bad_hyphens, PARTIAL_DIRS

import argparse
_ap = argparse.ArgumentParser(description="Verify site changes before shipping.")
_ap.add_argument("--root", default=os.path.expanduser("~/crestbrick-consult"))
_ap.add_argument("--base", default="origin/main")
_a = _ap.parse_args()
WT, BASE = _a.root, _a.base

def sh(*a):
    return subprocess.run(a, cwd=WT, capture_output=True, text=True).stdout


def changed():
    out = sh("git", "diff", "--name-status", BASE)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    for f in sh("git", "ls-files", "--others", "--exclude-standard").splitlines():
        rows.append(("A", f))
    return rows


TITLE = re.compile(r"<title>(.*?)</title>", re.S)
DESC = re.compile(
    r'<meta\s+name=["\']description["\']\s+content=(["\'])(.*?)\1', re.S | re.I)
CANON = re.compile(
    r'<link\s+rel=["\']canonical["\']\s+href=(["\'])(.*?)\1', re.I)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
LDJSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
TAGS = re.compile(r"<[^>]+>")


def text_of(s):
    return html.unescape(TAGS.sub("", s)).strip()




blocking, warn, info = [], [], []
stats = collections.Counter()

rows = changed()
htmls = [(st, f) for st, f in rows if f.endswith(".html") and f.startswith("public/")
         and not f.startswith(PARTIAL_DIRS)]

for st, f in htmls:
    p = os.path.join(WT, f)
    if st == "D" or not os.path.exists(p):
        continue
    src = open(p, encoding="utf-8", errors="replace").read()
    stats["html_files"] += 1

    m = TITLE.search(src)
    if not m:
        blocking.append(f"{f}: no <title>")
    else:
        t = text_of(m.group(1))
        bad = bad_hyphens(t)
        if bad:
            blocking.append(f"{f}: hyphen in title {bad}: {t!r}")
        if len(t) > 75:
            warn.append(f"{f}: title {len(t)} chars, will truncate: {t!r}")
        if len(t) < 20:
            warn.append(f"{f}: title suspiciously short ({len(t)}): {t!r}")
        stats["titles"] += 1

    m = DESC.search(src)
    if not m:
        warn.append(f"{f}: no meta description")
    else:
        dsc = text_of(m.group(2))
        bad = bad_hyphens(dsc)
        if bad:
            blocking.append(f"{f}: hyphen in description {bad}: {dsc!r}")
        if not (110 <= len(dsc) <= 175):
            warn.append(f"{f}: description {len(dsc)} chars (want 140 to 158)")
        stats["descs"] += 1

    if not H1.search(src):
        warn.append(f"{f}: no <h1>")

    c = CANON.search(src)
    if not c:
        warn.append(f"{f}: no canonical")
    else:
        want = "https://winfredquek.com/" + re.sub(r"^public/", "", f).replace(".html", "")
        got = c.group(2).rstrip("/")
        if got.rstrip("/") != want.rstrip("/") and not got.endswith("/"):
            info.append(f"{f}: canonical {got} (expected {want})")

    for blk in LDJSON.findall(src):
        try:
            json.loads(blk)
            stats["ldjson_ok"] += 1
        except Exception as e:
            blocking.append(f"{f}: invalid JSON-LD: {e}")

    # FAQPage schema must match visible copy
    for blk in LDJSON.findall(src):
        try:
            data = json.loads(blk)
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if isinstance(node, dict) and node.get("@type") == "FAQPage":
                # Strip script and style first. Without this the question text
                # matches against its own JSON-LD block and every check passes.
                visible = re.sub(r"<script.*?</script>|<style.*?</style>", " ",
                                 src, flags=re.S | re.I)
                body = re.sub(r"\s+", " ", text_of(visible)).lower()
                # A mismatch that already existed on the base branch is an
                # inherited defect, not a regression: warn, do not block, or a
                # site wide prose change can never ship past 176 old problems.
                prior = sh("git", "show", f"{BASE}:{f}")
                prior_body = ""
                if prior:
                    prior_body = re.sub(
                        r"\s+", " ",
                        text_of(re.sub(r"<script.*?</script>|<style.*?</style>",
                                       " ", prior, flags=re.S | re.I))).lower()
                for qa in node.get("mainEntity", []) or []:
                    q = re.sub(r"\s+", " ", text_of(str(qa.get("name", "")))).strip()
                    if not q or q.lower()[:45] in body:
                        continue
                    inherited = prior_body and q.lower()[:45] not in prior_body
                    msg = f"{f}: FAQ schema question not in visible page: {q[:60]!r}"
                    (warn if inherited else blocking).append(
                        msg + (" [pre-existing]" if inherited else " [NEW]"))

# vercel.json must still parse, and redirects must not point at deleted-and-missing targets
vj = os.path.join(WT, "vercel.json")
if any(f == "vercel.json" for _, f in rows):
    try:
        cfg = json.load(open(vj))
        stats["redirects"] = len(cfg.get("redirects", []))
        dests = {r.get("destination") for r in cfg.get("redirects", [])}
        for d in dests:
            if not d or not d.startswith("/") or d.startswith("//"):
                continue
            cand = os.path.join(WT, "public", d.lstrip("/"))
            if not (os.path.exists(cand + ".html") or os.path.exists(cand)
                    or os.path.exists(os.path.join(cand, "index.html"))):
                blocking.append(f"vercel.json: redirect target does not exist: {d}")
    except Exception as e:
        blocking.append(f"vercel.json does not parse: {e}")

# deleted pages must have a redirect, and must not still be linked
deleted = [f for st, f in rows if st == "D" and f.endswith(".html")]
if deleted:
    try:
        cfg = json.load(open(vj))
        srcs = {r.get("source", "").rstrip("/") for r in cfg.get("redirects", [])}
    except Exception:
        srcs = set()
    for f in deleted:
        slug = "/" + re.sub(r"^public/", "", f).replace(".html", "")
        if slug.rstrip("/") not in srcs:
            blocking.append(f"deleted {f} with no redirect for {slug}")
        hits = sh("grep", "-rl", "--include=*.html", "--include=*.json",
                  "--include=*.mjs", "--include=*.py", slug.lstrip("/"), "public", "scripts")
        live = [h for h in hits.splitlines() if h and not h.endswith(f)]
        if live:
            warn.append(f"deleted {slug} still referenced in {len(live)} file(s): {live[:4]}")

print(f"== traffic wave verification ==")
print(f"changed files: {len(rows)}  html: {stats['html_files']}  "
      f"titles: {stats['titles']}  descriptions: {stats['descs']}  "
      f"ld+json blocks ok: {stats['ldjson_ok']}  deleted: {len(deleted)}")
for label, items in (("BLOCKING", blocking), ("WARN", warn), ("INFO", info)):
    print(f"\n--- {label} ({len(items)}) ---")
    for i in items[:60]:
        print(" ", i)
    if len(items) > 60:
        print(f"  ... and {len(items) - 60} more")

sys.exit(1 if blocking else 0)
