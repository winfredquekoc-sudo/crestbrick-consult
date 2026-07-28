#!/usr/bin/env node
// seo-faq-inject.mjs — inject a GEO answer layer (quick-answer speakable block + visible FAQ
// section + FAQPage/speakable JSON-LD) into existing pages from generated content JSON.
// Content JSON: { "<relpath-from-public>": { "quickAnswer": "...", "faqs": [{"q","a"}, ...] } }
// (one or more .json files in --content dir, merged). Idempotent (skips pages already done).
// Run with cwd = the repo/worktree whose public/ is edited. --dry-run to preview.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).map(a => { const m = a.match(/^--([^=]+)(?:=(.*))?$/); return m ? [m[1], m[2] ?? true] : [a, true]; }));
const DRY = !!args['dry-run'];
const CONTENT = args.content || 'content/seo-cycles/faq';
const MARK = '<!-- geo-faq (auto) -->';
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const jesc = s => String(s).replace(/\s+/g, ' ').trim();

// merge all content JSON files
let content = {};
for (const f of readdirSync(CONTENT)) {
  if (f.endsWith('.json')) {
    try { Object.assign(content, JSON.parse(readFileSync(join(CONTENT, f), 'utf8'))); }
    catch (e) { console.error(`  ! bad JSON ${f}: ${e.message}`); }
  }
}

let done = 0, skip = 0, fail = 0;
for (const [rel, c] of Object.entries(content)) {
  const path = join('public', rel);
  if (!existsSync(path)) { console.error(`  ! missing ${rel}`); fail++; continue; }
  let h = readFileSync(path, 'utf8');
  if (h.includes(MARK) || h.includes('FAQPage')) { skip++; continue; }
  if (!c.quickAnswer || !Array.isArray(c.faqs) || c.faqs.length < 3) { console.error(`  ! thin content for ${rel}`); fail++; continue; }
  const url = (h.match(/rel=["']canonical["']\s+href=["'](.*?)["']/i) || [])[1] || `https://winfredquek.com/${rel.replace(/\.html$/, '')}`;

  // 1) JSON-LD: FAQPage + speakable WebPage
  const faqLd = { '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: c.faqs.map(f => ({ '@type': 'Question', name: jesc(f.q), acceptedAnswer: { '@type': 'Answer', text: jesc(f.a) } })) };
  const speak = { '@context': 'https://schema.org', '@type': 'WebPage', url, speakable: { '@type': 'SpeakableSpecification', cssSelector: ['.geo-quick-answer', '.geo-faq-q'] } };
  const ld = `  ${MARK}\n  <script type="application/ld+json">\n${JSON.stringify(faqLd)}\n</script>\n  <script type="application/ld+json">\n${JSON.stringify(speak)}\n</script>\n`;
  if (!/<\/head>/i.test(h)) { console.error(`  ! no </head> ${rel}`); fail++; continue; }
  h = h.replace(/<\/head>/i, ld + '</head>');

  // 2) quick-answer block after first </h1>
  const qa = `\n<div class="geo-quick-answer" style="max-width:48rem;margin:1.25rem auto;padding:1rem 1.25rem;background:#1a1c24;border:1px solid #252830;border-left:3px solid #c6a36a;border-radius:8px;color:#f0ede6;font-size:1.02rem;line-height:1.7;"><strong style="color:#c6a36a;">Quick answer:</strong> ${esc(c.quickAnswer)}</div>\n`;
  if (/<\/h1>/i.test(h)) h = h.replace(/<\/h1>/i, m => m + qa);

  // 3) visible FAQ section before footer (or </main>, or </body>)
  const faqHtml = `\n<section class="geo-faq" style="max-width:48rem;margin:2.5rem auto;padding:0 1.5rem;">\n  <h2 style="font-family:'Fraunces',Georgia,serif;font-size:1.6rem;margin-bottom:1rem;">Frequently asked questions</h2>\n` +
    c.faqs.map(f => `  <div style="border-top:1px solid #252830;padding:1rem 0;">\n    <h3 class="geo-faq-q" style="font-size:1.08rem;font-weight:600;margin-bottom:.4rem;color:#f0ede6;">${esc(f.q)}</h3>\n    <p style="color:#b8b4aa;line-height:1.7;margin:0;">${esc(f.a)}</p>\n  </div>\n`).join('') +
    `</section>\n`;
  if (/<footer[\s>]/i.test(h)) h = h.replace(/<footer[\s>]/i, m => faqHtml + m);
  else if (/<\/main>/i.test(h)) h = h.replace(/<\/main>/i, faqHtml + '</main>');
  else h = h.replace(/<\/body>/i, faqHtml + '</body>');

  if (!DRY) writeFileSync(path, h);
  done++;
}
console.log(`${DRY ? '[dry] ' : ''}geo-faq: injected ${done}, skipped ${skip} (already done), failed ${fail}`);
