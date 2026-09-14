// Filter facet normaliser tests for scripts/matchmaker/app.js — room type,
// gender preference, race preference and status, the four bucketing
// functions behind the new filter selects (#ft/#fg/#fe/#fs).
// Run: node --test tests/matchmaker/filters.test.mjs   (build.py runs it too)
//
// Same anchored-slice technique as state.test.mjs: app.js is a browser
// script that touches document/DATA at load, so it cannot be require()d
// here directly — the relevant function bodies are lifted out by source
// anchors and evaluated with TODAY/Scoring injected.

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

const FACET_SRC = slice('function lifecycleOf(l)', "function daysAgoLabel");

function makeFacets(today) {
  return new Function(
    "TODAY", "Scoring",
    FACET_SRC + "\nreturn { roomTypeOf, genderPrefOf, racePrefOf, statusOf, cookingOf };"
  )(today || new Date(2026, 8, 11), Scoring);
}
const F = makeFacets();

// =====================================================================
// roomTypeOf — property_type / rooms / req_raw free text
// =====================================================================

test("roomTypeOf: buckets every messy property_type value from the live data shape", () => {
  assert.equal(F.roomTypeOf({ property_type: "HDB (common room, 170 sqft)" }), "Common room");
  assert.equal(F.roomTypeOf({ property_type: "HDB (master room)" }), "Master room");
  assert.equal(F.roomTypeOf({ property_type: "Condo (whole unit for rent; 1,518 sqft)" }), "Whole unit");
  assert.equal(F.roomTypeOf({ property_type: "Standalone studio at landed house" }), "Studio");
  assert.equal(F.roomTypeOf({ property_type: "Condo coliving (Melville Park; 6 rooms ...)" }), "Co living");
  assert.equal(F.roomTypeOf({ property_type: "HDB" }), "Room");
  assert.equal(F.roomTypeOf({ property_type: "Condo" }), "Room");
});

test("roomTypeOf: falls back to rooms/req_raw text when property_type alone is generic", () => {
  assert.equal(F.roomTypeOf({ property_type: "HDB", rooms: "Master room: $900" }), "Master room");
  assert.equal(F.roomTypeOf({ property_type: "Condo", req_raw: { other: "common room facing the pool" } }), "Common room");
  assert.equal(F.roomTypeOf({}), "Room");
});

// =====================================================================
// genderPrefOf — reqs.gender free text
// =====================================================================

test("genderPrefOf: buckets every messy reqs.gender value from the live data shape", () => {
  assert.equal(F.genderPrefOf({ reqs: { gender: "Female only" } }), "Female only");
  assert.equal(F.genderPrefOf({ reqs: { gender: "Male only" } }), "Male only");
  assert.equal(F.genderPrefOf({ reqs: { gender: "Any gender" } }), "Any gender");
  assert.equal(F.genderPrefOf({ reqs: { gender: "Any (confirmed no re" } }), "Any gender");
  assert.equal(F.genderPrefOf({ reqs: { gender: "M/F" } }), "Any gender");
  assert.equal(F.genderPrefOf({ reqs: { gender: "Mixed gender co-livi" } }), "Any gender");
  assert.equal(F.genderPrefOf({ reqs: { gender: "Any race, gender or " } }), "Any gender");
  assert.equal(F.genderPrefOf({ reqs: { gender: "" } }), "Unstated");
  assert.equal(F.genderPrefOf({ reqs: {} }), "Unstated");
  assert.equal(F.genderPrefOf({}), "Unstated");
});

// =====================================================================
// racePrefOf — reqs.race free text (never "quota" — CEA copy rule)
// =====================================================================

test("racePrefOf: buckets every messy reqs.race value from the live data shape", () => {
  assert.equal(F.racePrefOf({ reqs: { race: "Any race" } }), "Any race");
  assert.equal(F.racePrefOf({ reqs: { race: "No Indian" } }), "No Indian");
  assert.equal(F.racePrefOf({ reqs: { race: "Prefers Indian" } }), "Prefers Indian");
  assert.equal(F.racePrefOf({ reqs: { race: "Prefers Chinese" } }), "Prefers Chinese");
  assert.equal(F.racePrefOf({ reqs: { race: "Prefers Indian/Chinese/Malay" } }), "Prefers Indian");
  assert.equal(F.racePrefOf({ reqs: { race: "Indian only" } }), "Indian only");
  assert.equal(F.racePrefOf({ reqs: { race: "No blanket exclusion evidenced; one male" } }), "Any race");
  assert.equal(F.racePrefOf({ reqs: { race: "" } }), "Unstated");
  assert.equal(F.racePrefOf({}), "Unstated");
});

test("racePrefOf: 'Prefers <group>' outside Indian/Chinese/Malay buckets as Prefers others", () => {
  assert.equal(F.racePrefOf({ reqs: { race: "Prefers Filipino" } }), "Prefers others");
});

// =====================================================================
// statusOf — lifecycle + available_from vs today
// =====================================================================

test("statusOf: available with no future available_from reads Available now", () => {
  const facets = makeFacets(new Date(2026, 8, 11));
  assert.equal(facets.statusOf({ lifecycle: "available" }), "Available now");
  assert.equal(facets.statusOf({}), "Available now");
  assert.equal(facets.statusOf({ lifecycle: "available", available_from: "2026-09-01" }), "Available now");
});

test("statusOf: available with a future available_from reads Available from date", () => {
  const facets = makeFacets(new Date(2026, 8, 11));
  assert.equal(facets.statusOf({ lifecycle: "available", available_from: "2026-09-20" }), "Available from date");
});

test("statusOf: a non available lifecycle reads back capitalised, underscores as spaces", () => {
  const facets = makeFacets(new Date(2026, 8, 11));
  assert.equal(facets.statusOf({ lifecycle: "tenanted" }), "Tenanted");
  assert.equal(facets.statusOf({ lifecycle: "renewal_watch" }), "Renewal watch");
  assert.equal(facets.statusOf({ lifecycle: "offer_pending" }), "Offer pending");
});

// =====================================================================
// cookingOf — verbatim field, empty is Unstated
// =====================================================================

test("cookingOf: passes the export's fixed cooking labels through verbatim, empty is Unstated", () => {
  assert.equal(F.cookingOf({ cooking: "Light only" }), "Light only");
  assert.equal(F.cookingOf({ cooking: "Ask landlord" }), "Ask landlord");
  assert.equal(F.cookingOf({ cooking: "Allowed" }), "Allowed");
  assert.equal(F.cookingOf({ cooking: "Not allowed" }), "Not allowed");
  assert.equal(F.cookingOf({ cooking: "" }), "Unstated");
  assert.equal(F.cookingOf({}), "Unstated");
});

// =====================================================================
// facetsOf — item 3's per-listing memoisation of the five facets above.
// Sliced separately since it lives well outside this file's protected
// anchor span (declared next to passFilter, not lifecycleOf..daysAgoLabel),
// and it is a tiny three-line function, so this pins it down with its own
// narrow start/end anchor rather than widening FACET_SRC.
// =====================================================================
const FACETS_OF_SRC = slice("function facetsOf(l) {", "// (runner up) f is hoisted out");

function makeFacetsOf(listingFacetsMap) {
  return new Function(
    "LISTING_FACETS", "roomTypeOf", "genderPrefOf", "racePrefOf", "statusOf", "cookingOf",
    FACETS_OF_SRC + "\nreturn facetsOf;"
  )(listingFacetsMap, F.roomTypeOf, F.genderPrefOf, F.racePrefOf, F.statusOf, F.cookingOf);
}

test("facetsOf: reads the precomputed entry when the listing is in the map", () => {
  const precomputed = { rt: "Master room", gp: "Any gender", rp: "Any race", st: "Available now", ck: "Allowed" };
  const facetsOf = makeFacetsOf(new Map([["L1", precomputed]]));
  assert.equal(facetsOf({ id: "L1" }), precomputed);
});

test("facetsOf: falls back to calling the live functions when the listing is missing from the map", () => {
  // rebuildMatches() always populates every listing it scores, so this path
  // is defensive only — but it must still match what the map would have held.
  const facetsOf = makeFacetsOf(new Map());
  const l = { id: "L2", property_type: "HDB (master room)", reqs: {}, lifecycle: "available", cooking: "Allowed" };
  assert.deepEqual(facetsOf(l), {
    rt: "Master room", gp: "Unstated", rp: "Unstated", st: "Available now", ck: "Allowed",
  });
});

// =====================================================================
// activeFilterChips / countMoreFilters — item 2's chip strip and Filters(n)
// badge data. Sliced separately (outside FACET_SRC's own span) since these
// depend on VERDICT_LABELS and $, injected the same way TODAY/Scoring are
// above rather than widening FACET_SRC into unrelated render-layer code.
// The chip `clear` closures touch real DOM ($("#q")...) — never invoked
// here, only their k/label are asserted, so a throwaway $ stub is enough.
// =====================================================================
const CHIP_SRC = slice("const MORE_FILTER_KEYS = [", "function updateChipsAndCount() {");

function makeChipHelpers(verdictLabels) {
  return new Function(
    "VERDICT_LABELS", "$",
    CHIP_SRC + "\nreturn { activeFilterChips, countMoreFilters, MORE_FILTER_KEYS };"
  )(verdictLabels, () => ({}));
}
const CH = makeChipHelpers({ QUALIFIED: "Qualified", NEEDS_INFO: "Needs info", BLOCKED: "Has conflict" });

test("countMoreFilters: counts only the 9 row-two keys, never q/d", () => {
  assert.equal(CH.countMoreFilters({ q: "marine", d: "D15" }), 0);
  assert.equal(CH.countMoreFilters({ v: "QUALIFIED", cold: true }), 2);
  assert.equal(CH.countMoreFilters({ v: "", r: 0, cold: false, hide: false, rt: "", gp: "", rp: "", st: "", ck: "" }), 0);
  assert.equal(CH.countMoreFilters({
    v: "QUALIFIED", r: 1500, cold: true, hide: true, rt: "Master room",
    gp: "Female only", rp: "Any race", st: "Available now", ck: "Allowed",
  }), 9);
});

test("activeFilterChips: q and d chip regardless of filtersActive (row one controls)", () => {
  const chips = CH.activeFilterChips({ q: "marine", d: "D15" }, false);
  assert.deepEqual(chips.map(c => c.k), ["q", "d"]);
  assert.equal(chips[0].label, '"marine"');
  assert.equal(chips[1].label, "D15");
});

test("activeFilterChips: the 9 row-two filters only chip when filtersActive is true", () => {
  const f = { rt: "Master room", v: "QUALIFIED" };
  assert.deepEqual(CH.activeFilterChips(f, false).map(c => c.k), []);
  assert.deepEqual(CH.activeFilterChips(f, true).map(c => c.k).sort(), ["rt", "v"]);
});

test("activeFilterChips: verdict label comes from VERDICT_LABELS, booleans get a fixed label, rent formats as <=$N", () => {
  const chips = CH.activeFilterChips({ v: "QUALIFIED", cold: true, hide: true, r: 1500 }, true);
  const byKey = Object.fromEntries(chips.map(c => [c.k, c.label]));
  assert.equal(byKey.v, "Qualified");
  assert.equal(byKey.cold, "hide cold >5d");
  assert.equal(byKey.hide, "hide actioned");
  assert.equal(byKey.r, "≤$1500");
});

test("activeFilterChips: no active filters returns an empty chip list", () => {
  assert.deepEqual(CH.activeFilterChips({}, true), []);
});
