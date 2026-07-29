#!/usr/bin/env node
// lentor-drip.mjs — progressively surface the Lentor Gardens Residences article cluster
// into the site's discovery surfaces (sitemap.xml, news-sitemap.xml, llms.txt, and the
// insights.html hub), 3 per day, idempotently. Publishes every manifest article whose
// publish date is on or before the target date and which is not already surfaced, so a
// missed day self-heals on the next run.
//
// Usage:
//   node scripts/lentor-drip.mjs --root=<repo-root> [--staged=<dir>] [--date=YYYY-MM-DD] [--dry-run]
//
// --root    repo root whose public/ files get edited (a clean main worktree in production).
// --staged  dir containing manifest.json and staged/insights/<slug>.html (defaults to
//           <root>/content/lentor-cluster). Article files are copied in if missing.
// --date    target date (defaults to today). --dry-run prints actions without writing.

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'node:fs';
import { join } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).map(a => {
  const m = a.match(/^--([^=]+)(?:=(.*))?$/); return m ? [m[1], m[2] ?? true] : [a, true];
}));
const ROOT = args.root || process.cwd();
const STAGED = args.staged || join(ROOT, 'content/lentor-cluster');
const DRY = !!args['dry-run'];
const TARGET = typeof args.date === 'string' ? args.date : new Date().toISOString().slice(0, 10);

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const firstSentence = s => { const m = String(s).match(/^.*?[.!?](\s|$)/); return (m ? m[0] : String(s)).trim(); };
const CLUSTER_LABEL = {
  pillar: 'Lentor · Overview', price: 'Lentor · Price', comparison: 'Lentor · Comparison',
  location: 'Lentor · Location', buyer: 'Lentor · Buyer guide', investor: 'Lentor · Investment',
  financial: 'Lentor · Financing', process: 'Lentor · Process',
};

const manifest = JSON.parse(readFileSync(join(STAGED, 'manifest.json'), 'utf8'));
const due = manifest.articles.filter(a => a.date <= TARGET).sort((a, b) => a.id - b.id);

const P = {
  sitemap: join(ROOT, 'public/sitemap.xml'),
  news: join(ROOT, 'public/news-sitemap.xml'),
  llms: join(ROOT, 'public/llms.txt'),
  hub: join(ROOT, 'public/insights.html'),
};
let sm = readFileSync(P.sitemap, 'utf8');
let nws = readFileSync(P.news, 'utf8');
let llms = readFileSync(P.llms, 'utf8');
let hub = readFileSync(P.hub, 'utf8');

// Ensure llms.txt cluster section + insertion marker exist.
const LLMS_HEAD = '## Lentor Gardens Residences, District 26 (new launch 2026)';
const LLMS_MARK = '<!-- lentor-cluster -->';
if (!llms.includes(LLMS_HEAD)) {
  llms = llms.replace('## Foreign buyer guides', `${LLMS_HEAD}\n${LLMS_MARK}\n\n## Foreign buyer guides`);
}
// Ensure insights.html hub section + card grid exist.
const HUB_ID = 'lentor-cluster-cards';
const HUB_GRID_OPEN = `<div class="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-4" id="${HUB_ID}">`;
if (!hub.includes(`id="${HUB_ID}"`)) {
  const section = `<!-- Lentor Gardens Residences cluster -->
<section class="max-w-6xl mx-auto px-6 pb-10">
  <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Lentor Gardens Residences &middot; New launch 2026</p>
  ${HUB_GRID_OPEN}</div>
  <a href="/insights/lentor-gardens-residences-review-2026" class="text-sm" style="color:var(--accent);">Read the full Lentor Gardens Residences review &rarr;</a>
</section>

<!-- Category filter -->`;
  hub = hub.replace('<!-- Category filter -->', section);
}

const published = [];
for (const a of due) {
  const url = `https://winfredquek.com/insights/${a.slug}`;
  let did = false;

  // 1. Copy the article file into the repo if missing.
  const dst = join(ROOT, 'public/insights', `${a.slug}.html`);
  if (!existsSync(dst)) {
    const src = join(STAGED, 'staged/insights', `${a.slug}.html`);
    if (existsSync(src)) { if (!DRY) copyFileSync(src, dst); did = true; }
    else { console.error(`  ! no file for ${a.slug} (not in repo or staging) — skipping`); continue; }
  }

  // 2. sitemap.xml
  if (!sm.includes(`<loc>${url}</loc>`)) {
    sm = sm.replace('</urlset>', `  <url>\n    <loc>${url}</loc>\n    <lastmod>${a.date}</lastmod>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n</urlset>`);
    did = true;
  }
  // 3. news-sitemap.xml
  if (!nws.includes(`<loc>${url}</loc>`)) {
    nws = nws.replace('</urlset>', `  <url>\n    <loc>${url}</loc>\n    <news:news>\n      <news:publication>\n        <news:name>Winfred Quek Singapore Property Advisory</news:name>\n        <news:language>en</news:language>\n      </news:publication>\n      <news:publication_date>${a.date}</news:publication_date>\n      <news:title>${esc(a.title)}</news:title>\n      <news:keywords>${esc(a.keyword)}, Lentor, District 26, Singapore, new launch</news:keywords>\n    </news:news>\n  </url>\n</urlset>`);
    did = true;
  }
  // 4. llms.txt
  if (!llms.includes(url)) {
    llms = llms.replace(LLMS_MARK, `${LLMS_MARK}\n- [${a.title}](${url}): ${firstSentence(a.quickAnswer)}`);
    did = true;
  }
  // 5. insights.html hub card
  if (!hub.includes(`/insights/${a.slug}"`)) {
    const card = `    <a href="/insights/${a.slug}" class="article card-lift block bg-[#1c1e26] border border-[var(--rule)] rounded-xl p-6">\n      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">${CLUSTER_LABEL[a.cluster] || 'Lentor'}</p>\n      <h3 class="serif text-xl font-semibold mb-2">${esc(a.title)}</h3>\n      <p class="text-[var(--ink-soft)] text-sm mb-3">${esc(a.metaDescription.slice(0, 140))}</p>\n      <p class="text-xs text-[var(--ink-muted)]">Read <span class="float-right">11 min</span></p>\n    </a>\n`;
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
console.log(`lentor-drip [${DRY ? 'DRY RUN' : 'WROTE'}] target=${TARGET}`);
console.log(`  newly surfaced this run: ${published.length} -> ${published.join(', ') || '(none, all due already live)'}`);
console.log(`  cluster now surfaced: ${liveCount}/${total}`);
