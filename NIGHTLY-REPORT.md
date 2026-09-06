# Nightly Site Improvements, 2026-09-07

15 distinct improvements across 24 files. Priority order followed: factual staleness in two launch briefs, a recurring technical SEO defect, Search Console driven CTR rewrites grounded only in facts already present on each page, striking distance content gaps, and internal linking. Two candidate fixes (a CTR rewrite and a cannibalization consolidation) were investigated and deliberately left alone; reasons are noted at the end.

## Factual staleness

1. **Penrith (Margaret Drive GLS) showed as "Upcoming" months after it launched** - `public/launches.json` and `public/new-launches.html` - the brief page itself already read correctly in past tense ("Launched Oct 2025", "97% sold"), but the data file and the static card on the new launches index still showed status "upcoming" and an indicative pre launch PSF range. Corrected status to active, PSF to the actual "S$2,800 psf avg (97% sold at launch)", and the launch date to note the October 2025 launch, matching the brief page's own figures. Updated the card badge from Upcoming to Launched.

2. **Thomson Modern (Upper Thomson Parcel B) had the same status bug, plus a naming wrinkle** - `public/launches.json` and `public/new-launches.html` - the brief page already correctly explains the project launched August 2025 as "Springleaf Residence" (its official marketed name) at 92% take up, and already canonicals to the springleaf-residence brief. Only the data file and index card were stale, still marked upcoming with an indicative PSF range. Corrected status to active, PSF to "S$2,175 psf avg (92% sold at launch)", and added the Springleaf Residence name to the launch date note so the card is not confusing next to the brief.

## Technical SEO defect

3. **18 insights pages carried two `<h1>` tags each** - a recurring generator bug (also caught and patched on 8 other pages in the two prior nightly runs) where a styled hero `<h1>` is followed by a leftover `aria-hidden="true"` duplicate `<h1>` with the identical text, from the standard article template. Converted the duplicate to a `<div>` with the same classes on all 18 files: approval-in-principle-aip-process-explained-singapore, breaking-lock-in-early-cost-benefit-singapore, booking-fee-vs-option-fee-vs-exercise-fee-difference-singapore, construction-loan-landed-property-rebuild-singapore, condo-ev-charging-parking-rules, condo-vs-landed-strata-jmb-differences-singapore, corridor-vs-lift-lobby-hdb-value-singapore, gazumping-backing-out-after-verbal-offer-singapore, ground-floor-hdb-unit-value-singapore, hdb-home-improvement-programme-hip-guide, how-to-read-your-otp-clause-by-clause-singapore, interest-rate-cap-step-up-loan-singapore, marina-south-property-outlook-singapore, move-out-inventory-checklist-landlord-singapore, renew-vs-relist-rental-unit-singapore, sembawang-canal-waterfront-pocket-guide, sers-scheme-explained-singapore, tampines-north-punggol-coast-new-towns-singapore. Verified every file now has exactly one `<h1>`. This bug is clearly still live in whatever produced these pages; it is worth a source level fix rather than continued page by page patching each night.

4. **thank-you.html had no `<h1>` at all**, only an `<h2>`. Changed the heading tag to `<h1>` with matching inline size override so the visual design is unaffected. Low impact since the page is noindex, but free to fix and technically correct.

## Search Console: CTR rewrites (title and meta only, grounded in facts already on the page)

5. `public/insights/iras-estamping-portal-guide-singapore.html` - "estamping" draws 568 impressions and 1 click (0.17% CTR) because searchers want a definition, not a walkthrough. Retitled to lead with "What Is Estamping?" and rewrote the meta to open with the definition already stated in the page's own "What e Stamping is" section.

6. `public/insights/property-valuation-singapore-guide.html` - ranks 25.5, off page one, for "valuer of property" because the title framed the page around bank valuation process, not the role of a valuer. Retitled to "What Is a Property Valuer?" and rewrote the meta to define the role using the page's own quick answer (MAS approved valuers, loan based on the lower of valuation or price, COV of $20,000 to $80,000).

7. `public/insights/rental-income-tax-singapore-guide.html` - ranks well (5.9) but 0.19% CTR because the title "IRAS Rules" is broad while the actual query is "deductible expenses". Retitled and re described around the page's own deductible expenses table (mortgage interest, property tax, agent commission, maintenance, furniture depreciation qualify; renovation and mortgage principal do not).

8. `public/insights/hdb-hfe-letter-guide-2026.html` - top under clicked query is "hfe validity", but the title only said "Explained". The page already states validity is 9 months in three separate places. Retitled to surface the 9 month figure directly.

9. `public/insights/hdb-prime-location-public-housing-plh-guide-2026.html` - queries include specific project names (Ulu Pandan Banks, River Peaks) that the page covers in its comparison table but the meta never mentioned. Added the project names (Rochor, Ulu Pandan Banks, River Peaks) to the meta description, all already named in the page body. Did not add an application rate figure since none is verified anywhere on the page.

## Search Console: striking distance content gaps (position 11 to 21)

10. `public/insights/posb-dbs-home-loan-singapore.html` - top query "posb mortgage rate" (1385 impressions, position 17.5) was not answered until deep in a rate table; the quick answer paragraph, which is what search snippets tend to surface, only described package types without a number. Added the actual figure already sourced elsewhere on the page (POSB's 3 year fixed HDB package, 1.70% per annum as of the 29 April 2026 survey) directly into the quick answer, with the existing hedge that rates move.

11. `public/insights/landlord-insurance-singapore-guide.html` - "landlord insurance" (1276 impressions, position 16.5) is never answered on cost, and the page has no premium figure anywhere to draw from. Added an honest new section, "How much does landlord insurance cost", explaining why one figure would be misleading and pointing readers to get quotes from a licensed insurance broker, since underwriting and pricing sit outside what a CEA registered salesperson can advise on. No number invented.

12. `public/insights/guarantor-home-loan-singapore.html` - the page explains why banks want a guarantor and what a guarantor is agreeing to, but never answers the literal ranking query, "what do I need to be a guarantor" (position 63.2, essentially invisible). Added a new section, "What a bank typically looks for in a guarantor", covering the generally known, non numeric criteria (income and credit standing, usually a close family member, citizenship or residency status), consistently hedged with the page's existing framing that criteria differ by bank and should be confirmed directly.

## Internal linking

13. `public/insights/tenancy-agreement-stamp-duty-singapore.html` links to the IRAS e Stamping guide, but the reverse link did not exist. Added it to the "Related guides" list, a genuinely relevant pairing (stamp duty amount and the portal used to pay it).

14. `public/insights/property-valuation-singapore-guide.html` had no "Related guides" section at all. Added one linking to three already existing, topically relevant guides: the Option to Purchase guide, the property agent commission guide, and the HDB resale levy guide.

15. `public/insights/option-to-purchase-guide-singapore.html` did not link to the property valuation guide despite valuation directly determining loan quantum and OTP structuring. Added the link to its existing "Related guides" list.

## Investigated and deliberately left alone

- **`public/insights/hdb-bto-waiting-time-2026.html`** - flagged for a vague "several years" meta description. The page deliberately avoids a specific wait time range anywhere in its body (quick answer, FAQ, and JSON-LD all say "several years" or "project specific"), because BTO wait times genuinely vary by project. Adding a number like "4 to 6 years" would have been a guess not supported by the page. Left the meta as is rather than inventing a figure.
- **`public/insights/two-room-flexi-scheme-guide-singapore.html`** - the title promises "Price" coverage but the page body has zero dollar figures anywhere, stating explicitly that "actual prices vary by town, floor and market conditions" and to verify with HDB. No verified price range exists to add to the meta. Left alone rather than inventing one.
- **Cannibalization: `/glossary/tenancy-in-common` vs `/glossary/tenancy-in-common.html`** - investigated as a Search Console reported duplicate. Confirmed via `vercel.json`'s cleanUrls setting and a live redirect check that the `.html` URL already 308 redirects to the clean URL, and the clean URL already carries the correct canonical tag. No file level fix needed; this will resolve naturally as Google recrawls.
- **Cannibalization: `/glossary/diplomatic-clause` vs `/insights/diplomatic-clause-singapore`, and `/answers/how-much-commission-does-a-property-agent-charge-in-singapore` vs `/insights/property-agent-fees-singapore`** - both weaker pages already link to their stronger sibling. Considered adding a canonical tag redirecting the weaker page's ranking signal to the stronger page, but declined: these are structurally a glossary term and an FAQ answer format respectively, both of which can independently win rich snippets, and forcing a canonical would deindex them entirely for a modest, unconfirmed signal gain. Judged this too aggressive a structural change for the size of the opportunity; better addressed with a future editorial pass, not a scripted redirect.
- **`public/insights/ura-master-plan-2025-property-impact.html`** - describes "Master Plan 2025" as still in draft, exhibited but not yet gazetted, published 13 July 2026. Could not verify the current real world gazettal status from any source available in this environment (no network access), so left the claim as is. The page already hedges extensively ("treat as official but subject to change and review", "confirm on URA SPACE"), which is the correct posture given the uncertainty.
- **`public/insights/understanding-rental-transaction-data-singapore.html`** - already retitled in the 17 Aug run specifically to fix this same CTR problem; it still shows 0.15% CTR on 1320 impressions despite the title now matching the "ura rental transaction" query closely. Likely a zero click search pattern (searchers satisfied by the snippet) rather than a fixable title problem. No further change made.

## Verification performed

- All 18 duplicate `<h1>` files and thank-you.html confirmed to have exactly one `<h1>` after the fix.
- `public/launches.json` and `vercel.json` both still parse as valid JSON.
- Every JSON-LD block on all 10 touched insights pages still parses as valid JSON.
- CEA R073319H confirmed present on every touched page.
- No dashes or unexpected hyphenated words introduced in any new title, meta description, or body copy.
