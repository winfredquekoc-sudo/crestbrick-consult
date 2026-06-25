# Overnight Website SEO + Technical Hygiene — 2026-06-23

Branch: `overnight/website-seo` (off `funnel-build`). Not pushed, not merged. `npm run build` passes and is now idempotent.

## Summary

The site was already in strong SEO shape (unique titles, canonicals, meta descriptions on virtually every page; a `_schema.js` injector that adds RealEstateAgent / Article / Service / BreadcrumbList / FAQPage JSON-LD on every page at runtime). So this pass focused on **structural hygiene** rather than content or schema generation:

1. Trimmed over-length titles + tightened two oversized meta descriptions on key pages.
2. Filled Open Graph / Twitter card gaps and fixed two broken `og:image` references.
3. Fixed genuinely-broken internal links (via redirects) and removed ~6,700 redirect-hop `.html` links.
4. Reconciled the sitemap so every listed URL returns 200.
5. Fixed a latent non-idempotent build bug in `stamp-nav.mjs`.

No new article/blog/content pages were created. All schema/data is real (CEA R073319H, Crestbrick L31010886H, Singapore context).

---

## 1. Titles & meta descriptions

Trimmed titles that ran 70–95 chars down to ~50–64 (Google truncates ~60). Keyword front-loaded, brand suffix kept.

- `public/buyers-guide.html` (95 → 51)
- `public/sell.html` (87 → 58)
- `public/listings.html` (78 → 61)
- `public/stamp-duty-calculator.html` (74 → 64)
- `public/family-office-brief.html` (74 → 51; removed double `|` brand)
- `public/case-studies.html` (38, weak → 58, keyword-rich)
- `public/llms-full-context.html` (fixed stray `,  ,` artifact)
- `public/property-portfolio-analysis-sample.html` (fixed `, ,` artifact)
- `public/tools/index.html` (86 → 50; meta desc 168 → ~158)
- `public/property-portfolio-analysis/index.html` (85 → 52; meta desc 242 → ~185, still slightly long but readable; og:title synced)

## 2. Open Graph / Twitter cards

Added the missing tags (`og:description`, `og:type`, `og:image`, `og:url`, `twitter:card`, `twitter:image`) to:

- `public/absd-calculator.html`, `public/tdsr-calculator.html`, `public/stamp-duty-calculator.html`
- `public/sell.html`, `public/glossary.html`, `public/resources.html`, `public/wealth-stack.html`
- `public/enbloc-reinvestment.html`, `public/family-office-brief.html`
- `public/newsletter-archive.html`, `public/start.html`, `public/llms-full-context.html`

Fixed **broken** `og:image` references that pointed at files that never existed (`/img/og/case-studies.png`, `/img/og/property-portfolio-analysis-sample.png`) — repointed to the existing `/img/og-image.jpg`. Also fixed an "an property" grammar slip in the PPA-sample og:description.

Skipped OG work on noindex/non-content pages (thank-you, unsubscribe, portal, 404, wa) by design.

## 3. Images

Per an image audit, the site is clean: existing static `<img>` tags already have descriptive alt + width/height + correct lazy/eager split, and no broken image paths. The only gap was missing `width`/`height` on three JS-templated listing cards — added `width="640" height="400"` (matches the 16:10 CSS aspect ratio, so no layout shift):

- `public/index.html` (listing card template)
- `public/listings.html` (listing card template)
- `public/new-launches.html` (launch card template)

## 4. Internal links & redirects (`vercel.json`)

A broken-link audit flagged ~370 references, but most already resolve via existing `vercel.json` redirects (`/audit`, `/launches`, `/hdb-towns`, `/blog`, calculator paths, etc.). After cross-checking against the redirect table, the **genuinely-broken** targets (no file, no redirect) were:

- Tool/calculator path typos: `/calculators`, `/calculators/stamp-duty-calculator`, `/calculators/tdsr-calculator`, `/tools/tdsr`, `/tools/stamp-duty`, `/tools/cpf`
- `/compare` and `/compare/` (a "Compare" section label with no index)
- 7 district slug mismatches (e.g. `/districts/d18-tampines` → `…d18-tampines-pasir-ris`)
- 3 insights slug mismatches (e.g. `/insights/cpf-refund-decoupling` → `…-singapore`)

All were fixed by adding `permanent` redirects to verified-existing destinations (19 new redirects). After the change, a full-site sweep finds **0 unresolved internal links** (every clean-URL href resolves to a file or a redirect).

**`.html` → clean-URL cleanup:** the site had ~6,734 internal `href` links carrying `.html`, each triggering a 301 hop under `cleanUrls:true`. The bulk lived in the stamped mobile-nav (`_templates/nav.html`, lines 28–39 used `.html` while the desktop nav was already clean). Fixed the template + globally rewrote the remaining body/footer `.html` hrefs to clean URLs (every target verified to have a clean route first; `/blog/*` excluded because it has bespoke redirects). Now **1** such link remains (the intentionally-excluded blog link). `src=` attributes and external URLs were untouched.

## 5. Sitemap (`public/sitemap.xml`)

Removed 9 URLs that were themselves 301 redirect sources (sitemaps should only list canonical 200 URLs):

- `/absd-calculator`, `/stamp-duty-calculator`, `/tdsr-calculator` (301 → `/tools/*`)
- `/blog/absd-decoupling-singapore`, `/blog/absd-second-property-guide`, `/blog/hdb-bto-vs-resale`, `/blog/hdb-upgrade-timeline` (301 → `/insights/*`)
- `/links` (301 → `/links.html`), `/services/property-portfolio-analysis` (301 → `/property-portfolio-analysis`)

Their canonical destinations are already in the sitemap. Now every sitemap URL returns 200. `robots.txt` was already correct (allows all incl. AI crawlers, blocks UTM/click-id params, points at both sitemaps) — no change needed. (`news-sitemap.xml` is referenced by robots but was out of scope here.)

## 6. Build fix (`scripts/stamp-nav.mjs`)

Found and fixed a latent non-idempotency bug: the header-replace regex didn't consume the header line's leading whitespace, so every `npm run build` added 2 spaces of indentation to `<header class="topnav">`. Because Vercel runs the build on each deploy, files were slowly accumulating indentation in production. Made the regex consume `[ \t]*` before `<header>`; re-runs now report "0 updated" and produce byte-identical output. This also normalized the existing accumulated indentation back to the canonical 2 spaces (visible in the diff across ~551 stamped pages).

---

## Needs Winfred's decision

1. **Standalone calculator pages vs `/tools/*` (duplication).** `public/absd-calculator.html`, `public/stamp-duty-calculator.html`, and `public/tdsr-calculator.html` exist on disk with self-canonicals, but `vercel.json` has `permanent` 301s sending those clean URLs to `/tools/absd|bsd|affordability`. So the standalone pages are effectively unreachable at their clean URL. I improved their meta + removed them from the sitemap, but the orphaned `.html` files are still in the repo. **Recommend:** either delete the three standalone files, or drop the redirects and make them canonical. (I did not delete files.)

2. **Route collision on `/property-portfolio-analysis`.** Both `public/property-portfolio-analysis.html` and `public/property-portfolio-analysis/index.html` resolve to `/property-portfolio-analysis`. Vercel will serve one (typically the `.html`); the other is dead weight and can drift. **Recommend:** pick one as the source of truth and remove the other.

3. **Pricing figure inconsistency (factual).** `public/pricing.html` says the seller-rep value stack is "S$32,300 in services" in the meta description but "S$22,300" in the og:description (and a `$22,300` appears in `pricing.html`'s schema elsewhere). I did **not** touch the dollar figure — please confirm the correct number and I'll align all three.

4. **Duplicate `og:image` on some pages.** A pre-existing script (`scripts/_inject_schema_and_og.py`) appends a second `og:image` near `</head>` on several pages, so a few now show `og:image` twice (harmless — crawlers use the first — but untidy). Low priority; flag only.

5. **Remaining long titles at scale (optional).** ~77% of all titles (mostly `/insights/*`, `/area/*`, `/districts/*`) exceed 65 chars due to the templated `| Winfred Quek` / `· … · Winfred's POV | Crestbrick` suffix. Keywords are front-loaded so Google just truncates the tail — not harmful, and fixing 500+ pages would be a mass edit. **Recommend** only if you want to tighten the article/district title templates at generation time.
