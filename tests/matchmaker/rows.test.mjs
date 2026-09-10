// Row pure-helper tests for scripts/matchmaker/app.js — the status pill
// label (item 7) and the line 2 facts builder (item 8) behind the worklist
// row's new three line hierarchy.
// Run: node --test tests/matchmaker/rows.test.mjs   (build.py runs it too)
//
// Same anchored-slice technique as filters.test.mjs/state.test.mjs: app.js is
// a browser script that touches document/DATA at load, so it cannot be
// require()d here directly — the relevant function bodies are lifted out by
// source anchors and evaluated with Scoring injected.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
require("../../scripts/matchmaker/scoring.js");
const Scoring = globalThis.Scoring;

const APP_SRC = readFileSync(new URL("../../scripts/matchmaker/app.js", import.meta.url), "utf8");
const APP_LINES = APP_SRC.split("\n");

function slice(startAnchor, endAnchor) {
  const a = APP_LINES.findIndex(l => l.includes(startAnchor));
  assert.notEqual(a, -1, "anchor not found in app.js: " + startAnchor);
  const b = APP_LINES.findIndex((l, i) => i > a && l.includes(endAnchor));
  assert.notEqual(b, -1, "end anchor not found in app.js: " + endAnchor);
  return APP_LINES.slice(a, b).join("\n");
}

// rentTxt (used by rowFacts when showListing) through the end of the pill/
// facts section, right before the draft text block begins.
const ROW_SRC = slice("function rentTxt(x)", "// ===================== draft text");

function makeRowHelpers() {
  return new Function(
    "Scoring",
    ROW_SRC + "\nreturn { markPillLabel, rowFacts };"
  )(Scoring);
}
const R = makeRowHelpers();

// =====================================================================
// markPillLabel — the row's status pill (item 7)
// =====================================================================

test("markPillLabel: no mark at all renders nothing", () => {
  assert.equal(R.markPillLabel(null, Date.now()), "");
  assert.equal(R.markPillLabel({}, Date.now()), "");
});

test("markPillLabel: Contacted shows the mark plus a real 'Xd ago'", () => {
  const now = Date.parse("2026-09-11T12:00:00+08:00");
  const ts = now - 2 * 86400000; // 2 real days earlier
  assert.equal(R.markPillLabel({ v: "Contacted", ts }, now), "Contacted · 2d ago");
});

test("markPillLabel: same instant reads 0d ago, not blank or negative", () => {
  const now = Date.parse("2026-09-11T12:00:00+08:00");
  assert.equal(R.markPillLabel({ v: "Queued", ts: now }, now), "Queued · 0d ago");
});

test("markPillLabel: a legacy v1 mark with no ts (0) falls back to the bare label", () => {
  assert.equal(R.markPillLabel({ v: "Not interested", ts: 0 }, Date.now()), "Not interested");
});

test("markPillLabel: Viewing booked with a stored date shows the date, not a day count", () => {
  const now = Date.parse("2026-09-11T12:00:00+08:00");
  const label = R.markPillLabel({ v: "Viewing booked", viewing_date: "2026-09-13", ts: now - 86400000 }, now);
  assert.equal(label, "Viewing 13 Sep");
});

test("markPillLabel: Viewing booked with an unparseable date falls back to the day-count form", () => {
  const now = Date.parse("2026-09-11T12:00:00+08:00");
  const ts = now - 86400000;
  assert.equal(R.markPillLabel({ v: "Viewing booked", viewing_date: "not a date", ts }, now), "Viewing booked · 1d ago");
});

// =====================================================================
// rowFacts — line 2's plain muted text (item 8): budget, move in, area
// =====================================================================

test("rowFacts: worklist row (no showListing) carries budget, move in and the tenant's own area", () => {
  const t = { budget: 1200, move_in: "2026-10-01", district: "D15", preferred_location: "near Marine Parade MRT, quiet unit" };
  assert.deepEqual(R.rowFacts(t, null, false), [
    "budget 1200",
    "move 2026-10-01",
    "D15 · near Marine Parade MRT, quie",
  ]);
});

test("rowFacts: missing budget/move in/district renders the same '?' placeholders as before", () => {
  const t = {};
  assert.deepEqual(R.rowFacts(t, null, false), ["budget ?", "move ?", "?"]);
});

test("rowFacts: showListing rows append the target listing's district and rent", () => {
  const t = { budget: 900, move_in: "2026-09-20", district: "D19" };
  const l = { district: "D19", rent_min: 850, rent_max: 950 };
  assert.deepEqual(R.rowFacts(t, l, true), ["budget 900", "move 2026-09-20", "D19", "D19 · $850 to $950"]);
});

test("rowFacts: preferred_location longer than 28 chars is truncated the same way the old chip was", () => {
  const t = { district: "D9", preferred_location: "somewhere very very very far away from everything" };
  const facts = R.rowFacts(t, null, false);
  assert.equal(facts[2], "D9 · " + "somewhere very very very far".slice(0, 28));
});
