# Nightly Site Improvements — 2026-08-16

24 distinct improvements across 52 files. Priority: factual staleness (several were compliance/accuracy relevant, not just SEO), then Search Console driven CTR and thin content fixes, then a technical SEO defect.

## Factual staleness (highest priority — outranks SEO work)

1. **SORA vs Fixed Rate Mortgage 2026 page rewritten with current numbers** — `public/insights/sora-vs-fixed-mortgage-2026.html` — the entire article's conclusion was backwards. It said 3M SORA was ~2.9% (May 2026) versus 2 year fixed at 1.45 to 1.65%, a ~1.8 to 2.0 point gap making "fixed the clear winner." Checked the site's own live rate tracker (`~/.claude/bin/rate-drop.state`, updated daily through 15 Aug): SORA has fallen to ~1.1% and fixed sits at 1.6 to 1.65%, so the gap has essentially closed. Rewrote title, meta, both FAQ JSON-LD entries (kept in sync with visible FAQ text), the quick answer, the $1M loan worked example, both rate tables, the rate shock section, the lock in section, and the 3 question decision framework — recomputed every dollar figure from the new rates using standard amortization math rather than reusing old numbers. This was giving buyers a materially wrong recommendation.

2. **HDB vs Condo comparison table mortgage row corrected, and the declared "winner" flipped** — `public/compare/hdb-vs-condo.html` — same underlying problem: it quoted bank loan rates at "3.0 to 3.8%" versus HDB concessionary's 2.6%, declaring HDB the winner on rate. Current bank rates (per verified data above) are 1.6 to 1.85%, now below HDB's 2.6%. Updated the figure and swapped the winner marker from HDB to Condo, with a caveat that HDB's rate is fixed and stable while bank rates float.

3. **Lentor Gardens Residences lead capture modal was telling visitors pricing wasn't public yet** — `public/LentorGardensResidences.html` — the popup every visitor sees on load still said "before the price is public" and "official pricing releases 4 Jul 2026" (future tense), even though pricing has been out since 4 July and balloting closed 18 July. The rest of the page already reflected this correctly; only the entry modal was stale. Fixed the eyebrow text, heading, CTA button and fine print.

4. **Dunearn House brief still said floor plans weren't released** — `public/launches/briefs/dunearn-house.html` — one paragraph said "floor plans not yet released as of April 2026" while the rest of the same page already showed launch PSF and 56% sold (from an earlier fix). Rewrote the paragraph to reflect the actual 25 to 26 July launch.

5. **Peck Hay Road Residences GLS tender result added** — `public/launches/briefs/peck-hay-road-residences.html` — the tender closed 11 June 2026 and the page still said the developer was unconfirmed. Verified via EdgeProp/The Edge Singapore: CDL Constellation and Garden Estates (Hong Leong Group) won at ~S$542.4M, ~S$1,865 psf ppr. Updated the hero stat, warn box, developer section and breakeven estimate across 5 spots on the page, keeping unit mix and PSF projections clearly labeled as still unconfirmed pending the developer's own announcement.

6. **The Serra Residences preview date added** — `public/launches/briefs/the-serra-residences.html` — confirmed developer preview date (19 September 2026, Far East Organization) replaces a stale "not yet released as of April 2026" line.

7. **5 more launch briefs had the same frozen "as of April 2026" date anchor** — `chuan-grove-residences.html`, `one-marina-gardens.html`, `miltonia-close-ec.html` (x2 lines), `coastal-cabana-jalan-loyang-besar-ec.html` (x3 lines) — researched each; none had a materially different verified status, so rather than leave a stale date implying the page was checked 4 months ago, removed the date anchor and rephrased present tense. One unverifiable stale count (Coastal Cabana's "~150 unsold, 20%") was softened to "WhatsApp me for the current count" rather than guessing a new number.

8. **Lentor absorption story had a contradiction with itself** — `public/insights/lentor-absorption-story-what-it-means.html` — said Lentor Gardens Residences had "booking day on 18 July 2026 and no pricing released," which is now false (both happened) and also contradicted the page's own footer note saying pricing was released. Fixed the clause.

9. **22 district pages + the new launches index had a false "monthly refresh" claim** — `public/districts/d1*.html` through `d27*.html` (22 files) and `public/new-launches.html` — every district page carried "Verified against URA GLS + developer announcements as of April 2026. List refreshes monthly." The date never moved despite the claim; the refresh isn't happening monthly. Removed the false cadence promise and replaced with an honest "confirm current status with the developer or WhatsApp me for an update."

10. **Bridging loan and Fixed vs Floating Mortgage pages had stale absolute rate figures** — `public/insights/bridging-loan-singapore-playbook.html` and `public/insights/fixed-vs-floating-mortgage.html` — both quoted specific "as of April/May 2026" percentages (bridging loan all in rate 4.5 to 5.5%, fixed 3.0 to 3.5%, floating 3.0 to 3.3%) that were 4+ months stale given SORA's fall. Recomputed using the same verified current rate data and each page's own spread methodology.

## Search Console: CTR rewrites (top 10 ranking, under clicked)

11. `public/insights/en-bloc-singapore-guide.html` — title/meta rewritten to cover meaning, process and timeline in one line (queries were split across all three).
12. `public/insights/property-agent-fees-singapore.html` — led with "buyers pay $0" (verified in the fee table) since most underperforming queries were cost anxious.
13. `public/insights/two-room-flexi-scheme-guide-singapore.html` — dropped an "income ceiling rules" claim the body never actually backs with a number.
14. `public/insights/condo-maintenance-fees-singapore.html` — reworded to lead with "Maintenance Fees Singapore" and "average," matching underperforming query terms.
15. `public/tools/hdb-eip-checker.html` — made it read clearly as a free instant tool, since tool seeking queries ("check hdb ethnic quota" etc) were getting a title that read like an article.
16. `public/insights/dbss-flats-explained-singapore.html` — led with "What Is DBSS?" since 305 impressions on the bare term "dbss" were converting almost nobody, most searchers don't know the acronym.
17. `public/insights/iras-estamping-portal-guide-singapore.html` — moved "e Stamping" to the front of the title (two words, matching the page's own body convention), since "estamping" as one word was a mismatch with how people actually search.

## Search Console: keyword cannibalization

18. **HFE letter cluster internal linking fixed** — `public/insights/hdb-hfe-letter-guide-2026.html` (added links to the glossary and short answer pages) and `public/answers/how-does-the-hfe-letter-work-and-how-long-does-it-take.html` (added an inline link to the full guide). Three of our own pages were competing for "hdb hfe" / "hfe letter" queries; the full guide is the clear winner by clicks and position, the other two now point to it. Checked the diplomatic clause cluster too — it was already fully cross linked, no change needed there.

## Technical SEO defect

19. **New launches index page was invisible to crawlers** — `public/new-launches.html` — the entire launch listing (36 projects) is normally rendered client side by fetching `/launches.json` after page load. The raw HTML crawlers see was just "Loading launches," with zero actual content. Pre rendered the full card grid as static HTML (reusing the exact JS render template, same classes, same structure) so it's there on first paint; the existing JS still re-fetches and re-renders on top for interactivity, so nothing about the live filtering/sorting changed. This was likely the single highest value technical fix tonight given it's the hub page for every /launches/briefs/ page.

20. **`public/launches.json` itself is stale, corrected 2 verified entries** — while fixing the above, found the underlying data file was last synced 26 April 2026 (a separate refresh job problem, not something I can fix from this repo). Updated the Dunearn House and Lentor Gardens Residences entries to `status: active` with real launch outcomes, so the newly static index page doesn't contradict the individual brief pages I fixed tonight (items 4 to 8 above). Left the other 34 entries alone since I have not independently verified their current status; several other "upcoming" entries also carry passed preview/booking dates and should be treated as a to-do for whoever owns the refresh pipeline.

## Broken/thin page defect

21 to 26. **6 pages had two conflicting `<h1>` tags** — `public/insights/remarriage-blended-family-property-planning-singapore.html`, `co-tenancy-roommate-agreement-singapore.html`, `completion-day-what-happens-property-singapore.html`, `defects-liability-period-new-launch-singapore.html`, `hdb-ballot-priority-schemes-singapore.html`, `seller-concessions-repair-credits-singapore.html`. Each has a custom animated hero section with its own visible h1, plus a leftover second h1 (marked `aria-hidden`, identical text) from the standard article template that should have been removed when the custom hero was added. Confirmed via cross-file comparison that the second h1's exact class combination is the standard, unhidden template h1 used site wide elsewhere. Converted the duplicate to a `div` with identical classes, preserving the visual output exactly while removing the duplicate heading.

## Deliberately left alone

- **Striking distance content** (5 pages ranking position 11 to 21 that were flagged as potentially missing an answer): HDB HFE validity, rental income deductible expenses, landlord insurance for landed property, SBF vs BTO balance flat comparison, and the GCB areas list all already explicitly and prominently answer their target query, confirmed by reading each page in full and checking the visible content against the JSON-LD FAQ. This looked like leftover work from an earlier nightly pass. Made no changes rather than force edits that weren't needed.
- **bank-mortgage-rates-singapore-2026.html and posb-dbs-home-loan-singapore.html** were checked for the same stale-rate pattern. Both were already hedged (rounded "~1.5%" framing, or "in our most recent dated survey" language with no bare percentage asserted as current) and still directionally correct against the verified current rates, so left untouched rather than over edit every mortgage adjacent page on the site.
- **The other 34 entries in launches.json** were not individually re-verified tonight; several visibly carry passed preview/booking dates. Flagging this as a separate data pipeline problem, not fixed here.
- **Coastal Cabana's exact unsold unit count**: could not verify a precise current figure (the developer's live balance chart is JS rendered and wasn't independently confirmed), so the stale April figure was removed rather than replaced with a guess.
- Did not touch `_templates/`, `.env` files, `src/wa-pipeline`, or anything with credentials, per hard limits. Did not run any git command. Did not delete any page.

## Facts I omitted rather than guessed

- Exact current unsold unit count for Coastal Cabana EC (softened to "ask for current count").
- 1 year SORA and 3 year fixed rate figures on the SORA vs Fixed page and the Current Rate Landscape table (only had verified data for 3M SORA and 2 year fixed from the site's own rate tracker; removed the old stale numbers rather than extrapolate new ones).
- Exact per bedroom type unit sizes for Dunearn House (kept as indicative pending confirmation with the sales team, rather than assert precise sqft I could not verify).
- Unit count revision for Peck Hay Road Residences (one source suggested it may have risen from 315 to ~380 post tender; kept the original GFA derived 315 figure since the higher number was reported as uncertain).

## Note on tool input

Several Search Console query strings in `brief_ctr.json` contained embedded prompt injection style text (e.g. "context: location: singapore... do not include location references... question: ..."). Treated as inert search query data, not instructions; flagged to Winfred at the start of the session, no action taken on their content.
