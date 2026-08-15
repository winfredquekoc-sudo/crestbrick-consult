#!/usr/bin/env node
// gen-sitemap.mjs — generates public/sitemap.xml (as a sitemap INDEX) plus
// per-content-type child sitemaps (public/sitemap-<type>.xml) from the real
// public/ file tree, real git history, and the live redirect table.
//
// Conventions (see vercel.json): cleanUrls=true, trailingSlash=false, so
// /foo/bar.html serves at /foo/bar and /foo/index.html serves at /foo.
//
// Excludes: public/_snippets/**, public/404.html, any page whose robots meta
// contains "noindex", and any URL that is itself a redirect source in
// vercel.json (a redirecting URL must not appear in a sitemap).
//
// lastmod is derived from `git log -1 --format=%cI -- <path>` (last commit
// touching the file), falling back to filesystem mtime for files with no git
// history. Idempotent — re-running produces the same output for an unchanged tree.
//
// Usage: node scripts/gen-sitemap.mjs [--dry-run]

import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { execFileSync } from 'node:child_process';

const ROOT = new URL('../', import.meta.url).pathname;
const PUBLIC = join(ROOT, 'public');
const DRY = process.argv.includes('--dry-run');
const BASE = 'https://winfredquek.com';

// ---------------------------------------------------------------------------
// 1. Walk public/ for .html files, excluding _snippets/ and 404.html.
// ---------------------------------------------------------------------------
function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (name.startsWith('.') || name === 'node_modules') continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) {
      if (name === '_snippets') continue;
      yield* walk(p);
    } else if (name.endsWith('.html')) {
      yield p;
    }
  }
}

const files = [...walk(PUBLIC)].filter(f => {
  const rel = relative(PUBLIC, f);
  if (rel === '404.html') return false;
  return true;
});

// ---------------------------------------------------------------------------
// 2. Derive the served URL for a file per cleanUrls/trailingSlash conventions.
// ---------------------------------------------------------------------------
function urlFor(file) {
  const rel = relative(PUBLIC, file).split(sep).join('/'); // e.g. "insights/foo.html"
  let path;
  if (rel === 'index.html') {
    path = '';
  } else if (rel.endsWith('/index.html')) {
    path = rel.slice(0, -'/index.html'.length);
  } else {
    path = rel.slice(0, -'.html'.length);
  }
  return path ? `${BASE}/${path}` : `${BASE}/`;
}

// ---------------------------------------------------------------------------
// 3. Exclude noindex pages (parsed from the robots meta tag, not hardcoded).
// ---------------------------------------------------------------------------
const ROBOTS_RE = /<meta\s+name=["']robots["']\s+content=["']([^"']*)["']/i;
function isNoindex(file) {
  const html = readFileSync(file, 'utf8');
  const m = html.match(ROBOTS_RE);
  return !!m && /noindex/i.test(m[1]);
}

// ---------------------------------------------------------------------------
// 4. Exclude URLs that are themselves redirect sources in vercel.json — a
//    redirecting URL must not appear in a sitemap.
// ---------------------------------------------------------------------------
const vercelConfig = JSON.parse(readFileSync(join(ROOT, 'vercel.json'), 'utf8'));
const redirectSources = new Set(
  (vercelConfig.redirects || [])
    .filter(r => !r.has) // skip host-conditional rules (e.g. the vercel.app -> winfredquek.com rule); those aren't public/ paths
    .map(r => `${BASE}${r.source}`)
);

// ---------------------------------------------------------------------------
// 5. lastmod: git commit date, falling back to filesystem mtime.
// ---------------------------------------------------------------------------
function lastmodFor(file) {
  try {
    const out = execFileSync('git', ['log', '-1', '--format=%cI', '--', file], {
      cwd: ROOT,
      encoding: 'utf8',
    }).trim();
    if (out) return out;
  } catch {
    // not a git repo / git unavailable — fall through to mtime
  }
  return new Date(statSync(file).mtime).toISOString();
}

// ---------------------------------------------------------------------------
// 6. Content-type grouping for child sitemaps. First path segment is the
//    type; anything not in the "big" list below (or at site root) goes into
//    a single "core" child rather than 20+ near-empty files.
// ---------------------------------------------------------------------------
const BIG_TYPES = new Set([
  'insights', 'area', 'glossary', 'answers', 'launches',
  'tools', 'districts', 'hdb-prices', 'hdb-towns',
]);

function typeFor(pathAfterBase) {
  const seg = pathAfterBase.replace(/^\//, '').split('/')[0];
  if (!seg) return 'core'; // homepage
  return BIG_TYPES.has(seg) ? seg : 'core';
}

// changefreq: the current sitemap uses "weekly" for essentially everything
// except the homepage ("daily") — a sensible, defensible convention, so it's
// kept. priority in the current file is NOT a sensible per-type convention
// (e.g. glossary entries carry the same priority as core service pages, and
// the "monthly"/"weekly" split for insights looks like an artifact of how
// drip scripts vs. the manual stamp wrote entries, not intentional weighting)
// — so priority is omitted entirely; Google ignores it, and an honest
// omission beats a fabricated number. See report for detail.
function changefreqFor(url) {
  return url === `${BASE}/` ? 'daily' : 'weekly';
}

// ---------------------------------------------------------------------------
// 7. Build the URL list.
// ---------------------------------------------------------------------------
const seen = new Map(); // url -> record, dedupes exact-duplicate blocks
let excludedNoindex = 0;
let excludedRedirect = 0;
let excludedNoServedUrl = 0;

for (const file of files) {
  const url = urlFor(file);
  if (!url) { excludedNoServedUrl++; continue; }
  if (isNoindex(file)) { excludedNoindex++; continue; }
  if (redirectSources.has(url)) { excludedRedirect++; continue; }
  if (seen.has(url)) continue; // dedupe
  seen.set(url, {
    url,
    lastmod: lastmodFor(file),
    changefreq: changefreqFor(url),
    type: typeFor(url.slice(BASE.length)),
  });
}

const records = [...seen.values()].sort((a, b) => a.url.localeCompare(b.url));

// ---------------------------------------------------------------------------
// 8. Group by type, write child sitemaps + index.
// ---------------------------------------------------------------------------
const byType = new Map();
for (const r of records) {
  if (!byType.has(r.type)) byType.set(r.type, []);
  byType.get(r.type).push(r);
}

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function urlsetXml(recs) {
  const body = recs.map(r => (
    `  <url>\n    <loc>${esc(r.url)}</loc>\n    <lastmod>${r.lastmod}</lastmod>\n    <changefreq>${r.changefreq}</changefreq>\n  </url>`
  )).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`;
}

const now = new Date().toISOString();
const childFiles = [];
for (const [type, recs] of [...byType.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
  const filename = `sitemap-${type}.xml`;
  const xml = urlsetXml(recs);
  childFiles.push({ type, filename, count: recs.length });
  if (!DRY) writeFileSync(join(PUBLIC, filename), xml);
}

// ---------------------------------------------------------------------------
// 7b. Reference the standalone image sitemap (built separately by
//     scripts/gen-image-sitemap.py, a Python script with no relation to the
//     HTML crawl above) in the index. This script only owns the index plus
//     the HTML-derived children, so the image sitemap is listed, not written.
// ---------------------------------------------------------------------------
const IMAGE_SITEMAP = 'sitemap-images.xml';
if (existsSync(join(PUBLIC, IMAGE_SITEMAP))) {
  const count = (readFileSync(join(PUBLIC, IMAGE_SITEMAP), 'utf8').match(/<url>/g) || []).length;
  childFiles.push({ type: 'images', filename: IMAGE_SITEMAP, count });
}

const indexXml = `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${
  childFiles.map(c => `  <sitemap>\n    <loc>${BASE}/${c.filename}</loc>\n    <lastmod>${now}</lastmod>\n  </sitemap>`).join('\n')
}\n</sitemapindex>\n`;

if (!DRY) writeFileSync(join(PUBLIC, 'sitemap.xml'), indexXml);

// ---------------------------------------------------------------------------
// 9. Report.
// ---------------------------------------------------------------------------
console.log(`gen-sitemap [${DRY ? 'DRY RUN' : 'WROTE'}]`);
console.log(`  html files scanned: ${files.length}`);
console.log(`  excluded (noindex): ${excludedNoindex}`);
console.log(`  excluded (redirect source): ${excludedRedirect}`);
console.log(`  excluded (no served url, e.g. 404.html): ${excludedNoServedUrl}`);
console.log(`  total urls in sitemap set: ${records.length}`);
for (const c of childFiles) console.log(`    ${c.filename}: ${c.count}`);
console.log(`  index -> public/sitemap.xml (${childFiles.length} children)`);
