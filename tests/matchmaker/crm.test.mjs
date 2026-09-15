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
// CRM_UI's own top level initialiser calls crmBlankDealDraft() immediately, which
// calls todayISO() — defined outside this slice's span (near crmBtn/openCRM above it)
// — so it has to be injected the same way TODAY/Scoring are injected elsewhere.
// crmFindContactByPhone calls normPhone() — defined outside this slice's span (it is
// PHONE_SRC's own function, sliced separately above) — injected here the same way
// todayISO is, reusing the real implementation rather than a stub.
function makeCrmHelpers(todayISOFn, normPhoneFn) {
  return new Function(
    "todayISO", "normPhone",
    CRM_SRC + "\nreturn { addDaysISO, followUpQueueItems, dealDateOf, dealTotals, crmComputeNet, dealSplitDisplay, dealExportRow, crmMergedContacts, crmFindContactByPhone };"
  )(todayISOFn || (() => "2026-09-16"), normPhoneFn || PH.normPhone);
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

// ---- dealTotals — buckets by deal_date only, never completion_date/otp_date/created_at ----

test("dealTotals: sums gross/net for the current month and separately for the year to date, by deal_date", () => {
  const deals = [
    { commission_gross: 1000, commission_net: 900, deal_date: "2026-09-10", stage: "completed" },
    { commission_gross: 500, commission_net: 450, deal_date: "2026-09-16", stage: "completed" },
    { commission_gross: 2000, commission_net: 1800, deal_date: "2026-03-05", stage: "completed" }, // this year, not this month
    { commission_gross: 9999, commission_net: 9999, deal_date: "2025-12-01", stage: "completed" }, // last year — excluded from both
  ];
  const t = C.dealTotals(deals, "2026-09-16");
  assert.equal(t.monthGross, 1500);
  assert.equal(t.monthNet, 1350);
  assert.equal(t.ytdGross, 3500);
  assert.equal(t.ytdNet, 3150);
});

test("dealTotals: a deal that fell through earned no commission and is excluded entirely", () => {
  const deals = [
    { commission_gross: 5000, commission_net: 4500, deal_date: "2026-09-10", stage: "fell_through" },
    { commission_gross: 1000, commission_net: 900, deal_date: "2026-09-10", stage: "completed" },
  ];
  const t = C.dealTotals(deals, "2026-09-16");
  assert.equal(t.monthGross, 1000);
  assert.equal(t.ytdGross, 1000);
});

test("dealTotals: a deal date matching completion_date/otp_date/created_at is ignored — deal_date only counts", () => {
  const deals = [
    // completion_date is this month, but deal_date says last year — deal_date wins.
    { commission_gross: 100, commission_net: 100, completion_date: "2026-09-10", deal_date: "2025-01-01", stage: "completed" },
    // no deal_date at all — excluded even though otp_date/created_at would place it this month.
    { commission_gross: 200, commission_net: 200, otp_date: "2026-09-05", created_at: "2026-09-01T00:00:00Z", stage: "otp" },
  ];
  const t = C.dealTotals(deals, "2026-09-16");
  assert.equal(t.monthGross, 0);
  assert.equal(t.ytdGross, 0);
});

// ---- crmComputeNet — mirrors v_commission_attribution's COALESCE(split_pct, 50) rule ----

test("crmComputeNet: a named agent with no split recorded defaults to 50 percent, not 0", () => {
  assert.equal(C.crmComputeNet(1000, null, "Jane Tan"), 500);
  assert.equal(C.crmComputeNet(1000, "", "Jane Tan"), 500);
});

test("crmComputeNet: an explicit split percentage always wins over the 50 percent default", () => {
  assert.equal(C.crmComputeNet(1000, 40, "Jane Tan"), 600);
  assert.equal(C.crmComputeNet(1000, 0, "Jane Tan"), 1000);
});

test("crmComputeNet: no agent named means no split at all, regardless of a stray split value", () => {
  assert.equal(C.crmComputeNet(1000, null, null), 1000);
  assert.equal(C.crmComputeNet(1000, 40, null), 1000);
  assert.equal(C.crmComputeNet(1000, 40, ""), 1000);
});

test("crmComputeNet: no gross means no net, regardless of agent or split", () => {
  assert.equal(C.crmComputeNet(null, 40, "Jane Tan"), null);
});

// ---- dealSplitDisplay — the deals table's own visible "50 assumed" (not just the form) ----

test("dealSplitDisplay: a named agent with no split shows the 50 percent assumption in the table itself", () => {
  assert.equal(C.dealSplitDisplay({ cobroke_agent: "Jane Tan", cobroke_split_pct: null }), "50% (assumed)");
  assert.equal(C.dealSplitDisplay({ cobroke_agent: "Jane Tan", cobroke_split_pct: "" }), "50% (assumed)");
});

test("dealSplitDisplay: an explicit split percentage displays as typed, not the assumed default", () => {
  assert.equal(C.dealSplitDisplay({ cobroke_agent: "Jane Tan", cobroke_split_pct: 40 }), "40%");
  assert.equal(C.dealSplitDisplay({ cobroke_agent: "Jane Tan", cobroke_split_pct: 0 }), "0%");
});

test("dealSplitDisplay: no agent named shows nothing at all, not '0%' or an assumed default", () => {
  assert.equal(C.dealSplitDisplay({ cobroke_agent: null, cobroke_split_pct: null }), "");
  assert.equal(C.dealSplitDisplay({ cobroke_agent: "", cobroke_split_pct: 40 }), "");
  assert.equal(C.dealSplitDisplay(null), "");
});

// ---- dealExportRow ----

test("dealExportRow: matches the clients.db deals table column shape log_deal.py's --import-json expects, plus deal_date", () => {
  const row = C.dealExportRow({
    id: "deal_abc", deal_type: "rental", property: "123 Example Rd", price: 3200,
    commission_gross: 1600, commission_net: 1400, cobroke_agent: "Jane Tan",
    cobroke_split_pct: 40, stage: "otp", otp_date: "2026-09-20", completion_date: null,
    deal_date: "2026-09-16", notes: "keys pending", created_at: "2026-09-16T00:00:00Z",
  }, null);
  assert.deepEqual(Object.keys(row), [
    "id", "client_slug", "deal_type", "property_address", "price", "commission_gross",
    "commission_net", "cobroke_agent", "cobroke_split_pct", "stage", "otp_date",
    "completion_date", "deal_date", "notes", "created_at",
  ]);
  assert.equal(row.client_slug, null);           // Matchmaker never knows a clients.db slug
  assert.equal(row.property_address, "123 Example Rd");
  assert.equal(row.deal_date, "2026-09-16");
  assert.equal(row.notes, "keys pending");
});

test("dealExportRow: no deal_date on the deal itself exports as null, not undefined or dropped", () => {
  const row = C.dealExportRow({ id: "d1" }, null);
  assert.equal(row.deal_date, null);
  assert.ok("deal_date" in row);
});

test("dealExportRow: a linked contact's name is folded into notes as a reconciliation tag", () => {
  const row = C.dealExportRow({ id: "d1", notes: "keys pending" }, "Jane Tan");
  assert.equal(row.notes, "keys pending [linked: Jane Tan]");
});

test("dealExportRow: no notes and no linked contact still returns null, not an empty string", () => {
  const row = C.dealExportRow({ id: "d1" }, null);
  assert.equal(row.notes, null);
});

// =====================================================================
// crmMergedContacts — shared phone numbers (item 11): a landlord and a tenant on the
// same number is normal in this business and must render as two separate rows, never
// silently collapse into one. Fixture phone below is invented, not a real number.
// =====================================================================

function fakeKeyOf(s) {
  if (!s) return null;
  const p = (s.phone || "").replace(/[^0-9]/g, "");
  return p ? ("phone:" + p) : (s.id != null ? ((s.kind || "person") + ":" + s.id) : null);
}

test("crmMergedContacts: a landlord and a tenant sharing one phone number both get their own row", () => {
  const landlords = [{ id: "L1", name: "Placeholder Landlord", phone: "91110000", address: "1 Placeholder Rd" }];
  const tenants = [{ id: "T1", name: "Placeholder Tenant", phone: "91110000", preferred_location: "somewhere" }];
  const rows = C.crmMergedContacts([], tenants, landlords, [], fakeKeyOf);
  assert.equal(rows.length, 2);
  const kinds = rows.map(r => r.kind).sort();
  assert.deepEqual(kinds, ["landlord", "tenant"]);
  const names = rows.map(r => r.name).sort();
  assert.deepEqual(names, ["Placeholder Landlord", "Placeholder Tenant"]);
  // Both rows resolve to the same underlying CRM entity key — that part is unchanged
  // and out of scope for this fix, only the row itself must not be dropped.
  assert.equal(rows[0].key, rows[1].key);
});

test("crmMergedContacts: the same person appearing in DATA and as a touched CRM entity still merges to one row", () => {
  const entities = [{ key: "phone:91110001", kind: "tenant", ref_id: "T2", name: "Placeholder Person", phone: "91110001", stage: "contacted" }];
  const tenants = [{ id: "T2", name: "Placeholder Person", phone: "91110001", preferred_location: "somewhere else" }];
  const rows = C.crmMergedContacts(entities, tenants, [], [], fakeKeyOf);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].stage, "contacted");
});

// =====================================================================
// crmFindContactByPhone — quick add's dedupe check (item 1 of the second review
// pass): a phone number already on file, in any kind, must be found rather than
// let quick add create a second row for it.
// =====================================================================

test("crmFindContactByPhone: finds an existing contact by phone regardless of kind", () => {
  const merged = C.crmMergedContacts([], [{ id: "T1", name: "Placeholder Tenant", phone: "91116666", preferred_location: "x" }], [], [], fakeKeyOf);
  const found = C.crmFindContactByPhone("6591116666", merged);
  assert.ok(found);
  assert.equal(found.name, "Placeholder Tenant");
  assert.equal(found.kind, "tenant");
});

test("crmFindContactByPhone: no match returns null rather than throwing", () => {
  const merged = C.crmMergedContacts([], [{ id: "T1", name: "Placeholder Tenant", phone: "91116666", preferred_location: "x" }], [], [], fakeKeyOf);
  assert.equal(C.crmFindContactByPhone("6599990000", merged), null);
  assert.equal(C.crmFindContactByPhone("", merged), null);
  assert.equal(C.crmFindContactByPhone(null, merged), null);
});

test("crmFindContactByPhone: matches a landlord row just as well as a tenant row on the same lookup", () => {
  const merged = C.crmMergedContacts([], [], [{ id: "L1", name: "Placeholder Landlord", phone: "91117777", address: "x" }], [], fakeKeyOf);
  const found = C.crmFindContactByPhone("6591117777", merged);
  assert.equal(found.kind, "landlord");
  assert.equal(found.name, "Placeholder Landlord");
});

// =====================================================================
// adopt() — a snapshot with no "deals" key at all (item 3: an older backend, or a
// deploy.sh rollback) must keep local deals rather than wipe them.
// =====================================================================
const ADOPT_SRC = slice("function adopt(d) {", "// (item 3) Every op type");
function makeAdopt(initialS) {
  return new Function(
    "S0", "queue", "replay",
    "let S = S0;\n" + ADOPT_SRC + "\nreturn { adopt, getDeals: () => S.deals };"
  )(initialS, [], () => {});
}

test("adopt: a snapshot with no deals key keeps the local deals untouched", () => {
  const localDeals = { d1: { id: "d1", property: "Placeholder property" } };
  const A = makeAdopt({ entities: {}, notes: [], tasks: [], match: {}, activity: [], deals: localDeals });
  A.adopt({ entities: [], notes: [], tasks: [], match: [], activity: [] });
  assert.deepEqual(A.getDeals(), localDeals);
});

test("adopt: a snapshot WITH a deals key (even an empty array) replaces the local deals", () => {
  const localDeals = { d1: { id: "d1", property: "Placeholder property" } };
  const A = makeAdopt({ entities: {}, notes: [], tasks: [], match: {}, activity: [], deals: localDeals });
  A.adopt({ entities: [], notes: [], tasks: [], match: [], activity: [], deals: [] });
  assert.deepEqual(A.getDeals(), {});
});

test("adopt: a snapshot with a real deals array is indexed by id as usual", () => {
  const A = makeAdopt({ entities: {}, notes: [], tasks: [], match: {}, activity: [], deals: {} });
  A.adopt({ entities: [], notes: [], tasks: [], match: [], activity: [], deals: [{ id: "d9", property: "x" }] });
  assert.deepEqual(A.getDeals(), { d9: { id: "d9", property: "x" } });
});
