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
