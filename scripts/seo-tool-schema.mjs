#!/usr/bin/env node
// seo-tool-schema.mjs — add WebApplication + BreadcrumbList JSON-LD to the calculator/tool
// pages that currently have no structured data, so AI engines and search understand them as
// free Singapore property tools. Templated from each page's own title/description/canonical.
// Idempotent (skips pages that already have ld+json or our marker). --dry-run to preview.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const DRY = process.argv.includes('--dry-run');
const MARK = '<!-- tool-schema (auto) -->';
const clean = t => t.replace(/\s*[|,]\s*Winfred Quek.*$/i, '').split(/\s*[|]\s*/)[0].trim();
const leaf = t => clean(t).split(/\s*,\s*/)[0].trim();
const attr = (h, re) => { const m = h.match(re); return m ? m[1].trim() : null; };

const targets = [];
for (const n of readdirSync('public/tools')) {
  if (n.endsWith('.html') && n !== 'index.html') targets.push(['public/tools/' + n, true]);
}
for (const n of ['absd-calculator', 'tdsr-calculator', 'stamp-duty-calculator']) {
  if (existsSync(`public/${n}.html`)) targets.push([`public/${n}.html`, false]);
}

let added = 0, skipped = 0;
for (const [path, isTools] of targets) {
  let h = readFileSync(path, 'utf8');
  if (h.includes('application/ld+json') || h.includes(MARK)) { skipped++; continue; }
  const title = attr(h, /<title>(.*?)<\/title>/is);
  const desc = attr(h, /name=["']description["']\s+content=["'](.*?)["']/is) || attr(h, /content=["'](.*?)["']\s+name=["']description["']/is);
  const canon = attr(h, /rel=["']canonical["']\s+href=["'](.*?)["']/i);
  if (!title || !canon) { skipped++; continue; }
  const name = clean(title);
  const crumbs = [{ n: 'Home', u: 'https://winfredquek.com/' }];
  if (isTools) crumbs.push({ n: 'Tools', u: 'https://winfredquek.com/tools' });
  crumbs.push({ n: leaf(title), u: canon });
  const breadcrumb = {
    '@context': 'https://schema.org', '@type': 'BreadcrumbList',
    itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.n, item: c.u })),
  };
  const app = {
    '@context': 'https://schema.org', '@type': 'WebApplication',
    name, url: canon, applicationCategory: 'FinanceApplication', operatingSystem: 'Any',
    browserRequirements: 'Requires JavaScript',
    description: desc || `${name} by Winfred Quek, CEA R073319H.`,
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'SGD' },
    isAccessibleForFree: true,
    publisher: { '@type': 'Organization', name: 'Crestbrick Pte Ltd', identifier: 'L31010886H' },
    creator: { '@type': 'Person', name: 'Winfred Quek', identifier: 'CEA R073319H', url: 'https://winfredquek.com/about' },
  };
  const block = `  ${MARK}\n  <script type="application/ld+json">\n${JSON.stringify(breadcrumb)}\n</script>\n  <script type="application/ld+json">\n${JSON.stringify(app)}\n</script>\n`;
  h = h.replace('</head>', `${block}</head>`);
  if (!DRY) writeFileSync(path, h);
  added++;
}
console.log(`${DRY ? '[dry] ' : ''}tool schema: added to ${added}, skipped ${skipped} (already had schema)`);
