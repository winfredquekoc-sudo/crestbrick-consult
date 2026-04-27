# District Detail Pages — Technical Build Plan

**Scope:** Ship 28 district landing pages with 5 inline-SVG charts + enriched narrative, no framework. Pure Python generator + existing Tailwind build.
**Owner:** developer agent. **Peers:** property-researcher (data), creative-director-mgr (visual spec).
**Target file for generator edits:** `/Users/winfredquek/crestbrick-consult/_render_area_pages.py`
**Chart starter code:** `/Users/winfredquek/crestbrick-consult/src/components/charts.html`

---

## 1. Chart components (shipped)

All 5 components live as Python functions in a single source file:
`/Users/winfredquek/crestbrick-consult/src/components/charts.html` (HTML-wrapped Python blocks — copy the block comment directly into `_render_area_pages.py` or a sibling `charts.py` module).

| Function | Inputs | Empty behaviour |
|---|---|---|
| `psf_range_bar(low, high, ccr_med, rcr_med, ocr_med, caption)` | numeric psf | `""` if any field missing |
| `yield_meter(low, high, caption)` | % numbers | `""` if either missing |
| `travel_times_ribbon(cbd, orchard, changi, caption)` | minutes | `""` if all three missing; single bars render if only one is set |
| `supply_timeline(launches, caption)` | list of `{name, year, psf_range}` | `""` on empty list |
| `region_spectrum(region, caption)` | `CCR\|RCR\|OCR` | `""` on other values |

All five emit `<figure class="cb-chart"><svg.../>[<figcaption/>]</figure>` with a single shared `<style>` block (~1.1 KB) injected once per page. SVGs use `viewBox` + `preserveAspectRatio="xMidYMid meet"` → responsive by default inside any parent column. The base template's `max-w-4xl` (~768px) is the reference width; charts also work at mobile 360px and expanded 1024px.

Accessibility: each `<figure>` has `role="img"` + `aria-label`. Text colour contrast uses `--ink`/`--ink-soft` against `#faf8f4`, clearing WCAG AA.

---

## 2. Python generator update plan

**Concrete edits to `_render_area_pages.py` — do not refactor the whole file.**

### 2a. Imports + data load (top of file, after existing constants)
```python
DISTRICTS_DETAIL_JSON = ROOT / "districts-detail.json"

def load_details():
    """Merge enriched data by district code. Missing file → empty dict → graceful degrade."""
    if not DISTRICTS_DETAIL_JSON.exists():
        return {}
    raw = json.loads(DISTRICTS_DETAIL_JSON.read_text())
    return {d["code"]: d for d in raw.get("districts", [])}

# Compute tier medians ONCE (cheap; ~28 districts).
def compute_tier_medians(details):
    by_tier = {"CCR": [], "RCR": [], "OCR": []}
    for d in details.values():
        if d.get("psf_range_sgd") and d.get("region") in by_tier:
            lo, hi = d["psf_range_sgd"]
            by_tier[d["region"]].append((lo + hi) / 2)
    med = {}
    for k, arr in by_tier.items():
        if arr:
            arr.sort()
            med[k] = arr[len(arr) // 2]
    # Fallback defaults so charts still render on partial data
    return {"CCR": med.get("CCR", 3200), "RCR": med.get("RCR", 2200), "OCR": med.get("OCR", 1750)}
```

Call both at the top of `main()` (or wherever `render_district` is invoked) and pass `detail = details.get(code, {})` + `medians` into `render_district`.

### 2b. `render_district(d, detail, medians)` signature change
- Keep all existing f-string blocks untouched — they work off `d` (POV/region/areas).
- Build chart snippets BEFORE the template string:
```python
psf_rng = detail.get("psf_range_sgd") or []
yield_rng = detail.get("yield_range") or []
travel = detail.get("travel_times") or {}
captions = detail.get("chart_captions") or {}

chart_region  = region_spectrum(region, captions.get("region"))
chart_psf     = psf_range_bar(*psf_rng, medians["CCR"], medians["RCR"], medians["OCR"], captions.get("psf")) if len(psf_rng) == 2 else ""
chart_yield   = yield_meter(*yield_rng, captions.get("yield")) if len(yield_rng) == 2 else ""
chart_travel  = travel_times_ribbon(travel.get("cbd"), travel.get("orchard"), travel.get("changi"), captions.get("travel"))
chart_supply  = supply_timeline(detail.get("notable_projects") or [], captions.get("supply"))
```

### 2c. Template injection points
Inside the existing f-string for `html`, insert **after the POV section (`</section>` following "The investor POV on {code}.")** a new block:
```html
<div class="divider max-w-4xl mx-auto"></div>
<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">The data read</p>
  {chart_region}
  {chart_psf}
  {chart_yield}
  {chart_travel}
  {chart_supply}
</section>
```
Because each function returns `""` on missing data, the section can go empty (5 blanks) — acceptable for backward compat, but we'll also wrap the whole `<section>` in a conditional:
```python
data_section = ""
charts = [chart_region, chart_psf, chart_yield, chart_travel, chart_supply]
if any(charts):
    data_section = f'<div class="divider max-w-4xl mx-auto"></div>\n<section class="max-w-4xl mx-auto px-6 py-12">\n  <p class="section-label mb-4">The data read</p>\n  {"".join(charts)}\n</section>'
```
Inject `{data_section}` in the template.

### 2d. Captions source
**Decision:** store captions in `districts-detail.json` under a `chart_captions` object per district (keys: `region`, `psf`, `yield`, `travel`, `supply`). Rationale: the interpretive sentence is editorial, not mechanical — content-director should be able to tune phrasing without a code change. Generator treats missing caption keys as "no caption" (chart renders without figcaption).

### 2e. New narrative sections (from detail JSON)
Also add, conditional on presence, after the 4-Pillar block:
- **Who it suits** — `detail["suits"]` as a 3-item list
- **FAQs** — `detail["faqs"]` as `<details>` accordions (SEO-friendly, zero JS)

### 2f. Backward compat
If `districts-detail.json` is missing or malformed, `load_details()` returns `{}`, all chart funcs return `""`, `data_section` becomes `""`, narrative extras are skipped → current page layout unchanged. No regressions.

### 2g. Build + verify
```bash
cd ~/crestbrick-consult
python3 _render_area_pages.py                          # regenerates public/districts/*.html
npx tailwindcss -i src/input.css -o public/tw.css --minify
vercel --prod=false                                    # preview deploy
# Open preview URL → check 3 sample pages (see Test plan §4)
# Once green:
vercel --prod
```

Verification: `ls public/districts/*.html | wc -l` should be 28; each file > 18 KB (vs current ~12 KB).

---

## 3. Bandwidth / performance

**Per-page size delta estimate:**
- 5 SVG charts, avg 700 bytes each after Python renders values → ~3.5 KB
- Shared `<style>` block (chart CSS) → ~1.1 KB, **once per page**
- New narrative ~500 words → ~3.2 KB raw → ~2.3 KB after gzip
- FAQ accordions (6 entries) → ~2 KB

Total per page: **~8–10 KB before gzip, ~3–4 KB over the wire** (Vercel gzip+brotli). Current district page ~12 KB → new ~22 KB raw / ~6 KB gzipped. Well under Lighthouse mobile budget (200 KB HTML).

**LCP risk:** charts sit below the fold (after POV section). Hero H1 remains the LCP candidate → unchanged. No images added, no web fonts added (Fraunces/Inter already preloaded).

**CLS risk:** inline SVG with fixed `viewBox` reserves aspect ratio via native aspect-ratio inference. Zero layout shift expected. Mitigation if it appears: add explicit `height: auto` (already in `.cb-chart svg`) and set `aspect-ratio` on the wrapping `<figure>` for older Safari — not needed per current baseline browsers.

**No JS added.** No network requests added. Tailwind class additions all use existing utilities → `tw.css` size unchanged (still 16 KB).

---

## 4. Test plan

**3 sample districts to render first** (cover all three region tiers + chart variants):

| District | Why |
|---|---|
| **D1 Raffles Place · Marina** | CCR + low/no supply pipeline → tests `supply_timeline` returning `""` cleanly; tests high-end of PSF bar (>$3K). |
| **D15 East Coast · Katong** | RCR + active new-launch supply → dense `supply_timeline`; mid-range PSF positioning; yield band around 3%. |
| **D19 Serangoon/Hougang/Punggol** | OCR upgrader-heartland → lower PSF band; yield comfortably above 3% floor; strong MRT/travel story. |

### 4a. Visual check (open in preview deploy)
- All 5 charts render on D15 and D19; D1 has 4 charts (supply correctly absent)
- Region spectrum marker sits over the correct label (CCR/RCR/OCR)
- PSF band for D19 sits left of the OCR median tick; D1 sits right of the CCR tick
- Yield meter: 3% floor line is visible and labelled
- Travel ribbon: at least one bar uses "good" green, one uses accent/brown
- Mobile (Chrome devtools 375px): no horizontal scroll, text readable ≥11px
- Captions wrap without orphans; italic serif style applied

### 4b. View-source / DOM checks
- `<script type="application/ld+json">` schema block present and valid JSON (paste into https://search.google.com/test/rich-results)
- SVG has no `<script>`, no external `<image href=>`, no external font refs
- `role="img"` + `aria-label` on every `<figure class="cb-chart">`
- No stray `{placeholder}` tokens left unreplaced (grep the rendered HTML for `{` followed by a letter)
- `<figcaption>` present for every chart that has data in `chart_captions`

### 4c. Data-integrity check
```bash
python3 -c "import json; d = json.load(open('public/districts-detail.json')); print(len(d['districts']), 'districts'); [print(x['code'], x.get('psf_range_sgd'), x.get('yield_range')) for x in d['districts']]"
```
All 28 codes present; psf_range_sgd and yield_range are 2-element numeric lists.

### 4d. Regression
- `public/districts.html` (index) untouched — spot-check it still lists 28 links
- HDB town pages (`render_town`) untouched — spot-check 1 page renders as before

### 4e. Rollback
Generator is idempotent — re-running with `districts-detail.json` removed or renamed restores the previous template output. Keep current `public/districts/` in git; a bad deploy reverts via `git checkout public/districts/ && vercel --prod`.

---

## 5. Open questions for peers

- **property-researcher:** please confirm `districts-detail.json` schema uses the field names above (`psf_range_sgd`, `yield_range`, `travel_times.cbd/orchard/changi`, `notable_projects[].{name,year,psf_range}`, `chart_captions.{region,psf,yield,travel,supply}`). If you've already chosen different names, I'll adapt in one commit — no re-render needed until JSON is final.
- **creative-director-mgr:** `charts.html` uses only brand tokens. If your spec asks for any non-token colour (e.g. a specific red for "above threshold"), tell me which threshold cases and I'll add one named colour to the shared style block.
- **content-director:** `chart_captions` is the editorial insertion point. One-sentence captions, ≤160 chars, first-person POV encouraged (same voice as the POV paragraph).
