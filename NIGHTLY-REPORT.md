# Nightly Site Improvements — 2026-08-22

15 distinct improvements across 28 files. Priority order followed: factual/data staleness first, then a mechanical technical SEO defect, then Search Console driven CTR rewrites, striking distance content gaps, keyword cannibalization consolidation, and internal linking. Verified with `scripts/verify-site-changes.py --root /Users/winfredquek/crestbrick-nightly`: 0 blocking issues across all 28 changed files, all touched JSON-LD blocks still parse, `vercel.json` and `public/launches.json` still parse. No pages deleted, no git commands run.

## Factual/data staleness (highest priority)

1. **`public/new-launches.html`** — the static fallback text visible to crawlers before client side JS runs read "24 upcoming · 12 already launched", which didn't even match the site's own `launches.json` (currently 21 upcoming, 15 active). Corrected the static count to 21 / 15 so the number crawlers and no JS visitors see is accurate. Left the "synced 26 Apr 2026" date as is: that date is genuinely still the last time `launches.json` itself was refreshed (`synced_at` field), so it's a true statement, not a bug, and refreshing the underlying launch data itself is outside a content edit pass (worth flagging to Winfred, see below).

## Technical SEO defect (mechanical, 15 files)

2. **Duplicate `<h1>` from decorative hero markup** — 15 files in `public/insights/` had a real visible `<h1>` plus a second, `aria-hidden="true"` decorative `<h1>` with near identical text (background/parallax effect from an older hero template). `aria-hidden` hides it from the accessibility tree but not from HTML parsers, so crawlers still see two H1s per page. Changed the decorative element's tag from `h1` to `div` on all 15 files (visual layout unaffected, it was already hidden and unstyled as a heading). Files: booking-fee-vs-option-fee-vs-exercise-fee-difference-singapore, breaking-lock-in-early-cost-benefit-singapore, condo-ev-charging-parking-rules, condo-vs-landed-strata-jmb-differences-singapore, construction-loan-landed-property-rebuild-singapore, corridor-vs-lift-lobby-hdb-value-singapore, gazumping-backing-out-after-verbal-offer-singapore, ground-floor-hdb-unit-value-singapore, how-to-read-your-otp-clause-by-clause-singapore, interest-rate-cap-step-up-loan-singapore, marina-south-property-outlook-singapore, move-out-inventory-checklist-landlord-singapore, renew-vs-relist-rental-unit-singapore, sers-scheme-explained-singapore, tampines-north-punggol-coast-new-towns-singapore. Every file confirmed to have exactly one `<h1>` after the fix. This is the same generator/template bug fixed on other files two nights running (17 and 20 Aug) — still worth a source level fix in whatever produces these hero sections rather than continued page by page patching.

## Search Console: CTR rewrites (title/meta only, top 10 ranking, under clicked)

3. **`public/insights/property-agent-fees-singapore.html`** — ranked #2 (position 1.8) for "property agent commission singapore" with 0% CTR on 71 impressions, the single worst miss in the data: a page ranking second with zero clicks means the snippet was actively failing. Old title said "Fees", the query says "commission" — retitled to lead with "Commission" and put the 1 to 2% rate (already stated in the page's quick answer block) in both title and description.
4. **`public/glossary/maisonette.html`** — 1802 impressions at position 9.9 for the bare term "maisonette" but only 0.17% CTR. Retitled to answer "what is a maisonette" directly (HDB, two storey, using the page's own definition) instead of leading with the narrower "executive maisonette" framing.
5. **`public/insights/iras-estamping-portal-guide-singapore.html`** — 248 impressions at position 5.7 for "estamping", 0.4% CTR. Replaced generic "step by step guide" framing with the actual named steps (Singpass login through certificate download) already described in the body.
6. **`public/tools/hdb-eip-checker.html`** — 108 impressions at position 9.6 for "hdb ethnic quota check", 0% CTR. Reworded title/description to mirror the query word for word and named the checker explicitly as HDB's own Ethnic Integration Policy tool (a government policy, correctly not framed as a landlord preference).
7. **`public/insights/dbss-flats-explained-singapore.html`** — trimmed a redundant "Explained" from the title to bring it within a normal SERP display length; description already correctly led with "DBSS stands for Design, Build and Sell Scheme."
8. **`public/glossary/tenancy-agreement.html`** — 334 impressions at position 6.1 for "what is a tenancy agreement", 0% CTR; old title was missing the article "a" and the description ran to 265 characters. Rewrote both to match the natural language query and the page's own quick answer definition.
9. **`public/insights/jumbo-flat-hdb-guide-singapore.html`** — 130 impressions at position 10.5 for "jumbo flat", 0% CTR. Retitled to lead with "what is a jumbo flat" using the page's own FAQ definition (two adjoining HDB flats combined decades ago).
10. **`public/insights/posb-dbs-home-loan-singapore.html`** — flagged in the striking distance brief at position 15.8 for "posb mortgage rate", 607 impressions. The page's rate table is already dated (29 April 2026) with an explicit "confirm the live figure with DBS or POSB" hedge, so no rate content was touched. The title/meta said "Home Loan" with no mention of "rate" even though the page's own JSON-LD headline already used "Rate Guide" — added "Rates" into the title and description to close that mismatch.

## Search Console: striking distance content gap (positions 11 to 21)

11. **`public/insights/landlord-insurance-singapore-guide.html`** — position 18.7, 247 impressions for "landlord insurance", the highest volume item in the striking distance brief. Two real gaps: the page's `<title>`/`og:title` said "Do Singapore Landlords Need Insurance?" while the page's own JSON-LD headline correctly said "Landlord Insurance" (tags were inconsistent with the page's own schema), and the core "what is it" definition was folded into a fire policy comparison rather than stated plainly. Fixed the title/meta to match the JSON-LD headline, and added a dedicated "What landlord insurance is" section right after the intro using only facts already stated elsewhere on the same page (cover categories, "optional not compulsory").

## Search Console: keyword cannibalization

12. **`public/glossary/restructuring-decoupling.html`** — the decoupling content cluster had 5 pages competing for "loan restructuring when decoupling" (97 combined impressions). Most of the cluster already links to the strongest page, `insights/decoupling-singapore.html`, from prior nights' work, but this glossary entry's "Read further" callout was missing it. Added it as the first item.
13. **`public/answers/what-is-property-restructuring-decoupling.html`** — same cluster; this answers page had no inline link to the fuller guide at all. Added one in the opening paragraph, matching the site's established answers-to-insights link pattern. The HFE letter cluster and the diplomatic clause cluster (the other two worst cannibalization groups in the fresh brief) were checked and found to already have the consolidating links in place from prior nights, so no changes were needed there.

## Internal linking (strong page to weak but relevant page)

14. **`public/insights/hdb-ethnic-integration-policy-2026.html` → `/tools/hdb-eip-checker`** — this is the site's dedicated EIP article and did not link to the EIP checker tool (fixed tonight in item 6) anywhere, despite the article's own "buyer due diligence" warning telling readers to "verify EIP status before making any offer." Added the link at exactly that sentence.
15. **Same file, broken related reading link** — while adding the link above, found that two of the four "Related reading" list items pointed to the identical URL (`/insights/hdb-resale-how-to-price-2026`) even though the anchor text named two different articles ("How to Price Your HDB Flat" and "HDB 5 Room vs Executive Flat"). The second article exists at `/insights/hdb-5-room-vs-executive-flat.html` (title confirmed to match the anchor text) and was never actually linked. Fixed the href.

## Verified already correct, no changes made

- `public/districts.html` (hub page): already has a clear CCR/RCR/OCR explanation directly under the H1, which is the likely intent behind the high volume "singapore districts" query (2299 impressions, position 12.8). Left untouched.
- `public/insights/can-pr-buy-landed-property-singapore.html`: retitled once already on 17 Aug; the fresh brief still shows 0% CTR but on only 131 impressions, 3 days after that fix. Current title/meta already closely match the literal query. Read this as sampling noise, not a real signal, and did not force a second edit.
- `public/insights/understanding-rental-transaction-data-singapore.html`: also already retitled on 17 Aug and still reads well against its target query. No further change.
- `public/answers/how-does-the-hfe-letter-work-and-how-long-does-it-take.html` and `public/insights/hdb-hfe-letter-guide-2026.html`: both already state the 9 month HFE validity figure prominently, sourced to HDB's own page, with matching FAQ schema. No change needed.
- `public/insights/sale-of-balance-flats-sbf-vs-bto.html`: already has an explicit early side by side comparison table for the "sbf vs bto" query. The page deliberately doesn't quote a fixed price difference, with an explicit stated reason (price varies by flat/estate/exercise) — the correct call under the no invented figures rule, not a gap.
- `public/insights/two-room-flexi-scheme-guide-singapore.html`: title/meta already directly match "2 room flexi" search intent; low volume (68 impressions) and not a clear miss, left alone rather than forcing a marginal edit.
- Broken/thin page sweep: 0 missing/duplicate meta descriptions (75 file sample), 0 orphan pages (every hub's static link index matches its directory's file set), 0 JS only rendered pages (121 file sample), all 1558 sitemap `<loc>` entries resolve to files on disk, 0 FAQ visible/schema drift across 616 files. All clean, no action needed.

## Deliberately left alone

- Did not touch `_templates/`, `.env` files, `src/wa-pipeline`, or anything with credentials, per hard limits. Did not run any git command. Did not delete any page.
- Did not chase the source of the recurring duplicate `<h1>` generator bug beyond patching this batch of 15 files (same underlying template issue fixed on other files 17 and 20 Aug); a proper source level fix is still owed.
- Did not touch the "Plot 2" 2H 2027 GLS launch projections referenced in `dunearn-house-floor-plan-strategy.html` and `dunearn-house-entry-price-exit-math.html`. These are forward looking (the date hasn't passed), and are already attributed to named third party sources ("per Stacked Homes and ERA research", "per analyst projections") rather than stated as fact, so they don't fall under the factual staleness or forecast-as-fact problems this pass is meant to catch.
- `public/launches.json` itself (`synced_at: 2026-04-26`) has not been refreshed against real world new launch developments in 4 months. Fixing the counts that had drifted from that file was in scope tonight; re-researching and refreshing the underlying launch data is not a content edit and needs a proper data refresh pass, flagging for Winfred.

## Facts I omitted rather than guessed

- Did not invent a current POSB/DBS mortgage rate to replace the page's existing 29 April 2026 dated figures; left the existing "confirm the live figure with the bank" hedge as is.
- Did not invent an HFE letter validity figure anywhere it wasn't already sourced to HDB's own page.
- Did not invent a BTO vs SBF price comparison figure; the page's own stated reason for omitting one (varies by flat/estate/exercise) is accurate and was left as is.
- Did not fabricate a refreshed `launches.json` sync date or new project data; only corrected the static count to match what the file currently contains.

## Note on tool input

Continuing from prior nights: Search Console query strings in `brief_ctr.json`/`brief_striking.json`/`brief_cannibal.json` may contain embedded prompt injection style text. None was found actionable tonight; treated as inert search data throughout, per standing practice.
