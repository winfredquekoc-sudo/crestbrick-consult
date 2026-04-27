# District Detail Page — Chart Component Spec

Five inline-SVG chart primitives for the 28 district pages. Brand tokens only. No Chart.js, D3, or image assets. Target: a founder's notebook, not a dashboard.

---

## Page Layout Grid

Retain the existing `max-w-4xl mx-auto px-6 py-12` editorial column. Charts insert between existing blocks:

```
Hero (chips + H1 + neighbourhoods)          [existing]
── Chart 5 — Region Spectrum Position       [new, orientation strip]
── Winfred's POV                            [existing]
── Quick-read row: Chart 1 + Chart 2        [new, side-by-side on desktop]
── Chart 3 — Travel Times Ribbon            [new]
── Chart 4 — Supply Pipeline Timeline       [new, conditional]
── 4-Pillar mapping                         [existing]
── Related / CTA                            [existing]
```

Rationale: Chart 5 orients before the POV; Charts 1–2 substantiate it; Charts 3–4 ground the district in time and connectivity before the framework pivots back to strategy.

---

## Typography Hierarchy

| Element | Font | Size (desktop / mobile) | Weight | Color |
|---|---|---|---|---|
| Section label | Inter | 11px, tracking .18em, upper | 600 | `--accent` |
| Chart title (H2) | Fraunces | 30 / 24px | 600 | `--ink` |
| Axis / tick | Inter | 11px | 500 | `--ink-muted` |
| Value label | Inter | 13 / 12px | 600 | `--ink` |
| Caption | Inter | 14px, italic | 400 | `--ink-soft` |

Spacing: 24px title→body, 16px body→caption, 48px between charts.

---

## 1. PSF Range Bar

**Purpose.** Reader learns in 2 seconds whether the district is premium, mid, or value within its own region tier.

**Visual.** Horizontal track spanning S$1,200–$3,500 psf. Three faint background bands mark OCR / RCR / CCR median ranges left-to-right. One solid `--accent` bar overlays the district's psf range. A thin `--ink` tick marks the district median.

```html
<figure class="psf-range">
  <figcaption class="section-label">PSF range · transacted last 12mo</figcaption>
  <svg viewBox="0 0 600 90">
    <rect x="0"   y="40" width="200" height="12" fill="#e6e0d6" opacity=".5"/>
    <rect x="200" y="40" width="180" height="12" fill="#e6e0d6" opacity=".7"/>
    <rect x="380" y="40" width="220" height="12" fill="#e6e0d6"/>
    <rect x="{lo_x}" y="36" width="{range_w}" height="20" fill="var(--accent)" rx="2"/>
    <line x1="{med_x}" y1="28" x2="{med_x}" y2="64" stroke="var(--ink)" stroke-width="1.5"/>
    <text x="{lo_x}" y="24" class="value">S${lo}</text>
    <text x="{hi_x}" y="24" text-anchor="end" class="value">S${hi}</text>
  </svg>
  <p class="caption">{caption}</p>
</figure>
```

**Responsive.** SVG scales. Below 640px, tier labels switch to abbreviations via CSS media query.

**Data.** `{ psfLow, psfHigh, psfMedian, regionMedians: {ocr, rcr, ccr} }`

**Edge.** If `psfLow === psfHigh`, render a 4px marker. If `psfHigh > 3500`, rescale track.

**Caption.** *"D9 sits in the upper third of the CCR band — entry is premium even relative to its own tier."*

---

## 2. Yield Meter

**Purpose.** Does this district clear the 3% investor floor?

**Visual.** Horizontal ruler from 2% to 5%. A tan `--accent` segment marks the district's yield range. A dashed vertical at 3% labelled "yield floor" in muted ink.

```html
<figure class="yield-meter">
  <figcaption class="section-label">Gross yield · typical range</figcaption>
  <svg viewBox="0 0 600 80">
    <line x1="20" y1="44" x2="580" y2="44" stroke="var(--ink)"/>
    <g class="ticks"><!-- 2,3,4,5 --></g>
    <line x1="{floor_x}" y1="20" x2="{floor_x}" y2="60"
          stroke="var(--ink-muted)" stroke-dasharray="2 3"/>
    <text x="{floor_x}" y="74" text-anchor="middle" class="tick">3% floor</text>
    <rect x="{lo_x}" y="38" width="{range_w}" height="12" fill="var(--accent)" rx="2"/>
    <text x="{mid_x}" y="30" text-anchor="middle" class="value">{lo}–{hi}%</text>
  </svg>
  <p class="caption">{caption}</p>
</figure>
```

**Responsive.** Below 480px, tick labels drop the `%` symbol.

**Data.** `{ yieldLow, yieldHigh }` (percent values: 3.0 not 0.03).

**Edge.** If yield data missing, component returns null — no placeholder. Floor line does the editorial work, not colour.

**Caption.** *"D9 yields sit under the 3% floor — buy here for appreciation, not cashflow."*

---

## 3. Travel Times Ribbon

**Purpose.** Connectivity truth in three numbers: CBD, Orchard, Changi by MRT.

**Visual.** Three stacked rows, each a horizontal bar on a 0–60min axis. Label left, bar centre, minutes right. Colour by band: <20min = `--accent`, 20–35min = `--ink`, >35min = `--ink-muted`. No axis ticks — numerical labels carry it.

```html
<figure class="travel-ribbon">
  <figcaption class="section-label">MRT travel · peak/off-peak averaged</figcaption>
  <div class="ribbon-row" data-band="{band}">
    <span class="ribbon-label">CBD · Raffles Place</span>
    <div class="ribbon-track">
      <div class="ribbon-bar" style="width:{pct}%; background:{color}"></div>
    </div>
    <span class="ribbon-value">{min} min</span>
  </div>
  <!-- repeat: Orchard, Changi -->
  <p class="caption">{caption}</p>
</figure>
```
```css
.ribbon-row{display:grid;grid-template-columns:140px 1fr 56px;
  align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--rule);}
.ribbon-track{height:8px;background:#f1ebe0;border-radius:2px;}
.ribbon-bar{height:100%;border-radius:2px;}
```

**Responsive.** Mobile: label stacks above bar; grid becomes `1fr 56px`.

**Data.** `{ toCbd, toOrchard, toChangi }` (minutes).

**Edge.** Missing value → label + "—" in muted, no bar. All missing → section omitted.

**Caption.** *"Under 10 minutes to both CBD and Orchard — D9 is the only district with dual-node access."*

---

## 4. Supply Pipeline Timeline

**Purpose.** Are you buying ahead of, into, or after a supply wave?

**Visual.** Horizontal rule 2026–2028 with quarterly gridlines in `--rule`. Each announced or expected launch is an 8px `--accent` dot on its quarter. Project name above dot; unit count below in muted ink.

```html
<figure class="supply-timeline" data-empty="{isEmpty}">
  <figcaption class="section-label">Announced supply · 2026–2028</figcaption>
  <svg viewBox="0 0 600 140">
    <line x1="20" y1="80" x2="580" y2="80" stroke="var(--ink)"/>
    <g class="years"><!-- 2026 / 2027 / 2028 --></g>
    {launches.map(l => `
      <g transform="translate(${x(l.quarter)},80)">
        <circle r="5" fill="var(--accent)"/>
        <text y="-14" text-anchor="middle" class="value">${l.name}</text>
        <text y="24" text-anchor="middle" class="tick">${l.units}u</text>
      </g>`)}
  </svg>
  <ul class="supply-list"><!-- fallback list, CSS-toggled --></ul>
  <p class="caption">{caption}</p>
</figure>
```

**Responsive.** Below 640px with >3 launches, SVG hides and stacked list shows via `@media (max-width:640px){.supply-timeline svg{display:none;} .supply-list{display:block;}}`.

**Data.** `{ launches: [{ name, quarter: "2026Q3", units }] }`

**Edge.** If `launches.length === 0`, the entire figure + its surrounding divider are stripped at render. **No "No upcoming launches" placeholder** — absence is stronger signal.

**Caption.** *"Three launches across 2027 — D9 supply concentrates mid-cycle, pressuring 2028 resale comps."*

---

## 5. Region Spectrum Position

**Purpose.** Instant orientation. Where does this district sit on CCR → RCR → OCR?

**Visual.** A 1px `--ink` rule across the column, segmented by two muted ticks into three tiers. A 10px `--accent` diamond marks the district's position. Tier labels below in muted 11px. 72px tall, no chart title — the eyebrow label carries it.

```html
<figure class="region-spectrum">
  <figcaption class="section-label">Where D9 sits</figcaption>
  <svg viewBox="0 0 600 60">
    <line x1="20" y1="28" x2="580" y2="28" stroke="var(--ink)"/>
    <line x1="220" y1="22" x2="220" y2="34" stroke="var(--ink-muted)"/>
    <line x1="400" y1="22" x2="400" y2="34" stroke="var(--ink-muted)"/>
    <polygon points="{x},18 {x+6},28 {x},38 {x-6},28" fill="var(--accent)"/>
    <text x="120" y="52" text-anchor="middle" class="tick">CCR</text>
    <text x="310" y="52" text-anchor="middle" class="tick">RCR</text>
    <text x="490" y="52" text-anchor="middle" class="tick">OCR</text>
  </svg>
</figure>
```

**Responsive.** Scales natively.

**Data.** `{ position: number /* 0=deep CCR, 100=deep OCR */ }`

**Edge.** None — every district has a region. For boundary districts (e.g. D15), position near the tick is honest.

**Caption.** Deliberately wordless. Optional one-line italic `--ink-muted` note per district if narrative context helps.

---

## Mobile Behaviour Summary

| Chart | Adaptation |
|---|---|
| 1. PSF Range | SVG scales; tier labels abbreviate |
| 2. Yield Meter | SVG scales; ticks drop `%` |
| 3. Travel Ribbon | Label stacks above bar |
| 4. Supply Timeline | Swaps to stacked list at >3 launches |
| 5. Region Spectrum | No change |

All charts stay inside `max-w-4xl mx-auto px-6`. None introduce horizontal scroll. None exceed 160px tall on desktop.

---

## Caption Discipline

Every caption answers **"so what?"** — never **"what is this?"** The SVG shows what it is; the caption tells the investor what to do with it. One sentence, Inter italic, muted. If a caption can't reach a conclusion, the chart doesn't belong on the page.
