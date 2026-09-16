// Unit tests for scripts/matchmaker/deploy/lib/crm-validate.js — the deal op
// validation api/crm.js relies on, factored into its own module precisely so it can
// be tested without a Postgres database.
// Run: node --test tests/matchmaker/crm-validate.test.mjs   (build.py runs it too)

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  str, date, bool, num, validateDealFields, DEAL_TYPES, DEAL_STAGES, KINDS,
  validateDispatchFields, DISPATCH_STATUSES, nextDispatchStatus, validateAmbiguousFields,
} from "../../scripts/matchmaker/deploy/lib/crm-validate.js";

test("str: trims, caps length, blank/whitespace-only becomes null", () => {
  assert.equal(str("  hello  ", 20), "hello");
  assert.equal(str("", 20), null);
  assert.equal(str("   ", 20), null);
  assert.equal(str(null, 20), null);
  assert.equal(str("x".repeat(30), 10), "x".repeat(10));
});

test("date: accepts a real calendar date, rejects malformed shapes and non-existent days", () => {
  assert.equal(date("2026-09-16"), "2026-09-16");
  assert.equal(date("2026-13-01"), null);   // month 13
  assert.equal(date("2026-02-30"), null);   // Feb 30 does not exist
  assert.equal(date("not-a-date"), null);
  assert.equal(date(""), null);
  assert.equal(date(null), null);
});

test("bool: true/'true'/1 are truthy, everything else is false", () => {
  assert.equal(bool(true), true);
  assert.equal(bool("true"), true);
  assert.equal(bool(1), true);
  assert.equal(bool(false), false);
  assert.equal(bool("false"), false);
  assert.equal(bool(0), false);
  assert.equal(bool(undefined), false);
});

test("num: rejects negative, non-finite and over-cap values, rounds to cents", () => {
  assert.equal(num("1500.456", 100000), 1500.46);
  assert.equal(num(-5, 100000), null);
  assert.equal(num("abc", 100000), null);
  assert.equal(num(Infinity, 100000), null);
  assert.equal(num(200, 100), null);        // over the max
  assert.equal(num(null, 100), null);
  assert.equal(num("", 100), null);
  assert.equal(num(0, 100), 0);
});

test("KINDS includes buyer (quick add's only source for that bucket)", () => {
  assert.ok(KINDS.has("buyer"));
  assert.ok(KINDS.has("tenant"));
  assert.ok(KINDS.has("landlord"));
  assert.ok(KINDS.has("sale"));
});

// =====================================================================
// validateDealFields — the "deal" op's field-by-field validation
// =====================================================================

test("validateDealFields: a well formed deal passes every field through normalised", () => {
  const d = validateDealFields({
    id: "d_abc123", deal_type: "rental", property: "  123 Example Rd #05-01  ",
    price: "3200", commission_gross: "1600.006", commission_net: 1400,
    cobroke_agent: "Jane Tan", cobroke_split_pct: "40", stage: "otp",
    otp_date: "2026-09-20", completion_date: "2026-10-15", deal_date: "2026-09-16",
    notes: "  keys handover pending  ",
  });
  assert.deepEqual(d, {
    id: "d_abc123", deal_type: "rental", property: "123 Example Rd #05-01",
    price: 3200, commission_gross: 1600.01, commission_net: 1400,
    cobroke_agent: "Jane Tan", cobroke_split_pct: 40, stage: "otp",
    otp_date: "2026-09-20", completion_date: "2026-10-15", deal_date: "2026-09-16",
    notes: "keys handover pending",
  });
});

test("validateDealFields: deal_date validates like every other date, and notes cap at 2000 characters", () => {
  const good = validateDealFields({ id: "d1", deal_date: "2026-09-16" });
  assert.equal(good.deal_date, "2026-09-16");
  const bad = validateDealFields({ id: "d1", deal_date: "2026-13-40" });
  assert.equal(bad.deal_date, null);
  const long = validateDealFields({ id: "d1", notes: "x".repeat(2500) });
  assert.equal(long.notes.length, 2000);
});

test("validateDealFields: no id at all means no usable deal — returns null", () => {
  assert.equal(validateDealFields({ deal_type: "rental", property: "x" }), null);
  assert.equal(validateDealFields({ id: "" }), null);
  assert.equal(validateDealFields({ id: "   " }), null);
});

test("validateDealFields: an invalid deal_type/stage falls back rather than reaching Postgres unchecked", () => {
  const d = validateDealFields({ id: "d1", deal_type: "resale-condo", stage: "won" });
  assert.equal(d.deal_type, null);       // not in DEAL_TYPES -> null, never a raw client string
  assert.equal(d.stage, "agreed");       // not in DEAL_STAGES -> the documented default
});

test("validateDealFields: every real deal_type and stage round-trips", () => {
  for (const t of DEAL_TYPES) {
    assert.equal(validateDealFields({ id: "d1", deal_type: t }).deal_type, t);
  }
  for (const s of DEAL_STAGES) {
    assert.equal(validateDealFields({ id: "d1", stage: s }).stage, s);
  }
});

test("validateDealFields: malformed dates and out-of-range money never reach the caller as-is", () => {
  const d = validateDealFields({
    id: "d1", otp_date: "2026-99-99", completion_date: "not a date",
    price: -100, commission_gross: "abc", cobroke_split_pct: "150",
  });
  assert.equal(d.otp_date, null);
  assert.equal(d.completion_date, null);
  assert.equal(d.price, null);
  assert.equal(d.commission_gross, null);
  assert.equal(d.cobroke_split_pct, null);   // 150 > the 100 percent cap
});

// =====================================================================
// validateDispatchFields — the "dispatch" op's field by field validation, used
// by both the app's Mark queued action and crm_pull.py marking a row pulled.
// =====================================================================

test("validateDispatchFields: a well formed dispatch row passes every field through normalised", () => {
  const d = validateDispatchFields({
    id: "dispatch_L1_T1", tenant_id: "T1", listing_id: "L1", jid: "6591234567@s.whatsapp.net",
    phone: "  91234567  ", text: "  Hi, this unit might suit you  ", viewing_slot: "Sat 2pm",
    status: "pulled", device: "winfreds mac",
  });
  assert.deepEqual(d, {
    id: "dispatch_L1_T1", tenant_id: "T1", listing_id: "L1", jid: "6591234567@s.whatsapp.net",
    phone: "91234567", text: "Hi, this unit might suit you", viewing_slot: "Sat 2pm",
    status: "pulled", device: "winfreds mac", sent_confirmed: false,
  });
});

test("validateDispatchFields: sent_confirmed defaults false, true only for true/'true'/1 (PR #132 sixth review)", () => {
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi" }).sent_confirmed, false);
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi", sent_confirmed: true }).sent_confirmed, true);
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi", sent_confirmed: "true" }).sent_confirmed, true);
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi", sent_confirmed: "yes" }).sent_confirmed, false);
});

test("validateDispatchFields: id, tenant_id and text are all required — missing any means no usable row", () => {
  assert.equal(validateDispatchFields({ tenant_id: "T1", text: "hi" }), null);          // no id
  assert.equal(validateDispatchFields({ id: "d1", text: "hi" }), null);                 // no tenant_id
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1" }), null);            // no text
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "   " }), null); // blank text
  assert.equal(validateDispatchFields({ id: "", tenant_id: "T1", text: "hi" }), null);
});

test("validateDispatchFields: status defaults to queued and falls back on an unknown value", () => {
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi" }).status, "queued");
  assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi", status: "sending now" }).status, "queued");
  for (const s of DISPATCH_STATUSES) {
    assert.equal(validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi", status: s }).status, s);
  }
});

test("validateDispatchFields: text caps at 2000 characters, same cap as a deal's notes", () => {
  const d = validateDispatchFields({ id: "d1", tenant_id: "T1", text: "x".repeat(2500) });
  assert.equal(d.text.length, 2000);
});

test("validateDispatchFields: optional fields (listing_id, jid, phone, viewing_slot, device) default to null when absent", () => {
  const d = validateDispatchFields({ id: "d1", tenant_id: "T1", text: "hi" });
  assert.equal(d.listing_id, null);
  assert.equal(d.jid, null);
  assert.equal(d.phone, null);
  assert.equal(d.viewing_slot, null);
  assert.equal(d.device, null);
});

// =====================================================================
// nextDispatchStatus — the "dispatch" op's status transition rule, used by
// its upsert in api/crm.js: queued moves anywhere, pulled only ever moves on
// to sent (never back to queued), cancelled only ever moves back to queued
// (a genuine requeue of the same pair), and sent is terminal.
// =====================================================================

test("nextDispatchStatus: a row that does not exist yet always takes the incoming status", () => {
  assert.equal(nextDispatchStatus(null, "queued"), "queued");
  assert.equal(nextDispatchStatus(null, "pulled"), "pulled");
});

test("nextDispatchStatus: a queued row can move anywhere, including staying queued", () => {
  assert.equal(nextDispatchStatus("queued", "pulled"), "pulled");
  assert.equal(nextDispatchStatus("queued", "cancelled"), "cancelled");
  assert.equal(nextDispatchStatus("queued", "queued"), "queued");
});

test("nextDispatchStatus: a pulled row never regresses to queued through this op", () => {
  assert.equal(nextDispatchStatus("pulled", "queued"), "pulled");
  assert.equal(nextDispatchStatus("pulled", "cancelled"), "pulled", "dispatch_cancel is the op for that, not this one");
});

test("nextDispatchStatus: a pulled row moves on to sent, its only legal next status here", () => {
  assert.equal(nextDispatchStatus("pulled", "sent"), "sent");
});

test("nextDispatchStatus: a sent row is terminal — no incoming status changes it", () => {
  assert.equal(nextDispatchStatus("sent", "queued"), "sent");
  assert.equal(nextDispatchStatus("sent", "pulled"), "sent");
  assert.equal(nextDispatchStatus("sent", "cancelled"), "sent");
});

test("nextDispatchStatus: a cancelled row CAN be re queued through the same id (a defensive allowance — the app itself always writes a brand new id instead, see writeDispatchRow)", () => {
  assert.equal(nextDispatchStatus("cancelled", "queued"), "queued");
});

test("nextDispatchStatus: a cancelled row cannot jump straight to pulled or sent, only back to queued first", () => {
  assert.equal(nextDispatchStatus("cancelled", "pulled"), "cancelled");
  assert.equal(nextDispatchStatus("cancelled", "sent"), "cancelled");
});

// One full table, every (currentStatus, incomingStatus) pair including the
// brand new row case (currentStatus null) — PR #132 rework: the per attempt
// dispatch id design (app.js writeDispatchRow) means a queued or pulled row
// almost never sees a cancelled or queued incoming write for the SAME id in
// practice any more, but the server side rule still has to hold for every
// combination on its own, since nothing here can see or trust what the
// client meant to do.
test("nextDispatchStatus: full transition table, every current x incoming pair", () => {
  const STATUSES = ["queued", "pulled", "sent", "cancelled"];
  const TABLE = {
    // currentStatus: { incomingStatus: expected }
    " new": { queued: "queued", pulled: "pulled", sent: "sent", cancelled: "cancelled" },
    queued:      { queued: "queued", pulled: "pulled", sent: "sent", cancelled: "cancelled" },
    pulled:      { queued: "pulled", pulled: "pulled", sent: "sent", cancelled: "pulled" },
    sent:        { queued: "sent",   pulled: "sent",   sent: "sent", cancelled: "sent" },
    cancelled:   { queued: "queued", pulled: "cancelled", sent: "cancelled", cancelled: "cancelled" },
  };
  for (const current of [" new", ...STATUSES]) {
    for (const incoming of STATUSES) {
      const currentArg = current === " new" ? null : current;
      const got = nextDispatchStatus(currentArg, incoming);
      const want = TABLE[current][incoming];
      assert.equal(got, want,
        `nextDispatchStatus(${JSON.stringify(currentArg)}, ${JSON.stringify(incoming)}) should be ${want}, got ${got}`);
    }
  }
});

// =====================================================================
// dispatch id shape — "dispatch_<listing_id>_<tenant_id>_<created ms>", one
// brand new id per attempt (app.js writeDispatchRow, PR #132 rework). This
// module never generates or parses that shape itself (id is an opaque str()
// here), but it is the field every dedupe and status transition check above
// keys off, so its round trip through validateDispatchFields has to be exact
// — nothing here may trim, reflow or otherwise alter it.
// =====================================================================
const PER_ATTEMPT_ID_RE = /^dispatch_[^_]+_[^_]+_\d+$/;

test("validateDispatchFields: a realistic per attempt id round trips untouched", () => {
  const id = "dispatch_L42_T1007_" + Date.now();
  assert.ok(PER_ATTEMPT_ID_RE.test(id), "fixture id must itself match the real shape: " + id);
  const d = validateDispatchFields({ id, tenant_id: "T1007", text: "hi" });
  assert.equal(d.id, id);
});

test("validateDispatchFields: two attempts for the same pair produce two different ids, both usable", () => {
  const first = validateDispatchFields({ id: "dispatch_L1_T1_1000", tenant_id: "T1", text: "first attempt" });
  const second = validateDispatchFields({ id: "dispatch_L1_T1_2000", tenant_id: "T1", text: "second attempt" });
  assert.notEqual(first.id, second.id);
  assert.ok(PER_ATTEMPT_ID_RE.test(first.id));
  assert.ok(PER_ATTEMPT_ID_RE.test(second.id));
});

test("validateDispatchFields: id is capped at 60 characters like any other str() field — stays well clear of that cap for realistic listing/tenant ids", () => {
  const id = "dispatch_L1_T1_" + Date.now();
  assert.ok(id.length < 60, "a realistic per attempt id must sit well under the 60 char cap: " + id.length);
  const d = validateDispatchFields({ id, tenant_id: "T1", text: "hi" });
  assert.equal(d.id, id, "a realistic id must never be truncated");
  // Documents the boundary rather than asserting it should be higher: an
  // oversized listing or tenant id could in principle push the timestamp
  // suffix past 60 characters and truncate away the very thing that makes
  // each attempt unique. Realistic ids from export_data.py are short slugs
  // (e.g. "L42", "T1007"), nowhere near this.
  const oversized = "dispatch_" + "x".repeat(80) + "_T1_" + Date.now();
  const truncated = validateDispatchFields({ id: oversized, tenant_id: "T1", text: "hi" });
  assert.equal(truncated.id.length, 60);
  assert.notEqual(truncated.id, oversized);
});

test("validateDispatchFields: an empty or whitespace only id is rejected outright, same as no id", () => {
  assert.equal(validateDispatchFields({ id: "", tenant_id: "T1", text: "hi" }), null);
  assert.equal(validateDispatchFields({ id: "   ", tenant_id: "T1", text: "hi" }), null);
});

// =====================================================================
// validateAmbiguousFields — the "ambiguous" op (PR #132 fifth review round):
// a pulled row crm_pull.py could not confidently resolve from the archive.
// Only ever carries an id; api/crm.js's own guard (status = 'pulled', and
// only stamps ambiguous_since the first time) is server side and out of
// reach of this module's own pure tests, same split as validateDispatchFields
// above vs nextDispatchStatus's own transition rule.
// =====================================================================
test("validateAmbiguousFields: a realistic id round trips untouched", () => {
  const id = "dispatch_L1_T1_" + Date.now();
  assert.deepEqual(validateAmbiguousFields({ id }), { id });
});

test("validateAmbiguousFields: missing, empty or whitespace only id is rejected, same as validateDispatchFields", () => {
  assert.equal(validateAmbiguousFields({}), null);
  assert.equal(validateAmbiguousFields({ id: "" }), null);
  assert.equal(validateAmbiguousFields({ id: "   " }), null);
  assert.equal(validateAmbiguousFields(null), null);
});

test("validateAmbiguousFields: id is capped at 60 characters like every other op's id", () => {
  const oversized = "dispatch_" + "x".repeat(80);
  const got = validateAmbiguousFields({ id: oversized });
  assert.equal(got.id.length, 60);
  assert.notEqual(got.id, oversized);
});

test("validateAmbiguousFields: ignores every other field on the op — only id is ever read", () => {
  const got = validateAmbiguousFields({ id: "d1", status: "sent", tenant_id: "T1", text: "smuggled" });
  assert.deepEqual(got, { id: "d1" }, "the ambiguous op must never be able to smuggle a status or field change through");
});
