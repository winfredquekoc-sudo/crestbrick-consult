#!/usr/bin/env python3
"""Inject internal "Related guides" cross-link blocks into winfredquek.com insight articles.

Pure SEO/GEO improvement: additive links only, no content/factual changes.
Idempotent (skips files already containing ">Related guides<").
"""
import os
import re
import sys
from html import unescape

INSIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "public", "insights",
)

# Unpublished / under-review — never edit, never link TO.
EXCLUDED = {
    "downpayment-condo-singapore-2026",
    "bto-application-guide-singapore-2026",
    "singapore-property-market-outlook-2026",
    "condo-maintenance-fees-singapore",
    "first-time-home-buyer-singapore-guide",
    "landlord-guide-renting-out-singapore-2026",
    "hdb-resale-buying-process-singapore",
    "cpf-at-55-property-impact-singapore",
    "executive-condominium-buyer-guide-2026",
    "how-to-sell-hdb-resale-singapore",
    "singapore-property-investment-beginners",
    "property-valuation-singapore-guide",
    "how-to-negotiate-property-price-singapore",
    "hdb-subletting-rules-singapore",
    "singapore-property-tax-investor-guide",
}

# Cluster -> ordered list of slug keyword markers.
CLUSTERS = {
    "absd": ["absd", "stamp-duty", "bsd", "ssd"],
    "hdb_mop": [],  # special: slugs ending -mop-2026
    "hdb": ["hdb"],
    "cpf": ["cpf"],
    "mortgage": ["mortgage", "loan", "refinanc", "tdsr", "msr", "sora",
                 "fixed", "floating", "bridging", "interest"],
    "decoupling": ["decoupling", "restructuring", "99-1", "ownership"],
    "foreign": ["foreign", "foreigner", "pr-", "australian", "hk-", "india",
                "indonesia", "malaysia", "us-", "citizen"],
    "ec": ["ec-", "executive-condominium"],
    "newlaunch": ["new-launch", "progressive-payment", "pre-sale", "subsale",
                  "integrated-development"],
    "districts": ["ccr", "rcr", "ocr", "district", "lentor", "tengah",
                  "bayshore", "jurong", "kallang", "marine-parade",
                  "north-coast", "upper-thomson"],
    "selling": ["sell", "seller", "exit", "net-proceeds"],
    "rental": ["rental", "landlord", "yield", "tenant"],
    "legal": ["divorce", "inheritance", "intestacy", "gift", "trust", "will",
              "joint-tenancy", "tenancy-in-common", "transferring",
              "lasting-power", "succession", "children", "marriage",
              "adding-child"],
    "investment": ["investment", "returns", "negative-gearing",
                   "cash-on-cash", "portfolio", "reits", "market-cycles"],
}

# Curated general guides used to top up "island" articles that have no
# cluster and little slug-keyword overlap with the rest of the corpus.
# All must be live (non-excluded) slugs.
FALLBACK_POOL = [
    "absd-singapore",
    "cooling-measures",
    "ccr-rcr-ocr-framework",
    "new-launch-vs-resale",
    "freehold-vs-leasehold",
    "hdb-lease-decay-impact",
    "best-district-invest-singapore-2026",
    "cooling-measures-timeline",
]

# Slug-token stopwords for keyword overlap scoring.
STOP = {"singapore", "sg", "2026", "2024", "guide", "the", "for", "and",
        "how", "to", "of", "a", "vs", "in", "your", "what", "is", "do"}


def slug_of(filename):
    return filename[:-5] if filename.endswith(".html") else filename


def extract_title(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if not m:
        return None
    t = unescape(m.group(1)).strip()
    # Drop " | Winfred Quek" or ", Winfred Quek" suffix.
    t = re.split(r"\s*[|,]\s*Winfred Quek\b.*$", t)[0].strip()
    return t or None


def cluster_of(slug):
    if slug.endswith("-mop-2026"):
        return "hdb_mop"
    for name, markers in CLUSTERS.items():
        if name == "hdb_mop":
            continue
        for kw in markers:
            if kw in slug:
                return name
    return None


def tokens(slug):
    return {t for t in re.split(r"-+", slug) if t and t not in STOP}


def detect_vars(html):
    """Return (rule, ink, accent, ink_soft) CSS var names for this file."""
    root = re.search(r":root\s*\{([^}]*)\}", html)
    body = root.group(1) if root else ""
    has = lambda v: ("--" + v) in body
    if has("accent") and has("rule"):
        return ("var(--rule)", "var(--ink)", "var(--accent)", "var(--ink-soft)")
    # Navy/gold scheme.
    if has("gold") and has("navy"):
        rule = "var(--gold)" if not has("rule") else "var(--rule)"
        ink = "var(--navy)" if has("navy") else "var(--text)"
        if has("text"):
            ink = "var(--text)"
        return (rule, ink, "var(--gold)", "var(--muted)")
    # Fallback: standard vars (most articles).
    return ("var(--rule)", "var(--ink)", "var(--accent)", "var(--ink-soft)")


def build_block(related, css):
    rule, ink, accent, _soft = css
    lines = [
        f'<section style="max-width:680px;margin:3rem auto 0;'
        f'padding:2rem 1.5rem 0;border-top:1px solid {rule};">',
        f'  <h2 style="font-size:1rem;font-weight:600;color:{ink};'
        f'margin-bottom:1rem;">Related guides</h2>',
        '  <ul style="list-style:none;padding:0;margin:0;display:flex;'
        'flex-direction:column;gap:.5rem;">',
    ]
    for slug, title in related:
        safe = title.replace("&", "&amp;").replace("<", "&lt;")
        lines.append(
            f'    <li style="font-size:.9rem;">'
            f'<a href="/insights/{slug}" style="color:{accent};">'
            f'{safe}</a></li>'
        )
    lines.append("  </ul>")
    lines.append("</section>")
    return "\n".join(lines) + "\n"


def main():
    files = sorted(f for f in os.listdir(INSIGHTS_DIR) if f.endswith(".html"))
    articles = {}  # slug -> dict(title, cluster, toks, filename, html)
    for f in files:
        slug = slug_of(f)
        if slug in EXCLUDED:
            continue
        path = os.path.join(INSIGHTS_DIR, f)
        with open(path, "r", encoding="utf-8") as fh:
            html = fh.read()
        title = extract_title(html)
        if not title:
            continue
        articles[slug] = {
            "title": title,
            "cluster": cluster_of(slug),
            "toks": tokens(slug),
            "filename": f,
            "html": html,
        }

    def pick_related(slug):
        a = articles[slug]
        if a["cluster"] == "hdb_mop":
            return pick_mop(slug)
        scored = []
        for other, b in articles.items():
            if other == slug:
                continue
            shared = len(a["toks"] & b["toks"])
            same = 1 if (a["cluster"] and a["cluster"] == b["cluster"]) else 0
            score = same * 100 + shared
            if score > 0:
                scored.append((score, shared, other))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        result = [s for _, _, s in scored[:5]]
        # Top up to 4 with curated general guides if the slug is an island
        # (no cluster, low keyword overlap with the rest of the corpus).
        if len(result) < 4:
            for cand in FALLBACK_POOL:
                if len(result) >= 5:
                    break
                if cand != slug and cand in articles and cand not in result:
                    result.append(cand)
        return [(s, articles[s]["title"]) for s in result[:5]]

    def pick_mop(slug):
        a = articles[slug]
        towns = sorted(
            o for o, b in articles.items()
            if b["cluster"] == "hdb_mop" and o != slug
        )
        chosen = towns[:4]
        extras = []
        if "hdb-mop-upgrade-timeline" in articles:
            extras.append("hdb-mop-upgrade-timeline")
        # One HDB-general article.
        hdb_general = sorted(
            o for o, b in articles.items()
            if b["cluster"] == "hdb" and o not in chosen and o != slug
        )
        if hdb_general:
            # prefer keyword overlap
            hdb_general.sort(
                key=lambda o: (-len(a["toks"] & articles[o]["toks"]), o))
            extras.append(hdb_general[0])
        result = chosen[:4] + extras
        return [(s, articles[s]["title"]) for s in result[:5]]

    changed = []
    skipped = []
    samples = {}

    for slug in sorted(articles):
        a = articles[slug]
        html = a["html"]
        if ">Related guides<" in html:
            skipped.append((a["filename"], "already has Related guides"))
            continue
        related = pick_related(slug)
        if len(related) < 4:
            skipped.append((a["filename"],
                            f"only {len(related)} related found"))
            continue
        css = detect_vars(html)
        block = build_block(related, css)

        # Injection point: before </main>, else before <footer.
        if "</main>" in html:
            new_html = html.replace("</main>", block + "</main>", 1)
        else:
            m = re.search(r"\n[ \t]*<footer\b", html)
            if not m:
                skipped.append((a["filename"], "no </main> and no <footer>"))
                continue
            idx = m.start() + 1  # keep the leading newline before block
            new_html = html[:idx] + block + html[idx:]

        if new_html == html:
            skipped.append((a["filename"], "injection produced no change"))
            continue

        path = os.path.join(INSIGHTS_DIR, a["filename"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_html)
        changed.append(a["filename"])
        samples[slug] = related

    print(f"Total articles processed: {len(articles)}")
    print(f"Files changed: {len(changed)}")
    print(f"Files skipped: {len(skipped)}")
    for fn, reason in skipped:
        print(f"  SKIP {fn}: {reason}")

    print("\n=== 5 sample articles ===")
    sample_slugs = []
    seen_clusters = set()
    for slug in sorted(samples):
        c = articles[slug]["cluster"] or "none"
        if c not in seen_clusters:
            seen_clusters.add(c)
            sample_slugs.append(slug)
        if len(sample_slugs) >= 5:
            break
    if len(sample_slugs) < 5:
        for slug in sorted(samples):
            if slug not in sample_slugs:
                sample_slugs.append(slug)
            if len(sample_slugs) >= 5:
                break
    for slug in sample_slugs:
        print(f"\n[{slug}]  (cluster: {articles[slug]['cluster']})")
        print(f"  {articles[slug]['title']}")
        for rs, rt in samples[slug]:
            print(f"    -> /insights/{rs}  ({rt})")


if __name__ == "__main__":
    main()
