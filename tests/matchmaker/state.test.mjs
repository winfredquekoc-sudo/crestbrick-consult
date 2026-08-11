// State model tests for scripts/matchmaker/app.js — migration, export/import
// merge, ring buffer bounds, scratch ids, offer checklist durability.
// Run: node --test tests/matchmaker/state.test.mjs   (build.py runs it too)
//
// These exercise the REAL functions out of app.js, not a copy of them. app.js
// is a browser script that touches document/DATA at load, so it cannot be
// require()d here; instead its state block is lifted out by source anchors and
// evaluated with a localStorage shim injected, exactly like scoring.test.mjs's
// hardening section does for the smaller pure helpers. A regression in app.js
// therefore fails these, and build.py will not write an artifact when they do.
//
// Every fixture person below is invented (obviously fake names/phones).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
require("../../scripts/matchmaker/scoring.js");
const Scoring = globalThis.Scoring;

const APP_SRC = readFileSync(new URL("../../scripts/matchmaker/app.js", import.meta.url), "utf8");
const APP_LINES = APP_SRC.split("\n");

// Anchored source slice: from the line containing `startAnchor` up to (but not
// including) the line containing `endAnchor`. Anchors over brace matching on
// purpose — several of these functions compare against a literal "{" inside a
// string, which no naive brace matcher survives.
function slice(startAnchor, endAnchor) {
  const a = APP_LINES.findIndex(l => l.includes(startAnchor));
  assert.notEqual(a, -1, "anchor not found in app.js: " + startAnchor);
  const b = APP_LINES.findIndex((l, i) => i > a && l.includes(endAnchor));
  assert.notEqual(b, -1, "end anchor not found in app.js: " + endAnchor);
  return APP_LINES.slice(a, b).join("\n");
}

const STATE_SRC = [
  slice('const ERR_KEY = "cbk_errors"', "window.onerror = function"),
  slice("const ESC_MAP = {", "// For anything landing in href/src"),
  slice('const MARK_PREFIX = "cbk_"', "// ---- undo (57) ----"),
  slice("function rejectionHistogramHtml()", "// (45) tenant missing field lists"),
].join("\n");

// localStorage stand in with the real Storage surface the app uses: getItem /
// setItem / removeItem / key(i) / length, and an opt in quota failure so
// safeSet()'s degrade path can be exercised for real.
function makeStore(initial) {
  const map = new Map(Object.entries(initial || {}));
  return {
    _map: map,
    full: false,
    get length() { return map.size; },
    key(i) { return i < map.size ? Array.from(map.keys())[i] : null; },
    getItem(k) { return map.has(k) ? map.get(k) : null; },
    setItem(k, v) { if (this.full) { const e = new Error("QuotaExceededError"); e.name = "QuotaExceededError"; throw e; } map.set(k, String(v)); },
    removeItem(k) { map.delete(k); },
  };
}

const WEEKDAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

// One "device": its own storage plus the app's real state functions bound to it.
// Pass opts.store to bind a SECOND instance to storage an existing one already
// holds — that is two tabs of one browser on one origin, which is the only way
// to exercise the read cache going stale behind another tab's write.
function makeDevice(opts) {
  opts = opts || {};
  const store = opts.store || makeStore(opts.seed);
  const toasts = [];
  const DATA = { listings: opts.listings || [], tenants: [] };
  const now = opts.now || new Date(2026, 7, 11, 9, 0, 0);
  // Mirrors app.js's real core-state pair exactly (NOW_REAL + NOW_REAL_SGT =
  // Scoring.sgtDay(NOW_REAL)) — writeAutoBackup() now reads NOW_REAL_SGT, not
  // NOW_REAL, so the sliced source needs both injected, same as the real file.
  const api = new Function(
    "localStorage", "toast", "WEEKDAY_NAMES", "NOW_REAL", "NOW_REAL_SGT", "DATA", "Scoring",
    STATE_SRC + "\nreturn {" + [
      "markKey", "readMark", "getMarkV", "patchMark", "clearMarkV",
      "overrideKey", "readOverride", "setOverride",
      "readScratch", "writeScratch", "addScratchTenant", "newScratchId",
      "loadPrefs", "savePrefs", "pushRingBuffer", "readRingBuffer", "pushMarkHistory",
      "offerKey", "readOffer", "setOfferStage", "activeOffers",
      "isMarkKey", "isOfferKey", "exportBlob", "tsOfRaw", "importBlob", "mergeLogInto",
      "writeAutoBackup", "listBackups", "restoreBackup", "rejectionHistogramHtml",
      "pushErrorEntry", "safeSet", "invalidateMarkCacheKey",
      "HISTORY_KEY", "REVEALS_KEY", "ERR_KEY", "SCRATCH_KEY",
      "HISTORY_CAP", "REVEALS_CAP", "ERR_CAP", "SCRATCH_IMPORT_CAP",
    ].join(", ") + ", setDeviceName: (n) => { PREFS.device_name = n; } };"
  )(store, (m) => toasts.push(m), WEEKDAY_NAMES, now, Scoring.sgtDay(now), DATA, Scoring);
  api.store = store;
  api.toasts = toasts;
  if (opts.device) api.setDeviceName(opts.device);
  return api;
}

// =====================================================================
// 1 — v1 migration: bare string marks on every path that touches a mark
// =====================================================================

test("migration: a v1 bare string mark reads, patches, exports and re-imports without loss", () => {
  const d = makeDevice({ seed: { "cbk_LL1_TN1": "Contacted" }, device: "phone" });

  // read paths
  assert.deepEqual(d.readMark("LL1", "TN1"), { v: "Contacted", ts: 0 });
  assert.equal(d.getMarkV("LL1", "TN1"), "Contacted");
  // ts=0 is what makes a legacy mark lose every merge to a real timestamp, and
  // what stops nextBestAction() reading epoch 1970 as "contacted 20000 days ago"
  assert.equal(d.tsOfRaw("Contacted"), 0);
  assert.equal(Scoring.nextBestAction({ verdict: "QUALIFIED", mark: d.readMark("LL1", "TN1"), today: new Date(2026, 7, 11) }), null);

  // export keeps the raw legacy bytes — nothing is decoded then re-encoded
  const blob = d.exportBlob();
  assert.equal(blob.marks["cbk_LL1_TN1"], "Contacted");

  // patching a legacy mark upgrades it in place, keeping v
  const next = d.patchMark("LL1", "TN1", { v: "Viewing booked", viewing_date: "2026-08-14" });
  assert.equal(next.v, "Viewing booked");
  assert.equal(next.viewing_date, "2026-08-14");
  assert.equal(next.by, "phone");
  assert.ok(next.ts > 0, "a patched mark must carry a real timestamp");
  assert.equal(d.readMark("LL1", "TN1").v, "Viewing booked");

  // the history log the funnel reads got the write too
  const hist = d.readRingBuffer(d.HISTORY_KEY);
  assert.equal(hist.length, 1);
  assert.deepEqual({ lid: hist[0].lid, tid: hist[0].tid, v: hist[0].v }, { lid: "LL1", tid: "TN1", v: "Viewing booked" });
});

test("migration: fuzzed localStorage of mixed legacy/v2/corrupt values never throws and never loses a valid entry", () => {
  // Deterministic LCG so a failure is reproducible from the seed alone.
  let s = 1234567;
  const rnd = () => (s = (s * 1103515245 + 12345) % 2147483648) / 2147483648;
  const pick = a => a[Math.floor(rnd() * a.length)];

  const CORRUPT = [
    "{not json at all", "{", "}", "", "null", "undefined", "[1,2,3]", "42", '"just a string"',
    '{"v":123}', '{"v":null}', '{"v":{"nested":true}}', '{"ts":"not a number"}', '{"ts":null}',
    '{"v":"Contacted","ts":{}}', '{"v":"Not interested","reason":42}',
    '{"snooze_until":"not-a-date"}', '{"v":"Viewing booked","viewing_date":99}',
    '{"__proto__":{"polluted":true}}', '{"v":"<img src=x onerror=alert(1)>","ts":1}',
  ];
  const LEGACY = ["Contacted", "Not interested", "Queued", "Viewing booked"];

  const seed = {};
  const valid = {};   // key -> the exact raw string a v2 mark was seeded with
  for (let i = 0; i < 400; i++) {
    const key = "cbk_LL" + Math.floor(rnd() * 40) + "_TN" + Math.floor(rnd() * 60);
    const roll = rnd();
    if (roll < 0.30) { seed[key] = pick(LEGACY); delete valid[key]; }
    else if (roll < 0.65) {
      const raw = JSON.stringify({ v: pick(LEGACY), ts: 1754870000000 + Math.floor(rnd() * 1e7), reason: "budget" });
      seed[key] = raw; valid[key] = raw;
    } else { seed[key] = pick(CORRUPT); delete valid[key]; }
  }
  // non mark keys sharing the cbk_ prefix, some of them corrupt too
  seed["cbk_prefs"] = '{"masked":true,"device_name":"phone"}';
  seed["cbk_history"] = '{"not":"an array"}';
  seed["cbk_reveals"] = "]]] broken";
  seed["cbk_errors"] = "17";
  seed["cbk_scratch"] = '{"not":"an array"}';
  seed["cbk_offer_LL1_TN1"] = '{"stage":1,"ts":1754870000000}';
  seed["cbk_offer_LL2_TN2"] = "corrupt";
  seed["cbk_backup_mon"] = "not json";
  seed["cbko_LL1_TN1"] = '{"verdict":"QUALIFIED","ts":1754870000000}';
  seed["cbko_LL3_TN3"] = "corrupt override";

  const d = makeDevice({ seed, listings: [{ id: "LL1", name: "Test Block" }] });

  // every read path the app has over marks, run over the whole fuzzed store
  assert.doesNotThrow(() => {
    for (let i = 0; i < d.store.length; i++) {
      const k = d.store.key(i);
      if (!k.startsWith("cbk_") || !d.isMarkKey(k)) continue;
      const rest = k.slice(4), us = rest.indexOf("_");
      const lid = rest.slice(0, us), tid = rest.slice(us + 1);
      d.readMark(lid, tid);
      d.getMarkV(lid, tid);
      d.tsOfRaw(d.store.getItem(k));
      Scoring.nextBestAction({ verdict: "QUALIFIED", mark: d.readMark(lid, tid) || {}, today: new Date(2026, 7, 11) });
    }
    d.activeOffers();
    d.rejectionHistogramHtml();
    d.readScratch();
    d.readRingBuffer(d.HISTORY_KEY);
    Scoring.weeklyFunnel(d.readRingBuffer(d.HISTORY_KEY), new Date(2026, 7, 11));
    d.exportBlob();
  });

  // corrupt containers degrade to empty rather than throwing
  assert.deepEqual(d.readScratch(), []);
  assert.deepEqual(d.readRingBuffer(d.HISTORY_KEY), []);
  assert.deepEqual(d.readRingBuffer(d.REVEALS_KEY), []);

  // no valid entry was lost through export -> import into a fresh device
  const blob = d.exportBlob();
  for (const k in valid) assert.equal(blob.marks[k], valid[k], "valid v2 mark missing from export: " + k);
  const fresh = makeDevice({});
  fresh.importBlob(JSON.parse(JSON.stringify(blob)));
  for (const k in valid) assert.equal(fresh.store.getItem(k), valid[k], "valid v2 mark lost on import: " + k);

  // and a corrupt store cannot poison the object prototype
  assert.equal({}.polluted, undefined);
});

test("migration: a wedged ring buffer key recovers instead of silently swallowing every later write", () => {
  // A non array under cbk_history used to make arr.push() throw inside the
  // catch-all, so the funnel read zero forever with nothing to show for it.
  const d = makeDevice({ seed: { "cbk_history": '{"was":"an object"}', "cbk_errors": "42" } });
  d.pushMarkHistory("LL1", "TN1", "Contacted");
  assert.equal(d.readRingBuffer(d.HISTORY_KEY).length, 1);
  d.pushErrorEntry({ ts: 1, kind: "error", msg: "boom" });
  assert.equal(d.readRingBuffer(d.ERR_KEY).length, 1);

  // an array holding junk entries must not take the merge down with it
  const junk = makeDevice({ seed: { "cbk_history": '[null, 7, "x", {"lid":"LL1","tid":"TN1","v":"Contacted","ts":100}]' } });
  let res;
  assert.doesNotThrow(() => { res = junk.importBlob({ history: [{ lid: "LL2", tid: "TN2", v: "Contacted", ts: 200 }] }); });
  assert.equal(res.history, 1);
  const merged = junk.readRingBuffer(junk.HISTORY_KEY);
  assert.equal(merged.length, 2, "the junk entries are dropped, the real ones survive");
  assert.deepEqual(merged.map(r => r.tid), ["TN1", "TN2"]);
});

// =====================================================================
// 2 — export / import round trip and merge policy
// =====================================================================

function markRaw(v, ts, extra) { return JSON.stringify(Object.assign({ v, ts }, extra || {})); }

test("merge: latest ts wins per key, legacy strings count as ts 0, ties go to the import", () => {
  const phone = makeDevice({ device: "phone", seed: {
    "cbk_LL1_TN1": markRaw("Viewing booked", 2000),   // newer than the laptop's
    "cbk_LL2_TN2": markRaw("Contacted", 1000),        // older than the laptop's
    "cbk_LL3_TN3": markRaw("Queued", 1500),           // laptop has none
    "cbk_LL4_TN4": markRaw("Contacted", 1500),        // laptop has a legacy string
    "cbk_LL5_TN5": markRaw("Contacted", 3000),        // exact tie with the laptop
  }});
  const laptop = makeDevice({ device: "laptop", seed: {
    "cbk_LL1_TN1": markRaw("Contacted", 1000),
    "cbk_LL2_TN2": markRaw("Not interested", 2000),
    "cbk_LL4_TN4": "Contacted",                        // v1 bare string
    "cbk_LL5_TN5": markRaw("Queued", 3000),
  }});

  const res = laptop.importBlob(phone.exportBlob());

  assert.equal(laptop.getMarkV("LL1", "TN1"), "Viewing booked", "newer import must win");
  assert.equal(laptop.getMarkV("LL2", "TN2"), "Not interested", "newer local must survive");
  assert.equal(laptop.getMarkV("LL3", "TN3"), "Queued", "a key only the import has must land");
  assert.equal(laptop.getMarkV("LL4", "TN4"), "Contacted");
  assert.equal(laptop.readMark("LL4", "TN4").ts, 1500, "a legacy local mark (ts 0) must lose to a real timestamp");
  assert.equal(laptop.getMarkV("LL5", "TN5"), "Contacted", "an exact tie favours the import the user just asked for");
  assert.equal(res.marks, 4);
});

test("merge: a hostile blob cannot reach prefs, a backup slot, or Object.prototype", () => {
  const d = makeDevice({ device: "laptop", seed: { "cbk_prefs": '{"masked":true,"assistant_mode":true,"device_name":"laptop"}' } });
  d.writeAutoBackup();
  // writeAutoBackup() now keys off NOW_REAL_SGT (the Singapore calendar day),
  // not NOW_REAL's own getters — match that here so this stays correct under
  // any TZ the suite is run with (node --test respects TZ).
  const backupKey = "cbk_backup_" + WEEKDAY_NAMES[Scoring.sgtDay(new Date(2026, 7, 11, 9, 0, 0)).getDay()];
  const backupBefore = d.store.getItem(backupKey);

  const hostile = JSON.parse(JSON.stringify({
    version: 2,
    marks: {
      "cbk_prefs": '{"masked":false,"assistant_mode":false}',        // privacy switches off
      "cbk_reveals": "[]",                                            // wipe the PDPA trail
      "cbk_errors": "[]",
      "cbk_history": "[]",
      "cbk_scratch": "[]",
      "cbk_backup_mon": "{}",
      [backupKey]: "{}",
      "cbk_offer_LL1_TN1": '{"stage":4,"ts":9999999999999}',          // reserved offer key via the marks map
      "cbko_LL1_TN1": '{"verdict":"QUALIFIED","ts":9999999999999}',   // override via the marks map
      "__proto__": '{"polluted":true}',
      "constructor": '{"polluted":true}',
      "cbk_LL9_TN9": markRaw("Contacted", 5000),                      // one legitimate entry
    },
    overrides: { "cbk_prefs": '{"masked":false}', "not_an_override": "{}" },
    offers: { "cbk_prefs": '{"stage":4}', "cbk_offer_": '{"stage":4}' },
  }));

  const res = d.importBlob(hostile);

  assert.equal(d.store.getItem("cbk_prefs"), '{"masked":true,"assistant_mode":true,"device_name":"laptop"}', "prefs must be untouchable by an import");
  assert.equal(d.store.getItem(backupKey), backupBefore, "a backup slot must be untouchable by an import");
  assert.equal(d.store.getItem("cbk_backup_mon"), null);
  assert.equal(d.store.getItem("cbk_offer_LL1_TN1"), null, "an offer key must not be writable through the marks map");
  assert.equal(d.store.getItem("cbko_LL1_TN1"), null, "an override must not be writable through the marks map");
  assert.equal(d.store.getItem("cbk_offer_"), null, "a prefix-only offer key is not a real pair");
  assert.equal(d.store.getItem("not_an_override"), null);
  assert.equal(d.getMarkV("LL9", "TN9"), "Contacted", "the legitimate entry still lands");
  assert.equal(res.marks, 1);
  assert.equal({}.polluted, undefined, "Object.prototype must not be polluted");
  assert.equal(Object.prototype.polluted, undefined);
});

test("merge: an oversized or unserializable blob is bounded, not swallowed whole", () => {
  const d = makeDevice({});
  const scratch = [];
  for (let i = 0; i < 5000; i++) scratch.push({ id: "TMPflood" + i, name: "Flood " + i, _scratch: true });
  // a deeply nested entry: it must be rejected on its own, without aborting the import
  let deep = { id: "TMPdeep", name: "Deep" }, node = deep;
  for (let i = 0; i < 5000; i++) { node.next = {}; node = node.next; }
  scratch.push(deep);
  // and one carrying a megabyte of text
  scratch.push({ id: "TMPfat", name: "x".repeat(1e6) });

  let res;
  assert.doesNotThrow(() => { res = d.importBlob({ scratch, marks: { "cbk_LL1_TN1": markRaw("Contacted", 1) } }); });
  assert.equal(res.scratch, d.SCRATCH_IMPORT_CAP, "scratch import must stop at the cap");
  assert.equal(d.readScratch().length, d.SCRATCH_IMPORT_CAP);
  assert.equal(d.getMarkV("LL1", "TN1"), "Contacted", "marks in the same blob still land");
  assert.ok(!d.readScratch().some(t => t.id === "TMPfat"), "an oversized entry is dropped");

  // a mark value that is itself deeply nested JSON must not throw out of tsOfRaw
  const deepRaw = "{" + '"a":['.repeat(200) + "]".repeat(200) + "}";
  assert.doesNotThrow(() => d.tsOfRaw(deepRaw));
  assert.equal(d.tsOfRaw(deepRaw), 0);
});

test("merge: a non numeric ts cannot win the merge", () => {
  const d = makeDevice({ seed: { "cbk_LL1_TN1": markRaw("Not interested", 5000) } });
  d.importBlob({ marks: { "cbk_LL1_TN1": '{"v":"Contacted","ts":"9e99"}' } });
  assert.equal(d.getMarkV("LL1", "TN1"), "Not interested", "a string ts is treated as no timestamp, so it loses");
  assert.equal(d.tsOfRaw('{"v":"x","ts":{}}'), 0);
  assert.equal(d.tsOfRaw('{"v":"x","ts":null}'), 0);
  assert.equal(d.tsOfRaw('{"v":"x","ts":1700000000000}'), 1700000000000);
});

test("merge: importing the same blob twice changes nothing the second time", () => {
  const phone = makeDevice({ device: "phone" });
  phone.patchMark("LL1", "TN1", { v: "Contacted" });
  phone.patchMark("LL2", "TN2", { v: "Viewing booked" });
  phone.setOfferStage("LL1", "TN1", 2);
  phone.addScratchTenant({ name: "Siti Test", phone: "90000009" });
  const blob = JSON.parse(JSON.stringify(phone.exportBlob()));

  const laptop = makeDevice({ device: "laptop" });
  const first = laptop.importBlob(JSON.parse(JSON.stringify(blob)));
  const snapshot = JSON.stringify(laptop.exportBlob().marks);
  const scratchAfterFirst = laptop.readScratch().length;
  const historyAfterFirst = laptop.readRingBuffer(laptop.HISTORY_KEY).length;

  const second = laptop.importBlob(JSON.parse(JSON.stringify(blob)));
  assert.equal(JSON.stringify(laptop.exportBlob().marks), snapshot);
  assert.equal(laptop.readScratch().length, scratchAfterFirst, "a scratch tenant must not be duplicated on re-import");
  assert.equal(laptop.readRingBuffer(laptop.HISTORY_KEY).length, historyAfterFirst, "history must not double count on re-import");
  assert.equal(second.scratch, 0);
  assert.equal(second.history, 0);
  assert.equal(first.history, 2);
});

// =====================================================================
// 3 — scratch tenant ids
// =====================================================================

test("scratch: ids generated independently on two devices do not collide", () => {
  const phone = makeDevice({ device: "phone" });
  const laptop = makeDevice({ device: "laptop" });
  const a = phone.addScratchTenant({ name: "Aisha Test", phone: "90000011" });
  const b = laptop.addScratchTenant({ name: "Ravi Test", phone: "90000012" });
  assert.notEqual(a.id, b.id, "two devices must not mint the same scratch id");
  assert.ok(/^TMP[a-z0-9]+$/.test(a.id), "id stays key safe (no underscore, no colon): " + a.id);

  const ids = new Set();
  for (let i = 0; i < 2000; i++) ids.add(phone.newScratchId());
  assert.equal(ids.size, 2000, "id generator must not repeat itself");
});

test("scratch: a legacy TMP<n> collision re-keys the incoming person instead of dropping them", () => {
  const laptop = makeDevice({ device: "laptop", seed: { "cbk_scratch": JSON.stringify([{ id: "TMP1", name: "Aisha Test", phone: "90000011", _scratch: true }]) } });
  const res = laptop.importBlob({ scratch: [{ id: "TMP1", name: "Ravi Test", phone: "90000012" }] });

  const all = laptop.readScratch();
  assert.equal(res.scratch, 1);
  assert.equal(all.length, 2, "the incoming person must not be silently dropped as a duplicate id");
  const names = all.map(t => t.name).sort();
  assert.deepEqual(names, ["Aisha Test", "Ravi Test"]);
  assert.notEqual(all[0].id, all[1].id);
  assert.ok(all.every(t => t._scratch === true), "an imported record must carry the scratch flag, never pass as database data");
});

test("scratch: existing stored entries keep their ids — nothing regenerates them", () => {
  const d = makeDevice({ device: "phone", seed: { "cbk_scratch": JSON.stringify([{ id: "TMP1", name: "Aisha Test", _scratch: true }, { id: "TMP2", name: "Ravi Test", _scratch: true }]) } });
  const added = d.addScratchTenant({ name: "Chen Test" });
  const ids = d.readScratch().map(t => t.id);
  assert.deepEqual(ids.slice(0, 2), ["TMP1", "TMP2"], "stored ids are load bearing (marks are keyed by them) and must never change");
  assert.equal(ids[2], added.id);
  assert.notEqual(added.id, "TMP3");
});

// =====================================================================
// 4 — device attribution and funnel counting across a merge
// =====================================================================

test("device: by:<device> attribution survives the merge, and the funnel counts each mark once", () => {
  const phone = makeDevice({ device: "phone" });
  phone.patchMark("LL1", "TN1", { v: "Contacted" });
  phone.patchMark("LL1", "TN2", { v: "Viewing booked" });

  const laptop = makeDevice({ device: "laptop" });
  laptop.patchMark("LL2", "TN3", { v: "Contacted" });

  laptop.importBlob(JSON.parse(JSON.stringify(phone.exportBlob())));

  assert.equal(laptop.readMark("LL1", "TN1").by, "phone", "the originating device's stamp must survive the merge");
  assert.equal(laptop.readMark("LL2", "TN3").by, "laptop");

  // the funnel reads the merged history log, so the phone's day of work is
  // visible on the laptop — and each mark is counted exactly once.
  // weeklyFunnel's `today` contract is "already a pinned SGT-day marker" (see
  // scoring.js) — real app.js always passes NOW_REAL_SGT, never NOW_REAL
  // itself, so this test does the same via Scoring.sgtDay(), rather than a
  // raw `new Date()` whose OWN calendar day would be read in the test
  // process's own timezone and could disagree with the marks' (sgtDay()
  // reduced) timestamps by a day whenever SGT and that TZ are on different
  // calendar days at the moment the suite happens to run.
  const funnel = Scoring.weeklyFunnel(laptop.readRingBuffer(laptop.HISTORY_KEY), Scoring.sgtDay(new Date()));
  assert.equal(funnel.contacted, 2);
  assert.equal(funnel.viewings, 1);

  // marking the same pair again on the laptop is a real second event and does
  // count, but re-importing the phone's blob adds nothing
  laptop.importBlob(JSON.parse(JSON.stringify(phone.exportBlob())));
  const again = Scoring.weeklyFunnel(laptop.readRingBuffer(laptop.HISTORY_KEY), Scoring.sgtDay(new Date()));
  assert.deepEqual(again, funnel);
});

// =====================================================================
// 5 — mark key parsing
// =====================================================================

test("keys: cbk_<listing>_<tenant> round trips, and a tenant id may contain an underscore", () => {
  const d = makeDevice({ listings: [{ id: "LL1", name: "Test Block" }] });
  assert.equal(d.markKey("LL1", "TN1"), "cbk_LL1_TN1");

  // the parse (activeOffers, the decline histogram) splits on the FIRST
  // underscore, so everything after it is the tenant id: a tenant id holding an
  // underscore round trips intact.
  d.setOfferStage("LL1", "TN_ODD_1", 1);
  assert.deepEqual(d.activeOffers(), [{ lid: "LL1", tid: "TN_ODD_1", stage: 1 }]);
  d.patchMark("LL1", "TN_ODD_1", { v: "Not interested", reason: "budget" });
  assert.match(d.rejectionHistogramHtml(), /1 declined: 1 budget/);

  // A LISTING id holding an underscore is the shape that cannot round trip —
  // "cbk_LL_1_TN1" parses back as listing "LL", tenant "1_TN1", and collides
  // with the genuine pair LL + 1_TN1. export_data.validate_key_ids() rejects
  // such ids at the source so this can never be written; pinned here so the two
  // halves of the invariant stay together.
  const rest = "LL_1_TN1", us = rest.indexOf("_");
  assert.equal(rest.slice(0, us), "LL");
  assert.notEqual(rest.slice(0, us), "LL_1");
});

// =====================================================================
// 6 — offer checklist durability
// =====================================================================

test("offers: offer progress survives an export/import device merge, newest stage wins", () => {
  const phone = makeDevice({ device: "phone" });
  phone.setOfferStage("LL1", "TN1", 3);          // tenancy agreement
  phone.setOfferStage("LL2", "TN2", 0);          // holding deposit

  const blob = JSON.parse(JSON.stringify(phone.exportBlob()));
  assert.ok(blob.offers, "the blob must carry an offers map");
  assert.equal(Object.keys(blob.offers).length, 2);

  const laptop = makeDevice({ device: "laptop" });
  laptop.store.setItem("cbk_offer_LL1_TN1", JSON.stringify({ stage: 1, ts: 1 }));   // stale local copy
  const res = laptop.importBlob(blob);

  assert.equal(res.offers, 2);
  assert.equal(laptop.readOffer("LL1", "TN1").stage, 3, "the newer stage must win");
  assert.equal(laptop.readOffer("LL2", "TN2").stage, 0);
  assert.equal(laptop.activeOffers().length, 2);

  // an older incoming stage must not roll a live offer backwards
  const older = { offers: { "cbk_offer_LL1_TN1": JSON.stringify({ stage: 0, ts: 1 }) } };
  laptop.importBlob(older);
  assert.equal(laptop.readOffer("LL1", "TN1").stage, 3);

  // offers stay out of the marks map in both directions
  assert.equal(laptop.isMarkKey("cbk_offer_LL1_TN1"), false);
  assert.equal(Object.keys(blob.marks).length, 0);
});

test("backups: the daily slots are restorable, which is the only thing that makes writing them worth the quota", () => {
  const d = makeDevice({ device: "phone" });
  d.patchMark("LL1", "TN1", { v: "Viewing booked", viewing_date: "2026-08-14" });
  d.setOfferStage("LL1", "TN1", 2);
  d.writeAutoBackup();

  const slots = d.listBackups();
  assert.equal(slots.length, 1);
  assert.equal(slots[0].marks, 1);

  // a bad bulk action wipes the pair
  d.clearMarkV("LL1", "TN1");
  d.store.removeItem("cbk_offer_LL1_TN1");
  assert.equal(d.getMarkV("LL1", "TN1"), "");

  const res = d.restoreBackup(slots[0].key);
  assert.equal(d.getMarkV("LL1", "TN1"), "Viewing booked");
  assert.equal(d.readMark("LL1", "TN1").viewing_date, "2026-08-14");
  assert.equal(d.readOffer("LL1", "TN1").stage, 2, "offer progress must come back too");
  assert.equal(res.marks, 1);

  // restoring must never roll back work done since the backup was written.
  // patchMark stamps Date.now(), and the merge rule is "newest wins, ties go to
  // the incoming copy" — so the gap has to be real for the assertion to mean
  // anything. In the app it always is (the slot is written at page load, the
  // mark on a click seconds later).
  const spin = Date.now(); while (Date.now() === spin) { /* let the clock tick */ }
  d.patchMark("LL1", "TN1", { v: "Contacted", note: "newer" });
  d.restoreBackup(slots[0].key);
  assert.equal(d.getMarkV("LL1", "TN1"), "Contacted", "a newer local mark survives a restore");

  assert.equal(d.restoreBackup("cbk_backup_nope"), null);
});

// =====================================================================
// 7 — ring buffer bounds, enforced on write
// =====================================================================

test("rings: caps are enforced on WRITE, at the cap and above it", () => {
  const d = makeDevice({});
  const cap = d.REVEALS_CAP;
  assert.equal(cap, 500);
  assert.equal(d.HISTORY_CAP, 1000);
  assert.equal(d.ERR_CAP, 50);

  for (let i = 0; i < cap; i++) d.pushRingBuffer(d.REVEALS_KEY, cap, { kind: "tenant", id: "TN" + i, ts: i });
  let arr = d.readRingBuffer(d.REVEALS_KEY);
  assert.equal(arr.length, cap, "exactly at the cap, nothing has been dropped yet");
  assert.equal(arr[0].id, "TN0");

  d.pushRingBuffer(d.REVEALS_KEY, cap, { kind: "tenant", id: "TNlast", ts: cap });
  arr = d.readRingBuffer(d.REVEALS_KEY);
  assert.equal(arr.length, cap, "one past the cap stays at the cap");
  assert.equal(arr[0].id, "TN1", "the oldest entry is the one dropped");
  assert.equal(arr[cap - 1].id, "TNlast");

  // an over cap array arriving from an older build or an import is trimmed by
  // the very next write rather than being allowed to keep growing
  const over = [];
  for (let i = 0; i < cap + 250; i++) over.push({ kind: "tenant", id: "OLD" + i, ts: i });
  d.store.setItem(d.REVEALS_KEY, JSON.stringify(over));
  d.pushRingBuffer(d.REVEALS_KEY, cap, { kind: "tenant", id: "TNnew", ts: 99999 });
  arr = d.readRingBuffer(d.REVEALS_KEY);
  assert.equal(arr.length, cap);
  assert.equal(arr[cap - 1].id, "TNnew");

  // the error ring, written from window.onerror, is bounded the same way
  for (let i = 0; i < d.ERR_CAP + 20; i++) d.pushErrorEntry({ ts: i, kind: "error", msg: "boom " + i });
  assert.equal(d.readRingBuffer(d.ERR_KEY).length, d.ERR_CAP);
});

test("rings: an import cannot push a log past its cap", () => {
  const d = makeDevice({});
  const reveals = [], history = [];
  for (let i = 0; i < 4000; i++) {
    reveals.push({ kind: "tenant", id: "TN" + i, ts: i });
    history.push({ lid: "LL1", tid: "TN" + i, v: "Contacted", ts: i });
  }
  d.importBlob({ reveals, history });
  assert.equal(d.readRingBuffer(d.REVEALS_KEY).length, d.REVEALS_CAP);
  assert.equal(d.readRingBuffer(d.HISTORY_KEY).length, d.HISTORY_CAP);
  // capped from the OLD end, so what survives is the most recent activity
  assert.equal(d.readRingBuffer(d.HISTORY_KEY)[d.HISTORY_CAP - 1].ts, 3999);
});

// Two tabs of the same browser on the same origin share one localStorage, but
// each has its own in-memory read cache. The cache is what makes this possible
// at all — before it existed, any render in the second tab read straight
// through and picked the other tab's write up on its own.
test("tabs: a mark written in another tab is not served stale from this tab's read cache", () => {
  const shared = makeStore({});
  const tabA = makeDevice({ device: "desktop", store: shared });
  const tabB = makeDevice({ device: "phone", store: shared });

  // tabB reads the pair first, which is what populates its read cache.
  assert.equal(tabB.getMarkV("LL1", "TN1"), "");
  tabA.patchMark("LL1", "TN1", { v: "Contacted" });
  assert.equal(tabA.getMarkV("LL1", "TN1"), "Contacted", "the writing tab sees its own write");
  assert.equal(shared.getItem(tabA.markKey("LL1", "TN1")) !== null, true, "and it did reach shared storage");

  // The browser fires `storage` in tabB only. Until it is handled, tabB is stale
  // — that staleness is the whole reason this handler has to exist.
  assert.equal(tabB.getMarkV("LL1", "TN1"), "", "precondition: the read cache is what goes stale");
  tabB.invalidateMarkCacheKey(tabB.markKey("LL1", "TN1"));
  assert.equal(tabB.getMarkV("LL1", "TN1"), "Contacted", "after the storage event tabB reads what tabA wrote");

  // Overrides use a different prefix and must invalidate too.
  tabB.readOverride("LL1", "TN1");
  tabA.setOverride("LL1", "TN1", "QUALIFIED", "why");
  assert.equal(tabB.readOverride("LL1", "TN1"), null, "precondition: the override read is cached too");
  tabB.invalidateMarkCacheKey(tabB.overrideKey("LL1", "TN1"));
  assert.equal((tabB.readOverride("LL1", "TN1") || {}).verdict, "QUALIFIED");

  // key === null is how the browser reports a wholesale localStorage.clear().
  tabA.patchMark("LL2", "TN2", { v: "Queued" });
  tabB.getMarkV("LL2", "TN2");
  shared._map.clear();
  tabB.invalidateMarkCacheKey(null);
  assert.equal(tabB.getMarkV("LL2", "TN2"), "", "a clear() in another tab drops the whole cache");

  // An unrelated key must not clear anything wholesale.
  tabB.patchMark("LL3", "TN3", { v: "Contacted" });
  tabB.invalidateMarkCacheKey("some_other_app_key");
  assert.equal(tabB.getMarkV("LL3", "TN3"), "Contacted");
});

test("storage full: a mark write degrades with a message instead of half applying", () => {
  const d = makeDevice({ device: "phone", seed: { "cbk_LL1_TN1": markRaw("Contacted", 1000) } });
  d.store.full = true;
  const out = d.patchMark("LL1", "TN1", { v: "Not interested", reason: "budget" });
  assert.equal(out.v, "Contacted", "the caller gets the unchanged prior mark back, not a fake success");
  assert.equal(d.getMarkV("LL1", "TN1"), "Contacted", "stored state is untouched");
  assert.ok(d.toasts.some(t => /storage is full/i.test(t)), "the user is told, rather than the click silently doing nothing");
  // and the history log did not record an event that never happened
  assert.equal(d.readRingBuffer(d.HISTORY_KEY).length, 0);
});
