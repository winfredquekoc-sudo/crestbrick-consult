/* Crestbrick Matchmaker — app.js
 * DOM/rendering/interaction layer. Scoring lives in scoring.js (global
 * `Scoring`, bridged via globalThis — see that file's tail comment). Data
 * comes from the DATA global set by the earlier <script> tag. Every DATA
 * field beyond the v1 shape (units, available_from, fixed_viewing, last_wa,
 * work_anchor, is_cobroke, ...) is optional — null guard on read, never
 * assume presence. Same rule for PASS 2 fields (delta, health, area_demand,
 * photos, listing_url, dup_of, dup_group, missing, lifecycle, build_history,
 * busy_blocks, ...) — every read below is guarded and every new panel hides
 * itself cleanly when its field is absent, so this file runs unmodified
 * against a v1 payload.
 *
 * PASS 2 section map (search these headers): error diagnostics (69) · core
 * state · formatting · co-broke/whole-unit/lifecycle · draft text · links ·
 * localStorage model (marks/overrides/scratch) · PASS 2 prefs/history/
 * offers/backups · scoring integration · matching engine · filters ·
 * row rendering · top level render · worklist+triage · listing rail+panel ·
 * batch viewing builder · tenant rail+panel · whole unit view · dispatch
 * drawer · decline/snooze/quick add · command palette (51) · gallery (34) ·
 * idle lock (20) · stats tab (13/27/45/69) · init.
 */

// ===================== error diagnostics ring buffer (69) =====================
// Registered before anything else in the file so init time errors get caught
// too. Best effort only — never throws itself, never blocks the real console.
const ERR_KEY = "cbk_errors", ERR_CAP = 50;
function pushErrorEntry(entry) {
  try {
    const raw = localStorage.getItem(ERR_KEY);
    let arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) arr = [];   // a non array under this key would make every push below throw, forever
    arr.push(entry);
    while (arr.length > ERR_CAP) arr.shift();
    localStorage.setItem(ERR_KEY, JSON.stringify(arr));
  } catch (e) { /* storage unavailable/full — diagnostics are best effort only */ }
}
window.onerror = function (msg, src, line, col, err) {
  pushErrorEntry({ ts: Date.now(), kind: "error", msg: String(msg), src: src || "", line: line || 0, col: col || 0, stack: (err && err.stack) ? String(err.stack).slice(0, 600) : "" });
  return false; // still let the browser's own console log it
};
window.addEventListener("unhandledrejection", (e) => {
  const r = e && e.reason;
  pushErrorEntry({ ts: Date.now(), kind: "rejection", msg: (r && r.message) ? String(r.message) : String(r), src: "", line: 0, col: 0, stack: (r && r.stack) ? String(r.stack).slice(0, 600) : "" });
});

// ===================== DOM helpers =====================
const $ = s => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };

// ===================== HTML escaping (single choke point) =====================
// This whole file builds DOM by string concatenation into innerHTML, and much
// of what it interpolates is text somebody else typed: last_wa.snippet is a
// REAL WhatsApp message from a stranger, req_raw is whatever a landlord wrote,
// and names/notes/imported state all round trip through localStorage. Every
// data derived value interpolated into HTML must go through esc().
// Draft text is escaped only at the HTML boundary, never inside the draft
// builders, so the clipboard and wa.me copies stay plain readable text.
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(v) { return v == null ? "" : String(v).replace(/[&<>"']/g, c => ESC_MAP[c]); }
// For anything landing in href/src. Allows only schemes this app actually
// uses, so a poisoned photo/listing URL cannot smuggle in javascript: or data:.
function escUrl(v) {
  const s = (v == null ? "" : String(v)).trim();
  if (!s) return "";
  // s[1] !== "/" excludes protocol relative URLs ("//evil.com/x.png") — the
  // browser resolves those as a full off-site request under the page's own
  // scheme, which a single leading slash check alone would wrongly admit.
  if (/^(?:https?:|tel:|mailto:|obsidian:)/i.test(s) || (s[0] === "/" && s[1] !== "/") || s[0] === "#") return esc(s);
  return "";
}

// ===================== modal focus manager (cycle 8 — a11y) =====================
// Single choke point for every overlay this app opens: .modal-wrap (device
// name, snooze, viewing booked/pack, quick add tenant, bulk action, decline,
// try instead, reconfirm, shortlist), .drawer-wrap (dispatch, snoozed list),
// .palette-wrap (command palette), .lightbox-wrap, and .idle-overlay — 15
// openers in total, all wired through mountOverlay() below. Before this
// existed none of them touched focus at all: opening one left focus wherever
// it already was on the BACKGROUND page, Tab kept cycling through the page
// underneath a fully opaque overlay, and nothing ever returned focus
// anywhere on close — however that close happened (Cancel, Confirm, backdrop
// click; only the palette had its own bespoke Escape handler, replaced here,
// and nothing else responded to Escape at all).
//
// Each opener's call site changes by exactly one line — mountOverlay(wrap,
// opts) replaces that opener's own `document.body.appendChild(wrap)` — and
// from that one call gets:
//   - role=dialog + aria-modal=true, labelled from the dialog's own h2/h3
//     (auto detected) or opts.label when it has none (palette, lightbox,
//     idle lock all use a plain div for their visual title, not a real
//     heading tag);
//   - focus moved to a sensible first control after mount — "first
//     focusable descendant" by default, unless opts.initialFocus overrides
//     it. Only one opener needs the override: openDeclineModal's first
//     controls are the reason buttons, and clicking any one of them commits
//     an irreversible mark with no separate confirm step — exactly the
//     "destructive button" case the fix asked NOT to autofocus, so it points
//     initialFocus at Cancel instead;
//   - Tab/Shift+Tab trapped within wrap for as long as wrap stays in the
//     DOM;
//   - Escape wired to close, UNLESS opts.closeOnEscape:false — idle lock is
//     the one opener that passes this: it is a privacy screen, not a
//     dismissible dialog, and must only close via the 4 digit code;
//   - focus restored to whatever had it before the overlay opened (the
//     "opener"), on whichever close path actually fires.
// Attributes and the Tab-trap listener live on `wrap` ITSELF, never a
// descendant, because openLightbox rebuilds its own innerHTML wholesale on
// every prev/next click (see its own paint() comment) — anything hung off a
// child would be destroyed the moment the user pages to the next photo.
//
// Close/cleanup fires no matter which of a modal's several close paths runs
// (Cancel, Confirm, backdrop click, Escape) because every opener already
// closes itself with `wrap.remove()` somewhere — this monkey patches THAT
// ONE element's own .remove() the moment it mounts, so no existing
// Cancel/Confirm/backdrop-click line anywhere in this file needs to change.
const FOCUSABLE_SEL = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
let OVERLAY_SEQ = 0;
// How many overlays are currently open — lets the background j/k/etc triage
// shortcuts and the cmd+K palette shortcut both stay out of the way while
// any modal/drawer is open, instead of firing on the page underneath it
// purely because a click/keydown bubbles to `document` regardless of which
// element on screen actually has focus (see onKeydown / the cmd+K listener).
let OPEN_OVERLAY_COUNT = 0;
// getClientRects().length is a reliable "is this actually rendered" check —
// offsetParent is NOT: it reports null for position:fixed elements in every
// engine this app runs on, and every overlay wrap here IS position:fixed, so
// offsetParent would misreport every single descendant as hidden.
function isRenderedEl(e) { return !!(e.getClientRects().length); }
function overlayFocusable(wrap) { return [...wrap.querySelectorAll(FOCUSABLE_SEL)].filter(isRenderedEl); }
function mountOverlay(wrap, opts) {
  opts = opts || {};
  const opener = (document.activeElement && document.activeElement !== document.body) ? document.activeElement : null;
  wrap.setAttribute("role", opts.role || "dialog");
  wrap.setAttribute("aria-modal", "true");
  const heading = wrap.querySelector("h2, h3");
  if (heading) {
    if (!heading.id) heading.id = "ovtitle" + (++OVERLAY_SEQ);
    wrap.setAttribute("aria-labelledby", heading.id);
  } else if (opts.label) {
    wrap.setAttribute("aria-label", opts.label);
  }
  document.body.appendChild(wrap);
  OPEN_OVERLAY_COUNT++;

  const closeOnEscape = opts.closeOnEscape !== false;
  function onOverlayKeydown(e) {
    if (e.key === "Tab") {
      const items = overlayFocusable(wrap);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      const idx = items.indexOf(document.activeElement);
      if (e.shiftKey) { if (idx <= 0) { e.preventDefault(); last.focus(); } }
      else if (idx === -1 || idx === items.length - 1) { e.preventDefault(); first.focus(); }
    } else if (e.key === "Escape" && closeOnEscape) {
      e.preventDefault();
      (opts.onEscape || (() => wrap.remove()))();
    }
  }
  wrap.addEventListener("keydown", onOverlayKeydown);

  const realRemove = wrap.remove.bind(wrap);
  // Idempotent: several openers wire more than one close path to the same wrap
  // (Cancel, Confirm, backdrop click, Escape, and a few that also render()
  // afterwards), so a second .remove() on an already closed overlay is one new
  // close path away at any time. Without this guard that second call decrements
  // OPEN_OVERLAY_COUNT again, and since the count is what suppresses the
  // background j/k triage keys and cmd+K while anything is open, an
  // over-decrement leaves it stuck above zero once everything is shut — the
  // keyboard shortcuts then silently stop working for the rest of the session
  // with nothing on screen to explain why. Math.max(0, ...) below only stops it
  // going negative, which is not the same protection.
  let closed = false;
  wrap.remove = () => {
    if (closed) return;
    closed = true;
    wrap.removeEventListener("keydown", onOverlayKeydown);
    OPEN_OVERLAY_COUNT = Math.max(0, OPEN_OVERLAY_COUNT - 1);
    realRemove();
    if (opener && document.contains(opener) && typeof opener.focus === "function") opener.focus();
  };

  const explicit = typeof opts.initialFocus === "string" ? wrap.querySelector(opts.initialFocus) : opts.initialFocus;
  const toFocus = explicit || overlayFocusable(wrap)[0] || wrap;
  if (toFocus === wrap && !wrap.hasAttribute("tabindex")) wrap.tabIndex = -1;
  setTimeout(() => toFocus.focus(), 0);
  return wrap;
}

// ===================== core state =====================
const TODAY = Scoring.parseDate(DATA.generated) || new Date();
// Deliberately distinct from TODAY. Pass 1 pins ALL scoring + in app state
// (snooze windows etc) to TODAY = DATA.generated for full determinism no
// matter how stale the dataset is — that convention stays untouched here.
// But TODAY compared to DATA.generated_ts is always ~0 (it was PARSED FROM
// it), so anything that has to answer "how stale IS this data" (48), or that
// compares against a REAL live timestamp — an idle timer, a mark's real
// Date.now() ts, a user picked viewing date, this real calendar week's
// funnel — needs the actual wall clock, not the data's own "today". NOW_REAL
// is that reference, captured once per page load.
const NOW_REAL = new Date();
// THE CORE TRAP (cycle 6): this artifact is BUILT on an SGT machine, but the
// phone OPENING it can sit in any timezone — Winfred travels; same-offset MYT
// trips are common but foreign wifi happens too. NOW_REAL.getDay()/getHours()
// etc. read the VIEWING DEVICE's own local calendar day/time, which drifts
// from Singapore's the further that device's zone sits from +08:00. Every
// place that needs "what weekday/time is it right now, for a Singapore
// viewing slot" must read NOW_REAL_SGT/NOW_REAL_SGT_PARTS below instead of
// NOW_REAL's own getters directly — NOW_REAL itself stays the real epoch
// (idle timer, Date.now()-shaped stamps, anything that only needs "how much
// real time has elapsed", not "what calendar day is it in Singapore").
const NOW_REAL_SGT_PARTS = Scoring.sgtParts(NOW_REAL);          // {y,mo,d,hh,mm}
const NOW_REAL_SGT = Scoring.sgtDay(NOW_REAL);                  // device-local midnight of NOW_REAL's SGT day
const AREA = DATA.districts || {};
let view = "work", curL = null, curT = null, triageIndex = 0, batchSelection = new Set();
let ALL_TENANTS = [], MATCHES = [], byListing = {}, byTenant = {}, CURRENT_WORKLIST = [];
let IDLE_TIMER = null;        // (20)/(49) idle lock
const IDLE_MS = 10 * 60 * 1000;

// ===================== formatting helpers =====================
function fname(n) { return (n || "there").split(/[ ,(]/)[0]; }
function rentTxt(x) {
  if (!x) return "rent TBC";
  return x.rent_min && x.rent_max && x.rent_min !== x.rent_max ? ("$" + x.rent_min + " to $" + x.rent_max)
    : x.rent_min ? ("$" + x.rent_min) : x.rent_max ? ("$" + x.rent_max) : "rent TBC";
}
function areaName(l) { return l.address || AREA[l.district] || l.district || ""; }
function unitTypeLabel(ut) {
  if (ut === "master") return "master room";
  if (ut === "common") return "common room";
  if (ut === "whole") return "whole unit";
  if (ut === "studio") return "studio unit";
  return "room";
}
function rentTxtForUnit(u, l) { return (u && (u.rent_min || u.rent_max)) ? rentTxt(u) : rentTxt(l); }
function listingShort(l) { return l.name && l.name.length > 40 ? l.name.slice(0, 40) + "…" : (l.name || "listing"); }
function isoLocal(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
function fmtTime(hh, mm) { const ap = hh >= 12 ? "pm" : "am"; let h12 = hh % 12; if (h12 === 0) h12 = 12; return h12 + ":" + String(mm).padStart(2, "0") + ap; }

const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
function shortDate(d) { return d ? (d.getDate() + " " + MONTH_SHORT[d.getMonth()]) : ""; }
function weekdayIndex(name) {
  if (name == null) return null;
  const i = WEEKDAY_NAMES.indexOf(String(name).trim().toLowerCase().slice(0, 3));
  return i === -1 ? null : i;
}
// today: a calendar-day marker, ALREADY reduced to the correct Singapore
// calendar day (callers pass NOW_REAL_SGT — never NOW_REAL itself, see its
// comment at the top of this file's core state section: reading NOW_REAL's
// OWN getters here would compute weekdays off the viewing device's timezone,
// not Singapore's).
// nowMinutes/startMinutes (both optional, minutes since SGT midnight): when
// weekdayName names TODAY's own SGT weekday, "this <day>" should only mean
// today while that slot has not started yet — e.g. it is Friday 9:30pm SGT
// and the fixed_viewing is Friday 8 to 9pm, "this Fri" must mean NEXT Friday,
// not a slot that already ended hours ago. Omit either argument to skip that
// check entirely (falls back to the older "today always counts" rule).
function nextWeekdayDate(weekdayName, today, nowMinutes, startMinutes) {
  const idx = weekdayIndex(weekdayName);
  if (idx == null || !today) return null;
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  let delta = (idx - base.getDay() + 7) % 7;
  if (delta === 0 && nowMinutes != null && startMinutes != null && nowMinutes >= startMinutes) delta = 7;
  base.setDate(base.getDate() + delta);
  return base;
}
// Current SGT time of day, in minutes since midnight — the "now" side of
// nextWeekdayDate's already-passed check.
function sgtNowMinutes() { return NOW_REAL_SGT_PARTS.hh * 60 + NOW_REAL_SGT_PARTS.mm; }
// l.fixed_viewing.start is landlord data passed through enrich.py unvalidated
// (see load_fixed_viewing_index) — parse defensively, null on anything that
// is not a plain "HH:MM" so nextWeekdayDate degrades to its no-check fallback
// rather than mis-parsing a malformed field into a wrong "already passed" call.
function fixedViewingStartMinutes(fv) {
  if (!fv || typeof fv.start !== "string" || !/^\d{1,2}:\d{2}$/.test(fv.start)) return null;
  const [hh, mm] = fv.start.split(":").map(Number);
  if (hh > 23 || mm > 59) return null;
  return hh * 60 + mm;
}
// The generic Sat/Sun fallback slot is always worded "3pm" (see draftSlot) —
// one constant so the "already passed" check and the displayed time can never
// silently drift apart if that copy ever changes.
const GENERIC_SLOT_START_MIN = 15 * 60;
// Stable per (listing,tenant) pick of Sat vs Sun so the same pair always
// suggests the same fallback slot on repeat renders (no flicker/randomness)
// while different pairs still spread naturally across the weekend.
function pairParity(a, b) {
  const s = String(a) + String(b);
  let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 2;
}

// ===================== co-broke / whole unit / lifecycle heuristics =====================
function isCobroke(l) { return l.is_cobroke === true || l.source === "co-broke"; }
function isWholeUnitListing(l) {
  if (l.property_type && /whole|studio/i.test(l.property_type)) return true;
  return Array.isArray(l.units) && l.units.some(u => u && (u.unit_type === "whole" || u.unit_type === "studio"));
}
function isWholeUnitTenant(t) {
  if (t.preferred_location && /whole\s*unit|entire\s*(unit|flat|house)|whole\s*(flat|house)/i.test(t.preferred_location)) return true;
  return (byTenant[t.id] || []).some(m => m.s.flags.includes(Scoring.WHOLE_UNIT_FLAG));
}
// (65) l.lifecycle is dormant plumbing until the data lane's small pass 2 agent
// wires it up — absent/unknown always reads as "available" so a v1 (or current
// v2) payload renders exactly as before; only listings the data lane explicitly
// marks paused/tenanted/offer_pending/renewal_watch move into the collapsed
// "not currently available" section.
function lifecycleOf(l) { return (l && l.lifecycle) || "available"; }
function isActiveLifecycle(l) { const c = lifecycleOf(l); return c === "available" || c === "renewal_watch" || c === "unknown"; }

function daysAgoLabel(days) {
  if (days == null) return "an unknown time";
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  return days + " days ago";
}
// (46) Last contact direction on tenant cards — last_wa wins when present
// (tells you WHO wrote last), falls back to the plain last_contact date.
function lastContactLine(t) {
  if (t.last_wa && t.last_wa.ts) {
    const d = Scoring.daysAgo(t.last_wa.ts, TODAY);
    const label = daysAgoLabel(d);
    return t.last_wa.from_me ? ("you wrote " + label + " — awaiting reply") : ("they wrote " + label + " — unanswered");
  }
  if (t.last_contact) return "last contact " + daysAgoLabel(Scoring.daysAgo(t.last_contact, TODAY));
  return "no contact date on file";
}

// ===================== draft text (spec: no hyphens, no sign off) =====================
// NOW_REAL_SGT, not TODAY. A viewing slot is a real world appointment a human
// is about to propose to a tenant, so it has to be a real FUTURE date, in
// Singapore terms (see NOW_REAL_SGT's comment — the viewing device may not be
// in SGT). Deriving it from TODAY (= DATA.generated) means a dataset even a
// few days stale suggests "this Sat" on a Saturday that has already been and
// gone — the app stays usable in the amber 4 to 14 day band, so this is
// reachable.
function draftSlot(l, t) {
  if (l.fixed_viewing && l.fixed_viewing.weekday && l.fixed_viewing.time_label) {
    const d = nextWeekdayDate(l.fixed_viewing.weekday, NOW_REAL_SGT, sgtNowMinutes(), fixedViewingStartMinutes(l.fixed_viewing));
    return d ? (l.fixed_viewing.time_label + " (" + shortDate(d) + ")") : l.fixed_viewing.time_label;
  }
  const useSat = pairParity(l.id, t ? t.id : "") === 0;
  const d = nextWeekdayDate(useSat ? "sat" : "sun", NOW_REAL_SGT, sgtNowMinutes(), GENERIC_SLOT_START_MIN);
  return (useSat ? "Sat" : "Sun") + " 3pm" + (d ? (" (" + shortDate(d) + ")") : "");
}
function lastMessageContext(t) {
  if (!t.last_wa || t.last_wa.from_me || !t.last_wa.snippet) return "";
  return "You mentioned “" + t.last_wa.snippet + "”";
}
function draftEN(l, t, slotOverride) {
  const fn = fname(t.name), area = areaName(l);
  const hasBudget = t.budget != null || t.budget_max != null;
  if (!hasBudget) {
    return "Hi " + fn + ", a room just opened in " + area + " (" + l.district + ") at about " + rentTxt(l) + " a month. What's your budget and when are you looking to move in, so I can send you the right details.";
  }
  const unit = Scoring.bestUnit(l, t).unit;
  const unitLabel = unitTypeLabel(unit && unit.unit_type);
  const rt = rentTxtForUnit(unit, l);
  const ctx = lastMessageContext(t);
  const slot = slotOverride || draftSlot(l, t);
  let msg = "Hi " + fn + ", saw you are looking around " + area + ". I have a " + unitLabel + " at " + l.name + " that fits your budget at " + rt + ".";
  if (ctx) msg += " " + ctx + ".";
  msg += " Viewing this " + slot + ", can you make it?";
  return msg;
}
function draftZH(l, t, slotOverride) {
  const fn = fname(t.name), area = areaName(l);
  const hasBudget = t.budget != null || t.budget_max != null;
  if (!hasBudget) {
    return "你好" + fn + "，" + area + "（" + l.district + "）刚好有一间房，租金大约" + rentTxt(l) + "一个月。方便告诉我你的预算和大概什么时候搬入吗，这样我可以给你合适的选择";
  }
  const unit = Scoring.bestUnit(l, t).unit;
  const unitLabel = unit && unit.unit_type === "master" ? "主人房" : unit && unit.unit_type === "whole" ? "整套单位" : unit && unit.unit_type === "studio" ? "小型公寓" : "普通房间";
  const rt = rentTxtForUnit(unit, l);
  const slot = slotOverride || draftSlot(l, t);
  const ctx = (t.last_wa && !t.last_wa.from_me && t.last_wa.snippet) ? ("之前你提到“" + t.last_wa.snippet + "”。") : "";
  return "你好" + fn + "，看到你在" + area + "附近找房。我这边" + l.name + "有一间" + unitLabel + "，租金" + rt + "，符合你的预算。" + ctx + "这个" + slot + "方便来看房吗";
}
function summarizeTenantAnon(t) {
  const budget = t.budget != null ? ("$" + t.budget) : (t.budget_min != null || t.budget_max != null) ? ("$" + (t.budget_min || "?") + " to $" + (t.budget_max || "?")) : "budget TBC";
  return (t.pax || "?") + " pax, " + budget + ", move in " + (t.move_in || "flexible") + (t.occupation ? (", " + t.occupation) : "");
}
function landlordShortlistDraft(l, tenants, slotOverride) {
  const fn = fname(l.name);
  const summaries = tenants.map(summarizeTenantAnon).join("; ");
  const slot = slotOverride || draftSlot(l, null);
  const n = tenants.length;
  // Singular matters: this draft is offered from one qualified tenant upward
  // (see renderListingPanel), and "1 screened tenants" in a message going to a
  // landlord reads as a bot wrote it.
  const who = n + (n === 1 ? " screened tenant" : " screened tenants");
  const ask = n === 1 ? "Want me to line up a viewing " : "Want me to line up viewings ";
  return "Hi " + fn + ", I have " + who + " keen on your " + listingShort(l) + ": " + summaries + ". " + ask + slot + "?";
}
function cobrokeDraft(l, t, slotOverride) {
  const budget = t.budget != null ? t.budget : (t.budget_max != null ? t.budget_max : null);
  const band = budget != null ? ("$" + budget) : "budget TBC";
  const slot = slotOverride || draftSlot(l, t);
  return "Hi, referring to your " + listingShort(l) + " listing. I have a qualified tenant, " + (t.pax || "?") + " pax, budget " + band + ", move in " + (t.move_in || "flexible") + ". Open to co-broke viewing " + slot + "?";
}
function draftFor(l, t, slotOverride) { return isCobroke(l) ? cobrokeDraft(l, t, slotOverride) : draftEN(l, t, slotOverride); }

// ---- pass 2 draft skeletons (exact voice: no hyphens, no sign off) ----
function reconfirmDraft(l) {
  return "Hi " + fname(l.name) + ", quick check that " + listingShort(l) + " is still available at " + rentTxt(l) + "? Have active tenants asking this week.";
}
const MISSING_FIELD_QUESTIONS = {
  budget: "what is your budget",
  move_in: "when are you looking to move in",
  pax: "how many pax will be staying",
  lease_months: "how many months lease are you looking for",
  district: "which areas are you looking at",
  // Half the available stock carries a landlord gender gate, and 61 of the 64 blanks
  // never stated it in their WhatsApp thread — so asking is the only way it resolves.
  gender: "is the room for a male or female tenant"
};
function oneQuestionDraft(t, field) {
  const q = MISSING_FIELD_QUESTIONS[field] || "a quick detail so I can shortlist properly";
  return "Hi " + fname(t.name) + ", quick one so I can shortlist properly: " + q + "?";
}
function landlordOneQuestionDraft(l) {
  return "Hi " + fname(l.name) + ", quick check on " + listingShort(l) + ", what are the tenant preferences (gender, pax, lease length)? Want to shortlist properly.";
}
function verdictCollectDraft(t, l) {
  return "Hi " + fname(t.name) + ", how did you find " + listingShort(l) + " yesterday? Keen to proceed or should I line up other options?";
}
function dayOfReminderDraft(l, t, slotLabel) {
  return "Hi " + fname(t.name) + ", quick reminder we are viewing " + listingShort(l) + " " + slotLabel + ". See you there";
}
function landlordHeadsUpDraft(l, t, slotLabel) {
  return "Hi " + fname(l.name) + ", heads up a tenant of mine is coming to view " + listingShort(l) + " " + slotLabel + ".";
}

// ===================== links =====================
function normPhone(raw) { let p = (raw || "").replace(/[^0-9]/g, ""); if (p.length === 8 && /^[89]/.test(p)) p = "65" + p; return p; }
function waLink(l, t) {
  const target = isCobroke(l) ? l.phone : t.phone;
  const p = normPhone(target);
  return p ? ("https://wa.me/" + p + "?text=" + encodeURIComponent(draftFor(l, t))) : "";
}
function waPlain(phone, msg) { const p = normPhone(phone); return p ? ("https://wa.me/" + p + (msg ? "?text=" + encodeURIComponent(msg) : "")) : ""; }
function mapLink(l) { return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(l.map_query || areaName(l)); }
function coldTitle() { return "quiet >30 days — dead per your rule"; }
// The DEAD lead rule, in ONE place. Every tenant facing action (draft body,
// copy, call, WhatsApp, queue) asks this and nothing else, so the badge on a row
// and what that row will actually let you do can never drift apart again.
//
// This asks isDeadFromDays (30 days), NOT isColdFromDays (5). The two were the
// same constant until 13 Aug 2026, which meant the 5 day ranking signal was also
// gagging outreach: 151 of 218 tenants were unreachable when Winfred's actual
// rule — widened from 5 to 14 to 30 — should have blocked 68. Cold still drives
// ranking, the freshness score and the amber badge; only dead blocks a send.
//
// Landlord and co-broke contact is exempt by design: pass the listing and a
// co-broke listing answers false, or pass null when the action targets the
// tenant regardless of listing (health tab one question).
function coldBlocked(l, t) {
  if (l && isCobroke(l)) return false;
  return Scoring.isDeadFromDays(coldDaysOf(t));
}
// (73) coldDays(t) reads only frozen payload fields (t.last_contact,
// t.last_wa.ts) against TODAY, which is itself pinned to DATA.generated for the
// whole life of the page — so a given tenant's answer cannot change while the
// app is open, which is what makes remembering it safe. Worth remembering
// because updateFacetedCounts() and passFilter() each ask it once per PAIR: at
// current dataset size that is 1960 questions for 140 distinct answers, and
// every tenant here carries a last_wa.ts, which routes coldDays through
// Intl.DateTimeFormat (Scoring.parseDate's offset branch -> sgtDay -> sgtParts)
// at ~6us a call. Measured in the built artifact over all 1960 pairs: 12.2ms
// per sweep before, 0.3ms after, against a ~23ms full render. Cleared in
// rebuildMatches() next to MARK_CACHE, which is the path a newly added scratch
// tenant already takes.
const COLD_CACHE = new Map();
function coldDaysOf(t) {
  if (!t) return null;
  // An unkeyed tenant must not land in the cache under a null id, where it
  // would then answer for every other unkeyed tenant.
  if (t.id == null) return Scoring.coldDays(t, TODAY);
  if (COLD_CACHE.has(t.id)) return COLD_CACHE.get(t.id);
  const v = Scoring.coldDays(t, TODAY);
  COLD_CACHE.set(t.id, v);
  return v;
}
// Threshold comparison stays in scoring.js (Scoring.isColdFromDays) so the 5
// day rule keeps exactly one definition — see coldBlocked's note above.
function isColdT(t) { return Scoring.isColdFromDays(coldDaysOf(t)); }
// Shown instead of a draft body whenever the rule fires — the point is that no
// message text is ever produced for a dead lead, not merely that Copy is greyed.
function coldRefusalHtml(t) {
  const dc = coldDaysOf(t);
  return '<div class="draftlabel">No draft</div><div class="draftbox mut">' +
    esc(fname(t.name)) + ' last replied ' + esc(daysAgoLabel(dc)) +
    '. Dead per your 30 day rule, so no message is drafted here. Landlord and co-broke contact is unaffected.</div>';
}
// Single choke point for every WhatsApp/Call button in a modal or popover
// (viewing pack, shortlist draft, reconfirm draft, ...) — keeps the dead lead
// rule consistent everywhere a wa.me/tel: link is offered, not just the per
// pair worklist rows (rowActionsHtml has its own equivalent check inline).
function waButtonHtml(phone, msg, label, coldBlocked) {
  if (!phone) return "";
  if (coldBlocked) return '<span class="btn disabled" title="' + coldTitle() + '">' + esc(label) + '</span>';
  return '<a class="btn w" target="_blank" rel="noopener noreferrer" href="' + escUrl(waPlain(phone, msg)) + '">' + esc(label) + '</a>';
}
function callButtonHtml(phone, label, coldBlocked) {
  if (!phone) return "";
  if (coldBlocked) return '<span class="btn disabled" title="' + coldTitle() + '">' + esc(label) + '</span>';
  return '<a class="btn" href="' + escUrl("tel:" + normPhone(phone)) + '">' + esc(label) + '</a>';
}
// For a link that is already fully built (l.listing_url is itself a wa.me
// deep link per the data lane — not something to run through waPlain(phone,
// msg) again).
function linkButtonHtml(href, label) {
  if (!href) return "";
  const safe = escUrl(href);
  if (!safe) return "";
  return '<a class="btn" target="_blank" rel="noopener noreferrer" href="' + safe + '">' + esc(label) + '</a>';
}

// ===================== CRM store (cloud-backed durability layer) =====================
// The durable half of the app. Everything Winfred RECORDS through the drawer below
// (stage, next action, notes, tasks) lives here and syncs to Postgres via /api/crm —
// see deploy/api/crm.js for the wire contract. Everything the app KNOWS (names,
// phones, budgets, last message dates) still comes from DATA, is rebuilt by build.py,
// and is never written back to — keeping those two apart is what stops a nightly
// rebuild and this app from ever overwriting each other.
//
// Offline first on purpose: every write lands in localStorage and renders
// immediately, then queues for the server. The queue survives a reload, so a write
// made on the MRT with no signal is still there when the connection returns. If
// DATABASE_URL was never set on the Vercel project the API answers 501 and the app
// simply stays local — a supported mode, not a failure, and the app must behave
// identically either way.
//
// Storage keys use "cbkcrm_" (no underscore between cbk and crm), not "cbk_crm_": the
// mark prefix is MARK_PREFIX="cbk_" and isMarkKey()/exportBlob() classify ANYTHING
// starting with it as a listing/tenant mark unless explicitly excluded — a
// "cbk_crm_v1" key would misparse as mark cbk_<lid="crm">_<tid="v1"> and get swept
// into state export/import. "cbkcrm_" does not start with "cbk_" so it stays
// invisible to that logic without teaching isMarkKey a new exception.
const CRM = (function () {
  const LKEY = "cbkcrm_v1", QKEY = "cbkcrm_queue_v1", MKEY = "cbkcrm_migrated_v1";
  const API = "/api/crm";
  let S = { entities: {}, notes: [], tasks: [], match: {}, activity: [] };
  let queue = [], mode = "local", lastErr = "", timer = null, retryTimer = null, backoff = 0, booted = false, nextTmp = -1;
  const RETRY_MIN = 5000, RETRY_MAX = 60000;

  const j = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } };
  const todayStr = () => new Date().toISOString().slice(0, 10);

  // (item 4/5) Immediate, synchronous persist — used where a caller needs to
  // KNOW the write actually landed before doing anything else (migrate()'s
  // flag ordering, a fresh server snapshot). Empty catch used to mean quota
  // exhaustion here failed completely silently — the drawer kept rendering
  // from in-memory S so a note looked saved until the next reload wiped it.
  // Mirrors safeSet's toast so every storage-full failure in this app reads
  // the same way to the user, mark layer or CRM layer.
  let saveScheduled = false;
  function saveSync() {
    saveScheduled = false;
    try { localStorage.setItem(LKEY, JSON.stringify(S)); localStorage.setItem(QKEY, JSON.stringify(queue)); return true; }
    catch (e) { toast("Device storage is full — that CRM change was not saved. Export state, then clear old browser data."); return false; }
  }
  // (item 4) Debounced persist for the hot interactive path (push() below,
  // called once per pair from bulk actions). Coalesces N synchronous writes
  // in the same call stack into exactly one JSON.stringify(S)+queue at the
  // end of that stack via a microtask — which always drains before control
  // returns to the browser (i.e. before a tab close can race it) — instead of
  // re-serialising the whole growing store on every single op. Measured: four
  // identical 300 pair bulk batches went 60/134/219/308ms before this: each
  // push() paid for a full store snapshot, and that snapshot only gets bigger
  // as the day goes on.
  function scheduleSave() {
    if (saveScheduled) return;
    saveScheduled = true;
    Promise.resolve().then(saveSync);
  }
  // (item 4) O(1) lookup so push() below can compact repeat writes to the
  // same entity/pair instead of appending a new queue entry every time —
  // without this a queue rebuilt via .find() per push is itself an O(n) scan
  // against an ever growing array, i.e. the exact quadratic cost this is
  // fixing, just moved one level down. Rebuilt (not incrementally patched)
  // anywhere `queue` itself is reassigned wholesale (boot's initial load,
  // migrate's concat, flush's post send slice) so a stale entry can never
  // point at an op object that has already left the array.
  let entityOpIndex = new Map(), matchOpIndex = new Map();
  function reindexQueue() {
    entityOpIndex = new Map(); matchOpIndex = new Map();
    queue.forEach(o => {
      if (o.op === "entity" && o.key) entityOpIndex.set(o.key, o);
      else if (o.op === "match" && o.listing_id) matchOpIndex.set(o.listing_id + "|" + o.tenant_id, o);
    });
  }

  // A person/record, not a row — keyed by phone whenever there is one so the same
  // human matches across every place they appear (worklist row, roster, drawer).
  // Records with no phone fall back to kind:id.
  function keyOf(s) { if (!s) return null; const p = normPhone(s.phone); return p ? ("phone:" + p) : (s.id != null ? ((s.kind || "person") + ":" + s.id) : null); }
  function ent(s) { const k = keyOf(s); return k ? (S.entities[k] || null) : null; }
  function ensure(k, s) { return S.entities[k] || (S.entities[k] = { key: k, kind: (s && s.kind) || "person", ref_id: s && s.id, name: s && s.name, phone: s && s.phone, stage: "new", flagged: false, archived: false }); }

  // (item 4) entity/match ops compact in place — a bulk action re-marking the
  // same pair, or a drawer edited twice before the next flush, updates the
  // still queued op instead of appending a duplicate, so a queue built while
  // offline (mode:"local" never drains it — see schedule() below) grows with
  // the number of DISTINCT pairs/records actually touched, not the number of
  // writes made to them. note/task ops are never compacted here — each one is
  // a genuinely distinct piece of content, not a repeatable status.
  function push(op) {
    if (op.op === "entity" && op.key) {
      const existing = entityOpIndex.get(op.key);
      if (existing) { existing.patch = Object.assign({}, existing.patch, op.patch); existing.kind = op.kind; existing.ref_id = op.ref_id; existing.name = op.name; existing.phone = op.phone; }
      else { queue.push(op); entityOpIndex.set(op.key, op); }
    } else if (op.op === "match" && op.listing_id) {
      const mk = op.listing_id + "|" + op.tenant_id;
      const existing = matchOpIndex.get(mk);
      if (existing) existing.status = op.status;
      else { queue.push(op); matchOpIndex.set(mk, op); }
    } else {
      queue.push(op);
    }
    scheduleSave(); schedule();
  }
  function schedule() { if (mode === "local") return; clearTimeout(timer); timer = setTimeout(flush, 600); }
  // A failed flush must keep retrying on its own — the realistic outage is the
  // backend or its database being briefly unreachable while the browser still
  // thinks it is online, and without this the queue would sit untouched until the
  // next write or the next reload, which for a note typed just before locking the
  // phone could be days.
  function retryLater() {
    clearTimeout(retryTimer);
    backoff = backoff ? Math.min(backoff * 2, RETRY_MAX) : RETRY_MIN;
    retryTimer = setTimeout(() => { if (mode === "local") return; queue.length ? flush() : resync(); }, backoff);
  }
  // Re-GET when there is nothing to send. Recovers the pill from "offline" once the
  // backend is reachable again, and picks up anything written on Winfred's other device.
  async function resync() {
    try {
      const r = await fetch(API, { headers: { "Accept": "application/json" } });
      if (r.status === 501) { mode = "local"; paint(); return; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      adopt(await r.json()); mode = "cloud"; lastErr = ""; backoff = 0;
      saveSync(); paint(); if (window.render) render();
    } catch (e) { mode = "offline"; lastErr = String(e && e.message || e); paint(); retryLater(); }
  }
  async function flush() {
    if (mode === "local" || !queue.length || mode === "syncing") return;
    const sending = queue.slice(0, 200), prev = mode;
    mode = "syncing"; paint();
    try {
      const r = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ops: sending }) });
      // 501 = no DATABASE_URL on this deployment. Stop trying, but KEEP the queue —
      // if this is a misconfiguration rather than a deliberate local-only deploy,
      // discarding it here would throw away real writes the next correct deploy
      // would otherwise pick up.
      if (r.status === 501) { mode = "local"; saveSync(); paint(); return; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      adopt(await r.json());
      queue = queue.slice(sending.length); reindexQueue(); mode = "cloud"; lastErr = ""; backoff = 0; clearTimeout(retryTimer);
      saveSync(); paint(); if (queue.length) schedule(); else if (window.render) render();
    } catch (e) {
      // Keep the queue — it is persisted, so nothing is lost. It goes out on the
      // retry below, on the next write, on the next boot, or on 'online', whichever
      // comes first.
      mode = prev === "syncing" ? "offline" : prev; if (mode !== "local") mode = "offline";
      lastErr = String(e && e.message || e); paint(); retryLater();
    }
  }
  // Server snapshot becomes the truth for everything already acknowledged; anything
  // still queued locally is re-applied on top so an in-flight write does not
  // visibly revert on screen while its POST is still in the air.
  function adopt(d) {
    const e = {}; (d.entities || []).forEach(x => e[x.key] = x);
    const m = {}; (d.match || []).forEach(x => m[x.listing_id + "|" + x.tenant_id] = x.status);
    S = { entities: e, notes: d.notes || [], tasks: d.tasks || [], match: m, activity: d.activity || [] };
    queue.forEach(replay);
  }
  // (item 3) Every op type the queue can hold must be replayable, not just
  // entity/match — adopt() above REPLACES S.notes/S.tasks wholesale with the
  // server's snapshot, so any note/task op still sitting in the queue (not
  // yet acknowledged — e.g. tail ops beyond flush()'s 200 per batch cap, or
  // anything queued while resync() runs) used to vanish from S the moment a
  // snapshot landed, then get persisted that way by the save() right after —
  // a note Winfred just typed disappearing from the drawer because a GET came
  // back while it was still in flight. tempId (set by addNote/addTask below)
  // is what lets a still local only note/task be reconstructed here; a "task"
  // op with a real `id` instead is an update to one the server already knows
  // about, patched onto the matching entry if the snapshot already has it.
  function replay(o) {
    if (o.op === "entity" && o.key) { const x = ensure(o.key, o); Object.assign(x, o.patch || {}); }
    else if (o.op === "match" && o.listing_id) { const k = o.listing_id + "|" + o.tenant_id; o.status ? S.match[k] = o.status : delete S.match[k]; }
    else if (o.op === "note" && o.tempId != null) {
      if (o.key) ensure(o.key, o);
      if (!S.notes.some(n => n.id === o.tempId)) S.notes.unshift({ id: o.tempId, key: o.key || null, body: o.body, created_at: o.created_at || new Date().toISOString() });
    }
    else if (o.op === "note_delete" && o.id != null) { S.notes = S.notes.filter(n => n.id !== o.id); }
    else if (o.op === "task" && o.tempId != null) {
      if (o.key) ensure(o.key, o);
      if (!S.tasks.some(t => t.id === o.tempId)) S.tasks.push({ id: o.tempId, key: o.key || null, title: o.title, due: o.due || null, done: !!o.done, created_at: o.created_at || new Date().toISOString() });
    }
    else if (o.op === "task" && o.id != null) {
      const t = S.tasks.find(x => x.id === o.id);
      if (t) Object.assign(t, { title: o.title, due: o.due, done: o.done });
    }
    else if (o.op === "task_delete" && o.id != null) { S.tasks = S.tasks.filter(t => t.id !== o.id); }
  }

  // One-time import of pre-existing device-local state (marks, verdict overrides,
  // offer stages — mirrored on write from here on by mirrorMatchToCRM/setOfferStage
  // below) so nothing Winfred already recorded before the CRM shipped is lost the
  // day it goes live. Runs once; MKEY guards the repeat. crmMatchStatusFor/isMarkKey/
  // isOfferKey/readMark/readOffer/OFFER_PREFIX/OFFER_STAGES are all function
  // declarations or module-level consts defined later in this file — safe to
  // reference here because this function body only runs when CRM.boot() calls it,
  // long after the whole script has finished its first synchronous pass.
  function migrate() {
    if (localStorage.getItem(MKEY)) return;
    const ops = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i); if (!k) continue;
        if (isMarkKey(k)) {
          const rest = k.slice(MARK_PREFIX.length), us = rest.indexOf("_");
          if (us === -1) continue;
          const lid = rest.slice(0, us), tid = rest.slice(us + 1);
          const status = crmMatchStatusFor(lid, tid);
          if (status) ops.push({ op: "match", listing_id: lid, tenant_id: tid, status });
          // "Contacted" is the one mark signal worth promoting to a durable
          // per-person stamp. Preserve the MARK's OWN timestamp as the contacted
          // date, not today's date — a stamp from 9 Aug must stay 9 Aug.
          const mk = readMark(lid, tid);
          if (mk && mk.v === "Contacted" && mk.ts) {
            const t = ALL_TENANTS.find(x => x.id === tid) || (DATA.all_tenants || []).find(x => x.id === tid);
            const subj = { kind: "tenant", id: tid, name: t && t.name, phone: t && t.phone };
            const key = keyOf(subj);
            if (key) ops.push({ op: "entity", key, kind: "tenant", ref_id: tid, name: subj.name, phone: subj.phone, patch: { contacted_on: new Date(mk.ts).toISOString().slice(0, 10) } });
          }
        } else if (isOfferKey(k)) {
          const rest = k.slice(OFFER_PREFIX.length), us = rest.indexOf("_");
          if (us === -1) continue;
          const lid = rest.slice(0, us), tid = rest.slice(us + 1);
          const o = readOffer(lid, tid); if (!o) continue;
          const t = ALL_TENANTS.find(x => x.id === tid) || (DATA.all_tenants || []).find(x => x.id === tid);
          const subj = { kind: "tenant", id: tid, name: t && t.name, phone: t && t.phone };
          const key = keyOf(subj);
          if (key) ops.push({ op: "entity", key, kind: "tenant", ref_id: tid, name: subj.name, phone: subj.phone, patch: { stage: o.stage >= OFFER_STAGES.length - 1 ? "closed_won" : "offer" } });
        }
      }
    } catch (e) { /* best effort — a migration failure must never block boot */ }
    ops.forEach(o => { if (o.op === "entity") { const x = ensure(o.key, o); Object.assign(x, o.patch); } else replay(o); });
    if (ops.length) { queue = queue.concat(ops); reindexQueue(); }
    // (item 6) Flag only AFTER the migrated data is durably written, and only
    // if that write actually succeeded — saveSync() (unlike the old bare
    // save()) reports failure. The 18 byte flag write fitting when the real
    // migrated data does not is exactly the case where migration must be
    // retried next boot, not marked done with nothing migrated.
    if (saveSync()) { try { localStorage.setItem(MKEY, todayStr()); } catch (e) { /* best effort marker only — migrated data is already durable */ } }
  }

  function paint() {
    const pillEl = document.getElementById("syncPill"); if (!pillEl) return;
    const n = queue.length;
    const map = {
      local: ["local only", "mut", "Saved on this device only — no cloud backend is configured, so nothing syncs to your phone or survives clearing this browser."],
      cloud: [n ? ("syncing " + n) : "synced", n ? "a" : "g", n ? ("Sending " + n + " pending change(s).") : "All changes saved to the cloud."],
      syncing: ["syncing…", "a", "Sending changes."],
      offline: ["offline · " + n + " queued", "r", "Cannot reach the CRM backend" + (lastErr ? (" (" + lastErr + ")") : "") + ". Your changes are saved on this device and will send automatically once it is reachable."],
    };
    const [txt, cls, tip] = map[mode] || map.local;
    pillEl.className = "chip " + cls; pillEl.textContent = (mode === "cloud" && !n ? "☁ " : mode === "local" ? "▣ " : "⟳ ") + txt; pillEl.title = tip;
  }

  return {
    get mode() { return mode; }, get pending() { return queue.length; }, paint, flush, keyOf,
    async boot() {
      if (booted) return; booted = true;
      S = j(LKEY, S); queue = j(QKEY, []);
      // (item 7) A hostile/corrupt cbkcrm_v1 blob that parses but is not the
      // shape this store expects (a bare number/string/array, or an object
      // missing one of these fields) used to throw the moment S.entities/
      // S.match got assigned below — an unguarded synchronous throw this
      // early in an async function rejects boot()'s own promise before the
      // first await, which with no .catch() on the call site (see init, this
      // file's bottom) left the CRM permanently unsynced for the session with
      // nothing on screen to explain why. Reset to a fresh, well shaped store
      // instead of trusting the parsed shape.
      if (!S || typeof S !== "object" || Array.isArray(S)) S = { entities: {}, notes: [], tasks: [], match: {}, activity: [] };
      if (!S.entities || typeof S.entities !== "object") S.entities = {};
      if (!S.match || typeof S.match !== "object") S.match = {};
      if (!Array.isArray(S.notes)) S.notes = [];
      if (!Array.isArray(S.tasks)) S.tasks = [];
      if (!Array.isArray(S.activity)) S.activity = [];
      if (!Array.isArray(queue)) queue = [];
      reindexQueue();
      migrate();
      paint();
      try {
        const r = await fetch(API, { headers: { "Accept": "application/json" } });
        if (r.status === 501) { mode = "local"; }
        else if (r.ok) { adopt(await r.json()); mode = "cloud"; saveSync(); }
        else throw new Error("HTTP " + r.status);
      } catch (e) { mode = "offline"; lastErr = String(e && e.message || e); }
      paint(); if (window.render) render();
      if (mode !== "local" && queue.length) flush();
      else if (mode === "offline") retryLater();   // reachable again later even with nothing queued
      window.addEventListener("online", () => { if (mode === "offline") { backoff = 0; flush(); } });
      // Coming back to the app is the moment a stalled queue most wants a retry, and
      // it costs nothing when there is nothing pending.
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && mode !== "local" && queue.length) { backoff = 0; flush(); }
      });
    },
    entity: ent,
    stage(s) { const e = ent(s); return e ? (e.stage || "new") : "new"; },
    setStage(s, v) {
      const k = keyOf(s); if (!k) return; Object.assign(ensure(k, s), { stage: v });
      push({ op: "entity", key: k, kind: s.kind, ref_id: s.id, name: s.name, phone: s.phone, patch: { stage: v } });
    },
    setPlan(s, action, due) {
      const k = keyOf(s); if (!k) return; Object.assign(ensure(k, s), { next_action: action || null, next_due: due || null });
      push({ op: "entity", key: k, kind: s.kind, ref_id: s.id, name: s.name, phone: s.phone, patch: { next_action: action || null, next_due: due || null } });
    },
    matchStatus(l, t) { return S.match[l + "|" + t] || ""; },
    setMatchStatus(l, t, v) {
      v ? S.match[l + "|" + t] = v : delete S.match[l + "|" + t];
      push({ op: "match", listing_id: l, tenant_id: t, status: v || "" });
    },
    notes(s) { const k = keyOf(s); return k ? S.notes.filter(n => n.key === k) : []; },
    addNote(s, body) {
      const k = keyOf(s); if (!k || !body) return; ensure(k, s);
      const id = nextTmp--, created_at = new Date().toISOString();
      S.notes.unshift({ id, key: k, body, created_at });
      // tempId travels with the queued op so delNote/replay can find this
      // exact still unsynced write again (see both below and replay() above).
      push({ op: "note", tempId: id, key: k, kind: s.kind, ref_id: s.id, name: s.name, phone: s.phone, body, created_at });
    },
    // (item 3) A temp id (negative — see nextTmp above) means this note has
    // never reached the server: the ORIGINAL "note" add op is still sitting
    // in the queue with nothing to delete server side yet. The old code only
    // pushed note_delete for id>0 and left that add op queued regardless — a
    // note deleted before its first sync got silently resurrected into
    // Postgres on the next successful flush. Cancel the still queued add
    // outright instead; nothing was ever sent, so there is nothing to undo.
    delNote(id) {
      S.notes = S.notes.filter(n => n.id !== id);
      if (id > 0) { push({ op: "note_delete", id }); }
      else {
        const before = queue.length;
        queue = queue.filter(o => !(o.op === "note" && o.tempId === id));
        if (queue.length !== before) scheduleSave();
      }
    },
    tasks(s) { if (!s) return S.tasks.slice(); const k = keyOf(s); return S.tasks.filter(t => t.key === k); },
    addTask(s, title, due) {
      if (!title) return; const k = s ? keyOf(s) : null; if (k) ensure(k, s);
      const id = nextTmp--, created_at = new Date().toISOString();
      S.tasks.push({ id, key: k, title, due: due || null, done: false, created_at });
      push(Object.assign({ op: "task", tempId: id, title, due: due || null, created_at }, k ? { key: k, kind: s.kind, ref_id: s.id, name: s.name, phone: s.phone } : {}));
    },
    // (item 3) Same temp id problem as delNote: a task added while offline has
    // no server id yet, so toggling it used to be a no-op on the queue (guarded
    // by id>0) — completion never left the device, and the queued add's fixed
    // done:false would win the moment it finally synced. Mutate that still
    // queued add op in place instead; there is nothing server side to patch yet.
    toggleTask(id) {
      const t = S.tasks.find(x => x.id === id); if (!t) return; t.done = !t.done;
      if (id > 0) { push({ op: "task", id, title: t.title, due: t.due, done: t.done }); }
      else {
        const q = queue.find(o => o.op === "task" && o.tempId === id);
        if (q) { q.done = t.done; scheduleSave(); }
      }
    },
    delTask(id) {
      S.tasks = S.tasks.filter(x => x.id !== id);
      if (id > 0) { push({ op: "task_delete", id }); }
      else {
        const before = queue.length;
        queue = queue.filter(o => !(o.op === "task" && o.tempId === id));
        if (queue.length !== before) scheduleSave();
      }
    },
    activity(s) { if (!s) return S.activity.slice(); const k = keyOf(s); return S.activity.filter(a => a.key === k); },
    all() { return Object.values(S.entities); },
    // ---- backup/export bridge (item 1) ----
    // Raw snapshot for exportBlob()/writeAutoBackup() below — this is the ONLY
    // durable copy of every stage, note and task Winfred has ever recorded
    // when DATABASE_URL is unset (the normal, supported mode — see this
    // module's header comment), so it has to travel with state export/import/
    // backup exactly like marks/overrides/offers/scratch do, not be left out.
    exportState() {
      return { entities: Object.values(S.entities), notes: S.notes.slice(), tasks: S.tasks.slice(), match: Object.assign({}, S.match) };
    },
    // Merge policy: if this device's CRM store is empty (the realistic case —
    // browser data was just cleared, or this is a restore onto a fresh
    // profile) the backup becomes the whole store outright. Otherwise merge
    // additively and only fill in what is missing — an import must never
    // overwrite or drop anything already recorded on this device, same rule
    // as importBlob() uses for marks/overrides/offers.
    importState(blob) {
      const empty = { entities: 0, notes: 0, tasks: 0, match: 0 };
      if (!blob || typeof blob !== "object") return empty;
      if (!Object.keys(S.entities).length && !S.notes.length && !S.tasks.length && !Object.keys(S.match).length) {
        const e = {}, restoreOps = [];
        (Array.isArray(blob.entities) ? blob.entities : []).forEach(x => {
          if (!x || !x.key) return;
          e[x.key] = Object.assign({}, x);
          // Idempotent patch (every non identity field) — safe to requeue even
          // if this exact record already reached a server from wherever it was
          // exported, so a restored device still gets its own copy onto a
          // backend that only appears later instead of the data staying
          // screen-only.
          const patch = Object.assign({}, x);
          delete patch.key; delete patch.kind; delete patch.ref_id; delete patch.name; delete patch.phone;
          restoreOps.push({ op: "entity", key: x.key, kind: x.kind, ref_id: x.ref_id, name: x.name, phone: x.phone, patch });
        });
        const notes = Array.isArray(blob.notes) ? blob.notes.slice() : [];
        const tasks = Array.isArray(blob.tasks) ? blob.tasks.slice() : [];
        // Only requeue notes/tasks that never reached a server (temp/negative
        // id) — a positive id already exists there and requeuing it as a plain
        // "note"/"task" add (no upsert by id in this wire protocol) would
        // duplicate it once this device eventually flushes.
        notes.forEach(n => { if (n && n.id < 0) restoreOps.push({ op: "note", tempId: n.id, key: n.key, body: n.body, created_at: n.created_at }); });
        tasks.forEach(t => { if (t && t.id < 0) restoreOps.push({ op: "task", tempId: t.id, key: t.key, title: t.title, due: t.due, done: t.done, created_at: t.created_at }); });
        const match = (blob.match && typeof blob.match === "object") ? Object.assign({}, blob.match) : {};
        Object.keys(match).forEach(k => {
          const us = k.indexOf("|");
          if (us !== -1) restoreOps.push({ op: "match", listing_id: k.slice(0, us), tenant_id: k.slice(us + 1), status: match[k] });
        });
        S = { entities: e, notes, tasks, match, activity: S.activity };
        if (restoreOps.length) { queue = queue.concat(restoreOps); reindexQueue(); }
        saveSync();
        return { entities: Object.keys(e).length, notes: notes.length, tasks: tasks.length, match: Object.keys(match).length };
      }
      let entC = 0, noteC = 0, taskC = 0, matchC = 0;
      (Array.isArray(blob.entities) ? blob.entities : []).forEach(x => {
        if (x && x.key && !S.entities[x.key]) { S.entities[x.key] = Object.assign({}, x); entC++; }
      });
      const noteIds = new Set(S.notes.map(n => n.key + "|" + n.id));
      (Array.isArray(blob.notes) ? blob.notes : []).forEach(n => {
        if (!n || n.id == null || !n.key) return;
        const idn = n.key + "|" + n.id;
        if (!noteIds.has(idn)) { S.notes.push(Object.assign({}, n)); noteIds.add(idn); noteC++; }
      });
      const taskIds = new Set(S.tasks.map(t => (t.key || "") + "|" + t.id));
      (Array.isArray(blob.tasks) ? blob.tasks : []).forEach(t => {
        if (!t || t.id == null) return;
        const idt = (t.key || "") + "|" + t.id;
        if (!taskIds.has(idt)) { S.tasks.push(Object.assign({}, t)); taskIds.add(idt); taskC++; }
      });
      if (blob.match && typeof blob.match === "object") {
        Object.keys(blob.match).forEach(k => { if (!(k in S.match)) { S.match[k] = blob.match[k]; matchC++; } });
      }
      saveSync();
      return { entities: entC, notes: noteC, tasks: taskC, match: matchC };
    },
  };
})();
const STAGE_LABELS = { new: "New", contacted: "Contacted", qualified: "Qualified", viewing_set: "Viewing set", viewed: "Viewed", offer: "Offer", closed_won: "Closed won", closed_lost: "Lost", dormant: "Dormant" };
const STAGE_ORDER = ["new", "contacted", "qualified", "viewing_set", "viewed", "offer", "closed_won", "closed_lost", "dormant"];
function subjOf(kind, o) { return { kind, id: o.id, name: o.name, phone: o.phone }; }

// ===================== localStorage model =====================
// marks: cbk_<lid>_<tid> -> JSON {v,ts,reason?,viewing_date?,snooze_until?,note?}
// legacy bare string values ("Contacted" etc, no JSON) are read transparently.
const MARK_PREFIX = "cbk_", OVERRIDE_PREFIX = "cbko_", SCRATCH_KEY = "cbk_scratch";
// setItem throws on a full or unavailable store (private browsing, quota).
// Unguarded that used to abort the click handler mid way, so the mark was lost
// AND the render() after it never ran — the UI just stopped responding with no
// explanation. Degrade instead: tell the user, keep everything already stored
// intact, and let the caller carry on.
function safeSet(key, value) {
  try { localStorage.setItem(key, value); return true; }
  catch (e) { toast("Device storage is full — that change was not saved. Export state, then clear old browser data."); return false; }
}
// (71) read-through cache for readMark/readOverride, keyed by the exact
// localStorage key string (cbk_.../cbko_... — distinct prefixes, one Map is
// safe for both). Measured cause of a 23ms/render facet-count cost: every
// render() calls updateFacetedCounts(), which runs facetCount() once per
// district + once per verdict option, each doing a full 1960-pair sweep —
// tens of thousands of synchronous localStorage.getItem() calls per render
// with the dataset at cycle 7 size, dwarfing the ~9ms the row-building itself
// costs. The mark/override VALUES only change through the handful of
// functions below that write these prefixes — every one of them deletes its
// key here right after writing, so a cached read can never go stale. Cleared
// wholesale in rebuildMatches() too, as a safety net for any bulk-write path.
const MARK_CACHE = new Map();
function markKey(lid, tid) { return MARK_PREFIX + lid + "_" + tid; }
function readMark(lid, tid) {
  const key = markKey(lid, tid);
  if (MARK_CACHE.has(key)) return MARK_CACHE.get(key);
  const raw = localStorage.getItem(key);
  let val;
  if (!raw) val = null;
  else if (raw[0] !== "{") val = { v: raw, ts: 0 };
  else { try { const o = JSON.parse(raw); val = (o && typeof o === "object") ? o : { v: raw, ts: 0 }; } catch (e) { val = { v: raw, ts: 0 }; } }
  MARK_CACHE.set(key, val);
  return val;
}
function getMarkV(lid, tid) { const mk = readMark(lid, tid); return mk ? (mk.v || "") : ""; }
// (70) every mark stamps by:<device name> once one has been set. (13)/(27) a
// mark write that changes `v` also appends to the history log the funnel
// reads — patchMark is the single choke point for every mark write in the
// app, so both concerns live here rather than being repeated per call site.
function patchMark(lid, tid, patch) {
  const cur = readMark(lid, tid) || {};
  const next = Object.assign({}, cur, patch, { ts: Date.now() });
  if (typeof PREFS !== "undefined" && PREFS && PREFS.device_name) next.by = PREFS.device_name;
  const key = markKey(lid, tid);
  if (!safeSet(key, JSON.stringify(next))) return cur;
  MARK_CACHE.delete(key);   // (71) next readMark(lid,tid) re-reads the value just written
  if (patch && patch.v) pushMarkHistory(lid, tid, patch.v);
  mirrorMatchToCRM(lid, tid);   // durable copy — see crmMatchStatusFor's comment
  return next;
}
function clearMarkV(lid, tid) { const key = markKey(lid, tid); localStorage.removeItem(key); MARK_CACHE.delete(key); mirrorMatchToCRM(lid, tid); }

function overrideKey(lid, tid) { return OVERRIDE_PREFIX + lid + "_" + tid; }
function readOverride(lid, tid) {
  const key = overrideKey(lid, tid);
  if (MARK_CACHE.has(key)) return MARK_CACHE.get(key);
  const raw = localStorage.getItem(key);
  let val = null;
  if (raw) { try { val = JSON.parse(raw); } catch (e) { val = null; } }
  MARK_CACHE.set(key, val);
  return val;
}
function setOverride(lid, tid, verdict, why) {
  const key = overrideKey(lid, tid);
  safeSet(key, JSON.stringify({ verdict, ts: Date.now(), why: why || null }));
  MARK_CACHE.delete(key);   // (71)
  mirrorMatchToCRM(lid, tid);
}
function clearOverride(lid, tid) { const key = overrideKey(lid, tid); localStorage.removeItem(key); MARK_CACHE.delete(key); mirrorMatchToCRM(lid, tid); }
// CRM durability bridge (see the CRM store's own header comment). crm_match_status
// has exactly one free-text `status` column per (listing_id, tenant_id) pair — no
// separate slots for the mark's reason/snooze/note, or for the override verdict — so
// this composes the pair's whole locally-meaningful state into one short summary
// string rather than losing everything but the bare status label. It is a forward-only
// durability copy: nothing in this app parses it back out of the CRM snapshot, exactly
// like the CRM store's own composite text fields in other apps. Called from every
// mark/override write above (patchMark/clearMarkV/setOverride/clearOverride) — one
// choke point, so a future new writer of either key only has to remember to call it
// once, here.
function crmMatchStatusFor(lid, tid) {
  const mk = readMark(lid, tid), ov = readOverride(lid, tid);
  const parts = [];
  if (mk && mk.v) parts.push(mk.v);
  if (mk && mk.snooze_until) parts.push("snoozed:" + mk.snooze_until);
  if (ov && ov.verdict) parts.push("override:" + ov.verdict);
  return parts.join(" | ").slice(0, 60);
}
function mirrorMatchToCRM(lid, tid) { if (typeof CRM !== "undefined") CRM.setMatchStatus(lid, tid, crmMatchStatusFor(lid, tid)); }
// (71) Invalidation for a write this tab did NOT make. Every writer above drops
// its own key inline, which is sufficient while one tab is the only thing
// touching this origin's storage — but a second tab (a browser tab alongside
// the installed PWA, or simply the app opened twice) writes the same keys
// without this tab's in memory Map ever hearing about it, and the stale cached
// value would then survive every later render instead of being re-read. Wired
// to the `storage` event at init, which fires only in the OTHER tabs. `key` is
// null for a wholesale localStorage.clear(), so drop everything in that case.
// Invalidate only — never re-render from here: repainting under the hands of
// whoever is mid triage in this tab would be worse than a value that refreshes
// on their next interaction, and dropping the key restores exactly the
// pre-cache read-through behaviour.
function invalidateMarkCacheKey(key) {
  if (key == null) { MARK_CACHE.clear(); return; }
  // cbk_ also prefixes scratch/prefs/backup/offer keys, which never enter
  // MARK_CACHE — deleting one of those is a harmless no-op, and matching
  // broadly is the safer direction here.
  if (key.indexOf(MARK_PREFIX) === 0 || key.indexOf(OVERRIDE_PREFIX) === 0) MARK_CACHE.delete(key);
}

function readScratch() {
  try { const raw = localStorage.getItem(SCRATCH_KEY); const arr = raw ? JSON.parse(raw) : []; return Array.isArray(arr) ? arr : []; }
  catch (e) { return []; }
}
function writeScratch(arr) { return safeSet(SCRATCH_KEY, JSON.stringify(arr)); }
// Ids must be unique ACROSS devices, not just within one. "TMP" + (length + 1)
// gave the phone and the laptop the same TMP1 for two different people: on
// import the second one was silently dropped as a duplicate (a lead lost with
// no message), and worse, every mark keyed cbk_<listing>_TMP1 merged onto the
// wrong person — one tenant's decline reason, viewing date and note landing on
// somebody else's card, with their phone number on the draft button. A time
// plus random suffix collides only if two devices add a tenant in the same
// millisecond AND draw the same 4 random base36 characters. Ids already stored
// keep whatever they were given: nothing regenerates them.
const SCRATCH_IMPORT_CAP = 200, SCRATCH_ENTRY_MAX = 8000;
// Three independent parts, cheapest guarantee first: a per session salt (two
// devices differ), the clock (two sessions on one device differ), and a counter
// (two adds in the same millisecond differ by construction, not by luck).
// Base36 only, so the id stays safe in a cbk_<listing>_<tenant> key.
const SCRATCH_SALT = Math.random().toString(36).slice(2, 8);
let scratchSeq = 0;
function newScratchId() {
  return "TMP" + Date.now().toString(36) + SCRATCH_SALT + (++scratchSeq).toString(36);
}
function addScratchTenant(t) {
  const arr = readScratch();
  const taken = new Set(arr.map(x => x && x.id));
  let id = newScratchId();
  while (taken.has(id)) id = newScratchId();
  t.id = id;
  t._scratch = true;
  arr.push(t);
  writeScratch(arr);
  return t;
}

// ===================== PASS 2 — prefs, history log, offers, backups =====================
const PREFS_KEY = "cbk_prefs", HISTORY_KEY = "cbk_history";
const HISTORY_CAP = 1000;
const OFFER_PREFIX = "cbk_offer_";
const OFFER_STAGES = ["Holding deposit", "LOI", "Intake form complete", "Tenancy agreement", "Keys"];
// Phone numbers render in full, always (Winfred, 13 Aug 2026): he is the only
// user, the app is already behind Basic Auth, and masking was only ever
// display-only — the numbers sat in the payload either way. The masking and
// assistant-mode toggles were removed entirely, not just disabled.
const PREFS_DEFAULTS = { theme: null, density: "card", device_name: null, lock_code_hash: null, last_active: Date.now() };

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const p = raw ? JSON.parse(raw) : {};
    return Object.assign({}, PREFS_DEFAULTS, (p && typeof p === "object") ? p : {});
  } catch (e) { return Object.assign({}, PREFS_DEFAULTS); }
}
function savePrefs() { return safeSet(PREFS_KEY, JSON.stringify(PREFS)); }
let PREFS = loadPrefs();

// Cap is enforced on WRITE (not just on read) so a log can never grow past its
// bound, including when the stored array arrives already over cap from an
// import or an older build. A non array under the key is reset rather than
// pushed onto: arr.push would throw, the catch below would swallow it, and
// every later write would be lost in silence — for cbk_history that is the
// weekly funnel quietly reading zero forever.
function pushRingBuffer(key, cap, entry) {
  try {
    const raw = localStorage.getItem(key);
    let arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) arr = [];
    arr.push(entry);
    while (arr.length > cap) arr.shift();
    localStorage.setItem(key, JSON.stringify(arr));
  } catch (e) { /* storage full/unavailable — best effort only, never blocks the write it logs */ }
}
function readRingBuffer(key) {
  try { const raw = localStorage.getItem(key); const arr = raw ? JSON.parse(raw) : []; return Array.isArray(arr) ? arr : []; }
  catch (e) { return []; }
}
function pushMarkHistory(lid, tid, v) { if (v) pushRingBuffer(HISTORY_KEY, HISTORY_CAP, { lid, tid, v, ts: Date.now() }); }

// ---- phone display ----
// Phone numbers render in full, always — see PREFS_DEFAULTS' comment. Both
// functions keep their (kind, id, phone) signature even though kind/id are no
// longer read, so every call site across the file (worklist rows, rosters,
// dup lists, gallery, gcalLink) stays unchanged.
function displayPhone(kind, id, phone) { return phone || ""; }
// displayPhone() feeds plain text contexts (calendar link details);
// phoneSpanHtml below is the single place a phone renders as HTML, and
// escapes on its own.
function phoneSpanHtml(kind, id, phone) {
  if (!phone) return "";
  return esc(phone);
}

// ---- offer checklist (41) ----
function offerKey(lid, tid) { return OFFER_PREFIX + lid + "_" + tid; }
function readOffer(lid, tid) { try { const raw = localStorage.getItem(offerKey(lid, tid)); return raw ? JSON.parse(raw) : null; } catch (e) { return null; } }
function setOfferStage(lid, tid, stageIdx) {
  const ok = safeSet(offerKey(lid, tid), JSON.stringify({ stage: stageIdx, ts: Date.now() }));
  // Mirror onto the tenant's CRM entity stage — an offer in progress is a person
  // moving through the funnel, not a per-pair status, so this is the one write in
  // this file that targets CRM's `entity` op (patchMark/setOverride above target
  // the per-pair `match` op instead). "Keys" (the final stage) reads as closed_won.
  if (ok && typeof CRM !== "undefined") {
    const t = ALL_TENANTS.find(x => x.id === tid) || (DATA.all_tenants || []).find(x => x.id === tid);
    const subj = { kind: "tenant", id: tid, name: t && t.name, phone: t && t.phone };
    if (CRM.keyOf(subj)) CRM.setStage(subj, stageIdx >= OFFER_STAGES.length - 1 ? "closed_won" : "offer");
  }
  return ok;
}
function activeOffers() {
  const out = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k || k.indexOf(OFFER_PREFIX) !== 0) continue;
    const rest = k.slice(OFFER_PREFIX.length);
    const us = rest.indexOf("_");
    if (us === -1) continue;
    const lid = rest.slice(0, us), tid = rest.slice(us + 1);
    const o = readOffer(lid, tid);
    if (o && o.stage < OFFER_STAGES.length - 1) out.push({ lid, tid, stage: o.stage });
  }
  return out;
}

// ---- backup / export / import (47) ----
// Marks/overrides are kept keyed by their FULL localStorage key with the raw
// string value untouched (legacy bare string or JSON) — nothing is decoded
// then re-encoded, so import writes back byte for byte what export read.
// A mark key is cbk_<listingId>_<tenantId> and nothing else. Every other cbk_*
// key in this app (prefs, history, errors, scratch, offers, backups) shares
// that prefix, so "starts with cbk_" alone is NOT a safe test — import
// in particular must not be able to write cbk_prefs through the marks map.
function isMarkKey(k) {
  return !!k && k.indexOf(MARK_PREFIX) === 0 && k !== SCRATCH_KEY && k.indexOf(OFFER_PREFIX) !== 0
    && k !== PREFS_KEY && k !== HISTORY_KEY && k !== ERR_KEY && k.indexOf("cbk_backup_") !== 0;
}
// Offers travel in their own map, NOT through marks: isMarkKey() deliberately
// rejects cbk_offer_* so an import can never reach a reserved key, and leaving
// them out of the blob entirely meant a device merge silently dropped every
// offer in progress (holding deposit / LOI / tenancy agreement / keys) —
// exactly the state that matters most, resetting to "no offers" on the device
// Winfred imported into. Same for the mark history log the weekly funnel reads:
// marks merged but the funnel that counts them did not, so a laptop that
// imported the phone's day of work reported 0 contacted, 0 viewings.
function isOfferKey(k) { return !!k && k.indexOf(OFFER_PREFIX) === 0 && k.length > OFFER_PREFIX.length; }
function exportBlob() {
  const marks = {}, overrides = {}, offers = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k) continue;
    if (k.indexOf(OVERRIDE_PREFIX) === 0) { overrides[k] = localStorage.getItem(k); continue; }
    if (isOfferKey(k)) { offers[k] = localStorage.getItem(k); continue; }
    if (isMarkKey(k)) marks[k] = localStorage.getItem(k);
  }
  return {
    version: 2, exported_ts: new Date().toISOString(),
    marks, overrides, offers, scratch: readScratch(),
    history: readRingBuffer(HISTORY_KEY),
    // (item 1) cbkcrm_v1/cbkcrm_queue_v1 hold every stage, note and task Winfred
    // has recorded — with no backend configured (the normal mode) that data
    // exists ONLY in this browser profile, so an export that omits it is not a
    // backup of the app's state, just of the older mark layer. See CRM.exportState.
    crm: (typeof CRM !== "undefined") ? CRM.exportState() : null
  };
}
function tsOfRaw(raw) {
  if (!raw) return 0;
  if (raw[0] !== "{") return 0; // legacy bare string mark -> ts=0, always loses ties to a real timestamp
  // A non numeric ts (a hand edited or hostile blob carrying "9e99", {} or null)
  // must not decide the merge: anything that is not a finite number is treated
  // as no timestamp at all, so the comparison below stays a total order.
  try { const o = JSON.parse(raw); const n = o && o.ts; return (typeof n === "number" && isFinite(n)) ? n : 0; } catch (e) { return 0; }
}
// Merge policy: per key, latest ts wins for marks/overrides/offers (ties favor
// the import, since the user just explicitly asked to import). Scratch tenants
// merge by id, keeping the local record and re-keying an incoming one that
// collides — never clobber a local edit, never silently drop a person either.
// The mark history log merges through mergeLogInto below.
//
// Log merge: exact de-dupe on the record's own identity, then ts sort and the
// same cap the live writer enforces. Exact de-dupe is what makes importing the
// same blob twice (or re-importing after a two way sync) idempotent, so the
// weekly funnel cannot double count a mark both devices already know about.
function mergeLogInto(key, cap, incoming, identity) {
  if (!Array.isArray(incoming)) return 0;
  // A stored log can hold junk (older build, hand edit, partial write); drop
  // non objects before identity() reads a property off one of them.
  const local = readRingBuffer(key).filter(r => r && typeof r === "object");
  const seen = new Set(local.map(identity));
  let added = 0;
  incoming.forEach(r => {
    if (!r || typeof r !== "object") return;
    const id = identity(r);
    if (id == null || seen.has(id)) return;
    local.push(r); seen.add(id); added++;
  });
  local.sort((a, b) => (a.ts || 0) - (b.ts || 0));
  while (local.length > cap) local.shift();
  return safeSet(key, JSON.stringify(local)) ? added : 0;  // never report entries that were not persisted
}
function importBlob(blob) {
  const result = { marks: 0, overrides: 0, offers: 0, scratch: 0, history: 0, crm: null };
  if (!blob || typeof blob !== "object") return result;
  const marks = blob.marks || {};
  for (const k in marks) {
    if (!isMarkKey(k)) continue;   // never let an import reach cbk_prefs / a backup slot
    if (tsOfRaw(marks[k]) >= tsOfRaw(localStorage.getItem(k))) { if (safeSet(k, marks[k])) { result.marks++; MARK_CACHE.delete(k); } }
  }
  const overrides = blob.overrides || {};
  for (const k in overrides) {
    if (k.indexOf(OVERRIDE_PREFIX) !== 0) continue;
    if (tsOfRaw(overrides[k]) >= tsOfRaw(localStorage.getItem(k))) { if (safeSet(k, overrides[k])) { result.overrides++; MARK_CACHE.delete(k); } }
  }
  const offers = blob.offers || {};
  for (const k in offers) {
    if (!isOfferKey(k)) continue;
    if (tsOfRaw(offers[k]) >= tsOfRaw(localStorage.getItem(k))) { if (safeSet(k, offers[k])) result.offers++; }
  }
  if (Array.isArray(blob.scratch)) {
    const local = readScratch();
    const byId = new Map(local.map(t => [t && t.id, t]));
    // Bounded, shape checked and re-keyed on collision. Unbounded before: a
    // blob with thousands of entries both blew the storage quota and made
    // rebuildMatches() score every one of them against every listing. A
    // colliding id is re-keyed rather than dropped, so a scratch tenant added
    // on a device still running the old TMP<n> scheme is never silently lost —
    // and _scratch is forced on, so an imported record can never render as
    // though it came from the real database.
    blob.scratch.forEach(t => {
      if (!t || typeof t !== "object" || typeof t.id !== "string" || !t.id) return;
      if (result.scratch >= SCRATCH_IMPORT_CAP) return;
      // Serialize the candidate ALONE first: a deeply nested or circular entry
      // throws here, where it costs nothing, instead of inside writeScratch()
      // below — which would abort the whole import after the marks had already
      // been written and report it to the user as unreadable JSON.
      let ser;
      try { ser = JSON.stringify(t); } catch (e) { return; }
      if (!ser || ser.length > SCRATCH_ENTRY_MAX) return;
      const existing = byId.get(t.id);
      if (existing) {
        if (JSON.stringify(existing) === ser) return;  // same record, already here
        t = Object.assign({}, t, { id: newScratchId() });
      }
      t._scratch = true;
      local.push(t); byId.set(t.id, t); result.scratch++;
    });
    writeScratch(local);
  }
  result.history = mergeLogInto(HISTORY_KEY, HISTORY_CAP, blob.history, r => r.lid + ":" + r.tid + ":" + r.v + ":" + r.ts);
  // (item 1) merges cbkcrm_v1's stages/notes/tasks/match back in — see
  // CRM.importState for the merge policy (empty store -> replace, otherwise
  // fill gaps only, never overwrite).
  if (blob.crm && typeof CRM !== "undefined") result.crm = CRM.importState(blob.crm);
  return result;
}
function downloadJSON(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
const BACKUP_PREFIX = "cbk_backup_";
function writeAutoBackup() {
  // Real weekday (NOW_REAL_SGT), not TODAY — this must actually rotate day by
  // day as the user opens the app, independent of how stale the dataset is,
  // and it rotates on the Singapore day boundary, not whatever day the
  // device's own clock reads while Winfred is traveling.
  const wd = WEEKDAY_NAMES[NOW_REAL_SGT.getDay()];
  // Slot contents are deliberately slimmer than a manual export: seven rolling
  // copies of the 1000 entry history log would be several hundred KB of the
  // same quota whose exhaustion makes safeSet() drop a live mark. What a
  // backup exists to bring back is the state itself.
  const slot = exportBlob();
  delete slot.history;
  try { localStorage.setItem(BACKUP_PREFIX + wd, JSON.stringify(slot)); } catch (e) { /* quota — skip, not fatal */ }
}
// These seven rolling slots were written on every load and read by nothing:
// there was no way to get back into them from anywhere in the app, so they
// bought no safety at all while holding up to seven full copies of the state
// against the same storage quota whose exhaustion is what makes safeSet() drop
// a mark. Restoring is the same merge an import does, so a bad bulk action or a
// cleared key can be recovered from yesterday's slot.
function listBackups() {
  const out = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k || k.indexOf(BACKUP_PREFIX) !== 0) continue;
    const raw = localStorage.getItem(k);
    let blob = null;
    try { blob = JSON.parse(raw); } catch (e) { continue; }
    if (!blob || typeof blob !== "object") continue;
    out.push({ key: k, day: k.slice(BACKUP_PREFIX.length), ts: blob.exported_ts || "", marks: Object.keys(blob.marks || {}).length });
  }
  out.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  return out;
}
function restoreBackup(key) {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try { return importBlob(JSON.parse(raw)); } catch (e) { return null; }
}

// ---- undo (57) ----
function restoreMarks(priors) {
  priors.forEach(p => {
    const key = markKey(p.lid, p.tid);
    if (p.prior) { localStorage.setItem(key, JSON.stringify(p.prior)); MARK_CACHE.delete(key); }
    else clearMarkV(p.lid, p.tid);
  });
  render();
}
function capturePriors(matches) { return matches.map(m => ({ lid: m.l.id, tid: m.t.id, prior: readMark(m.l.id, m.t.id) })); }
function undoToast(msg, onUndo) {
  const old = $("#undotoast"); if (old) { clearTimeout(old._t); old.remove(); }
  const box = el("div", "wm undo-toast");
  box.id = "undotoast";
  // msg is built from fname(t.name) at every real call site (writeMarkUndoable,
  // openDeclineModal, openSnoozeModal, openSnoozedList, openDispatchDrawer's
  // unqueue) — a tenant name is data derived text, same as everywhere else in
  // this file, so it goes through esc() before it reaches innerHTML.
  // role=status/aria-live=polite live on this inner span (not box.setAttribute)
  // on purpose — this function is extracted and unit tested against a minimal
  // fake `document` (scoring.test.mjs) that only stubs innerHTML/className/id/
  // appendChild, not setAttribute, and baking the live-region markup into the
  // HTML string itself keeps that test's fake DOM sufficient.
  box.innerHTML = '<span role="status" aria-live="polite">' + esc(msg) + '</span>';
  const btn = el("button", "btn", "Undo");
  btn.onclick = () => { clearTimeout(box._t); box.remove(); onUndo(); };
  box.appendChild(btn);
  document.body.appendChild(box);
  box._t = setTimeout(() => box.remove(), 5000);
}
function writeMarkUndoable(lid, tid, patch, label) {
  const priors = [{ lid, tid, prior: readMark(lid, tid) }];
  patchMark(lid, tid, patch);
  undoToast(label, () => restoreMarks(priors));
  render();
}
function writeMarkClearUndoable(lid, tid, label) {
  const priors = [{ lid, tid, prior: readMark(lid, tid) }];
  clearMarkV(lid, tid);
  undoToast(label || "Cleared", () => restoreMarks(priors));
  render();
}
function bulkApplyMark(matches, patch, label) {
  if (!matches.length) return;
  const priors = capturePriors(matches);
  matches.forEach(m => patchMark(m.l.id, m.t.id, patch));
  undoToast(label, () => restoreMarks(priors));
  render();
}

// ===================== scoring integration (override aware) =====================
function effective(m) { const ov = readOverride(m.l.id, m.t.id); return ov ? Scoring.applyOverride(m.s, ov) : m.s; }
function worklistRank(m) { return m.s.total * Scoring.urgencyMult(m.t, TODAY); }
function isSnoozedNow(m) {
  const mk = readMark(m.l.id, m.t.id);
  if (!mk || !mk.snooze_until) return false;
  const until = Scoring.parseDate(mk.snooze_until);
  return !!until && Scoring.atMidnight(until) > Scoring.atMidnight(TODAY);
}
function declinedSimilarPenalty(t, l) {
  const matches = byTenant[t.id] || [];
  for (const m of matches) {
    if (m.l.id === l.id) continue;
    const mk = readMark(m.l.id, t.id);
    if (mk && mk.v === "Not interested" && Scoring.isSimilarListing(m.l, l)) return true;
  }
  return false;
}
// (60) thin bridge from live mark/cold state into the pure Scoring.nextBestAction
function nbaFor(m) {
  const eff = effective(m);
  return Scoring.nextBestAction({
    verdict: eff.verdict,
    mark: readMark(m.l.id, m.t.id) || {},
    isCold: isColdT(m.t),  // cold styling stays TODAY pinned, consistent with the row's own cold badge
    today: NOW_REAL_SGT,                 // but mk.ts/mk.viewing_date are REAL timestamps, and the "today" they compare
                                          // against has to be the real Singapore day too (not NOW_REAL's device-local
                                          // one) — otherwise this would silently never fire the nudge/collect verdict
                                          // chip once the dataset is even a little stale, or fire a day early/late
                                          // purely from the viewing device's own timezone
    needsInfoReasons: m.s.needsInfoReasons
  });
}
function nbaChipHtml(m) {
  const nba = nbaFor(m);
  if (!nba) return "";
  return '<span class="chip nba" data-nba="' + nba.code + '">→ ' + nba.label + '</span>';
}

// ===================== matching engine =====================
function rebuildMatches() {
  MARK_CACHE.clear();   // (71) safety net for any bulk mark/override write this dataset reload follows
  COLD_CACHE.clear();   // (73) a scratch tenant added since the last build gets its own answer
  ALL_TENANTS = (DATA.tenants || []).concat(readScratch());
  MATCHES = [];
  for (const l of (DATA.listings || [])) for (const t of ALL_TENANTS) MATCHES.push({ l, t, s: Scoring.score(l, t, TODAY) });
  byListing = {}; byTenant = {};
  for (const m of MATCHES) {
    (byListing[m.l.id] = byListing[m.l.id] || []).push(m);
    (byTenant[m.t.id] = byTenant[m.t.id] || []).push(m);
  }
  for (const k in byListing) byListing[k].sort((a, b) => b.s.total - a.s.total);
  for (const k in byTenant) byTenant[k].sort((a, b) => b.s.total - a.s.total);
}

// ===================== filters =====================
const F = () => ({ q: $("#q").value.trim().toLowerCase(), d: $("#fd").value, v: $("#fv").value, r: parseInt($("#fr").value) || 0, cold: $("#fc").checked, hide: $("#fh").checked });
function passFilter(m) {
  const f = F();
  if (isSnoozedNow(m)) return false;
  if (f.d && m.l.district !== f.d) return false;
  if (f.v && effective(m).verdict !== f.v) return false;
  if (f.r && m.l.rent_min && m.l.rent_min > f.r) return false;
  if (f.cold && isColdT(m.t)) return false;
  if (f.hide && getMarkV(m.l.id, m.t.id)) return false;
  if (f.q) {
    const hay = (m.t.name + " " + m.l.name + " " + m.l.district + " " + (AREA[m.l.district] || "") + " " + m.l.address + " " + (m.t.preferred_location || "") + " " + (m.t.phone || "")).toLowerCase();
    if (!hay.includes(f.q)) return false;
  }
  return true;
}
function passFilterListing(m) {
  const f = F();
  if (isSnoozedNow(m)) return false;
  if (f.v && effective(m).verdict !== f.v) return false;
  if (f.cold && isColdT(m.t)) return false;
  if (f.hide && getMarkV(m.l.id, m.t.id)) return false;
  if (f.q) { const hay = (m.t.name + " " + m.t.preferred_location + " " + m.t.district).toLowerCase(); if (!hay.includes(f.q)) return false; }
  return true;
}

// ===================== row rendering pieces =====================
function vchip(verdict) {
  if (verdict === "QUALIFIED") return '<span class="chip g">✅ Good fit</span>';
  if (verdict === "NEEDS_INFO") return '<span class="chip a" data-explain="1" style="cursor:pointer">❓ Ask a question</span>';
  return '<span class="chip r" data-explain="1" style="cursor:pointer">⛔ Not a fit</span>';
}
function scoreBar(p, total) {
  const seg = [["#2ecc71", p.budget], ["#5b8cff", p.location], ["#f5a623", p.lease], ["#9b8cff", p.movein], ["#7a8aa8", p.fresh]];
  // role=img treats the whole bar as one described unit rather than 5 mute,
  // unlabelled <i> segments — total is the caller's displayed score (falls
  // back to summing the parts if a caller does not have one on hand).
  const fitTotal = total != null ? total : (p.budget + p.location + p.lease + p.movein + p.fresh);
  return '<span class="sb" role="img" aria-label="' + esc("fit " + fitTotal + " of 100") + '" title="budget ' + p.budget + ' · location ' + p.location + ' · lease ' + p.lease + ' · move in ' + p.movein + ' · fresh ' + p.fresh + '">' + seg.map(s => '<i style="width:' + s[1] + 'px;background:' + s[0] + '"></i>').join('') + '</span>';
}
function coldChip(dc) { if (dc == null) return '<span class="chip">no contact date</span>'; if (dc <= 5) return '<span class="chip g">heard ' + dc + 'd ago</span>'; if (dc <= 30) return '<span class="chip a">cold ' + dc + 'd</span>'; return '<span class="chip r">stale ' + dc + 'd</span>'; }

function explainHtml(l, t, m, eff) {
  const parts = [];
  if (eff.verdict === "BLOCKED") {
    parts.push('<div class="hd">Why this is blocked</div>');
    if (m.s.gateHits.length) {
      m.s.gateHits.forEach(g => {
        // req_raw is verbatim landlord phrasing out of the database — escape it.
        const raw = l.req_raw && l.req_raw[g.sourceKey];
        parts.push('<div>' + esc(g.reason) + (raw ? (' <span class="src">— landlord preference: "' + esc(raw) + '"</span>') : '') + '</div>');
      });
    } else {
      parts.push('<div>' + esc(m.s.flags[0] || 'budget below landlord minimum') + '</div>');
    }
    if (m.s.near_miss) parts.push('<div style="margin-top:6px">Negotiable gap — budget is $' + esc(m.s.near_miss_gap) + ' short. Worth a call to the landlord.</div>');
  } else if (eff.verdict === "NEEDS_INFO") {
    parts.push('<div class="hd">What would unlock this</div>');
    (m.s.needsInfoReasons || []).forEach(r => {
      if (r === "budget") parts.push('<div>missing budget — one question unlocks this</div>');
      if (r === "location") parts.push('<div>no district preference on file that matches or is near this listing — one question unlocks this</div>');
    });
  } else {
    parts.push('<div class="hd">' + esc(t.name) + ' × ' + esc(l.name) + '</div>');
  }
  if (eff.overridden) parts.push('<div style="margin-top:6px" class="mut">Overridden to ' + esc(eff.verdict) + (eff.overrideWhy ? (' — ' + esc(eff.overrideWhy)) : '') + '</div>');
  parts.push(
    '<div class="ov">' +
      '<button class="btn" data-ov="QUALIFIED">Override to qualified</button>' +
      '<button class="btn" data-ov="BLOCKED">Force block</button>' +
      (eff.overridden ? '<button class="btn" data-ov="clear">Clear override</button>' : '') +
    '</div><input type="text" placeholder="why (optional)" data-ovwhy="1">'
  );
  return parts.join("");
}
// Row detail panels — verdict explain, draft preview, and the "collect
// verdict" preview — share one slot per row on purpose: there is no room on
// a 375px wide card for two stacked text blocks. Opening any one of them
// always closes whatever OTHER kind is left open first (clean swap);
// tapping the trigger for the SAME kind that is already open just closes it
// (plain toggle). Before this helper existed, toggleExplain and
// toggleDraftPreview each tested for a bare ".explain" class without
// checking which kind was actually showing (toggleDraftPreview's own panel
// also carried "explain" for shared styling) — so opening the explain panel
// while a draft preview was open silently deleted the draft preview and
// showed nothing, and the identical mistake existed between the draft
// preview and the collect-verdict preview (both keyed off ".draftpreview").
// Every opener below now routes through this one function so a stray class
// collision can't reintroduce either bug.
function swapRowPanel(row, kind, buildFn) {
  const existing = row.querySelector(".panelexplain, .paneldraft, .panelcollect");
  if (existing) {
    existing.remove();
    if (existing.classList.contains(kind)) return null; // was already open here -> toggle off
  }
  const panel = buildFn();
  panel.classList.add(kind);
  row.appendChild(panel);
  return panel;
}
function toggleExplain(row, l, t, m, eff) {
  const panel = swapRowPanel(row, "panelexplain", () => el("div", "explain", explainHtml(l, t, m, eff)));
  if (!panel) return;
  const whyInput = panel.querySelector("[data-ovwhy]");
  panel.querySelectorAll("[data-ov]").forEach(btn => {
    btn.onclick = () => {
      const v = btn.dataset.ov;
      if (v === "clear") clearOverride(l.id, t.id);
      else setOverride(l.id, t.id, v, whyInput.value.trim() || null);
      render();
    };
  });
}
function toggleDraftPreview(row, l, t) {
  // Cold check FIRST — a dead lead gets no draft text generated at all, on
  // every route into this function (Draft button, keyboard d, swipe right,
  // next best action chip). Previously only the Copy button was greyed while
  // the full message still rendered, which is what the rule forbids.
  if (coldBlocked(l, t)) {
    swapRowPanel(row, "paneldraft", () => el("div", "explain draftpreview", coldRefusalHtml(t)));
    return;
  }
  const en = draftFor(l, t);
  const panel = swapRowPanel(row, "paneldraft", () => {
    let html = '<div class="draftlabel">EN</div><div class="draftbox">' + esc(en) + '</div>';
    if (t.lang === "zh") {
      const zh = isCobroke(l) ? cobrokeDraft(l, t) : draftZH(l, t);
      html += '<div class="draftlabel">中文</div><div class="draftbox">' + esc(zh) + '</div>';
    }
    html += '<div class="acts" style="margin-top:6px"><button class="btn" data-cpen="1">Copy EN</button>' +
      (t.lang === "zh" ? '<button class="btn" data-cpzh="1">Copy 中文</button>' : '') + '</div>';
    return el("div", "explain draftpreview", html);
  });
  if (!panel) return;
  const cpen = panel.querySelector("[data-cpen]");
  if (cpen) cpen.onclick = (e) => { navigator.clipboard.writeText(en); e.target.textContent = "Copied"; };
  const cpzh = panel.querySelector("[data-cpzh]");
  if (cpzh) cpzh.onclick = (e) => { navigator.clipboard.writeText(isCobroke(l) ? cobrokeDraft(l, t) : draftZH(l, t)); e.target.textContent = "Copied"; };
}

// ===================== viewing pack (37) =====================
// Marking "Viewing booked" always asks for a date+time first (never an
// instant mark) so the pack below (calendar link, directions, landlord heads
// up, day of reminder) has something concrete to build from.
function openViewingBookedFlow(l, t) {
  const useSat = pairParity(l.id, t.id) === 0;
  // NOW_REAL_SGT for the same reason as draftSlot — this prefills a real
  // Singapore calendar date, not whatever calendar day the viewing device's
  // own clock happens to show.
  const suggested = l.fixed_viewing && l.fixed_viewing.weekday
    ? nextWeekdayDate(l.fixed_viewing.weekday, NOW_REAL_SGT, sgtNowMinutes(), fixedViewingStartMinutes(l.fixed_viewing))
    : nextWeekdayDate(useSat ? "sat" : "sun", NOW_REAL_SGT, sgtNowMinutes(), GENERIC_SLOT_START_MIN);
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>Viewing booked — ' + esc(t.name) + ' · ' + esc(l.name) + '</h3>' +
    '<div class="qfield"><label>Date</label><input type="date" data-vdate="1" value="' + esc(isoLocal(suggested || NOW_REAL_SGT)) + '"></div>' +
    '<div class="qfield"><label>Time</label><input type="time" data-vtime="1" value="15:00"></div>' +
    '<div class="foot"><button class="btn" data-cancel="1">Cancel</button><button class="btn p" data-confirm="1">Confirm</button></div></div>';
  mountOverlay(wrap);
  wrap.querySelector("[data-confirm]").onclick = () => {
    const d = wrap.querySelector("[data-vdate]").value, tm = wrap.querySelector("[data-vtime]").value;
    if (!d) return;
    wrap.remove();
    writeMarkUndoable(l.id, t.id, { v: "Viewing booked", viewing_date: d }, fname(t.name) + " — viewing booked");
    openViewingPack(l, t, d, tm);
  };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
// dateStr/timeStr are a plain Singapore wall clock date+time the user picked
// (the viewing itself happens in Singapore regardless of where this link is
// generated from) — encoded here WITHOUT a "Z" suffix plus an explicit
// &ctz=Asia/Singapore, which Google's render endpoint documents as "interpret
// these local date/times in the given zone", not UTC and not the browser's
// own zone. That is deliberate and correct: a "Z" suffix would mean UTC, and
// omitting ctz entirely would fall back to the viewer's own Google account
// zone — either way a 7pm SGT viewing could render as 7pm somewhere else.
function gcalLink(l, t, dateStr, timeStr) {
  const parts = (timeStr || "15:00").split(":").map(Number);
  const hh = parts[0], mm = parts[1];
  const pad = (n) => String(n).padStart(2, "0");
  const start = dateStr.replace(/-/g, "") + "T" + pad(hh) + pad(mm) + "00";
  const endMins = hh * 60 + mm + 30;
  // A viewing starting late enough that +30min crosses midnight (e.g. 23:45)
  // must roll the END date forward too — otherwise start/end carry the SAME
  // calendar day with end's clock time EARLIER than start's, which Google
  // Calendar reads as an event that ends before it begins.
  const dayRoll = Math.floor(endMins / 1440);
  const eh = Math.floor(endMins / 60) % 24, em = endMins % 60;
  const [Y, M, D] = dateStr.split("-").map(Number);
  const endDateStr = dayRoll ? isoLocal(new Date(Y, M - 1, D + dayRoll)) : dateStr;
  const end = endDateStr.replace(/-/g, "") + "T" + pad(eh) + pad(em) + "00";
  const phoneBit = t.phone ? (" " + displayPhone("tenant", t.id, t.phone)) : "";
  const details = "Viewing with " + fname(t.name) + phoneBit;
  return "https://www.google.com/calendar/render?action=TEMPLATE&text=" + encodeURIComponent("Viewing — " + l.name) +
    "&dates=" + start + "/" + end + "&details=" + encodeURIComponent(details) + "&location=" + encodeURIComponent(l.address || areaName(l)) + "&ctz=Asia%2FSingapore";
}
function openViewingPack(l, t, dateStr, timeStr) {
  const parts = (timeStr || "15:00").split(":").map(Number);
  const slotLabel = shortDate(Scoring.parseDate(dateStr)) + " " + fmtTime(parts[0], parts[1]);
  const cold = coldBlocked(l, t);
  const landlordDraft = landlordHeadsUpDraft(l, t, slotLabel);
  const reminderDraft = dayOfReminderDraft(l, t, slotLabel);
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>Viewing pack — ' + esc(t.name) + '</h3>' +
    '<div class="acts">' +
      '<a class="btn" target="_blank" rel="noopener" href="' + escUrl(gcalLink(l, t, dateStr, timeStr)) + '">📅 Add to Google Calendar</a>' +
      '<a class="btn" target="_blank" rel="noopener" href="' + escUrl(mapLink(l)) + '">📍 Directions</a>' +
    '</div>' +
    // Landlord half is never gated by the cold rule — the rule is about dead
    // tenant leads only.
    '<div class="draftlabel">Landlord heads up</div><div class="draftbox">' + esc(landlordDraft) + '</div>' +
    '<div class="acts">' + (l.phone
      ? ('<button class="btn" data-cp="0">Copy</button>' + waButtonHtml(l.phone, landlordDraft, "WhatsApp", false))
      : '<span class="mut">no phone on file</span>') + '</div>' +
    (cold
      ? ('<div class="draftlabel">Day of reminder (tenant)</div>' + coldRefusalHtml(t))
      : ('<div class="draftlabel">Day of reminder (tenant)</div><div class="draftbox">' + esc(reminderDraft) + '</div>' +
         '<div class="acts"><button class="btn" data-cp="1">Copy</button>' + waButtonHtml(t.phone, reminderDraft, "WhatsApp", false) + '</div>')) +
    '<div class="foot"><button class="btn" data-cancel="1">Close</button></div></div>';
  mountOverlay(wrap);
  const cp0 = wrap.querySelector('[data-cp="0"]'); if (cp0) cp0.onclick = (e) => { navigator.clipboard.writeText(landlordDraft); e.target.textContent = "Copied"; };
  const cp1 = wrap.querySelector('[data-cp="1"]'); if (cp1) cp1.onclick = (e) => { navigator.clipboard.writeText(reminderDraft); e.target.textContent = "Copied"; };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
// `cold` drops "Queued" from the options: queueing is an outbound send action
// (it feeds the morning dispatch export), so the dead lead rule applies to it the
// same way it applies to WhatsApp, Call and Draft.
function markSelectHtml(st, cold, tname) {
  const opts = ["Contacted", "Viewing booked", "Not interested"].concat(cold ? [] : ["Queued"]);
  // aria-label, not a wrapping <label> — this select sits inline in a compact
  // action row with no room for visible label text, and its options already
  // say what they do; the label just names WHO the mark is for.
  let html = '<select class="btn mk" data-mk="1" aria-label="Mark status for ' + esc(tname || "tenant") + '"><option value="">Mark…</option>';
  opts.forEach(o => { html += '<option' + (st === o ? ' selected' : '') + '>' + esc(o) + '</option>'; });
  html += '<option value="__clr">Clear</option></select>';
  return html;
}
function rowActionsHtml(l, t, cold) {
  const cobroke = isCobroke(l);
  const hasTarget = cobroke ? !!l.phone : !!t.phone;
  const label = cobroke ? "WhatsApp co-broke agent" : "WhatsApp draft";
  // One source of truth for the rule — matchRow passes what coldBlocked() said,
  // and this function must not re-derive it differently.
  const blocked = cold && !cobroke;
  let waBtn;
  if (!hasTarget) waBtn = '<span class="btn mut">' + (cobroke ? "no phone on file for the co-broke agent" : "no phone on file") + '</span>';
  else if (blocked) waBtn = '<span class="btn disabled" title="' + coldTitle() + '">' + esc(label) + '</span>';
  else waBtn = '<a class="btn w" target="_blank" rel="noopener noreferrer" href="' + escUrl(waLink(l, t)) + '">' + esc(label) + '</a>';
  // Draft is an outbound action too: greyed for a cold tenant, exactly like
  // WhatsApp and Call beside it. toggleDraftPreview() refuses independently so
  // the keyboard d / swipe right routes are covered even though they never
  // touch this button.
  const draftBtn = blocked
    ? '<span class="btn disabled" title="' + coldTitle() + '">Draft</span>'
    : '<button class="btn" data-draft="1">Draft</button>';
  const callTarget = cobroke ? l.phone : t.phone;
  let callBtn = "";
  if (callTarget) {
    if (blocked) callBtn = '<span class="btn disabled" title="' + coldTitle() + '">Call</span>';
    else callBtn = '<a class="btn" href="' + escUrl("tel:" + normPhone(callTarget)) + '">Call</a>';
  }
  return waBtn + draftBtn + callBtn + '<a class="btn" target="_blank" rel="noopener" href="' + escUrl(mapLink(l)) + '">Map</a>';
}
function wireRowEvents(row, l, t, m, eff) {
  const mkSel = row.querySelector('[data-mk]');
  if (mkSel) mkSel.onchange = (e) => {
    const v = e.target.value;
    if (v === "__clr") { writeMarkClearUndoable(l.id, t.id, fname(t.name) + " mark cleared"); return; }
    if (v === "Not interested") { openDeclineModal(l, t); return; }
    if (v === "Viewing booked") { openViewingBookedFlow(l, t); return; }
    writeMarkUndoable(l.id, t.id, { v }, fname(t.name) + " marked " + v);
  };
  const draftBtn = row.querySelector('[data-draft]');
  if (draftBtn) draftBtn.onclick = () => toggleDraftPreview(row, l, t);
  const cb = row.querySelector('[data-batch]');
  if (cb) cb.onchange = () => toggleBatchSelect(t.id, cb.checked);
  const explainTrigger = row.querySelector('[data-explain]');
  if (explainTrigger) explainTrigger.onclick = () => toggleExplain(row, l, t, m, eff);
  const dupTrigger = row.querySelector('[data-dupgroup]');
  if (dupTrigger) dupTrigger.onclick = (e) => { e.stopPropagation(); toggleDupGroupList(row, t.dup_group); };
}

function matchRow(m, showListing, opts) {
  opts = opts || {};
  const l = m.l, t = m.t;
  const eff = effective(m);
  const mk = readMark(l.id, t.id) || {};
  const st = mk.v || "";
  const blocked = eff.verdict === "BLOCKED";
  const cold = coldBlocked(l, t);
  const declinedSimilar = st !== "Not interested" && declinedSimilarPenalty(t, l);
  const displayScore = declinedSimilar ? Math.max(0, m.s.total - Scoring.LOOKALIKE_PENALTY) : m.s.total;

  const row = el("div", "row" + (st === "Not interested" || st === "Contacted" ? " done" : "") + (blocked ? " blk" : "") + (opts.focused ? " focus" : ""));
  row.dataset.l = l.id; row.dataset.t = t.id;

  const badges = [];
  if (t._scratch) badges.push('<span class="badge scratch">scratch</span>');
  if (eff.overridden) badges.push('<span class="badge overridden">overridden</span>');
  if (declinedSimilar) badges.push('<span class="badge declined">declined similar</span>');
  if (t.dup_group != null) badges.push(dupGroupBadgeHtml(t));   // (12) one grouped chip, never N loose badges
  if (l.reconfirm_due) badges.push('<span class="badge overridden">reconfirm — 14d+</span>'); // (16)

  const availFromNote = (showListing && l.available_from) ? (' · vacant from ' + esc(shortDate(Scoring.parseDate(l.available_from)))) : ''; // (24)
  const head = showListing
    ? '<span class="nm">' + esc(t.name) + '</span> <span class="mut">→ ' + esc(l.name) + ' · ' + esc(l.district) + ' · ' + esc(rentTxt(l)) + availFromNote + '</span>' + (l.availability === "Offer pending" ? ' <span class="chip a">offer pending, hold</span>' : '')
    : '<span class="nm">' + esc(t.name) + '</span> <span class="mut">' + esc(t.pass_type || '') + ' ' + esc(t.nationality || '') + '</span>';

  // (75) label wrap gives the checkbox a real >=44px tap target (the glyph
  // itself stays a normal-looking 18px so a chain of them doesn't look
  // oversized next to the row's chips) without touching hit areas for any
  // of the row's other controls (label wrapping is scoped to this one input,
  // unlike a click handler on .rtop/.row which would have to individually
  // exclude every other trigger already living in the same row).
  const checkboxHtml = opts.checkbox ? ('<label class="cbwrap"><input type="checkbox" class="rowcheck" data-batch="1" aria-label="Select ' + esc(t.name) + ' for batch viewing"' + (batchSelection.has(t.id) ? ' checked' : '') + '></label>') : '';
  const nba = nbaChipHtml(m); // (60)

  row.innerHTML =
    '<div class="rtop">' + checkboxHtml + head + ' ' + vchip(eff.verdict) + scoreBar(m.s.parts, displayScore) + '<span class="sc">' + displayScore + '</span></div>' +
    (badges.length || nba ? ('<div class="rtop" style="margin-top:4px">' + badges.join(' ') + (nba ? (' ' + nba) : '') + '</div>') : '') +
    '<div class="rtop" style="margin-top:5px">' +
      '<span class="chip">budget ' + esc(t.budget || t.budget_max || '?') + '</span>' +
      genderChip(t) +
      '<span class="chip">pax ' + esc(t.pax || '?') + '</span>' +
      '<span class="chip">lease ' + esc(t.lease_months || '?') + 'mo</span>' +
      '<span class="chip">move ' + esc(t.move_in || '?') + '</span>' +
      '<span class="chip">' + esc(t.district || '?') + (t.preferred_location ? (' · ' + esc(String(t.preferred_location).slice(0, 28))) : '') + '</span>' +
      coldChip(m.s.dc) + (st ? ('<span class="chip a">' + esc(st) + '</span>') : '') +
    '</div>' +
    (m.s.near_miss && blocked ? ('<div class="gap" style="color:#e39a1c;font-style:normal">Negotiable gap — $' + esc(m.s.near_miss_gap) + ' short of landlord\'s min</div>') : '') +
    (blocked
      ? ('<div class="gap" style="color:#ff6b78;font-style:normal">⛔ Do not offer this room to ' + esc(fname(t.name)) + ' — ' + esc(m.s.flags[0] || 'landlord requirement conflict') + '</div>'
        + '<div class="acts"><a class="btn" target="_blank" rel="noopener" href="' + escUrl(mapLink(l)) + '">Map</a>' + markSelectHtml(st, cold, t.name) + crmBtn("tenant", t) + '</div>')
      : ((m.s.flags.length ? ('<div class="gap">⚑ ' + esc(m.s.flags.join(' · ')) + '</div>') : '')
        + (t.phone ? ('<div class="mk" style="margin-top:6px">→ you will message <b>' + esc(t.name) + '</b> · ' + phoneSpanHtml("tenant", t.id, t.phone) + '</div>') : '')
        + '<div class="acts">' + rowActionsHtml(l, t, cold) + markSelectHtml(st, cold, t.name) + crmBtn("tenant", t) + '</div>'));

  wireRowEvents(row, l, t, m, eff);
  wireNbaChip(row, l, t);
  return row;
}
// (12) real data can have a dup_group of 12+ tenants sharing a phone — always
// render ONE chip with the count, never one badge per member. Tap expands.
function dupGroupBadgeHtml(t) {
  const n = ALL_TENANTS.filter(x => x.dup_group === t.dup_group).length;
  return '<span class="badge declined" data-dupgroup="' + esc(t.dup_group) + '" style="cursor:pointer">possible duplicate (' + esc(n) + ')</span>';
}
function toggleDupGroupList(row, gid) {
  const existing = row.querySelector(".dup-list");
  if (existing) { existing.remove(); return; }
  const members = ALL_TENANTS.filter(x => x.dup_group === gid);
  const html = members.map(x => '<div>' + esc(x.name) + ' · ' + (x.phone ? phoneSpanHtml("tenant", x.id, x.phone) : 'no phone') + '</div>').join("");
  const box = el("div", "dup-list", html);
  row.appendChild(box);
}
function wireNbaChip(row, l, t) {
  const chip = row.querySelector('[data-nba]');
  if (!chip) return;
  chip.onclick = () => { chip.dataset.nba === "collect_verdict" ? toggleVerdictCollectPreview(row, l, t) : toggleDraftPreview(row, l, t); };
}
function toggleVerdictCollectPreview(row, l, t) {
  if (coldBlocked(l, t)) { swapRowPanel(row, "panelcollect", () => el("div", "explain draftpreview", coldRefusalHtml(t))); return; }
  const text = verdictCollectDraft(t, l);
  const panel = swapRowPanel(row, "panelcollect", () => {
    const html = '<div class="draftlabel">Collect verdict</div><div class="draftbox">' + esc(text) + '</div>' +
      '<div class="acts" style="margin-top:6px"><button class="btn" data-cpv="1">Copy</button></div>';
    return el("div", "explain draftpreview", html);
  });
  if (!panel) return;
  const cp = panel.querySelector('[data-cpv]'); if (cp) cp.onclick = (e) => { navigator.clipboard.writeText(text); e.target.textContent = "Copied"; };
}

function renderNearMissSection(container, matches, showListing) {
  const nm = matches.filter(m => m.s.near_miss && effective(m).verdict === "BLOCKED" && !isSnoozedNow(m));
  if (!nm.length) return;
  container.appendChild(el("div", "section-hd", "Negotiable gap"));
  nm.forEach(m => container.appendChild(matchRow(m, showListing)));
}

// ===================== top level render =====================
// (48) Hard expiry, three states. Green stays a quiet inline dot (no banner);
// amber/red get a full width banner.
// (item 2) red tier banner announces assertively — but only the first time it
// renders. render() re-runs this on every mark/filter/tab change, and #sub's
// innerHTML is rebuilt wholesale each time, so a plain always-on aria-live
// would re-interrupt a screen reader user on every single click for the rest
// of the session. AGE_BANNER_ANNOUNCED flips true the first time (real) red
// tier text is produced and every render after that omits the live-region
// markup — the banner still shows, it just stops re-announcing itself.
let AGE_BANNER_ANNOUNCED = false;
function dataAgeBannerHtml() {
  const t = Scoring.dataAgeTier(DATA.generated_ts || DATA.generated, NOW_REAL_SGT);
  if (t.tier === "red") {
    const live = AGE_BANNER_ANNOUNCED ? "" : ' role="alert" aria-live="assertive"';
    AGE_BANNER_ANNOUNCED = true;
    return '<div class="wm banner-red"' + live + '>🔴 Data is ' + esc(t.days) + ' days old — rebuild: <code>python3 scripts/matchmaker/build.py</code>.</div>';
  }
  if (t.tier === "amber") return '<div class="wm banner-amber">🟡 Data is ' + esc(t.days) + ' days old (from ' + esc(DATA.generated) + '). Ask Winfred to refresh it so you are working today\'s rooms and tenants.</div>';
  if (t.tier === "green") return '<span class="agedot" title="data is ' + esc(t.days) + ' day(s) old">🟢 up to date</span>';
  return "";
}
// (56) real header height, for the sticky context bar's CSS offset (see
// .sticky-ctx in styles.css) and (item 4, cycle 8) for .row's scroll-margin-top,
// which needs the SAME live value so a Tab/j/k focused row scrolls clear of the
// actual sticky header rather than a stale one. Originally measured once at
// init plus on window resize only — that missed every other way the header's
// OWN height can change (KPI digit counts, filter option counts, chip wrapping
// from state changes), so --header-h could go stale mid session with no resize
// event to catch it. render() now re-measures on every call, which happens
// after exactly those state changes; offsetHeight is cheap for one element and
// render() is already the app's single per-change checkpoint.
function measureHeaderHeight() {
  const h = document.querySelector("header");
  if (h) document.documentElement.style.setProperty("--header-h", h.offsetHeight + "px");
}
function render() {
  $("#sub").innerHTML = "Priority: availability → location → price → landlord requirements   ·   data " + esc(DATA.generated) + " " + dataAgeBannerHtml();
  $("#kpis").innerHTML =
    '<div class="kpi"><b>' + (DATA.listings || []).length + '</b> available listings</div>' +
    '<div class="kpi"><b>' + ALL_TENANTS.length + '</b> still looking</div>' +
    '<div class="kpi"><b>' + MATCHES.filter(m => effective(m).verdict === "QUALIFIED").length + '</b> qualified matches</div>';
  ["work", "pipeline", "listing", "tenant", "whole", "stats", "landlords", "alltenants", "sales", "revival"].forEach(v => { const e = $("#" + v); if (e) e.style.display = v === view ? ((v === "listing" || v === "tenant") ? "grid" : "block") : "none"; });
  document.querySelectorAll("#tabs .tab").forEach(tb => {
    const on = tb.dataset.v === view;
    tb.classList.toggle("on", on);
    tb.setAttribute("aria-selected", on ? "true" : "false");
  });
  if (view === "work") renderWork(); else renderTriageBar(null);
  if (view === "pipeline") renderPipeline();
  if (view === "listing") renderListingRail();
  if (view === "tenant") renderTenantRail();
  if (view === "whole") renderWholeUnit();
  if (view === "stats") renderStats();
  if (view === "landlords") renderLandlordsRoster();
  if (view === "alltenants") renderAllTenantsRoster();
  if (view === "sales") renderSalesRoster();
  if (view === "revival") renderRevival();
  CRM.paint();
  $("#legend").innerHTML = "Score = budget 30 + location 25 + lease 15 + move in 15 + freshness 15 (urgency and MRT adjacency can add a little more, capped at 100). ⚑ flags are landlord preference gates (gender, ethnicity, pax) or budget/lease gaps — a red conflict still shows so you can judge, it is not auto hidden. " +
    "WhatsApp opens a pre filled draft you send yourself (never auto sent). Tenants quiet over 30 days have WhatsApp, draft copy and call turned off — landlord and co-broke contact is never turned off. Mark status is saved on this device only and never edits the databases. " +
    (CRM.mode === "local" ? "The 🗂 CRM drawer and Pipeline tab are saved on this device only — no cloud backend is configured." : "The 🗂 CRM drawer and Pipeline tab sync to your private CRM database and survive a rebuild.") +
    " PDPA: keep this file private.";
  const sc = $("#snoozechip"); if (sc) sc.innerHTML = 'Snoozed <span class="cnt">' + snoozedActive().length + '</span>';
  const dc = $("#dispatchchip"); if (dc) dc.innerHTML = 'Dispatch <span class="cnt">' + queuedMatches().length + '</span>';
  updateFacetedCounts();
  measureHeaderHeight();   // (56)/(item 4) re-measure after every header content change, not just window resize
}
function helpStrip() {
  const wrap = el("div", "");
  wrap.innerHTML = '<div class="help"><b>How to use:</b> ① Pick the top match &nbsp; ② Read the details and any ⚑ flag &nbsp; ③ Tap <b>WhatsApp</b> to open a ready message — <b>you</b> press send. This tool never messages anyone by itself and never changes your databases.</div>' + deltaStripHtml();
  const dtoggle = wrap.querySelector('[data-deltatoggle]');
  if (dtoggle) dtoggle.onclick = () => { const d = wrap.querySelector('[data-deltadetail]'); if (d) d.style.display = d.style.display === "none" ? "block" : "none"; };
  renderVerdictCards(wrap);   // (38) viewed, past date, still marked "Viewing booked" -> collect verdict
  renderOfferSection(wrap);   // (41) offer checklists in progress
  return wrap;
}

// (17) delta strip — collapsed one liner that expands to the detail lists.
function deltaStripHtml() {
  const d = DATA.delta;
  if (!d) return "";
  const nt = (d.new_tenant_ids || []).length, nl = (d.new_listing_ids || []).length;
  const gone = (d.gone_listings || []).length, ac = (d.availability_changes || []).length;
  if (!nt && !nl && !gone && !ac) return '<div class="wm">Since ' + esc(d.prev_generated) + ': nothing changed.</div>';
  let html = '<div class="wm" data-deltatoggle="1" style="cursor:pointer">🆕 since ' + esc(d.prev_generated) + ': ' + esc(nt) + ' new tenants, ' + esc(nl) + ' new listings, ' + esc(ac) + ' availability changes — tap for detail</div>';
  html += '<div class="help" data-deltadetail="1" style="display:none">' +
    (nt ? ('<div>New tenants: ' + esc((DATA.tenants || []).filter(t => (d.new_tenant_ids || []).indexOf(t.id) !== -1).map(t => t.name).join(", ")) + '</div>') : '') +
    (nl ? ('<div>New listings: ' + esc((DATA.listings || []).filter(l => (d.new_listing_ids || []).indexOf(l.id) !== -1).map(l => l.name).join(", ")) + '</div>') : '') +
    (gone ? ('<div>Gone: ' + esc((d.gone_listings || []).map(g => g.name || g.id).join(", ")) + '</div>') : '') +
    (ac ? ('<div>Availability changes: ' + esc((d.availability_changes || []).map(c => c.id + " " + c.from + " to " + c.to).join("; ")) + '</div>') : '') +
    '</div>';
  return html;
}
// (38) pairs whose viewing date has really passed (real Singapore wall clock,
// not TODAY, and not the viewing device's own day — see NOW_REAL_SGT's
// comment) but the mark still says "Viewing booked".
function pairPassedViewing(m) {
  const mk = readMark(m.l.id, m.t.id);
  if (!mk || mk.v !== "Viewing booked" || !mk.viewing_date) return false;
  const vd = Scoring.parseDate(mk.viewing_date);
  return !!vd && Scoring.atMidnight(vd) < Scoring.atMidnight(NOW_REAL_SGT);
}
function renderVerdictCards(container) {
  const pending = MATCHES.filter(pairPassedViewing);
  if (!pending.length) return;
  container.appendChild(el("div", "section-hd", "Viewed — collect verdict"));
  pending.forEach(m => {
    const mk = readMark(m.l.id, m.t.id) || {};
    const row = el("div", "row");
    row.innerHTML = '<div class="rtop"><span class="nm">' + esc(m.t.name) + '</span><span class="mut"> viewed ' + esc(m.l.name) + ' on ' + esc(mk.viewing_date) + '</span></div>' +
      '<div class="acts"><button class="btn p" data-yes="1">Interested — start offer</button><button class="btn" data-no="1">Not interested</button></div>';
    row.querySelector('[data-yes]').onclick = () => { setOfferStage(m.l.id, m.t.id, 0); writeMarkUndoable(m.l.id, m.t.id, { v: "Contacted", note: "interested after viewing" }, fname(m.t.name) + " — interested, offer started"); };
    row.querySelector('[data-no]').onclick = () => openDeclineModal(m.l, m.t);
    container.appendChild(row);
  });
}
// (41) offer checklist — click a stage to jump to it (marks everything up to
// and including it done); "Keys" (the final stage) drops off this list.
function renderOfferSection(container) {
  const offers = activeOffers();
  if (!offers.length) return;
  container.appendChild(el("div", "section-hd", "Offers in progress"));
  offers.forEach(o => {
    const l = (DATA.listings || []).find(x => x.id === o.lid), t = ALL_TENANTS.find(x => x.id === o.tid);
    if (!l || !t) return;
    const row = el("div", "row offer-checklist");
    const stagesHtml = OFFER_STAGES.map((s, i) => '<span class="stage' + (i <= o.stage ? ' done' : '') + '" data-stage="' + i + '">' + esc(s) + '</span>').join('');
    row.innerHTML = '<div class="rtop"><span class="nm">' + esc(t.name) + '</span><span class="mut"> · ' + esc(l.name) + '</span></div><div class="stages">' + stagesHtml + '</div>';
    row.querySelectorAll('[data-stage]').forEach(s => s.onclick = () => { setOfferStage(o.lid, o.tid, +s.dataset.stage); render(); });
    container.appendChild(row);
  });
}

// (55) faceted counts — every filter control shows how many rows it would
// leave, given the OTHER filters currently applied. facetCount(base,
// overrides) below is the reference, one-facet-at-a-time definition — kept
// as-is, unused in the hot path but documenting exactly what each bucket
// below must equal. It is no longer called from updateFacetedCounts(): with
// ~14 districts + 4 verdicts + cold + hide = ~20 options, calling it once per
// option meant ~20 full sweeps of MATCHES on every single render() (every tab
// switch, every filter keystroke, every mark write) — measured at 16-23ms/call
// even with (71)'s mark-read cache in place, ahead of the 1960-pair scoring
// pass itself and the single largest interaction cost in the app.
function facetCount(base, overrides) {
  const f = Object.assign({}, base, overrides);
  let n = 0;
  for (const m of MATCHES) {
    if (isSnoozedNow(m)) continue;
    if (f.d && m.l.district !== f.d) continue;
    if (f.v && effective(m).verdict !== f.v) continue;
    if (f.r && m.l.rent_min && m.l.rent_min > f.r) continue;
    if (f.cold && isColdT(m.t)) continue;
    if (f.hide && getMarkV(m.l.id, m.t.id)) continue;
    if (f.q) {
      const hay = (m.t.name + " " + m.l.name + " " + m.l.district + " " + (AREA[m.l.district] || "") + " " + m.l.address + " " + (m.t.preferred_location || "") + " " + (m.t.phone || "")).toLowerCase();
      if (hay.indexOf(f.q) === -1) continue;
    }
    n++;
  }
  return n;
}
const VERDICT_LABELS = { "": "All verdicts", QUALIFIED: "Qualified", NEEDS_INFO: "Needs info", BLOCKED: "Has conflict" };
// (72) single pass over MATCHES computing every bucket facetCount() would —
// each district option's count, each verdict option's count, the cold count
// and the hide count — together in one sweep instead of ~20 separate sweeps.
// district/verdict select options never carry value="" except the hardcoded
// "All districts"/"All verdicts" entries (dynamic options are filtered
// Boolean at init — see the district <select> populate loop), so distTotal/
// verdTotal (every match that clears the OTHER active filters, d/v itself
// unconstrained) is exactly what facetCount(base, {d:""}) / {v:""} returns.
function updateFacetedCounts() {
  const base = F();
  const distCounts = {}, verdCounts = {};
  let distTotal = 0, verdTotal = 0, coldCount = 0, hideCount = 0;
  for (const m of MATCHES) {
    if (isSnoozedNow(m)) continue;
    if (base.r && m.l.rent_min && m.l.rent_min > base.r) continue;
    if (base.q) {
      const hay = (m.t.name + " " + m.l.name + " " + m.l.district + " " + (AREA[m.l.district] || "") + " " + m.l.address + " " + (m.t.preferred_location || "") + " " + (m.t.phone || "")).toLowerCase();
      if (hay.indexOf(base.q) === -1) continue;
    }
    const verdict = effective(m).verdict;
    const isCold = isColdT(m.t);
    const markV = getMarkV(m.l.id, m.t.id);
    // "Ok at base" = would this match still pass if THIS dimension were left
    // at whatever the user currently has set, i.e. every dimension except the
    // one a given facet is enumerating over — mirrors facetCount's f.X checks.
    const vOkBase = !base.v || verdict === base.v;
    const coldOkBase = !base.cold || !isCold;
    const hideOkBase = !base.hide || !markV;
    const dOkBase = !base.d || m.l.district === base.d;
    if (vOkBase && coldOkBase && hideOkBase) { distCounts[m.l.district] = (distCounts[m.l.district] || 0) + 1; distTotal++; }
    if (dOkBase && coldOkBase && hideOkBase) { verdCounts[verdict] = (verdCounts[verdict] || 0) + 1; verdTotal++; }
    // cold facet mirrors facetCount(base,{cold:true}): "if (f.cold && isCold)
    // continue" excludes COLD rows once the flag is forced on, so the count
    // shown is survivors — i.e. NOT cold — not the cold ones themselves.
    if (dOkBase && vOkBase && hideOkBase && !isCold) coldCount++;
    if (dOkBase && vOkBase && coldOkBase && !markV) hideCount++;
  }
  const fd = $("#fd");
  if (fd) [...fd.options].forEach(opt => {
    if (!opt._label) opt._label = opt.value ? opt.textContent : "All districts";
    opt.textContent = opt._label + " (" + (opt.value ? (distCounts[opt.value] || 0) : distTotal) + ")";
  });
  const fv = $("#fv");
  if (fv) [...fv.options].forEach(opt => {
    opt.textContent = (VERDICT_LABELS[opt.value] || opt.textContent) + " (" + (opt.value ? (verdCounts[opt.value] || 0) : verdTotal) + ")";
  });
  const fc = $("#fccount"); if (fc) fc.textContent = "(" + coldCount + ")";
  const fh = $("#fhcount"); if (fh) fh.textContent = "(" + hideCount + ")";
}

// ===================== worklist + triage mode =====================
function renderWork() {
  const box = $("#work"); box.innerHTML = "";
  box.appendChild(helpStrip());
  const seen = new Set();
  const rows = MATCHES.filter(m => effective(m).verdict !== "BLOCKED" && m.l.availability !== "Offer pending")
    .filter(m => !isSnoozedNow(m)).filter(passFilter)
    .sort((a, b) => worklistRank(b) - worklistRank(a));
  const prim = []; for (const m of rows) { if (!seen.has(m.t.id)) { seen.add(m.t.id); prim.push(m); } }
  const list = prim.slice(0, 25);
  CURRENT_WORKLIST = list;
  if (triageIndex >= list.length) triageIndex = Math.max(0, list.length - 1);
  box.appendChild(el("div", "wm", "⭐ Top " + list.length + " to action today — highest fit and most urgent first, one line per tenant (their best available room)."));
  box.appendChild(triageLegend());
  if (!list.length) { box.appendChild(el("div", "empty", "No matches with these filters. Tap ↺ Clear filters.")); renderTriageBar(null); return; }
  list.forEach((m, i) => box.appendChild(matchRow(m, true, { focused: i === triageIndex })));
  renderTriageBar(list[triageIndex]);
}
function triageLegend() {
  return el("div", "triage-legend",
    '<span class="key">j</span>/<span class="key">k</span> move &nbsp; <span class="key">d</span> draft &nbsp; <span class="key">c</span> contacted &nbsp; ' +
    '<span class="key">v</span> viewing &nbsp; <span class="key">n</span> not interested &nbsp; <span class="key">s</span> snooze &nbsp; <span class="key">q</span> queue &nbsp; swipe left skip, right draft');
}
function renderTriageBar(m) {
  let bar = $("#triagebar");
  if (!m) { if (bar) bar.remove(); document.body.classList.remove("has-triagebar"); return; }
  if (!bar) { bar = el("div", "triagebar"); bar.id = "triagebar"; document.body.appendChild(bar); }
  document.body.classList.add("has-triagebar");
  // Label is "Decline" here (not "Not interested" as everywhere else) purely
  // for width — five actions in a 375px sticky bar with the legend right
  // above spelling out "n not interested" already, so the short form reads
  // fine in context. See .triagebar .btn in styles.css for the matching
  // padding trim that gets all five on screen without a horizontal scroll.
  bar.innerHTML =
    '<button class="btn lg" data-tc="c">Contacted</button>' +
    '<button class="btn lg p" data-tc="v">Viewing</button>' +
    '<button class="btn lg w" data-tc="d">Draft</button>' +
    '<button class="btn lg" data-tc="n">Decline</button>' +
    '<button class="btn lg" data-tc="s">Snooze</button>';
  bar.querySelectorAll("[data-tc]").forEach(b => b.onclick = () => triageAction(b.dataset.tc, m));
}
function triageAction(key, m) {
  if (!m) return;
  // Queueing a cold tenant is refused here as well as in the Mark dropdown —
  // this is the keyboard q / triage bar route into the same outbound action.
  if (key === "q" && coldBlocked(m.l, m.t)) { toast(fname(m.t.name) + " is quiet over 30 days — not queueing"); return; }
  // c/v/q now route through the exact same calls the row's own Mark dropdown
  // uses (wireRowEvents' mkSel.onchange) instead of a separate raw patchMark:
  // the keyboard/triage-bar path is the FAST, high frequency one, so it is
  // the one most likely to fat-finger the wrong key — it must not be the one
  // path with no undo toast. "v" specifically used to skip
  // openViewingBookedFlow entirely (no date/time captured, no viewing pack,
  // contradicting that flow's own "always asks first" comment) simply
  // because this branch patched the mark directly instead of calling it.
  if (key === "c") writeMarkUndoable(m.l.id, m.t.id, { v: "Contacted" }, fname(m.t.name) + " marked Contacted");
  else if (key === "v") openViewingBookedFlow(m.l, m.t);
  else if (key === "q") writeMarkUndoable(m.l.id, m.t.id, { v: "Queued" }, fname(m.t.name) + " marked Queued");
  else if (key === "n") openDeclineModal(m.l, m.t);
  else if (key === "s") openSnoozeModal(m.l, m.t);
  else if (key === "d") {
    const rows = document.querySelectorAll("#work .row");
    const row = rows[triageIndex];
    if (row) toggleDraftPreview(row, m.l, m.t);
  }
}
function toast(msg) {
  let box = $("#toast");
  if (!box) {
    box = el("div", "wm"); box.id = "toast";
    box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite");
    box.style.cssText = "position:fixed;bottom:70px;left:50%;transform:translateX(-50%);z-index:60"; document.body.appendChild(box);
  }
  box.textContent = msg; box.style.display = "block";
  clearTimeout(toast._t); toast._t = setTimeout(() => { box.style.display = "none"; }, 1600);
}
// (77) j/k re-render the worklist with a new .row.focus but never touched
// scroll position — confirmed live: the header/legend/help strip alone fill
// most of a 800px viewport, so the focused row went off screen after just 2
// or 3 presses of "j" with no way back into view except reaching for the
// mouse, defeating the entire point of keyboard triage. block:"nearest" only
// moves the minimum distance needed (no-op if already visible, so this
// never fights a user who scrolled manually to read something), and the
// default instant (non smooth) behavior matches the snappy, vim/Gmail-style
// feel "j"/"k" are modeled on rather than animating against rapid repeats.
function scrollFocusedRowIntoView() {
  const el = document.querySelector(".row.focus");
  if (el) el.scrollIntoView({ block: "nearest" });
}
// (item 4, cycle 8) Tab-focus counterpart to scrollFocusedRowIntoView() above —
// that one handles j/k triage navigation (moves a .row.focus CSS class, not
// real DOM focus, see onKeydown below); this handles genuine keyboard Tab into
// any button/link/input, on every tab, not just the worklist. Confirmed live:
// the browser's own default focus-scroll has no idea the sticky header or the
// fixed triage bar exist, so a focused control near either edge can still
// render UNDER one of them even though the browser itself considers it
// "in the viewport". .row's CSS scroll-margin-top/bottom (styles.css) is a
// defensive baseline for the same problem, but scroll-margin is not
// inherited — the browser's focus-scroll targets the DESCENDANT that actually
// received focus (a button/link inside .row), not the .row ancestor carrying
// the CSS rule — so this JS is the mechanism that actually does the work,
// reading live header/triagebar bounds on every focus rather than trusting a
// cached height. Only nudges scroll on a REAL overlap (never proactively
// re-centers), matching scrollFocusedRowIntoView's own "minimal movement"
// philosophy, and no-ops entirely while a modal/drawer is open — the
// background page is not visible then, and its scroll position is unrelated
// to whatever is focused inside the overlay's own self-contained scroll area.
function ensureFocusVisible(el) {
  if (OPEN_OVERLAY_COUNT > 0 || !el || el === document.body) return;
  const header = document.querySelector("header");
  const headerBottom = header ? header.getBoundingClientRect().bottom : 0;
  const triagebar = document.querySelector(".triagebar");
  const triagebarTop = triagebar ? triagebar.getBoundingClientRect().top : window.innerHeight;
  const r = el.getBoundingClientRect();
  if (r.top < headerBottom) window.scrollBy(0, r.top - headerBottom - 8);
  else if (r.bottom > triagebarTop) window.scrollBy(0, r.bottom - triagebarTop + 8);
}
function onKeydown(e) {
  // While any modal/drawer is open, its own focus-trapped controls should be
  // the only thing keys act on — without this, typing "n"/"c"/"v"/etc while a
  // dialog button happened to have focus fell through to document and fired
  // a background triage action on a totally different row.
  if (OPEN_OVERLAY_COUNT > 0) return;
  if (view !== "work") return;
  const tag = ((e.target && e.target.tagName) || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return;
  const list = CURRENT_WORKLIST || [];
  if (!list.length) return;
  if (e.key === "j") { triageIndex = Math.min(list.length - 1, triageIndex + 1); renderWork(); scrollFocusedRowIntoView(); }
  else if (e.key === "k") { triageIndex = Math.max(0, triageIndex - 1); renderWork(); scrollFocusedRowIntoView(); }
  else if (["d", "c", "v", "n", "s", "q"].includes(e.key)) triageAction(e.key, list[triageIndex]);
}
function wireSwipe(container) {
  let sx = null, sy = null, srow = null;
  container.addEventListener("touchstart", (e) => {
    const row = e.target.closest && e.target.closest(".row");
    if (!row) return;
    srow = row; sx = e.touches[0].clientX; sy = e.touches[0].clientY;
  }, { passive: true });
  container.addEventListener("touchend", (e) => {
    if (sx == null || !srow) return;
    const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
    const lid = srow.dataset.l, tid = srow.dataset.t;
    srow = null;
    if (Math.abs(dx) < 60 || Math.abs(dy) > 50) return;
    const m = (CURRENT_WORKLIST || []).find(x => x.l.id === lid && x.t.id === tid);
    if (!m) return;
    if (dx < 0) { const i = CURRENT_WORKLIST.indexOf(m); triageIndex = Math.min(CURRENT_WORKLIST.length - 1, i + 1); renderWork(); scrollFocusedRowIntoView(); }
    else triageAction("d", m);
  }, { passive: true });
}

// ===================== listing rail + panel =====================
let listingSortValue = "default"; // (18)/(29)/(30)
const LISTING_SORTS = { default: "District, then rent", waitlist: "Waitlist depth", thin: "Thin pipeline first", demand: "Demand heat" };
function qualifiedCount(l) { return (byListing[l.id] || []).filter(m => effective(m).verdict === "QUALIFIED").length; }
function demandFor(district) {
  const row = (DATA.area_demand || []).find(d => d.district === district);
  return (row && row.unmatched_waiting != null) ? row.unmatched_waiting : 0;
}
function sortListings(arr, mode) {
  if (mode === "waitlist") arr.sort((a, b) => qualifiedCount(b) - qualifiedCount(a));
  else if (mode === "thin") arr.sort((a, b) => qualifiedCount(a) - qualifiedCount(b));
  else if (mode === "demand") arr.sort((a, b) => demandFor(b.district) - demandFor(a.district));
  else arr.sort((a, b) => (a.district || "z").localeCompare(b.district || "z") || (a.rent_min || 9999) - (b.rent_min || 9999));
  return arr;
}
function listingSortBar() {
  const box = el("div", "batch-bar");
  box.innerHTML = '<b style="font-size:12px">Sort:</b><select id="lsort" aria-label="Sort listings">' +
    Object.keys(LISTING_SORTS).map(k => '<option value="' + k + '"' + (k === listingSortValue ? ' selected' : '') + '>' + LISTING_SORTS[k] + '</option>').join('') + '</select>';
  box.querySelector("#lsort").onchange = (e) => { listingSortValue = e.target.value; renderListingRail(); };
  return box;
}
// (33) first photo as a lazy loaded thumbnail + count badge; tap opens the lightbox.
function listingThumbHtml(l) {
  if (!Array.isArray(l.photos) || !l.photos.length) return "";
  // Wrapped so the count badge is position:absolute inside a sized, position:relative
  // box (see styles.css .thumb-wrap) — stays correctly placed even if the photo URL
  // is dead/slow and the <img> never renders at its intended height.
  const src = escUrl(l.photos[0]);
  if (!src) return "";
  return '<div class="thumb-wrap"><img class="thumb" src="' + src + '" loading="lazy" alt="" data-lightbox="1">' + (l.photos.length > 1 ? ('<span class="thumb-badge">' + esc(l.photos.length) + '</span>') : '') + '</div>';
}
function openLightbox(photos, startIdx) {
  if (!Array.isArray(photos) || !photos.length) return;
  let idx = startIdx || 0;
  const wrap = el("div", "lightbox-wrap");
  function paint() {
    // paint() replaces this whole subtree on every prev/next — including
    // whichever button currently has focus, which the browser responds to by
    // dropping focus back to <body>. mountOverlay's Tab-trap re-queries
    // live each keypress so it survives that fine, but focus itself would
    // silently fall out of the dialog after every single navigation unless
    // it is explicitly put back somewhere inside wrap — hadFocus/refocus
    // below is that: only re-steal focus if a keyboard/AT user was actually
    // driving this (a mouse click doesn't focus the button first, so this
    // is a no-op for mouse/touch use).
    const hadFocus = wrap.contains(document.activeElement);
    wrap.innerHTML = '<div class="lightbox"><img src="' + escUrl(photos[idx]) + '" loading="lazy" alt="Listing photo ' + esc(idx + 1) + ' of ' + esc(photos.length) + '">' +
      '<div class="acts">' +
      (photos.length > 1 ? ('<button class="btn" data-prev="1" aria-label="Previous photo">‹</button><span class="mut" aria-live="polite">' + esc(idx + 1) + ' / ' + esc(photos.length) + '</span><button class="btn" data-next="1" aria-label="Next photo">›</button>') : '') +
      '<button class="btn" data-close="1">Close</button></div></div>';
    const prev = wrap.querySelector("[data-prev]"); if (prev) prev.onclick = () => { idx = (idx - 1 + photos.length) % photos.length; paint(); };
    const next = wrap.querySelector("[data-next]"); if (next) next.onclick = () => { idx = (idx + 1) % photos.length; paint(); };
    wrap.querySelector("[data-close]").onclick = () => wrap.remove();
    if (hadFocus) {
      const refocus = wrap.querySelector("[data-next]") || wrap.querySelector("[data-close]");
      if (refocus) refocus.focus();
    }
  }
  paint();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
  mountOverlay(wrap, { label: "Photo viewer" });
}
// (56) sticky mini header — CSS position:sticky does the work (see styles.css);
// this just supplies the content, right below the header once you scroll past it.
function stickyCtxHtml(l) {
  const slot = (l.fixed_viewing && l.fixed_viewing.time_label) ? l.fixed_viewing.time_label : draftSlot(l, null);
  return '<div class="sticky-ctx">' + esc(l.name) + ' · ' + esc(rentTxt(l)) + ' · ' + esc(slot) + '</div>';
}
// (18)/(29)/(30) demand heat (DATA.area_demand) + price elasticity (days_listed>21)
function listingIntelHtml(l) {
  const parts = [];
  const demandRow = (DATA.area_demand || []).find(d => d.district === l.district);
  if (demandRow && demandRow.unmatched_waiting != null) parts.push('<span class="chip">🔥 ' + esc(demandRow.unmatched_waiting) + ' unmatched waiting in ' + esc(demandRow.area || l.district) + '</span>');
  if (l.days_listed != null && l.days_listed > Scoring.DAYS_LISTED_ELASTICITY_THRESHOLD) {
    const ladder = Scoring.priceElasticity(l, ALL_TENANTS, TODAY);
    if (ladder.some(x => x.additional > 0)) {
      const lines = ladder.map(x => 'at $' + Math.max(0, (l.rent_min || 0) - x.delta) + ', ' + x.additional + ' more qualify').join(' · ');
      parts.push('<span class="chip a">📈 ' + esc(l.days_listed) + 'd listed — ' + esc(lines) + '</span>');
    }
  }
  return parts.length ? ('<div class="chips" style="margin-top:8px">' + parts.join(' ') + '</div>') : '';
}
function showReconfirmDraft(l) {
  const wrap = el("div", "modal-wrap");
  const text = reconfirmDraft(l);
  wrap.innerHTML = '<div class="modal"><h3>Reconfirm — ' + esc(l.name) + '</h3><div class="draftbox">' + esc(text) + '</div>' +
    '<div class="foot"><button class="btn" data-copy2="1">Copy</button>' +
    waButtonHtml(l.phone, text, "WhatsApp", false) +
    '<button class="btn" data-cancel="1">Close</button></div></div>';
  mountOverlay(wrap);
  wrap.querySelector("[data-copy2]").onclick = (e) => { navigator.clipboard.writeText(text); e.target.textContent = "Copied"; };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
// (31) dup_of listings never get their own top level rail card — they nest
// under the primary's "+N also listed via co broke" line, still individually
// selectable (a co broke duplicate usually means a DIFFERENT phone number).
function listingCard(l, dups) {
  const q = byListing[l.id] || []; const nq = q.filter(m => effective(m).verdict === "QUALIFIED").length;
  const c = el("div", "lc" + (curL === l.id ? " on" : ""));
  c.innerHTML = listingThumbHtml(l) +
    '<div class="t">' + esc(l.name) + ' ' + (l.availability === "Offer pending" ? '<span class="chip a">offer pending</span>' : '') + (isCobroke(l) ? '<span class="badge cobroke">co-broke</span>' : '') + (l.reconfirm_due ? '<span class="badge overridden">reconfirm 14d+</span>' : '') + '</div>' +
    '<div class="m">' + esc(l.district) + ' · ' + esc(rentTxt(l)) + ' · ' + esc(l.address || AREA[l.district] || '') + '</div>' +
    (l.available_from ? ('<div class="m">vacant from ' + esc(shortDate(Scoring.parseDate(l.available_from))) + '</div>') : '') +
    '<div class="m"><span class="chip g">' + esc(nq) + ' qualified</span> <span class="chip">' + esc(q.length) + ' candidates</span></div>' +
    (dups.length ? ('<div class="m dupline" data-dupfor="1">+' + esc(dups.length) + ' also listed via co-broke</div>') : '');
  const thumbImg = c.querySelector("[data-lightbox]");
  if (thumbImg) thumbImg.onclick = (e) => { e.stopPropagation(); openLightbox(l.photos, 0); };
  c.onclick = (e) => { if (e.target.closest("[data-dupfor]")) return; curL = l.id; batchSelection = new Set(); renderListingRail(); renderListingPanel(l); };
  const dupline = c.querySelector("[data-dupfor]");
  if (dupline) dupline.onclick = (e) => { e.stopPropagation(); toggleDupListingList(c, dups); };
  return c;
}
function toggleDupListingList(card, dups) {
  const existing = card.querySelector(".dup-list");
  if (existing) { existing.remove(); return; }
  const box = el("div", "dup-list");
  dups.forEach(d => {
    const row = el("div", "", esc(d.name) + ' · ' + (d.phone ? phoneSpanHtml("listing", d.id, d.phone) : "no phone"));
    row.style.cursor = "pointer";
    row.onclick = (e) => { e.stopPropagation(); curL = d.id; batchSelection = new Set(); renderListingRail(); renderListingPanel(d); };
    box.appendChild(row);
  });
  card.appendChild(box);
}
// (65) closed/paused/tenanted supply is NOT in DATA.listings (scoring never
// sees it) — it lives in the separate DATA.supply_overview array (every
// landlord, any status). Absent/empty -> this whole section quietly omits,
// same null guard discipline as everything else new in pass 2.
function supplyOverviewSectionHtml() {
  const overview = DATA.supply_overview;
  if (!Array.isArray(overview) || !overview.length) return null;
  const activeIds = new Set((DATA.listings || []).map(l => l.id));
  const others = overview.filter(x => x && x.id != null && !activeIds.has(x.id));
  if (!others.length) return null;
  const counts = {};
  others.forEach(x => { const lc = x.lifecycle || "unknown"; counts[lc] = (counts[lc] || 0) + 1; });
  const summary = Object.keys(counts).map(k => counts[k] + " " + k.replace(/_/g, " ")).join(", ");
  const box = el("div", "");
  const toggle = el("div", "section-hd", "Not currently available (" + esc(others.length) + ": " + esc(summary) + ") — tap to show");
  toggle.style.cursor = "pointer";
  const list = el("div", "");
  list.style.cssText = "display:none;flex-direction:column;gap:8px";
  others.forEach(x => {
    const card = el("div", "lc");
    card.innerHTML = '<div class="t">' + esc(x.name || x.landlord_name || "Listing") + ' <span class="badge">' + esc(String(x.lifecycle || "unknown").replace(/_/g, " ")) + '</span></div>' +
      '<div class="m">' + esc(x.district || "") + (x.rent_min || x.rent_max ? (' · ' + esc(rentTxt(x))) : '') + (x.address ? (' · ' + esc(x.address)) : '') + '</div>';
    list.appendChild(card);
  });
  toggle.onclick = () => { list.style.display = list.style.display === "none" ? "flex" : "none"; };
  box.appendChild(toggle); box.appendChild(list);
  return box;
}
function renderListingRail() {
  const rail = $("#lrail"); rail.innerHTML = "";
  rail.appendChild(listingSortBar());
  const ls = [...(DATA.listings || [])];
  const dupByPrimary = {};
  ls.filter(l => l.dup_of).forEach(l => (dupByPrimary[l.dup_of] = dupByPrimary[l.dup_of] || []).push(l));
  const primaries = sortListings(ls.filter(l => !l.dup_of), listingSortValue);
  if (!primaries.length) rail.appendChild(el("div", "empty", "No listings loaded yet. Once export_data.py runs there will be rooms to match here."));
  primaries.forEach(l => rail.appendChild(listingCard(l, dupByPrimary[l.id] || [])));
  const otherSection = supplyOverviewSectionHtml();
  if (otherSection) rail.appendChild(otherSection);
  if (curL) { const l = (DATA.listings || []).find(x => x.id === curL) || (ls.find(x => x.id === curL)); if (l) renderListingPanel(l); }
}
function renderListingPanel(l) {
  const p = $("#lpanel"); const g = l.gates || {};
  const req = [];
  if (g.max_pax) req.push("max " + g.max_pax + "pax");
  if (g.lease_min) req.push(g.lease_min + "mo min");
  if (g.gender && g.gender !== "any") req.push("landlord preference: " + g.gender.replace("_", " "));
  if (g.ethnicity && g.ethnicity.rule !== "any" && g.ethnicity.rule !== "note") req.push("landlord preference: " + g.ethnicity.rule + " " + (g.ethnicity.races || []).join("/"));
  if (g.pets) req.push("pets " + g.pets);
  if (g.smoking) req.push("smoke " + g.smoking);
  if (g.cooking) req.push("cooking " + g.cooking.slice(0, 22));
  const cobroke = isCobroke(l);
  const contactLabel = cobroke ? "co-broke agent" : "landlord";
  let h = stickyCtxHtml(l) +
    '<div class="phead"><div><div class="big">' + esc(l.name) + ' · ' + esc(rentTxt(l)) + (cobroke ? ' <span class="badge cobroke">co-broke</span>' : '') + (l.reconfirm_due ? ' <span class="badge overridden">reconfirm 14d+</span>' : '') + '</div>' +
    '<div class="mut">' + esc(l.district) + ' · ' + esc(l.address || '') + ' · ' + esc(l.property_type || '') + (l.available_from ? (' · vacant from ' + esc(shortDate(Scoring.parseDate(l.available_from)))) : '') + '</div>' +
    // req[] carries verbatim landlord phrasing (cooking/pets/smoking notes).
    '<div class="chips">' + req.map(r => '<span class="chip">' + esc(r) + '</span>').join('') + '</div>' +
    (l.rooms ? ('<div class="mut" style="margin-top:6px">' + esc(l.rooms) + '</div>') : '') +
    (l.viewing ? ('<div class="chips"><span class="chip g">🕐 viewing: ' + esc(l.viewing) + '</span></div>') : '') +
    (l.availability === "Offer pending" ? '<div class="chips"><span class="chip a">⚠ offer pending, hold new offers</span></div>' : '') +
    listingIntelHtml(l) +
    (Array.isArray(l.photos) && l.photos.length ? ('<div class="photostrip" data-photostrip="1">' + l.photos.slice(0, 6).map((u, i) => '<img loading="lazy" src="' + escUrl(u) + '" alt="" data-pidx="' + i + '">').join('') + '</div>') : '') +
    '</div>' +
    '<div>' +
    '<a class="btn" target="_blank" rel="noopener" href="' + escUrl(mapLink(l)) + '">📍 Map</a> ' +
    waButtonHtml(l.phone, "Hi " + fname(l.name) + ", checking on the room at " + (l.address || AREA[l.district] || "your unit") + ", is it still available and when can tenants view", "WhatsApp " + contactLabel, false) +
    (l.phone ? (" " + callButtonHtml(l.phone, "Call " + contactLabel, false)) : "") +
    (l.listing_url ? (" " + linkButtonHtml(l.listing_url, "💬 WA listing copy")) : "") +
    (l.reconfirm_due ? ' <button class="btn" data-reconfirm="1">🔄 Reconfirm</button>' : '') +
    '</div></div>';
  p.innerHTML = h;
  const photostrip = p.querySelector('[data-photostrip]');
  if (photostrip) photostrip.querySelectorAll('img').forEach(img => img.onclick = () => openLightbox(l.photos, +img.dataset.pidx));
  const reconfirmBtn = p.querySelector('[data-reconfirm]'); if (reconfirmBtn) reconfirmBtn.onclick = () => showReconfirmDraft(l);

  const allForListing = byListing[l.id] || [];
  const q = allForListing.filter(passFilterListing);
  const qualified = q.filter(m => effective(m).verdict === "QUALIFIED");

  // Spec item 5 reads "top <=3 qualified tenants" — a cap on how many go into
  // the draft, not a floor on when it is offered. Requiring 3 withheld this
  // from exactly the listings that most need a landlord nudge: item 29's own
  // "thin pipeline" sort exists to surface listings with the FEWEST qualified
  // tenants, and those then had no shortlist button at all.
  if (qualified.length >= 1) {
    const shortlistBtn = el("button", "btn p", "Draft landlord shortlist (top " + Math.min(3, qualified.length) + ")");
    shortlistBtn.onclick = () => showShortlistDraft(l, qualified.slice(0, 3).map(m => m.t));
    p.appendChild(shortlistBtn);
  }
  if (qualified.length >= 2) {
    const batchWrap = el("div", "", renderBatchBuilder(qualified));
    p.appendChild(batchWrap);
    wireBatchBuilder(batchWrap, l);
  }

  if (!q.length) { p.appendChild(el("div", "empty", "No tenants match the current filters for this listing. Try clearing a filter above.")); return; }
  q.slice(0, 60).forEach(m => {
    // The batch builder queues whoever is ticked, and queueing is an outbound
    // action — so a cold tenant is not offered a checkbox in the first place.
    const checkbox = qualified.length >= 2 && effective(m).verdict === "QUALIFIED" && !coldBlocked(l, m.t);
    p.appendChild(matchRow(m, false, { checkbox }));
  });
  renderNearMissSection(p, allForListing, false);
}
function showShortlistDraft(l, tenants) {
  const wrap = el("div", "modal-wrap");
  const text = landlordShortlistDraft(l, tenants);
  wrap.innerHTML = '<div class="modal"><h3>Landlord shortlist — ' + esc(l.name) + '</h3><div class="draftbox">' + esc(text) + '</div>' +
    '<div class="foot"><button class="btn" data-copy2="1">Copy</button>' +
    waButtonHtml(l.phone, text, "WhatsApp", false) +
    '<button class="btn" data-cancel="1">Close</button></div></div>';
  mountOverlay(wrap);
  wrap.querySelector("[data-copy2]").onclick = (e) => { navigator.clipboard.writeText(text); e.target.textContent = "Copied"; };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}

// ===================== batch viewing builder =====================
function toggleBatchSelect(tid, checked) {
  if (checked) batchSelection.add(tid); else batchSelection.delete(tid);
  const btn = document.querySelector("[data-batchgen]");
  if (btn) btn.textContent = "Generate for selected (" + batchSelection.size + ")";
}
function renderBatchBuilder(qualifiedMatches) {
  if (qualifiedMatches.length < 2) return "";
  return '<div class="batch-bar">' +
    '<b style="font-size:12px">Batch viewings:</b>' +
    '<input type="date" data-batchdate="1" aria-label="Batch viewing date">' +
    '<input type="time" data-batchtime="1" value="15:00" aria-label="Batch viewing start time">' +
    '<label class="mut" style="font-size:11px">interval<input type="number" data-batchival="1" value="20"></label>' +
    '<button class="btn" data-batchgen="1">Generate for selected (' + batchSelection.size + ')</button>' +
    '</div><div id="batchout"></div>';
}
// (68) busy_blocks is confirmed null on this real machine right now — this
// check stays dormant until the data lane populates DATA.busy_blocks. Its
// exact shape is not finalized upstream, so this degrades to "no warning" on
// anything it cannot parse rather than guessing wrong and blocking a real
// batch of viewings.
function busyBlockClashes(dateStr, startMinutes, endMinutes) {
  const blocks = DATA.busy_blocks;
  if (!Array.isArray(blocks) || !blocks.length) return [];
  const out = [];
  blocks.forEach(b => {
    if (!b) return;
    try {
      if (b.date && b.date !== dateStr) return;
      let bStart, bEnd;
      if (b.start && /^\d{1,2}:\d{2}$/.test(b.start)) {
        const p1 = b.start.split(":").map(Number), p2 = (b.end || b.start).split(":").map(Number);
        bStart = p1[0] * 60 + p1[1]; bEnd = p2[0] * 60 + p2[1];
      } else if (b.start) {
        // If a future data lane populates this with full ISO timestamps
        // (rather than bare "HH:MM"), read the time of day via Singapore,
        // not the viewing device's own zone — startMinutes/endMinutes above
        // are always a Singapore wall clock time the user typed directly, so
        // comparing them against a device-local reading of b.start could
        // silently miss (or invent) a clash purely from where the device is.
        const sd = Scoring.sgtParts(new Date(b.start)), ed = Scoring.sgtParts(new Date(b.end || b.start));
        if (!sd || !ed) return;
        bStart = sd.hh * 60 + sd.mm; bEnd = ed.hh * 60 + ed.mm;
      } else return;
      if (startMinutes < bEnd && endMinutes > bStart) out.push(b.label || "busy block");
    } catch (e) { /* unrecognised shape — skip rather than block a real batch */ }
  });
  return out;
}
function wireBatchBuilder(container, l) {
  const genBtn = container.querySelector("[data-batchgen]");
  if (!genBtn) return;
  const dateI = container.querySelector("[data-batchdate]"), timeI = container.querySelector("[data-batchtime]"), ivalI = container.querySelector("[data-batchival]");
  genBtn.onclick = () => {
    const ids = [...batchSelection];
    if (ids.length < 2 || !dateI.value || !timeI.value) { toast("Pick a date/time and select at least 2 tenants"); return; }
    const interval = (+ivalI.value) || 20;
    const [hh0, mm0] = timeI.value.split(":").map(Number);
    // Defence in depth: the checkbox is already withheld from cold tenants,
    // but a stale batchSelection must not slip one through either.
    const chosen = ids.map(id => (byListing[l.id] || []).find(x => x.t.id === id)).filter(Boolean).filter(m => !coldBlocked(l, m.t));
    const out = $("#batchout"); out.innerHTML = "";
    if (chosen.length < 2) { toast("Need at least 2 tenants who are not quiet over 30 days"); return; }
    const clashes = busyBlockClashes(dateI.value, hh0 * 60 + mm0, hh0 * 60 + mm0 + chosen.length * interval);
    if (clashes.length) out.appendChild(el("div", "wm banner-amber", "⚠ Clashes with: " + esc(clashes.join(", ")) + " — double check before sending"));
    let summary = "Viewings " + dateI.value + " at " + l.name + ":\n";
    const priors = chosen.map(m => ({ lid: l.id, tid: m.t.id, prior: readMark(l.id, m.t.id) }));
    chosen.forEach((m, i) => {
      const mins = hh0 * 60 + mm0 + i * interval;
      const label = fmtTime(Math.floor(mins / 60) % 24, mins % 60);
      const text = draftFor(m.l, m.t, label + " on " + dateI.value);
      summary += label + " — " + m.t.name + "\n";
      out.appendChild(el("div", "draftbox", esc(m.t.name + ": " + text)));
      patchMark(l.id, m.t.id, { v: "Queued" });
    });
    out.appendChild(el("div", "draftlabel", "Summary (copy for yourself)"));
    out.appendChild(el("div", "draftbox", esc(summary.trim())));
    const copyBtn = el("button", "btn", "Copy summary");
    copyBtn.onclick = () => { navigator.clipboard.writeText(summary.trim()); copyBtn.textContent = "Copied"; };
    out.appendChild(copyBtn);
    batchSelection = new Set();
    undoToast("Queued " + chosen.length + " for batch viewing", () => restoreMarks(priors));
    render();
  };
}

// ===================== tenant rail + panel =====================
// (74) big-DOM cap for the tenant rail (140+ real tenants) — bumped by the
// "Show more" button below, never auto-reset mid session so a tap forward
// can't get silently undone by the next unrelated render(). A hard cap with
// no way past it used to make the bottom ~20 tenants (by score) unreachable
// from this tab whenever a search didn't happen to narrow past it.
let tenantRailLimit = 120;
function renderTenantRail() {
  const rail = $("#trail"); rail.innerHTML = "";
  const f = F();
  let ts = [...ALL_TENANTS];
  if (f.q) ts = ts.filter(t => (t.name + " " + t.preferred_location + " " + t.district + " " + (t.nationality || "") + " " + (t.phone || "")).toLowerCase().includes(f.q));
  ts.sort((a, b) => (byTenant[b.id]?.[0]?.s.total || 0) - (byTenant[a.id]?.[0]?.s.total || 0));
  if (!ts.length) { rail.appendChild(el("div", "empty", "No tenants match this search. Try clearing the search box above.")); return; }
  const shown = ts.slice(0, tenantRailLimit);
  shown.forEach(t => {
    const best = byTenant[t.id]?.[0]; const nq = (byTenant[t.id] || []).filter(m => effective(m).verdict === "QUALIFIED").length;
    const c = el("div", "lc" + (curT === t.id ? " on" : ""));
    const nba = best ? nbaChipHtml(best) : ""; // (60)
    c.innerHTML = '<div class="t">' + esc(t.name) + (t._scratch ? ' <span class="badge scratch">scratch</span>' : '') + (t.dup_group != null ? dupGroupBadgeHtml(t) : '') + '</div>' +
      '<div class="m">' + esc(t.district || t.preferred_location || '?') + ' · budget ' + esc(t.budget || t.budget_max || '?') + ' · ' + esc(t.pax || '?') + 'pax</div>' +
      '<div class="m">' + genderChip(t) + '</div>' +
      '<div class="m">' + esc(lastContactLine(t)) + '</div>' +                                  // (46)
      '<div class="m"><span class="chip g">' + esc(nq) + ' fit</span> ' + (best ? ('<span class="chip">top ' + esc(best.s.total) + '</span>') : '') + (nba ? (' ' + nba) : '') + '</div>';
    c.onclick = (e) => { if (e.target.closest("[data-dupgroup],[data-nba]")) return; curT = t.id; renderTenantRail(); renderTenantPanel(t); };
    const dupTrigger = c.querySelector('[data-dupgroup]');
    if (dupTrigger) dupTrigger.onclick = (e) => { e.stopPropagation(); toggleDupGroupList(c, t.dup_group); };
    if (best) wireNbaChip(c, best.l, best.t);
    rail.appendChild(c);
  });
  if (ts.length > shown.length) {
    const remaining = ts.length - shown.length;
    const more = el("button", "btn full", "Show " + Math.min(40, remaining) + " more (" + remaining + " left)");
    more.onclick = () => { tenantRailLimit += 40; renderTenantRail(); };
    rail.appendChild(more);
  }
  if (curT) { const t = ALL_TENANTS.find(x => x.id === curT); if (t) renderTenantPanel(t); }
}
// (34) gallery, (66) client folder chip, (67) dormant obsidian deep link
function clientSlug(t) { return (t.name || "tenant").toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "tenant"; }
function tenantToolsHtml(t) {
  const parts = ['<button class="btn" data-gallery="1">🖼 Gallery</button>', '<button class="btn" data-clientchip="1">📋 /client ' + esc(clientSlug(t)) + '</button>'];
  // (67) only ever renders if the data lane supplies a confidently joined note slug — never guessed client side.
  if (t.obsidian_slug) parts.push('<a class="btn" href="' + escUrl("obsidian://open?vault=Winfred%20Brain&file=" + encodeURIComponent(t.obsidian_slug)) + '">🧠 Obsidian note</a>');
  return '<div class="acts" style="margin-top:8px">' + parts.join('') + '</div>';
}
function wireTenantTools(p, t) {
  const gal = p.querySelector('[data-gallery]'); if (gal) gal.onclick = () => openGallery(t);
  const cc = p.querySelector('[data-clientchip]'); if (cc) cc.onclick = (e) => { navigator.clipboard.writeText("/client " + clientSlug(t)); e.target.textContent = "Copied"; };
}
function renderTenantPanel(t) {
  const p = $("#tpanel");
  const dupBadge = t.dup_group != null ? dupGroupBadgeHtml(t) : "";
  p.innerHTML = '<div class="phead"><div><div class="big">' + esc(t.name) + (t._scratch ? ' <span class="badge scratch">scratch — rebuild to make permanent</span>' : '') + ' ' + dupBadge + '</div>' +
    '<div class="mut">' + esc(t.preferred_location || t.district || '') + ' · budget ' + esc(t.budget || t.budget_max || '?') + ' · ' + esc(t.pax || '?') + 'pax · lease ' + esc(t.lease_months || '?') + 'mo · move ' + esc(t.move_in || '?') + '</div>' +
    '<div class="mut" style="margin-top:2px">' + esc(lastContactLine(t)) + '</div>' +
    '<div class="chips"><span class="chip">' + esc(t.gender || '?') + '</span><span class="chip">' + esc(t.ethnicity || '?') + '</span><span class="chip">' + esc(t.nationality || '?') + '</span><span class="chip">' + esc(t.pass_type || '?') + '</span><span class="chip">' + esc(t.occupation || '?') + '</span>' + coldChip(coldDaysOf(t)) + '</div>' +
    tenantToolsHtml(t) + '</div></div>';
  wireTenantTools(p, t);
  const dupTrigger = p.querySelector('[data-dupgroup]');
  if (dupTrigger) dupTrigger.onclick = (e) => { e.stopPropagation(); toggleDupGroupList(p.querySelector(".phead"), t.dup_group); };
  const allForTenant = byTenant[t.id] || [];
  const q = allForTenant.filter(m => {
    const f = F();
    if (isSnoozedNow(m)) return false;
    if (f.v && effective(m).verdict !== f.v) return false;
    if (f.d && m.l.district !== f.d) return false;
    if (f.r && m.l.rent_min && m.l.rent_min > f.r) return false;
    return true;
  });
  if (!q.length) { p.appendChild(el("div", "empty", "No available listing fits this tenant right now. Clear a filter above, or check back once new rooms come in.")); return; }
  q.forEach(m => p.appendChild(matchRow(m, true)));
  renderNearMissSection(p, allForTenant, true);
}
// (34) Shortlist gallery — top <=3 qualified listings, print only. No other
// tenants and no landlord phone anywhere in the printed output.
function openGallery(t) {
  const top3 = (byTenant[t.id] || []).filter(m => effective(m).verdict === "QUALIFIED").slice(0, 3);
  if (!top3.length) { toast("No qualified listings yet for a gallery"); return; }
  const old = $("#printgallery"); if (old) old.remove();
  const box = el("div", "printgallery");
  box.id = "printgallery";
  let html = '<div class="pg-header">Prepared for ' + esc(fname(t.name)) + '</div>';
  top3.forEach(m => {
    const l = m.l;
    html += '<div class="pg-card">' +
      (Array.isArray(l.photos) && l.photos.length ? ('<img src="' + escUrl(l.photos[0]) + '" alt="">') : '') +
      '<div class="pg-facts"><b>' + esc(l.name) + '</b><br>' + esc(rentTxt(l)) + ' · ' + esc(l.district) +
      (l.available_from ? (' · vacant from ' + esc(shortDate(Scoring.parseDate(l.available_from)))) : '') +
      (l.viewing ? ('<br>Viewing: ' + esc(l.viewing)) : '') + '</div></div>';
  });
  box.innerHTML = html;
  document.body.appendChild(box);
  document.body.classList.add("printing-gallery");
  const cleanup = () => { document.body.classList.remove("printing-gallery"); box.remove(); window.removeEventListener("afterprint", cleanup); };
  window.addEventListener("afterprint", cleanup);
  window.print();
  setTimeout(cleanup, 4000); // fallback for browsers that never fire afterprint
}

// ===================== whole unit view =====================
function renderWholeUnit() {
  const box = $("#whole"); box.innerHTML = "";
  box.appendChild(el("div", "help", "Tenants whose budget comfortably clears a whole unit, or who explicitly asked for one, matched against whole unit and studio listings."));
  const tenants = ALL_TENANTS.filter(isWholeUnitTenant);
  const listings = (DATA.listings || []).filter(isWholeUnitListing);
  if (!tenants.length || !listings.length) { box.appendChild(el("div", "empty", "No whole unit candidates or listings right now. This tab fills in once a tenant's budget clears a whole unit, or a landlord lists one.")); return; }
  tenants.forEach(t => {
    const matches = listings.map(l => (byTenant[t.id] || []).find(m => m.l.id === l.id)).filter(Boolean).filter(passFilter);
    if (!matches.length) return;
    box.appendChild(el("div", "section-hd", esc(t.name)));
    matches.forEach(m => box.appendChild(matchRow(m, true)));
  });
}

// ===================== landlord / all tenants / sales / revival rosters =====================
// Ported from the pre-v2 monolith (matchmaker/crm-cloud-backend:template.html) so nothing
// Winfred uses daily regresses when v2 ships. These 4 tabs read straight off
// DATA.all_landlords / DATA.all_tenants / DATA.sales / DATA.revival / DATA.duplicate_phones —
// payload contract keys another agent is adding concurrently. Every read below degrades to
// an empty state rather than throwing when a key is missing or empty on a given build.
const DUP_PHONES = new Set((DATA.duplicate_phones || []).map(d => d.phone).filter(Boolean));

// Region grouping, ported for parity with the source app. Not currently wired into the
// All Tenants grouping below — that groups by primary_district instead, matching exactly
// what Winfred already sees today in the deployed monolith. Left available for a future
// pass that wants area level grouping.
const REGION_DISTRICTS = { East: ["D15", "D16", "D17", "D18"], West: ["D5", "D21", "D22", "D23"], North: ["D25", "D26", "D27", "D28"], Northeast: ["D19", "D20"] };
(function () {
  const assigned = new Set(Object.values(REGION_DISTRICTS).flat());
  const allDistricts = Array.from({ length: 28 }, (_, i) => "D" + (i + 1));
  REGION_DISTRICTS.Central = allDistricts.filter(d => !assigned.has(d));
})();
const _D2R = {};
Object.keys(REGION_DISTRICTS).forEach(r => REGION_DISTRICTS[r].forEach(d => { _D2R[d] = r; }));
function regionOf(t) {
  const cands = [t.primary_district, t.district].filter(Boolean);
  for (const d of cands) { if (_D2R[d]) return _D2R[d]; }
  const loc = (t.preferred_location || "").trim().toLowerCase();
  if (!loc) return "Location not captured";
  if (/\b(east|bedok|tampines|changi|katong|siglap|simei|pasir ris|eunos|marine parade)\b/.test(loc)) return "East";
  if (/\b(west|jurong|clementi|boon lay|pioneer|bukit batok|lakeside|choa chu kang|tengah)\b/.test(loc)) return "West";
  if (/\b(north|woodlands|yishun|sembawang|admiralty|khatib|canberra)\b/.test(loc)) return "North";
  if (/\b(serangoon|hougang|punggol|sengkang|kovan|ang mo kio|amk|lew lian|cherryhill|nex)\b/.test(loc)) return "Northeast";
  if (/\b(town|orchard|central|novena|newton|river valley|tanjong pagar|smu|cbd|bugis|lavender|farrer park)\b/.test(loc)) return "Central";
  // last resort: match the free text against the district area names already carried in
  // AREA, so a place name never hardcoded above still lands in the right region.
  for (const d in AREA) {
    if (!_D2R[d]) continue;
    for (const part of String(AREA[d] || "").toLowerCase().split(/[,/]/)) {
      const a = part.trim();
      if (a.length > 3 && loc.includes(a)) return _D2R[d];
    }
  }
  return "Other areas";
}

// Shows where a tenant actually wants to live, not just the raw district code — district
// stays on the end of the line as a fallback for tenants whose location was never captured.
function tenantWhere(t) {
  const loc = (t.preferred_location || "").trim();
  const d = (t.district || "").trim();
  if (!loc) return d || "location not captured";
  const short = loc.length > 44 ? loc.slice(0, 44).replace(/[ ,]+$/, "") + "…" : loc;
  return d ? short + " · " + d : short;
}

function statusChip(av) {
  if (av === "Available") return '<span class="chip g">🟢 Available</span>';
  if (av === "Offer pending") return '<span class="chip a">🟡 Offer pending</span>';
  if (av === "Pending") return '<span class="chip">⚪ Pending intake</span>';
  if (av === "Taken") return '<span class="chip r">🔴 Taken</span>';
  return '<span class="chip mut">⚫ Off market</span>';
}
function saleStatusChip(st) {
  if (st === "Available") return '<span class="chip g">🟢 Available</span>';
  if (st === "Pending") return '<span class="chip a">⚪ Pending</span>';
  return '<span class="chip mut">⚫ Closed</span>';
}
// Informational, at Winfred's direction (13 Aug 2026): he wants to SEE a tenant's
// gender at a glance. A landlord's stated preference is a preference, not a hard
// requirement, so a blank one is not an alarm and must not be styled as a problem —
// it is simply a detail he does not have yet.
//
// The raw field is messy — "female", "f", "couple", "1 female 1 male" all appear —
// so the chip shows what is actually recorded rather than tidying it into a cleaner
// answer than exists. The only case worth a second look is a mixed/couple value,
// because it describes more than one person.
function genderChip(t) {
  const raw = String((t && t.gender) || "").trim();
  if (!raw) {
    // Every one of these leads did come in over WhatsApp, but of the 64 blanks only 3
    // ever stated a gender — the rest simply never said (checked against the real
    // message store, 13 Aug 2026), so there is nothing to extract. Guessing from a
    // first name is not an option either: this book is Malaysian, Indian, Chinese and
    // Indonesian names, where that inference is unreliable.
    // So the blank offers a quiet one-tap ask and otherwise stays out of the way.
    // Still honours the dead-lead rule — no ask link past the 30 day cutoff.
    const askable = t && t.phone && !coldBlocked(null, t);
    if (askable) {
      // oneQuestionDraft is v2's existing single-missing-field ask, so this reuses the
      // house wording instead of inventing a second voice. (An earlier version of this
      // called greet(), which exists in the OLD monolith but NOT in v2 — it threw on the
      // first blank-gender tenant and silently truncated the All Tenants roster to 24 of
      // 554 rows. Reusing a helper that provably exists here is the point.)
      const msg = oneQuestionDraft(t, "gender");
      return '<a class="chip mut" style="text-decoration:none" target="_blank" rel="noopener noreferrer"' +
        ' href="' + escUrl(waPlain(t.phone, msg)) + '"' +
        ' title="No gender on file — tap to ask them.">gender? — ask</a>';
    }
    return '<span class="chip mut" title="No gender on file">gender not on file</span>';
  }
  // Mixed/couple MUST be tested before the /^m/i and /^f/i prefixes. "Mixed (2 female
  // + 2 male siblings and husband)" starts with an m and was being rendered "♂ Male" —
  // a group of five read as one man, on the exact field a landlord gender gate turns on.
  // Note scoring.js gates on the same bare prefixes, so it treats these as MALE too;
  // this chip deliberately disagrees with it and says "confirm", because the honest
  // answer is that the field cannot resolve a gate on its own.
  if (/mixed|couple|both|\band\b|\+|\bfamily\b|female.*male|male.*female/i.test(raw)) {
    return '<span class="chip" title="More than one person — worth confirming who is actually taking the room">⚧ ' + esc(raw) + '</span>';
  }
  if (/^f/i.test(raw)) return '<span class="chip">♀ ' + esc(raw) + '</span>';
  if (/^m/i.test(raw)) return '<span class="chip">♂ ' + esc(raw) + '</span>';
  return '<span class="chip" title="Unrecognised value — shown exactly as recorded">⚧ ' + esc(raw) + '</span>';
}
function tenantLookingChip(lk) {
  if (lk === "Still looking") return '<span class="chip g">🟢 Still looking</span>';
  if (lk === "Found") return '<span class="chip">✅ Found a place</span>';
  return '<span class="chip mut">⚫ Not looking</span>';
}
function revivalTierChip(tier) {
  if (tier === "good") return '<span class="chip g">✅ good match waiting</span>';
  if (tier === "weak") return '<span class="chip a">❓ weak match</span>';
  return '<span class="chip mut">— no match right now</span>';
}
// CEA register status of the co-broke agent who brought a landlord/sale listing, verified
// by contact number at build time. The record's own phone belongs to the OWNER and is never
// checked — they are not a salesperson.
function ceaChip(r) {
  const c = r && r.cea;
  if (!c) return "";
  const who = c.agent ? esc(c.agent) : "agent";
  const t = c.reg_no ? (esc(c.name) + " · " + esc(c.reg_no) + " · " + esc(c.agency) + " · valid to " + esc(c.valid_until)) : "";
  if (c.status === "active")
    return '<span class="chip g" title="' + t + '">✅ ' + who + ' · CEA ' + esc(c.reg_no) + '</span>'
      + (c.disciplinary ? '<span class="chip r" title="Disciplinary actions on record">⚠ disciplinary</span>' : '');
  if (c.status === "expired")
    return '<span class="chip r" title="' + t + '">❌ ' + who + ' · CEA EXPIRED ' + esc(c.valid_until) + '</span>';
  if (c.status === "not_registered")
    return '<span class="chip r" title="No registered salesperson at this agent\'s contact number. Verify before sharing client info or splitting commission.">⚠ ' + who + ' NOT on CEA register</span>';
  if (c.status === "agent_unknown")
    return '<span class="chip a" title="Co-broke listing but the counterpart agent is not identified, so no CEA check was possible.">' + who + ' not identified</span>';
  return '<span class="chip a" title="Register lookup unavailable at build time">CEA unchecked</span>';
}

function dupBanner() {
  const dups = DATA.duplicate_phones || [];
  if (!dups.length) return "";
  return '<div class="wm">⚠ ' + dups.length + ' phone number(s) shared across more than one record — likely a duplicate or data entry collision: ' +
    dups.map(d => esc((d.owners || []).join(" = "))).join(" · ") + '</div>';
}
function copyBtn(label, rows, box) {
  const b = el("button", "btn", label);
  b.onclick = () => { navigator.clipboard.writeText(rows.join("\n")); b.textContent = "Copied ✓ (" + rows.length + ")"; setTimeout(() => { b.textContent = label; }, 2000); };
  box.appendChild(b);
  return b;
}

// ---- flag wrong (landlord roster only) — device local, v2 has no CRM backend to key this to ----
const FLAG_PREFIX = "cbk_flag_landlord_";
function isFlaggedLandlord(l) { return localStorage.getItem(FLAG_PREFIX + l.id) === "1"; }
function toggleFlaggedLandlord(l) {
  const key = FLAG_PREFIX + l.id;
  if (localStorage.getItem(key) === "1") localStorage.removeItem(key); else safeSet(key, "1");
  render();
}

// ---- short check-in drafts for these 4 rosters (same voice as the rest of the app: no
// hyphens, no sign off) ----
function landlordCheckInDraft(l) {
  return "Hi " + fname(l.name) + ", just checking in, is the room at " + (l.address || areaName(l)) + " still available?";
}
function tenantCheckInDraft(t) {
  return "Hi " + fname(t.name) + ", checking in on your room search, still looking? Let me know your latest budget and move in date and I will send matches.";
}
function saleCheckInDraft(s) {
  return "Hi " + fname(s.name) + ", checking in on the sale at " + (s.address || areaName(s)) + ", still on the market?";
}
function revivalDraft(r) {
  if (r.match) return "Hi " + fname(r.name) + ", following up on your room search, a unit just opened in " + (r.match.district || "the area") + ". Want the details?";
  return "Hi " + fname(r.name) + ", checking in, are you still looking for a room? Let me know your latest budget and move in date and I will send options.";
}

function renderLandlordsRoster() {
  const box = $("#landlords"); box.innerHTML = "";
  box.appendChild(el("div", "help", "🏢 <b>Full landlord roster</b> — every landlord in the database, every status. This is the same data Claude Code reads; nothing here is filtered for matching. 🚩 Flag wrong lets you mark a status you know is stale so you can send the correction back."));
  const db = dupBanner();
  if (db) box.innerHTML += db;

  const f = F();
  let ls = (DATA.all_landlords || []).slice();
  if (f.d) ls = ls.filter(l => l.primary_district === f.d);
  if (f.q) {
    const hay = l => (String(l.name || "") + " " + (l.district || "") + " " + (AREA[l.district] || "") + " " + (l.address || "") + " " + (l.phone || "")).toLowerCase();
    ls = ls.filter(l => hay(l).includes(f.q));
  }
  ls.sort((a, b) => (a.sort != null ? a.sort : 99) - (b.sort != null ? b.sort : 99));

  const actionsRow = el("div", "acts");
  const stale = ls.filter(l => l.availability === "Available" && l.phone && (Scoring.daysAgo(l.last_contact, TODAY) == null || Scoring.daysAgo(l.last_contact, TODAY) > 14));
  copyBtn("📋 Copy stale check in list (" + stale.length + ")",
    stale.map(l => (l.name || "?") + " | " + l.phone + " | " + landlordCheckInDraft(l)),
    actionsRow);
  const flaggedList = ls.filter(l => isFlaggedLandlord(l));
  copyBtn("🚩 Copy flagged list (" + flaggedList.length + ")",
    flaggedList.map(l => (l.id || "?") + " " + (l.name || "?") + " — marked wrong by Winfred"),
    actionsRow);
  box.appendChild(actionsRow);

  if (!ls.length) { box.appendChild(el("div", "empty", "No landlords match the current search/district filter.")); return; }

  ls.forEach(l => {
    const dc = Scoring.daysAgo(l.last_contact, TODAY);
    const flagged = isFlaggedLandlord(l);
    // Taken/Off market landlords get no contact action — some of these are explicit
    // "never re-engage" drops. Map stays (harmless); WhatsApp/Call are the outbound
    // contact risk, so they are suppressed here, same as the source app.
    const blockedContact = l.availability === "Taken" || l.availability === "Off market";
    const row = el("div", "row" + (flagged ? " done" : ""));
    const mapHtml = '<a class="btn" target="_blank" rel="noopener noreferrer" href="' + escUrl(mapLink(l)) + '">📍 Map</a>';
    const actsHtml = blockedContact
      ? '<div class="acts">' + mapHtml + ' <span class="btn mut">' + esc(String(l.availability || "").toLowerCase()) + ' — no contact action</span>' +
          '<button class="btn" data-flag="1">' + (flagged ? "↺ Unflag" : "🚩 Flag wrong") + '</button>' + crmBtn("landlord", l) + '</div>'
      : '<div class="acts">' + mapHtml + ' ' +
          waButtonHtml(l.phone, landlordCheckInDraft(l), "WhatsApp landlord", false) +
          callButtonHtml(l.phone, "Call", false) +
          (!l.phone ? '<span class="btn mut">no phone on file</span>' : '') +
          '<button class="btn" data-flag="1">' + (flagged ? "↺ Unflag" : "🚩 Flag wrong") + '</button>' + crmBtn("landlord", l) + '</div>';
    row.innerHTML =
      '<div class="rtop"><span class="nm">' + esc(l.name || "?") + '</span> ' + statusChip(l.availability) +
        (l.source === "co-broke" ? '<span class="chip">co-broke</span>' + ceaChip(l) : '') +
        (l.handed_off ? '<span class="chip a">🤝 handed off</span>' : '') +
        (l.commission_est ? '<span class="chip">💰 ~$' + esc(l.commission_est) + ' est.</span>' : '') +
        (DUP_PHONES.has(l.phone) ? '<span class="chip a">⚠ duplicate phone on file</span>' : '') +
        (flagged ? '<span class="chip r">🚩 flagged wrong</span>' : '') +
      '</div>' +
      '<div class="rtop" style="margin-top:5px">' +
        '<span class="chip">' + esc(l.district || "?") + (l.property_type ? ' · ' + esc(l.property_type) : '') + '</span>' +
        '<span class="chip">' + esc(rentTxt(l)) + '</span>' +
        (l.address ? '<span class="chip">' + esc(String(l.address).slice(0, 40)) + '</span>' : '') +
        (l.phone ? '<span class="chip">' + phoneSpanHtml("landlord", l.id, l.phone) + '</span>' : '') +
        coldChip(dc) +
      '</div>' +
      (l.rooms ? '<div class="gap">' + esc(String(l.rooms).slice(0, 140)) + '</div>' : '') +
      (l.follow_up ? '<div class="gap">📝 ' + esc(String(l.follow_up).slice(0, 160)) + '</div>' : '') +
      actsHtml;
    const flagBtn = row.querySelector("[data-flag]");
    if (flagBtn) flagBtn.onclick = () => toggleFlaggedLandlord(l);
    box.appendChild(row);
  });
}

function allTenantRow(t) {
  const dc = Scoring.daysAgo(t.last_contact, TODAY);
  const missing = Array.isArray(t.missing) ? t.missing : [];
  const looking = t.looking === "Still looking";
  const row = el("div", "row");
  const areaHtml = '<a class="btn" target="_blank" rel="noopener noreferrer" href="' + escUrl(mapLink({ address: t.preferred_location, district: t.district })) + '">📍 Area</a>';
  const actsHtml = looking
    // coldBlocked(null, t), NOT false. This row builder was copied from the landlord
    // one, where a hardcoded false is correct because landlords are exempt. Carried onto
    // a TENANT row it bypassed the dead-lead rule entirely: 154 live wa.me links, 72 of
    // them to tenants over 30 days quiet, each with the message body already composed.
    ? '<div class="acts">' + areaHtml + ' ' + waButtonHtml(t.phone, coldBlocked(null, t) ? "" : tenantCheckInDraft(t), "WhatsApp", coldBlocked(null, t)) + callButtonHtml(t.phone, "Call", coldBlocked(null, t)) +
        (!t.phone ? '<span class="btn mut">no phone on file</span>' : '') + crmBtn("tenant", t) + '</div>'
    : '<div class="acts"><span class="btn mut">' + esc(String(t.looking || "").toLowerCase()) + ' — no contact action</span>' + crmBtn("tenant", t) + '</div>';
  row.innerHTML =
    '<div class="rtop"><span class="nm">' + esc(t.name || "?") + '</span> ' + tenantLookingChip(t.looking) +
      (missing.length ? '<span class="chip a">⚠ missing ' + esc(missing.join("/")) + '</span>' : '') +
      (DUP_PHONES.has(t.phone) ? '<span class="chip a">⚠ duplicate phone on file</span>' : '') +
    '</div>' +
    '<div class="rtop" style="margin-top:5px">' +
      '<span class="chip">' + esc(tenantWhere(t)) + '</span>' +
      '<span class="chip">budget ' + esc(t.budget != null ? t.budget : "?") + '</span>' +
      genderChip(t) +
      '<span class="chip">' + esc(t.pax != null ? t.pax : "?") + 'pax</span>' +
      '<span class="chip">move ' + esc(t.move_in || "?") + '</span>' +
      (t.phone ? '<span class="chip">' + phoneSpanHtml("alltenant", t.id, t.phone) + '</span>' : '') +
      coldChip(dc) +
    '</div>' +
    (t.listing_enquired ? '<div class="gap">enquired: ' + esc(t.listing_enquired) + '</div>' : '') +
    actsHtml;
  return row;
}
function renderAllTenantsRoster() {
  const box = $("#alltenants"); box.innerHTML = "";
  box.appendChild(el("div", "help", "🙋‍♀️ <b>Full tenant roster</b>, grouped by district — every tenant, every status (still looking, found a place, no longer looking). Tenants with no district on file sit in their own group at the end rather than being mixed in. Missing key info is flagged inline."));

  const f = F();
  let ts = (DATA.all_tenants || []).slice();
  if (f.d) ts = ts.filter(t => t.primary_district === f.d);
  if (f.q) {
    const hay = t => (String(t.name || "") + " " + (t.district || "") + " " + (t.preferred_location || "") + " " + (t.phone || "")).toLowerCase();
    ts = ts.filter(t => hay(t).includes(f.q));
  }
  if (!ts.length) { box.appendChild(el("div", "empty", "No tenants match the current search/district filter.")); return; }

  ts.sort((a, b) => String(a.primary_district || "zzz").localeCompare(String(b.primary_district || "zzz")) || ((a.sort != null ? a.sort : 99) - (b.sort != null ? b.sort : 99)));

  let shown = 0, curGroup = null;
  for (const t of ts) {
    if (shown >= 400) break;
    const g = t.primary_district || "";
    if (g !== curGroup) {
      curGroup = g;
      const label = g ? (g + " — " + (AREA[g] || "")) : "Unspecified location";
      const n = ts.filter(x => (x.primary_district || "") === g).length;
      box.appendChild(el("div", "section-hd", esc(label + " (" + n + ")")));
    }
    box.appendChild(allTenantRow(t));
    shown++;
  }
  if (ts.length > 400) box.appendChild(el("div", "mut", "showing first 400 of " + ts.length + " — narrow with search/district filters"));
}

function renderSalesRoster() {
  const box = $("#sales"); box.innerHTML = "";
  box.appendChild(el("div", "help", "🏡 <b>Sale listings</b> — landlord contacts flagged as a sale deal, not a rental, kept as a separate track. Asking price is parsed best effort from freeform notes — the raw note is always shown too since parsing SG price shorthand is not perfect."));

  const f = F();
  let ss = (DATA.sales || []).slice();
  if (f.d) ss = ss.filter(s => s.primary_district === f.d);
  if (f.q) {
    const hay = s => (String(s.name || "") + " " + (s.district || "") + " " + (s.address || "") + " " + (s.phone || "")).toLowerCase();
    ss = ss.filter(s => hay(s).includes(f.q));
  }
  ss.sort((a, b) => (a.sort != null ? a.sort : 99) - (b.sort != null ? b.sort : 99));
  if (!ss.length) { box.appendChild(el("div", "empty", "No sale listings match the current filters.")); return; }

  ss.forEach(s => {
    const dc = Scoring.daysAgo(s.last_contact, TODAY);
    const blockedContact = s.sale_status === "Closed";
    const row = el("div", "row");
    const mapHtml = '<a class="btn" target="_blank" rel="noopener noreferrer" href="' + escUrl(mapLink(s)) + '">📍 Map</a>';
    const actsHtml = blockedContact
      ? '<div class="acts">' + mapHtml + ' <span class="btn mut">closed — no contact action</span>' + crmBtn("sale", s) + '</div>'
      : '<div class="acts">' + mapHtml + ' ' + waButtonHtml(s.phone, saleCheckInDraft(s), "WhatsApp", false) + callButtonHtml(s.phone, "Call", false) +
          (!s.phone ? '<span class="btn mut">no phone on file</span>' : '') + crmBtn("sale", s) + '</div>';
    row.innerHTML =
      '<div class="rtop"><span class="nm">' + esc(s.name || "?") + '</span> ' + saleStatusChip(s.sale_status) +
        (s.source === "co-broke" ? '<span class="chip">co-broke</span>' + ceaChip(s) : '') +
      '</div>' +
      '<div class="rtop" style="margin-top:5px">' +
        '<span class="chip">' + esc(s.district || "?") + (s.property_type ? ' · ' + esc(s.property_type) : '') + '</span>' +
        '<span class="chip">' + (s.asking_price ? 'asking ~$' + esc(Number(s.asking_price).toLocaleString()) : 'price TBC') + '</span>' +
        (s.address ? '<span class="chip">' + esc(String(s.address).slice(0, 40)) + '</span>' : '') +
        (s.phone ? '<span class="chip">' + phoneSpanHtml("sale", s.id, s.phone) + '</span>' : '') +
        coldChip(dc) +
      '</div>' +
      (s.price_text ? '<div class="gap">' + esc(String(s.price_text).slice(0, 140)) + '</div>' : '') +
      (s.follow_up ? '<div class="gap">📝 ' + esc(String(s.follow_up).slice(0, 160)) + '</div>' : '') +
      actsHtml;
    box.appendChild(row);
  });
}

// ---- revival tab — reuses revival_board.py's own scan (exported into DATA.revival by
// export_data.py, not recomputed here). It has no EV/priority score of its own, only a
// good/weak/none match tier, so rows are ranked in whatever order DATA.revival provides.
function renderRevival() {
  const box = $("#revival"); box.innerHTML = "";
  box.appendChild(el("div", "help", "♻️ <b>Revival board</b> — still looking tenants past the lead cutoff, cross checked against currently available listings. This reuses revival_board.py's own matching (not the main score engine above), so there is no 0 to 100 score here, only a good/weak/none match tier. Review list only, nothing sends itself."));

  const rows = DATA.revival || [];
  if (!rows.length) { box.appendChild(el("div", "empty", "No revival candidates — every still looking tenant is within the lead cutoff.")); return; }

  const f = F();
  let rs = rows.slice();
  if (f.q) {
    const hay = r => (String(r.name || "") + " " + (r.district || "") + " " + (r.phone || "")).toLowerCase();
    rs = rs.filter(r => hay(r).includes(f.q));
  }
  if (f.d) rs = rs.filter(r => r.district === f.d);
  if (!rs.length) { box.appendChild(el("div", "empty", "No revival candidates match the current search/district filter.")); return; }

  rs.forEach((r, i) => {
    const snippet = r.match
      ? "best option: " + (r.match.name || r.match.id || "(no name saved)") + " · " + (r.match.district || "") + " · " + (r.match.rent_min || r.match.rent_max ? ("$" + (r.match.rent_min || r.match.rent_max)) : "rent TBC")
      : "no current listing fits";
    const row = el("div", "row");
    row.innerHTML =
      '<div class="rtop"><span class="sc" style="min-width:24px">#' + (i + 1) + '</span><span class="nm">' + esc(r.name || "?") + '</span>' + revivalTierChip(r.tier) + '</div>' +
      '<div class="rtop" style="margin-top:5px">' +
        '<span class="chip">' + esc(r.district || "?") + '</span>' +
        '<span class="chip">budget ' + esc(r.budget != null ? r.budget : "?") + '</span>' +
        '<span class="chip">' + esc(r.pax != null ? r.pax : "?") + 'pax</span>' +
        (r.phone ? '<span class="chip">' + phoneSpanHtml("revival", r.phone, r.phone) + '</span>' : '') +
        (r.days_quiet != null ? '<span class="chip r">quiet ' + esc(r.days_quiet) + 'd</span>' : '<span class="chip">no contact date</span>') +
      '</div>' +
      '<div class="gap">' + esc(snippet) + '</div>' +
      (r.phone
        // Every row on this tab is by definition past the 30 day cutoff, so a hardcoded
        // false here meant 72 of 72 rows offered a one-tap send with the body already
        // written — to exactly the people Winfred's rule says never to re-engage, and the
        // same shape of mistake as the July backfill blast. The rule wins until he says
        // otherwise; the tab still shows WHO went cold and what would have matched them,
        // which is the part worth looking at.
        ? '<div class="acts">' + waButtonHtml(r.phone, "", "WhatsApp", true) + callButtonHtml(r.phone, "Call", true) + '</div>'
        : '<div class="acts"><span class="btn mut">no phone on file</span></div>');
    box.appendChild(row);
  });
}

// ===================== dispatch / queued drawer =====================
function queuedMatches() { return MATCHES.filter(m => { const mk = readMark(m.l.id, m.t.id); return mk && mk.v === "Queued"; }); }
function openDispatchDrawer() {
  const wrap = el("div", "drawer-wrap");
  const items = queuedMatches();
  let html = '<div class="drawer"><h2>Dispatch (' + items.length + ')</h2>' +
    '<div class="mut" style="font-size:12px;margin-bottom:10px">send happens via your morning dispatch after you run the queue command — nothing sends from this page</div>';
  if (!items.length) html += '<div class="empty">Nothing queued yet. Mark a tenant Queued from the worklist or a listing panel.</div>';
  items.forEach((m, i) => {
    html += '<div class="row"><div class="nm">' + esc(m.t.name) + '</div><div class="mut" style="font-size:12px">' + esc(m.l.name) + ' · ' + esc(m.l.district) + '</div>' +
      '<div class="draftbox">' + esc(draftFor(m.l, m.t)) + '</div>' +
      '<div class="acts"><button class="btn" data-dcopy="' + i + '">Copy</button><button class="btn" data-dunq="' + i + '">Unqueue</button></div></div>';
  });
  html += '<div class="acts" style="margin-top:12px"><button class="btn p full" data-exportq="1">Export queue JSON</button><button class="btn full" data-closedrawer="1">Close</button></div></div>';
  wrap.innerHTML = html;
  mountOverlay(wrap);
  wrap.querySelectorAll("[data-dcopy]").forEach(b => b.onclick = () => { const m = items[+b.dataset.dcopy]; navigator.clipboard.writeText(draftFor(m.l, m.t)); b.textContent = "Copied"; });
  wrap.querySelectorAll("[data-dunq]").forEach(b => b.onclick = () => {
    const m = items[+b.dataset.dunq]; wrap.remove();
    writeMarkClearUndoable(m.l.id, m.t.id, fname(m.t.name) + " unqueued");
    openDispatchDrawer();
  });
  wrap.querySelector("[data-exportq]").onclick = () => exportQueueJSON(items);
  wrap.querySelector("[data-closedrawer]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
function exportQueueJSON(items) {
  const payload = { items: items.map(m => ({ tenant_id: m.t.id, phone: m.t.phone || "", name: m.t.name, message: draftFor(m.l, m.t) })) };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "matchmaker-queue-" + DATA.generated + ".json";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// ===================== decline reason + lookalike + try instead =====================
const DECLINE_REASONS = [["budget", "Budget"], ["location", "Location"], ["timing", "Timing"], ["landlord_rejected", "Landlord rejected"], ["other", "Other"]];
function openDeclineModal(l, t) {
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>Why not interested — ' + esc(t.name) + '</h3><div class="row-x">' +
    DECLINE_REASONS.map(r => '<button class="pick" data-reason="' + esc(r[0]) + '">' + esc(r[1]) + '</button>').join('') +
    '</div><div class="qfield" style="margin-top:10px"><label>Note (optional)</label><input type="text" data-note="1"></div>' +
    '<div class="foot"><button class="btn" data-cancel="1">Cancel</button></div></div>';
  // initialFocus override: the reason buttons are the first controls in DOM
  // order, but a single click on any one of them commits "Not interested"
  // immediately (no separate confirm step) — exactly the destructive-button
  // case the fix list says never to autofocus. Cancel is the safe default.
  mountOverlay(wrap, { initialFocus: "[data-cancel]" });
  wrap.querySelectorAll("[data-reason]").forEach(b => b.onclick = () => {
    const note = wrap.querySelector("[data-note]").value.trim();
    const priors = [{ lid: l.id, tid: t.id, prior: readMark(l.id, t.id) }];
    patchMark(l.id, t.id, { v: "Not interested", reason: b.dataset.reason, note: note || undefined });
    wrap.remove();
    undoToast(fname(t.name) + " marked not interested", () => restoreMarks(priors));
    render();
    showTryInstead(t, l);
  });
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
function showTryInstead(t, l) {
  const opts = (byTenant[t.id] || []).filter(m => m.l.id !== l.id).filter(m => {
    const mk = readMark(m.l.id, t.id);
    return !(mk && mk.v === "Not interested") && effective(m).verdict !== "BLOCKED" && !isSnoozedNow(m);
  }).slice(0, 2);
  if (!opts.length) return;
  const wrap = el("div", "modal-wrap");
  let html = '<div class="modal"><h3>Try instead for ' + esc(t.name) + '</h3>';
  opts.forEach(m => {
    // Same cold rule as every other tenant facing WhatsApp button — this panel
    // pops up right after a decline and used to hand out a live draft link
    // regardless of how long the tenant had been silent.
    const blocked = coldBlocked(m.l, t);
    const action = !t.phone ? ''
      : blocked ? ('<span class="btn disabled" title="' + coldTitle() + '">WhatsApp draft</span>')
      : linkButtonHtml(waLink(m.l, t), "WhatsApp draft");
    html += '<div class="tryinstead"><b>' + esc(m.l.name) + '</b> · ' + esc(m.l.district) + ' · ' + esc(rentTxt(m.l)) + ' · score ' + esc(m.s.total) +
      '<div class="acts" style="margin-top:6px">' + action + '</div></div>';
  });
  html += '<div class="foot"><button class="btn" data-cancel="1">Close</button></div></div>';
  wrap.innerHTML = html;
  mountOverlay(wrap);
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}

// ===================== snooze =====================
function snoozedActive() { return MATCHES.filter(isSnoozedNow); }
function openSnoozeModal(l, t) {
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>Snooze — ' + esc(t.name) + ' · ' + esc(l.name) + '</h3><div class="row-x">' +
    '<button class="pick" data-days="3">+3d</button><button class="pick" data-days="7">+1w</button><button class="pick" data-days="14">+2w</button>' +
    '</div><div class="qfield" style="margin-top:10px"><label>Or pick a date</label><input type="date" data-date="1"></div>' +
    '<div class="qfield"><label>Note (optional)</label><input type="text" data-note="1"></div>' +
    '<div class="foot"><button class="btn" data-cancel="1">Cancel</button><button class="btn p" data-apply="1">Snooze</button></div></div>';
  mountOverlay(wrap);
  const dateInput = wrap.querySelector("[data-date]");
  // "+3d" has to mean 3 days from now, not 3 days from the dataset's stamp —
  // otherwise a stale dataset resolves the quick buttons to dates in the past
  // and the pair un-snoozes immediately. Based off NOW_REAL_SGT (the real
  // Singapore day), not NOW_REAL, so "+3d" near midnight lands on the same
  // intuitive day regardless of which timezone the device is currently in.
  wrap.querySelectorAll("[data-days]").forEach(b => b.onclick = () => { const d = new Date(NOW_REAL_SGT); d.setDate(d.getDate() + (+b.dataset.days)); dateInput.value = isoLocal(d); });
  wrap.querySelector("[data-apply]").onclick = () => {
    if (!dateInput.value) return;
    const note = wrap.querySelector("[data-note]").value.trim();
    const priors = [{ lid: l.id, tid: t.id, prior: readMark(l.id, t.id) }];
    patchMark(l.id, t.id, { snooze_until: dateInput.value, note: note || undefined });
    wrap.remove();
    undoToast(fname(t.name) + " snoozed to " + dateInput.value, () => restoreMarks(priors));
    render();
  };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
function openSnoozedList() {
  const wrap = el("div", "drawer-wrap");
  const items = snoozedActive();
  let html = '<div class="drawer"><h2>Snoozed (' + items.length + ')</h2>';
  if (!items.length) html += '<div class="empty">Nothing snoozed right now. Snooze a pair from its Snooze button and it will show up here until the date you picked.</div>';
  items.forEach((m, i) => {
    const mk = readMark(m.l.id, m.t.id) || {};
    html += '<div class="row"><div class="nm">' + esc(m.t.name) + '</div><div class="mut" style="font-size:12px">' + esc(m.l.name) + ' · until ' + esc(mk.snooze_until) + (mk.note ? (' · ' + esc(mk.note)) : '') + '</div>' +
      '<div class="acts"><button class="btn" data-wake="' + i + '">Wake now</button></div></div>';
  });
  html += '<div class="acts" style="margin-top:12px"><button class="btn full" data-closedrawer="1">Close</button></div></div>';
  wrap.innerHTML = html;
  // onEscape mirrors the Close button exactly (remove + render), not the
  // default plain remove() — a "Wake now" done earlier in this session
  // already re-renders the background itself, but Escape should behave
  // identically to every other way of leaving this drawer.
  mountOverlay(wrap, { onEscape: () => { wrap.remove(); render(); } });
  wrap.querySelectorAll("[data-wake]").forEach(b => b.onclick = () => {
    const m = items[+b.dataset.wake];
    const priors = [{ lid: m.l.id, tid: m.t.id, prior: readMark(m.l.id, m.t.id) }];
    patchMark(m.l.id, m.t.id, { snooze_until: undefined }); // JSON.stringify drops undefined keys — clean removal
    wrap.remove();
    undoToast(fname(m.t.name) + " woken", () => restoreMarks(priors));
    openSnoozedList(); render();
  });
  wrap.querySelector("[data-closedrawer]").onclick = () => { wrap.remove(); render(); };
  wrap.onclick = (e) => { if (e.target === wrap) { wrap.remove(); render(); } };
}

// ===================== quick add scratch tenant =====================
function field(key, label, type) { return '<div class="qfield"><label>' + label + '</label><input type="' + type + '" data-f="' + key + '"></div>'; }
function openQuickAddTenant() {
  const districts = [...new Set((DATA.listings || []).map(l => l.district).filter(Boolean))].sort();
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>Add tenant (scratch)</h3>' +
    field("name", "Name", "text") + field("phone", "Phone", "tel") +
    '<div class="qfield"><label>Budget (or min to max)</label><div class="row-x"><input type="number" placeholder="budget" data-f="budget"><input type="number" placeholder="min" data-f="budget_min"><input type="number" placeholder="max" data-f="budget_max"></div></div>' +
    field("pax", "Pax", "number") + field("move_in", "Move in", "date") + field("lease_months", "Lease months", "number") +
    '<div class="qfield"><label>Preferred districts</label><div class="row-x">' + districts.map(d => '<button class="pick" data-dist="' + esc(d) + '">' + esc(d) + '</button>').join('') + '</div></div>' +
    field("gender", "Gender (optional)", "text") + field("nationality", "Nationality (optional)", "text") +
    '<div class="foot"><button class="btn" data-cancel="1">Cancel</button><button class="btn p" data-add="1">Add</button></div></div>';
  mountOverlay(wrap);
  const chosen = new Set();
  wrap.querySelectorAll("[data-dist]").forEach(b => b.onclick = () => {
    const d = b.dataset.dist;
    if (chosen.has(d)) { chosen.delete(d); b.classList.remove("sel"); } else { chosen.add(d); b.classList.add("sel"); }
  });
  wrap.querySelector("[data-add]").onclick = () => {
    const get = f => { const e = wrap.querySelector('[data-f="' + f + '"]'); return e ? e.value.trim() : ""; };
    const name = get("name");
    if (!name) { wrap.querySelector('[data-f="name"]').focus(); return; }
    const t = {
      name, phone: get("phone") || "",
      budget: get("budget") ? +get("budget") : null,
      budget_min: get("budget_min") ? +get("budget_min") : null,
      budget_max: get("budget_max") ? +get("budget_max") : null,
      pax: get("pax") ? +get("pax") : null,
      move_in: get("move_in") || "",
      lease_months: get("lease_months") ? +get("lease_months") : null,
      preferred_districts: [...chosen], district: "",
      gender: get("gender") || "", nationality: get("nationality") || "",
      ethnicity: "", pass_type: "", occupation: "",
      // Real date: a tenant you are adding right now must not be born cold
      // because the dataset stamp is a week old. NOW_REAL_SGT so "today" means
      // today in Singapore, matching how every other last_contact date in this
      // app is interpreted, regardless of which device added this person.
      last_contact: isoLocal(NOW_REAL_SGT), status: "scratch"
    };
    addScratchTenant(t);
    wrap.remove();
    rebuildMatches();
    render();
  };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}

// ===================== command palette (51) =====================
// Fuzzy match here means substring match plus a small curated district
// alias table (area names from DATA.districts, common D-number shorthand,
// and a handful of common misspellings) — not full fuzzy string distance.
// Good enough for a personal tool's own tenant/listing names and districts.
const DISTRICT_ALIASES = (function () {
  const map = {};
  Object.keys(AREA).forEach(d => {
    map[d.toLowerCase()] = d;
    (AREA[d] || "").toLowerCase().split(/[\/,]/).forEach(a => { a = a.trim(); if (a) map[a] = d; });
  });
  Object.assign(map, {
    cbd: "D1", orchard: "D9", novena: "D11", "toa payoh": "D12", bishan: "D20",
    tampines: "D18", "bukit timah": "D10", holland: "D10", clementi: "D5",
    jurong: "D22", "jurong east": "D22", "jurong west": "D22", punggol: "D19",
    sengkang: "D19", yishun: "D27", woodlands: "D25", serangoon: "D19",
    queenstown: "D3", "pasir ris": "D18", changi: "D17", sentosa: "D4",
    "east coast": "D15", katong: "D15", "tiong bahru": "D3",
    novna: "D11", orchad: "D9", jurng: "D22" // common misspellings
  });
  return map;
})();
function resolveDistrictAlias(term) { return DISTRICT_ALIASES[term.toLowerCase().trim()] || null; }
function paletteActions() {
  return [
    { label: "Open Worklist", run: () => { view = "work"; render(); } },
    { label: "Open Listings", run: () => { view = "listing"; render(); } },
    { label: "Open Tenants", run: () => { view = "tenant"; render(); } },
    { label: "Open Whole unit", run: () => { view = "whole"; render(); } },
    { label: "Open Stats", run: () => { view = "stats"; render(); } },
    { label: "Add tenant", run: () => openQuickAddTenant() },
    { label: "Open Dispatch", run: () => openDispatchDrawer() },
    { label: "Open Snoozed", run: () => openSnoozedList() },
    { label: "Bulk action on filtered set", run: () => openBulkActionModal() },
    { label: "Export state", run: () => downloadJSON(exportBlob(), "matchmaker-state-" + DATA.generated + ".json") },
    { label: "Toggle day/night", run: () => toggleTheme() },
    { label: "Toggle density", run: () => toggleDensity() }
  ];
}
function paletteResults(query) {
  const q = query.toLowerCase().trim();
  if (!q) return [];
  const dist = resolveDistrictAlias(q);
  const out = [];
  paletteActions().forEach(a => { if (a.label.toLowerCase().indexOf(q) !== -1) out.push({ kind: "action", label: a.label, run: a.run }); });
  (DATA.listings || []).forEach(l => {
    const hay = (l.name + " " + l.district + " " + (l.address || "")).toLowerCase();
    if (hay.indexOf(q) !== -1 || (dist && l.district === dist)) out.push({ kind: "listing", label: l.name + " · " + l.district, run: () => { view = "listing"; curL = l.id; render(); } });
  });
  ALL_TENANTS.forEach(t => {
    const hay = (t.name + " " + (t.preferred_location || "") + " " + (t.district || "")).toLowerCase();
    if (hay.indexOf(q) !== -1 || (dist && (t.district === dist || (t.preferred_districts || []).indexOf(dist) !== -1))) out.push({ kind: "tenant", label: t.name, run: () => { view = "tenant"; curT = t.id; render(); } });
  });
  return out.slice(0, 20);
}
function openCommandPalette() {
  const wrap = el("div", "modal-wrap palette-wrap");
  wrap.innerHTML = '<div class="modal palette"><input type="text" placeholder="Find a tenant, listing, or action…" data-pq="1" aria-label="Find a tenant, listing, or action">' +
    '<div data-presults="1"></div><div class="sr-only" role="status" aria-live="polite" data-pcount="1"></div></div>';
  mountOverlay(wrap, { label: "Command palette" });
  const input = wrap.querySelector("[data-pq]"), results = wrap.querySelector("[data-presults]"), countEl = wrap.querySelector("[data-pcount]");
  // (78) keyboard selection — this command palette used to be filterable and
  // closeable by keyboard but not actually USABLE by keyboard: nothing let
  // you pick a result without reaching for the mouse/trackpad, defeating the
  // point of a cmd+K launcher. Up/Down move curItems, Enter runs it; .sel
  // reuses the same highlight class the district/reason pick chips already
  // use elsewhere, so this needed no new CSS.
  let curItems = [], selIdx = 0;
  function highlightSel() {
    results.querySelectorAll("[data-pidx]").forEach(r => r.classList.toggle("sel", +r.dataset.pidx === selIdx));
    const selEl = results.querySelector('[data-pidx="' + selIdx + '"]');
    if (selEl) selEl.scrollIntoView({ block: "nearest" });
  }
  function paint() {
    curItems = paletteResults(input.value);
    selIdx = 0;
    results.innerHTML = curItems.length
      ? curItems.map((it, i) => '<div class="pick" data-pidx="' + i + '" style="display:block;margin-top:4px">' + esc(it.label) + ' <span class="mut">' + esc(it.kind) + '</span></div>').join('')
      : '<div class="mut" style="padding:8px 0">Type to search tenants, listings, or actions. District names, D numbers, and a few common area aliases all work.</div>';
    results.querySelectorAll("[data-pidx]").forEach(r => r.onclick = () => { curItems[+r.dataset.pidx].run(); wrap.remove(); });
    // (item 2) result count announced politely as the user filters — separate
    // from `results` itself, which stays non-live so a screen reader isn't
    // forced to read out every one of up to 20 result rows on each keystroke.
    countEl.textContent = input.value.trim() ? (curItems.length + (curItems.length === 1 ? " result" : " results")) : "";
    highlightSel();
  }
  input.oninput = paint;
  input.addEventListener("keydown", (e) => {
    if (!curItems.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); selIdx = Math.min(curItems.length - 1, selIdx + 1); highlightSel(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); selIdx = Math.max(0, selIdx - 1); highlightSel(); }
    else if (e.key === "Enter") { e.preventDefault(); curItems[selIdx].run(); wrap.remove(); }
  });
  paint();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
  // Escape-to-close and initial focus (the search input, being the only
  // focusable element in this dialog) are both handled centrally now — see
  // mountOverlay above.
}

// ===================== theme (53) / density (54) =====================
function applyThemeClass() {
  document.documentElement.setAttribute("data-theme", PREFS.theme || "");
  const btn = $("#themebtn");
  if (btn) {
    // Visible text is emoji only — aria-label is the only accessible name.
    btn.textContent = PREFS.theme === "light" ? "☀️" : PREFS.theme === "dark" ? "🌙" : "🌓";
    btn.setAttribute("aria-label", "Theme: " + (PREFS.theme === "light" ? "light" : PREFS.theme === "dark" ? "dark" : "auto") + " — tap to change");
  }
}
function toggleTheme() {
  PREFS.theme = PREFS.theme === "dark" ? "light" : (PREFS.theme === "light" ? null : "dark");
  savePrefs(); applyThemeClass();
}
function applyDensityClass() {
  document.body.classList.toggle("density-compact", PREFS.density === "compact");
  const btn = $("#densitybtn");
  if (btn) {
    btn.textContent = PREFS.density === "compact" ? "☰ Compact" : "☰ Card";
    btn.setAttribute("aria-label", "Density: " + (PREFS.density === "compact" ? "compact" : "card") + " — tap to change");
  }
}
function toggleDensity() {
  PREFS.density = PREFS.density === "compact" ? "card" : "compact";
  savePrefs(); applyDensityClass();
}

// ===================== idle lock (20)/(49) =====================
// Honest label: this is a privacy screen (stop a shoulder surfer glancing at
// an open tab), NOT encryption — the data underneath is never re encrypted,
// just visually hidden until the 4 digit code is entered.
async function sha256Hex(str) {
  try {
    const enc = new TextEncoder().encode(str);
    const buf = await crypto.subtle.digest("SHA-256", enc);
    return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
  } catch (e) {
    // crypto.subtle needs a secure context (https/localhost) — a file:// preview
    // or plain http won't have it. Fall back to a simple non cryptographic hash
    // so the idle lock still works as a screen lock, just an honestly weaker one.
    let h = 0; for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
    return "fallback-" + h.toString(16);
  }
}
function resetIdleTimer() {
  PREFS.last_active = Date.now();
  clearTimeout(IDLE_TIMER);
  IDLE_TIMER = setTimeout(showIdleLock, IDLE_MS);
}
function showIdleLock() {
  if ($("#idlelock")) return;
  const overlay = el("div", "idle-overlay");
  overlay.id = "idlelock";
  const firstUse = !PREFS.lock_code_hash;
  overlay.innerHTML = '<div class="idle-lock">' +
    '<div class="hd">🔒 Privacy screen</div>' +
    '<div class="mut" style="margin-bottom:10px">This is a privacy screen, not encryption — it just hides the tab from view. Enter your 4 digit code to continue.</div>' +
    (firstUse ? '<div class="mut">First time — set a 4 digit code:</div>' : '') +
    '<input type="password" inputmode="numeric" maxlength="4" data-code="1">' +
    (firstUse ? '<input type="password" inputmode="numeric" maxlength="4" data-code2="1" placeholder="confirm">' : '') +
    '<div class="mut" data-idleerr="1" style="color:#ff6b78;min-height:16px"></div>' +
    '<button class="btn p full" data-unlock="1">' + (firstUse ? "Set code and continue" : "Unlock") + '</button></div>';
  // closeOnEscape:false — this is a privacy screen, not a dismissible
  // dialog; it must only ever close via the 4 digit code, never a keypress.
  // No backdrop-click handler is added anywhere below either, for the same
  // reason (mountOverlay never adds one on its own — see its own comment).
  mountOverlay(overlay, { label: "Privacy screen", closeOnEscape: false });
  const codeI = overlay.querySelector("[data-code]");
  const err = overlay.querySelector("[data-idleerr]");
  overlay.querySelector("[data-unlock]").onclick = async () => {
    if (firstUse) {
      const c1 = codeI.value.trim(), c2 = overlay.querySelector("[data-code2]").value.trim();
      if (!/^\d{4}$/.test(c1) || c1 !== c2) { err.textContent = "Enter matching 4 digit codes."; return; }
      PREFS.lock_code_hash = await sha256Hex(c1);
      savePrefs(); overlay.remove(); resetIdleTimer();
      return;
    }
    const hash = await sha256Hex(codeI.value.trim());
    if (hash === PREFS.lock_code_hash) { overlay.remove(); resetIdleTimer(); }
    else { err.textContent = "Wrong code."; codeI.value = ""; codeI.focus(); }
  };
  // Initial focus (the code input, being the first focusable element in this
  // overlay) is handled centrally now — see mountOverlay above.
}

// ===================== bulk action (59) =====================
function currentFilteredWorklistSet() {
  return MATCHES.filter(m => effective(m).verdict !== "BLOCKED" && m.l.availability !== "Offer pending").filter(m => !isSnoozedNow(m)).filter(passFilter);
}
function openBulkActionModal() {
  const set = currentFilteredWorklistSet();
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>Bulk action — ' + set.length + ' filtered rows</h3>' +
    (set.length ? '' : '<div class="empty">Nothing matches the current filters. Adjust a filter above first.</div>') +
    '<div class="row-x">' +
    '<button class="pick" data-v="Contacted">Mark Contacted</button>' +
    '<button class="pick" data-v="Queued">Mark Queued</button>' +
    '<button class="pick" data-v="Not interested">Mark not interested</button>' +
    '</div>' +
    '<div class="qfield" id="bulkreasonwrap" style="display:none;margin-top:10px"><label>Reason</label><select data-bulkreason="1">' +
    DECLINE_REASONS.map(r => '<option value="' + r[0] + '">' + r[1] + '</option>').join('') + '</select></div>' +
    '<div class="foot"><button class="btn" data-cancel="1">Cancel</button><button class="btn p" data-apply="1" disabled>Apply</button></div></div>';
  mountOverlay(wrap);
  let chosenV = null;
  wrap.querySelectorAll("[data-v]").forEach(b => b.onclick = () => {
    wrap.querySelectorAll("[data-v]").forEach(x => x.classList.remove("sel"));
    b.classList.add("sel"); chosenV = b.dataset.v;
    wrap.querySelector("#bulkreasonwrap").style.display = chosenV === "Not interested" ? "block" : "none";
    wrap.querySelector("[data-apply]").disabled = false;
  });
  wrap.querySelector("[data-apply]").onclick = () => {
    if (!chosenV || !set.length) { wrap.remove(); return; }
    const patch = { v: chosenV };
    if (chosenV === "Not interested") patch.reason = wrap.querySelector("[data-bulkreason]").value;
    wrap.remove();
    // Bulk Queued is still an outbound action: cold pairs drop out of the set
    // rather than the whole bulk being refused. Contacted / Not interested are
    // bookkeeping and apply to everything as before.
    const target = chosenV === "Queued" ? set.filter(m => !coldBlocked(m.l, m.t)) : set;
    if (!target.length) { toast("Every row in that set is quiet over 30 days — nothing queued"); return; }
    const skipped = set.length - target.length;
    bulkApplyMark(target, patch, chosenV + " applied to " + target.length + " rows" + (skipped ? (" (" + skipped + " cold skipped)") : ""));
  };
  wrap.querySelector("[data-cancel]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}

// ===================== PWA (52) =====================
// Best effort only — must fail silently. /sw.js and /manifest.json (deploy/
// side, data lane's) only resolve once this is actually deployed to Vercel;
// a 404 in any other context (local file preview, before first deploy) is
// normal and must never surface as a scary console error.
// register() logs its own failure straight to the console, and a .catch() on
// the returned promise cannot suppress that — which is why the local artifact
// showed a 404 for /sw.js on every load. Probe first instead: a fetch that
// comes back 404 is an ordinary resolved response and stays silent, so nothing
// reaches the console unless the worker genuinely exists. Also skip outright
// on file:// and plain http, where a service worker cannot register at all.
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  // Deployed origin only. Locally — file://, plain http, or a throwaway
  // localhost server over the _local artifact — /sw.js does not exist and the
  // PWA buys nothing, and even a mere probe for a missing file surfaces as a
  // console 404. So make no request at all outside the real deployment: a
  // clean local load has to be completely silent.
  const deployed = location.protocol === "https:" &&
    location.hostname !== "localhost" && location.hostname !== "127.0.0.1";
  if (!deployed) return;
  // On the deployment, still probe before registering: register() reports its
  // own failure straight to the console and .catch() cannot suppress that.
  fetch("/sw.js", { method: "HEAD" })
    .then(r => (r && r.ok) ? navigator.serviceWorker.register("/sw.js") : null)
    .catch(() => {});
}

// ===================== stats tab (13/27/45/47/48/49/62/69) =====================
function funnelHtml() {
  const f = Scoring.weeklyFunnel(readRingBuffer(HISTORY_KEY), NOW_REAL_SGT); // real Singapore calendar week, see NOW_REAL_SGT's comment
  return '<div class="stats-grid">' +
    '<div class="stat-card"><b>' + f.contacted + '</b><div class="mut">contacted this week</div></div>' +
    '<div class="stat-card"><b>' + f.viewings + '</b><div class="mut">viewings booked this week</div></div>' +
    '<div class="stat-card"><b>' + f.conversionPct + '%</b><div class="mut">conversion</div></div>' +
    '</div>';
}
// (27) per listing decline reason histogram. Deliberately reason CODES only
// (budget/location/timing/landlord_rejected/other) — this repo's compliance
// rule frames landlord gates as "landlord preference" everywhere else, and a
// breakdown by tenant ethnicity/nationality/gender would read as protected
// attribute profiling even in a private, PDPA gated tool. Out of bounds here;
// see the build report for this judgment call.
function rejectionHistogramHtml() {
  const byListingReason = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k || k.indexOf(MARK_PREFIX) !== 0 || k === SCRATCH_KEY || k.indexOf(OFFER_PREFIX) === 0) continue;
    if ([PREFS_KEY, HISTORY_KEY, ERR_KEY].indexOf(k) !== -1 || k.indexOf("cbk_backup_") === 0) continue;
    const rest = k.slice(MARK_PREFIX.length);
    const us = rest.indexOf("_");
    if (us === -1) continue;
    const lid = rest.slice(0, us);
    const mk = readMark(lid, rest.slice(us + 1));
    if (!mk || mk.v !== "Not interested" || !mk.reason) continue;
    (byListingReason[lid] = byListingReason[lid] || {})[mk.reason] = (byListingReason[lid][mk.reason] || 0) + 1;
  }
  const lids = Object.keys(byListingReason);
  if (!lids.length) return '<div class="empty">No declines logged yet. This fills in as you mark tenants not interested with a reason.</div>';
  return lids.map(lid => {
    const l = (DATA.listings || []).find(x => x.id === lid);
    const reasons = byListingReason[lid];
    const total = Object.values(reasons).reduce((a, b) => a + b, 0);
    const parts = Object.keys(reasons).map(r => reasons[r] + " " + String(r).replace(/_/g, " ")).join(", ");
    return '<div class="row"><b>' + esc(l ? l.name : lid) + '</b> — ' + esc(total) + ' declined: ' + esc(parts) + '</div>';
  }).join("");
}
// (45) tenant missing field lists fall back to computing from raw fields when
// t.missing[] is absent (v1 payload); listing set mirrors export_data.py's
// own compute_health() "unparsed" predicate exactly so counts agree.
function tenantsMissing(field) {
  return ALL_TENANTS.filter(t => Array.isArray(t.missing) ? t.missing.indexOf(field) !== -1 : (
    field === "budget" ? (t.budget == null && t.budget_max == null) :
    field === "move_in" ? !t.move_in :
    field === "pax" ? t.pax == null :
    field === "lease_months" ? t.lease_months == null :
    field === "district" ? !t.district : false));
}
function isUnparsedReqRaw(l) {
  const g = l.gates || {};
  return !!(l.req_raw && Object.keys(l.req_raw).length) && g.gender === "any" &&
    g.ethnicity && (g.ethnicity.rule === "any" || g.ethnicity.rule === "note") &&
    g.max_pax == null && g.lease_min == null && !g.cooking && !g.pets && !g.smoking;
}
function renderHealthRow(container, label, field, list, kind) {
  const row = el("div", "row");
  const head = el("div", "rtop", '<b>' + esc(list.length) + '</b> ' + esc(label));
  row.appendChild(head);
  if (!list.length) { container.appendChild(row); return; }
  const toggleBtn = el("button", "btn", "show");
  head.appendChild(toggleBtn);
  const detail = el("div", ""); detail.style.cssText = "display:none;margin-top:8px";
  list.forEach(x => {
    // Landlords (kind === "listing") are exempt from the 30 day dead rule; a cold
    // tenant gets no question drafted at all, not just a greyed Copy button.
    const cold = kind === "tenant" && coldBlocked(null, x);
    if (cold) {
      const line = el("div", "", coldRefusalHtml(x));
      detail.appendChild(line);
      return;
    }
    const draft = kind === "tenant" ? oneQuestionDraft(x, field) : landlordOneQuestionDraft(x);
    const line = el("div", "", '<div class="draftbox">' + esc(x.name + ": " + draft) + '</div>');
    const acts = el("div", "acts"); acts.style.marginBottom = "6px";
    const cp = el("button", "btn", "Copy");
    cp.onclick = () => { navigator.clipboard.writeText(draft); cp.textContent = "Copied"; };
    acts.appendChild(cp);
    line.appendChild(acts);
    detail.appendChild(line);
  });
  toggleBtn.onclick = () => { const show = detail.style.display === "none"; detail.style.display = show ? "block" : "none"; toggleBtn.textContent = show ? "hide" : "show"; };
  row.appendChild(detail);
  container.appendChild(row);
}
function renderHealthSection(container) {
  container.appendChild(el("div", "section-hd", "Data health"));
  renderHealthRow(container, "tenants missing budget", "budget", tenantsMissing("budget"), "tenant");
  renderHealthRow(container, "tenants missing move in date", "move_in", tenantsMissing("move_in"), "tenant");
  renderHealthRow(container, "tenants missing pax", "pax", tenantsMissing("pax"), "tenant");
  renderHealthRow(container, "tenants missing lease length", "lease_months", tenantsMissing("lease_months"), "tenant");
  renderHealthRow(container, "tenants missing district", "district", tenantsMissing("district"), "tenant");
  renderHealthRow(container, "listings with unparsed requirements", null, (DATA.listings || []).filter(isUnparsedReqRaw), "listing");
}
function diagnosticsText() {
  const errs = readRingBuffer(ERR_KEY);
  const lines = [
    "Crestbrick Matchmaker diagnostics",
    "generated: " + DATA.generated + " (" + (DATA.generated_ts || "no ts") + ")",
    "schema_version: " + DATA.schema_version, "build_id: " + (DATA.build_id || "?"),
    "userAgent: " + navigator.userAgent, "errors (" + errs.length + "):"
  ];
  errs.forEach(e => lines.push("  [" + new Date(e.ts).toISOString() + "] " + e.kind + ": " + e.msg + (e.src ? (" @ " + e.src + ":" + e.line + ":" + e.col) : "")));
  return lines.join("\n");
}
// (62) build_history is [{ts,listings,tenants,worklist_size,health_total}] —
// sparkline plots health_total (lower is better); absent -> section omits.
function sparklineSvg(values, w, h) {
  if (!values.length) return "";
  const max = Math.max.apply(null, values), min = Math.min.apply(null, values);
  const range = (max - min) || 1;
  const step = values.length > 1 ? w / (values.length - 1) : 0;
  const pts = values.map((v, i) => (i * step) + "," + (h - ((v - min) / range) * h)).join(" ");
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" class="sparkline"><polyline points="' + pts + '" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
}
function buildHistoryHtml() {
  const hist = DATA.build_history;
  if (!Array.isArray(hist) || !hist.length) return "";
  const values = hist.map(b => (b && typeof b.health_total === "number") ? b.health_total : 0);
  const rows = hist.map(b => {
    const ts = (b && b.ts) || "?", listings = (b && b.listings != null) ? b.listings : "?";
    const tenants = (b && b.tenants != null) ? b.tenants : "?", wl = (b && b.worklist_size != null) ? b.worklist_size : "?";
    const ht = (b && b.health_total != null) ? b.health_total : "?";
    return esc(ts + ": " + listings + " listings, " + tenants + " tenants, worklist " + wl + ", health issues " + ht);
  }).join("<br>");
  return '<div class="section-hd">Build history (last ' + esc(hist.length) + ')</div>' + sparklineSvg(values, 260, 40) +
    '<div class="mut" style="margin-top:6px">health issues per build (lower is better)</div><div class="help" style="margin-top:8px">' + rows + '</div>';
}
function backupsHtml() {
  const items = listBackups();
  if (!items.length) return '<div class="mut" style="margin-top:8px">No automatic backups yet — one is written for each day you open this app.</div>';
  return '<div class="mut" style="margin-top:8px">Automatic backups on this device — restoring merges a slot back in, same rules as an import (newest change per pair wins, nothing is deleted).</div>' +
    '<div class="acts">' + items.map(b => '<button class="btn" data-restorebackup="' + esc(b.key) + '">↺ ' + esc(b.day) + ' (' + esc(b.marks) + ' marks)</button>').join('') + '</div>';
}
function exportImportHtml() {
  return '<div class="section-hd">Export / import state</div>' +
    '<div class="acts"><button class="btn" data-exportstate="1">⬇ Export state</button><button class="btn" data-importstate="1">⬆ Import state</button><button class="btn" data-copydiag="1">📋 Copy diagnostics</button></div>' +
    '<div id="importbox" style="display:none;margin-top:8px"><textarea data-importtext="1" rows="6" style="width:100%;font-family:monospace;font-size:11px" placeholder="Paste exported JSON here"></textarea>' +
    '<div class="acts" style="margin-top:6px"><button class="btn p" data-importgo="1">Merge import</button><button class="btn" data-importcancel="1">Cancel</button></div></div>' +
    backupsHtml();
}
function importSummary(r) {
  let s = "Imported " + r.marks + " marks, " + r.overrides + " overrides, " + r.offers + " offers, " +
    r.scratch + " scratch, " + r.history + " history";
  if (r.crm) s += ", CRM " + r.crm.entities + " records/" + r.crm.notes + " notes/" + r.crm.tasks + " tasks/" + r.crm.match + " match";
  return s;
}
function wireExportImport(container) {
  const exp = container.querySelector("[data-exportstate]");
  if (exp) exp.onclick = () => downloadJSON(exportBlob(), "matchmaker-state-" + DATA.generated + ".json");
  const diag = container.querySelector("[data-copydiag]");
  if (diag) diag.onclick = () => { navigator.clipboard.writeText(diagnosticsText()); diag.textContent = "Copied"; };
  const box = container.querySelector("#importbox");
  const impBtn = container.querySelector("[data-importstate]");
  if (impBtn && box) impBtn.onclick = () => { box.style.display = box.style.display === "none" ? "block" : "none"; };
  const go = container.querySelector("[data-importgo]");
  if (go) go.onclick = () => {
    const txt = container.querySelector("[data-importtext]").value.trim();
    if (!txt) return;
    try {
      const result = importBlob(JSON.parse(txt));
      toast(importSummary(result));
      rebuildMatches(); render();
    } catch (e) { toast("Could not read that as exported state JSON"); }
  };
  const cancel = container.querySelector("[data-importcancel]");
  if (cancel && box) cancel.onclick = () => { box.style.display = "none"; };
  container.querySelectorAll("[data-restorebackup]").forEach(b => b.onclick = () => {
    const result = restoreBackup(b.dataset.restorebackup);
    toast(result ? ("Restored — " + importSummary(result)) : "That backup slot could not be read");
    rebuildMatches(); render();
  });
}
// (70) asked once on first load; every mark stamps by:<name> (see patchMark).
function promptDeviceName(force) {
  if (PREFS.device_name && !force) return;
  const wrap = el("div", "modal-wrap");
  wrap.innerHTML = '<div class="modal"><h3>What should we call this device?</h3>' +
    '<div class="mut" style="margin-bottom:8px">Every mark you make gets stamped with this name, for example "phone". Asked once.</div>' +
    '<input type="text" data-devname="1" placeholder="e.g. phone" value="' + esc(PREFS.device_name || "") + '">' +
    '<div class="foot"><button class="btn" data-skip="1">Skip</button><button class="btn p" data-save="1">Save</button></div></div>';
  mountOverlay(wrap);
  wrap.querySelector("[data-save]").onclick = () => {
    const v = wrap.querySelector("[data-devname]").value.trim();
    if (v) { PREFS.device_name = v; savePrefs(); }
    wrap.remove(); render();
  };
  wrap.querySelector("[data-skip]").onclick = () => wrap.remove();
  wrap.onclick = (e) => { if (e.target === wrap) wrap.remove(); };
}
function renderStats() {
  const box = $("#stats"); box.innerHTML = "";
  box.appendChild(el("div", "help", "Everything on this tab lives only on this device — nothing is sent anywhere. Export state before switching devices, or before clearing browser data."));
  box.appendChild(el("div", "section-hd", "Weekly funnel"));
  box.appendChild(el("div", "", funnelHtml()));
  box.appendChild(el("div", "section-hd", "Decline reasons by listing"));
  box.appendChild(el("div", "", rejectionHistogramHtml()));
  renderHealthSection(box);
  const bh = buildHistoryHtml();
  if (bh) box.appendChild(el("div", "", bh));
  const eiBox = el("div", "", exportImportHtml());
  box.appendChild(eiBox);
  wireExportImport(eiBox);
  const deviceBox = el("div", "", '<div class="section-hd">Device</div><div class="mut">Marks on this device are stamped as: <b>' + esc(PREFS.device_name || "not set") + '</b> <button class="btn" data-changedevice="1">Change</button></div>');
  box.appendChild(deviceBox);
  const cd = deviceBox.querySelector('[data-changedevice]'); if (cd) cd.onclick = () => promptDeviceName(true);
}

// ===================== CRM drawer + Pipeline tab =====================
// One record component for every subject type (tenant/landlord/sale/listing). Rows
// carry the subject on data attributes rather than a closure so the 🗂 CRM button
// survives render() rebuilding the DOM underneath it — see the [data-crm] delegated
// listener in init below.
function todayISO() { return isoLocal(new Date()); }
let CUR_SUBJ = null, CRM_DRAWER_WRAP = null;
function crmBtn(kind, o) {
  const subj = subjOf(kind, o);
  const e = CRM.entity(subj), st = (e && e.stage && e.stage !== "new") ? STAGE_LABELS[e.stage] : null;
  const n = e ? CRM.notes(subj).length : 0;
  return '<button class="btn" data-crm="' + esc(kind) + '" data-crm-id="' + esc(o.id) + '" data-crm-name="' + esc(o.name || "") +
    '" data-crm-phone="' + esc(o.phone || "") + '">🗂 ' + (st ? esc(st) : "CRM") + (n ? (" · " + n + "📝") : "") + '</button>';
}
function openCRM(s) {
  CUR_SUBJ = s;
  const wrap = el("div", "drawer-wrap");
  wrap.innerHTML = '<aside class="drawer crm-drawer" id="crmDrawer" role="dialog" aria-label="CRM record"></aside>';
  wrap.onclick = (e) => { if (e.target === wrap) closeCRM(); };
  CRM_DRAWER_WRAP = wrap;
  mountOverlay(wrap, { label: "CRM record", onEscape: closeCRM });
  renderDrawer();
}
function closeCRM() {
  CUR_SUBJ = null;
  if (CRM_DRAWER_WRAP) { CRM_DRAWER_WRAP.remove(); CRM_DRAWER_WRAP = null; }
  render();
}
function renderDrawer() {
  const s = CUR_SUBJ; if (!s || !CRM_DRAWER_WRAP) return;
  const d = CRM_DRAWER_WRAP.querySelector("#crmDrawer"); if (!d) return;
  const e = CRM.entity(s) || {}, cur = e.stage || "new";
  const notes = CRM.notes(s), tasks = CRM.tasks(s), acts = CRM.activity(s);
  const overdue = t => !t.done && t.due && t.due < todayISO();
  d.innerHTML =
    '<div class="dhead"><div><h3>' + esc(s.name || "?") + '</h3>' +
      '<div class="sub">' + esc(s.kind || "") + (s.phone ? (' · ' + esc(s.phone)) : '') + (CRM.keyOf(s) ? '' : ' · <b>no phone or id — cannot save</b>') + '</div></div>' +
      '<button class="dclose" id="dClose" title="Close" aria-label="Close">×</button></div>' +
    '<div class="dbody">' +
      '<div class="dsec"><span class="lbl">Stage</span><div class="stagerow">' +
        STAGE_ORDER.map(k => '<span class="pick' + (k === cur ? " sel" : "") + '" data-stage="' + k + '">' + esc(STAGE_LABELS[k]) + '</span>').join("") +
      '</div></div>' +
      '<div class="dsec"><span class="lbl">Next action</span>' +
        '<input id="dAction" placeholder="e.g. confirm Friday viewing slot" value="' + esc(e.next_action || "") + '">' +
        '<input id="dDue" type="date" value="' + esc(e.next_due || "") + '"></div>' +
      '<div class="dsec"><span class="lbl">Notes (' + notes.length + ')</span>' +
        '<textarea id="dNote" placeholder="What happened? Saved with today\'s date."></textarea>' +
        '<button class="btn" id="dAddNote">+ Add note</button>' +
        notes.map(n => '<div class="crm-note"><span class="when">' + esc((n.created_at || "").slice(0, 10)) + '</span>' +
          esc(n.body) + '<span class="del" data-delnote="' + n.id + '" title="Delete note">×</span></div>').join("") +
      '</div>' +
      '<div class="dsec"><span class="lbl">Tasks</span>' +
        '<input id="dTask" placeholder="Task, then Enter">' +
        '<input id="dTaskDue" type="date">' +
        (tasks.length ? tasks.map(t => '<label class="crm-task' + (t.done ? " done" : "") + '"><input type="checkbox" data-task="' + t.id + '"' + (t.done ? " checked" : "") + '>' +
          '<span class="t">' + esc(t.title) + (t.due ? ('<span class="due' + (overdue(t) ? " over" : "") + '">due ' + esc(t.due) + '</span>') : '') + '</span>' +
          '<span class="del" data-deltask="' + t.id + '" title="Delete task">×</span></label>').join("") : '<div class="mut" style="font-size:12px">No tasks yet.</div>') +
      '</div>' +
      (acts.length ? ('<div class="dsec"><span class="lbl">History</span>' +
        acts.slice(0, 20).map(a => '<div class="mut" style="font-size:12px">' + esc((a.at || "").slice(0, 10)) + ' — ' + esc(a.verb) + (a.detail ? (': ' + esc(a.detail.slice(0, 80))) : '') + '</div>').join("") + '</div>') : '') +
    '</div>';
  d.querySelector("#dClose").onclick = closeCRM;
  d.querySelectorAll("[data-stage]").forEach(p => p.onclick = () => { CRM.setStage(s, p.dataset.stage); renderDrawer(); });
  const commitPlan = () => CRM.setPlan(s, d.querySelector("#dAction").value.trim(), d.querySelector("#dDue").value || null);
  d.querySelector("#dAction").onchange = commitPlan; d.querySelector("#dDue").onchange = commitPlan;
  const addNote = () => { const v = d.querySelector("#dNote").value.trim(); if (!v) return; CRM.addNote(s, v); renderDrawer(); };
  d.querySelector("#dAddNote").onclick = addNote;
  d.querySelector("#dNote").onkeydown = ev => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); addNote(); } };
  d.querySelector("#dTask").onkeydown = ev => {
    if (ev.key !== "Enter") return; const v = d.querySelector("#dTask").value.trim(); if (!v) return;
    CRM.addTask(s, v, d.querySelector("#dTaskDue").value || null); renderDrawer();
  };
  d.querySelectorAll("[data-task]").forEach(c => c.onchange = () => { CRM.toggleTask(parseInt(c.dataset.task, 10)); renderDrawer(); });
  d.querySelectorAll("[data-deltask]").forEach(x => x.onclick = () => { CRM.delTask(parseInt(x.dataset.deltask, 10)); renderDrawer(); });
  d.querySelectorAll("[data-delnote]").forEach(x => x.onclick = () => { CRM.delNote(parseInt(x.dataset.delnote, 10)); renderDrawer(); });
}

// Deliberately shows only records Winfred has actually touched — a column per stage
// over the whole tenant/landlord database would just be the roster tabs again.
function renderPipeline() {
  const box = $("#pipeline"); box.innerHTML = "";
  box.appendChild(el("div", "help", "📈 <b>Pipeline</b> — every record you have given a stage, a note or a task via the 🗂 CRM button. " +
    (CRM.mode === "local"
      ? "⚠️ No cloud backend is configured, so this is saved on <b>this device only</b> — it will not appear on your phone and is lost if you clear this browser."
      : "This is the part of the app saved to your CRM database and it survives a rebuild; the roster tabs are rebuilt from the rental databases every night.") +
    " Tap any card to reopen its CRM record."));
  const f = F();
  const touched = CRM.all().filter(e => (e.stage && e.stage !== "new") || e.next_action || CRM.notes(e).length || CRM.tasks(e).length);
  const openTasks = CRM.tasks().filter(t => !t.done);
  const due = openTasks.filter(t => t.due && t.due <= todayISO());
  const plans = CRM.all().filter(e => e.next_due && e.next_due <= todayISO());

  if (due.length || plans.length) {
    const b = el("div", "");
    b.appendChild(el("div", "section-hd", "🔔 Due now (" + (due.length + plans.length) + ")"));
    due.forEach(t => {
      const e = t.key ? CRM.all().find(x => x.key === t.key) : null;
      const r = el("div", "row", "<div class='rtop'><span class='nm'>" + esc(t.title) + "</span><span class='chip r'>due " + esc(t.due) + "</span>" +
        (e ? ("<span class='chip'>" + esc(e.name || "?") + "</span>") : "") + "</div>");
      if (e) r.onclick = () => openCRM(e);
      b.appendChild(r);
    });
    plans.forEach(e => {
      const r = el("div", "row", "<div class='rtop'><span class='nm'>" + esc(e.name || "?") + "</span><span class='chip a'>" + esc(e.next_action || "follow up") + "</span><span class='chip r'>" + esc(e.next_due) + "</span></div>");
      r.onclick = () => openCRM(e);
      b.appendChild(r);
    });
    box.appendChild(b);
  }

  if (!touched.length) {
    box.appendChild(el("div", "empty", "Nothing in the pipeline yet. Open any tenant, landlord or listing and tap 🗂 CRM to set a stage, leave a note or add a task."));
    return;
  }
  const q = (f.q || "");
  const grid = el("div", "pipe");
  // No stage filter here: `touched` already excludes untouched stage:"new"
  // records (see its own filter above) — a record that only ever got a note
  // or task, with the stage left at the "new" default, is still touched and
  // must still get a column, or it renders 0 cards with no search active
  // (repro: add a note without changing stage).
  STAGE_ORDER.forEach(k => {
    let rows = touched.filter(e => (e.stage || "new") === k);
    if (q) rows = rows.filter(e => ((e.name || "") + " " + (e.phone || "") + " " + (e.kind || "")).toLowerCase().includes(q));
    if (!rows.length) return;
    const col = el("div", "pcol", "<h4>" + esc(STAGE_LABELS[k]) + " (" + rows.length + ")</h4>");
    rows.forEach(e => {
      const nt = CRM.notes(e).length, tk = CRM.tasks(e).filter(t => !t.done).length;
      const c = el("div", "pcard", "<b>" + esc(e.name || "?") + "</b><span class='mut'>" + esc(e.kind || "") + (e.phone ? (" · " + esc(e.phone)) : "") + "</span>" +
        (e.next_action ? ("<div class='mut' style='margin-top:3px'>→ " + esc(e.next_action) + (e.next_due ? (" (" + esc(e.next_due) + ")") : "") + "</div>") : "") +
        (nt || tk ? ("<div class='mut' style='margin-top:3px'>" + (nt ? (nt + "📝 ") : "") + (tk ? (tk + "☑") : "") + "</div>") : ""));
      c.onclick = () => openCRM(e);
      col.appendChild(c);
    });
    grid.appendChild(col);
  });
  box.appendChild(grid.children.length ? grid : el("div", "empty", "No pipeline records match the current search."));
}

// ===================== init =====================
(function () {
  const ds = [...new Set((DATA.listings || []).map(l => l.district).filter(Boolean))].sort();
  ds.forEach(d => { const o = el("option"); o.value = d; o.textContent = d + " " + (AREA[d] || ""); $("#fd").appendChild(o); });
  // (item 6) role=tab divs are not natively focusable/operable — tabindex
  // makes them reachable by Tab, and the keydown handler gives Enter/Space
  // the click-equivalent activation a real <button> gets for free.
  document.querySelectorAll("#tabs .tab").forEach(t => {
    const activate = () => { view = t.dataset.v; render(); };
    t.onclick = activate;
    t.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
  });
  // #q fires on every keystroke, unlike the select/checkbox/number filters
  // (one event per discrete choice) — a render() here also recomputes
  // faceted counts across every match plus rebuilds up to 120+ tenant/listing
  // cards, so fast typing on a real phone paid that cost per character.
  // Debounced so a burst of keystrokes collapses into one render ~140ms
  // after the user pauses — short enough to still feel instant.
  let qDebounceTimer = null;
  $("#q").addEventListener("input", () => { clearTimeout(qDebounceTimer); qDebounceTimer = setTimeout(render, 140); });
  ["fd", "fv", "fr", "fc", "fh"].forEach(id => $("#" + id).addEventListener("input", render));
  $("#clr").onclick = () => { clearTimeout(qDebounceTimer); ["q", "fr", "fd", "fv"].forEach(id => $("#" + id).value = ""); $("#fc").checked = false; $("#fh").checked = false; render(); };
  const addBtn = $("#addtenant"); if (addBtn) addBtn.onclick = openQuickAddTenant;
  const snoozeBtn = $("#snoozechip"); if (snoozeBtn) snoozeBtn.onclick = openSnoozedList;
  const dispatchBtn = $("#dispatchchip"); if (dispatchBtn) dispatchBtn.onclick = openDispatchDrawer;
  const paletteBtn = $("#palettebtn"); if (paletteBtn) paletteBtn.onclick = openCommandPalette;
  const themeBtn = $("#themebtn"); if (themeBtn) themeBtn.onclick = toggleTheme;
  const densityBtn = $("#densitybtn"); if (densityBtn) densityBtn.onclick = toggleDensity;
  const bulkBtn = $("#bulkbtn"); if (bulkBtn) bulkBtn.onclick = openBulkActionModal;
  document.addEventListener("keydown", onKeydown);
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      if (OPEN_OVERLAY_COUNT > 0) return;   // don't stack a second overlay on top of one already open
      e.preventDefault(); openCommandPalette();
    }
  });
  const workBox = $("#work"); if (workBox) wireSwipe(workBox);

  // (51) long press the search box on mobile also opens the palette
  let pressTimer = null;
  const qInput = $("#q");
  if (qInput) {
    qInput.addEventListener("touchstart", () => { pressTimer = setTimeout(openCommandPalette, 550); }, { passive: true });
    ["touchend", "touchmove"].forEach(ev => qInput.addEventListener(ev, () => clearTimeout(pressTimer), { passive: true }));
  }

  applyThemeClass(); applyDensityClass(); // (53)/(54) restore before first paint

  measureHeaderHeight();
  window.addEventListener("resize", measureHeaderHeight);
  // (item 4, cycle 8) — see ensureFocusVisible's own comment above. focusin
  // bubbles (plain focus does not), so one listener on document covers every
  // tab and every row without having to re-wire it after each render().
  document.addEventListener("focusin", (e) => ensureFocusVisible(e.target));

  // (20)/(49) idle lock — any real interaction resets the 10 minute timer.
  ["click", "keydown", "touchstart", "scroll"].forEach(ev => document.addEventListener(ev, resetIdleTimer, { passive: true }));
  resetIdleTimer();

  // (71) a second tab on this origin writing a mark/override — see
  // invalidateMarkCacheKey. Fires only in tabs that did NOT make the write.
  window.addEventListener("storage", (e) => invalidateMarkCacheKey(e.key));

  registerServiceWorker();  // (52)
  writeAutoBackup();        // (47)

  // 🗂 CRM opens the record drawer. Delegated on document (not wired per row) so it
  // survives every render() clearing the DOM underneath it — same reason the wa.me
  // contacted-stamp listener below is delegated too. stopPropagation because these
  // buttons sit inside rows that have their own click behaviour (e.g. the
  // listing/tenant rail's row-select) — without it, opening a record would also
  // trigger whatever the row itself does on click.
  document.addEventListener("click", (e) => {
    const c = e.target.closest("[data-crm]");
    if (!c) return;
    e.preventDefault(); e.stopPropagation();
    openCRM({ kind: c.getAttribute("data-crm"), id: c.getAttribute("data-crm-id"), name: c.getAttribute("data-crm-name"), phone: c.getAttribute("data-crm-phone") });
  });
  // Escape-to-close and focus trapping are handled by mountOverlay itself (see
  // openCRM's onEscape:closeCRM) — no separate listener needed here.

  rebuildMatches();
  render();
  // boot() is async — an unhandled rejection here (e.g. a corrupt cbkcrm_v1
  // blob that survives its own guards, or a synchronous storage throw) would
  // otherwise leave the CRM permanently unsynced for the session with no
  // visible signal at all.
  CRM.boot().catch(e => {
    try { pushErrorEntry({ ts: Date.now(), kind: "error", msg: "CRM.boot failed: " + String(e && e.message || e), src: "", line: 0, col: 0, stack: (e && e.stack) ? String(e.stack).slice(0, 600) : "" }); } catch (e2) { /* diagnostics best effort */ }
    toast("CRM could not start — working in local only mode this session. Reload to retry.");
  });

  // (70) ask for a device name once, after first paint so it never blocks getting into the app.
  if (!PREFS.device_name) setTimeout(() => promptDeviceName(false), 400);
})();
