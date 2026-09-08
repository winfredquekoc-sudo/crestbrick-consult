#!/usr/bin/env python3
"""
wa_intake_shadow_replay.py -- dry run only, scratchpad output only. Proves the listing
binding fix (Sep 2026) actually changes outcomes on real recent conversations, without
sending anything and without touching live state.

For every WhatsApp chat that has a FILLED tenant form reply in the last N days, this
replays the whole message sequence for that chat (both directions, in order) through
intake_engine.handle_event on a FRESH state dict, 4 ways:

  1. OLD code (git ref before the fix) + LIVE listing-index.json
  2. OLD code                          + PROPOSED listing-index.json (build-listing-index.py
                                          --fill-missing output)
  3. NEW code (this worktree)          + LIVE listing-index.json
  4. NEW code                          + PROPOSED listing-index.json

"OLD" vs "NEW" only changes intake_engine.py's behaviour (the dead end fix, outbound
binding, copilot UNBOUND verdict); the runner's own match_listing() text matcher is
unchanged, so this script reimplements the one line that differs between the two runners
(whether match_listing() also runs on outbound rows) rather than needing two runner
snapshots.

Never writes to any live file: messages.db is opened read only (uri mode), DRY_RUN is
forced True on both engine snapshots, and intake-state.json is never touched (a FRESH
state dict is used per chat per config).

Usage:
  /usr/bin/python3 scripts/wa_intake_shadow_replay.py [--days 7] [--old-ref <sha>]
      [--live-idx PATH] [--proposed-idx PATH] [--out DIR]
"""
import os, sys, re, json, sqlite3, argparse, importlib.util, subprocess, datetime, types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WA_DIR = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
sys.path.insert(0, _WA_DIR)

MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
LIVE_IDX_DEFAULT = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")
SCRATCH = ("/private/tmp/claude-501/-Users-winfredquek-crestbrick-consult/"
           "92b405a9-4a65-471d-bd8e-97356c5a5b42/scratchpad")
PROPOSED_IDX_DEFAULT = os.path.join(SCRATCH, "listing-index.proposed.json")
# the commit this fix branched from -- immutable, so "OLD" always means "before this fix"
# regardless of what HEAD becomes later (env override for a re-run against a different base).
OLD_REF_DEFAULT = os.environ.get("WISR_OLD_REF", "3674be590e521b56db3ff8b9f62e438b89d89e62")

import wa_intake_runner as RNR   # unchanged by this fix: safe to reuse from the current tree


def _load_engine_module(name, source_text):
    """exec SOURCE_TEXT as a standalone module under NAME, isolated from sys.modules
    collisions between the old and new snapshots (both are internally named
    'intake_engine' inside their own file, but that never matters at exec time)."""
    mod = types.ModuleType(name)
    mod.__file__ = "<shadow:" + name + ">"
    exec(compile(source_text, "<shadow:" + name + ">", "exec"), mod.__dict__)
    return mod


def _git_show(ref, path):
    return subprocess.run(["git", "show", ref + ":" + path], cwd=_REPO_ROOT,
                          capture_output=True, text=True, check=True).stdout


def load_engines(old_ref):
    new_src = open(os.path.join(_WA_DIR, "intake_engine.py")).read()
    old_src = _git_show(old_ref, "src/wa-pipeline/intake_engine.py")
    return _load_engine_module("intake_engine_new", new_src), _load_engine_module("intake_engine_old", old_src)


def discover_filled_form_chats(days):
    """Chat jids with an inbound (is_from_me=0) row in the last DAYS days that looks like a
    completed tenant form and is NOT an echo of our own blank form."""
    con = sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=15)
    try:
        rows = con.execute(
            "SELECT chat_jid, content FROM messages WHERE is_from_me=0 AND chat_jid LIKE '%@lid' "
            "AND datetime(timestamp) > datetime('now', ?)", ("-" + str(days) + " days",)).fetchall()
    finally:
        con.close()
    chats = set()
    for jid, content in rows:
        if content and RNR._FILLED_RE.search(content) and not RNR._is_our_echo(content):
            chats.add(jid)
    return sorted(chats)


def fetch_chat_rows(jid, days):
    con = sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=15)
    try:
        rows = con.execute(
            "SELECT rowid, is_from_me, content FROM messages WHERE chat_jid=? "
            "AND datetime(timestamp) > datetime('now', ?) ORDER BY rowid", (jid, "-" + str(days) + " days")
        ).fetchall()
    finally:
        con.close()
    return rows


def build_events(rows, reqs, engine_mod, bind_outbound):
    events = []
    for rowid, ifm, content in rows:
        ev = {"jid": None, "msg_id": str(rowid), "text": content or "", "is_from_me": bool(ifm)}
        if bind_outbound or not ifm:
            ev["listing_key"] = RNR.match_listing(content, reqs)
        ev["engine"] = engine_mod.is_engine_outbound(content) if ifm else False
        events.append(ev)
    return events


ACTION_BUCKETS = ("OFFER_VIEWING", "REDIRECT", "ASK_ONE", "COPILOT_VERDICT", "FLAG_NOT_BOUND", "OTHER")


def _bucket(action):
    if action is None:
        return None
    t = action.get("type")
    if t == "OFFER_VIEWING":
        return "OFFER_VIEWING"
    if t == "REDIRECT":
        return "REDIRECT"
    if t == "ASK_ONE":
        return "ASK_ONE"
    if t == "COPILOT_VERDICT":
        return "COPILOT_VERDICT"
    if t == "FLAG_HUMAN" and ("listing not bound" in (action.get("reason") or "")
                              or "no open listing fits" in (action.get("reason") or "")
                              or "possible listings" in (action.get("reason") or "")):
        return "FLAG_NOT_BOUND"
    return "OTHER"


def replay_chat(jid, rows, engine_mod, idx_path, bind_outbound):
    engine_mod.DRY_RUN = True
    engine_mod.IDX = idx_path
    if hasattr(engine_mod, "_TPL_HEADS"):
        delattr(engine_mod, "_TPL_HEADS")
    reqs = engine_mod.listing_reqs()
    events = build_events(rows, reqs, engine_mod, bind_outbound)
    pn = engine_mod.resolve_pn(jid)
    state = {"version": 1, "conversations": {}}
    last_action = None
    for ev in events:
        ev["jid"] = jid
        a = engine_mod.handle_event(state, ev)
        if a is not None:
            last_action = a
    rec = state["conversations"].get(pn, {})
    return {"jid": jid, "pn": pn, "bound": bool(rec.get("listing_key")),
            "listing_key": rec.get("listing_key"), "bucket": _bucket(last_action),
            "action_type": (last_action or {}).get("type"),
            "reason": (last_action or {}).get("reason")}


def run_sweep(chats, chat_rows, old_mod, new_mod, live_idx, proposed_idx):
    configs = [("old", "live", old_mod, live_idx, False),
              ("old", "proposed", old_mod, proposed_idx, False),
              ("new", "live", new_mod, live_idx, True),
              ("new", "proposed", new_mod, proposed_idx, True)]
    results = {}
    for code, idxname, mod, idx_path, bind_outbound in configs:
        key = code + "+" + idxname
        per_chat = {}
        for jid in chats:
            per_chat[jid] = replay_chat(jid, chat_rows[jid], mod, idx_path, bind_outbound)
        results[key] = per_chat
    return results


def summarize(results):
    lines = []
    header = f"{'config':16} {'chats':>6} {'bound':>6} " + " ".join(f"{b:>15}" for b in ACTION_BUCKETS)
    lines.append(header)
    for key, per_chat in results.items():
        n = len(per_chat)
        bound = sum(1 for r in per_chat.values() if r["bound"])
        counts = {b: 0 for b in ACTION_BUCKETS}
        for r in per_chat.values():
            if r["bucket"]:
                counts[r["bucket"]] += 1
        lines.append(f"{key:16} {n:>6} {bound:>6} " + " ".join(f"{counts[b]:>15}" for b in ACTION_BUCKETS))
    return "\n".join(lines)


def diff_old_new(results, idxname):
    old = results["old+" + idxname]
    new = results["new+" + idxname]
    changed = []
    for jid in old:
        o, n = old[jid], new[jid]
        if o["action_type"] != n["action_type"] or o["bound"] != n["bound"] or o["listing_key"] != n["listing_key"]:
            changed.append((jid, o, n))
    return changed


def format_changed(changed):
    lines = []
    for jid, o, n in changed:
        lines.append(f"  {jid}")
        lines.append(f"    OLD: action={o['action_type']!r:20} bound={o['bound']!s:5} listing_key={o['listing_key']!r}")
        lines.append(f"    NEW: action={n['action_type']!r:20} bound={n['bound']!s:5} listing_key={n['listing_key']!r}")
    return "\n".join(lines) if lines else "  (none)"


def results_fingerprint(results):
    """Deterministic, order independent summary used to prove repeat runs agree."""
    out = {}
    for key, per_chat in results.items():
        out[key] = sorted((jid, r["bound"], r["listing_key"], r["action_type"]) for jid, r in per_chat.items())
    return json.dumps(out, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--old-ref", default=OLD_REF_DEFAULT)
    ap.add_argument("--live-idx", default=LIVE_IDX_DEFAULT)
    ap.add_argument("--proposed-idx", default=PROPOSED_IDX_DEFAULT)
    ap.add_argument("--out", default=SCRATCH)
    args = ap.parse_args()

    new_mod, old_mod = load_engines(args.old_ref)
    chats = discover_filled_form_chats(args.days)
    chat_rows = {jid: fetch_chat_rows(jid, args.days) for jid in chats}

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    report = []
    report.append(f"wa_intake_shadow_replay -- {len(chats)} chats with a filled tenant form "
                  f"in the last {args.days} days (DRY RUN, no sends, no live writes)")
    report.append(f"old ref: {args.old_ref}")
    report.append(f"live idx: {args.live_idx}")
    report.append(f"proposed idx: {args.proposed_idx}")
    report.append("")

    results_1 = run_sweep(chats, chat_rows, old_mod, new_mod, args.live_idx, args.proposed_idx)
    report.append("== 4 way summary (run 1) ==")
    report.append(summarize(results_1))
    report.append("")

    for idxname in ("live", "proposed"):
        report.append(f"== outcomes changed, OLD vs NEW code, {idxname} index ==")
        report.append(format_changed(diff_old_new(results_1, idxname)))
        report.append("")

    # determinism: replay the exact same inputs again, compare fingerprints
    new_mod2, old_mod2 = load_engines(args.old_ref)
    results_2 = run_sweep(chats, chat_rows, old_mod2, new_mod2, args.live_idx, args.proposed_idx)
    same = results_fingerprint(results_1) == results_fingerprint(results_2)
    report.append("== determinism check (same inputs replayed twice) ==")
    report.append("IDENTICAL" if same else "MISMATCH -- non deterministic replay, investigate")
    report.append("")

    text = "\n".join(report)
    print(text)
    out_path = os.path.join(args.out, f"shadow-replay-{stamp}.txt")
    with open(out_path, "w") as f:
        f.write(text)
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
