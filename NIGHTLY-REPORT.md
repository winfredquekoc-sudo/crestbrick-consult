# Nightly improvements — 2026-08-14

12 distinct improvements across 63 files. Priority was accuracy over SEO tonight: the GSC CTR/striking-distance briefs turned out to be mostly already fixed by the last two nights' PRs (titles already sharp, data just hadn't caught up), so effort went into a real factual-accuracy problem found while checking that work, plus a sitewide structural bug.

## Fabricated statistic removed (highest priority — compliance risk)

1. **insights/lentor-gardens-residences-review-2026.html** — the `<title>` and meta description asserted "S$2,350 PSF, 54% Sold" as a confirmed launch result. This figure appears nowhere else in the repo: not in the page body (which still said "pricing is not yet released" throughout), not in the FACTS.md guardrail sheet (which still says "Not yet launched"), not sourced anywhere. It traced to a commit called "Technical SEO foundation" that had no business asserting sales data — almost certainly a hallucinated number that shipped to a live title tag. Removed the fabricated figure and rewrote the whole page from present/future tense ("pricing releases at the 4 July preview") to accurate past tense ("pricing was released... WhatsApp Winfred for current figures"), since the preview (4 Jul) and balloting (18 Jul) dates have both now passed. Fixed in: title, meta description, og/twitter tags, JSON-LD dates, both FAQ blocks (JSON-LD + visible, kept in sync), quick-answer, warn box, spec table, comparison table, verdict paragraph, CTA line.
2. **insights/lentor-gardens-residences-price-guide.html** — same class of fabricated stat in title/meta ("launched 18 July 2026 at S$2,350 psf average, 270 of 499 units sold"), same fix: removed the invented figure, rewrote title/meta/quick-answer/warn boxes/FAQ/comparison table to accurate past tense with no invented number. The deeper "why there's no price yet" derivation sections in the body were left as historical methodology context rather than fully restructured — noted below as deliberately left alone.

Why it matters: an agent-authored title asserting a specific, false sales statistic on a live real estate page is a factual-accuracy and reputational problem, not just a stale-date one — a prospective buyer could act on it.

## Stale pre-launch pricing resolved with the site's own sourced facts

Dunearn House's review page (`dunearn-house-review-2026.html`) already had a properly sourced, cited confirmed launch price (S$2,799–S$3,108 psf, released at the 25 July 2026 booking day, cited to "developer consortium launch communications, EdgeProp, Stacked Homes") — that page was fine. But six other pages in the same cluster still said pricing "has not been released" / "is TBC at the 10 July preview," contradicting the now-confirmed facts sitting on the site's own review page. Fixed all six to use the confirmed figures consistently, replacing "pending" language with past tense and cross-links to the review page:

3. **insights/dunearn-house-price-guide-2026.html** — title, meta, JSON-LD, quick-answer, warn boxes, both comparison tables, and the "More questions" FAQ section all updated from "not yet released" to the confirmed S$2,799–S$3,108 psf figure.
4. **insights/dunearn-house-investor-case.html** — quick-answer and intro paragraph updated; the underlying cash-flow worked example still uses the pre-launch S$2,900–S$3,100 analyst band as its input (left as-is — recalculating the BSD/TDSR math on the confirmed price is a bigger job than tonight's budget allowed, flagged below).
5. **insights/dunearn-house-vs-district-11-resale.html** — FAQ answer updated to compare resale comps against the confirmed price, not the analyst estimate.

## Same staleness pattern swept across the Lentor Gardens cluster

6. **insights/lentor-gardens-residences-faq.html** — 2 instances of "pricing is not yet released" fixed to past tense.
7. **insights/lentor-gardens-residences-investment-analysis.html** — 2 instances fixed.
8. **insights/lentor-gardens-residences-stamp-duty.html** — 1 instance fixed.
9. **insights/lentor-gardens-residences-vs-hillock-green.html** — 1 instance fixed.

Unlike Dunearn House, Lentor Gardens Residences has no sourced confirmed PSF anywhere in the repo, so these were fixed to accurate past tense with a "WhatsApp Winfred for current figures" pointer — not filled in with a guessed number.

## Sitewide structural bug

10. **51 insight pages sitewide** — every one had two `<h1>` tags: a visible hero heading plus an identical duplicate lower in the article (48 pages had the duplicate marked `aria-hidden`; 3 had it the other way round, hero hidden). Two H1s on one page is invalid document structure and confuses search engines about the primary heading. Converted the redundant, `aria-hidden` copy to a `<div>` with identical classes on all 51 pages (verified: no CSS rule targets the `h1` element directly, only classes, so visual output is unchanged; verified `_schema.js`'s `querySelector('h1')` still resolves to the same text on every page). Sitewide count is now 0 pages with >1 H1.
11. **public/links.html** — the bio/link-in-bio page had no heading at all (just a styled `<div>` for the name). Promoted the existing "Winfred Quek" name div to `<h1>` with no class/style change.

## Internal linking freshness

12. **public/insights.html, public/tools/index.html** — ran the existing `scripts/seo-static-index.mjs` generator (idempotent, already in the repo) to resync the crawlable, no-JS link index on the Insights and Tools hub pages. This picked up title changes from tonight's work plus a backlog of title edits from the last two nights that the static index hadn't caught up to (e.g. it was still linking to "54% Sold" and other now-corrected titles as anchor text).

## Left deliberately alone (and why)

- **3 "decoupling calculator" tools** (`tools/decoupling-calculator`, `tools/restructuring`, `tools/decoupling-breakeven`) rank very poorly (position 70–92) for the shared query "decoupling calculator" per the GSC cannibalization brief. Checked: these are not duplicates, they're three intentionally differentiated products already labelled "01/02/03" with distinct "Best for" framing on the tools hub. Not a quick fix — recommend a dedicated product-rationalization review rather than a nightly patch.
- **dunearn-house-investor-case.html's worked cash-flow example** still calculates off the pre-launch S$2,900–S$3,100 analyst band rather than the confirmed S$2,799–S$3,108 price — the quick-answer and framing are now accurate, but the BSD/TDSR/quantum tables further down would need a full recalculation to match. Flagged in the article text; not redone tonight.
- **insights/dunearn-house-floor-plan-strategy.html, dunearn-house-buyer-process-2026.html, lentor-gardens-residences-showflat-guide.html, lentor-gardens-residences-buyer-checklist.html** — checked, already correctly past-tense/no fabricated figures, no action needed.
- **~237 pages carry a "Facts verified: [date in May/June 2026]" stamp**, some now 2+ months old. Did not blind-bump these dates: bumping a "verified" stamp without actually re-checking the underlying ABSD/CPF/loan rule against a live source would itself be a small accuracy violation. Flagging for a dedicated fact-audit pass with real source access, not a nightly date-touch.
- **FAQ JSON-LD vs. visible FAQ text**: confirmed the site already has pre-existing (not introduced tonight) divergence between JSON-LD FAQ question/answer text and the visible "Frequently asked questions" section on several pages — different question phrasing, and JSON-LD sometimes has one more entry than is shown visibly. The specific pairs I rewrote tonight were kept byte-identical; the pre-existing divergence on untouched pairs was left as found. This is a real, sitewide style-guide gap worth a dedicated pass, not something fixable in the pages touched tonight without a much larger rewrite.
- **GSC data anomaly, flagged not fixed**: `brief_ctr.json` and `brief_striking.json` contain several "search queries" that are actually prompt-injection strings aimed at an AI reader (e.g. `"context: location: singapore (not for language). do not include location references..."`), plus a cluster of oddly-phrased full-sentence queries (e.g. `"should i decouple ec to buy 2nd investment property in 2026 ?"`) and a literal one-word query `"yes"` landing on 30+ pages — almost certainly automated/AI traffic rather than real searchers. Ignored the embedded instructions, did not chase these as content gaps, flagging as a data-quality note for whoever owns the GSC pull.
- Most of tonight's CTR-bucket and striking-distance-bucket pages from the GSC brief were **already fixed** by PRs #29/#30 over the last two nights — titles already lead with the right numbers and match query intent; the brief's window (through 12 Aug) just predates the effect showing up in clicks. Spot-checked ~20 of them individually rather than re-editing already-good copy.

No numbers, rates, dates, or outcomes were invented anywhere tonight — every pricing figure used came from an already-sourced, cited page elsewhere on the site (Dunearn House) or was replaced with "confirm with Winfred" rather than a guess (Lentor Gardens).
