#!/usr/bin/env node
// seo-llms-augment.mjs — make llms.txt a complete GEO index by regenerating the Tools and
// Area-pages sections from the actual pages (titles + descriptions), preserving every other
// curated section. Idempotent (regenerates deterministically). Run cwd=worktree.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const DRY = process.argv.includes('--dry-run');
const F = 'public/llms.txt';
const BASE = 'https://winfredquek.com';
const titleOf = h => ((h.match(/<title>(.*?)<\/title>/is) || [, ''])[1]).replace(/\s*[|,]\s*Winfred Quek.*$/i, '').split(/\s*[|]\s*/)[0].replace(/&amp;/g, '&').trim();
const descOf = h => { const m = h.match(/name=["']description["']\s+content=(["'])(.*?)\1/is); return m ? m[2].replace(/\s+/g, ' ').trim() : ''; };
const short = (s, n = 120) => s.length > n ? s.slice(0, s.lastIndexOf(' ', n)) + '.' : s;

function listDir(dir, urlBase) {
  const out = [];
  for (const n of readdirSync(join('public', dir)).sort()) {
    if (!n.endsWith('.html') || n === 'index.html') continue;
    const h = readFileSync(join('public', dir, n), 'utf8');
    if (/name=["']robots["'][^>]*noindex/i.test(h) || /http-equiv=["']refresh["']/i.test(h)) continue;
    const t = titleOf(h), d = descOf(h);
    out.push(d ? `- [${t}](${urlBase}/${n.slice(0, -5)}): ${short(d)}` : `- [${t}](${urlBase}/${n.slice(0, -5)})`);
  }
  return out;
}

let txt = readFileSync(F, 'utf8');
function replaceSection(headingStartsWith, body) {
  const lines = txt.split('\n');
  const start = lines.findIndex(l => l.startsWith(headingStartsWith));
  if (start < 0) { console.error(`! section not found: ${headingStartsWith}`); return; }
  let end = start + 1;
  while (end < lines.length && !lines[end].startsWith('## ')) end++;
  const heading = lines[start];
  txt = [...lines.slice(0, start), heading, '', ...body, '', ...lines.slice(end)].join('\n');
}

const tools = listDir('tools', `${BASE}/tools`);
// also include the remaining root calculators (absd-calculator retired: redirects to /tools/absd)
for (const n of ['tdsr-calculator', 'stamp-duty-calculator']) {
  if (existsSync(`public/${n}.html`)) { const h = readFileSync(`public/${n}.html`, 'utf8'); tools.push(`- [${titleOf(h)}](${BASE}/${n}): ${short(descOf(h))}`); }
}
replaceSection('## Tools', tools);

if (existsSync('public/area')) {
  const area = listDir('area', `${BASE}/area`);
  replaceSection('## Area pages', area);
}

let answers = [];
if (existsSync('public/answers')) {
  answers = listDir('answers', `${BASE}/answers`);
  replaceSection('## Quick answers', answers);
}

if (!DRY) writeFileSync(F, txt);
console.log(`${DRY ? '[dry] ' : ''}llms-augment: Tools=${tools.length} entries, Area=${existsSync('public/area') ? listDir('area', '').length : 0} entries, Answers=${answers.length} entries`);
