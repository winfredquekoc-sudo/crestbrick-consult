#!/usr/bin/env python3
"""
wa_intake_resume_replay.py -- dry run only, scratchpad output only, messages.db opened
READ ONLY. Proves out takeover resume (Feature 1) and the short lease auto reply (Feature
2) against real recent conversations without sending anything and without touching live
state (mirrors scripts/wa_intake_shadow_replay.py's safety model).

For every chat with a genuine hand reply (an is_from_me row that is not one of our own
engine template sends) in the last N days, replays that chat's ENTIRE message history, both
directions, in order, through intake_engine.handle_event on a FRESH state dict. For every
inbound row inside the reporting window that qualifies for takeover resume, this prints
whether it would have been auto-sent (exact text, allow listed action type) or drafted
(REDIRECT/SUGGEST_ALT/unanswerable question/etc, DRY -- no Haiku call in this pass so the
determinism check is meaningful run to run). Separately lists every chat where the short
lease free text trigger would have fired, with the exact triggering sentence.

Usage:
  /usr/bin/python3 scripts/wa_intake_resume_replay.py [--days 7] [--out DIR]
  /usr/bin/python3 scripts/wa_intake_resume_replay.py --sample-drafts 8   # real Haiku calls
"""
import os, sys, json, sqlite3, argparse, datetime

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo) -- set before importing any
# wa-pipeline module. This replay never calls a send/notify function itself (it drives
# intake_engine.handle_event on a fresh state dict), but it imports wa_intake_runner, and
# defense in depth here costs nothing. See wa_intake_notify.py _tg_send's docstring for the
# root cause this guards against.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WA_DIR = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
sys.path.insert(0, _WA_DIR)
import wa_intake_paths as _P
import intake_engine as E
import wa_intake_runner as RNR
import wa_intake_resume as RES

# STEP 0 sandbox seal (9 Sep 2026 merge redo): read only (mode=ro) against REAL recent
# WhatsApp history is this script's intentional design (see module docstring), not a leak
# -- it never writes to messages.db and intake-state.json is never touched. MSG_DB now
# honours WA_INTAKE_MSG_DB purely so a hermetic test can point this at a throwaway fixture
# db; production usage (no env var set) is unchanged.
MSG_DB = _P.paths()["messages_db"]
SCRATCH = ("/private/tmp/claude-501/-Users-winfredquek-crestbrick-consult/"
          "92b405a9-4a65-471d-bd8e-97356c5a5b42/scratchpad")


def _ro_con():
    return sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=15)


def discover_handtake_chats(days, landlords):
    """Chats with a genuine hand reply (is_from_me, not our own engine template) in the
    last DAYS days -- the population takeover resume ever applies to. Skips landlord chats,
    mirroring the runner's own pre-loop gate (a landlord never becomes a tenant record), so
    this population matches production rather than over counting."""
    con = _ro_con()
    try:
        rows = con.execute(
            "SELECT DISTINCT chat_jid, content FROM messages WHERE is_from_me=1 "
            "AND chat_jid LIKE '%@lid' AND datetime(timestamp) > datetime('now', ?)",
            ("-" + str(days) + " days",)).fetchall()
    finally:
        con.close()
    chats = set()
    for jid, content in rows:
        if not content or E.is_engine_outbound(content):
            continue
        pn = E.resolve_pn(jid)
        if pn and pn in landlords:
            continue          # landlord DB skip -- mirrors wa_intake_runner.run()'s own gate
        chats.add(jid)
    return sorted(chats)


def fetch_full_chat(jid):
    """Every row ever recorded for this chat -- correctness of 'no outbound answered it
    yet' needs the WHOLE history, not just the reporting window."""
    con = _ro_con()
    try:
        rows = con.execute(
            "SELECT rowid, is_from_me, content, timestamp FROM messages "
            "WHERE chat_jid=? ORDER BY rowid", (jid,)).fetchall()
    finally:
        con.close()
    return rows


def _in_window(ts, days):
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    except Exception:
        return True     # unparseable -> do not silently drop it from the report
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - dt).total_seconds() <= days * 86400


def replay_chat(con, jid, rows, days):
    """Returns a list of report rows: one per resume decision and one per short lease
    free text fire that fell inside the reporting window. Eligibility for resume is decided
    by the SAME wa_intake_resume.resume_reason_blocked the live runner calls (not a separate
    reimplementation) -- supply/buyer/excluded-status latches, the excluded_reason()
    re-check, the 5 minute wait, and the rowid-ordered "did Winfred already answer this"
    check all apply exactly as they do in production, so these numbers ARE production
    numbers. CON is a read only connection to the SAME messages.db these rows came from --
    resume_reason_blocked's own "already answered" check queries it directly."""
    engine_mod = E
    engine_mod.DRY_RUN = True
    state = {"version": 1, "conversations": {}}
    pn = engine_mod.resolve_pn(jid)
    out = []
    for rowid, ifm, content, ts in rows:
        ev = {"jid": jid, "msg_id": str(rowid), "text": content or "", "is_from_me": bool(ifm),
              "ts": ts}
        ev["listing_key"] = RNR.match_listing(content)
        ev["engine"] = engine_mod.is_engine_outbound(content) if ifm else False
        pre_snapshot, disputed = None, False
        if not ifm:
            rec_before = state["conversations"].get(pn)
            # B established (review fix): full shallow snapshot (mirrors wa_intake_runner.py
            # exactly) -- is_established_prospect() needs listing_key_source/profile/
            # first_inbound_text/outbound_before_first_inbound too, not just form_sent/
            # listing_key. 'profile' gets its own copy so this inbound's own extraction
            # never leaks into the "before" snapshot through a shared dict reference.
            pre_snapshot = dict(rec_before) if rec_before is not None else {}
            pre_snapshot["profile"] = dict((rec_before or {}).get("profile") or {})
            under_takeover = bool(rec_before and (rec_before.get("manual_takeover")
                                                  or rec_before.get("human_takeover")))
            # B2: dispute/legal escalation language in the last 10 messages -> never
            # resume-eligible (mirrors wa_intake_runner.run()'s own gate exactly).
            disputed = under_takeover and RES.dispute_language_recent(con, jid)
            if not disputed:
                blocked = RES.resume_reason_blocked(con, "rowid", jid, rec_before, rowid, ts)
                ev["resume"] = blocked is None
        a = engine_mod.handle_event(state, ev)
        if ifm or not _in_window(ts, days):
            continue          # only inbound rows inside the reporting window are reportable
        if disputed:
            out.append({"jid": jid, "pn": pn, "kind": "DISPUTE_BLOCKED", "rowid": rowid,
                       "inbound": content})
        elif ev.get("resume"):
            if RES.needs_draft(a, pre_snapshot):
                RES.revert_unsent_form(a, state["conversations"].get(pn, {}), pre_snapshot)
                out.append({"jid": jid, "pn": pn, "kind": "DRAFT", "rowid": rowid,
                           "inbound": content, "would_be_type": (a or {}).get("type")})
            else:
                texts = (a or {}).get("texts") or ([a["text"]] if (a or {}).get("text") else [])
                # An allow listed action with NO text (a bare FLAG_HUMAN/COPILOT_VERDICT/
                # VIEWING_TIME_PROPOSED notify) never reaches the runner's real send choke --
                # it hits the runner's OWN earlier "if not texts:" branch and just logs a
                # note to Winfred. Counting it as SENT overstated real auto sends 28-to-1 in
                # an earlier pass of this report (Opus review, 9 Sep 2026).
                if texts:
                    out.append({"jid": jid, "pn": pn, "kind": "SENT", "rowid": rowid,
                               "type": a.get("type"), "texts": texts})
                else:
                    out.append({"jid": jid, "pn": pn, "kind": "NOTIFY_ONLY", "rowid": rowid,
                               "type": a.get("type")})
        else:
            if E._short_lease_requested(content) and (a or {}).get("type") == "LEASE_NOTE" \
                    and (a or {}).get("reason") == "asked for a lease of 6 months or less":
                out.append({"jid": jid, "pn": pn, "kind": "LEASE_FIRE", "rowid": rowid,
                           "sentence": content})
    return out


def results_fingerprint(all_rows):
    return json.dumps(sorted((r["jid"], r["rowid"], r["kind"], r.get("type"),
                             tuple(r.get("texts") or []), r.get("sentence"), r.get("inbound"))
                            for r in all_rows), sort_keys=True)


def _landlords():
    """Bare pns known to be landlords, fail OPEN to an empty set for replay purposes only
    (mirrors wa_intake_runner.run()'s own fallback -- an unreadable landlord DB there defers
    every new tenant send via excluded_reason's separate fail-closed 'db_error', it does not
    widen who counts as a landlord)."""
    ls = E._landlord_pn_set()
    return ls if ls is not None else frozenset()


def build_report(days):
    landlords = _landlords()
    chats = discover_handtake_chats(days, landlords)
    con = _ro_con()
    try:
        all_rows = []
        for jid in chats:
            rows = fetch_full_chat(jid)
            all_rows.extend(replay_chat(con, jid, rows, days))
    finally:
        con.close()
    return chats, all_rows


def format_report(chats, all_rows, days):
    lines = [f"wa_intake_resume_replay -- {len(chats)} hand takeover chats, "
            f"last {days} days (DRY RUN, no sends, messages.db read only)", ""]
    sent = [r for r in all_rows if r["kind"] == "SENT"]
    drafts = [r for r in all_rows if r["kind"] == "DRAFT"]
    notify_only = [r for r in all_rows if r["kind"] == "NOTIFY_ONLY"]
    fires = [r for r in all_rows if r["kind"] == "LEASE_FIRE"]
    disputed = [r for r in all_rows if r["kind"] == "DISPUTE_BLOCKED"]

    lines.append(f"== RESUME auto sends by type ({len(sent)} total, real prospect facing text) ==")
    by_type = {}
    for r in sent:
        by_type.setdefault(r["type"], []).append(r)
    for t, items in sorted(by_type.items()):
        lines.append(f"  {t}: {len(items)}")
        for r in items:
            for tx in r["texts"]:
                lines.append(f"    [{r['pn']}] {tx!r}")

    lines.append("")
    lines.append(f"== RESUME drafts needed ({len(drafts)} total) ==")
    skip_reasons = {}
    for r in drafts:
        key = r["would_be_type"] or "(nothing sendable)"
        skip_reasons[key] = skip_reasons.get(key, 0) + 1
    for t, n in sorted(skip_reasons.items()):
        lines.append(f"  would have been {t}: {n}")

    lines.append("")
    lines.append(f"== RESUME notify only, no prospect text sent ({len(notify_only)} total) ==")
    notify_by_type = {}
    for r in notify_only:
        notify_by_type[r["type"]] = notify_by_type.get(r["type"], 0) + 1
    for t, n in sorted(notify_by_type.items()):
        lines.append(f"  {t}: {n}")

    lines.append("")
    lines.append(f"== SHORT LEASE auto reply fires ({len(fires)} total) ==")
    for r in fires:
        lines.append(f"  [{r['pn']}] {r['sentence']!r}")

    lines.append("")
    lines.append(f"== B2 DISPUTE language blocked (no auto send, no draft) ({len(disputed)} total) ==")
    for r in disputed:
        lines.append(f"  [{r['pn']}] {r['inbound']!r}")

    return "\n".join(lines)


_CATEGORY_KEYWORDS = [
    ("price_question", ("price", "lower", "nego", "discount", "cheaper", "can you do")),
    ("unit_taken", ("taken already", "gone", "sold", "rented out", "no longer available")),
    ("viewing_time", ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "tomorrow", "today",
                     "am", "pm")),
    ("foreign_student", ("student", "studying", "university", "sim", "ntu", "nus")),
    ("couple", ("couple", "husband", "wife", "boyfriend", "girlfriend", "married")),
    ("complaint", ("angry", "unhappy", "disappointed", "refund", "unacceptable", "terrible",
                  "awful", "scam", "complain", "not happy")),
    ("agent", ("agent", "co-broke", "cobroke", "commission", "propnex", "era ", "huttons",
              "orangetee")),
]


def _classify_category(content):
    t = (content or "").lower()
    for cat, kws in _CATEGORY_KEYWORDS:
        if any(k in t for k in kws):
            return cat
    return "unclear"


def _collect_draft_candidates(days):
    """Every resumed inbound across the window that needs a draft, tagged with a rough
    scenario category so sample_drafts() can pick a MIXED set rather than just the first
    N chats discovered. Same resume_reason_blocked + landlord skip as build_report/replay_chat
    (Opus review, 9 Sep 2026: this used to be a second, drifted reimplementation)."""
    landlords = _landlords()
    chats = discover_handtake_chats(days, landlords)
    con = _ro_con()
    candidates = []
    try:
        for jid in chats:
            rows = fetch_full_chat(jid)
            E.DRY_RUN = True
            state = {"version": 1, "conversations": {}}
            pn = E.resolve_pn(jid)
            for rowid, ifm, content, ts in rows:
                ev = {"jid": jid, "msg_id": str(rowid), "text": content or "",
                      "is_from_me": bool(ifm), "ts": ts}
                ev["listing_key"] = RNR.match_listing(content)
                ev["engine"] = E.is_engine_outbound(content) if ifm else False
                pre_snapshot = None
                if not ifm:
                    rec_before = state["conversations"].get(pn)
                    # B established (review fix): see the mirrored comment above.
                    pre_snapshot = dict(rec_before) if rec_before is not None else {}
                    pre_snapshot["profile"] = dict((rec_before or {}).get("profile") or {})
                    under_takeover = bool(rec_before and (rec_before.get("manual_takeover")
                                                          or rec_before.get("human_takeover")))
                    if not (under_takeover and RES.dispute_language_recent(con, jid)):
                        blocked = RES.resume_reason_blocked(con, "rowid", jid, rec_before, rowid, ts)
                        ev["resume"] = blocked is None
                a = E.handle_event(state, ev)
                _drafting = ev.get("resume") and RES.needs_draft(a, pre_snapshot)
                if _drafting:
                    RES.revert_unsent_form(a, state["conversations"].get(pn, {}), pre_snapshot)
                if _drafting and _in_window(ts, days):
                    candidates.append({"jid": jid, "rowid": rowid, "content": content,
                                       "category": _classify_category(content)})
    finally:
        con.close()
    return candidates


def sample_drafts(n_chats, days, out_path):
    """Generate REAL Haiku drafts for N representative takeover chats and save them with a
    short chat summary. Picks a MIXED set (one per scenario category where available, then
    fills any remaining slots) rather than just the first N chats found. This is the ONLY
    part of this script that spends real Haiku tokens; run() above never calls it."""
    candidates = _collect_draft_candidates(days)
    picked, used_jids, used_cats = [], set(), set()
    for cat, _ in _CATEGORY_KEYWORDS + [("unclear", ())]:
        for c in candidates:
            if len(picked) >= n_chats:
                break
            if c["category"] == cat and c["jid"] not in used_jids:
                picked.append(c); used_jids.add(c["jid"]); used_cats.add(cat)
        if len(picked) >= n_chats:
            break
    for c in candidates:          # fill any remaining slots regardless of category
        if len(picked) >= n_chats:
            break
        if c["jid"] not in used_jids:
            picked.append(c); used_jids.add(c["jid"])

    con = _ro_con()
    idc = "rowid"
    out = ["# Resume draft samples (real Haiku calls)", ""]
    for p in picked:
        pn = E.resolve_pn(p["jid"])
        state = {"version": 1, "conversations": {}}
        rows = fetch_full_chat(p["jid"])
        for rowid, ifm, content, ts in rows:
            ev = {"jid": p["jid"], "msg_id": str(rowid), "text": content or "",
                  "is_from_me": bool(ifm), "ts": ts}
            ev["listing_key"] = RNR.match_listing(content)
            ev["engine"] = E.is_engine_outbound(content) if ifm else False
            E.handle_event(state, ev)
        rec = state["conversations"].get(pn, {})
        listing = E.listing_reqs().get(rec.get("listing_key"))
        transcript = RES.fetch_transcript(con, idc, p["jid"], limit=80)
        prompt = RES.build_prompt(rec.get("profile", {}).get("name") or pn, pn,
                                  rec.get("listing_key"), listing, rec.get("profile", {}),
                                  transcript, p["content"])
        text, err = RES.call_haiku(prompt)
        if err == "timeout":
            # one retry with a longer budget for THIS offline sample generation only -- the
            # live runner's real 25s timeout (RES.HAIKU_TIMEOUT_SEC) is never changed by this.
            _orig_timeout = RES.HAIKU_TIMEOUT_SEC
            RES.HAIKU_TIMEOUT_SEC = 60
            try:
                text, err = RES.call_haiku(prompt)
            finally:
                RES.HAIKU_TIMEOUT_SEC = _orig_timeout
        out.append(f"## {pn} ({rec.get('listing_key') or 'no listing'}) -- {p.get('category', 'unclear')}")
        out.append(f"Last inbound: {p['content']!r}")
        out.append("Chat summary (last 6 lines):")
        for m in transcript[-6:]:
            out.append(f"  {m['who']}: {m['text']}")
        if err:
            out.append(f"HAIKU ERROR: {err}")
        else:
            out.append(f"DRAFT:\n{text}")
        out.append("")
    con.close()
    with open(out_path, "w") as f:
        f.write("\n".join(out))
    print(f"saved {len(picked)} sample drafts to {out_path}")


def main():
    _P.sandbox_init()   # no-op unless WA_INTAKE_SANDBOX=1 (a test); see its own docstring
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default=SCRATCH)
    ap.add_argument("--sample-drafts", type=int, default=0,
                    help="if >0, generate this many REAL Haiku sample drafts instead of "
                         "the dry replay report")
    args = ap.parse_args()

    if args.sample_drafts:
        sample_drafts(args.sample_drafts, args.days,
                      os.path.join(args.out, "resume-drafts-sample.md"))
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    chats1, rows1 = build_report(args.days)
    report1 = format_report(chats1, rows1, args.days)
    print(report1)

    chats2, rows2 = build_report(args.days)
    same = results_fingerprint(rows1) == results_fingerprint(rows2)
    det_line = "\n== determinism check (same inputs replayed twice) ==\n" + \
              ("IDENTICAL" if same else "MISMATCH -- non deterministic replay, investigate")
    print(det_line)

    out_path = os.path.join(args.out, f"resume-replay-{stamp}.txt")
    with open(out_path, "w") as f:
        f.write(report1 + "\n" + det_line + "\n")
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
