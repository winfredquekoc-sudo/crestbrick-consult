#!/usr/bin/env node
// seo-glossary-schema.mjs — the /glossary terms are rendered by JS (invisible to non-JS AI
// crawlers). Extract the `const G = [...]` data, emit DefinedTermSet JSON-LD + a <noscript>
// static definition list so the definitions become citable by AI engines and search.
// Idempotent. Run cwd=worktree. --dry-run to preview.

import { readFileSync, writeFileSync } from 'node:fs';

const DRY = process.argv.includes('--dry-run');
const F = 'public/glossary.html';
const MARK = '<!-- glossary-geo (auto) -->';
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

let h = readFileSync(F, 'utf8');
if (h.includes(MARK)) { console.log('glossary-schema: already applied'); process.exit(0); }

const m = h.match(/const\s+G\s*=\s*(\[[\s\S]*?\]);/);
if (!m) { console.error('! could not find glossary data array'); process.exit(1); }
let terms;
try { terms = Function(`"use strict";return (${m[1]})`)(); }
catch (e) { console.error('! could not evaluate glossary data: ' + e.message); process.exit(1); }
terms = terms.filter(t => t && t.t && t.def);
if (terms.length < 5) { console.error('! too few terms parsed'); process.exit(1); }

// DefinedTermSet schema
const ld = {
  '@context': 'https://schema.org', '@type': 'DefinedTermSet',
  name: 'Singapore Property Glossary', url: 'https://winfredquek.com/glossary',
  hasDefinedTerm: terms.map(t => ({ '@type': 'DefinedTerm', name: (t.full ? `${t.t} (${t.full})` : t.t), description: String(t.def).replace(/\s+/g, ' ').trim(), inDefinedTermSet: 'https://winfredquek.com/glossary' })),
};
const block = `  ${MARK}\n  <script type="application/ld+json">\n${JSON.stringify(ld)}\n</script>\n`;
h = h.replace(/<\/head>/i, block + '</head>');

// <noscript> static definition list (readable by non-JS crawlers)
const dl = `<noscript>${MARK}\n<section style="max-width:48rem;margin:2rem auto;padding:0 1.5rem;">\n  <h2>Singapore property glossary</h2>\n  <dl>\n` +
  terms.map(t => `    <dt><strong>${esc(t.full ? `${t.t} (${t.full})` : t.t)}</strong></dt>\n    <dd>${esc(String(t.def).replace(/\s+/g, ' ').trim())}</dd>`).join('\n') +
  `\n  </dl>\n</section>\n</noscript>\n`;
if (/<\/main>/i.test(h)) h = h.replace(/<\/main>/i, dl + '</main>');
else h = h.replace(/<\/body>/i, dl + '</body>');

if (!DRY) writeFileSync(F, h);
console.log(`${DRY ? '[dry] ' : ''}glossary-schema: ${terms.length} terms -> DefinedTermSet + noscript fallback`);
