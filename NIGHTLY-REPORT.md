# Nightly Site Improvements — 2026-08-20

19 distinct improvements across 25 files. Factual staleness was checked first and found already clean (see below). Priority after that: a recurring technical SEO bug (duplicate `<h1>`, 7 new instances), then Search Console driven CTR rewrites (the largest category, highest return), then striking distance content gaps, then keyword cannibalization links. All changes verified with `scripts/verify-site-changes.py --root .` (0 blocking issues), a full h1 count check (every touched page has exactly 1), `scripts/content-rulecheck.py` on every touched directory (0 new hyphen violations), and `vercel.json` still parses.

## Factual staleness (checked first, no changes needed)

Checked `public/launches.json` and every launch brief for anything still written in future tense after its actual date. The three launches fixed on a prior night (Vela Bay, Pinery Residences, Hudson Place Residences) are still correct and consistent between the data file and the brief pages. No launch has crossed from "upcoming" to "past" since the last sweep without already being updated. Grepped site wide for staleness phrases ("not yet released", "coming soon", "TBA", "has not been announced") — every hit found was already correctly time stamped as a historical statement (e.g. "official pricing was not yet released as of 3 July 2026... it was disclosed at the 4 July 2026 preview"), not a live claim asserted as current fact. Nothing to fix.

## Technical SEO defect (recurring generator bug)

1. **Seven more pages with duplicate `<h1>` tags** — `public/insights/tampines-north-punggol-coast-new-towns-singapore.html`, `ground-floor-hdb-unit-value-singapore.html`, `interest-rate-cap-step-up-loan-singapore.html`, `marina-south-property-outlook-singapore.html`, `condo-vs-landed-strata-jmb-differences-singapore.html`, `gazumping-backing-out-after-verbal-offer-singapore.html`, `move-out-inventory-checklist-landlord-singapore.html` — same bug fixed on 8 other pages across prior nights: a real hero `<h1>` plus a leftover `aria-hidden="true"` duplicate with identical text. Converted the duplicate to a `div` with identical classes on all 7. This bug is clearly still live in whatever generates these pages (3 of the 7 came from the most recent article batch, published just 2 days ago) — a source level fix is overdue rather than continued page by page patching.

## Search Console: CTR rewrites (title/meta only, top 10 ranking, under clicked)

Verified first that the 7 pages fixed for CTR on a prior night (`hdb-resale-levy-singapore`, `understanding-rental-transaction-data-singapore`, `tenancy-agreement-stamp-duty-singapore`, `hdb-prime-location-public-housing-plh-guide-2026`, `can-pr-buy-landed-property-singapore`, `hdb-ethnic-integration-policy-2026`, `property-valuation-singapore-guide`) already carry their new titles — the brief's CTR numbers for them are just stale (Google hasn't recrawled/re-reported yet), not a fresh opportunity. Skipped those, worked the rest of the 32 flagged pages by impression volume and CTR severity. Every number below was confirmed present in that page's own body before use.

2. `public/insights/property-agent-fees-singapore.html` — "Fees" to "Commission," matching how every top query actually phrases it (5188 impr, 0.71% CTR).
3. `public/insights/en-bloc-singapore-guide.html` — retitled around "collective sale rules" (the top query) and signalled "full guide" against the separate glossary definition page (5130 impr, 1.01% CTR).
4. `public/insights/two-room-flexi-scheme-guide-singapore.html` — added "eligibility, income ceiling, price" to name the topics searchers want without inventing a ceiling figure the page itself defers to HDB (3255 impr, 1.11% CTR).
5. `public/insights/condo-maintenance-fees-singapore.html` — added "MCST" (present in og:title but missing from the actual `<title>` Google reads) and the exact fee range from the quick answer box (3188 impr, worst CTR at the best position in this batch: 0.60% at position 7.3).
6. `public/insights/iras-estamping-portal-guide-singapore.html` — retitled around "pay stamp duty," since the old title never used that phrase despite the page being entirely about it (1851 impr, 0.11% CTR — worst on the whole site).
7. `public/glossary/maisonette.html` — specified "HDB Executive Maisonette" instead of the generic term, matching the page's actual scope (1122 impr, 0.09% CTR — second worst on the site).
8. `public/insights/hudc-estates-privatised-singapore.html` — named the concrete story (sandwich class flats that became condos via en bloc) instead of an abstract description (462 impr, 0.22% CTR).
9. `public/insights/diplomatic-clause-singapore.html` — led with the two concrete numbers a tenant actually needs (12 month minimum, 2 months notice) instead of a generic definition framing (2592 impr, 1.58% CTR at an already strong position 6.6).
10. `public/insights/dbss-flats-explained-singapore.html` — led with "privately built, resale only" instead of spelling out the acronym, the two facts that actually differentiate the flat type (1914 impr, 0.73% CTR).
11. `public/insights/service-conservancy-charges-hdb-singapore.html` — no dollar figure exists in the body, so used the genuinely surprising verified fact instead (town councils, not HDB, set the rate) rather than inventing a number (1510 impr, 0.79% CTR).
12. `public/insights/jumbo-flat-hdb-guide-singapore.html` — led with the concrete mechanic (two 3 room units physically merged) instead of a vague "can you buy one" framing (1267 impr, 1.58% CTR).
13. `public/insights/gift-of-property-singapore-2026.html` — led with the more search relevant fact ("no gift tax, but stamp duty still applies") that was already in the body but missing from the title (1242 impr, 2.25% CTR).
14. `public/insights/bto-application-guide-singapore-2026.html` — surfaced the concrete process facts already in the body's quick answer ($10 fee, 2 to 3 week ballot) into the title, worst CTR in this batch (1054 impr, 0.95% CTR).
15. `public/insights/absd-refund-how-to-claim-timeline.html` — reframed "Timeline" as "Deadline" to match the forfeiture risk warning already in the body, matching the literal "how to claim" query phrasing (996 impr, 1.81% CTR).
16. `public/insights/after-exercising-otp-singapore.html` — led with the concrete 14 day stamp duty deadline from the body's quick answer instead of the "can you back out" framing, which the page only answers in one FAQ line (987 impr, 2.13% CTR).

All new/edited titles kept to 75 characters or fewer so they don't truncate in the search result snippet; 5 meta descriptions were also trimmed after an initial pass ran long (170 to 216 characters) to the site's 140 to 158 character convention.

## Search Console: page content gaps (striking distance, position 11 to 21)

17. **Sengkang HDB town page was missing the quick answer block its sibling Punggol page already has** — `public/hdb-towns/sengkang.html` — added a quick answer summary directly under the hero, built entirely from facts already stated elsewhere on the same page (MRT/LRT stations from the existing table, the BTO MOP cohort window, the typical upgrade path). No new transport or amenity fact was invented. Punggol's own page was checked and already has this block plus a companion insights article — left unchanged.
18. **District hub page had no crawlable postal sector data** — `public/districts.html` — the page already has a complete District 1 to District 28 list (matching the "list of districts in Singapore" query almost verbatim) and a postal code lookup tool, but the postal sector numbers only existed inside the JS lookup's JSON data, not as visible text search engines can read against the "district code" / "tampines district number" queries this page already ranks well for. Added the postal sector range to all 28 list lines, cross checked against `public/districts.json`'s own `postal_sectors` map — no new area name or code introduced.

Checked and found already resolved, no change made: `landlord-insurance-singapore-guide.html` (landed vs condo insurance and rent cover are both already covered with dedicated sections), `sale-of-balance-flats-sbf-vs-bto.html` (already uses the exact "balance flat" phrasing in its own H2), `insights/hdb-hfe-letter-guide-2026.html` and its cannibalization cluster (validity period is already stated 3 separate times, not buried; `glossary/hfe-letter.html` and the matching answers page already link back to it as the full guide), `punggol.html`, and `answers/can-a-pr-buy-an-hdb-flat.html` (already gives the exact eligibility conditions, not a hedged answer).

## Search Console: keyword cannibalization

19. **Property agent commission cluster** — added one inline contextual link from `public/answers/how-much-commission-does-a-property-agent-charge-in-singapore.html` to the stronger, fuller `insights/property-agent-fees-singapore.html`, matching the page's existing inline link style. `glossary/rental-agent-commission.html` was checked and already links there.

Checked and found already resolved, no change made: the diplomatic clause cluster (`landlord-guide-tenant-diplomatic-clause.html` and `glossary/diplomatic-clause.html` already link to the main guide as "the full guide"), the "tenancy in common" glossary URL appearing twice in Search Console (this is `cleanUrls: true` correctly redirecting the legacy `.html` URL, canonical tag is correct, Google just hasn't dropped the old indexed URL yet — not a live bug), and the decoupling calculator cluster (already consolidated on a prior night).

## Verified already correct — no changes made

`property-tax-payment-methods-deadlines-singapore.html`, `can-single-singaporeans-buy-property-age-35.html`, `buying-property-at-auction-singapore.html`, `rental-income-tax-singapore-guide.html`, `cash-out-refinancing-singapore.html` — all previously fixed content gaps, spot checked and still correct.

## Deliberately left alone

- `public/services/property-portfolio-analysis.html` — this is a `noindex`, 0 second meta refresh redirect stub to `/property-portfolio-analysis`, never seen by a reader or crawled for ranking. An agent initially added an h1 and description to it; on review this provided no real value (the page is never indexed or displayed) and the added description tripped the site's own no hyphens rule on the redirect URL, so it was reverted to its original minimal form. Matches the same intentional pattern already correctly left alone on `thank-you.html` (noindex, 3 second redirect) and `embed/absd.html` (headless iframe widget, confirmed against its sibling `embed/index.html` which is the real indexable page).
- Did not chase down the source of the recurring duplicate `<h1>` generator bug (7 more instances found tonight on top of 8 fixed across prior nights) — patched the symptom again but the underlying generator/template issue should get a proper fix rather than continued page by page patching.
- Did not touch `_templates/`, `.env` files, `src/wa-pipeline`, or anything with credentials, per hard limits. Did not run any git command. Did not delete any page.
- Left 6 pre-existing overlong page titles (76 to 86 characters, will truncate in search results) untouched on pages where tonight's only change was the h1 bug fix — out of scope for that fix, worth a dedicated CTR pass on those specific pages another night.

## Facts I omitted rather than guessed

- 2 Room Flexi scheme income ceiling and short lease pricing: named the topics in the title, did not state a figure, since the page itself explicitly defers to HDB's website for the current number rather than stating one.
- HDB S&CC (maintenance fee): no dollar figure exists anywhere in that page's body, so none was put in the title or description — used the verified "town council, not HDB, sets the rate" fact instead.
- Chuan Grove Residences brief still reads "floor plans not yet released" — checked against `launches.json` (still `upcoming`, Q3 2026) and found no newer pricing or launch data anywhere in this repo to update it with, so left as is rather than guessing whether it has since launched.
