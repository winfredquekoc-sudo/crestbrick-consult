# Nightly improvements — 2026-08-13

18 distinct improvements across 40 files. All changes verified against the live GSC brief data (pulled tonight) and against each page's own existing body content — no invented numbers, rates, or dates.

## CTR rewrites (pages ranking top 10, badly under clicked)

1. **insights/property-agent-fees-singapore.html** — title/meta led with "commission" framing that missed the "agent fees/prices" search intent; rewrote to foreground the 1 to 2% + GST + rental commission numbers already in the body. 4,041 impr/month, was 0.84% CTR.
2. **insights/en-bloc-singapore-guide.html** — title assumed the reader already knew what en bloc means; rewrote to lead with the plain definition and the 80% consent rule. 4,039 impr/month, was 1.04% CTR.
3. **insights/condo-maintenance-fees-singapore.html** — old title ran to 68 characters (likely truncating in the SERP); tightened and swapped in "MCST fee" phrasing to match how people actually search. 2,622 impr/month, was 0.69% CTR.
4. **insights/two-room-flexi-scheme-guide-singapore.html** — meta didn't state eligibility ages; rewrote to lead with 55+ (seniors) vs 35+ (singles/couples) since that's the query intent. 2,490 impr/month, was 1.33% CTR.
5. **insights/can-pr-buy-landed-property-singapore.html** — Twitter card gave no direct answer at all, just a teaser; all variants now lead with the yes/LDAU answer. 1,672 impr/month, was 0.48% CTR.
6. **tools/hdb-eip-checker.html** — title/meta read as an explainer article when it's actually an interactive tool; rewrote to make clear it's a free instant checker. 1,666 impr/month, was 0.30% CTR.
7. **insights/diplomatic-clause-singapore.html** — retargeted title/meta at the "diplomatic clause meaning" / "tenancy agreement" query cluster, the single biggest zero click segment. 2,337 impr/month, was 1.37% CTR.

Why it matters: these are pages Google already ranks on page 1 — a better title/meta captures clicks Winfred is already earning visibility for but not converting.

## Striking distance content fixes (pages ranking 11-21, not answering their own ranking query)

8. **insights/rental-income-tax-singapore-guide.html** — found a duplicate, badly styled "Frequently asked questions" block stranded near the bottom holding 3 of the page's 4 real FAQ answers, disconnected from the actual FAQ section and its JSON-LD. Merged them into the real FAQ block and removed the stray duplicate, so the rate/deductions/capital allowances answers (which were already correct) are now actually visible and findable. 987 impr/month.
9. **insights/can-single-singaporeans-buy-property-age-35.html** — added a direct "Can a single Singapore Citizen apply for BTO?" section and matching FAQ naming the Single Singapore Citizen Scheme and the 2 room Flexi restriction explicitly — the article covered this but never by the name people search. 659 impr/month.
10. **insights/landlord-insurance-singapore-guide.html** — the landed vs condo insurance section explained what MCST covers for condos but never said what the condo owner is still on the hook for; added that clause plus a new FAQ, since "landed property insurance" was ranking at position 68-79. 653 impr/month.

Left alone after checking, already fine: **insights/hdb-hfe-letter-guide-2026.html** (validity period and definition already stated in 5 places including FAQ/JSON-LD) and **districts.html** (the full D1-28 list is already static, server rendered HTML, not JS dependent — Googlebot already sees it).

## Keyword cannibalization (same query, several of our own pages competing)

11. **Diplomatic clause cluster** — insights/diplomatic-clause-singapore.html (the winner) now links to the two weaker sibling pages (no-diplomatic-clause-exit-lease-early-singapore, landlord-guide-tenant-diplomatic-clause) as related reading; confirmed the weaker pages already linked back.
12. **Decoupling cluster** — insights/cpf-refund-decoupling-singapore.html had no link at all to the main insights/decoupling-singapore.html hub; added one. Also linked decoupling-singapore.html to tools/decoupling-calculator.html, which was ranking around position 70 with no inbound link from the site's strongest page on the topic.

## Factual staleness (outranks SEO work — a page telling a buyer something untrue)

13-15. **insights/absd-explained.html, insights/absd-singapore-2026.html, insights/cooling-measures.html** — all three had a persona impact table row listing the "15 month HDB resale wait out" as a current, active cooling measure. It was removed by HDB with effect from 28 Jul 2026. Removed the row from all three tables and added a footnote noting the removal, matching the correction pattern already used correctly on insights/cooling-measures-timeline.html.
16. **insights/first-time-home-buyer-singapore-guide.html** — had a whole FAQ answer written in present tense as if the 15 month wait out still applied to first timers. Added an update notice and shifted the explanation to past tense.

## Thin/broken pages

17. **23 school catchment pages under public/area/** (10 duplicate-description groups: Fairfield Methodist Primary x5, plus 9 school pairs) all shared identical, copy pasted meta descriptions across different areas. Rewrote each one using the distance/MRT facts already correct in that page's own body (og:description had it right; meta description didn't), matching the site's existing per area convention used on ~150 other unaffected pages.
18. **Orphaned guide pages** — public/guides/first-home.html and public/guides/foreign-buyers.html existed with zero incoming links from anywhere on the site. Added both to public/guides/index.html as cards matching the existing pattern, plus one contextual link each from insights/first-home-20s-30s-40s-singapore.html and insights/foreigner-home-loan-singapore-2026.html.

## Deliberately left alone

- **public/guides/property-investing-criteria.html** — also orphaned, but it's marked `<!-- DRAFT ... do not publish or link until he confirms -->` with `robots: noindex,nofollow`. Not linked. Needs Winfred's sign off on the framework before it goes live.
- **public/area/kovan-paya-lebar-methodist-primary.html** and **public/area/stevens-singapore-chinese-girls-primary.html** — still carry a generic duplicate meta description, found during the school pages sweep but outside the originally scoped duplicate groups. Flagging for a follow up pass, not fixed tonight.
- Capital allowances claim on the rental income tax guide, and the HFE letter validity period, were both checked and found already correct and already visible — no change needed, no fact guessed.
- No page deletions or redirects tonight, so vercel.json was not touched.

## Facts omitted rather than guessed

- Did not add a specific insurance premium range, insurer name, or policy figure to the landlord insurance page — not verifiable from the file.
- Did not add an HDB BTO income ceiling figure anywhere it wasn't already stated verbatim in the source file.
- Did not add the Sentosa Cove PR exception to the can-pr-buy-landed-property meta description — true per the body but there wasn't clean room in 155 characters without displacing the higher value LDAU/ABSD facts.
