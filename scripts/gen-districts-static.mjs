#!/usr/bin/env node
// gen-districts-static.mjs — pre-render the district cards + HDB town cards on
// public/districts.html into real, crawlable HTML at build time. The page used to render
// this content entirely client-side via fetch('/districts.json') / fetch('/hdb-towns.json'),
// so the served HTML was just a "Loading…" placeholder — invisible to Googlebot's first pass,
// GPTBot, ClaudeBot, and PerplexityBot (none execute JS). This bakes the district names,
// regions, postal sectors, and Winfred's POV text straight into the markup, marker-delimited
// so it regenerates every build and never drifts from districts.json/hdb-towns.json.
// The existing client-side script.js is left in place as progressive enhancement (region
// filter chips, postal lookup) — it re-fetches and re-renders on load, but the raw HTML
// response now already contains the real content. Idempotent: re-running replaces the block.

import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const PUB = 'public';
const HTML_PATH = join(PUB, 'districts.html');

const DS = '<!-- districts-static:START (auto, gen-districts-static.mjs) -->';
const DE = '<!-- districts-static:END -->';
const TS = '<!-- towns-static:START (auto, gen-districts-static.mjs) -->';
const TE = '<!-- towns-static:END -->';

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const reEscape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const districtsData = JSON.parse(readFileSync(join(PUB, 'districts.json'), 'utf8'));
const townsData = JSON.parse(readFileSync(join(PUB, 'hdb-towns.json'), 'utf8'));

function districtSlug(d) {
  const clean = d.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return `${d.code.toLowerCase()}-${clean}`;
}

function sectorsForDistrict(code) {
  return Object.entries(districtsData.postal_sectors)
    .filter(([, c]) => c === code)
    .map(([s]) => s);
}

function districtCard(d) {
  const waAsk = encodeURIComponent(`Hi Winfred, I want your view on ${d.code} (${d.name}).`);
  return `    <details class="card-lift district-card bg-[#1c1e26] border border-[var(--rule)] rounded-xl p-5 md:p-6">
      <summary class="flex justify-between items-center gap-4 cursor-pointer">
        <div class="flex items-center gap-3 flex-wrap">
          <span class="serif text-xl font-semibold text-[var(--accent)]">${esc(d.code)}</span>
          <span class="region-pill ${esc(d.region)}">${esc(d.region)}</span>
          <h3 class="serif text-lg md:text-xl font-semibold">${esc(d.name)}</h3>
        </div>
        <span class="chev text-2xl text-[var(--accent)] flex-shrink-0">+</span>
      </summary>
      <div class="mt-4 pt-4 border-t border-[var(--rule)] grid md:grid-cols-2 gap-6">
        <div>
          <p class="text-xs uppercase tracking-widest text-[var(--ink-muted)] mb-1">Areas</p>
          <p class="text-[var(--ink-soft)] text-sm mb-4">${esc(d.areas)}</p>
          <p class="text-xs uppercase tracking-widest text-[var(--ink-muted)] mb-1">Postal sectors</p>
          <p class="text-[var(--ink-soft)] text-sm font-mono">${esc(sectorsForDistrict(d.code).join(' · '))}</p>
        </div>
        <div>
          <p class="text-xs uppercase tracking-widest text-[var(--accent)] mb-2">Winfred's POV</p>
          <p class="text-[var(--ink-soft)] text-sm leading-relaxed mb-4">${esc(d.pov)}</p>
          <div class="flex flex-wrap gap-2">
            <a href="/districts/${districtSlug(d)}" class="btn btn-primary text-xs" style="padding:.4rem .8rem;">Read full ${esc(d.code)} guide </a>
            <a href="https://wa.me/6581618149?text=${waAsk}" class="btn btn-ghost text-xs" style="padding:.4rem .8rem;">Ask about ${esc(d.code)}</a>
          </div>
        </div>
      </div>
    </details>`;
}

const districtCards = districtsData.districts.map(districtCard).join('\n');
const districtsBlock = `${DS}\n${districtCards}\n${DE}`;

function townCard(t) {
  const mrt = t.mrt.replace(/\([^)]*\)/g, '').trim();
  return `          <a href="/hdb-towns/${t.slug}" class="card-lift block p-5 bg-[#1c1e26] border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] hover:-translate-y-[2px] transition">
            <div class="flex items-center gap-2 mb-2">
              <span class="text-[10px] uppercase tracking-widest font-semibold" style="color:${t.maturity === 'mature' ? 'var(--accent)' : 'var(--ink-muted)'};">${esc(t.maturity)}</span>
            </div>
            <h3 class="serif text-lg font-semibold mb-1">${esc(t.name)}</h3>
            <p class="text-[var(--ink-muted)] text-xs leading-relaxed">${esc(mrt)}</p>
          </a>`;
}

const REGION_ORDER = ['Central', 'East', 'North-East', 'North', 'West'];
const byRegion = {};
for (const t of townsData.towns) (byRegion[t.region] = byRegion[t.region] || []).push(t);

const townsBlock = REGION_ORDER.filter(r => byRegion[r]).map(region => `      <div class="mb-10">
        <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-3">${esc(region)}</p>
        <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
${byRegion[region].map(townCard).join('\n')}
        </div>
      </div>`).join('\n');

const townsFullBlock = `${TS}\n${townsBlock}\n${TE}`;

let html = readFileSync(HTML_PATH, 'utf8');

function replaceOrInsert(html, startMarker, endMarker, block, fallbackNeedle) {
  const re = new RegExp(`${reEscape(startMarker)}[\\s\\S]*?${reEscape(endMarker)}`);
  if (re.test(html)) return html.replace(re, block);
  if (!html.includes(fallbackNeedle)) {
    throw new Error(`gen-districts-static.mjs: could not find injection point (expected "${fallbackNeedle.slice(0, 60)}...")`);
  }
  return html.replace(fallbackNeedle, block);
}

html = replaceOrInsert(
  html, DS, DE, districtsBlock,
  '<p class="text-center text-[var(--ink-muted)] py-12">Loading districts, </p>'
);
html = replaceOrInsert(
  html, TS, TE, townsFullBlock,
  '<p class="text-center text-[var(--ink-muted)] py-12">Loading towns, </p>'
);

writeFileSync(HTML_PATH, html);
console.log(`districts.html: pre-rendered ${districtsData.districts.length} district cards + ${townsData.towns.length} HDB town cards (static)`);
