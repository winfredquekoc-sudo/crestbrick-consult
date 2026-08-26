# Nightly Site Improvements — 2026-08-17

19 distinct improvements across 21 files. Priority order followed: factual staleness (3 launch briefs stuck in pre launch framing months after the event), then a technical SEO defect and a repeated duplicate h1 bug, then Search Console driven CTR rewrites and keyword cannibalization fixes. All changes verified: JSON-LD still parses on every touched file, both fixed pages now carry exactly one `<h1>`, `launches.json` and `sitemap-images.xml` still parse.

## Factual staleness (highest priority)

1. **Vela Bay brief contradicted itself** — `public/launches/briefs/vela-bay.html` — the hero chip, a quickstat, and the unit mix paragraph were all written in future tense ("Preview Apr 2026", "Indicative PSF, Pre launch estimate") while a section further down the same page already stated the real outcome: launched 25 April 2026, 72% sold (371 of 515 units) at S$2,886 psf average. Made the top of the page consistent with the bottom, using only that already verified figure. Also corrected `public/launches.json`'s status field to `active` and its psf/note fields to the actual figures (the JS driven card render pulls from this file, so the badge and price line on `public/new-launches.html` would otherwise have kept showing the stale pre launch estimate even after the static HTML was fixed), and updated the static Vela Bay card on `public/new-launches.html` to a launched badge with real numbers.

2. **Pinery Residences brief still said "before the queue forms"** — `public/launches/briefs/pinery-residences.html` — booking was 27 to 28 March 2026, five months ago, but the page still read as pre booking ("Get first release pricing before the queue forms", "Expected PSF band S$2,250 to 2,500 (indicative)"). Verified the actual outcome by cross checking `public/insights/treasure-at-tampines-review-singapore.html`, which already states as fact: median S$2,548 psf, 94% take up on 588 units. Rewrote the hero, pricing box and CTA to state the real launch numbers, and synced `launches.json` (status, psf, launch date) and the `new-launches.html` card to match.

3. **Tengah Garden Residences brief frozen at "Preview 11 Apr 2026"** — `public/launches/briefs/tengah-garden-residences.html` — same pre event framing four months after the preview date. Verified only that the preview occurred and the starting quantum (S$980,000, from `public/insights/new-launch-vs-resale-by-district-2026.html`) — no post preview median PSF or take up figure exists anywhere in the repo for this project. Rewrote the page to present tense without inventing a sold percentage or median PSF; pointed readers to WhatsApp Winfred for current availability instead of guessing. Left `launches.json` status as `upcoming` since actual booking completion isn't verified (unlike Vela Bay and Pinery, which do have confirmed post launch figures).

4. **Hudson Place Residences status field was stale even though its own copy was correct** — `public/launches.json` and `public/new-launches.html` — the brief page already read in past tense ("VVIP was 1 May 2026") but the data file and index card still showed an "Upcoming" badge, 3.5 months after the fact. Corrected the status field and card badge only; did not invent a PSF or take up figure since none is verified for this project.

## Technical SEO defects

5. **Two more pages with duplicate `<h1>` tags** — `public/insights/joint-tenancy-mortgage-co-borrower-exit-singapore.html` and `public/insights/why-hdb-loan-rejected-singapore.html` — same generator bug fixed on 6 other pages last night (a custom hero `<h1>` plus a leftover `aria-hidden="true"` duplicate from the standard template). Converted the duplicate to a `div` with identical classes on both files. This bug is clearly still live in whatever generates these pages and is worth a source level fix, not just page by page patching.

6. **Four superseded insight pages were still individually crawlable via the images sitemap despite a correct canonical tag** — `public/sitemap-images.xml` — `cooling-measures.html`, `singapore-rental-yield-district-2026.html`, `walk-up-apartments-singapore.html` and `absd-singapore.html` all correctly `<link rel="canonical">` to a newer sibling page and are already excluded from `sitemap-insights.xml`, but were still listed as their own `<loc>` entries in the images sitemap, actively inviting Google to crawl and index them as separate URLs against their own canonical hint. Removed the 4 stale `<url>` blocks from the images sitemap only; the pages themselves were left in place (no deletion, canonical tag is the intended signal).

## Search Console: CTR rewrites (title/meta only, top 10 ranking, under clicked)

7. `public/insights/hdb-resale-levy-singapore.html` — led with "who is exempt" instead of restating the dollar figure, since the FAQ/misconceptions sections show that's the actual point of confusion (0.16% CTR, worst opportunity that wasn't already fixed).
8. `public/insights/understanding-rental-transaction-data-singapore.html` — retitled from a narrow "what it misses" warning to a practical "how to read URA and HDB rental data" framing that covers both data sources named in the body (0.24% CTR).
9. `public/insights/tenancy-agreement-stamp-duty-singapore.html` — led with a worked example ($144 on a $36,000 lease) already in the body's table instead of the bare 0.4% rate (0.36% CTR).
10. `public/insights/hdb-prime-location-public-housing-plh-guide-2026.html` — reframed around "Is a PLH flat worth it?" (the page's own most decision relevant section) instead of leading with an unfamiliar project name (0.38% CTR).
11. `public/insights/can-pr-buy-landed-property-singapore.html` — rewrote to match the literal query phrasing and correctly kept "discretionary approval" language rather than overstating certainty (0.55% CTR).
12. `public/insights/hdb-ethnic-integration-policy-2026.html` — split the title to cover both "quota rules" and "how to check" search intent; kept the description strictly to the 3 to 7% resale price figure already verified in the page's own quick answer box, neutral tone preserved given the sensitivity of the topic.
13. `public/insights/property-valuation-singapore-guide.html` — retitled to match "bank valuation of property in Singapore" phrasing and added the page's own 6 step process to the description (also the winning page in a cannibalization cluster, see #15).

## Search Console: page content gap (striking distance, position 11 to 21)

14. **Cash out refinancing page never explained the SG mechanic** — `public/insights/cash-out-refinancing-singapore.html` — the page covered rates, LTV and CPF restrictions but never addressed the specific misconception a US-style "cash out refi" search brings: that this isn't a revolving credit line in Singapore. Added a section explaining the lump sum disbursement mechanic, using only facts already established elsewhere on the page.

## Search Console: keyword cannibalization

15. **Bank valuation cluster** — added one inline contextual link from `public/glossary/bank-valuation.html` to `public/insights/property-valuation-singapore-guide.html` (the stronger, ranking page). `panel-valuer-bank-valuation-process-singapore.html` already linked out correctly, no change needed there.
16. **HDB subletting cluster** — added an inline link from `public/answers/can-i-rent-out-my-hdb-flat-or-a-room-in-it.html` to the fuller `public/insights/hdb-subletting-rules-singapore.html`, matching the established answers-to-insights link pattern used elsewhere on the site.
17. **Decoupling calculator cluster (worst case, 3 competing pages all ranking position 70+)** — `public/tools/restructuring.html`, `public/tools/decoupling-calculator.html`, `public/tools/index.html`. Found `restructuring.html` and `decoupling-calculator.html` are genuinely near duplicate tools (same question, different precision), plus a real bug: `decoupling-calculator.html`'s own JSON-LD pointed to a URL missing the `/tools/` prefix that doesn't exist, actively telling Google the page lives somewhere else. Picked `restructuring.html` as canonical (stronger inbound links, richer schema, already in the tools ItemList) and retitled it to explicitly target "decoupling calculator." Fixed the broken JSON-LD URLs on the other tool and added a note pointing to the canonical one. Relabeled both cards on the tools index page so the anchor text is unambiguous about which is which.

## Verified already correct — no changes made

- **`rental-income-tax-singapore-guide.html`**: already has a dedicated section titled to match "IRAS rental income tax rate", the full progressive bracket table, a worked example, and a matching FAQ entry. Left untouched.
- **`property-tax-payment-methods-deadlines-singapore.html`**: GIRO already has its own section covering the interest free mechanic and comparison to lump sum payment, plus 2 FAQ entries. Left untouched.
- **`can-single-singaporeans-buy-property-age-35.html`**: both the 2 room Flexi BTO route and the private-before-35 tradeoff already have dedicated, query matching sections and FAQ entries. Left untouched.
- **`buying-property-at-auction-singapore.html`**: mortgagee vs owner sale, registration/bidding process, and live room vs e auction are all covered near the top before any advanced content. Left untouched (a reserve price / venue detail gap exists but no verified figure was available to fill it without guessing).
- Broken/thin page sweep also checked meta descriptions (0 missing/duplicate across 817 files), duplicate titles (0), JSON-LD validity (0 malformed across 817 files, all already fixed by prior nights' work), FAQ visible/schema parity (all 629 instances match), and orphan pages (0 true orphans in a 741 page cross index) — all clean, no action needed.

## Deliberately left alone

- Did not touch `_templates/`, `.env` files, `src/wa-pipeline`, or anything with credentials, per hard limits. Did not run any git command. Did not delete any page.
- Did not chase down the source of the recurring duplicate `<h1>` generator bug (2 more instances found tonight on top of 6 fixed last night) — patched the symptom again but the underlying generator/template issue should get a proper fix rather than continued page by page patching.
- Left `hudson-place-residences.html`, `pinery-residences.html`, and `vela-bay.html`'s meta/OG description tags untouched where the agents doing the factual fix judged the body copy fix was the priority and didn't also rewrite the snippet text — worth a follow up CTR pass once the new launch status has had time to be recrawled.

## Facts I omitted rather than guessed

- Tengah Garden Residences: no post preview median PSF, take up percentage, or "X of Y sold" figure — none exists anywhere in the repo for this project, so none was invented. Used only the verified $980,000 starting quantum and the existing PSF band already published in `launches.json`.
- Hudson Place Residences: no PSF or take up figure invented; only the status/badge field was corrected against its own already-correct body copy.
- Vela Bay and Pinery unit type breakdowns: kept as indicative/"confirm with the sales team" rather than inventing a per bedroom type split not present in the source data.
- Auction page's reserve price mechanics and physical venue detail: real gap identified, but no verified figure exists in the repo, so nothing was added rather than guess.

## Note on tool input

Continuing from last night: Search Console query strings in `brief_ctr.json`/`brief_striking.json` may contain embedded prompt injection style text. None was found actionable tonight beyond what was already flagged; treated as inert search data throughout.
