#!/usr/bin/env node
// seo-entity-meta.mjs — fill structured-data + social-meta gaps:
//  (1) add og:image + twitter card to pages missing them (default brand image)
//  (2) add baseline WebPage + BreadcrumbList JSON-LD to real content pages that have NO schema
//      (skips redirect stubs, partials, and noindex pages)
// Idempotent. Run with cwd = the repo/worktree. --dry-run to preview.

import { readFileSync, writeFileSync } from 'node:fs';
import { globSync } from 'node:fs';

const DRY = process.argv.includes('--dry-run');
const OG = 'https://winfredquek.com/img/og-image.jpg';
const title = (h, slug) => ((h.match(/<title>(.*?)<\/title>/is) || [, slug])[1]).replace(/\s*[|,]\s*Winfred Quek.*$/i, '').split(/\s*[|]\s*/)[0].replace(/&amp;/g, '&').trim();
const titleCase = s => s.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

const files = globSync('public/**/*.html');
let og = 0, sch = 0;
for (const f of files) {
  let h = readFileSync(f, 'utf8');
  const rel = f.replace(/^public\//, '');
  if (rel.startsWith('_snippets/')) continue;
  let changed = false;

  // (1) og:image + twitter
  if (!/property=["']og:image["']/i.test(h) && /<\/head>/i.test(h)) {
    let add = `  <meta property="og:image" content="${OG}" />\n`;
    if (!/name=["']twitter:card["']/i.test(h)) add += `  <meta name="twitter:card" content="summary_large_image" />\n`;
    if (!/name=["']twitter:image["']/i.test(h)) add += `  <meta name="twitter:image" content="${OG}" />\n`;
    h = h.replace(/<\/head>/i, add + '</head>');
    og++; changed = true;
  }

  // (2) baseline schema for real content pages with none
  const isStub = /http-equiv=["']refresh["']/i.test(h) || /name=["']robots["'][^>]*noindex/i.test(h);
  if (!/application\/ld\+json/i.test(h) && !isStub && /<\/head>/i.test(h)) {
    const slug = rel.replace(/\.html$/, '').replace(/\/index$/, '');
    const url = `https://winfredquek.com/${slug}`;
    const segs = slug.split('/');
    const crumbs = [{ n: 'Home', u: 'https://winfredquek.com/' }];
    if (segs.length > 1) crumbs.push({ n: titleCase(segs[0]), u: `https://winfredquek.com/${segs[0]}` });
    crumbs.push({ n: title(h, slug), u: url });
    const webpage = { '@context': 'https://schema.org', '@type': 'WebPage', name: title(h, slug), url, isPartOf: { '@type': 'WebSite', name: 'Winfred Quek', url: 'https://winfredquek.com' }, publisher: { '@type': 'Organization', name: 'Crestbrick Pte Ltd', identifier: 'L31010886H' } };
    const bc = { '@context': 'https://schema.org', '@type': 'BreadcrumbList', itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.n, item: c.u })) };
    const block = `  <script type="application/ld+json">\n${JSON.stringify(webpage)}\n</script>\n  <script type="application/ld+json">\n${JSON.stringify(bc)}\n</script>\n`;
    h = h.replace(/<\/head>/i, block + '</head>');
    sch++; changed = true;
  }
  if (changed && !DRY) writeFileSync(f, h);
}
console.log(`${DRY ? '[dry] ' : ''}entity-meta: og:image added ${og}, baseline schema added ${sch}`);
