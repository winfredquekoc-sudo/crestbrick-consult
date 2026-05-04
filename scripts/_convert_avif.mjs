#!/usr/bin/env node
import sharp from 'sharp';
import fs from 'fs';
import path from 'path';

const ROOT = '/Users/winfredquek/crestbrick-consult/public';

function walk(dir, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p, acc);
    else acc.push(p);
  }
  return acc;
}

const exts = new Set(['.jpg', '.jpeg', '.png']);
const files = walk(ROOT).filter(f => {
  const ext = path.extname(f).toLowerCase();
  if (!exts.has(ext)) return false;
  const base = path.basename(f).toLowerCase();
  if (base.startsWith('favicon')) return false;
  const stat = fs.statSync(f);
  if (stat.size < 10 * 1024) return false; // skip tiny icons
  return true;
});

const results = [];
for (const f of files) {
  const stat = fs.statSync(f);
  const dir = path.dirname(f);
  const base = path.basename(f, path.extname(f));
  const avifPath = path.join(dir, base + '.avif');
  const webpPath = path.join(dir, base + '.webp');
  try {
    await sharp(f).avif({ quality: 50 }).toFile(avifPath);
    await sharp(f).webp({ quality: 80 }).toFile(webpPath);
    const avifSize = fs.statSync(avifPath).size;
    const webpSize = fs.statSync(webpPath).size;
    results.push({ file: f, orig: stat.size, avif: avifSize, webp: webpSize, ok: true });
    console.log(`OK ${path.relative(ROOT, f)} ${(stat.size/1024).toFixed(1)}KB -> avif ${(avifSize/1024).toFixed(1)}KB / webp ${(webpSize/1024).toFixed(1)}KB`);
  } catch (e) {
    results.push({ file: f, orig: stat.size, ok: false, err: String(e) });
    console.log(`FAIL ${path.relative(ROOT, f)} ${e.message}`);
  }
}

const totalOrig = results.filter(r => r.ok).reduce((s, r) => s + r.orig, 0);
const totalAvif = results.filter(r => r.ok).reduce((s, r) => s + r.avif, 0);
const totalWebp = results.filter(r => r.ok).reduce((s, r) => s + r.webp, 0);
console.log(`\nTOTAL: orig=${(totalOrig/1024).toFixed(0)}KB avif=${(totalAvif/1024).toFixed(0)}KB webp=${(totalWebp/1024).toFixed(0)}KB`);
console.log(`AVIF reduction: ${((1 - totalAvif/totalOrig) * 100).toFixed(1)}%`);

fs.writeFileSync('/Users/winfredquek/crestbrick-consult/scripts/_avif-results.json', JSON.stringify(results, null, 2));
