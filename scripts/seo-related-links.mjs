#!/usr/bin/env node
// seo-related-links.mjs — strengthen topical internal linking. For each /insights/ article
// with fewer than 3 internal insight links, inject a "Related guides" section linking the
// most topically-similar articles (shared distinctive slug/title tokens). Idempotent; skips
// self + already-linked; only injects when it finds >=3 confident matches. Run cwd=worktree.

import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const DRY = process.argv.includes('--dry-run');
const DIR = 'public/insights';
const MARK = '<!-- related-guides (auto) -->';
const STOP = new Set(('a an the of for to in on and or vs your you i how what when why where which who do does is are be singapore singapore\'s property properties 2024 2025 2026 guide guides explained complete the best top new used buyer buyers seller sellers home homes condo private hdb-vs should can will from with into out about more less by it that this these those winfred quek crestbrick cea part full real estate').split(/\s+/));
const clean = t => t.replace(/\s*[|,]\s*Winfred Quek.*$/i, '').split(/\s*[|]\s*/)[0].replace(/&amp;/g, '&').trim();
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const pages = readdirSync(DIR).filter(n => n.endsWith('.html') && n !== 'index.html');
const idx = {};
for (const n of pages) {
  const slug = n.slice(0, -5);
  const h = readFileSync(join(DIR, n), 'utf8');
  const title = clean((h.match(/<title>(.*?)<\/title>/is) || [, slug])[1]);
  const toks = new Set([...slug.split('-'), ...title.toLowerCase().split(/[^a-z0-9]+/)]
    .filter(t => t.length > 2 && !STOP.has(t)));
  idx[slug] = { slug, title, toks, h, links: new Set((h.match(/href="\/insights\/([a-z0-9-]+)"/g) || []).map(x => x.match(/insights\/([a-z0-9-]+)/)[1])) };
}

let done = 0, skip = 0;
for (const p of Object.values(idx)) {
  const internal = [...p.links].filter(s => s !== p.slug).length;
  if (internal >= 3 || p.h.includes(MARK)) { skip++; continue; }
  // score others by shared distinctive tokens
  const scored = Object.values(idx)
    .filter(o => o.slug !== p.slug && !p.links.has(o.slug))
    .map(o => ({ o, score: [...p.toks].filter(t => o.toks.has(t)).length }))
    .filter(x => x.score >= 2)
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);
  if (scored.length < 3) { skip++; continue; }
  const links = scored.map(({ o }) =>
    `    <li style="font-size:.9rem;"><a href="/insights/${o.slug}" style="color:var(--accent,#c6a36a);text-decoration:none;">${esc(o.title)}</a></li>`).join('\n');
  const sec = `${MARK}\n<section style="max-width:48rem;margin:2.5rem auto 0;padding:1.5rem 1.5rem 0;border-top:1px solid var(--rule,#252830);">\n  <h2 style="font-size:1rem;font-weight:600;color:var(--ink,#f0ede6);margin-bottom:1rem;">Related guides</h2>\n  <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:.5rem;">\n${links}\n  </ul>\n</section>\n`;
  let h = p.h;
  if (/<footer[\s>]/i.test(h)) h = h.replace(/<footer[\s>]/i, m => sec + m);
  else if (/<\/main>/i.test(h)) h = h.replace(/<\/main>/i, sec + '</main>');
  else { skip++; continue; }
  if (!DRY) writeFileSync(join(DIR, p.slug + '.html'), h);
  done++;
}
console.log(`${DRY ? '[dry] ' : ''}related-links: injected ${done}, skipped ${skip}`);
