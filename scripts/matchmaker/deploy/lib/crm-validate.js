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
    // What month/year to date totals bucket by (dealTotals() in app.js) — deliberately
    // separate from otp_date/completion_date, which track the property transaction.
    deal_date: date(o.deal_date),
    notes: str(o.notes, 2000),
  };
}

export const DISPATCH_STATUSES = new Set(["queued", "pulled", "sent", "cancelled"]);

// The "dispatch" op's status transition rule, used by its upsert in api/crm.js.
// currentStatus is null for a row that does not exist yet (a first insert),
// which always takes the incoming status as is. Otherwise:
//   queued    -> anything (the normal flow: the app writes queued, crm_pull.py
//                moves it to pulled once it has appended the draft)
//   pulled    -> sent only (crm_pull.py's own terminal state marking, once the
//                real send is confirmed via the queue's own .done-* archive or
//                a long enough time in its append ledger); pulled can never
//                regress to queued through this op
//   cancelled -> queued only (requeueing the SAME pair after a refusal or an
//                unqueue — Mark Queued in the app writes this op again with the
//                same id on purpose, so a fresh id is not required here, unlike
//                an earlier draft of this rule assumed)
//   sent      -> sent, always — terminal, no further write through this op can
//                change it
// dispatch_cancel is a separate op with its own guard in api/crm.js and does
// not go through this function — a pulled row must still be cancellable
// (crm_pull.py's own append failed recovery, and cancelling before the 08:00
// send), which this function does not need to allow since that path never
// calls it.
export function nextDispatchStatus(currentStatus, incomingStatus) {
  if (currentStatus == null) return incomingStatus;
  if (currentStatus === "sent") return "sent";
  if (currentStatus === "queued") return incomingStatus;
  if (currentStatus === "pulled") return incomingStatus === "sent" ? "sent" : currentStatus;
  if (currentStatus === "cancelled") return incomingStatus === "queued" ? "queued" : currentStatus;
  return currentStatus;
}

// Validates and normalises one incoming "dispatch" op's fields — a row destined
// for crm_dispatch. Required: id, tenant_id and text (the drafted message); with
// no draft text or no tenant to send it to there is nothing usable to queue.
// Everything else is context the app or crm_pull.py may or may not have on
// hand, so it is optional. Returns null on a missing required field, same
// null on unusable contract as validateDealFields above.
export function validateDispatchFields(o) {
  const id = str(o && o.id, 60);
  const tenant_id = str(o && o.tenant_id, 80);
  const text = str(o && o.text, 2000);
  if (!id || !tenant_id || !text) return null;
  return {
    id,
    tenant_id,
    listing_id: str(o.listing_id, 80),
    jid: str(o.jid, 80),
    phone: str(o.phone, 40),
    text,
    viewing_slot: str(o.viewing_slot, 200),
    status: DISPATCH_STATUSES.has(o.status) ? o.status : "queued",
    device: str(o.device, 80),
  };
}
