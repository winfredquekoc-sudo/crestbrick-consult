#!/usr/bin/env python3
"""Full standing-rule check over a corpus of article HTML.

Emits a per-file defect map so repairs are driven by current truth, not a
stale list. Run after any bulk transform.
"""
import re, sys, json, glob, os
from pathlib import Path

EXPECT_SOURCES = True
EXPECT_DARK = True
TAG = re.compile(r"<[^>]+>")
SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)

# Use the site's canonical hyphen rules rather than a competing local list,
# so this checker and scripts/dehyphen.py can never disagree.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prose_rules import KEEP as KEEP_EXACT, KEEP_PREFIX  # noqa: E402
HYPHEN_TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")


def visible_text(html):
    return TAG.sub(" ", SCRIPT_STYLE.sub(" ", html))


def keep(tok):
    low = tok.lower()
    if low in KEEP_EXACT:
        return True
    if re.fullmatch(r"[\d-]+", low):      # dates and pure number ranges
        return True
    head = low.split("-", 1)[0]
    return head in KEEP_PREFIX


def check(path):
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    vis = visible_text(html)
    d = []

    # 1. hyphens in visible prose
    # URLs printed as visible text (citations) legitimately carry hyphens
    vis_no_urls = re.sub(r"(?:https?://|www\.)?[\w.-]+\.(?:com|sg|org|net|gov|edu|io)\S*", " ", vis)
    hy = [t for t in HYPHEN_TOKEN.findall(vis_no_urls) if not keep(t)]
    if hy:
        d.append({"type": "HYPHEN", "n": len(hy), "sample": sorted(set(hy))[:8]})

    # 2. the word "audit" in customer facing copy. The rule is about not
    # branding Winfred's own service that way; describing IRAS's actual
    # enforcement activity is correct English and stays.
    aud = [m for m in re.finditer(r"\baudit\w*\b", vis, re.I)
           if not re.search(r"IRAS[^.]{0,40}$", vis[max(0, m.start() - 60):m.start()], re.I)]
    if aud:
        d.append({"type": "WORD_AUDIT", "n": len(aud)})

    # 3. a not-advice statement is present, in any of the wordings used across
    # the site (insights use a disclaimer-block, ebooks use a callout).
    low = html.lower()
    has_disclaimer = (
        "disclaimer" in low
        or "informational purposes only" in low
        or "general information" in low
        or re.search(r"not (?:legal|financial|investment|professional|tax|personalised)[^.]{0,80}advice", low)
    )
    if not has_disclaimer:
        d.append({"type": "NO_DISCLAIMER"})

    # 4. sources / references section. Only insights articles carry one; the
    # glossary and answers templates never had one, so requiring it there is
    # noise, not a defect.
    if EXPECT_SOURCES and not re.search(r"Sources\s*(&amp;|&|and)\s*References", html, re.I):
        d.append({"type": "NO_SOURCES"})

    # 5. JSON-LD validity
    badld = 0
    for m in re.finditer(r'type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            json.loads(m.group(1))
        except Exception:
            badld += 1
    if badld:
        d.append({"type": "BAD_JSONLD", "n": badld})

    # 6. stale 3 year SSD schedule (current is the 4 year 16/12/8/4)
    if re.search(r"12\s*/\s*8\s*/\s*4|within\s+3\s*(-|\s)?y(ea)?rs?\b.{0,40}SSD|SSD.{0,40}\b3\s*year", vis, re.I):
        current = (re.search(r"16\s*/\s*12\s*/\s*8\s*/\s*4", vis)
                   or re.search(r"16\s*percent.{0,60}year\s*1", vis, re.I | re.S)
                   or re.search(r"\b4\s*year\b.{0,40}(window|schedule)", vis, re.I))
        if not current:
            d.append({"type": "STALE_SSD"})

    # 7. year stamped title / h1 (these publish in 2027)
    t = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if t and re.search(r"\b20(2[5-9]|3\d)\b", t.group(1)):
        d.append({"type": "YEAR_IN_TITLE", "v": t.group(1).strip()[:70]})
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if h1 and re.search(r"\b20(2[5-9]|3\d)\b", TAG.sub("", h1.group(1))):
        d.append({"type": "YEAR_IN_H1", "v": TAG.sub("", h1.group(1)).strip()[:70]})

    # 8. CEA identifier present
    if "R073319H" not in html:
        d.append({"type": "NO_CEA"})

    # 9. light theme leakage. Ebooks are deliberately cream (#faf6ee) as print
    # documents, and decorative SVG strokes use light colours on purpose, so
    # only flag a light colour used as an actual page background.
    # A light body background inside @media print is correct, so drop print
    # blocks before testing.
    screen_css = re.sub(r"@media\s+print\s*\{(?:[^{}]|\{[^{}]*\})*\}", " ", html, flags=re.I)
    if EXPECT_DARK and re.search(r"body\s*\{[^}]*background:\s*#(?:f{3,6}|faf8f4|fbf6ec|faf6ee)", screen_css, re.I):
        d.append({"type": "LIGHT_THEME"})

    return d


def main():
    global EXPECT_SOURCES, EXPECT_DARK
    root = sys.argv[1]
    # only insights articles are expected to carry a Sources section
    EXPECT_SOURCES = not any(x in root for x in ("glossary", "answers", "ebooks", "guides"))
    EXPECT_DARK = "ebooks" not in root
    files = [f for f in sorted(glob.glob(os.path.join(root, "*.html")))
             if not re.match(r"page-\d+$", os.path.basename(f)[:-5])]
    out, counts = {}, {}
    for f in files:
        d = check(f)
        if d:
            out[os.path.basename(f)[:-5]] = d
            for x in d:
                counts[x["type"]] = counts.get(x["type"], 0) + 1
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
    print(f"scanned {len(files)} files; {len(out)} with defects")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {v:5d}  {k}")


if __name__ == "__main__":
    main()
