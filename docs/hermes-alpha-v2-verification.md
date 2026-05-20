## Hermes Alpha v2: Technical SEO Fix Verification Report

**Site:** winfredquek.com  
**Agent:** Hermes Alpha v2  
**Verification date:** 2026-05-19  
**Branch:** `claude/zealous-jang-86cef2`  
**Prior audit:** docs/hermes-alpha-technical-seo.md

---

## Verdict at a Glance

| Fix | Status | Severity of residual issue |
|-----|--------|---------------------------|
| 1. Title tags (QW3) | PARTIAL | Medium — all three audited titles now exceed 65 chars |
| 2. Double H1 (Issue #4) | PASS | — |
| 3. GA4 in `_enhance.js` (Issue #1) | PARTIAL | High — 27 of 156 insight pages still get no GA4 |
| 4. JSON-LD @graph merge (Issue #3) | PASS | — |
| 5. ABSD canonical (Issue #5) | PASS | — |
| 6. `_schema.js` image/email/logo (Issues #7, #8) | PASS | — |
| 7. Duplicate Article schema guard (QW4) | PASS | — |
| 8. og:image absolute URL (Issue #6) | PASS | — |
| NEW: 404 still in sitemap | FAIL (unaddressed) | Medium |
| NEW: Vercel Analytics double-load | FAIL (unaddressed) | Medium |
| NEW: news-sitemap.xml comma artifacts | FAIL (unaddressed) | Low |
| NEW: ABSD old URL still in sitemap | FAIL (unaddressed) | High — canonicalised away but still crawlable |
| NEW: 3 broken homepage links now resolved | PASS (23 pages created) | — |
| NEW: Title over-engineering introduces redundancy | NEW ISSUE | Medium |

---

## PASS — Fixes correctly applied

### Double H1 eliminated (Issue #4)

Verified: `grep -rc '<h1 aria-hidden="true"' public/insights/` returns empty — no page has that pattern. `tampines-mop-2026.html` now has exactly one `<h1>`. The hero display text was converted to a `<p>` tag with `aria-hidden="true"` and identical font-family/size/weight styling. The fix is correct and the accessibility pattern is sound.

**Score: PASS.** The implementation (converting to `<p>` rather than `<div role="heading" aria-level="1">`) is arguably slightly better than what the audit recommended, because a `<p>` carries no implicit heading role at all — less ambiguity for Google.

---

### JSON-LD @graph merge (Issue #3)

Verified: `index.html` has exactly **1** `<script type="application/ld+json">` block (1 `@graph`). The duplicate second block that previously redefined `#winfred` is gone. The single merged graph contains: RealEstateAgent (`#agent`), Person (`#winfred`), ProfessionalService (`#service`), WebSite (`#website`), LocalBusiness (`#crestbrick`). The comment `<!-- LocalBusiness merged into main @graph above -->` confirms intent.

`#winfred` now appears exactly twice in index.html: once as the entity definition (line 70) and once as a cross-reference `{ "@id": "https://winfredquek.com/#winfred" }` (line 115 `provider` field). This is correct JSON-LD cross-referencing practice.

**Score: PASS.**

---

### ABSD canonical fix (Issue #5)

Verified: `public/insights/absd-singapore.html` canonical is now `https://winfredquek.com/insights/absd-singapore-2026`. Title of the old page is `"ABSD Singapore 2026: The Hub, Winfred Quek | Winfred Quek"` — the old page is now clearly a redirect shell pointing to the 2026 version. The canonical is correct.

**Score: PASS.** However see the FAIL section: the old URL `insights/absd-singapore` is *still present in sitemap.xml*, which partially undermines this fix.

---

### `_schema.js` image/email/logo references (Issues #7, #8)

Verified:
- Line 16: `'image': 'https://winfredquek.com/img/winfred-hero.jpg'` — correct (was `winfred.jpg`)
- Line 19: `'email': 'winfredquekoc@gmail.com'` — correct (was `winfred@winfredquek.com`)
- Line 74: `'logo':{'@type':'ImageObject','url':'https://winfredquek.com/img/og-image.jpg'}` — correct (was `logo.png`)

All three broken references are now fixed. `_schema.js` passes `node --check` with no syntax errors.

**Score: PASS.**

---

### Duplicate Article schema guard (QW4)

Verified lines 59–80 of `_schema.js`. The guard logic:

```js
const hasArticleSchema = [...document.querySelectorAll('script[type="application/ld+json"]')]
  .some(s => { try { const d = JSON.parse(s.textContent); return d['@type'] === 'Article' || (d['@graph'] && d['@graph'].some(n => n['@type'] === 'Article')); } catch(e) { return false; } });
if (hasArticleSchema) { /* skip */ }
else { /* inject */ }
```

Structure is syntactically valid JavaScript (`node --check` confirms). Brace matching is correct: one `if`, one `else`, closing brace at line 80 is the `else` block, line 81 closes the `if (path.startsWith('/insights')...)` outer block. No unclosed braces. The guard also correctly handles the `@graph`-wrapped Article case (where insight pages store their Article via `d['@graph'].some(n => n['@type'] === 'Article')`).

**Score: PASS.** The implementation is correct and handles both direct `@type` and `@graph`-wrapped schemas.

---

### og:image absolute URL (Issue #6)

Verified:
```html
<meta property="og:image" content="https://winfredquek.com/img/og-image.jpg" />
<meta name="twitter:image" content="https://winfredquek.com/img/og-image.jpg" />
```

Both are now absolute. Fix applied correctly.

**Score: PASS.**

---

### 23 missing pages created (Issue #2 — partial resolution)

All 23 slugs that were in the sitemap but had no `.html` file now exist:
`one-vs-two-properties-singapore.html`, `second-property-timing-singapore.html`, `property-retirement-planning-singapore.html`, and all 20 others confirmed present in `public/insights/`. The three pages linked from the homepage now resolve.

**Score: PASS for page creation.** (Sitemap and Vercel Analytics issues remain as separate FAILs below.)

---

## PARTIAL — Fix applied but incomplete or suboptimal

### Title tags (QW3)

Verified titles for the three audited articles:

| Page | Title | Char count | Status |
|------|-------|-----------|--------|
| tampines-mop-2026 | `Tampines MOP 2026: Upgrading in the East — New Launch or Resale? | Winfred Quek` | **79** | OVER |
| absd-singapore-2026 | `ABSD Singapore 2026: The Definitive Reference, Winfred Quek | Winfred Quek` | **74** | OVER |
| decoupling-singapore | `Decoupling Singapore 2026: The Definitive Guide, Winfred Quek | Winfred Quek` | **76** | OVER |

The truncated placeholders ("Every |", "Upgrading in |") were fixed — the titles are now complete phrases. However **all three exceed the 65-character threshold** at which Google typically rewrites title tags in SERPs. The prior audit stated "Keep final title under 60 characters before the `|` suffix" — this guideline was not followed.

**Specific problems:**

1. `tampines-mop-2026` at 79 chars: Google will almost certainly rewrite. The em-dash character (`—`) also risks display issues in some SERP renderings.
2. `absd-singapore-2026` at 74 chars: "The Definitive Reference, Winfred Quek | Winfred Quek" — the brand name `Winfred Quek` appears **twice** in the title (once before the pipe, once after). This is redundant and costs 14 characters with no SEO benefit.
3. `decoupling-singapore` at 76 chars: Same double `Winfred Quek` redundancy issue.

**What was done right:** The dangling words ("Every |", "in |", "The |") that were the original problem are gone. Complete phrase titles are now present.

**What remains wrong:** Title lengths exceed the threshold where Google renders the full title. The double brand attribution (`Winfred Quek | Winfred Quek`) is a new artifact introduced by the fix.

**Recommended titles:**
- Tampines: `Tampines MOP 2026: New Launch or Resale? | Winfred Quek` (55 chars)
- ABSD: `ABSD Singapore 2026: The Definitive Guide | Winfred Quek` (56 chars)
- Decoupling: `Decoupling Singapore 2026: The Complete Guide | Winfred Quek` (60 chars)

**Score: PARTIAL.** Truncation fixed; length limit not achieved; redundant brand suffix introduced.

---

### GA4 in `_enhance.js` (Issue #1)

**What was done:** GA4 injection was added to `_enhance.js` with a correct guard:

```js
function initGA4() {
  if (document.querySelector('script[src*="googletagmanager"]')) return;
  // inject GA4 snippet
}
```

The guard correctly checks for an existing `googletagmanager` script tag before injecting, preventing double-fire on `index.html` (which has the static async GA4 script in the HTML). On pages with `_enhance.js`, GA4 will fire once.

**The gap:** 27 of 156 insight pages do **not** load `_enhance.js`. These are older/lightweight article templates (`sora-vs-fixed-mortgage-2026.html`, `shophouse-investment-singapore-2026.html`, `three-properties-legally-singapore-2026.html`, and 24 others). They load no JavaScript at all — just static HTML with inline JSON-LD schema. They receive **zero GA4 tracking**.

Additionally, note that the GA4 measurement ID `G-T6C46CFKCM` remains flagged with `<!-- TODO: Create GA4 property -->` comments on `index.html`. If this is still a placeholder ID, none of the GA4 injection — whether via `index.html` or `_enhance.js` — is recording real data. This was Issue #11 in the prior audit and remains unaddressed.

**Score: PARTIAL.** Guard is correct, the mechanism works for 129/156 pages. 27 pages still get no GA4. Placeholder ID concern unresolved.

---

## FAIL — Fix incorrect or not applied

### 404 page still in sitemap.xml (Issue #9 / QW5)

Verified: `https://winfredquek.com/404` is still present in `sitemap.xml` with `changefreq: weekly` and `priority: 0.5`. The prior audit flagged this as a trivial 1-line fix. It was not made.

**Impact:** Google crawls the 404 error page on a weekly basis per the sitemap instruction. Crawl budget waste continues.

---

### Vercel Analytics still double-loaded on index.html (Issue #10)

Verified: `/_vercel/insights/script.js` appears at **both line 34 and line 285** of `index.html`. The prior audit identified this as a 1-line fix. It was not made.

**Impact:** Vercel Analytics event count is doubled for homepage pageviews. Performance data for the homepage is statistically unreliable.

---

### `news-sitemap.xml` comma artifacts not fixed (Issue #14)

Verified: `grep -c ' ,  ' public/news-sitemap.xml` returns **17**. The ` ,  ` spacing artifact appears in every `<news:name>` and `<news:title>` element (e.g., `Winfred Quek ,  Singapore Property Advisory`, `Singapore cooling measures ,  chronological timeline and impact`).

**Impact:** Google News parser receives a malformed publication name on every news sitemap entry. Low severity but trivially fixable and not attempted.

---

### `insights/absd-singapore` still in sitemap (Issue #5 — partial)

Verified: `sitemap.xml` still includes `https://winfredquek.com/insights/absd-singapore` as an indexed URL. The canonical on that page now points to `absd-singapore-2026`, but the sitemap entry remains.

**Impact:** Google sees a sitemap URL that has a non-self canonical. This is a contradiction: Google's sitemap spec expects sitemap URLs to be the canonical versions. Having the non-canonical URL in the sitemap dilutes the canonical signal and wastes crawl budget. The prior audit explicitly said "Remove `/insights/absd-singapore` from `sitemap.xml`."

---

### Mobile nav `.html` suffix inconsistency not fixed (Issue #13)

Verified: Mobile nav links in `index.html` (lines 331–341) still use `.html` suffixes: `/about.html`, `/services.html`, `/listings.html`, etc. The desktop nav uses clean paths. Internal link equity continues to flow to both URL forms.

---

### Homepage meta description unchanged (Issue #12)

Verified: Meta description is still `"Singapore property investor advisor: portfolio-first approach, 9 years, 5 properties before 30, and the Property Portfolio Analysis methodology. CEA R073319H."` — 158 characters, unchanged from the prior audit finding. CEA number at the end remains at risk of truncation.

---

### Video preload hint not added (QW1)

Verified: No `<link rel="preload" as="video">` tag was added for `/video/hero-scroll.mp4`. The prior audit called this a sub-5-minute fix.

---

### hreflang tags not added (QW2)

Verified: No `hreflang` tags exist in `index.html` or `_enhance.js`. The geographic targeting signal remains absent.

---

## NEW ISSUES FOUND — Problems not in original audit

### NEW-1: Double brand attribution in titles (introduced by fix)

Titles in `absd-singapore-2026.html` and `decoupling-singapore.html` now read: `"... Winfred Quek | Winfred Quek"` — the brand appears both as part of the descriptive phrase and as the standard pipe-separator suffix. This is a direct consequence of the fix appending a full brand name before the `|` separator, then also appending it after. This redundancy:
- Wastes character budget (14 chars)
- Looks unprofessional in SERPs
- May cause Google to rewrite the title to remove perceived keyword stuffing

**Files affected:** `absd-singapore-2026.html`, `decoupling-singapore.html` (and likely other articles fixed in the same batch)

---

### NEW-2: 27 insight pages are completely JS-free (no GA4, no schema injection, no nav enhancement)

The 27 pages identified as missing `_enhance.js` (e.g. `sora-vs-fixed-mortgage-2026.html`, `shophouse-investment-singapore-2026.html`) also lack `_schema.js` and `_nav.js`. They use a stripped-down template with bare-minimum HTML. While their static JSON-LD appears well-formed, they miss:
- GA4 tracking
- Dynamic schema injection (RealEstateAgent base schema)
- Nav enhancements
- Any consent / bookmark / exit intent functionality

These pages appear to be generated from an older or alternate build template. They should be upgraded to use the full `_enhance.js` + `_schema.js` + `_nav.js` stack, or the GA4 snippet should be added inline.

---

### NEW-3: `absd-singapore.html` title is now `"ABSD Singapore 2026: The Hub, Winfred Quek | Winfred Quek"`

The old ABSD page (now canonical-pointed to 2026) has a title `"The Hub"` which is incomplete/unclear. Since this page no longer serves as a primary landing page (it canonicalises away), the title matters less for rankings — but it will still appear in browser tabs and any crawl that follows this URL before obeying the canonical. It reads as a content stub title, not a real page.

---

### NEW-4: `_schema.js` `RealEstateAgent` schema is injected on every page including index.html — potential conflict with the merged `@graph` on index.html

`index.html` already has a full `@graph` with a `RealEstateAgent` (`#agent`) entity statically defined. `_schema.js` injects a **separate** `RealEstateAgent` schema block dynamically on every page, including index.html. This creates two `RealEstateAgent` schemas on the homepage:

1. Static: the `#agent` node within the `@graph` block (correct, full, includes all CEA fields)
2. Dynamic: the standalone `agent` object injected by `_schema.js` at runtime

The Article guard in `_schema.js` was correctly added to prevent duplicate `Article` schemas. No equivalent guard exists to skip the base `RealEstateAgent` injection when it is already present statically. This is a lower-severity problem than the duplicate `Article` schema was, but it is still a duplicate entity definition.

**Recommendation:** Add a guard in `_schema.js` to skip the `agent` injection on pages that already have a static `@graph` with a `RealEstateAgent`.

---

## Debate: What I Would Have Done Differently

### On title fixes: length-first, not phrase-completion-first

The fix prioritised making the title phrase sound complete, which is correct. But the execution added words that pushed titles over 65 chars and introduced duplicate brand attribution. The correct order of operations is: (1) complete the phrase, (2) check if it fits in 55 chars before `|`, (3) if not, find a shorter equivalent phrase. "New Launch or Resale?" is a more compelling SERP hook than "Upgrading in the East — New Launch or Resale?" anyway.

### On GA4: the right fix was to add it to _enhance.js, but the scope was misunderstood

Adding `initGA4()` to `_enhance.js` is exactly correct. The oversight was not auditing which pages actually load `_enhance.js`. Before implementing, the correct verification step was: `grep -rL '_enhance.js' public/insights/ | wc -l`. That would have revealed 27 pages need the script tag added directly or need the `_enhance.js` import added to their template.

### On sitemap cleanup: it was four separate 1-line edits — not one

The four sitemap issues (404 entry, `absd-singapore` canonical mismatch, the now-resolved 23 missing URLs, and news-sitemap formatting) should have been batched as a single sitemap cleanup commit. All four are trivially small. Only the missing URL fix (creating 23 pages) was done; the three sitemap text fixes were not.

### On the @graph merge: the implementation is correct but incomplete in one direction

The `_schema.js` `RealEstateAgent` injection should also be guarded, similar to the `Article` guard. The pattern should be: "if page already has a static JSON-LD with this `@type`, skip dynamic injection." This was the right approach for `Article` and should be applied uniformly to the base `agent` schema.

---

## Priority Remediation List

Ordered by SEO impact:

1. **Add `_enhance.js` (and inline `_schema.js` import) to the 27 pages that are currently JS-free** — these pages have zero GA4, zero dynamic schema enhancement, and no nav functionality. 27 pages × 1 line each.
2. **Remove `insights/absd-singapore` from sitemap.xml** — 1 line delete.
3. **Remove `https://winfredquek.com/404` from sitemap.xml** — 1 line delete.
4. **Fix title redundancy** in `absd-singapore-2026.html` and `decoupling-singapore.html` (and audit all batch-fixed titles for `Winfred Quek | Winfred Quek` pattern). Target: ≤ 60 chars before pipe, no double brand attribution.
5. **Fix `tampines-mop-2026.html` title** to under 65 chars total.
6. **Remove Vercel Analytics duplicate** at line 285 of `index.html`.
7. **Fix `news-sitemap.xml`** — replace ` ,  ` with ` – ` across 17 occurrences.
8. **Standardise nav links** — remove `.html` from mobile nav hrefs in `index.html`.
9. **Trim homepage meta description** to ≤ 155 chars with CEA number preserved.
10. **Add `RealEstateAgent` guard to `_schema.js`** — skip injection when static `@graph` is already present.

---

*Hermes Alpha v2 verification complete.*
