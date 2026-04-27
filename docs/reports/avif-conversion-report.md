# AVIF Conversion Report

**Date:** 2026-04-27
**Tooling installed:** `sharp@latest` (npm devDependency) — single-step install, used for both AVIF (q=50) and WebP (q=80) generation. `libavif`/`avifenc` not needed.

## Summary

| Metric | Value |
|---|---|
| Images converted | 60 (JPG → AVIF + WebP) |
| Images failed | 0 |
| HTML pages updated | 35 (of 244 scanned) |
| `<img>` tags wrapped in `<picture>` | 39 |
| Total original size | 10,234 KB |
| Total AVIF size | 4,905 KB |
| Total WebP size | 7,797 KB |
| **AVIF reduction** | **52.1%** (saved 5,329 KB) |
| WebP reduction | 23.8% |

## Conversion Strategy

- AVIF generated at quality 50 (sharp default-balanced)
- WebP generated at quality 80 (middle fallback)
- Original JPG/PNG kept untouched (final fallback)
- Naming: `image.jpg` → adds `image.avif` + `image.webp` siblings

## HTML Markup Pattern

Each static `<img src="…jpg">` is wrapped:

```html
<picture>
  <source srcset="/img/hero.avif?v=3" type="image/avif">
  <source srcset="/img/hero.webp?v=3" type="image/webp">
  <img src="/img/hero.jpg?v=3" alt="…" loading="eager" fetchpriority="high">
</picture>
```

Original `loading`/`fetchpriority`/`class`/`alt`/`width`/`height`/`?v=3` cache-buster preserved.

## Standout Reductions

| File | Original | AVIF | Reduction |
|---|---|---|---|
| rivelle-tampines-ec.jpg | 936 KB | 51 KB | **94.5%** |
| bloomsbury-residences.jpg | 808 KB | 461 KB | 43.0% |
| one-marina-gardens.jpg | 524 KB | 156 KB | 70.3% |
| narra-residences.jpg | 321 KB | 134 KB | 58.4% |
| winfred-hero.jpg | 36 KB | 6.5 KB | 82.0% |
| og-image.jpg | 25 KB | 5.8 KB | 77.1% |

## Why only 35 HTML pages updated?

The site has 244 HTML files but most launch/listing/insight pages render images via JavaScript (`new-launches.html`, `listings.html`, JSON-driven). Browsers serving those JPGs still get cache benefits but no `<picture>` swap. Static-rendered hero/profile/insight imagery on `/`, `/about.html`, `/insights/*`, etc. is fully wrapped.

## Validation

- `html.parser` validation: 0 failures across all 244 files
- All edits applied atomically; no partial writes

## Failed Conversions

None. All 60 candidate images converted successfully.

## Files Skipped (intentional)

- SVG files (already vector)
- `favicon*` and icons <10KB
- `apple-touch-icon.png`, `favicon-32.png` (icon size)

## Deployment

- **Production URL:** https://crestbrick-consult-d3yz2w8mv-winfredquekoc-3888s-projects.vercel.app
- **Inspector:** https://vercel.com/winfredquekoc-3888s-projects/crestbrick-consult/GdLiukgn5ZGrpyXvaA1Ds1Ahgzb6
- **Status:** READY (production)

## Scripts (kept for re-runs)

- `/Users/winfredquek/crestbrick-consult/scripts/_convert_avif.mjs` — sharp-based batch converter
- `/Users/winfredquek/crestbrick-consult/scripts/_wrap_picture.py` — `<img>` → `<picture>` HTML rewriter (idempotent: skips already-wrapped imgs)
- `/Users/winfredquek/crestbrick-consult/scripts/_avif-results.json` — per-file conversion ledger
