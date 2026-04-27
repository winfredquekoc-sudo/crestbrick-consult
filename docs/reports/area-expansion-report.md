# MRT × School Catchment Matrix — Expansion Report

**Date:** 2026-04-27
**Deployment:** https://crestbrick-consult-bd7uuq27c-winfredquekoc-3888s-projects.vercel.app
**Production URL:** https://winfredquek.com/area

---

## Numbers

- **Pages added:** 89
- **Total area pages now:** 153 (was 64)
- **MRT stations covered:** 97
- **Sitemap URLs:** 306 total, 153 of which are `/area/` pages
- **Parent pages updated with internal link:** 54 (28 districts + 26 HDB-town pages)

## Coverage by MRT line

The original 64 covered the core mature-estate stations on NSL/EWL/NEL/CCL/DTL plus the headline TEL stations (Mayflower, Marine Parade, Outram Park, Woodlands).

Added in this round:

| Line | New stations covered |
|---|---|
| **TEL** | Lentor, Bright Hill, Upper Thomson, Caldecott, Stevens, Napier, Orchard Boulevard, Great World, Havelock, Maxwell, Shenton Way, Marina Bay, Gardens by the Bay |
| **CRL (phase 1)** | Aviation Park, Loyang, Pasir Ris East, Tampines North, Defu, Hougang (CRL), Serangoon North, Tavistock, Ang Mo Kio (CRL), Teck Ghee, Bright Hill (CRL) |
| **JRL** | Choa Chu Kang (JRL), Tengah, Jurong East (JRL), Pandan Reservoir, Boon Lay |
| **NSL north** | Sembawang, Khatib, Yio Chu Kang, Admiralty, Marsiling, Kranji |
| **EWL east** | Tanah Merah, Simei, Expo, Kembangan, Eunos |
| **EWL west** | Dover, Commonwealth, Lakeside, Chinese Garden, Pioneer |
| **NEL** | Punggol Coast, Kovan, Buangkok, Woodleigh, Potong Pasir, Boon Keng, Farrer Park |
| **DTL extra** | Beauty World, King Albert Park, Hillview, Cashew, Botanic Gardens |
| **CCL extra** | Haw Par Villa, Kent Ridge, One-North, Lorong Chuan, Bartley, MacPherson, Mountbatten |

## Quality decisions

- **No fabricated distances.** Per the project's HARD DON'T, every new page uses `"within walking distance"` (with `"a short walk"`) for sub-1 km pairs and `"approximately X km"` (with `"around N min"`) for 1–2 km pairs. The original 64 had precise figures because they were curated; for new stations I lacked confident measurements, so I used conservative ranges.
- The page schema (Article + Place + EducationalOrganization + RealEstateAgent + BreadcrumbList JSON-LD) is identical to the original template, with the breadcrumb pointing to `/area` instead of `/districts` for the new pages.
- **Index page:** New `/area/index.html` lists all 153 pages grouped by MRT (97 cards, alphabetised).
- **Internal linking:** Every existing district + HDB-town footer now has a `MRT × school areas` link; the new pages link back via `← All MRT & school areas` in the hero and in their footer Quick Links block.

## Skipped pairs (and why)

- **Cairnhill / Mount Pleasant / Springleaf TEL stops** — no clearly defendable primary-school catchment within 2 km that wasn't already covered by an adjacent station; would have produced thin pages.
- **CG1 Expo / Changi Airport** — almost no residential catchment.
- **Holland Plain CRL** — station name and surrounding catchment still in flux at construction stage.
- **Punggol Town Centre / Sumang LRT loops** — already absorbed under the Punggol NEL parent page; adding LRT would produce near-duplicates.
- **Bukit Brown / Mount Pleasant** — non-revenue or future stations; deferred.
- **Boon Lay JRL extension stations beyond Boon Lay (Tukang, Gek Poh, Tawas)** — limited residential catchment relevant to school-distance buyers; deferred.
- **CCL stage 6 closure stations (Cantonment / Prince Edward / Marina Bay loop)** — Cantonment Primary catchment was already represented via Outram Park, Maxwell, Shenton Way; adding a Cantonment CCL page would be a near-duplicate.
- **Most ECP / Founders' Memorial TEL extension stops** — masterplan-stage, no defendable catchment yet.

## Validation

- All 153 area pages parse cleanly via `html.parser`.
- All 153 pages have valid JSON-LD `<script type="application/ld+json">` blocks.

## Files touched

- New: `_render_mrt_school_matrix_ext.py` (extension generator)
- New: 89 HTML files in `/public/area/*.html`
- New: `/public/area/index.html` (browse-all hub)
- Modified: `/public/sitemap.xml` (90 new `<url>` entries: 89 area pages + index)
- Modified: 54 parent pages in `/public/hdb-towns/` and `/public/districts/` (footer link added)
- New: `/public/_area-sitemap-ext-fragment.xml` (sitemap splice source — kept for audit)

## What did not change

- The original 64 area pages were untouched.
- Visual styling, header/footer, brand voice unchanged.
- No new dependencies introduced.
- `settings.json` not touched.
