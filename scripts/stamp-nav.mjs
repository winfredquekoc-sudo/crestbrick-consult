#!/usr/bin/env node
// stamp-nav.mjs — single source of truth for the site nav.
// Reads _templates/nav.html and replaces every <header class="topnav">...</header>
// in public/**/*.html with that canonical block. Idempotent. Runs at build time.
//
// Usage: node scripts/stamp-nav.mjs [--dry-run]

import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('../', import.meta.url).pathname;
const PUBLIC = join(ROOT, 'public');
const TEMPLATE = join(ROOT, '_templates/nav.html');
const DRY = process.argv.includes('--dry-run');

const nav = readFileSync(TEMPLATE, 'utf8').trimEnd();

// Match <header class="topnav...">...</header> non-greedy across newlines.
// Leading [ \t]* on the header line is consumed so re-runs don't accumulate
// indentation (the template already carries its own 2-space indent) — keeps the
// stamp idempotent.
const HEADER_RE = /[ \t]*<header class="topnav[^"]*"[^>]*>[\s\S]*?<\/header>/i;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (name.startsWith('.') || name === 'node_modules' || name === '_templates') continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) yield* walk(p);
    else if (name.endsWith('.html')) yield p;
  }
}

let scanned = 0, changed = 0, noheader = 0;

for (const file of walk(PUBLIC)) {
  scanned++;
  const before = readFileSync(file, 'utf8');
  if (!HEADER_RE.test(before)) { noheader++; continue; }
  const after = before.replace(HEADER_RE, nav);
  if (after === before) continue;
  changed++;
  if (!DRY) writeFileSync(file, after);
}

console.log(`stamp-nav: scanned ${scanned} HTML files, ${changed} updated, ${noheader} had no <header class="topnav"> (skipped)`);
if (DRY) console.log('DRY RUN — no files written.');
