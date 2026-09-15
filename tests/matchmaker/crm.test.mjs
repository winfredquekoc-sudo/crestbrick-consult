// CRM tab pure helper tests for scripts/matchmaker/app.js — phone normalisation,
// the follow up queue's own selection, snooze date arithmetic, deal totals and the
// deals JSON export row shape.
// Run: node --test tests/matchmaker/crm.test.mjs   (build.py runs it too)
//
// Same anchored slice technique as filters.test.mjs/rows.test.mjs/state.test.mjs:
// app.js is a browser script that touches document/DATA at load, so it cannot be
// require()d here directly — the relevant function bodies are lifted out by source
// anchors and evaluated with new Function.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const APP_SRC = readFileSync(new URL("../../scripts/matchmaker/app.js", import.meta.url), "utf8");
const APP_LINES = APP_SRC.split("\n");

function slice(startAnchor, endAnchor) {
  const a = APP_LINES.findIndex(l => l.includes(startAnchor));
  assert.notEqual(a, -1, "anchor not found in app.js: " + startAnchor);
  const b = APP_LINES.findIndex((l, i) => i > a && l.includes(endAnchor));
  assert.notEqual(b, -1, "end anchor not found in app.js: " + endAnchor);
  return APP_LINES.slice(a, b).join("\n");
}

// =====================================================================
// normPhone — the +65 normaliser, including the doubled "6565" prefix fix
// =====================================================================
const PHONE_SRC = slice("function normPhone(raw)", "function waLink(l, t)");
function makePhoneHelpers() {
  return new Function(PHONE_SRC + "\nreturn { normPhone };")();
}
const PH = makePhoneHelpers();

test("normPhone: bare 8 digit SG mobile gets the 65 prefix", () => {
  assert.equal(PH.normPhone("91234567"), "6591234567");
  assert.equal(PH.normPhone("81234567"), "6581234567");
});

test("normPhone: already prefixed 10 digit numbers pass through unchanged", () => {
  assert.equal(PH.normPhone("6591234567"), "6591234567");
});

test("normPhone: a doubled 6565 prefix (typed twice) is stripped back to one", () => {
  assert.equal(PH.normPhone("6565 9123 4567"), "6591234567");
  assert.equal(PH.normPhone("+6565 8123 4567"), "6581234567");
});

test("normPhone: strips punctuation, empty/garbage input returns empty string", () => {
  assert.equal(PH.normPhone("+65 9123-4567"), "6591234567");
  assert.equal(PH.normPhone(""), "");
  assert.equal(PH.normPhone(null), "");
});

// =====================================================================
// CRM tab pure helpers — addDaysISO / followUpQueueItems / dealTotals /
// dealExportRow / crmComputeNet, sliced together (they share the same span).
// =====================================================================
const CRM_SRC = slice("function addDaysISO(iso, days) {", "function crmContactNameByKey(key) {");
function makeCrmHelpers() {
  return new Function(
    CRM_SRC + "\nreturn { addDaysISO, followUpQueueItems, dealDateOf, dealTotals, crmComputeNet, dealExportRow };"
  )();
}
const C = makeCrmHelpers();

test("addDaysISO: adds calendar days, crossing a month boundary correctly", () => {
  assert.equal(C.addDaysISO("2026-09-16", 1), "2026-09-17");
  assert.equal(C.addDaysISO("2026-09-16", 3), "2026-09-19");
  assert.equal(C.addDaysISO("2026-09-16", 7), "2026-09-23");
  assert.equal(C.addDaysISO("2026-09-29", 3), "2026-10-02");
});

test("addDaysISO: 0/negative days work too (defensive, not just the 1/3/7 snooze presets)", () => {
  assert.equal(C.addDaysISO("2026-09-16", 0), "2026-09-16");
  assert.equal(C.addDaysISO("2026-09-16", -1), "2026-09-15");
});

// ---- followUpQueueItems ----

test("followUpQueueItems: overdue and due today tasks are included, future and done ones are not", () => {
  const tasks = [
    { id: 1, title: "overdue", due: "2026-09-10", done: false },
    { id: 2, title: "due today", due: "2026-09-16", done: false },
    { id: 3, title: "future", due: "2026-09-20", done: false },
    { id: 4, title: "done but overdue", due: "2026-09-10", done: true },
    { id: 5, title: "no due date", due: null, done: false },
  ];
  const { tasks: due } = C.followUpQueueItems(tasks, [], "2026-09-16");
  assert.deepEqual(due.map(t => t.id), [1, 2]);
});

test("followUpQueueItems: sorts tasks soonest/most overdue first", () => {
  const tasks = [
    { id: "a", due: "2026-09-16", done: false },
    { id: "b", due: "2026-09-01", done: false },
    { id: "c", due: "2026-09-10", done: false },
  ];
  const { tasks: due } = C.followUpQueueItems(tasks, [], "2026-09-16");
  assert.deepEqual(due.map(t => t.id), ["b", "c", "a"]);
});

test("followUpQueueItems: entity next_due plans due today or overdue are included, future ones are not", () => {
  const entities = [
    { key: "phone:1", name: "Overdue Plan", next_due: "2026-09-01" },
    { key: "phone:2", name: "Due Today", next_due: "2026-09-16" },
    { key: "phone:3", name: "Future", next_due: "2026-09-30" },
    { key: "phone:4", name: "No plan", next_due: null },
  ];
  const { plans } = C.followUpQueueItems([], entities, "2026-09-16");
  assert.deepEqual(plans.map(p => p.key), ["phone:1", "phone:2"]);
});

// ---- dealTotals ----

test("dealTotals: sums gross/net for the current month and separately for the year to date", () => {
  const deals = [
    { commission_gross: 1000, commission_net: 900, completion_date: "2026-09-10", stage: "completed" },
    { commission_gross: 500, commission_net: 450, completion_date: "2026-09-16", stage: "completed" },
    { commission_gross: 2000, commission_net: 1800, completion_date: "2026-03-05", stage: "completed" }, // this year, not this month
    { commission_gross: 9999, commission_net: 9999, completion_date: "2025-12-01", stage: "completed" }, // last year — excluded from both
  ];
  const t = C.dealTotals(deals, "2026-09-16");
  assert.equal(t.monthGross, 1500);
  assert.equal(t.monthNet, 1350);
  assert.equal(t.ytdGross, 3500);
  assert.equal(t.ytdNet, 3150);
});

test("dealTotals: a deal that fell through earned no commission and is excluded entirely", () => {
  const deals = [
    { commission_gross: 5000, commission_net: 4500, completion_date: "2026-09-10", stage: "fell_through" },
    { commission_gross: 1000, commission_net: 900, completion_date: "2026-09-10", stage: "completed" },
  ];
  const t = C.dealTotals(deals, "2026-09-16");
  assert.equal(t.monthGross, 1000);
  assert.equal(t.ytdGross, 1000);
});

test("dealTotals: falls back to otp_date then created_at when completion_date is not set yet", () => {
  const deals = [
    { commission_gross: 100, commission_net: 100, otp_date: "2026-09-05", stage: "otp" },
    { commission_gross: 200, commission_net: 200, created_at: "2026-09-01T00:00:00Z", stage: "agreed" },
  ];
  const t = C.dealTotals(deals, "2026-09-16");
  assert.equal(t.monthGross, 300);
});

test("crmComputeNet: applies the co broke split percentage to gross, no split means net equals gross", () => {
  assert.equal(C.crmComputeNet(1000, 40), 600);
  assert.equal(C.crmComputeNet(1000, null), 1000);
  assert.equal(C.crmComputeNet(1000, 0), 1000);
  assert.equal(C.crmComputeNet(null, 40), null);
});

// ---- dealExportRow ----

test("dealExportRow: matches the clients.db deals table column shape log_deal.py's --import-json expects", () => {
  const row = C.dealExportRow({
    id: "deal_abc", deal_type: "rental", property: "123 Example Rd", price: 3200,
    commission_gross: 1600, commission_net: 1400, cobroke_agent: "Jane Tan",
    cobroke_split_pct: 40, stage: "otp", otp_date: "2026-09-20", completion_date: null,
    notes: "keys pending", created_at: "2026-09-16T00:00:00Z",
  }, null);
  assert.deepEqual(Object.keys(row), [
    "id", "client_slug", "deal_type", "property_address", "price", "commission_gross",
    "commission_net", "cobroke_agent", "cobroke_split_pct", "stage", "otp_date",
    "completion_date", "notes", "created_at",
  ]);
  assert.equal(row.client_slug, null);           // Matchmaker never knows a clients.db slug
  assert.equal(row.property_address, "123 Example Rd");
  assert.equal(row.notes, "keys pending");
});

test("dealExportRow: a linked contact's name is folded into notes as a reconciliation tag", () => {
  const row = C.dealExportRow({ id: "d1", notes: "keys pending" }, "Jane Tan");
  assert.equal(row.notes, "keys pending [linked: Jane Tan]");
});

test("dealExportRow: no notes and no linked contact still returns null, not an empty string", () => {
  const row = C.dealExportRow({ id: "d1" }, null);
  assert.equal(row.notes, null);
});
