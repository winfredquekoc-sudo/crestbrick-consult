// Shared validation for the Matchmaker CRM wire protocol — pulled out of api/crm.js
// so these pure functions can be unit tested without a database (see
// tests/matchmaker/crm-validate.test.mjs). Nothing here touches pg or the request/
// response objects; it only turns whatever a client sent into either a safe value
// or null.
export const STAGES = new Set([
  "new", "contacted", "qualified", "viewing_set", "viewed",
  "offer", "closed_won", "closed_lost", "dormant",
]);
// "buyer" has no source in the rental/sale databases build.py reads — it only ever
// appears on an entity created through the CRM tab's quick add. "listing" stays for
// completeness even though no crmBtn call site uses it today.
export const KINDS = new Set(["tenant", "landlord", "listing", "sale", "buyer", "person"]);

export const DEAL_TYPES = new Set(["rental", "sale"]);
export const DEAL_STAGES = new Set(["agreed", "otp", "signed", "completed", "fell_through"]);
// A deal that fell through earned no commission — excluded from month/YTD totals
// on both the client and in anything that ever sums crm_deal server side.
export const DEAL_CLOSED_STAGES = new Set(["completed", "fell_through"]);

export const str = (v, max) => {
  if (v == null) return null;
  const s = String(v).trim();
  return s ? s.slice(0, max) : null;
};
// Dates arrive as YYYY-MM-DD from the client. Anything else becomes null rather than
// reaching Postgres, where a malformed date aborts the whole transaction — and since the
// batch is transactional, one bad date would discard every good write sent with it.
// The shape check alone is not enough: "2026-13-99" matches the pattern and is still
// rejected by Postgres, so the calendar itself has to agree the day exists.
export const date = (v) => {
  const s = String(v || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const d = new Date(s + "T00:00:00Z");
  return !isNaN(d) && d.toISOString().slice(0, 10) === s ? s : null;
};
export const bool = (v) => v === true || v === "true" || v === 1;
// Money and percentages. Same "reject rather than let Postgres choke on it" rule as
// date() — NaN/Infinity/negative/absurd values become null, never reach a query.
// Rounded to cents so a float typed in a number input cannot smuggle in
// 0.1+0.2-style binary rounding dust.
export const num = (v, max) => {
  if (v == null || v === "") return null;
  const n = Number(v);
  if (!isFinite(n) || n < 0) return null;
  if (max != null && n > max) return null;
  return Math.round(n * 100) / 100;
};

// Validates and normalises one incoming "deal" op's fields. Returns null when there
// is no usable id (a deal with no id cannot be upserted — the client always generates
// one client side before the op is queued, same as a note/task tempId).
export function validateDealFields(o) {
  const id = str(o && o.id, 60);
  if (!id) return null;
  return {
    id,
    deal_type: DEAL_TYPES.has(o.deal_type) ? o.deal_type : null,
    property: str(o.property, 300),
    price: num(o.price, 500_000_000),
    commission_gross: num(o.commission_gross, 50_000_000),
    commission_net: num(o.commission_net, 50_000_000),
    cobroke_agent: str(o.cobroke_agent, 200),
    cobroke_split_pct: num(o.cobroke_split_pct, 100),
    stage: DEAL_STAGES.has(o.stage) ? o.stage : "agreed",
    otp_date: date(o.otp_date),
    completion_date: date(o.completion_date),
    notes: str(o.notes, 4000),
  };
}
