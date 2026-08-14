#!/usr/bin/env node
// insights-drip.mjs — general staged-article publisher (adapted from lentor-drip.mjs).
// Surfaces manifest articles into sitemap.xml, news-sitemap.xml, llms.txt and the
// insights.html hub, N per day, idempotently and catch-up safe. Manifest-driven:
// works for any staged cluster, not just Lentor.
//
// Usage: node scripts/insights-drip.mjs --root=<repo-root> --staged=<state-dir> [--date=YYYY-MM-DD] [--dry-run]

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'node:fs';
import { join } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).map(a => {
  const m = a.match(/^--([^=]+)(?:=(.*))?$/); return m ? [m[1], m[2] ?? true] : [a, true];
}));
const ROOT = args.root || process.cwd();
const STAGED = args.staged;
if (!STAGED) { console.error('missing --staged'); process.exit(1); }
const DRY = !!args['dry-run'];
const TARGET = typeof args.date === 'string' ? args.date : new Date().toISOString().slice(0, 10);

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const firstSentence = s => { const m = String(s || '').match(/^.*?[.!?](\s|$)/); return (m ? m[0] : String(s || '')).trim(); };

const manifest = JSON.parse(readFileSync(join(STAGED, 'manifest.json'), 'utf8'));
const LABEL = manifest.hubLabel || 'Guides';
const LLMS_HEAD = manifest.llmsSection || '## Property guides (2026)';
const LLMS_MARK = `<!-- ${manifest.cluster} -->`;
const HUB_ID = `${manifest.cluster}-cards`;
const due = manifest.articles.filter(a => a.date <= TARGET).sort((a, b) => a.id - b.id);

const P = {
  // NOTE: public/sitemap.xml is a sitemap INDEX (see scripts/gen-sitemap.mjs)
  // — its child sitemaps are per content type, so drip-published insights
  // articles splice into the insights child, not the index. Splicing into
  // the index itself would silently no-op (no </urlset> to match, since an
  // index is a <sitemapindex>, not a <urlset>) and the article would never
  // surface in search. The next `node scripts/gen-sitemap.mjs` run (part of
  // `npm run build`) regenerates this file wholesale from the real public/
  // tree anyway, so this splice only needs to hold the gap until then.
  sitemap: join(ROOT, 'public/sitemap-insights.xml'),
  news: join(ROOT, 'public/news-sitemap.xml'),
  llms: join(ROOT, 'public/llms.txt'),
  hub: join(ROOT, 'public/insights.html'),
};
if (!existsSync(P.sitemap) && !DRY) {
  // gen-sitemap.mjs normally creates this; guard so a drip run standalone
  // (before the first build) doesn't crash on a missing child sitemap.
  writeFileSync(P.sitemap, '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n</urlset>\n');
}
let sm = existsSync(P.sitemap) ? readFileSync(P.sitemap, 'utf8') : '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n</urlset>\n';
let nws = readFileSync(P.news, 'utf8');
let llms = readFileSync(P.llms, 'utf8');
let hub = readFileSync(P.hub, 'utf8');

if (!llms.includes(LLMS_HEAD)) {
  llms = llms.replace('## Foreign buyer guides', `${LLMS_HEAD}\n${LLMS_MARK}\n\n## Foreign buyer guides`);
}
const HUB_GRID_OPEN = `<div class="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-4" id="${HUB_ID}">`;
if (!hub.includes(`id="${HUB_ID}"`)) {
  const section = `<!-- ${manifest.cluster} cards -->
<section class="max-w-6xl mx-auto px-6 pb-10">
  <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Latest guides</p>
  ${HUB_GRID_OPEN}</div>
</section>

<!-- Category filter -->`;
  hub = hub.replace('<!-- Category filter -->', section);
}

const published = [];
for (const a of due) {
  const url = `https://winfredquek.com/insights/${a.slug}`;
  let did = false;
  const dst = join(ROOT, 'public/insights', `${a.slug}.html`);
  if (a.refresh) {
    // refresh entry: overwrite the live article and bump its sitemap lastmod
    const src = join(STAGED, 'staged/insights', `${a.slug}.html`);
    if (!existsSync(src)) { console.error(`  ! no file for ${a.slug} — skipping`); continue; }
    if (!DRY) copyFileSync(src, dst);
    const lm = new RegExp(`(<loc>${url.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}</loc>\\s*<lastmod>)[^<]*`);
    if (lm.test(sm)) sm = sm.replace(lm, `$1${a.date}`);
    did = true;
  } else if (!existsSync(dst)) {
    const src = join(STAGED, 'staged/insights', `${a.slug}.html`);
    if (existsSync(src)) { if (!DRY) copyFileSync(src, dst); did = true; }
    else { console.error(`  ! no file for ${a.slug} — skipping`); continue; }
  }
  if (!sm.includes(`<loc>${url}</loc>`)) {
    sm = sm.replace('</urlset>', `  <url>\n    <loc>${url}</loc>\n    <lastmod>${a.date}</lastmod>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n</urlset>`);
    did = true;
  }
  if (!nws.includes(`<loc>${url}</loc>`)) {
    nws = nws.replace('</urlset>', `  <url>\n    <loc>${url}</loc>\n    <news:news>\n      <news:publication>\n        <news:name>Winfred Quek Singapore Property Advisory</news:name>\n        <news:language>en</news:language>\n      </news:publication>\n      <news:publication_date>${a.date}</news:publication_date>\n      <news:title>${esc(a.title)}</news:title>\n      <news:keywords>Singapore property, ${esc(LABEL)}</news:keywords>\n    </news:news>\n  </url>\n</urlset>`);
    did = true;
  }
  if (!llms.includes(url)) {
    llms = llms.replace(LLMS_MARK, `${LLMS_MARK}\n- [${a.title}](${url}): ${firstSentence(a.description)}`);
    did = true;
  }
  if (!hub.includes(`/insights/${a.slug}"`)) {
    const card = `    <a href="/insights/${a.slug}" class="article card-lift block bg-[#1c1e26] border border-[var(--rule)] rounded-xl p-6">\n      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">${esc(LABEL)}</p>\n      <h3 class="serif text-xl font-semibold mb-2">${esc(a.title)}</h3>\n      <p class="text-[var(--ink-soft)] text-sm mb-3">${esc(String(a.description || '').slice(0, 140))}</p>\n      <p class="text-xs text-[var(--ink-muted)]">Read <span class="float-right">9 min</span></p>\n    </a>\n`;
    hub = hub.replace(HUB_GRID_OPEN, `${HUB_GRID_OPEN}\n${card}`);
    did = true;
  }
  if (did) published.push(a.slug);
}

if (!DRY) {
  writeFileSync(P.sitemap, sm);
  writeFileSync(P.news, nws);
  writeFileSync(P.llms, llms);
  writeFileSync(P.hub, hub);
}

const total = manifest.articles.length;
const liveCount = manifest.articles.filter(a => sm.includes(`<loc>https://winfredquek.com/insights/${a.slug}</loc>`)).length;
const remaining = total - liveCount;
console.log(`insights-drip [${DRY ? 'DRY RUN' : 'WROTE'}] target=${TARGET}`);
console.log(`  newly surfaced this run: ${published.length} -> ${published.join(', ') || '(none)'}`);
console.log(`  cluster surfaced: ${liveCount}/${total}`);
console.log(`REMAINING_UNPUBLISHED=${remaining}`);
