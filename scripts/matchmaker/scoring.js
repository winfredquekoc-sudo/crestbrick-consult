/* Crestbrick Matchmaker — scoring.js
 * Pure matching/scoring logic. No DOM, no globals besides what is passed in.
 * require()able from Node (tests use createRequire) and loaded as a plain
 * classic script tag in the browser build (see template.html's placeholder).
 *
 * PARITY NOTE: score()/verdict semantics below are an EXACT port of the
 * original template.html inline score() (budget/location/lease/move-in/
 * freshness weights, hard gates, verdict formula). When called with v1
 * shaped data (no units[]/available_from/last_wa/work_anchor), every v2
 * extension is a documented no-op and the numbers must match the legacy
 * function bit for bit — see tests/matchmaker/scoring.test.mjs "parity"
 * cases. Everything past the parity block is additive v2 behavior.
 *
 * PASS 2 additions (near the bottom, before exports): nextBestAction,
 * weeklyFunnel, dataAgeDays/dataAgeTier, discountListing/priceElasticity.
 * Same rule applies — pure functions only, DOM/localStorage stays in app.js.
 */
(function () {
"use strict";

// ---------------------------------------------------------------------
// constants
// ---------------------------------------------------------------------

// District adjacency — unchanged from the legacy template (verbatim).
var ADJ = {
  D1: ["D2", "D4", "D6", "D7"], D2: ["D1", "D3", "D4"], D3: ["D2", "D4", "D5", "D10"],
  D4: ["D1", "D2", "D3", "D5"], D5: ["D3", "D4", "D10", "D21", "D22"], D6: ["D1", "D7", "D9"],
  D7: ["D1", "D6", "D8", "D14"], D8: ["D7", "D9", "D11", "D12", "D13"], D9: ["D6", "D8", "D10", "D11"],
  D10: ["D3", "D5", "D9", "D11", "D21"], D11: ["D8", "D9", "D10", "D12", "D20"], D12: ["D8", "D11", "D13", "D20"],
  D13: ["D8", "D12", "D14", "D19", "D20"], D14: ["D7", "D13", "D15", "D16", "D19"], D15: ["D14", "D16"],
  D16: ["D14", "D15", "D17", "D18"], D17: ["D16", "D18"], D18: ["D16", "D17", "D19"],
  D19: ["D13", "D14", "D18", "D20", "D28"], D20: ["D11", "D12", "D13", "D19", "D26", "D28"], D21: ["D5", "D10", "D23"],
  D22: ["D5", "D21", "D23"], D23: ["D22", "D24", "D25", "D26"], D24: ["D23", "D25"],
  D25: ["D23", "D24", "D26", "D27"], D26: ["D20", "D23", "D25", "D28"], D27: ["D25", "D26", "D28"],
  D28: ["D19", "D20", "D26", "D27"]
};

// Curated approximation of which MRT trunk lines touch each district —
// good enough for a "same line, treat as adjacent" scoring nudge, NOT
// authoritative transit routing. Six lines per spec: NEL/NSL/EWL/CCL/DTL/TEL.
var MRT_LINES = {
  D1: ["NSL", "EWL", "CCL", "DTL"], D2: ["EWL", "TEL"], D3: ["EWL", "CCL"],
  D4: ["CCL", "NEL"], D5: ["EWL", "CCL"], D6: ["NSL", "EWL", "NEL"],
  D7: ["EWL", "DTL"], D8: ["NEL", "DTL"], D9: ["NSL", "TEL"],
  D10: ["CCL", "DTL"], D11: ["NSL", "DTL", "TEL"], D12: ["NSL", "NEL"],
  D13: ["CCL", "NEL"], D14: ["EWL", "CCL"], D15: ["TEL"],
  D16: ["EWL"], D17: ["EWL"], D18: ["EWL", "DTL"],
  D19: ["NEL", "CCL"], D20: ["NSL", "CCL"], D21: ["DTL"],
  D22: ["EWL", "CCL"], D23: ["DTL"], D24: ["DTL"],
  D25: ["NSL"], D26: ["TEL"], D27: ["NSL"], D28: ["NEL"]
};

var BUDGET_HARD_BLOCK_RATIO = 0.9;   // legacy: budget < 0.9 * rent_min -> hard block
// near_miss "clearance" is expressed against the ACTUAL hard block threshold
// (rent_min * 0.9), not against rent_min directly — budget >= 0.92x of the
// threshold that blocked you (i.e. within ~8% of clearing the block). Using
// 0.92 directly against rent_min is arithmetically impossible to satisfy at
// the same time as the < 0.9x block condition (0.92x > 0.9x), so this is a
// deliberate, documented reading of the spec's "budget >= 0.92 x rent_min"
// near miss rule. See build report for the full reasoning.
var NEAR_MISS_CLEARANCE = 0.92;
var COLD_DAYS_THRESHOLD = 5;         // >5 days = cold, per standing 5 day rule
var LOOKALIKE_PENALTY = 8;           // display only; applied by app.js using isSimilarListing()
var WHOLE_UNIT_FLAG = "budget well above room (may want whole unit)";

// ---------------------------------------------------------------------
// dates — robust parser (replaces the fragile `d+"T00:00:00"` original)
//
// THE CORE TRAP (cycle 6): every date in DATA is authored on an SGT machine
// (build.py/export_data.py run in Singapore), but the phone opening the built
// artifact can be in ANY timezone — Winfred travels. A bare "YYYY-MM-DD" is
// pinned, unambiguous digits (parsed below as device-local midnight of those
// EXACT digits, so re-reading it back via the SAME device's own getters always
// returns the same day, on any device — no cross-device risk there). The
// danger is real ABSOLUTE INSTANTS: DATA.generated_ts, last_wa.ts, a mark's
// Date.now() ts. Read those back via the VIEWING DEVICE's own local getters
// (`.getFullYear()`, `.getDate()`, ...) and you get "what calendar day was it
// on THIS device", which silently drifts from "what calendar day was it in
// Singapore" the further the device's zone sits from +08:00 — a nudge/cold/
// data-age boundary can then land on the wrong side purely from where the
// phone happens to be. sgtParts()/sgtDay() below are the ONE place a real
// instant is reduced to a calendar day, always via Asia/Singapore explicitly
// (never the device's own zone) — every other date function in this file
// keeps operating on already-pinned "calendar day" values exactly as before.
// ---------------------------------------------------------------------

var SGT_TZ = "Asia/Singapore";
// en-CA locale conveniently formats as YYYY-MM-DD; hour12:false avoids "24:00"
// vs "00:00" AM/PM ambiguity in the extracted hour.
var _sgtFmt = (typeof Intl !== "undefined" && Intl.DateTimeFormat)
  ? new Intl.DateTimeFormat("en-CA", {
      timeZone: SGT_TZ, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
    })
  : null;

// Any real Date instant -> its Asia/Singapore calendar Y/M/D + H/M/S, as plain
// numbers. Never call this on a value that is ALREADY a pinned calendar-day
// marker (device-local midnight built from known-correct digits) — doing so
// re-interprets those digits through yet another zone and can shift the day.
// Only call it on a genuinely fresh instant: `new Date()`, Date.now(), or an
// offset-bearing timestamp string.
function sgtParts(d) {
  if (!d || isNaN(d.getTime())) return null;
  if (!_sgtFmt) {
    // No Intl at all (ancient engine) — device-local is the least-bad fallback;
    // this only affects calendar-day math, and a missing Intl is a bigger
    // problem than a possible day skew on such a runtime.
    return { y: d.getFullYear(), mo: d.getMonth() + 1, d: d.getDate(), hh: d.getHours(), mm: d.getMinutes() };
  }
  var parts = {};
  _sgtFmt.formatToParts(d).forEach(function (p) { if (p.type !== "literal") parts[p.type] = p.value; });
  return { y: Number(parts.year), mo: Number(parts.month), d: Number(parts.day), hh: Number(parts.hour), mm: Number(parts.minute) };
}
// Any real Date instant -> a Date pinned at DEVICE-LOCAL MIDNIGHT of that
// instant's ASIA/SINGAPORE calendar day. Shape-compatible with parseDate()'s
// bare YYYY-MM-DD branch (same `new Date(y, mo-1, d)` constructor), so every
// existing atMidnight()/daysAgo() comparison downstream needs no changes at
// all — it just now always starts from the correct SGT day number instead of
// whatever day the instant happens to fall on in the viewing device's own zone.
function sgtDay(d) {
  var p = sgtParts(d);
  return p ? new Date(p.y, p.mo - 1, p.d) : null;
}

// Accepts YYYY-MM-DD (optionally with a time/offset, i.e. a full ISO
// timestamp) or DD/MM/YYYY. Anything else, or an impossible calendar date
// (32/13/2026, 2026-02-30, ...), returns null — never NaN.
//
// Every caller of the with-time branch below only ever consumes the CALENDAR
// DAY of the result (via atMidnight()/shortDate() — never the hour/minute), so
// collapsing straight to a pinned SGT day marker here is safe and is what
// fixes the cross-form mixing bug: previously an offset-bearing timestamp
// (e.g. "...+08:00") fell through to the native `new Date(str)` parse, which
// preserves the real instant — fine on its own, but every place that instant
// later got reduced to a day via DEVICE-local getters (atMidnight, shortDate)
// could land on a different calendar day than intended once the viewing
// device's zone was far enough from SGT. Reducing here instead means every
// downstream comparison is diffing two already-pinned SGT-day markers, which
// is where this file's existing atMidnight()-based math is already correct.
function parseDate(v) {
  if (v == null || v === "") return null;
  if (v instanceof Date) return isNaN(v.getTime()) ? null : v;
  var s = String(v).trim();
  if (!s) return null;

  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$/);
  if (m) {
    var y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    if (m[4] && m[7]) {
      // Explicit offset (Z or +/-HH:MM) -> a genuine, unambiguous absolute
      // instant. Reduce it via Asia/Singapore, not whatever zone the runtime
      // parsing this string happens to be in.
      var withOffset = new Date(s.replace(" ", "T"));
      return isNaN(withOffset.getTime()) ? null : sgtDay(withOffset);
    }
    // Bare date, OR a timestamp with a time but NO offset (e.g. a WhatsApp
    // bridge row missing its zone — see reference_wa_bridge... "mixed offsets
    // hid a lead" incident). Per the JS spec a bare "YYYY-MM-DDTHH:MM:SS" with
    // no offset would parse as LOCAL time on whatever device runs this code —
    // exactly the ambiguity this file exists to remove. Every field in this
    // app is authored in SGT terms, so treat the WRITTEN digits themselves as
    // the intended SGT calendar day (never construct a Date from the full
    // string in this branch at all — there is nothing safe to read a hidden
    // "local time" interpretation from).
    var dateOnly = new Date(y, mo - 1, d);
    return (isNaN(dateOnly.getTime()) || dateOnly.getMonth() !== mo - 1) ? null : dateOnly;
  }

  m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) {
    var dd = Number(m[1]), mm = Number(m[2]), yy = Number(m[3]);
    var dmy = new Date(yy, mm - 1, dd);
    return (isNaN(dmy.getTime()) || dmy.getMonth() !== mm - 1) ? null : dmy;
  }

  return null;
}

function atMidnight(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

// Legacy-compatible: positive = date is in the past relative to `today`,
// negative = date is in the future. Both sides are normalized to local
// midnight so a full ISO timestamp (e.g. last_wa.ts) still yields whole
// day counts.
function daysAgo(v, today) {
  var x = parseDate(v);
  if (!x || !today) return null;
  return Math.round((atMidnight(today) - atMidnight(x)) / 864e5);
}

// ---------------------------------------------------------------------
// budget / units
// ---------------------------------------------------------------------

// Mirrors the legacy per-listing budget scoring, but against a single unit
// (unit_type/rent_min/rent_max) so bestUnit() can run it per l.units[] entry.
function scoreUnitBudget(unit, b) {
  var flags = [], sb = 15, hardBudget = false;
  var rentMin = unit && unit.rent_min, rentMax = unit && unit.rent_max;
  if (b != null) {
    if (rentMin && b < rentMin) {
      sb = 4;
      flags.push("over landlord's min ($" + rentMin + ")");
      if (b < rentMin * BUDGET_HARD_BLOCK_RATIO) hardBudget = true;
    } else if (rentMax && b > rentMax * 1.6) {
      sb = 14;
      flags.push(WHOLE_UNIT_FLAG);
    } else {
      sb = 30;
    }
  } else {
    flags.push("budget unknown");
  }
  return { sb: sb, flags: flags, hardBudget: hardBudget };
}

// Scores the tenant's budget against every l.units[] entry and keeps the
// best fitting one. v1 listings (no units[]) get a synthesized single unit
// mirroring l.rent_min/rent_max — this is what keeps parity with the
// original single rent_min/rent_max scoring intact.
function bestUnit(l, t) {
  var b = (t && t.budget != null) ? t.budget : ((t && t.budget_max != null) ? t.budget_max : null);
  var units = (l && Array.isArray(l.units) && l.units.length)
    ? l.units
    : [{ unit_type: null, rent_min: l && l.rent_min, rent_max: l && l.rent_max }];
  var best = null;
  for (var i = 0; i < units.length; i++) {
    var r = scoreUnitBudget(units[i], b);
    if (!best || r.sb > best.sb) {
      best = { unit: units[i], sb: r.sb, flags: r.flags, hardBudget: r.hardBudget };
    }
  }
  return { budget: b, unit: best.unit, sb: best.sb, flags: best.flags, hardBudget: best.hardBudget };
}

// ---------------------------------------------------------------------
// location
// ---------------------------------------------------------------------

function mrtSharesLine(distA, distB) {
  if (!distA || !distB) return false;
  var a = MRT_LINES[distA], b = MRT_LINES[distB];
  if (!a || !b) return false;
  for (var i = 0; i < a.length; i++) if (b.indexOf(a[i]) !== -1) return true;
  return false;
}

// Legacy scoring with one addition: when the tenant's preferred/current
// district doesn't match and isn't in the hardcoded ADJ map, a shared MRT
// line upgrades the flat "mismatch" (6) to "adjacent" (15). Only reachable
// from the mismatch branch, so v1 data (or any pair that already resolves
// via preferred/same/ADJ) scores exactly as before.
function locationScore(l, t) {
  var sl = 8;
  var pd = (t && t.preferred_districts) || [];
  if (pd.indexOf(l.district) !== -1) sl = 25;
  else if (t.district && t.district === l.district) sl = 20;
  else if (pd.some(function (d) { return (ADJ[l.district] || []).indexOf(d) !== -1; })) sl = 15;
  else if (pd.length === 0 && !t.district) sl = 8;
  else {
    sl = 6;
    var candidates = t.district ? pd.concat([t.district]) : pd;
    if (candidates.some(function (d) { return mrtSharesLine(d, l.district); })) sl = 15;
  }
  return sl;
}

// ---------------------------------------------------------------------
// lease
// ---------------------------------------------------------------------

function leaseScore(l, t) {
  var sle = 8;
  var lm = t.lease_months;
  var need = (l.gates && l.gates.lease_min) || 12;
  var flag = null;
  if (lm != null) {
    if (lm >= 12) sle = 15;
    else if (lm >= need) sle = 11;
    else { sle = 5; flag = "lease " + lm + "mo vs " + need + "mo wanted"; }
  }
  return { sle: sle, flag: flag };
}

// ---------------------------------------------------------------------
// move-in / vacancy gap
// ---------------------------------------------------------------------

// When the listing has a known available_from date, score the gap between
// that and the tenant's move_in instead of the plain "days from today"
// window — this is strictly a v2 behavior gated on l.available_from being
// present, so v1 listings fall straight through to the legacy branch.
function moveInScore(l, t, today) {
  var sm = 8;
  // Prefer the export time normalized date when present: move_in stays the
  // tenant's verbatim typed text (e.g. "Immediately", "mid Aug") which this
  // file's own parseDate() cannot read, silently defaulting sm below exactly
  // as before for anyone without a move_in_norm.
  var moveIn = (t && (t.move_in_norm || t.move_in)) || null;
  var availFrom = (l && l.available_from) ? parseDate(l.available_from) : null;
  if (availFrom) {
    var mv = parseDate(moveIn);
    if (mv) {
      var gap = Math.abs(Math.round((atMidnight(mv) - atMidnight(availFrom)) / 864e5));
      if (gap <= 14) sm = 15;
      else if (gap <= 45) sm = 10;
      else sm = 6;
    }
    return sm;
  }
  if (moveIn) {
    var di = daysAgo(moveIn, today);
    if (di != null) {
      if (di <= 35 && di >= -40) sm = 15;
      else if (di <= 95) sm = 10;
      else sm = 6;
    }
  }
  return sm;
}

// ---------------------------------------------------------------------
// freshness / cold
// ---------------------------------------------------------------------

// Freshest of last_contact and last_wa.ts (v1 tenants have no last_wa, so
// this reduces to plain days-since-last_contact — parity preserved).
function coldDays(t, today) {
  var vals = [];
  var c1 = daysAgo(t && t.last_contact, today);
  if (c1 != null) vals.push(c1);
  if (t && t.last_wa && t.last_wa.ts) {
    var c2 = daysAgo(t.last_wa.ts, today);
    if (c2 != null) vals.push(c2);
  }
  if (!vals.length) return null;
  return Math.min.apply(null, vals);
}

// Unknown contact date is treated as "not cold" (we don't know, so don't
// disable outreach) — only a CONFIRMED gap of >5 days counts as cold.
// Split from isCold so a caller holding a day count already (app.js memoises
// coldDays per tenant — see COLD_CACHE there) applies this exact comparison
// instead of re-implementing the threshold at a second site.
function isColdFromDays(dc) {
  return dc != null && dc > COLD_DAYS_THRESHOLD;
}
function isCold(t, today) {
  return isColdFromDays(coldDays(t, today));
}

function freshnessScoreFromDays(dc) {
  var sf = 2;
  if (dc != null) {
    if (dc <= 7) sf = 15;
    else if (dc <= 30) sf = 10;
    else if (dc <= 90) sf = 5;
    else sf = 2;
  }
  return sf;
}

// ---------------------------------------------------------------------
// urgency (worklist ranking only — displayed score stays raw)
// ---------------------------------------------------------------------

function urgencyMult(t, today) {
  var moveIn = t && (t.move_in_norm || t.move_in);   // prefer the export time normalized date, see moveInScore()
  if (!t || !moveIn || !today) return 1.0;
  var mv = parseDate(moveIn);
  if (!mv) return 1.0;
  var daysUntil = Math.round((atMidnight(mv) - atMidnight(today)) / 864e5);
  if (daysUntil <= 7) return 1.25;   // includes overdue/already past move_in — most urgent
  if (daysUntil <= 14) return 1.15;
  if (daysUntil <= 30) return 1.05;
  return 1.0;
}

// ---------------------------------------------------------------------
// work anchor bonus
// ---------------------------------------------------------------------

function workAnchorBonus(l, t) {
  if (!t || !t.work_anchor || !l) return 0;
  return mrtSharesLine(t.work_anchor, l.district) ? 3 : 0;
}

// ---------------------------------------------------------------------
// hard gates (gender / ethnicity / pax) — verbatim port
// ---------------------------------------------------------------------

function hardGates(l, t) {
  var flags = [], gateHits = [], hardOther = false;
  var g = (l && l.gates) || {};
  if (g.gender === "female_only" && /^m/i.test(t.gender || "")) {
    flags.push("landlord: female only");
    gateHits.push({ code: "gender", sourceKey: "gender", reason: "landlord: female only" });
    hardOther = true;
  }
  if (g.gender === "male_only" && /^f/i.test(t.gender || "")) {
    flags.push("landlord: male only");
    gateHits.push({ code: "gender", sourceKey: "gender", reason: "landlord: male only" });
    hardOther = true;
  }
  var eth = (t.ethnicity || "").toLowerCase();
  var er = g.ethnicity || { rule: "any", races: [] };
  if (er.rule === "exclude" && er.races.some(function (r) { return eth.indexOf(r) !== -1; })) {
    var reasonExcl = "landlord excludes " + er.races.join("/");
    flags.push(reasonExcl);
    gateHits.push({ code: "ethnicity", sourceKey: "ethnicity", reason: reasonExcl });
    hardOther = true;
  }
  if (er.rule === "only" && er.races.length && !er.races.some(function (r) { return eth.indexOf(r) !== -1; })) {
    var reasonOnly = "landlord wants " + er.races.join("/") + " only";
    flags.push(reasonOnly);
    gateHits.push({ code: "ethnicity", sourceKey: "ethnicity", reason: reasonOnly });
    hardOther = true;
  }
  if (g.max_pax != null && t.pax != null && t.pax > g.max_pax) {
    var reasonPax = "pax " + t.pax + " > max " + g.max_pax;
    flags.push(reasonPax);
    gateHits.push({ code: "pax", sourceKey: "max_pax", reason: reasonPax });
    hardOther = true;
  }
  return { flags: flags, gateHits: gateHits, hardOther: hardOther };
}

// ---------------------------------------------------------------------
// main score
// ---------------------------------------------------------------------

function score(l, t, today) {
  today = today || new Date();
  var flags = [];
  var gateHits = [];

  var bu = bestUnit(l, t);
  flags.push.apply(flags, bu.flags);                 // budget flags — legacy order 1st
  var b = bu.budget;

  var sl = locationScore(l, t);

  var leaseResult = leaseScore(l, t);
  if (leaseResult.flag) flags.push(leaseResult.flag); // lease flag — legacy order 2nd

  var sm = moveInScore(l, t, today);
  var dc = coldDays(t, today);
  var sf = freshnessScoreFromDays(dc);

  var gates = hardGates(l, t);
  flags.push.apply(flags, gates.flags);               // hard gate flags — legacy order 3rd
  gateHits.push.apply(gateHits, gates.gateHits);

  var hard = bu.hardBudget || gates.hardOther;

  var total = bu.sb + sl + leaseResult.sle + sm + sf;
  total += workAnchorBonus(l, t);
  if (total > 100) total = 100;

  var verdict = hard ? "BLOCKED" : ((b == null || sl <= 6) ? "NEEDS_INFO" : "QUALIFIED");

  var needsInfoReasons = [];
  if (b == null) needsInfoReasons.push("budget");
  if (sl <= 6) needsInfoReasons.push("location");

  var near_miss = false, near_miss_gap = null;
  if (bu.hardBudget && !gates.hardOther && b != null && bu.unit && bu.unit.rent_min) {
    var floor = bu.unit.rent_min * BUDGET_HARD_BLOCK_RATIO * NEAR_MISS_CLEARANCE;
    if (b >= floor) {
      near_miss = true;
      near_miss_gap = Math.max(0, bu.unit.rent_min - b);
      flags.push("near miss — $" + near_miss_gap + " short of landlord's min");
    }
  }

  return {
    total: total,
    parts: { budget: bu.sb, location: sl, lease: leaseResult.sle, movein: sm, fresh: sf },
    flags: flags,
    verdict: verdict,
    dc: dc,
    unit: bu.unit,
    gateHits: gateHits,
    needsInfoReasons: needsInfoReasons,
    near_miss: near_miss,
    near_miss_gap: near_miss_gap,
    overridden: false
  };
}

function verdict(l, t, today) { return score(l, t, today).verdict; }

function nearMiss(l, t, today) {
  var r = score(l, t, today);
  return { near_miss: r.near_miss, near_miss_gap: r.near_miss_gap };
}

// ---------------------------------------------------------------------
// manual override
// ---------------------------------------------------------------------

// Pure — returns a NEW result with verdict forced, never mutates `result`.
function applyOverride(result, override) {
  if (!result || !override || !override.verdict) return result;
  var out = {};
  for (var k in result) out[k] = result[k];
  out.verdict = override.verdict;
  out.overridden = true;
  out.overrideWhy = override.why || null;
  return out;
}

// ---------------------------------------------------------------------
// lookalike similarity (used by app.js for decline downrank, #26)
// ---------------------------------------------------------------------

function isSimilarListing(a, b) {
  if (!a || !b) return false;
  if (!a.district || !b.district || a.district !== b.district) return false;
  var ar = a.rent_min != null ? a.rent_min : a.rent_max;
  var br = b.rent_min != null ? b.rent_min : b.rent_max;
  if (ar == null || br == null || !br) return false;
  return Math.abs(ar - br) <= 0.10 * br;
}

// ---------------------------------------------------------------------
// PASS 2 — next best action, weekly funnel, data age tiers, price elasticity
// Still pure: every function below takes plain data (no DOM, no localStorage
// reads) so app.js gathers whatever it needs from localStorage/DATA first and
// hands it in as plain args — same discipline as the rest of this file.
// ---------------------------------------------------------------------

var NUDGE_AFTER_DAYS = 3;            // contacted, no reply this long (still short of the 5 day cold cutoff) -> suggest one nudge
var DATA_AGE_AMBER_DAYS = 3;         // (48) banner tiers: green <=3d, amber 4-14d, red >14d
var DATA_AGE_RED_DAYS = 14;
var PRICE_ELASTICITY_DELTAS = [50, 100, 150];
var DAYS_LISTED_ELASTICITY_THRESHOLD = 21;

// (60) Next best action chip. Deliberately narrow: one suggestion or none,
// never a list — the UI renders at most one small chip per tenant/pair.
// `input`: { verdict, mark:{v,ts,viewing_date}, isCold, today, needsInfoReasons }
function nextBestAction(input) {
  input = input || {};
  var verdict = input.verdict, mk = input.mark || {}, today = input.today;
  if (input.isCold) return null;                                   // cold -> no chip, per the 5 day rule
  if (verdict === "BLOCKED") return null;
  if (mk.v === "Not interested" || mk.v === "Queued") return null;  // already actioned, nothing to suggest
  if (mk.v === "Viewing booked") {
    if (mk.viewing_date && today) {
      var vd = parseDate(mk.viewing_date);
      if (vd && atMidnight(vd) < atMidnight(today)) return { code: "collect_verdict", label: "collect verdict" };
    }
    return null; // upcoming viewing booked, nothing to nudge yet
  }
  if (verdict === "NEEDS_INFO") {
    var reason = (input.needsInfoReasons && input.needsInfoReasons[0]) || null;
    var label = reason === "budget" ? "ask their budget" : reason === "location" ? "ask their area" : "one question";
    return { code: "needs_info", label: label };
  }
  if (verdict === "QUALIFIED" && !mk.v) return { code: "offer_slot", label: "offer a slot" };
  // mk.ts is always a real Date.now() epoch stamped on whatever device made
  // the mark — sgtDay() it before diffing so "how many days since contacted"
  // reads the same on any viewing device, not just one sitting in SGT. `today`
  // is left as-is: callers already hand this function an already-pinned
  // calendar-day marker (see this file's "THE CORE TRAP" note above parseDate).
  if (mk.v === "Contacted" && mk.ts && today) {
    // sgtDay() returns null for a garbage/corrupt ts (unlike raw `new Date()`,
    // which silently yields an Invalid Date that atMidnight() would otherwise
    // propagate as NaN) — guard explicitly so a fuzzed/hand-edited mark can
    // never throw here, it just never suggests a nudge, same net effect as before.
    var markDay = sgtDay(new Date(mk.ts));
    var days = markDay ? Math.round((atMidnight(today) - atMidnight(markDay)) / 864e5) : null;
    if (days != null && days >= NUDGE_AFTER_DAYS) return { code: "nudge", label: "one nudge left" };
  }
  return null;
}

// (13)/(27) Weekly funnel over a trailing 7 day window ending `today`.
// `records` is a plain array of {v, ts (epoch ms)} — app.js gathers this from
// a mark history log; kept here so the counting rule (which statuses count,
// how conversion is computed) has one tested home rather than living in the
// DOM layer. Each record's ts is a real Date.now() epoch (any device, any
// moment) so it is reduced via sgtDay() before bucketing — a mark made in
// Singapore must count in the same 7 day window regardless of which device
// later renders the funnel. `today` keeps its existing contract (an
// already-pinned calendar-day marker; app.js passes its own SGT-reduced NOW).
function weeklyFunnel(records, today) {
  records = records || [];
  var contacted = 0, viewings = 0;
  if (today) {
    var since = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
    var sinceMid = atMidnight(since), todayMid = atMidnight(today);
    records.forEach(function (r) {
      if (!r || !r.ts) return;
      // sgtDay() returns null on a garbage ts (a fuzzed/hand-edited history
      // entry) — skip the record rather than let atMidnight() crash on it.
      var rDay = sgtDay(new Date(r.ts));
      if (!rDay) return;
      var d = atMidnight(rDay);
      if (d < sinceMid || d > todayMid) return;
      if (r.v === "Contacted") contacted++;
      else if (r.v === "Viewing booked") viewings++;
    });
  }
  var conversionPct = contacted > 0 ? Math.round((viewings / contacted) * 100) : 0;
  return { contacted: contacted, viewings: viewings, conversionPct: conversionPct };
}

// (48) Data age tiers for the hard expiry banner. generatedTs is normally
// DATA.generated_ts (ISO8601+08:00); a v1 payload's plain DATA.generated
// (YYYY-MM-DD) parses fine too since parseDate accepts both shapes.
function dataAgeDays(generatedTs, now) {
  var g = parseDate(generatedTs);
  if (!g || !now) return null;
  return Math.round((atMidnight(now) - atMidnight(g)) / 864e5);
}
function dataAgeTier(generatedTs, now) {
  var d = dataAgeDays(generatedTs, now);
  if (d == null) return { tier: "unknown", days: null };
  if (d > DATA_AGE_RED_DAYS) return { tier: "red", days: d };
  if (d > DATA_AGE_AMBER_DAYS) return { tier: "amber", days: d };
  return { tier: "green", days: d };
}

// (18)/(29)/(30) Price elasticity. Re-scores every tenant against a copy of
// the listing with each unit discounted by $50/$100/$150 and reports how
// many MORE tenants would clear QUALIFIED at each step versus baseline.
// Gating on days_listed>21 is the caller's job (app.js) — this just computes.
function discountListing(l, delta) {
  if (!l) return l;
  var out = {};
  for (var k in l) out[k] = l[k];
  if (Array.isArray(l.units) && l.units.length) {
    out.units = l.units.map(function (u) {
      return {
        unit_type: u.unit_type,
        rent_min: u.rent_min != null ? Math.max(0, u.rent_min - delta) : u.rent_min,
        rent_max: u.rent_max != null ? Math.max(0, u.rent_max - delta) : u.rent_max
      };
    });
  } else {
    out.rent_min = l.rent_min != null ? Math.max(0, l.rent_min - delta) : l.rent_min;
    out.rent_max = l.rent_max != null ? Math.max(0, l.rent_max - delta) : l.rent_max;
  }
  return out;
}
function priceElasticity(l, tenants, today) {
  if (!l || !Array.isArray(tenants) || !tenants.length) return [];
  var baseline = tenants.filter(function (t) { return score(l, t, today).verdict === "QUALIFIED"; }).length;
  return PRICE_ELASTICITY_DELTAS.map(function (delta) {
    var discounted = discountListing(l, delta);
    var count = tenants.filter(function (t) { return score(discounted, t, today).verdict === "QUALIFIED"; }).length;
    return { delta: delta, additional: Math.max(0, count - baseline) };
  });
}

// ---------------------------------------------------------------------
// exports
// ---------------------------------------------------------------------

var Scoring = {
  score: score, verdict: verdict, urgencyMult: urgencyMult, coldDays: coldDays, isCold: isCold,
  isColdFromDays: isColdFromDays,
  nearMiss: nearMiss, bestUnit: bestUnit, applyOverride: applyOverride,
  locationScore: locationScore, leaseScore: leaseScore, moveInScore: moveInScore,
  scoreUnitBudget: scoreUnitBudget, workAnchorBonus: workAnchorBonus, hardGates: hardGates,
  parseDate: parseDate, daysAgo: daysAgo, atMidnight: atMidnight, mrtSharesLine: mrtSharesLine,
  isSimilarListing: isSimilarListing, sgtParts: sgtParts, sgtDay: sgtDay, SGT_TZ: SGT_TZ,
  nextBestAction: nextBestAction, weeklyFunnel: weeklyFunnel,
  dataAgeDays: dataAgeDays, dataAgeTier: dataAgeTier,
  discountListing: discountListing, priceElasticity: priceElasticity,
  ADJ: ADJ, MRT_LINES: MRT_LINES,
  BUDGET_HARD_BLOCK_RATIO: BUDGET_HARD_BLOCK_RATIO, NEAR_MISS_CLEARANCE: NEAR_MISS_CLEARANCE,
  COLD_DAYS_THRESHOLD: COLD_DAYS_THRESHOLD, LOOKALIKE_PENALTY: LOOKALIKE_PENALTY,
  WHOLE_UNIT_FLAG: WHOLE_UNIT_FLAG,
  NUDGE_AFTER_DAYS: NUDGE_AFTER_DAYS, DATA_AGE_AMBER_DAYS: DATA_AGE_AMBER_DAYS, DATA_AGE_RED_DAYS: DATA_AGE_RED_DAYS,
  PRICE_ELASTICITY_DELTAS: PRICE_ELASTICITY_DELTAS, DAYS_LISTED_ELASTICITY_THRESHOLD: DAYS_LISTED_ELASTICITY_THRESHOLD
};

// Exposed two ways on purpose:
//  - module.exports for genuine CommonJS consumers.
//  - globalThis.Scoring for everyone else, INCLUDING this repo's own test
//    setup: package.json sets "type":"module", so Node treats every plain
//    .js file (this one included) as an ES module with no CJS `module`
//    global, and this file intentionally has zero import/export syntax
//    (build.py inlines its raw text into a plain, non type=module <script>
//    tag in template.html — a real `export` statement there would throw
//    in the browser). require()ing an ES module still runs its body, so
//    tests require() this file for its side effect and then read
//    globalThis.Scoring back out. window===globalThis in real browsers,
//    so this also covers the classic <script> case.
if (typeof module !== "undefined" && module.exports) module.exports = Scoring;
if (typeof globalThis !== "undefined") globalThis.Scoring = Scoring;
})();
