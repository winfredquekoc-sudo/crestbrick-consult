# Lentor Cluster — WRITER BRIEF (read fully before writing)

You are writing ONE long form SEO + GEO article for Winfred Quek's property site
(winfredquek.com), part of a 42 article Lentor Gardens Residences cluster. Your job is to
produce a single complete HTML file that is visually and structurally identical to the
exemplar, with original, distinct, accurate prose for your assigned topic.

## STEP 1 — Read these three files first
1. `content/lentor-cluster/FACTS.md` — the ONLY source of numbers. Obey every hard rule in it.
2. `public/insights/lentor-gardens-residences-review-2026.html` — the EXEMPLAR. Clone its
   `<head>`, `<style>`, `<header class="topnav">` nav, hero `<section>`, `<article>` scaffold,
   FAQ pattern, `.article-cta`, related guides section, `<footer>`, and the `#wa-float` link
   EXACTLY. Change only the per article content and metadata.
3. `content/lentor-cluster/manifest.json` — find your row by your slug for title, h1,
   metaDescription, keyword, date, quickAnswer and related links.

## STEP 2 — Write the file to `public/insights/<your-slug>.html`

### Head (clone exemplar, swap these per your manifest row)
- `<title>` = manifest title + " | Winfred Quek"
- `<meta name="description">` = manifest metaDescription
- og:title, og:description, og:url (https://winfredquek.com/insights/<slug>), og:image stays /img/launches/lentor-gardens-residences.jpg
- article:published_time AND article:modified_time = your manifest `date`
- canonical = https://winfredquek.com/insights/<slug>
- THREE JSON-LD blocks, all valid JSON:
  1. Article — headline, description, the same author/publisher objects as the exemplar,
     datePublished/dateModified = your date, a realistic wordCount, mainEntityOfPage = canonical,
     and the speakable cssSelector [".quick-answer","h1","h2"].
  2. BreadcrumbList — Home > Insights > <your title>.
  3. FAQPage — 5 or 6 Question/Answer pairs whose text MATCHES your visible FAQ section word for word.

### Body
- Hero section: keep the exemplar's structure and the decorative SVG; change only the badge label
  (e.g. "New Launch · District 26 · 2026" or a cluster-appropriate label), the h1 (your manifest h1),
  and the byline date (Published <your date in "DD Month 2026">).
- `.section-label` "All insights", the kicker line, the visible (aria-hidden) h1, the byline with CEA R073319H.
- A `.quick-answer` div containing EXACTLY your manifest `quickAnswer` text (this is the GEO answer; do not alter it).
- The small "Facts verified: 16 June 2026 · Pricing pending official launch" line.
- A `.warn` pricing note near the top whenever you mention any price/PSF/quantum.
- 1500 to 2200 words of ORIGINAL prose specific to your topic. Use h2/h3 headings phrased to match
  search intent. Use at least one `<table>` inside `.table-wrap` where it helps (comparisons, ladders,
  cost breakdowns). Use the comparable launch ladder table from FACTS.md where relevant.
- A visible "Frequently asked questions" h2 with 5 to 6 `.faq-q` questions + answers that MATCH the FAQPage schema.
- The `.article-cta` block (Calendly: https://calendly.com/winfredquekoc).
- The author/disclosure paragraph (Winfred Quek is the Principal of Crestbrick Pte Ltd … CEA R073319H …).
- "Related guides" section linking your manifest `related` slugs (full /insights/<slug> paths) — always
  include the pillar `/insights/lentor-gardens-residences-review-2026`.
- The exemplar `<footer>` and the `#wa-float` link to
  `https://wa.me/6581618149?text=Hi%20Winfred%2C%20I%20have%20a%20question%20about%20Lentor%20Gardens%20Residences.`

## HARD RULES (from FACTS.md — repeated because they matter)
- NEVER invent a PSF, price, quantum, unit count, or floor area. Use ONLY FACTS.md figures, each
  labelled "estimate, pending official pricing on 4 July 2026" where FACTS.md labels them.
- NO HYPHENS in prose. Write "99 year leasehold", "6 to 7 minute walk", "self contained",
  "near sold out", "end user", "north side", "long term", "mixed use". (URLs/CSS/code exempt.)
- Advisor is "Winfred Quek" / "Winfred". CEA R073319H. Crestbrick Pte Ltd only in author/disclosure.
- CEA compliant: no guaranteed returns, no fabricated urgency, no invented appreciation %.
- Vary your prose from the other articles. Do NOT copy whole paragraphs from the exemplar; only clone
  STRUCTURE and reuse FACTS. Your angle is your slug's specific intent.

## STEP 3 — Return ONLY this (do not print the article):
`DONE <slug> — <approx word count> words, 3 schema blocks, <N> FAQs, hyphen-clean.`
