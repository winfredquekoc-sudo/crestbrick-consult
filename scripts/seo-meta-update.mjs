#!/usr/bin/env node
// seo-meta-update.mjs — replace meta description (and og:description / twitter:description to
// match) on pages from generated content JSON: { "<relpath>": "new description", ... } merged
// from one or more .json files in --content. Validates length 110-165. Run cwd=worktree.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).map(a => { const m = a.match(/^--([^=]+)(?:=(.*))?$/); return m ? [m[1], m[2] ?? true] : [a, true]; }));
const DRY = !!args['dry-run'];
const CONTENT = args.content;
const attr = s => s.replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

let content = {};
for (const f of readdirSync(CONTENT)) if (f.endsWith('.json')) Object.assign(content, JSON.parse(readFileSync(join(CONTENT, f), 'utf8')));

let done = 0, skip = 0, warn = 0;
for (const [rel, descRaw] of Object.entries(content)) {
  const desc = String(descRaw).replace(/\s+/g, ' ').trim();
  const path = join('public', rel);
  if (!existsSync(path)) { console.error(`  ! missing ${rel}`); skip++; continue; }
  if (desc.length < 110 || desc.length > 165) { console.error(`  ~ ${rel}: len ${desc.length} (want 110-165)`); warn++; }
  let h = readFileSync(path, 'utf8');
  const a = attr(desc);
  let n = 0;
  // match the full quoted value via a quote backreference (apostrophes inside the value are safe)
  h = h.replace(/(<meta\s+name=["']description["']\s+content=)(["'])[\s\S]*?\2/i, (_, p) => { n++; return `${p}"${a}"`; });
  h = h.replace(/<meta\s+content=(["'])[\s\S]*?\1\s+name=["']description["']/i, () => { n++; return `<meta name="description" content="${a}"`; });
  // keep og/twitter description consistent if present
  h = h.replace(/(<meta\s+property=["']og:description["']\s+content=)(["'])[\s\S]*?\2/i, (_, p) => `${p}"${a}"`);
  h = h.replace(/(<meta\s+name=["']twitter:description["']\s+content=)(["'])[\s\S]*?\2/i, (_, p) => `${p}"${a}"`);
  if (n === 0) { console.error(`  ! no description tag in ${rel}`); skip++; continue; }
  if (!DRY) writeFileSync(path, h);
  done++;
}
console.log(`${DRY ? '[dry] ' : ''}meta-update: updated ${done}, skipped ${skip}, length-warnings ${warn}`);
