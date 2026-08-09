#!/usr/bin/env python3
"""Add a "Read further" block to each glossary entry linking to relevant insights.

Links open in a new tab so the reader keeps their place in the dictionary.
Matching is by shared keywords between the glossary slug and the insight
slug/title, scored so that a whole-term match outranks a single common word.

Usage: link_glossary_insights.py [--apply]
"""
import re, sys, html
from pathlib import Path

PUB = Path("/Users/winfredquek/crestbrick-consult/public")
GLOSS, INS = PUB / "glossary", PUB / "insights"
TAG = re.compile(r"<[^>]+>")

# words too common to carry signal
STOP = {
    "singapore", "property", "the", "a", "an", "of", "and", "or", "to", "in", "for",
    "your", "what", "is", "how", "guide", "vs", "versus", "2026", "2025", "explained",
    "buying", "buyer", "sell", "with", "on", "at", "you", "it", "be", "do", "can",
}


def words(s):
    return {w for w in re.split(r"[^a-z0-9]+", s.lower()) if w and w not in STOP and len(w) > 2}


def title_of(p):
    h = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    if m:
        return html.unescape(TAG.sub("", m.group(1))).strip()
    m = re.search(r"<title>(.*?)</title>", h, re.S)
    return html.unescape(TAG.sub("", m.group(1))).split("|")[0].strip() if m else p.stem


def main():
    apply = "--apply" in sys.argv
    insights = []
    for p in sorted(INS.glob("*.html")):
        if p.stem == "index":
            continue
        t = title_of(p)
        insights.append({"slug": p.stem, "title": t, "w": words(p.stem + " " + t)})

    added = skipped = nomatch = 0
    for g in sorted(GLOSS.glob("*.html")):
        if g.stem == "index":
            continue
        h = g.read_text(encoding="utf-8")
        if 'class="read-further"' in h:
            skipped += 1
            continue
        term = title_of(g)
        gw = words(g.stem + " " + term)
        if not gw:
            nomatch += 1
            continue

        scored = []
        for i in insights:
            overlap = gw & i["w"]
            if not overlap:
                continue
            score = len(overlap)
            # whole slug appearing inside the insight slug is a much stronger signal
            if g.stem in i["slug"]:
                score += 6
            elif all(w in i["w"] for w in gw) and len(gw) > 1:
                score += 3
            scored.append((score, i))
        # On equal score prefer the more general article: a shorter slug beats a
        # town or project specific one, so "MOP" links the explainer rather than
        # whichever estate happens to sort first alphabetically.
        scored.sort(key=lambda x: (-x[0], len(x[1]["slug"]), x[1]["slug"]))
        picks = [i for s, i in scored if s >= 2][:3]
        if not picks:
            nomatch += 1
            continue

        items = "\n".join(
            f'      <li><a href="/insights/{i["slug"]}" target="_blank" rel="noopener">'
            f'{html.escape(i["title"])}</a></li>'
            for i in picks
        )
        block = f'''
  <div class="read-further" style="background:var(--highlight);border:1px solid var(--rule);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;padding:1.1rem 1.35rem;margin:2rem 0;">
    <p class="section-label" style="margin-bottom:.6rem;">Read further</p>
    <ul style="margin:0;padding-left:1.1rem;color:var(--ink-soft);font-size:.9rem;line-height:1.7;">
{items}
    </ul>
    <p style="font-size:.75rem;color:var(--ink-muted);margin:.6rem 0 0;">Opens in a new tab.</p>
  </div>
'''
        # place before the closing article, falling back to the disclaimer
        anchors = [re.compile(r"(?=\n\s*</article>)"),
                   re.compile(r'(?=\n\s*<div class="disclaimer-block")'),
                   re.compile(r"(?=\n\s*</main>)")]
        placed = False
        for rx in anchors:
            m = rx.search(h)
            if m:
                h = h[: m.start()] + "\n" + block + h[m.start():]
                placed = True
                break
        if not placed:
            nomatch += 1
            continue
        if apply:
            g.write_text(h, encoding="utf-8")
        added += 1

    print(f"{'APPLIED' if apply else 'DRY RUN'}: added={added} already_had={skipped} no_match={nomatch}")


if __name__ == "__main__":
    main()
