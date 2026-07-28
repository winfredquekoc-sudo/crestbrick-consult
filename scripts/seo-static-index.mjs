#!/usr/bin/env node
// seo-static-index.mjs — inject static, crawlable link sections into JS-driven / under-linked
// hub pages so every child page has a real <a href> reachable WITHOUT JavaScript. Fixes
// orphan pages for AI crawlers (GPTBot/ClaudeBot/PerplexityBot don't run JS) and strengthens
// internal PageRank. Idempotent: re-running replaces the marked block. --dry-run to preview.

import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const DRY = process.argv.includes('--dry-run');
const PUB = 'public';
const S = '<!-- static-index:START (auto, seo-static-index.mjs) -->';
const E = '<!-- static-index:END -->';

const cleanTitle = t => t
  .replace(/\s*[|]\s*Winfred Quek.*$/i, '')
  .replace(/\s*[,]\s*Winfred Quek.*$/i, '')
  .replace(/\s*[|].*$/,'')
  .replace(/&amp;/g, '&').trim();
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function childLinks(dir, urlPrefix) {
  const out = [];
  for (const name of readdirSync(join(PUB, dir)).sort()) {
    if (!name.endsWith('.html') || name === 'index.html') continue;
    const slug = name.slice(0, -5);
    const h = readFileSync(join(PUB, dir, name), 'utf8');
    const m = h.match(/<title>(.*?)<\/title>/is);
    // skip noindex pages
    if (/<meta[^>]+name=["']robots["'][^>]+noindex/i.test(h)) continue;
    const title = m ? cleanTitle(m[1]) : slug;
    out.push(`      <a href="${urlPrefix}/${slug}" class="text-[var(--ink-soft)] hover:text-[var(--accent)]" style="text-decoration:none;">${esc(title)}</a>`);
  }
  return out;
}

function section(label, links) {
  return `<section class="max-w-6xl mx-auto px-6 py-12 border-t border-[var(--rule)]">
    <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">${label} (${links.length})</p>
    <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-2 text-sm" style="line-height:1.5;">
${links.join('\n')}
    </div>
  </section>`;
}

const HUBS = [
  { file: 'insights.html', blocks: [['All insight guides', 'insights', '/insights'], ['From the blog', 'blog', '/blog']] },
  { file: 'new-launches.html', blocks: [['All new launch project briefs', 'launches/briefs', '/launches/briefs']] },
  { file: 'districts.html', blocks: [['All Singapore district guides', 'districts', '/districts'], ['All HDB town guides', 'hdb-towns', '/hdb-towns']] },
  { file: 'services.html', blocks: [['Advisory services', 'services', '/services'], ['Specialist niches', 'niches', '/niches'], ['Cross border buyer guides', 'cross-border', '/cross-border']] },
  { file: 'tools/index.html', blocks: [['All calculators and tools', 'tools', '/tools']] },
];

for (const hub of HUBS) {
  const path = join(PUB, hub.file);
  let h = readFileSync(path, 'utf8');
  const sections = hub.blocks.map(([label, dir, url]) => section(label, childLinks(dir, url))).join('\n');
  const block = `${S}\n${sections}\n${E}`;
  const re = new RegExp(`${S.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[\\s\\S]*?${E}`);
  if (re.test(h)) {
    h = h.replace(re, block);
  } else {
    h = h.replace('</main>', `${block}\n</main>`);
  }
  const n = hub.blocks.reduce((a, [l, d]) => a + childLinks(d).length, 0);
  console.log(`${DRY ? '[dry] ' : ''}${hub.file}: injected ${n} static links across ${hub.blocks.length} section(s)`);
  if (!DRY) writeFileSync(path, h);
}
