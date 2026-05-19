## Hermes Alpha: Technical SEO Audit

**Site:** winfredquek.com  
**Agent:** Hermes Alpha  
**Audit date:** 2026-05-19  
**Scope:** public/ directory on branch `claude/zealous-jang-86cef2`

---

## Critical Issues

### 1. GA4 tracking absent from all insight articles (131 pages)

**Problem:** Google Analytics 4 (`G-T6C46CFKCM`) is only injected on `index.html` and approximately 48 other top-level pages. All 131 insight articles (`/insights/*.html`) have zero GA4 tracking. The `_enhance.js` bundle (loaded on every page) does not inject GA4. The `_schema.js` file does not inject it either.

**SEO impact:** Google Search Console performance data (clicks, impressions, CTR, average position) is not recorded for the highest-traffic-potential pages on the site. You cannot see which articles rank, convert, or drive booking intent. Conversion attribution is broken.

**Fix:** Add the GA4 snippet to the `<head>` of every insight template, or inject it via `_enhance.js`. Because the GA4 ID `G-T6C46CFKCM` has a code comment explicitly stating it is a placeholder (`TODO: replace with real ID`), also verify whether this measurement ID has an active GA4 property behind it. If not, create the GA4 property first, then distribute the correct ID sitewide.

---

### 2. 23 sitemap URLs point to non-existent pages (guaranteed 404s)

**Problem:** The sitemap.xml (479 URLs) references 23 insight slugs for which no `.html` file exists in `public/insights/`. These pages will return 404. Google will crawl them, find 404s, log coverage errors in Search Console, and waste crawl budget.

The missing pages include:
- `one-vs-two-properties-singapore`
- `second-property-timing-singapore`
- `property-retirement-planning-singapore`
- `buying-property-before-marriage-singapore`
- `cpf-oa-vs-cash-downpayment-singapore`
- `hdb-flat-inheritance-singapore`
- `india-buyer-singapore-property-2026`
- `indonesia-buyer-singapore-property-2026`
- `lentor-hills-property-investment`
- `mrt-distance-property-value-sg`
- `msr-explained-singapore`
- `new-citizen-property-rights-singapore`
- `north-coast-innovation-corridor-property`
- `option-to-purchase-guide-singapore`
- `progressive-payment-scheme-new-launch`
- `property-trust-singapore-guide`
- `right-sizing-retirement-singapore`
- `school-catchment-property-strategy-sg`
- `tengah-eco-town-property-guide`
- `upper-thomson-bishan-property-2026`
- `us-buyer-singapore-property-2026`
- `hdb-rent-out-vs-sell-analysis`
- `income-ceiling-private-property-singapore`

**Additional problem:** Three of these missing pages are linked directly from the homepage (`index.html`): `one-vs-two-properties-singapore`, `second-property-timing-singapore`, and `property-retirement-planning-singapore`. Homepage link equity is leaking to 404 pages.

**SEO impact:** Crawl budget waste, GSC coverage errors, broken internal links from homepage, potential ranking demotion signal.

**Fix:** Either create the missing articles (preferred) or remove the URLs from sitemap.xml and remove the three broken links from index.html.

---

### 3. Duplicate JSON-LD `@id` across two separate `@graph` blocks on index.html

**Problem:** `index.html` contains two separate `<script type="application/ld+json">` blocks, each with their own `@graph`. Both blocks define `@id: "https://winfredquek.com/#winfred"` as a `Person` entity with different properties. Google's structured data parser may produce conflicting entity definitions, or drop one silently.

Block 1 (lines 37–135): defines `#winfred` as a `Person` with `jobTitle: "Investor-minded Property Advisor"`, `worksFor: Crestbrick Pte Ltd`, email `winfredquekoc@gmail.com`.

Block 2 (lines 260–299): redefines `#winfred` as a `Person` with `jobTitle: "Property Investment Advisor"`, `worksFor: { @id: #crestbrick }` (a `LocalBusiness`), no email.

**SEO impact:** Conflicting schema signals reduce confidence for Knowledge Panel generation and E-E-A-T signals. Google may ignore or partially merge the two definitions in an unpredictable way.

**Fix:** Merge all Person/RealEstateAgent/ProfessionalService/WebSite/LocalBusiness entities into a single `@graph` block. Remove the second `<script type="application/ld+json">` block entirely. Use `@id` cross-references within the one graph.

---

## High Priority

### 4. Dual `<h1>` on 88 of 131 insight articles

**Problem:** Articles using the hero-banner template emit two `<h1>` elements: one in the hero section and one in the article body. The hero `<h1>` has `aria-hidden="true"` on the Tampines and ABSD articles, which means screen readers skip it — but Google's crawler does not honour `aria-hidden` for structured-data or ranking purposes. Google sees two H1s and treats this as a heading hierarchy problem.

Example from `tampines-mop-2026.html`:
- Line 39: `<h1 aria-hidden="true">Tampines MOP 2026: Upgrading in the East...`
- Line 45: `<h1 class="serif text-4xl...">Tampines MOP 2026: Upgrading in the East...`

The `decoupling-singapore.html` has one `<h1>` in the hero without `aria-hidden`, and no second `<h1>` in the body (that article uses the correct single-h1 pattern).

88 out of 131 insight articles have 2 `<h1>` tags confirmed.

**SEO impact:** Google's guidelines recommend one H1 per page. Duplicate H1s confuse topic signals. While not a direct penalty, it degrades heading hierarchy signals and can suppress rich result eligibility.

**Fix:** Change the hero `<h1>` to `<div role="heading" aria-level="1">` (for accessibility) or convert it to a `<p>` or `<div>` and keep only the article body `<h1>`. Since the hero version already has `aria-hidden="true"` on most pages, the simplest fix is changing those hero tags to `<div>` — this preserves the visual design and removes the duplicate H1 signal.

---

### 5. Two duplicate ABSD pages both indexed (no canonical cross-reference)

**Problem:** Two pages cover essentially the same topic:
- `/insights/absd-singapore` — canonical points to `https://winfredquek.com/insights/absd-singapore` (self-referencing)
- `/insights/absd-singapore-2026` — canonical points to `https://winfredquek.com/insights/absd-singapore-2026` (self-referencing)

Both are in `sitemap.xml`. Both have near-identical `<title>` tags (`ABSD Singapore 2026: Every | Winfred Quek CEA R073319H`). The meta descriptions differ slightly but target the same queries. Neither canonicalises to the other. Google will split link equity and may rank neither strongly.

**SEO impact:** Keyword cannibalisation. Diluted backlink authority. Both pages compete for the same queries (`ABSD Singapore 2026`, `ABSD rates Singapore`).

**Fix:** Decide which URL is the canonical version (recommend `/insights/absd-singapore-2026` as it is the more specific and content-rich version). Change the canonical on `/insights/absd-singapore` to point to `/insights/absd-singapore-2026`. Remove `/insights/absd-singapore` from `sitemap.xml`. Optionally add a 301 redirect (requires Vercel config).

---

### 6. OpenGraph and Twitter Card images use relative URLs on homepage

**Problem:** On `index.html`:
```html
<meta property="og:image" content="/img/og-image.jpg" />
<meta name="twitter:image" content="/img/og-image.jpg" />
```

The OpenGraph spec requires absolute URLs for image properties. Facebook, LinkedIn, WhatsApp, and Twitter parsers may fail to fetch or render the image when sharing.

**SEO impact:** Poor social share preview images reduce click-through rates when pages are shared on social media. This affects social-driven traffic indirectly.

**Fix:** Change both to `https://winfredquek.com/img/og-image.jpg`. The insight pages (e.g. `decoupling-singapore.html`) already use absolute URLs correctly — apply the same pattern to index.html.

---

### 7. `_schema.js` references two non-existent assets

**Problem:** The dynamically injected `_schema.js` (loaded on every page) references:
- `'image': 'https://winfredquek.com/winfred.jpg'` — this file does not exist in `public/`. The correct versioned image is `/img/winfred-hero.jpg?v=3`.
- `'logo': {'@type':'ImageObject','url':'https://winfredquek.com/logo.png'}` — `logo.png` does not exist in `public/`.

Both broken URLs appear in JSON-LD that Google fetches. Google's Rich Results Test will flag these as invalid image references and may suppress rich results.

**SEO impact:** Publisher logo and agent image in structured data resolve to 404s. This reduces confidence for Knowledge Panel, Article rich results, and Local Business rich results.

**Fix:** In `_schema.js` line 17, change `'https://winfredquek.com/winfred.jpg'` to `'https://winfredquek.com/img/winfred-hero.jpg'` (no query string needed in schema). On line 69, change `'https://winfredquek.com/logo.png'` to `'https://winfredquek.com/img/og-image.jpg'` (the closest equivalent available), or add a proper `logo.png` to `public/`.

---

### 8. `_schema.js` email uses non-existent address

**Problem:** `_schema.js` line 19 sets `'email': 'winfred@winfredquek.com'`. This email domain (`winfredquek.com`) likely does not have active email — the real contact email used across all 308 HTML pages is `winfredquekoc@gmail.com` (also in the JSON-LD on index.html).

**SEO impact:** Schema validator sees a mismatched email. Minor E-E-A-T signal inconsistency. More importantly, if Google shows this email in a Knowledge Panel or rich snippet, users attempting to email via that address will receive no reply.

**Fix:** Change `_schema.js` line 19 to `'email': 'winfredquekoc@gmail.com'`.

---

## Medium Priority

### 9. `404` page is included in sitemap.xml

**Problem:** Line 10–14 of `sitemap.xml` includes:
```xml
<url>
  <loc>https://winfredquek.com/404</loc>
  ...
  <priority>0.5</priority>
</url>
```

A 404 error page should never appear in a sitemap. Google should not crawl or index the 404 page.

**SEO impact:** Crawl budget waste, minor signal noise.

**Fix:** Remove the `<url>` block for `https://winfredquek.com/404` from `sitemap.xml`.

---

### 10. Vercel Analytics script loaded twice on homepage

**Problem:** `index.html` includes `<script defer src="/_vercel/insights/script.js"></script>` at both line 34 (in the `<head>`) and line 317 (after the GA4 block, before closing `</head>`). This causes the analytics beacon to fire twice per pageview.

**SEO impact:** No direct ranking impact, but doubles Vercel Analytics event count, making homepage performance data unreliable. May also marginally slow page load.

**Fix:** Remove the duplicate `<script defer src="/_vercel/insights/script.js"></script>` at line 317.

---

### 11. GA4 measurement ID is a placeholder on all 49 pages that have it

**Problem:** The comment blocks adjacent to the GA4 snippet on `index.html` explicitly say:
```
<!-- TODO: Create GA4 property at analytics.google.com Admin > Create Property > get Measurement ID -->
<!-- Note: Replace G-T6C46CFKCM with real ID from analytics.google.com once property is created. -->
```

If `G-T6C46CFKCM` is a genuine placeholder and no GA4 property has been created, all analytics data is being dropped.

**SEO impact:** No data loss in Google rankings, but total loss of behavioural data (sessions, bounce rate, events) that would inform content decisions.

**Fix:** Create a GA4 property at analytics.google.com, obtain the real Measurement ID, and replace `G-T6C46CFKCM` sitewide.

---

### 12. Homepage description is 158 characters (over recommended limit)

**Problem:** The meta description on `index.html` is 158 characters:
> "Singapore property investor advisor: portfolio-first approach, 9 years, 5 properties before 30, and the Property Portfolio Analysis methodology. CEA R073319H."

Google typically truncates descriptions at 155–160 characters in SERPs. The key credential (`CEA R073319H`) at the end may be cut.

**SEO impact:** Click-through rate may suffer if the truncated snippet loses the persuasive end.

**Fix:** Trim to under 155 characters while preserving CEA number: e.g., "Singapore property advisor. Portfolio-first strategy for HDB upgraders and investors. 9 years, 5 properties before 30. CEA R073319H." (133 chars).

---

### 13. Mobile and desktop nav use inconsistent URL formats

**Problem:** The desktop nav uses clean paths (`/about`, `/services`, `/tools`) while the mobile nav uses `.html` suffixes (`/about.html`, `/services.html`, `/listings.html`). Additionally, the mobile nav includes several pages not on the desktop nav (`/new-launches.html`, `/districts.html`, `/faq.html`, `/track-record.html`, `/testimonials.html`).

**SEO impact:** Duplicate content risk if Googlebot crawls both `/about` and `/about.html` as separate URLs (depends on Vercel routing config). Internal link dilution: link equity passes to two different URL forms.

**Fix:** Standardise all nav links to the clean path format (no `.html` extension) to match the canonical URLs defined in each page's `<link rel="canonical">`.

---

### 14. `news-sitemap.xml` uses non-standard comma formatting in `<news:name>`

**Problem:** The news sitemap publication name reads:
```xml
<news:name>Winfred Quek ,  Singapore Property Advisory</news:name>
```
The comma-space-space pattern (` ,  `) is a formatting artifact from automated generation. Similarly, news titles contain ` ,  ` separators: `Singapore cooling measures ,  chronological timeline`.

**SEO impact:** Google News parser may misread the publication name. While Google News is not a primary distribution channel for property advisory content, it affects any eligibility for news-style rich results.

**Fix:** Remove the errant spaces around commas: `Winfred Quek – Singapore Property Advisory`.

---

## Quick Wins

### QW1. Add `<link rel="preload">` for the hero background video on index.html

**Problem:** The homepage autoplay video (`/video/hero-scroll.mp4`) has `preload="auto"` but no corresponding `<link rel="preload" as="video">` in the `<head>`. The browser discovers the video via the `<video>` element late in parse, which may delay LCP.

**Fix:** Add to `<head>`:
```html
<link rel="preload" as="video" href="/video/hero-scroll.mp4" type="video/mp4" />
```
(< 5 minutes)

---

### QW2. Add `hreflang="en-sg"` self-referential tag to all pages

**Problem:** The site targets Singapore (English) users exclusively, but no `hreflang` tags exist anywhere. For a geo-targeted site, `hreflang="en-sg"` signals to Google which language/region the content is intended for.

**Fix:** Add to `<head>` of every page:
```html
<link rel="alternate" hreflang="en-sg" href="https://winfredquek.com/[current-path]" />
<link rel="alternate" hreflang="x-default" href="https://winfredquek.com/[current-path]" />
```
This can be injected via `_enhance.js` using `window.location.href`. (< 30 minutes in `_enhance.js`)

---

### QW3. Fix truncated `<title>` tags across ~55 insight articles

**Problem:** Many insight article titles were cut mid-phrase, leaving incomplete titles that confuse both users and Google:
- `"ABSD Singapore 2026: Every | Winfred Quek CEA R073319H"` — "Every" is meaningless without the rest
- `"Tampines MOP 2026: Upgrading in | Winfred Quek CEA R073319H"` — "in" is dangling
- `"Bridging Loan Singapore: The | Winfred Quek CEA R073319H"` — "The" alone is meaningless

The pattern is the title keyword phrase was truncated before the suffix was appended, likely during batch generation.

**Fix:** For each affected file, complete the title phrase. Correct examples:
- `"ABSD Singapore 2026: Every Rate, Every Remission | Winfred Quek"`
- `"Tampines MOP 2026: Upgrading in the East | Winfred Quek CEA R073319H"`
- `"Bridging Loan Singapore: The Complete Playbook | Winfred Quek"`

Keep final title under 60 characters before the ` | ` suffix. (Batch find-and-fix across ~55 files)

---

### QW4. Consolidate `_schema.js` Article schema injection with static `<head>` schemas

**Problem:** Insight articles already have a full static `Article` JSON-LD block in `<head>`. The `_schema.js` dynamically injects a second `Article` block on every page with path `/insights/*`. This means insight pages end up with two `Article` schemas — one static (authoritative, with correct dates and wordCount) and one dynamic (using `new Date()` as fallback, which always resolves to today's date, making `datePublished` incorrect after the first day).

**Fix:** In `_schema.js`, guard the Article schema injection with a check for an existing static `Article` schema:
```js
const existingArticle = [...document.querySelectorAll('script[type="application/ld+json"]')]
  .some(s => { try { return JSON.parse(s.textContent)['@type'] === 'Article'; } catch(e){ return false; } });
if (!existingArticle && (path.startsWith('/insights') || path.startsWith('/blog'))) {
  // inject Article schema
}
```
(< 15 minutes)

---

### QW5. Remove `https://winfredquek.com/404` from sitemap and fix the formatting

See **Critical Issue #2** and **Medium Priority #9** — the 404 removal is a 1-line sitemap edit.

---

## Summary Table

| # | Issue | Severity | Effort | Files Affected |
|---|-------|----------|--------|----------------|
| 1 | GA4 missing from 131 insight pages | Critical | Medium | All 131 insight `.html` files or `_enhance.js` |
| 2 | 23 sitemap URLs → 404 (3 linked from homepage) | Critical | Medium | `sitemap.xml` + `index.html` |
| 3 | Duplicate JSON-LD `@graph` / `@id` on homepage | Critical | Low | `index.html` |
| 4 | Dual `<h1>` on 88 insight articles | High | Medium | 88 insight templates |
| 5 | Two ABSD pages, both self-canonicalised | High | Low | `absd-singapore.html` + `sitemap.xml` |
| 6 | OG/Twitter images are relative URLs on homepage | High | Low | `index.html` |
| 7 | `_schema.js` references 2 missing image assets | High | Low | `_schema.js` |
| 8 | `_schema.js` email mismatch | High | Low | `_schema.js` |
| 9 | 404 page in sitemap | Medium | Low | `sitemap.xml` |
| 10 | Vercel Analytics double-loaded on homepage | Medium | Low | `index.html` |
| 11 | GA4 ID may be placeholder | Medium | Low | All pages with GA4 |
| 12 | Homepage meta description 158 chars | Medium | Low | `index.html` |
| 13 | Nav URL format inconsistency (.html vs clean) | Medium | Medium | `index.html` nav |
| 14 | `news-sitemap.xml` formatting artifacts | Medium | Low | `news-sitemap.xml` |
| QW1 | Add video preload hint | Quick win | < 5 min | `index.html` |
| QW2 | Add hreflang tags | Quick win | < 30 min | `_enhance.js` |
| QW3 | Fix truncated `<title>` tags (~55 articles) | Quick win | Medium | ~55 insight files |
| QW4 | Guard duplicate Article schema in `_schema.js` | Quick win | < 15 min | `_schema.js` |
| QW5 | Remove 404 from sitemap | Quick win | < 1 min | `sitemap.xml` |

---

## Positive Findings

- **robots.txt:** Well-structured. Correctly blocks tracking query strings (`?utm_`, `?fbclid=`, `?gclid=`, `?msclkid=`). All major AI crawlers are explicitly allowed. Sitemap URL is correct.
- **Canonical tags:** Present and self-referencing on all audited pages. Insight article canonicals use the clean (non-.html) URL form, consistent with the sitemap.
- **Structured data (static):** Article, FAQPage, and BreadcrumbList schemas on insight pages are well-formed and contain complete data. FAQPage on `decoupling-singapore.html` has 10 high-quality Q&A pairs — excellent for featured snippets.
- **Internal linking on insight pages:** Related reading sections (3–4 links per article) use descriptive anchor text and link to valid, existing pages. 
- **Heading hierarchy in article bodies:** H2 and H3 structure inside article `<article>` tags is logical and well-organised. No skipped heading levels detected.
- **Sitemap priority/changefreq:** All 479 sitemap entries include both `<changefreq>` and `<priority>`. Homepage is correctly set to `priority=1.0` / `daily`.
- **Alt text:** Hero images have descriptive alt text (`alt="Winfred Quek, Property Advisor at Crestbrick"`).
- **`preload` for hero image:** `index.html` correctly preloads `/img/winfred-hero.jpg?v=3` as an image in the `<head>`.
