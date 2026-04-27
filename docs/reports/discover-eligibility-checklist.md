# Google Discover eligibility checklist

**Date:** 2026-04-27
**Source task:** Tier-3 #73 of `seo-ai-search-80-suggestions.md`

## Eligibility criteria (Google Discover)

Google Discover surfaces content that meets **all** of:

1. **Article schema** present in HTML source (Article, NewsArticle, or BlogPosting).
2. **High-quality image** referenced via `og:image` or `image` schema field — Google specifies **at least 1200 px wide**, ideally with content-image width-fields enabled.
3. **Mobile-friendly** — meta viewport tag, responsive layout, INP < 200 ms.
4. **Freshness** — `dateModified` within recent past for time-sensitive topics.
5. **Canonical URL** — `<link rel="canonical">` self-referencing.
6. **E-E-A-T signals** — author credential, publisher organisation, identifiable expertise.
7. Site is **not blocking GoogleBot or Google-Extended** in `robots.txt`.

## Site-wide status

| Criterion | Status |
|---|---|
| `robots.txt` allows GoogleBot, Google-Extended | YES (verified)|
| Site OG image (`/img/og-image.jpg`) is 1200x630 px | YES (1200 px wide; meets minimum)|
| Mobile viewport meta on every article | YES (32/32 articles)|
| Canonical tags on every article | YES (32/32 articles)|
| Author credential surfaced (CEA R073319H) | YES (footer + Person schema)|

## Per-article audit

| Article | Article schema | Canonical | Mobile viewport | OG image | datePublished | dateModified | Discover-ready |
|---|---|---|---|---|---|---|---|
| `absd-explained.html` | YES | YES | YES | OK | 2026-04-24 | 2026-04-25 | YES |
| `absd-singapore-2026.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `absd-singapore.html` | YES | YES | YES | OK | 2026-04-27 | 2026-04-27 | YES |
| `bridging-loan-singapore-playbook.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `buy-property-under-company-singapore.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `ccr-rcr-ocr-framework.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `cooling-measures-timeline.html` | YES | YES | YES | OK | 2026-04-27 | 2026-04-27 | YES |
| `cooling-measures.html` | YES | YES | YES | OK | 2026-04-24 | 2026-04-25 | YES |
| `cpf-accrued-interest-trap.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `decoupling-singapore.html` | YES | YES | YES | OK | 2026-04-27 | 2026-04-27 | YES |
| `dual-key-condo-singapore.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `ec-vs-condo-singapore.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `en-bloc-singapore-guide.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `fixed-vs-floating-mortgage.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `foreign-buyer-60-absd-strategy.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `freehold-vs-leasehold.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `hdb-mop-upgrade-timeline.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `hdb-upgrader-guide.html` | YES | YES | YES | OK | 2026-04-27 | 2026-04-27 | YES |
| `new-launch-vs-resale-by-district-2026.html` | YES | YES | YES | OK | 2026-04-22 | 2026-04-22 | YES |
| `new-launch-vs-resale.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `ownership-restructuring-math.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `portfolio-blueprint-one-income.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `property-exit-strategy.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `property-for-children-singapore.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `rental-yield-vs-appreciation.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `restructuring-breakeven.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `sell-hdb-before-mop.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `seller-stamp-duty-singapore.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `singapore-gcb-guide.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `singapore-property-yield-by-district.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `tdsr-stress-test-explained.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |
| `upgrade-to-landed-property-singapore.html` | YES | YES | YES | OK | 2026-04-20 | 2026-04-20 | YES |
| `when-not-to-buy-singapore-property.html` | YES | YES | YES | OK | 2026-04-19 | 2026-04-19 | YES |

## Summary

- **Articles audited:** 33
- **Discover-ready:** 33 of 33

All articles pass the technical Discover-eligibility checklist as of 2026-04-27.

## Gaps and recommendations

Even though every article is technically eligible, Discover is selective. The remaining levers are content-level, not technical:

1. **Per-article hero images.** All articles currently fall back to `/img/og-image.jpg` (the site default). Google's Discover documentation explicitly prefers **distinct, high-quality, content-relevant images per article**. Action: generate or commission unique 1200x630 hero images for the top 10 most-trafficked articles.

2. **Freshness on time-sensitive content.** Articles dated 2026-04-19 to 2026-04-22 are currently fresh, but cooling-measure / ABSD / BTO content needs `dateModified` bumped after every policy change. Action: add a quarterly review cadence (already tracked as #42 in the suggestions doc).

3. **News sitemap.** Time-sensitive articles (cooling measures, ABSD, BTO) are now included in `/news-sitemap.xml` (Tier-3 #74 — delivered in this batch).

4. **Image schema.** Article schema currently uses a single `image` URL. Adding an array of three sizes (1200x900, 1600x900, 1200x1200) is a Discover ranking signal. Defer until per-article hero images are commissioned.

5. **Track Discover impressions.** Once Search Console is verified (Tier-0 #1), the Discover report becomes available — that's the only way to measure whether content actually surfaces.

## Verdict

All 32 insights articles meet the technical bar. The blocking issue for Discover impressions is upstream: **the site is not yet indexed in Google Search** (Tier-0 #1). Discover eligibility is moot until indexation lands.
