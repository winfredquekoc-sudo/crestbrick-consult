# SEO Tier 0 + Tier 1 Execution Report

**Date:** 2026-04-27
**Deployed to:** https://winfredquek.com
**Production deployment URL:** https://crestbrick-consult-1h3qc60dm-winfredquekoc-3888s-projects.vercel.app
**Deployment status:** READY

---

## Items completed

### #4 robots.txt audit and fix
- Added explicit `Allow: /` directives for: GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, ClaudeUserBot, Claude-Web, anthropic-ai, PerplexityBot, Perplexity-User, BraveBot, Bravebot, Google-Extended, CCBot, Applebot-Extended, Bytespider.
- Preserved existing UTM-block lines.
- File: `/Users/winfredquek/crestbrick-consult/public/robots.txt`

### #5 llms.txt at root
- Created per llmstxt.org spec with markdown-list format.
- Sections: About, Tools, Insights, Foreign buyer guides, Districts and HDB towns, Listings and launches.
- ~75 URLs covered (under 80 cap).
- File: `/Users/winfredquek/crestbrick-consult/public/llms.txt`

### #6 Verify Person + RealEstateAgent JSON-LD in raw HTML
- Confirmed schema is rendered in raw HTML via `<script type="application/ld+json">` blocks (not JS-injected) on both `/index.html` and `/about.html`.
- Schema validation across all 244 HTML files: 221 OK, 0 broken JSON.

### #7 Canonical tags everywhere
- Audited every HTML in `public/`. All site pages now have `<link rel="canonical">`.
- Added canonicals to: `case-studies.html`, `audit-sample.html`.
- Pages without canonicals confirmed as either snippets (testimonials-carousel.html, tools-promo.html) or correctly redirected.

### #8 Schema validation
- Built and ran Python validator (`/tmp/schema_validator.py`).
- Result: 244 HTML files scanned, 221 with valid JSON-LD, 23 without schema (intentional: snippets, redirected legacy pages, 404, thank-you), **0 broken**.

### #9 vercel.json redirect chains
- Audited all 44 redirects. **No chains detected** (no `/a → /b → /c` paths).
- All redirects are direct: source → terminal destination.
- File: `/Users/winfredquek/crestbrick-consult/vercel.json` (no changes required).

### #10 Vercel rebuild + deploy
- `vercel --prod --yes` ran successfully.
- Deployment: dpl_6tf8bW63Xg8guFnQhdYfPBcGref5
- Aliased to: https://winfredquek.com

### #15 sameAs JSON-LD chain
- Updated homepage and about.html Person schema `sameAs` to:
  - https://www.linkedin.com/in/winfredquek
  - https://crestbrick.com
  - https://www.cea.gov.sg/aceas/public-register/eas?registrationNo=R073319H
- Wikidata not included (entity does not yet exist).

### #16 Wayback Machine submissions
- Successfully submitted (HTTP 302):
  - https://winfredquek.com/about
  - https://winfredquek.com/buyers/china
  - https://winfredquek.com/buyers/india
  - https://winfredquek.com/sitemap.xml
  - https://winfredquek.com/llms.txt
  - https://winfredquek.com/robots.txt
- Rate-limited (HTTP 520/429/timeout) on first attempt: homepage, /buyers/us, /buyers/uk, /buyers/fta-group, top calculator pages.
- The rate-limited URLs will be auto-archived by Wayback's regular crawl.

### #17 ProfessionalService schema
- Added `ProfessionalService` JSON-LD to homepage with:
  - `@id: https://winfredquek.com/#service`
  - `provider: { @id: https://winfredquek.com/#winfred }`
  - `serviceType`: Property advisory, Decoupling strategy, ABSD planning, HDB upgrade strategy, Foreign-buyer property strategy, Family-office property advisory
  - `areaServed: Singapore`

### #18 hasCredential / hasOccupation
- Verified `hasCredential` block on /about.html lists CEA R073319H with public register URL.
- Updated to use specific URL: `https://www.cea.gov.sg/aceas/public-register/eas?registrationNo=R073319H`.
- Added `hasOccupation: { @type: "Occupation", name: "Real Estate Broker", occupationLocation: "Singapore" }` to both homepage and about.html Person schema.

### #19 Foreign-buyer landing pages
Created 5 NEW pages in `public/buyers/`:
- `/buyers/us` — US buyer FTA exemption, FATCA, financing
- `/buyers/uk` — UK buyer 60% ABSD, SDLT 3% surcharge interaction, treaty
- `/buyers/china` — China buyer 60% ABSD, $50,000 USD SAFE outflow context
- `/buyers/india` — India buyer 60% ABSD, RBI LRS $250,000 family-pooling
- `/buyers/fta-group` — FTA-exempt countries (US, Switzerland, Liechtenstein, Iceland, Norway)
- Each page: 1500–2500 words, H1, FAQ block (FAQPage schema), Article schema, RealEstateAgent schema, internal links to /tools/absd and /contact, author attribution, dark theme + gold accents matching site.
- Added all 5 to `sitemap.xml`.

### #20 Author bio block on every blog post
- Built a shared author-bio component (photo placeholder via /img/winfred-hero.jpg, name, CEA R073319H, link to /about, 2-line bio).
- Injected before `</main>` in all 32 files in `public/insights/`.
- Script: `/tmp/inject_author_bio.py` — Modified: 32, Skipped: 0.

### #24 Lazy-load below-fold images
- Built and ran `/tmp/lazy_load.py` to add `loading="lazy"` to all `<img>` except the first per page (assumed hero).
- Result: existing images already had appropriate loading attributes; no additional changes needed.

### #28 Defer non-critical JS
- Audited all `<script src=>` tags across `public/*.html`.
- Found 1 non-deferred script: `<script src="/new-launches-data.js"></script>` on `new-launches.html`.
- Added `defer` attribute. All other scripts already use `defer` or `async`.

### #29 Explicit robots meta on Tier-1 pages
- Added `<meta name="robots" content="index,follow,max-image-preview:large" />` to 52 Tier-1 pages (homepage, /about, /contact, /faq, /listings, /services, all /tools/*, all /insights/*, all /buyers/*).
- 8 already had robots meta; preserved.

### #30 OG images
- Verified every page has `og:image`. Added default `https://winfredquek.com/img/og-image.jpg` to 15 pages that were missing it (including 404, sell, thank-you, tdsr-calculator etc.).
- `/img/og-image.jpg` already exists (25.8 KB) — used as default.
- Did not generate a new `og-default.jpg`; the existing `og-image.jpg` serves the same role.

---

## Files changed

### New files (6)
- `/Users/winfredquek/crestbrick-consult/public/llms.txt`
- `/Users/winfredquek/crestbrick-consult/public/buyers/us.html`
- `/Users/winfredquek/crestbrick-consult/public/buyers/uk.html`
- `/Users/winfredquek/crestbrick-consult/public/buyers/china.html`
- `/Users/winfredquek/crestbrick-consult/public/buyers/india.html`
- `/Users/winfredquek/crestbrick-consult/public/buyers/fta-group.html`

### Modified core files
- `/Users/winfredquek/crestbrick-consult/public/robots.txt` (AI crawler allow lines)
- `/Users/winfredquek/crestbrick-consult/public/sitemap.xml` (5 buyer URLs added)
- `/Users/winfredquek/crestbrick-consult/public/index.html` (schema graph: ProfessionalService, hasOccupation, sameAs)
- `/Users/winfredquek/crestbrick-consult/public/about.html` (sameAs, hasOccupation, CEA URL with reg number)
- `/Users/winfredquek/crestbrick-consult/public/case-studies.html` (canonical)
- `/Users/winfredquek/crestbrick-consult/public/audit-sample.html` (canonical)
- `/Users/winfredquek/crestbrick-consult/public/new-launches.html` (defer on new-launches-data.js)

### Bulk-modified
- 32 files in `public/insights/*.html` — author-bio block injected
- 52 Tier-1 pages — robots meta added
- 15 pages — og:image meta added

### Helper scripts (in /tmp, not deployed)
- `/tmp/schema_validator.py`
- `/tmp/inject_author_bio.py`
- `/tmp/lazy_load.py`
- `/tmp/robots_meta.py`
- `/tmp/og_check.py`

---

## Items deferred (with reason)

### #22 Convert hero images to AVIF
**Reason:** No AVIF/WebP encoder available locally (avifenc, cavif, cwebp not installed); `sharp` not in node_modules; hard constraint forbids new dependencies.
**Mitigating factor:** All hero images are already small (<50 KB JPG). Heavy LCP cost not present.
**Recommended next step:** Install `sharp` as dev dependency in a future task, or use Vercel Image Optimization API (which works without local tooling).

### #25 Preload critical fonts
**Reason:** Fraunces and Inter are loaded from Google Fonts CDN (fonts.gstatic.com) with rotating subset URLs, not from local woff2 files. Hardcoding the current woff2 URL would break when Google rotates the asset.
**Mitigating factor:** `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>` is already present on all primary pages — the appropriate optimization for CDN-hosted fonts.
**Recommended next step:** Self-host the specific Fraunces and Inter weights actually used (download woff2 files into `/public/fonts/`), then preload those local files. This requires committing font files and is best done as a dedicated task.

### Wayback Machine for some URLs
**Reason:** Archive.org applied rate-limiting (HTTP 520/429) on roughly half the submissions during a single session.
**Mitigating factor:** Critical URLs were saved (about, sitemap, llms.txt, robots.txt, china, india buyers).
**Recommended next step:** Re-run `curl "https://web.archive.org/save/<url>"` for each remaining URL with a 30+ second gap between calls in a future cron.

---

## Validation summary

- HTML parsing: clean (no syntax errors caught during write/edit operations)
- JSON-LD validation: 221 valid blocks, 0 broken, across 244 HTML files
- Vercel deploy: READY (status ok)
- Live URL: https://winfredquek.com — confirmed alias to production deployment

## Hard constraints honoured

- `.claude/settings.json` and allowlists not modified
- No nested research agents spawned
- No permission prompts paused for
- Visual styling tokens (--bg, --accent, --ink, --rule, etc.) untouched
- No existing functionality removed
- No new dependencies introduced
- Per-item failures (Wayback rate limits, AVIF tooling absence) logged and execution continued
