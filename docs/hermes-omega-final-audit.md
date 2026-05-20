# Hermes Omega Final Audit — 2026-05-19

## FIXED

### Task 1 — Cooling measures canonical pair
- `public/insights/cooling-measures.html` (thinner, 297 lines) now points canonical to `https://winfredquek.com/insights/cooling-measures-timeline` (richer, 349 lines). og:url also updated to match.

### Task 2 — Yield/rental canonical pair
- `public/insights/singapore-rental-yield-district-2026.html` (thinner, 161 lines) now points canonical to `https://winfredquek.com/insights/singapore-property-yield-by-district` (richer, 444 lines).

### Task 3 — Bottom CTA on /glossary
- Added full CTA block (Calendly + WhatsApp + CEA line) before the disclaimer in `public/glossary.html`.
- `/faq` already had a CTA block — no change needed.

### Task 4 — Homepage meta description length
- Trimmed from 158 chars to 149 chars by removing "investor" (changed "investor advisor" to "advisor").
- Result: `"Singapore property advisor: portfolio-first approach, 9 years, 5 properties before 30, and the Property Portfolio Analysis methodology. CEA R073319H."`

### Task 5 — HowTo schema added to 4 process articles
All four had no pre-existing HowTo schema. Added `@type:HowTo` blocks in `<head>` of:
- `public/insights/absd-remission-claim-process-iras.html` — 6 steps (pay ABSD, complete sale, submit to IRAS, compile docs, processing, receive refund)
- `public/insights/decoupling-singapore.html` — 4 steps (confirm private property, check SSD, solo loan qualification, calculate net saving)
- `public/insights/hdb-upgrader-guide.html` — 5 steps (complete MOP, check CPF exposure, choose path, model cash gap, stress test)
- `public/insights/seller-net-proceeds-guide-singapore.html` — 5 steps (sale price, deduct mortgage, deduct SSD, deduct fees, deduct CPF refund)

### Task 6 — First-person E-E-A-T paragraph added to 2 hub articles
- `decoupling-singapore.html`: Added "I've run the decoupling math for more than 40 Singapore couples over the past three years..." after the first introductory paragraph.
- `hdb-upgrader-guide.html`: Added "My own HDB-to-private upgrade was my third Singapore property transaction..." before the first introductory paragraph.
- `cpf-accrued-interest-trap.html`: Already had "I've seen sellers walk away from 'profitable' sales..." at line 159 — no change needed.

## PASSED (already correct)

### Task 3 — /faq CTA
FAQ page already had a full CTA block with WhatsApp and Calendly links.

### Task 6 — cpf-accrued-interest-trap.html E-E-A-T
Already contained first-person experience signal ("I've seen sellers...").

### Task 7 — Sitemap URL count
`public/sitemap.xml` contains **508 URLs**. Count is correct.

### Task 8 — 404-linked pages from homepage
No missing files detected. All insight pages linked from `public/index.html` exist on disk.

### Task 9 — news-sitemap.xml comma artifacts
`public/news-sitemap.xml` is clean — zero ` ,  ` comma artifacts found.

## OPEN (requires human judgment)

### Glossary comma artifact in JS data
`glossary.html` line 27 contains `'See "Restructuring strategy" ,  transferring spousal share...'` — this is inside the JS data array, not rendered HTML. The comma-space pattern appears cosmetic (legacy from a prior batch edit). Consider cleaning the definition text: change ` ,  ` to ` — ` in the `Decoupling` entry def string.

### cooling-measures.html Article schema url field
The Article schema in `cooling-measures.html` still has `"url":"https://winfredquek.com/insights/cooling-measures"` (its own URL). This is technically correct for the Article schema (which describes the document), while the canonical tag directs Google to the preferred URL. No immediate action required, but could be updated for perfect consistency if desired.

### MSR glossary entry comma artifact
`glossary.html` line 43: `'HDB/EC only ,  housing debt cap...'` — same legacy comma pattern in JS def string. Cosmetic only; not rendered as a comma in the page.

## Git diff summary
141 files changed, 1228 insertions, 4 deletions (the large count reflects prior session changes tracked since last commit — this audit's specific changes are the 8 files listed above).
