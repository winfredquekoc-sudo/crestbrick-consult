# Site templates

Single-source-of-truth partials, stamped into every page on build.

- `nav.html` — the global site nav. Edit this file, then run
  `npm run stamp-nav` (or just `npm run build`) to propagate to all
  HTML pages in `public/`. Vercel runs it automatically on every deploy.

The stamping logic is in `scripts/stamp-nav.mjs`.
