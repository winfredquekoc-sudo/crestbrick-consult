// Unit tests for scripts/matchmaker/deploy/lib/crm-validate.js — the deal op
// validation api/crm.js relies on, factored into its own module precisely so it can
// be tested without a Postgres database.
// Run: node --test tests/matchmaker/crm-validate.test.mjs   (build.py runs it too)

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  str, date, bool, num, validateDealFields, DEAL_TYPES, DEAL_STAGES, KINDS,
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
    otp_date: "2026-09-20", completion_date: "2026-10-15", notes: "  keys handover pending  ",
  });
  assert.deepEqual(d, {
    id: "d_abc123", deal_type: "rental", property: "123 Example Rd #05-01",
    price: 3200, commission_gross: 1600.01, commission_net: 1400,
    cobroke_agent: "Jane Tan", cobroke_split_pct: 40, stage: "otp",
    otp_date: "2026-09-20", completion_date: "2026-10-15", notes: "keys handover pending",
  });
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
