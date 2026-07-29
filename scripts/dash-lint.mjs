#!/usr/bin/env node
// dash-lint: flag em dashes, en dashes, and word-joining hyphens in human readable prose.
// Excludes URLs, code spans/blocks, file paths/slugs, and markdown structure
// (leading bullet dashes, --- rules, table borders).
// Exit 1 if any prose violation is found, so it can gate a skill's enforcement phase.

import { readFile } from 'node:fs/promises';

const EM_DASH = '—';
const EN_DASH = '–';

// A token is a file path/slug if it contains a slash or ends in a known extension.
const PATH_OR_SLUG = /\b[\w./-]+\.(?:md|markdown|json|jsonl|js|mjs|cjs|ts|tsx|jsx|csv|txt|sh|py|yml|yaml|html|css|png|jpg|jpeg|svg|pdf|log)\b|\b[\w-]+\/[\w./-]+/gi;
const URL = /https?:\/\/\S+|www\.\S+/gi;
const INLINE_CODE = /`[^`]*`/g;

// Word-joining hyphen: letters on both sides, e.g. "high-contrast".
const WORD_HYPHEN = /[A-Za-z]+-[A-Za-z]+/g;

function isHorizontalRule(line) {
  return /^\s*-{3,}\s*$/.test(line);
}

function isTableBorder(line) {
  // e.g. |---|---| or | --- | :--- | ---: |
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}

function stripLeadingBullet(line) {
  // Replace a leading "- " / "* " / "+ " list marker with equal-width spaces
  // so column numbers in the remaining text stay accurate.
  return line.replace(/^(\s*)([-*+])(\s)/, (_, a, _m, c) => a + ' ' + c);
}

function blankOutMatches(line, regex) {
  // Replace matched spans with spaces, preserving length/columns.
  return line.replace(regex, (m) => ' '.repeat(m.length));
}

function scanFile(text) {
  const lines = text.split(/\r?\n/);
  const violations = [];
  let inFence = false;

  lines.forEach((raw, idx) => {
    const lineNo = idx + 1;
    const fenceMatch = /^\s*(```|~~~)/.test(raw);
    if (fenceMatch) {
      inFence = !inFence;
      return;
    }
    if (inFence) return;
    if (isHorizontalRule(raw)) return;
    if (isTableBorder(raw)) return;

    // Strip excluded spans before scanning prose.
    let line = stripLeadingBullet(raw);
    line = blankOutMatches(line, INLINE_CODE);
    line = blankOutMatches(line, URL);
    line = blankOutMatches(line, PATH_OR_SLUG);

    for (const m of line.matchAll(WORD_HYPHEN)) {
      violations.push({ lineNo, col: m.index + 1, kind: 'hyphen', match: m[0] });
    }
    for (let i = 0; i < line.length; i++) {
      if (line[i] === EM_DASH) violations.push({ lineNo, col: i + 1, kind: 'em-dash', match: EM_DASH });
      else if (line[i] === EN_DASH) violations.push({ lineNo, col: i + 1, kind: 'en-dash', match: EN_DASH });
    }
  });

  return violations;
}

async function main() {
  const file = process.argv[2];
  if (!file) {
    console.error('Usage: node scripts/dash-lint.mjs <markdown-file>');
    process.exit(2);
  }

  let text;
  try {
    text = await readFile(file, 'utf8');
  } catch (err) {
    console.error(`dash-lint: cannot read ${file}: ${err.message}`);
    process.exit(2);
  }

  const violations = scanFile(text);

  if (violations.length === 0) {
    console.log(`PASS  ${file}  (no prose dashes or hyphens found)`);
    process.exit(0);
  }

  console.log(`FAIL  ${file}  (${violations.length} violation${violations.length === 1 ? '' : 's'})\n`);
  for (const v of violations) {
    console.log(`  ${file}:${v.lineNo}:${v.col}  ${v.kind.padEnd(7)}  ${JSON.stringify(v.match)}`);
  }
  const byKind = violations.reduce((acc, v) => ((acc[v.kind] = (acc[v.kind] || 0) + 1), acc), {});
  console.log('\nSummary: ' + Object.entries(byKind).map(([k, n]) => `${n} ${k}`).join(', '));
  process.exit(1);
}

main();
