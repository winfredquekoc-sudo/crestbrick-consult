// Parity + v2 behavior tests for scripts/matchmaker/scoring.js.
// Run: node --test tests/matchmaker/scoring.test.mjs
//
// Section 1 (PARITY) pins scoring.js against hand computed expected values
// for the ORIGINAL template.html score() — every fixture here is v1 shaped
// (no units[]/available_from/last_wa/work_anchor) so every v2 extension is
// a documented no-op. If any of these break, the port has drifted.
// Section 2 (V2) exercises the new-in-v2 behaviors and their boundaries.
//
// Fixtures use invented, obviously fake SG-plausible people only.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

// This repo's package.json sets "type":"module", so Node treats scoring.js
// (deliberately import/export free — it is inlined verbatim into a plain
// <script> tag for the browser build) as an ES module with no CJS `module`
// global. require() still executes the file for its side effects, which is
// enough: scoring.js sets globalThis.Scoring itself. See the comment above
// scoring.js's export block for the full reasoning.
const require = createRequire(import.meta.url);
require("../../scripts/matchmaker/scoring.js");
const Scoring = globalThis.Scoring;

const TODAY = new Date(2026, 7, 11); // 2026-08-11, local midnight

function baseGates(overrides) {
  return Object.assign({
    max_pax: null, lease_min: null, gender: "any",
    ethnicity: { rule: "any", races: [] },
    occupation: "", cooking: "", pets: "", smoking: ""
  }, overrides || {});
}
function listing(overrides) {
  return Object.assign({
    id: "LL1", name: "Test Listing", district: "D15",
    rent_min: 1200, rent_max: 1200, gates: baseGates()
  }, overrides || {});
}
function tenant(overrides) {
  return Object.assign({
    id: "TN1", name: "Tan Ah Test", phone: "90000001",
    preferred_districts: [], district: "", budget: null, budget_max: null,
    pax: 1, gender: "Female", ethnicity: "Chinese", lease_months: null,
    move_in: "", last_contact: ""
  }, overrides || {});
}

// =====================================================================
// SECTION 1 — PARITY (must match the legacy inline score() exactly)
// =====================================================================

test("parity: full qualified match scores 100 and has no flags", () => {
  const l = listing({ district: "D15", rent_min: 1200, rent_max: 1200 });
  const t = tenant({
    preferred_districts: ["D15"], district: "D15", budget: 1200, pax: 1,
    lease_months: 12, move_in: "2026-08-11", last_contact: "2026-08-10"
  });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.total, 100);
  assert.deepEqual(r.parts, { budget: 30, location: 25, lease: 15, movein: 15, fresh: 15 });
  assert.deepEqual(r.flags, []);
  assert.equal(r.verdict, "QUALIFIED");
  assert.equal(r.dc, 1);
});

test("parity: budget under 90% of rent_min hard blocks", () => {
  const l = listing({ district: "D9", rent_min: 2000, rent_max: 2000 });
  const t = tenant({
    preferred_districts: ["D9"], budget: 1000, pax: 1,
    lease_months: 12, move_in: "2026-08-11", last_contact: "2026-08-10"
  });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.total, 74);
  assert.deepEqual(r.parts, { budget: 4, location: 25, lease: 15, movein: 15, fresh: 15 });
  assert.deepEqual(r.flags, ["over landlord's min ($2000)"]);
  assert.equal(r.verdict, "BLOCKED");
  assert.equal(r.near_miss, false);
});

test("parity: budget below rent_min but >=90% is flagged, not blocked", () => {
  const l = listing({ district: "D10", rent_min: 1000, rent_max: 1000 });
  const t = tenant({
    preferred_districts: ["D10"], budget: 950, pax: 1,
    lease_months: 12, move_in: "2026-08-11", last_contact: "2026-08-10"
  });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.total, 74);
  assert.deepEqual(r.parts, { budget: 4, location: 25, lease: 15, movein: 15, fresh: 15 });
  assert.deepEqual(r.flags, ["over landlord's min ($1000)"]);
  assert.equal(r.verdict, "QUALIFIED"); // not hard -> soft flag does not block
});

test("parity: budget far above rent_max flags whole unit, stays qualified", () => {
  const l = listing({ district: "D11", rent_min: 800, rent_max: 800 });
  const t = tenant({
    preferred_districts: ["D11"], budget: 1400, pax: 1,
    lease_months: 12, move_in: "2026-08-11", last_contact: "2026-08-10"
  });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.total, 84);
  assert.deepEqual(r.parts, { budget: 14, location: 25, lease: 15, movein: 15, fresh: 15 });
  assert.deepEqual(r.flags, ["budget well above room (may want whole unit)"]);
  assert.equal(r.verdict, "QUALIFIED");
});

test("parity: unknown budget forces NEEDS_INFO regardless of other scores", () => {
  const l = listing({ district: "D12", rent_min: 900, rent_max: 900 });
  const t = tenant({
    preferred_districts: ["D12"], budget: null, budget_max: null, pax: 1,
    lease_months: 12, move_in: "2026-08-11", last_contact: "2026-08-10"
  });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.total, 85);
  assert.deepEqual(r.parts, { budget: 15, location: 25, lease: 15, movein: 15, fresh: 15 });
  assert.deepEqual(r.flags, ["budget unknown"]);
  assert.equal(r.verdict, "NEEDS_INFO");
  assert.deepEqual(r.needsInfoReasons, ["budget"]);
});

test("parity: known budget but weak location still forces NEEDS_INFO", () => {
  // D2 is not in ADJ[D25] and shares no MRT line with D25 in the curated map,
  // so location resolves to the flat 6 (mismatch) bucket exactly as legacy did.
  const l = listing({ district: "D25", rent_min: 800, rent_max: 800 });
  const t = tenant({ preferred_districts: ["D2"], district: "", budget: 900, pax: 1 });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.parts.location, 6);
  assert.equal(r.parts.budget, 30);
  assert.equal(r.verdict, "NEEDS_INFO");
  assert.deepEqual(r.needsInfoReasons, ["location"]);
});

test("parity: location score branches (preferred/same/adjacent/none/mismatch)", () => {
  assert.equal(Scoring.locationScore({ district: "D15" }, { preferred_districts: ["D15"], district: "" }), 25);
  assert.equal(Scoring.locationScore({ district: "D16" }, { preferred_districts: [], district: "D16" }), 20);
  assert.equal(Scoring.locationScore({ district: "D2" }, { preferred_districts: ["D1"], district: "" }), 15); // D1 in ADJ[D2]
  assert.equal(Scoring.locationScore({ district: "D14" }, { preferred_districts: [], district: "" }), 8);
  assert.equal(Scoring.locationScore({ district: "D25" }, { preferred_districts: ["D2"], district: "" }), 6);
});

test("parity: lease score branches", () => {
  const need12 = { gates: baseGates() };
  const need6 = { gates: baseGates({ lease_min: 6 }) };
  assert.deepEqual(Scoring.leaseScore(need12, { lease_months: null }), { sle: 8, flag: null });
  assert.deepEqual(Scoring.leaseScore(need12, { lease_months: 12 }), { sle: 15, flag: null });
  assert.deepEqual(Scoring.leaseScore(need6, { lease_months: 6 }), { sle: 11, flag: null });
  assert.deepEqual(
    Scoring.leaseScore(need6, { lease_months: 3 }),
    { sle: 5, flag: "lease 3mo vs 6mo wanted" }
  );
});

test("parity: move-in window boundaries against today (no available_from)", () => {
  const l = {};
  assert.equal(Scoring.moveInScore(l, { move_in: "" }, TODAY), 8);
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-08-11" }, TODAY), 15); // di=0
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-07-07" }, TODAY), 15); // di=35 boundary
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-07-06" }, TODAY), 10); // di=36
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-09-20" }, TODAY), 15); // di=-40 boundary
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-09-21" }, TODAY), 10); // di=-41
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-05-08" }, TODAY), 10); // di=95 boundary
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-05-07" }, TODAY), 6);  // di=96
});

test("parity: freshness score buckets", () => {
  assert.equal(Scoring.score.length >= 0, true); // sanity the module loaded
  const buckets = [[null, 2], [7, 15], [8, 10], [30, 10], [31, 5], [90, 5], [91, 2]];
  // freshnessScoreFromDays is internal; exercise it through coldDays+score instead
  for (const [dc, expected] of buckets) {
    const t = dc == null ? tenant({ last_contact: "" }) : tenant({ last_contact: daysAgoStr(dc) });
    const l = listing();
    const r = Scoring.score(l, t, TODAY);
    assert.equal(r.parts.fresh, expected, `dc=${dc}`);
  }
});

test("parity: hard gates — gender", () => {
  const lFemaleOnly = listing({ gates: baseGates({ gender: "female_only" }) });
  const lMaleOnly = listing({ gates: baseGates({ gender: "male_only" }) });
  assert.equal(Scoring.score(lFemaleOnly, tenant({ gender: "Male" }), TODAY).verdict, "BLOCKED");
  assert.equal(Scoring.score(lFemaleOnly, tenant({ gender: "Female" }), TODAY).verdict !== "BLOCKED", true);
  assert.equal(Scoring.score(lMaleOnly, tenant({ gender: "Female" }), TODAY).verdict, "BLOCKED");
  const blockedFlags = Scoring.score(lFemaleOnly, tenant({ gender: "Male" }), TODAY).flags;
  assert.ok(blockedFlags.includes("landlord: female only"));
});

test("parity: hard gates — ethnicity exclude/only", () => {
  const lExclude = listing({ gates: baseGates({ ethnicity: { rule: "exclude", races: ["indian"] } }) });
  const lOnly = listing({ gates: baseGates({ ethnicity: { rule: "only", races: ["chinese"] } }) });
  assert.equal(Scoring.score(lExclude, tenant({ ethnicity: "Indian" }), TODAY).verdict, "BLOCKED");
  assert.equal(Scoring.score(lExclude, tenant({ ethnicity: "Malay" }), TODAY).verdict !== "BLOCKED", true);
  assert.equal(Scoring.score(lOnly, tenant({ ethnicity: "Malay" }), TODAY).verdict, "BLOCKED");
  assert.equal(Scoring.score(lOnly, tenant({ ethnicity: "Chinese" }), TODAY).verdict !== "BLOCKED", true);
});

test("parity: hard gate — pax over max", () => {
  const l = listing({ gates: baseGates({ max_pax: 2 }) });
  assert.equal(Scoring.score(l, tenant({ pax: 3 }), TODAY).verdict, "BLOCKED");
  assert.equal(Scoring.score(l, tenant({ pax: 2 }), TODAY).verdict !== "BLOCKED", true);
  const flags = Scoring.score(l, tenant({ pax: 3 }), TODAY).flags;
  assert.ok(flags.includes("pax 3 > max 2"));
});

test("parity: v1 shaped payload never throws and scores sanely (null guard)", () => {
  // Deliberately missing every v2 field a data lane build might omit.
  const l = { id: "LL9", name: "Old Shape Listing", district: "D9", rent_min: 1000, rent_max: 1200,
    gates: { max_pax: null, lease_min: null, gender: "any", ethnicity: { rule: "any", races: [] } } };
  const t = { id: "TN9", name: "Ng Wei Test", preferred_districts: ["D9"], district: "", budget: 1100,
    pax: 2, gender: "Male", ethnicity: "", lease_months: 12, move_in: "2026-08-20", last_contact: "2026-08-09" };
  assert.doesNotThrow(() => Scoring.score(l, t, TODAY));
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.verdict, "QUALIFIED");
  assert.equal(r.unit.unit_type, null); // synthesized single unit, no unit_type known
});

function daysAgoStr(n) {
  // Build the LOCAL date string directly — toISOString() converts to UTC
  // first, which silently shifts the calendar date in any timezone ahead
  // of UTC (e.g. SGT, UTC+8) and would throw the boundary math off by a day.
  const d = new Date(TODAY);
  d.setDate(d.getDate() - n);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() + "-" + mm + "-" + dd;
}

// =====================================================================
// SECTION 2 — V2 EXTENSIONS
// =====================================================================

test("v2: bestUnit picks the better fitting unit over the naive top level rent", () => {
  const l = listing({
    district: "D9", rent_min: 1300, rent_max: 1300, // naive/top level would hard block
    units: [
      { unit_type: "common", rent_min: 900, rent_max: 900 },
      { unit_type: "master", rent_min: 1300, rent_max: 1300 }
    ]
  });
  const t = tenant({ preferred_districts: ["D9"], budget: 1000 });
  const bu = Scoring.bestUnit(l, t);
  assert.equal(bu.unit.unit_type, "common");
  assert.equal(bu.sb, 30);
  assert.equal(bu.hardBudget, false);
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.verdict, "QUALIFIED");
  assert.equal(r.parts.budget, 30);
});

test("v2: MRT shared line upgrades a flat mismatch to adjacent (15)", () => {
  // D26 (TEL) vs D9 (NSL, TEL) share TEL; D26 is not in ADJ[D9].
  const sl = Scoring.locationScore({ district: "D9" }, { preferred_districts: ["D26"], district: "" });
  assert.equal(sl, 15);
  assert.equal(Scoring.mrtSharesLine("D26", "D9"), true);
  assert.equal(Scoring.mrtSharesLine("D25", "D2"), false); // control: no shared line
});

test("v2: vacancy gap scoring replaces the move-in window when available_from is set", () => {
  const l = { available_from: "2026-09-01" };
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-09-10" }, TODAY), 15); // gap 9d
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-10-05" }, TODAY), 10); // gap 34d
  assert.equal(Scoring.moveInScore(l, { move_in: "2026-12-01" }, TODAY), 6);  // gap 91d
  assert.equal(Scoring.moveInScore(l, { move_in: "" }, TODAY), 8);            // unknown move_in -> default
});

test("v2: freshness uses the freshest of last_contact and last_wa.ts", () => {
  const t = tenant({ last_contact: "2026-07-22", last_wa: { ts: "2026-08-09T10:00:00+08:00", from_me: false, snippet: "still looking?" } });
  assert.equal(Scoring.coldDays(t, TODAY), 2); // last_wa (2d ago) beats last_contact (20d ago)
  const r = Scoring.score(listing(), t, TODAY);
  assert.equal(r.parts.fresh, 15);
});

test("v2: isCold true only when confirmed gap exceeds 5 days; unknown is not cold", () => {
  assert.equal(Scoring.isCold(tenant({ last_contact: "2026-08-06" }), TODAY), false); // dc=5
  assert.equal(Scoring.isCold(tenant({ last_contact: "2026-08-05" }), TODAY), true);  // dc=6
  assert.equal(Scoring.isCold(tenant({ last_contact: "" }), TODAY), false);           // unknown != cold
});

test("v2: urgencyMult buckets, including overdue move_in as most urgent", () => {
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-08-18" }), TODAY), 1.25); // 7d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-08-19" }), TODAY), 1.15); // 8d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-08-25" }), TODAY), 1.15); // 14d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-08-26" }), TODAY), 1.05); // 15d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-09-10" }), TODAY), 1.05); // 30d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-09-11" }), TODAY), 1.0);  // 31d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "2026-08-01" }), TODAY), 1.25); // overdue by 10d
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "" }), TODAY), 1.0);
  assert.equal(Scoring.urgencyMult(tenant({ move_in: "not a date" }), TODAY), 1.0);
});

test("v2: move_in_norm drives urgencyMult and moveInScore when the verbatim move_in text is unparseable", () => {
  // "Immediately" is exactly the kind of free text export_data.py's
  // enrich.norm_move_in() now resolves at build time (-> move_in_norm) but
  // this file's own parseDate() still cannot read directly.
  assert.equal(Scoring.parseDate("Immediately"), null); // sanity: the raw text really is unparseable
  const today0 = tenant({ move_in: "Immediately", move_in_norm: "2026-08-11" }); // 0 days out
  assert.equal(Scoring.urgencyMult(today0, TODAY), 1.25); // most urgent bucket
  assert.equal(Scoring.moveInScore({}, today0, TODAY), 15);

  const withoutNorm = tenant({ move_in: "Immediately" }); // no move_in_norm at all (v1 payload, or unparseable)
  assert.equal(Scoring.urgencyMult(withoutNorm, TODAY), 1.0); // falls back to today's existing default, unchanged
  assert.equal(Scoring.moveInScore({}, withoutNorm, TODAY), 8);

  // move_in_norm 14 days out -> the 1.15 bucket boundary, same math as a clean move_in would get
  const farNorm = tenant({ move_in: "mid Aug 2026", move_in_norm: "2026-08-25" });
  assert.equal(Scoring.urgencyMult(farNorm, TODAY), 1.15);

  // vacancy gap branch (l.available_from set) also prefers move_in_norm
  const l = { available_from: "2026-09-01" };
  const gapTenant = tenant({ move_in: "early Sep 2026", move_in_norm: "2026-09-05" }); // 4d gap
  assert.equal(Scoring.moveInScore(l, gapTenant, TODAY), 15);
});

test("v2: near_miss fires only when budget clears 92% of the actual block threshold, and only when budget is the sole hard gate", () => {
  const l = listing({ district: "D9", rent_min: 1000, rent_max: 1000 });
  const closeMiss = Scoring.score(l, tenant({ preferred_districts: ["D9"], budget: 850 }), TODAY);
  assert.equal(closeMiss.verdict, "BLOCKED");
  assert.equal(closeMiss.near_miss, true);
  assert.equal(closeMiss.near_miss_gap, 150);

  const farMiss = Scoring.score(l, tenant({ preferred_districts: ["D9"], budget: 800 }), TODAY);
  assert.equal(farMiss.verdict, "BLOCKED");
  assert.equal(farMiss.near_miss, false);
  assert.equal(farMiss.near_miss_gap, null);

  const lPaxCapped = listing({ district: "D9", rent_min: 1000, rent_max: 1000, gates: baseGates({ max_pax: 1 }) });
  const blockedByBoth = Scoring.score(lPaxCapped, tenant({ preferred_districts: ["D9"], budget: 850, pax: 2 }), TODAY);
  assert.equal(blockedByBoth.verdict, "BLOCKED");
  assert.equal(blockedByBoth.near_miss, false); // not solely budget
});

test("v2: work_anchor bonus adds 3 when it shares an MRT line with the listing district, capped at 100", () => {
  const l = listing({ district: "D15", rent_min: 1000, rent_max: 1000 }); // D15 -> TEL
  const t = tenant({
    preferred_districts: ["D15"], budget: 1000, work_anchor: "D26" // D26 -> TEL, shares line
  });
  const r = Scoring.score(l, t, TODAY);
  assert.equal(r.total, 76); // 30+25+8+8+2 (no lease/move_in/last_contact given) + 3 bonus

  const capped = Scoring.score(
    listing({ district: "D15", rent_min: 1200, rent_max: 1200 }),
    tenant({
      preferred_districts: ["D15"], district: "D15", budget: 1200, pax: 1,
      lease_months: 12, move_in: "2026-08-11", last_contact: "2026-08-10", work_anchor: "D26"
    }),
    TODAY
  );
  assert.equal(capped.total, 100); // already maxed before bonus; bonus must not push past 100
});

test("v2: applyOverride forces verdict without mutating the input or changing scores", () => {
  const l = listing({ district: "D9", rent_min: 2000, rent_max: 2000 });
  const t = tenant({ preferred_districts: ["D9"], budget: 1000 });
  const blocked = Scoring.score(l, t, TODAY);
  const overridden = Scoring.applyOverride(blocked, { verdict: "QUALIFIED", why: "landlord relaxed after call" });
  assert.equal(overridden.verdict, "QUALIFIED");
  assert.equal(overridden.overridden, true);
  assert.equal(overridden.overrideWhy, "landlord relaxed after call");
  assert.equal(overridden.total, blocked.total);
  assert.deepEqual(overridden.parts, blocked.parts);
  assert.equal(blocked.verdict, "BLOCKED"); // original untouched
  assert.equal(blocked.overridden, false);

  const forcedBlock = Scoring.applyOverride(Scoring.score(listing(), tenant({ budget: 1200 }), TODAY), { verdict: "BLOCKED" });
  assert.equal(forcedBlock.verdict, "BLOCKED");

  const noop = Scoring.applyOverride(blocked, null);
  assert.equal(noop, blocked);
});

test("v2: robust date parser accepts YYYY-MM-DD, DD/MM/YYYY, ISO timestamps; rejects garbage as null (never NaN)", () => {
  assert.equal(Scoring.parseDate("2026-08-11").getFullYear(), 2026);
  const dmy = Scoring.parseDate("11/08/2026");
  assert.equal(dmy.getDate(), 11);
  assert.equal(dmy.getMonth(), 7); // August, 0 indexed
  assert.equal(Scoring.parseDate("2026-08-11T13:45:00+08:00") instanceof Date, true);
  assert.equal(Scoring.parseDate("2026-08-11T13:45:00Z") instanceof Date, true);
  assert.equal(Scoring.parseDate("not a date"), null);
  assert.equal(Scoring.parseDate(""), null);
  assert.equal(Scoring.parseDate(null), null);
  assert.equal(Scoring.parseDate(undefined), null);
  assert.equal(Scoring.parseDate("2026-13-01"), null);
  assert.equal(Scoring.parseDate("2026-02-30"), null);
  assert.equal(Scoring.parseDate("32/01/2026"), null);
  assert.equal(Scoring.parseDate("13/13/2026"), null);
  assert.equal(Scoring.parseDate(new Date("garbage")), null);
});

test("v2: daysAgo helper", () => {
  assert.equal(Scoring.daysAgo("2026-08-01", TODAY), 10);
  assert.equal(Scoring.daysAgo(null, TODAY), null);
  assert.equal(Scoring.daysAgo("2026-08-11", null), null);
});

test("v2: isSimilarListing matches same district + rent within 10%", () => {
  assert.equal(Scoring.isSimilarListing({ district: "D9", rent_min: 1000 }, { district: "D9", rent_min: 1050 }), true);
  assert.equal(Scoring.isSimilarListing({ district: "D9", rent_min: 1000 }, { district: "D9", rent_min: 1200 }), false);
  assert.equal(Scoring.isSimilarListing({ district: "D9", rent_min: 1000 }, { district: "D10", rent_min: 1000 }), false);
  assert.equal(Scoring.isSimilarListing({ district: "D9" }, { district: "D9" }), false);
  assert.equal(Scoring.isSimilarListing(null, { district: "D9", rent_min: 1000 }), false);
});

test("v2: verdict() and nearMiss() convenience exports agree with score()", () => {
  const l = listing({ district: "D9", rent_min: 2000, rent_max: 2000 });
  const t = tenant({ preferred_districts: ["D9"], budget: 1000 });
  assert.equal(Scoring.verdict(l, t, TODAY), "BLOCKED");
  const nm = Scoring.nearMiss(l, t, TODAY);
  assert.equal(nm.near_miss, false);
});

// =====================================================================
// SECTION 3 — PASS 2 EXTENSIONS (next best action, funnel, data age, elasticity)
// =====================================================================

test("pass2: nextBestAction returns null when cold, blocked, or already actioned", () => {
  assert.equal(Scoring.nextBestAction({ verdict: "QUALIFIED", isCold: true, today: TODAY }), null);
  assert.equal(Scoring.nextBestAction({ verdict: "BLOCKED", today: TODAY }), null);
  assert.equal(Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Not interested" }, today: TODAY }), null);
  assert.equal(Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Queued" }, today: TODAY }), null);
  assert.equal(Scoring.nextBestAction(), null);
  assert.equal(Scoring.nextBestAction(null), null);
});

test("pass2: nextBestAction — viewing booked surfaces collect verdict only once the date has passed", () => {
  const future = Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Viewing booked", viewing_date: "2026-08-12" }, today: TODAY });
  assert.equal(future, null); // 2026-08-12 is tomorrow relative to TODAY (2026-08-11)
  const past = Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Viewing booked", viewing_date: "2026-08-10" }, today: TODAY });
  assert.deepEqual(past, { code: "collect_verdict", label: "collect verdict" });
  const noDate = Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Viewing booked" }, today: TODAY });
  assert.equal(noDate, null);
});

test("pass2: nextBestAction — needs info suggests the one question, qualified+unmarked offers a slot", () => {
  assert.deepEqual(
    Scoring.nextBestAction({ verdict: "NEEDS_INFO", needsInfoReasons: ["budget"], today: TODAY }),
    { code: "needs_info", label: "ask their budget" }
  );
  assert.deepEqual(
    Scoring.nextBestAction({ verdict: "NEEDS_INFO", needsInfoReasons: ["location"], today: TODAY }),
    { code: "needs_info", label: "ask their area" }
  );
  assert.deepEqual(
    Scoring.nextBestAction({ verdict: "QUALIFIED", mark: {}, today: TODAY }),
    { code: "offer_slot", label: "offer a slot" }
  );
});

test("pass2: nextBestAction — contacted with no reply for NUDGE_AFTER_DAYS+ suggests one nudge, earlier does not", () => {
  // Explicit +08:00 offset, not new Date(2026,7,n) — mk.ts is a real Date.now()
  // epoch (sgtDay()-reduced internally now, see scoring.js), so this fixture
  // must mean the same SGT instant regardless of which TZ runs the test suite
  // (node --test respects TZ; a device-local construction here would silently
  // land on a different SGT day under e.g. TZ=Pacific/Auckland).
  const tsRecent = new Date("2026-08-09T09:00:00+08:00").getTime();  // 2 days ago
  const tsStale = new Date("2026-08-08T09:00:00+08:00").getTime();   // 3 days ago (boundary)
  assert.equal(Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Contacted", ts: tsRecent }, today: TODAY }), null);
  assert.deepEqual(
    Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Contacted", ts: tsStale }, today: TODAY }),
    { code: "nudge", label: "one nudge left" }
  );
});

test("pass2: weeklyFunnel counts contacted/viewings within the trailing 7 day window and computes conversion", () => {
  const day = (n) => new Date(2026, 7, n).getTime();
  const records = [
    { v: "Contacted", ts: day(11) },   // today
    { v: "Contacted", ts: day(6) },    // 5 days ago, in window
    { v: "Contacted", ts: day(4) },    // 7 days ago boundary — since = today-6 = Aug 5, so Aug 4 is OUTSIDE
    { v: "Viewing booked", ts: day(9) },
    { v: "Not interested", ts: day(10) }, // does not count toward either bucket
  ];
  const r = Scoring.weeklyFunnel(records, TODAY);
  assert.equal(r.contacted, 2);
  assert.equal(r.viewings, 1);
  assert.equal(r.conversionPct, 50);
});

test("pass2: weeklyFunnel guards divide by zero and null/empty input", () => {
  assert.deepEqual(Scoring.weeklyFunnel([], TODAY), { contacted: 0, viewings: 0, conversionPct: 0 });
  assert.deepEqual(Scoring.weeklyFunnel(null, TODAY), { contacted: 0, viewings: 0, conversionPct: 0 });
  assert.deepEqual(Scoring.weeklyFunnel([{ v: "Viewing booked", ts: Date.now() }], null), { contacted: 0, viewings: 0, conversionPct: 0 });
});

test("pass2: dataAgeTier — green <=3d, amber 4-14d, red >14d, unknown when unparseable", () => {
  // Explicit +08:00 offset (a real generated_ts shape), not new Date(2026,7,n)
  // — dataAgeTier parses this via parseDate(), which now reduces an
  // offset-bearing timestamp through Asia/Singapore rather than the running
  // process's own zone (see "THE CORE TRAP" note in scoring.js). A
  // device-local construction here would represent a DIFFERENT SGT calendar
  // day depending on which TZ runs the suite, which defeats the point of the
  // TZ matrix this test is run under.
  const gen = (n) => `2026-08-${String(n).padStart(2, "0")}T09:00:00+08:00`;
  assert.deepEqual(Scoring.dataAgeTier(gen(11), TODAY), { tier: "green", days: 0 });
  assert.deepEqual(Scoring.dataAgeTier(gen(8), TODAY), { tier: "green", days: 3 });   // boundary: not >3
  assert.deepEqual(Scoring.dataAgeTier(gen(7), TODAY), { tier: "amber", days: 4 });
  assert.deepEqual(Scoring.dataAgeTier("2026-07-28T09:00:00+08:00", TODAY), { tier: "amber", days: 14 }); // boundary: not >14
  assert.deepEqual(Scoring.dataAgeTier("2026-07-27T09:00:00+08:00", TODAY), { tier: "red", days: 15 });
  assert.deepEqual(Scoring.dataAgeTier(null, TODAY), { tier: "unknown", days: null });
  assert.deepEqual(Scoring.dataAgeTier("garbage", TODAY), { tier: "unknown", days: null });
  // v1 payloads only have DATA.generated (YYYY-MM-DD, no time) — must still work
  assert.deepEqual(Scoring.dataAgeTier("2026-08-11", TODAY), { tier: "green", days: 0 });
});

test("pass2: discountListing reduces every unit's rent by delta, floors at 0, leaves unit_type untouched", () => {
  const l = { units: [{ unit_type: "common", rent_min: 900, rent_max: 950 }, { unit_type: "master", rent_min: 30, rent_max: 40 }] };
  const d = Scoring.discountListing(l, 50);
  assert.deepEqual(d.units, [
    { unit_type: "common", rent_min: 850, rent_max: 900 },
    { unit_type: "master", rent_min: 0, rent_max: 0 } // floored, never negative
  ]);
  const flat = Scoring.discountListing({ rent_min: 1000, rent_max: 1200 }, 100);
  assert.equal(flat.rent_min, 900);
  assert.equal(flat.rent_max, 1100);
});

test("pass2: priceElasticity reports how many additional tenants qualify at each discount step", () => {
  // rent_min 1000 -> baseline hard block below 900 (0.9x). At -$50/-$100/-$150 the
  // block floor drops to 855/810/765 respectively — each tenant below is placed to
  // cross exactly one of those floors so the additional count climbs 1,2,3.
  const l = listing({ district: "D9", rent_min: 1000, rent_max: 1000 });
  const tenants = [
    tenant({ id: "T1", preferred_districts: ["D9"], budget: 700 }),  // never clears (700 < 765)
    tenant({ id: "T2", preferred_districts: ["D9"], budget: 860 }),  // clears at -$50 (860 >= 855)
    tenant({ id: "T3", preferred_districts: ["D9"], budget: 820 }),  // clears at -$100 (820 >= 810)
    tenant({ id: "T4", preferred_districts: ["D9"], budget: 780 }),  // clears at -$150 (780 >= 765)
  ];
  const r = Scoring.priceElasticity(l, tenants, TODAY);
  assert.deepEqual(r, [
    { delta: 50, additional: 1 },   // T2 only
    { delta: 100, additional: 2 },  // T2 + T3
    { delta: 150, additional: 3 },  // T2 + T3 + T4
  ]);
  assert.equal(Scoring.priceElasticity(null, tenants, TODAY).length, 0);
  assert.equal(Scoring.priceElasticity(l, [], TODAY).length, 0);
});

// ---------------------------------------------------------------------------
// Section 3 (HARDENING) — app.js safety helpers.
//
// app.js is a browser script: it touches document/localStorage/DATA at load,
// so it cannot simply be require()d here. These helpers are pure though, so
// the tests below lift their REAL source text out of app.js and evaluate just
// that, with dependencies injected. Testing the shipped source rather than a
// copy is the whole point — a regression in app.js fails these, and build.py
// runs this suite before it will write an artifact.
// ---------------------------------------------------------------------------
import { readFileSync } from "node:fs";

const APP_SRC = readFileSync(new URL("../../scripts/matchmaker/app.js", import.meta.url), "utf8");

// Brace matched slice of `function NAME(...) { ... }`. Safe for the small pure
// helpers used below (none contain a brace inside a string or regex literal).
function extractFn(name) {
  const start = APP_SRC.indexOf("function " + name + "(");
  assert.notEqual(start, -1, "function " + name + "() not found in app.js");
  let depth = 0;
  for (let j = APP_SRC.indexOf("{", start); j < APP_SRC.length; j++) {
    if (APP_SRC[j] === "{") depth++;
    else if (APP_SRC[j] === "}" && --depth === 0) return APP_SRC.slice(start, j + 1);
  }
  throw new Error("unbalanced braces extracting " + name + " from app.js");
}
function extractLine(startsWith) {
  const line = APP_SRC.split("\n").find(l => l.trim().startsWith(startsWith));
  assert.ok(line, "line starting with " + startsWith + " not found in app.js");
  return line;
}

const appEsc = new Function(
  extractLine("const ESC_MAP") + "\n" + extractFn("esc") + "\n" + extractFn("escUrl") + "\nreturn { esc, escUrl };"
)();

// coldBlocked() closes over app.js's module level TODAY, so the clock is
// injected here rather than passed as an argument. It reaches the 5 day rule
// through isColdT/coldDaysOf (app.js's per tenant memo of Scoring.coldDays),
// so those come along too — lifting the real ones keeps this exercising the
// same path the app takes, memo included, rather than a stand in that could
// answer differently.
function makeColdBlocked(today) {
  const fn = new Function("Scoring", "TODAY",
    extractLine("const COLD_CACHE") + "\n" +
    extractFn("coldDaysOf") + "\n" + extractFn("isColdT") + "\n" +
    extractFn("isCobroke") + "\n" + extractFn("coldBlocked") +
    "\nreturn { coldBlocked, COLD_CACHE };"
  )(Scoring, today);
  const out = fn.coldBlocked;
  out.COLD_CACHE = fn.COLD_CACHE;
  return out;
}

const appIsMarkKey = new Function(
  'const MARK_PREFIX = "cbk_", SCRATCH_KEY = "cbk_scratch", OFFER_PREFIX = "cbk_offer_";\n' +
  'const PREFS_KEY = "cbk_prefs", HISTORY_KEY = "cbk_history", REVEALS_KEY = "cbk_reveals", ERR_KEY = "cbk_errors";\n' +
  extractFn("isMarkKey") + "\nreturn isMarkKey;"
)();

test("hardening: esc() neutralises markup in data derived strings", () => {
  // last_wa.snippet is a REAL WhatsApp message from a stranger and reaches
  // innerHTML — it must come out inert.
  const payload = 'hi <img src=x onerror="alert(1)">';
  const out = appEsc.esc(payload);
  // No tag can open, so the onerror text that remains is inert prose.
  assert.ok(!/<[a-z/!]/i.test(out), "a tag could still open: " + out);
  assert.ok(!out.includes('"'), "an unescaped quote could still close an attribute");
  assert.equal(out, "hi &lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  // attribute breakouts, both quote styles
  assert.equal(appEsc.esc(`" onmouseover="x`), "&quot; onmouseover=&quot;x");
  assert.equal(appEsc.esc("' onfocus='x"), "&#39; onfocus=&#39;x");
  // ampersand first, so nothing can be double decoded back into a tag
  assert.equal(appEsc.esc("&lt;script&gt;"), "&amp;lt;script&amp;gt;");
  // null/undefined render as empty, never the string "null"
  assert.equal(appEsc.esc(null), "");
  assert.equal(appEsc.esc(undefined), "");
  assert.equal(appEsc.esc(0), "0");
});

test("hardening: escUrl() allows real link schemes and drops script bearing ones", () => {
  assert.equal(appEsc.escUrl("https://example.com/a.jpg"), "https://example.com/a.jpg");
  assert.equal(appEsc.escUrl("tel:6590000001"), "tel:6590000001");
  assert.equal(appEsc.escUrl("/sw.js"), "/sw.js");
  // a poisoned photo/listing URL out of the database must not become a link
  assert.equal(appEsc.escUrl("javascript:alert(1)"), "");
  assert.equal(appEsc.escUrl("JaVaScRiPt:alert(1)"), "");
  assert.equal(appEsc.escUrl("data:text/html,<script>alert(1)</script>"), "");
  assert.equal(appEsc.escUrl(""), "");
  assert.equal(appEsc.escUrl(null), "");
  // quotes inside an otherwise fine URL still get escaped, so it cannot break out of href="..."
  assert.ok(!appEsc.escUrl('https://example.com/"onload="x').includes('"'));
});

test("hardening: coldBlocked() is the one 5 day rule, and exempts co-broke", () => {
  const today = new Date(2026, 7, 11);
  const appCold = makeColdBlocked(today);
  const cold = { id: "T1", name: "Tan Ah Test", last_contact: "2026-07-27" };   // 15 days
  const fresh = { id: "T2", name: "Lim Ah Test", last_contact: "2026-08-10" };  // 1 day
  const unknown = { id: "T3", name: "Ng Ah Test" };                             // no signal
  const own = { id: "LL1", name: "Test Block", is_cobroke: false };
  const cobroke = { id: "LL2", name: "Other Block", is_cobroke: true };
  const bySource = { id: "LL3", name: "Third Block", source: "co-broke" };

  // The badge said "cold 15d" while the Draft button still produced a full
  // message — badge and enforcement now read the same function.
  assert.equal(Scoring.coldDays(cold, today), 15);
  assert.equal(appCold(own, cold), true);

  // landlord / co-broke contact is never blocked by the tenant cold rule
  assert.equal(appCold(cobroke, cold), false);
  assert.equal(appCold(bySource, cold), false);

  assert.equal(appCold(own, fresh), false);
  assert.equal(appCold(null, cold), true);      // tenant only contexts (health tab) still apply it
  assert.equal(appCold(own, unknown), false);   // unknown is not proof of death — app stays permissive,
                                                // queue_drafts.py refuses these separately at dispatch time
  // boundary: exactly 5 days is not cold, 6 is
  assert.equal(appCold(own, { last_contact: "2026-08-06" }), false);
  assert.equal(appCold(own, { last_contact: "2026-08-05" }), true);
});

// This draft is the one piece of text that goes to a LANDLORD about tenants,
// so what it must never contain matters more than what it says.
const appShortlist = new Function("draftSlot",
  extractFn("fname") + "\n" + extractFn("listingShort") + "\n" +
  extractFn("summarizeTenantAnon") + "\n" + extractFn("landlordShortlistDraft") +
  "\nreturn landlordShortlistDraft;"
)(() => "this Sat 3pm");

test("hardening: the landlord shortlist draft is anonymous and reads naturally at one tenant", () => {
  const l = { id: "LL1", name: "Test Block", rent_min: 1500 };
  const t1 = { id: "T1", name: "Tan Ah Test", phone: "90000001", pax: 2, budget: 1600,
               move_in: "2026-09-01", occupation: "engineer" };
  const t2 = { id: "T2", name: "Lim Ah Test", phone: "90000002", pax: 1, budget: 1500,
               move_in: "2026-09-15", occupation: "teacher" };

  const one = appShortlist(l, [t1], "this Sat 3pm");
  const two = appShortlist(l, [t1, t2], "this Sat 3pm");

  // The gate now opens at 1 qualified tenant, so singular has to be right.
  assert.ok(one.includes("1 screened tenant"), one);
  assert.ok(!one.includes("1 screened tenants"), "singular/plural bug: " + one);
  assert.ok(one.includes("line up a viewing"), one);
  assert.ok(two.includes("2 screened tenants") && two.includes("line up viewings"), two);

  // Never a tenant name or phone — the whole point of the anonymised summary.
  [one, two].forEach(txt => {
    ["Tan", "Lim", "Ah Test", "90000001", "90000002"].forEach(leak =>
      assert.ok(!txt.includes(leak), "leaked " + leak + " to a landlord: " + txt));
    assert.ok(txt.includes("pax"), "the anonymous facts should still be there: " + txt);
    // House voice: no sign off, and no hyphens in the PROSE. A passed through
    // move_in date (YYYY-MM-DD) is data, not copy, so it is excluded from the
    // hyphen check — every draft in this app renders move_in verbatim, which is
    // a separate copy question and deliberately not changed here.
    assert.ok(!/-/.test(txt.replace(/\d{4}-\d{2}-\d{2}/g, "")), "hyphen in landlord facing copy: " + txt);
    assert.ok(!/Winfred|CEA|R073319H/.test(txt), "sign off leaked into the draft: " + txt);
  });
});

test("hardening: the per tenant cold memo answers per tenant, never across them", () => {
  // updateFacetedCounts() asks coldBlocked once per PAIR, so each tenant's
  // answer is recomputed once per listing — the memo exists to collapse that.
  // Its whole risk is answering for the wrong person.
  const today = new Date(2026, 7, 11);
  const appCold = makeColdBlocked(today);
  const own = { id: "LL1", name: "Test Block", is_cobroke: false };

  const coldT = { id: "T1", name: "Tan Ah Test", last_contact: "2026-07-27" };
  const freshT = { id: "T2", name: "Lim Ah Test", last_contact: "2026-08-10" };
  assert.equal(appCold(own, coldT), true);
  assert.equal(appCold(own, freshT), false, "the cold answer must not carry over to the next tenant");
  // repeat reads (what the pair sweep actually does) stay stable and correct
  for (let i = 0; i < 5; i++) {
    assert.equal(appCold(own, coldT), true);
    assert.equal(appCold(own, freshT), false);
  }
  assert.equal(appCold.COLD_CACHE.size, 2, "one entry per tenant, not per pair");

  // Tenants with no id (the boundary fixtures below, and any caller passing a
  // bare object) must never share a single null-keyed slot.
  const before = appCold.COLD_CACHE.size;
  assert.equal(appCold(own, { last_contact: "2026-08-05" }), true);
  assert.equal(appCold(own, { last_contact: "2026-08-06" }), false,
    "an unkeyed tenant must not inherit the previous unkeyed tenant's answer");
  assert.equal(appCold.COLD_CACHE.size, before, "unkeyed tenants must not enter the cache at all");

  // the memo must not drift from the rule it is memoising
  [null, undefined, 0, 5, 6, 15, 400].forEach(dc => {
    assert.equal(Scoring.isColdFromDays(dc), dc != null && dc > Scoring.COLD_DAYS_THRESHOLD, "dc=" + dc);
  });
  assert.equal(Scoring.isCold(coldT, today), Scoring.isColdFromDays(Scoring.coldDays(coldT, today)));
});

test("hardening: isMarkKey() keeps import out of every non mark cbk_ key", () => {
  assert.equal(appIsMarkKey("cbk_LL1_T1"), true);
  assert.equal(appIsMarkKey("cbk_LL001_TMP2"), true);
  // all of these share the cbk_ prefix — an imported blob must not reach them
  ["cbk_prefs", "cbk_history", "cbk_reveals", "cbk_errors", "cbk_scratch",
   "cbk_offer_LL1_T1", "cbk_backup_mon"].forEach(k => {
    assert.equal(appIsMarkKey(k), false, k + " must not be writable as a mark");
  });
  assert.equal(appIsMarkKey("cbko_LL1_T1"), false); // overrides have their own map
  assert.equal(appIsMarkKey("other_key"), false);
  assert.equal(appIsMarkKey(""), false);
  assert.equal(appIsMarkKey(null), false);
});

// Minimal DOM stand in: just enough of createElement()/appendChild()/innerHTML
// for undoToast() (and the el() helper it calls) to run for real, so the test
// below inspects what the REAL app.js source actually assigns to innerHTML —
// not a copy of the escaping logic typed again into the test.
function fakeDocument() {
  const created = [];
  function makeNode(tag) {
    const node = {
      tagName: tag, _html: "", children: [], attrs: {},
      set innerHTML(v) { this._html = String(v); },
      get innerHTML() { return this._html; },
      set className(v) { this.attrs.class = v; },
      set id(v) { this.attrs.id = v; },
      get id() { return this.attrs.id; },
      appendChild(child) { this.children.push(child); return child; },
      querySelector() { return null; },
      remove() {},
      addEventListener() {},
    };
    created.push(node);
    return node;
  }
  const body = makeNode("body");
  return { createElement: makeNode, body, querySelector: () => null, _created: created };
}

function makeUndoToast(doc) {
  const src =
    extractLine("const $ =") + "\n" +
    extractLine("const el =") + "\n" +
    extractLine("const ESC_MAP") + "\n" +
    extractFn("esc") + "\n" +
    extractFn("undoToast") + "\n" +
    "return undoToast;";
  return new Function("document", src)(doc);
}

test("hardening: undoToast() escapes its message — a hostile tenant name cannot open a tag", () => {
  const doc = fakeDocument();
  const undoToast = makeUndoToast(doc);
  // Mirrors a real call site: writeMarkUndoable's label is built from
  // fname(t.name), and fname() only splits on space/","/"(" — a name using
  // none of those (a backtick call needs no parens) reaches undoToast whole,
  // exactly like every mark action (Contacted/Viewing booked/snoozed/woken/
  // unqueued/cleared) routes a tenant name through this same function.
  const hostileLabel = "Tan<img src=x onerror=alert`1`> marked Contacted";
  undoToast(hostileLabel, () => {});
  const box = doc._created.find(n => n.attrs.id === "undotoast");
  assert.ok(box, "undo toast element was not created");
  clearTimeout(box._t);
  assert.ok(!/<img/i.test(box.innerHTML), "a live <img> tag survived into innerHTML: " + box.innerHTML);
  assert.ok(box.innerHTML.includes("&lt;img"), "payload should be escaped, not silently dropped: " + box.innerHTML);
});

function makePhoneSpanHtml({ prefs, dataObj, nowReal, revealed }) {
  const src =
    extractLine("const ESC_MAP") + "\n" +
    extractFn("esc") + "\n" +
    extractFn("normPhone") + "\n" +
    extractFn("maskPhone") + "\n" +
    "const PREFS = " + JSON.stringify(prefs) + ";\n" +
    "const DATA = " + JSON.stringify(dataObj || {}) + ";\n" +
    "const NOW_REAL = new Date(" + (nowReal || new Date()).getTime() + ");\n" +
    // phonesForceMasked() reads NOW_REAL_SGT (Singapore calendar day), not
    // NOW_REAL directly — see that function's comment in app.js — so this
    // slice needs it defined too, exactly as the real core-state block does.
    "const NOW_REAL_SGT = Scoring.sgtDay(NOW_REAL);\n" +
    "const REVEALED = new Set(" + JSON.stringify(revealed || []) + ");\n" +
    extractFn("phonesForceMasked") + "\n" +
    extractFn("maskingActive") + "\n" +
    extractFn("assistantMode") + "\n" +
    extractFn("isRevealed") + "\n" +
    extractFn("phoneSpanHtml") + "\n" +
    "return phoneSpanHtml;";
  return new Function("Scoring", src)(Scoring);
}

test("hardening: phoneSpanHtml() checks assistant mode before global masking — a phone cannot leak when assistant mode is on", () => {
  // The exact real world sequence that produced the bug: Winfred had global
  // masking OFF (unmasked numbers for himself), then handed the device over
  // and flipped assistant mode ON without separately turning masking back on.
  // dataObj: {} -> phonesForceMasked() reads dataAgeTier as "unknown", never "red".
  const assistantOnMaskOff = makePhoneSpanHtml({ prefs: { masked: false, assistant_mode: true } });
  const leaked = assistantOnMaskOff("tenant", "T1", "91234567");
  assert.ok(!leaked.includes("91234567"), "raw phone leaked through assistant mode: " + leaked);
  assert.ok(leaked.includes("assistant mode"), "expected the assistant mode disabled span: " + leaked);

  // Defence in depth: the "masking fully off, not in assistant mode" branch
  // must still escape — a phone value is data derived text like any other.
  const unmaskedNotAssistant = makePhoneSpanHtml({ prefs: { masked: false, assistant_mode: false } });
  const hostilePhone = '"><img src=x onerror=alert(1)>';
  const escaped = unmaskedNotAssistant("tenant", "T2", hostilePhone);
  assert.ok(!/<img/i.test(escaped), "a live tag survived an unmasked phone value: " + escaped);

  // Ordinary masked behavior (the common case) is unaffected by the reordering.
  const maskedDefault = makePhoneSpanHtml({ prefs: { masked: true, assistant_mode: false } });
  const masked = maskedDefault("tenant", "T3", "91234567");
  assert.ok(!masked.includes("91234567"), "should still be masked by default: " + masked);
  assert.ok(masked.includes("data-reveal"), "should still offer the tap to reveal affordance: " + masked);

  // A previously revealed number still shows in plain text when NOT in assistant mode.
  const revealedCase = makePhoneSpanHtml({ prefs: { masked: true, assistant_mode: false }, revealed: ["tenant:T4"] });
  assert.ok(revealedCase("tenant", "T4", "91234567").includes("91234567"));
  // But assistant mode overrides even an already revealed number.
  const revealedButAssistant = makePhoneSpanHtml({ prefs: { masked: true, assistant_mode: true }, revealed: ["tenant:T4"] });
  assert.ok(!revealedButAssistant("tenant", "T4", "91234567").includes("91234567"));
});

// =====================================================================
// SECTION 4 (TZ MATRIX, cycle 6) — the core trap: DATA is built on an SGT
// machine, but the phone opening the artifact can sit in ANY timezone. These
// spawn a REAL child `node` process under an explicit TZ env var (node
// respects TZ) so the assertion is proven against an actual foreign device
// clock, not simulated — a regression here (e.g. someone routing an
// offset-bearing timestamp back through raw device-local getters instead of
// sgtDay()) fails even under a single default-TZ `node --test` run, without
// a human remembering to re-run the whole suite by hand under TZ=...
//
// This is also how build.py's own test gate exercises the fix: run_tests()
// just shells out to `node --test`, so these child-per-TZ cases run as part
// of every pre-build check on whatever machine invokes build.py.
// =====================================================================
import { spawnSync } from "node:child_process";

const SCORING_PATH = new URL("../../scripts/matchmaker/scoring.js", import.meta.url).pathname;
const TZ_MATRIX = ["Asia/Singapore", "America/New_York", "Pacific/Auckland", "UTC"];

function runUnderTZ(tz, code) {
  const r = spawnSync(process.execPath, ["-e", code], {
    env: Object.assign({}, process.env, { TZ: tz }),
    encoding: "utf8",
  });
  if (r.status !== 0) throw new Error(`child (TZ=${tz}) exited ${r.status}: ${r.stderr}`);
  return r.stdout.trim();
}
// require() of scoring.js inside the child returns {} for the same reason
// noted at the top of this file (repo package.json is "type":"module") — the
// child reads globalThis.Scoring back out, exactly like this file does.
const REQUIRE_SCORING = `require(${JSON.stringify(SCORING_PATH)});const Scoring=globalThis.Scoring;`;

test("tz-matrix: coldDays does not drift when the freshest signal is an offset timestamp (last_wa.ts), on any device timezone", () => {
  // TODAY is 2026-08-11 (data-pinned, bare digits). last_wa.ts is an
  // unambiguous "2026-08-09, 09:00 SGT" — exactly 2 SGT-calendar-days ago.
  // Before this cycle's fix, a device set well behind SGT (America/New_York,
  // UTC-4 in August) would read that instant's OWN calendar day via its own
  // local getters as Aug 8, not Aug 9 — one day older than reality, silently
  // crossing real thresholds (NUDGE_AFTER_DAYS=3, COLD_DAYS_THRESHOLD=5)
  // purely from where the phone happens to be sitting.
  const code = `${REQUIRE_SCORING}
    const TODAY = new Date(2026, 7, 11);
    const t = { last_contact: "", last_wa: { ts: "2026-08-09T09:00:00+08:00" } };
    console.log(Scoring.coldDays(t, TODAY));`;
  for (const tz of TZ_MATRIX) assert.equal(runUnderTZ(tz, code), "2", `coldDays wrong under TZ=${tz}`);
});

test("tz-matrix: dataAgeTier does not jump a tier depending on the viewing device's timezone", () => {
  const code = `${REQUIRE_SCORING}
    const TODAY = new Date(2026, 7, 11);
    console.log(JSON.stringify(Scoring.dataAgeTier("2026-08-11T08:15:58+08:00", TODAY)));`;
  for (const tz of TZ_MATRIX) {
    assert.deepEqual(JSON.parse(runUnderTZ(tz, code)), { tier: "green", days: 0 }, `dataAgeTier wrong under TZ=${tz}`);
  }
});

test("tz-matrix: nextBestAction's nudge-after-N-days boundary does not shift with device timezone", () => {
  // A "Contacted" mark stamped at 2026-08-08T09:00+08:00 (real Date.now()
  // shape) is exactly 3 SGT-days before 2026-08-11 -> right at the
  // NUDGE_AFTER_DAYS boundary, which must trigger on every device.
  const code = `${REQUIRE_SCORING}
    const TODAY = new Date(2026, 7, 11);
    const ts = new Date("2026-08-08T09:00:00+08:00").getTime();
    console.log(JSON.stringify(Scoring.nextBestAction({ verdict: "QUALIFIED", mark: { v: "Contacted", ts }, today: TODAY })));`;
  for (const tz of TZ_MATRIX) {
    assert.deepEqual(JSON.parse(runUnderTZ(tz, code)), { code: "nudge", label: "one nudge left" }, `nudge boundary wrong under TZ=${tz}`);
  }
});

test("tz-matrix: parseDate never mixes a bare YYYY-MM-DD day with an offset timestamp's raw device-local day", () => {
  // Both operands name the exact same real calendar concept ("2026-08-09" in
  // SGT terms) via the two different forms parseDate() accepts — a bare date
  // and a full ISO instant. They must diff to zero days apart on every device.
  const code = `${REQUIRE_SCORING}
    const a = Scoring.parseDate("2026-08-09");
    const b = Scoring.parseDate("2026-08-09T23:30:00+08:00");
    console.log(Scoring.atMidnight(a).getTime() === Scoring.atMidnight(b).getTime());`;
  for (const tz of TZ_MATRIX) assert.equal(runUnderTZ(tz, code), "true", `mixed parse forms disagree under TZ=${tz}`);
});

const APP_JS_PATH = new URL("../../scripts/matchmaker/app.js", import.meta.url).pathname;
// Same brace-matched extraction extractFn()/extractLine() use above, rewritten
// as a plain string so it can run INSIDE a spawned child (the child has its
// own fresh process, so it re-reads and re-extracts app.js itself rather than
// receiving already-extracted closures from this parent process).
const EXTRACT_FN_SRC = `
  function extractFn(src, name) {
    const start = src.indexOf("function " + name + "(");
    if (start === -1) throw new Error("not found: " + name);
    let depth = 0;
    for (let j = src.indexOf("{", start); j < src.length; j++) {
      if (src[j] === "{") depth++;
      else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
    }
    throw new Error("unbalanced braces extracting " + name);
  }`;

// weekdayName/startHHMM name a Singapore viewing slot (e.g. the landlord's
// fixed_viewing); nowIso is a FIXED real-world instant given as an explicit
// +08:00 string so it means the same Singapore moment no matter which TZ the
// child process itself runs under. Returns the resolved date as YYYY-MM-DD.
function resolvedSlotDateUnderTZ(tz, nowIso, weekdayName, startHHMM) {
  const code = `
    const fs = require("fs");
    ${EXTRACT_FN_SRC}
    const appSrc = fs.readFileSync(${JSON.stringify(APP_JS_PATH)}, "utf8");
    ${REQUIRE_SCORING}
    const WEEKDAY_NAMES = ["sun","mon","tue","wed","thu","fri","sat"];
    const build = new Function("Scoring", "WEEKDAY_NAMES", "NOW_REAL_SGT_PARTS",
      extractFn(appSrc, "weekdayIndex") + "\\n" +
      extractFn(appSrc, "nextWeekdayDate") + "\\n" +
      extractFn(appSrc, "sgtNowMinutes") + "\\n" +
      extractFn(appSrc, "fixedViewingStartMinutes") + "\\n" +
      "return { nextWeekdayDate, fixedViewingStartMinutes, sgtNowMinutes };"
    );
    const NOW_REAL = new Date(${JSON.stringify(nowIso)});
    const NOW_REAL_SGT_PARTS = Scoring.sgtParts(NOW_REAL);
    const NOW_REAL_SGT = Scoring.sgtDay(NOW_REAL);
    const { nextWeekdayDate, fixedViewingStartMinutes, sgtNowMinutes } = build(Scoring, WEEKDAY_NAMES, NOW_REAL_SGT_PARTS);
    const startMin = ${startHHMM ? `fixedViewingStartMinutes({ start: ${JSON.stringify(startHHMM)} })` : "null"};
    const d = nextWeekdayDate(${JSON.stringify(weekdayName)}, NOW_REAL_SGT, sgtNowMinutes(), startMin);
    console.log(d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"));`;
  return runUnderTZ(tz, code);
}

test("tz-matrix: a viewing slot proposed for 'Fri' resolves to the correct Singapore Friday on every device timezone", () => {
  // 2026-08-14 is a Friday (SGT). At 09:00 SGT that same Friday, "this Fri"
  // must still mean TODAY (14th) on every device — the naive bug this guards
  // against is a device far enough from SGT reading "now" as Thursday the
  // 13th and proposing a Friday a week further out, or vice both.
  for (const tz of TZ_MATRIX) {
    assert.equal(resolvedSlotDateUnderTZ(tz, "2026-08-14T09:00:00+08:00", "fri", null), "2026-08-14", `TZ=${tz}`);
  }
});

test("tz-matrix: 'this Fri' rolls to NEXT Friday once the fixed_viewing start time has already passed, on every device timezone", () => {
  // Same Friday, but now 21:30 SGT — half an hour after a fixed_viewing that
  // runs 20:00-21:00. "This Fri" proposing a slot that already ended is the
  // exact bug item 3 calls out; the sane reading is next Friday, 2026-08-21.
  for (const tz of TZ_MATRIX) {
    assert.equal(resolvedSlotDateUnderTZ(tz, "2026-08-14T21:30:00+08:00", "fri", "20:00"), "2026-08-21", `TZ=${tz}`);
  }
});

test("tz-matrix: 'this Fri' still resolves to today while the fixed_viewing slot has not started yet, on every device timezone", () => {
  // 19:30 SGT, 30 minutes before an 8pm start — still today.
  for (const tz of TZ_MATRIX) {
    assert.equal(resolvedSlotDateUnderTZ(tz, "2026-08-14T19:30:00+08:00", "fri", "20:00"), "2026-08-14", `TZ=${tz}`);
  }
});

test("tz-matrix: gcalLink encodes a Singapore wall clock time (not UTC, not the viewing device's zone), and rolls the end date forward across a midnight-crossing slot", () => {
  const code = `
    const fs = require("fs");
    ${EXTRACT_FN_SRC}
    const appSrc = fs.readFileSync(${JSON.stringify(APP_JS_PATH)}, "utf8");
    const build = new Function("assistantMode", "displayPhone", "fname", "areaName", "isoLocal",
      extractFn(appSrc, "gcalLink") + "\\nreturn gcalLink;"
    );
    const isoLocal = new Function(extractFn(appSrc, "isoLocal") + "\\nreturn isoLocal(arguments[0]);");
    const gcalLink = build(() => false, () => "", (n) => n, (l) => l.address, (d) => isoLocal(d));
    // 7pm SGT must appear in the link as 1900 local + ctz=Singapore, never as
    // 1900Z (which Google reads as UTC — 7pm UTC is 3am the NEXT DAY in SGT).
    const l = { name: "Test Listing", address: "1 Test Ave" };
    const t = { name: "Tan Ah Test", phone: "" };
    console.log(gcalLink(l, t, "2026-08-14", "19:00"));
    // Late-night slot spilling past midnight: start 23:45 + 30min = 00:15 the
    // NEXT calendar day. The end date must roll forward, or Google reads the
    // event as ending before it starts.
    console.log(gcalLink(l, t, "2026-08-14", "23:45"));`;
  const [normal, midnightCross] = runUnderTZ("Asia/Singapore", code).split("\n");
  assert.ok(!/Z\/|Z$/.test(normal), `dates= must not carry a bare Z suffix (UTC): ${normal}`);
  assert.ok(normal.includes("ctz=Asia%2FSingapore"), `ctz=Asia/Singapore missing: ${normal}`);
  assert.ok(normal.includes("dates=20260814T190000/20260814T193000"), `wrong SGT wall clock encoding: ${normal}`);
  assert.ok(midnightCross.includes("dates=20260814T234500/20260815T001500"), `end date did not roll forward across midnight: ${midnightCross}`);
});

test("hardening: service worker touches the network only on the deployed origin", () => {
  // The local artifact logged a console 404 for /sw.js on every load: register()
  // reports its own failure to the console and .catch() cannot mute it, and a
  // bare probe for a missing file logs a 404 of its own. So locally we make no
  // request at all, and on the deployment we probe before registering.
  const src = extractFn("registerServiceWorker");
  assert.ok(/location\.protocol === "https:"/.test(src), "https gate missing");
  assert.ok(/hostname !== "localhost"/.test(src), "localhost must be excluded, not allowed");
  assert.ok(/hostname !== "127\.0\.0\.1"/.test(src), "127.0.0.1 must be excluded");
  assert.ok(/fetch\("\/sw\.js"/.test(src), "existence probe missing — register() would log its own 404");
  // the early return has to come before anything that hits the network
  assert.ok(src.indexOf("if (!deployed) return;") < src.indexOf("fetch("),
    "the deployed gate must short circuit before any network call");
  assert.ok(src.indexOf("fetch(") < src.indexOf("navigator.serviceWorker.register"),
    "register() must only run after the probe says the file is there");
});
