"""
wa_intake_runner.py — the live runner that drives intake_engine against the WhatsApp bridge.

Safety model:
  - DRY_RUN (in intake_engine) gates ALL real sends. While True, every intended
    message is written to a preview log and NOTHING is sent.
  - Starts from NOW on first run (does not backfill old threads), so the engine
    only handles NEW enquiries from this point. The backlog is left for manual handling.
  - The old com.crestbrick.wa-pipeline sender must be disabled before this is relied on,
    otherwise two senders run. (Done at install time.)

To go live: set DRY_RUN = False in intake_engine.py, then watch the preview log first.
"""
import os, json, time, sqlite3, re, fcntl, random
import intake_engine as E
import wa_intake_paths as _P
import wa_intake_resume as RES
import wa_intake_selfchat as RESC
import wa_intake_replies as REPLIES2   # category 2: acknowledge and pivot (thin layer on
                                        # top of handle_event's own FLAG_HUMAN/ANSWER_QUESTION,
                                        # never a second sender -- see its own module docstring)
# owner side of the loop (Winfred, 9 Sep 2026): asking/chasing landlords and capturing their
# answers is entirely self contained in these two sibling modules -- see wa_intake_owner.py's
# own docstring for the enqueue_owner_question() hook the category-2 reply layer calls.
import wa_intake_owner as OWNQ
import wa_intake_owner_answers as OWNA
import wa_intake_draft_worker as WORKER
# split out 8 Sep 2026 to keep this file under the repo's 500 line guideline; re-imported
# here so every existing call site (incl. tests reaching them via wa_intake_runner.<name>)
# keeps working unchanged.
from wa_intake_notify import (PREVIEW, WINFRED_CHAT, TG_SEND, NOTIFY_Q, _log, _tg_send,
                              _hot_line, notify_winfred, _drain_notify_queue, _alert_hourly,
                              notify_winfred_coalesced, _flush_stale_coalesce_windows,
                              notify_for_action, _slot_confirm_count, notify_stale_backfill,
                              notify_viewing_slot_needed, notify_unhandled_inbound)
# STEP 0 sandbox seal (9 Sep 2026 merge redo): kept for backward compat with existing
# mock.patch.object(wa_intake_runner, "NAME", ...) tests; _msg_db()/_lockf() resolve them at
# call time (see wa_intake_paths.resolved's docstring).
MSG_DB  = _P.paths()["messages_db"]
_default_MSG_DB = MSG_DB
LOCKF   = _P.paths()["lock_file"]
_default_LOCKF = LOCKF


def _msg_db():
    return _P.resolved(globals(), "MSG_DB", "messages_db")


def _lockf():
    return _P.resolved(globals(), "LOCKF", "lock_file")

# Low level send/guard primitives (_send, _guard_reserve, _write_last, _real_age_hours),
# the PRE-PASS outbound classifier (_prelatch_decision), quiet hours (_quiet_hours,
# QUIET_START_MIN/SEND_START_MIN) and the watermark path (LASTF) live in wa_intake_send.py
# (split out 9 Sep 2026 merge review to keep this file under the repo's 500 line guideline);
# re-imported here so every existing call site (incl. tests that reach them via
# wa_intake_runner.<name>) keeps working unchanged.
from wa_intake_send import (LASTF, BRIDGE, GUARD, QUIET_START_MIN, SEND_START_MIN,
                            _quiet_hours, _rowid_col, _prelatch_decision, _send,
                            _guard_reserve, _write_last, _real_age_hours, _lastf,
                            _BOUNDED_ONCE_FIELD, daily_cap_should_skip,
                            circuit_breaker_gate, record_prospect_send, CIRCUIT_MAX_SENDS)

# match_listing() and its keyword/fallback ranking helpers live in
# wa_intake_listing_match.py (split out 9 Sep 2026 merge review to keep this file under the
# repo's 500 line guideline); re-imported here so every existing call site (incl. tests that
# reach them via wa_intake_runner.<name>) keeps working unchanged.
from wa_intake_listing_match import (_listing_open, _fallback_tokens, _match_pass,
                                     match_listing)

import subprocess

# Echo detection (_is_our_echo/_FILLED_RE/_OUTBOUND_ONLY) and the landlord onboarding action
# type list live in wa_intake_echo.py (split out 8 Sep 2026 to keep this file under the
# repo's 500 line guideline); re-imported here so every existing call site (and every test
# that reaches them via wa_intake_runner.<name>) keeps working unchanged.
from wa_intake_echo import (_FILLED_RE, _OUTBOUND_ONLY, _LANDLORD_ONBOARDING_TYPES,
                            _is_our_echo)
STALE_ROW_HOURS = 48   # backfilled history older than this is skipped (never auto-served)
SEND_MAX_INBOUND_AGE_HOURS = 5 * 24   # never message anyone whose triggering reply is >5 days old
DAILY_SEND_CAP = 2     # max automated touches per client per SGT day (Winfred, 29 Jul 2026)

def run():
    # STEP 0 sandbox seal (9 Sep 2026 merge redo): a no-op unless WA_INTAKE_SANDBOX=1, in
    # which case it refuses to proceed if any resolved path is still inside a real live
    # root -- BEFORE the lock (or anything else) is touched. See wa_intake_paths.sandbox_init.
    _P.sandbox_init()
    # single-instance lock: a slow run (bridge stalls) must not overlap the next 120s tick,
    # or two processes load the same state and double-send.
    lock_fh = open(_lockf(), "a+")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another wa-intake run is in progress — skipping this tick")
        return
    WORKER.reset_tick_budget()             # fresh 2-spawn draft/extract budget for this tick
    _drain_notify_queue()                 # deliver any pings lost to an earlier Telegram outage
    _flush_stale_coalesce_windows()       # send any per-chat notify digest whose window ended
    # Engine integrity pin (9 Sep 2026, merge redo): the working tree IS
    # production, and another session's git reset silently reverted a deploy
    # today. If the bytes on disk stop matching the pinned committed state, hold
    # every send and alarm; a missing pin only logs (first deploy).
    try:
        import wa_intake_pin as _pin
        _ok, _why, _detail = _pin.verify()
        if not _ok and _why != "no_pin":
            print("ENGINE PIN " + _why + " — holding, nothing processed:", json.dumps(_detail)[:300])
            _alert_hourly("pin", "wa-intake: engine files on disk do not match the pinned deploy ("
                          + _why + "). HOLDING all sends until re-pinned. "
                          + json.dumps(_detail)[:200])
            return
        if not _ok:
            print("ENGINE PIN missing — proceeding (run wa_intake_pin.py --pin after the next deploy)")
    except Exception as _e:
        print("ENGINE PIN check error (proceeding):", str(_e)[:120])
    try:
        con = sqlite3.connect(_msg_db(), timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        idc = _rowid_col(con)
    except Exception as e:
        # bridge store locked or missing: skip this tick WITHOUT advancing the watermark —
        # messages are preserved and served when the DB is readable again.
        print(f"messages.db unavailable ({type(e).__name__}) — skipping tick, nothing lost")
        _alert_hourly("msgdb", "wa-intake: messages.db unreadable (" + type(e).__name__ + "). "
                      "Pipeline holding safely; check the WhatsApp bridge if this persists.")
        return
    if _quiet_hours():
        # hold WITHOUT advancing last_ts: overnight enquiries are served after 07:00 SGT
        print("quiet hours (SGT) — holding; overnight enquiries will be served after 07:00")
        return
    try:
        if not os.path.exists(_lastf()):
            # first run: watermark at the newest row, do not backfill old threads
            mx = con.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
            _write_last(mx)
            print("first run: watermark set at rowid", mx, "(no backfill)")
            return
        try:
            wm = json.load(open(_lastf()))
        except (ValueError, OSError):
            # corrupt/half-written watermark -> re-derive at newest rather than crash/backfill
            wm = {"last_rowid": con.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]}
            print("watermark unreadable — re-derived at MAX(rowid)")

        # also pull Winfred's OWN self chat: it never matches '%@lid' but is where /send and
        # /drop draft commands live (takeover resume, Winfred 8 Sep 2026) -- same watermark,
        # same ordering, so it never needs a second cursor.
        if "last_rowid" in wm:
            rows = con.execute(
                f"SELECT rowid, {idc}, chat_jid, is_from_me, content, timestamp, media_type FROM messages "
                f"WHERE rowid > ? AND (chat_jid LIKE '%@lid' OR chat_jid = ?) ORDER BY rowid",
                (wm["last_rowid"], RES.OWN_JID)
            ).fetchall()
        else:
            # ONE-TIME migration from the legacy timestamp watermark. datetime() normalizes
            # the mixed +08:00/-04:00 offsets, so rows the string comparison hid (real leads)
            # are recovered here; the stale-row guard below keeps old history out.
            rows = con.execute(
                f"SELECT rowid, {idc}, chat_jid, is_from_me, content, timestamp, media_type FROM messages "
                f"WHERE datetime(timestamp) > datetime(?) AND (chat_jid LIKE '%@lid' OR chat_jid = ?) "
                f"ORDER BY rowid",
                (wm.get("last_ts", ""), RES.OWN_JID)
            ).fetchall()
            print(f"watermark migration: {len(rows)} rows newer than legacy ts watermark")
    except sqlite3.Error as e:
        # connect succeeded but the store is unusable (locked mid-query, truncated file,
        # missing table after a bridge rebuild): hold this tick, watermark untouched.
        print(f"messages.db unusable ({type(e).__name__}: {e}) — skipping tick, nothing lost")
        _alert_hourly("msgdb", "wa-intake: messages.db unusable (" + type(e).__name__ + "). "
                      "Pipeline holding safely; check the WhatsApp bridge if this persists.")
        return

    try:
        state = E.load_state()
    except E.StateCorrupt as e:
        # NEVER run on a corrupt state: every latch would be lost and the next tick would
        # re-form every past prospect. Hold (watermark unchanged) and alert until fixed.
        print("intake-state.json CORRUPT — holding, nothing processed:", e)
        _alert_hourly("state", "wa-intake: intake-state.json is CORRUPT — the pipeline is "
                      "HOLDING (no sends, no messages lost) until the file is restored. "
                      "Latest backup: intake-state.json.bak-* in the same folder.\n" + str(e)[:200])
        return
    # quiet hours are confirmed over (the return above) -- safe to retry any /send Winfred
    # approved while they were still in effect, still subject to every other /send gate.
    RESC.sweep_approved_drafts(state, send_fn=_send, guard_reserve_fn=_guard_reserve,
                               log_fn=_log, notify_fn=notify_winfred, con=con)
    acted = 0
    # PRE-PASS: if this batch contains a MANUAL outbound reply from Winfred in a chat, latch
    # manual_takeover for that chat BEFORE acting on any of its inbound rows. Without this,
    # a backlog replay (e.g. after quiet hours) processes the prospect's 1am enquiry first
    # and form-blasts someone Winfred already answered by hand at 2am.
    landlords = E._landlord_pn_set()
    landlords_unreadable = landlords is None   # resume auto sends fail CLOSED on this too --
                                                # see RES.resume_gate_blocked at the send choke
    if landlords is None:
        # landlord DB unreadable: the engine fails closed per message (all new form sends
        # defer via db_error), so nothing wrong goes out — but Winfred must know, or new
        # intake sits deferred indefinitely.
        _alert_hourly("landlorddb", "wa-intake: landlord-db.json is unreadable. New tenant "
                      "enquiries are DEFERRED (no forms sent, nothing lost) until the file "
                      "is fixed — the engine cannot verify who is a landlord.")
        landlords = frozenset()
    reqs_tick = E.listing_reqs()   # one disk read per tick, not one per row; feeds the pre pass too
    for _rowid, _mid, _jid, _ifm, _content, _ts, _mtype in rows:
        if not _ifm:
            continue
        if _jid == RES.OWN_JID:
            continue          # self chat command console, never a prospect record
        try:
            _pn = E.resolve_pn(_jid)
            # a landlord chat never becomes a tenant record: latching one here only for
            # the engine to pop it again re-processed the same boundary row every tick
            # (observed: 18 consecutive PRELATCH logs on LL052, 12 Jul 2026).
            if _pn and _pn not in landlords:
                _rec = E._rec(state, _pn)
                # bind the listing from Winfred's hand text OR a sanctioned automation ack
                # (PG auto-ack), whichever comes first in the batch — this can be a LATER
                # row in rowid order than an unbound inbound enquiry earlier in the same
                # tick, so it must run before the main per-row loop below.
                if not _rec.get("listing_key"):
                    _lk = match_listing(_content, reqs_tick)
                    if _lk:
                        _rec["listing_key"] = _lk
                _decision = _prelatch_decision(_content)
                if _decision == "LATCH":
                    if not _rec.get("manual_takeover") or not _rec.get("copilot_muted"):
                        _rec["manual_takeover"] = True
                        _rec["copilot_muted"] = True   # mute BEFORE any inbound row in this batch acts
                        _rec["human_takeover"] = True  # genuine hand reply -- silences landlord onboarding too
                        _rec["last_hand_reply_ts"] = _ts   # takeover resume clock (Winfred, 8 Sep 2026)
                        _log("PRELATCH", _pn, "manual reply found later in batch")
                elif _decision == "FORM_PASTED" and not _rec.get("form_sent"):
                    # a hand paste of the BLANK intake form (Maddie re sending it, word
                    # joiners and all) is engine equivalent -- no latch (is_engine_outbound
                    # already returned True for it) -- but the engine's own send flow never
                    # ran for this row, so form_sent would otherwise stay False and the
                    # engine could independently form blast the same prospect later.
                    _rec["form_sent"] = True
                    _rec["form_sent_ts"] = time.time()
                    _log("FORM_PASTED", _pn, "blank intake form pasted by hand, no latch")
        except Exception as _e:
            _log("PRELATCH_ERR", _jid, f"{type(_e).__name__}: {str(_e)[:100]}")
    last_rowid = wm.get("last_rowid")
    stale_backfill_skipped = 0   # aggregated for ONE notify at the end of the tick, never per row
    stale_backfill_by_pn = {}    # phone -> skipped row count, so the notify can name every chat
    stale_backfill_fields_by_pn = {}   # phone -> sorted field names parsed off a stale row
    for rowid, rid, jid, ifm, content, ts, mtype in rows:
        last_rowid = rowid if (last_rowid is None or rowid > last_rowid) else last_rowid
        # stale backfill guard: the bridge re-syncs reconnect gaps with old-stamped rows.
        # Genuinely old history must never be auto-served as a fresh enquiry -- this only
        # makes the drop VISIBLE (a runner outage used to swallow a backlog with no log line
        # and no flag at all, P3 attack-harness finding 9 Sep 2026); no-auto-serve is unchanged.
        if _real_age_hours(ts) > STALE_ROW_HOURS:
            _pn_stale = E.resolve_pn(jid)
            _log("STALE_BACKFILL_SKIP", jid,
                 f"row {rid} is {_real_age_hours(ts)/24:.1f}d old (>{STALE_ROW_HOURS}h backfill guard); never auto-served")
            # BIND ONLY, never serve or reply from a stale row: a still active thread whose
            # opening enquiry fell outside the backfill window otherwise permanently loses its
            # listing anchor, and the later "possible listings" fallback names unrelated live
            # rooms instead (P2 fix, 9 Sep 2026 cycle5 c5ec02).
            if not ifm and _pn_stale and _pn_stale not in landlords:
                _rec_stale = E._rec(state, _pn_stale)
                if not _rec_stale.get("listing_key"):
                    _lk_stale = match_listing(content, reqs_tick)
                    if _lk_stale:
                        _rec_stale["listing_key"] = _lk_stale
                        _rec_stale["listing_key_source"] = "stale_backfill_guess"
                # PARSE, never send (P1 fix, 9 Sep 2026 cycle5 c5s08): "do not auto-serve"
                # must not also mean "do not learn". A returning prospect whose stale backlog
                # already carried a filled profile used to be treated as brand new -- re-sent
                # the whole opener plus the 14-field form -- purely because this guard threw
                # the text away instead of parsing it. missing_required()/SEND_FORM already
                # know how to skip the blank form once the profile is complete (see
                # test_incomplete_profile_still_gets_the_full_form's sibling test); this just
                # feeds them what a stale row already gave, never overwriting a field the
                # prospect already confirmed some other way.
                _parsed_stale = E.extract_profile(content or "")
                _prof_stale = _rec_stale.setdefault("profile", {})
                _new_fields = [f for f, v in (_parsed_stale or {}).items()
                              if v not in (None, "") and not _prof_stale.get(f)]
                if _new_fields:
                    _prof_stale.update({f: _parsed_stale[f] for f in _new_fields})
                # ONE notify per chat, not once per stale row (P1 fix, same finding: a 21 day
                # old backlog of 5 rows pinged Winfred with the identical generic line 5
                # times) -- latched on the RECORD, so it survives across ticks, not just this
                # loop. Later stale rows for the same chat still parse silently, no re-ping.
                if not _rec_stale.get("stale_backfill_notified"):
                    _rec_stale["stale_backfill_notified"] = True
                    stale_backfill_skipped += 1
                    stale_backfill_by_pn[_pn_stale] = stale_backfill_by_pn.get(_pn_stale, 0) + 1
                    if _new_fields:
                        stale_backfill_fields_by_pn[_pn_stale] = sorted(
                            set(stale_backfill_fields_by_pn.get(_pn_stale, [])) | set(_new_fields))
            continue
        # the bridge echoes some of OUR bot sends with is_from_me=0. Do not treat those as a
        # prospect inbound (they poison the profile / self-trigger sends). A prospect's FILLED
        # form is not an echo and still flows.
        if not ifm and _is_our_echo(content):
            continue
        # per-row guard: a malformed message or transient error must not abort the whole run
        # (which would stall the pipeline and replay rows). Log, skip the row, keep going.
        try:
            # landlord chats are Winfred's to work by hand + the nightly refresh's to record.
            # Skipping them here (not just inside the engine) keeps their rows out of the
            # dedup-less create-then-pop cycle that re-processed the boundary row every tick.
            _pn0 = E.resolve_pn(jid)
            if jid == RES.OWN_JID:
                # Winfred's own self chat: never a prospect record, only a /send or /drop
                # command console for the takeover resume drafts (Winfred, 8 Sep 2026).
                if ifm and (content or "").strip():
                    RESC.handle_self_chat_command(jid, content, send_fn=_send,
                                                  guard_reserve_fn=_guard_reserve, log_fn=_log,
                                                  notify_fn=notify_winfred,
                                                  quiet_hours_fn=_quiet_hours, state=state,
                                                  con=con)
                continue
            if _pn0 and _pn0 in landlords:
                continue
            ev = {"jid": jid, "msg_id": str(rid), "text": content or "", "is_from_me": bool(ifm),
                  "media_type": mtype or "", "ts": ts}
            # run on BOTH directions: an inbound enquiry rarely names the exact listing, but
            # Winfred's own hand reply or a sanctioned automation ack (PG auto-ack) often does.
            ev["listing_key"] = match_listing(content, reqs_tick)
            # a bot-template outbound is OUR send (no takeover); any other outbound = Winfred by hand.
            # STRICT prefix matching: a manual reply that merely contains "still available" must
            # latch takeover, so only exact engine template starts count as engine sends.
            ev["engine"] = E.is_engine_outbound(content) if ifm else False
            # TAKEOVER RESUME (Winfred, 8 Sep 2026): only ever considered for a PROSPECT
            # inbound on a record already under a hand takeover. ev["resume"] tells the
            # engine to run this one inbound through its normal (non manual-takeover) flow;
            # the allow list right below decides whether the result may actually reach a
            # real send or must become a drafted suggestion instead.
            _pre_snapshot = None   # record state BEFORE this inbound is processed
            if not ifm and _pn0:
                _rec0 = state["conversations"].get(_pn0)
                # B established (review fix): a full shallow snapshot, not just form_sent/
                # listing_key -- is_established_prospect() also needs listing_key_source,
                # profile (>= 2 REQUIRED_FIELDS), first_inbound_text and
                # outbound_before_first_inbound, all as they stood BEFORE this inbound.
                # 'profile' gets its OWN copy: handle_event mutates rec['profile'] in place,
                # so a bare dict(_rec0) would let a field THIS inbound just added leak into
                # the "before" snapshot through the shared dict reference.
                _pre_snapshot = dict(_rec0) if _rec0 is not None else {}
                _pre_snapshot["profile"] = dict((_rec0 or {}).get("profile") or {})
                _under_takeover = bool(_rec0 and (_rec0.get("manual_takeover") or _rec0.get("human_takeover")))
                # B2: dispute/legal escalation language anywhere in the last 10 messages ->
                # no auto-send, no draft, ever, for as long as it stays in that window. Flag
                # Winfred once per chat, never again while the record stays disputed.
                if _under_takeover and RES.dispute_language_recent(con, jid):
                    if not _rec0.get("dispute_flagged"):
                        _rec0["dispute_flagged"] = True
                        _log("RESUME_DISPUTE", _pn0, "dispute language in last 10 messages")
                        notify_winfred(f"{_pn0}: dispute language in the last 10 messages of "
                                       f"this chat. The engine will not auto reply or draft "
                                       f"here -- reply by hand.")
                    # ev["resume"] stays unset: handle_event still runs below (its own silent
                    # manual_takeover path), just never resume-eligible while disputed.
                else:
                    if _rec0 is not None:
                        _rec0.pop("dispute_flagged", None)   # aged out of the window
                    _blocked = RES.resume_reason_blocked(con, idc, jid, _rec0, rowid, ts)
                    if _blocked is None:
                        ev["resume"] = True
                    elif _under_takeover:
                        _log("RESUME_SKIP", _pn0, _blocked)
            a = E.handle_event(state, ev)
            # category 2 (acknowledge and pivot): only ever swaps text into a bare
            # FLAG_HUMAN/ANSWER_QUESTION the engine already decided to leave silent, on a
            # confirmed tenant record -- every gate below (resume allow list, takeover skip,
            # cold guard, daily cap) still runs on the SAME action dict exactly as it does
            # for any other engine action.
            a = REPLIES2.augment_action(state, ev, a)
            # SAFETY NET (Winfred, 11 Sep 2026 attack fix package E): a bound or form_sent
            # prospect's inbound that produced neither a prospect send nor a Telegram notify
            # is a true silent dead end -- a bare voice note, a photo/screenshot request
            # answered only by a form, a multi listing ask, a utilities/area or move in date
            # question that fell through every dedicated branch. Never auto answers any of
            # them (notify_unhandled_inbound carries no prospect text at all); just makes
            # sure Winfred hears about it, at most once per chat per 6 hours.
            if not ifm and _pn0:
                _zero_action = not (a and (a.get("text") or a.get("texts")))
                _zero_notify = not (a and a.get("notify"))
                if _zero_action and _zero_notify:
                    notify_unhandled_inbound(state["conversations"].get(_pn0), _pn0,
                                             ev.get("text"))
            if ev.get("resume"):
                if RES.needs_draft(a, _pre_snapshot):
                    _rec_r = state["conversations"].get(_pn0, {})
                    RES.revert_unsent_form(a, _rec_r, _pre_snapshot)
                    _listing_r = reqs_tick.get(_rec_r.get("listing_key"))
                    RES.process_draft_needed(con, idc, jid, _pn0, _rec_r, _listing_r,
                                             notify_winfred, _log)
                    E.save_state(state); acted += 1
                    continue
                # Tag ONLY here, once needs_draft confirms an allow listed action with real
                # text -- the choke point below is the only place allowed to act on this tag
                # (Opus review, 9 Sep 2026: this tag never existed, so the choke below
                # TAKEOVER_SKIP'd every resume send — the auto send half was dead on arrival).
                RES.mark_resume(a)
            if not a:
                continue
            # per-action Telegram ping, built + sent/coalesced by notify_for_action
            # (wa_intake_notify.py; pure move out of this loop, 9 Sep 2026 merge review).
            notify_for_action(a, state)
            # at first enquiry for a listing with no captured viewing slot, ask Winfred for the
            # landlord's availability (once per listing per day, so it never spams).
            if a.get("type") == "SEND_FORM" and a.get("capture_availability"):
                lk2 = a.get("listing_key") or "a listing"
                today = time.strftime("%Y-%m-%d")
                ap = state.setdefault("availability_pinged", {})
                if ap.get(lk2) != today:
                    ap[lk2] = today
                    notify_winfred("New tenant enquiry for " + lk2 + ", but no upcoming viewing "
                        "slot is captured for it. Reply with the landlord's next available viewing "
                        "time so I can offer it to qualified prospects.")
            # a QUALIFIED prospect got the hold line (VIEWING_HOLD_TEXT) instead of a named
            # slot -- ping Winfred once per listing per day (see notify_viewing_slot_needed's
            # own docstring, wa_intake_notify.py).
            notify_viewing_slot_needed(a, state)
            # an action may carry 1 or 2 messages (SEND_FORM = unit info + form, sent once).
            texts = a.get("texts") or ([a["text"]] if a.get("text") else [])
            if not texts:
                _kind = "COPILOT" if a.get("type") == "COPILOT_VERDICT" else "FLAG"
                _log(_kind, a.get("pn"), a.get("type") + " :: " + (a.get("reason","") or a.get("verdict","")))
                E.save_state(state)            # persist per-prospect memory immediately
                acted += 1; continue
            # HARD SEND SAFEGUARDS (29 Jul 2026, after the cold-lead blast): no prospect send
            # unless the person themselves messaged within the last 5 days, and never under
            # manual takeover unless the action is an explicit co-pilot one. These sit at the
            # single send choke point so no upstream regression (watermark reset, state wipe,
            # backfill) can ever re-text a dead lead again.
            _grec = state["conversations"].get(a.get("pn"), {})
            if _real_age_hours(ts) > SEND_MAX_INBOUND_AGE_HOURS:
                _log("STALE_SKIP", a.get("pn"),
                     a.get("type") + f" :: triggering inbound is {_real_age_hours(ts)/24:.1f}d old (>5d rule)")
                E.save_state(state); acted += 1; continue
            # TAKEOVER RESUME AUTO SEND (Winfred, 8-9 Sep 2026): the ONE other manual_takeover
            # bypass, alongside co-pilot and landlord onboarding — decided in one call so it is
            # unit testable without a live tick. Quiet hours already gated the whole tick
            # above; the cold guard and daily cap below still apply to this action as normal.
            _resume_attempted, _resume_why = RES.resume_send_gate(a, _grec, landlords_unreadable)
            if _resume_attempted and _resume_why:
                _log("RESUME_SKIP", a.get("pn"), a.get("type") + " :: " + _resume_why)
                E.save_state(state); acted += 1; continue
            _resume_bypass = _resume_attempted and not _resume_why
            if (_grec.get("manual_takeover") and not a.get("copilot")
                    and a.get("type") not in _LANDLORD_ONBOARDING_TYPES
                    and not _resume_bypass):
                # the whole landlord onboarding sequence is exempt: the supply branch latches
                # takeover BEFORE any of its sends (SEND_SUPPLY_FORM: runner-integration catch
                # c74, 11 Aug 2026; the nudge/media ask/chase for the same reason) -- without
                # this carve-out every one of them is silently suppressed here.
                _log("TAKEOVER_SKIP", a.get("pn"), a.get("type") + " :: manual takeover latched")
                E.save_state(state); acted += 1; continue
            # DAILY CAP: decision + full rationale in wa_intake_send.daily_cap_should_skip;
            # _bound_field stays in scope for the once-a-day stamp after a successful send.
            _today_sgt = time.strftime("%Y-%m-%d",
                         time.gmtime(time.time() + 8 * 3600))
            _bound_field = _BOUNDED_ONCE_FIELD.get(a.get("type"))
            _cap_skip = daily_cap_should_skip(a, _grec, _today_sgt, _bound_field, DAILY_SEND_CAP)
            if _cap_skip is not None:
                _cap_log_suffix, _cap_notify_msg = _cap_skip
                _log("DAILY_CAP_SKIP", a.get("pn"), _cap_log_suffix)
                if _cap_notify_msg:
                    # force notify on every OTHER type the cap still holds back -- a held
                    # reply must never vanish with zero signal to Winfred.
                    notify_winfred_coalesced(a.get("pn"), _cap_notify_msg)
                E.save_state(state); acted += 1; continue
            if E.DRY_RUN:
                for tx in texts:
                    _log("WOULD_SEND", a.get("pn"), a.get("type") + " :: " + tx.replace("\n"," / "))
            elif not _guard_reserve(jid):
                # atomic reserve failed: another sender (or our own just-completed send) holds
                # this person inside the shared cooldown — never double-send. State still advances.
                _log("GUARD_SKIP", a.get("pn"), a.get("type"))
            elif (_cb_gate := circuit_breaker_gate(a.get("type"))) is not None:
                # GLOBAL circuit breaker (last line of defense, separate from the per-client
                # cap above and the owner loop's own per-landlord cap) -- see
                # wa_intake_send.circuit_breaker_gate for the decision + notify latch.
                _log("CIRCUIT_OPEN", a.get("pn"), _cb_gate[0])
                if _cb_gate[1]:
                    notify_winfred(_cb_gate[1])
            else:
                # reserve() already marked the send under one lock (no separate mark needed)
                # PARTIAL-SEND RESUME: a 2-message action (unit info + form) whose second send
                # failed must not re-send the first on retry — skip the texts already delivered.
                _r0 = state["conversations"].get(a.get("pn"), {})
                start_i = int(_r0.get("partial_sent") or 0) if a.get("type") == "SEND_FORM" else 0
                start_i = min(start_i, len(texts))
                allok = True
                sent_n = start_i
                for tx in texts[start_i:]:     # form_sent is already marked, so this fires once only
                    ok = _send(jid, tx)        # send to the chat handle we received from
                    if not ok:                 # one immediate retry: most bridge hiccups are momentary
                        time.sleep(2)
                        ok = _send(jid, tx)
                    _log("SENT" if ok else "SEND_FAIL", a.get("pn"), a.get("type"))
                    if ok:
                        sent_n += 1
                        record_prospect_send()   # count toward the global circuit breaker
                    else:
                        allok = False
                        break                  # do not attempt later texts out of order
                if not allok:
                    # bridge failed: roll back the once-only latch so the next tick retries
                    # (resuming AFTER any texts already delivered), and tell Winfred — a
                    # silently lost form/offer is a permanently lost lead.
                    try:
                        _r = state["conversations"].get(a.get("pn"), {})
                        if a.get("type") == "SEND_FORM":
                            _r["form_sent"] = False
                            _r["partial_sent"] = sent_n
                        if a.get("type") in ("OFFER_VIEWING", "VIEWING_ASK"): _r["viewing_asked"] = False
                        notify_winfred(f"WA send FAILED to {a.get('pn')} ({a.get('type')}). "
                                       f"Latch rolled back so the engine retries next tick; check the bridge if this repeats.")
                    except Exception:
                        pass
                else:
                    _r0.pop("partial_sent", None)
                    if a.get("resume"):
                        # logged only here, after delivery — it used to log BEFORE the choke
                        # point and print even when TAKEOVER_SKIP then silently ate the send.
                        _log("RESUME_SENT", a.get("pn"),
                             a.get("type") + " :: " + " / ".join(texts).replace("\n", " / "))
                    # count this touch against the per-client daily cap (SGT day)
                    _rc = state["conversations"].get(a.get("pn"))
                    if _rc is not None:
                        if _rc.get("sends_today_date") != _today_sgt:
                            _rc["sends_today_date"] = _today_sgt
                            _rc["sends_today"] = 0
                        _rc["sends_today"] = int(_rc.get("sends_today") or 0) + 1
                        # latch the once-a-day bound for VIEWING_TIME_PROPOSED / ASK_TENANT_TIME
                        # / REDIRECT / LEASE_NOTE above, separate from the ordinary cap counter
                        if _bound_field:
                            _rc[_bound_field] = _today_sgt
                # throttle: drip the morning backlog instead of a bot-like instant burst
                time.sleep(random.uniform(4, 9))
                if allok and a.get("type") == "CONFIRM_VIEWING" and a.get("slot_id"):
                    try:
                        E.book_slot(state["conversations"][a["pn"]].get("listing_key"), a["slot_id"])
                    except Exception as be:
                        _log("BOOK_FAIL", a.get("pn"), str(be))   # malformed avail file must not abort the loop
            # persist the prospect's memory after every action so a crash never re-sends a message
            E.save_state(state)
            acted += 1
        except Exception as e:
            _log("ERROR", jid, f"{type(e).__name__}: {str(e)[:140]}")
            try:
                pn = E.resolve_pn(jid)
                if pn:   # mark this msg_id processed so the boundary row is not retried forever.
                    rec = E._rec(state, pn)   # route through _rec so the record is shape-complete
                    if str(rid) not in rec["processed_ids"]:
                        rec["processed_ids"].append(str(rid))
                E.save_state(state)
            except Exception:
                pass
            continue

    E.save_state(state)
    notify_stale_backfill(stale_backfill_skipped, stale_backfill_by_pn, STALE_ROW_HOURS,
                          stale_backfill_fields_by_pn)
    # owner side of the loop -- entirely separate from the tenant watermark above, and
    # wrapped so any failure here can never block the tenant pipeline's own progress.
    try:
        OWNQ.run_owner_asks(con, send_fn=_send, guard_reserve_fn=_guard_reserve,
                            log_fn=_log, notify_fn=notify_winfred)
        OWNQ.run_owner_chases(con, send_fn=_send, guard_reserve_fn=_guard_reserve,
                              log_fn=_log, notify_fn=notify_winfred)
        OWNA.spawn_owner_answer_extracts(con, log_fn=_log)
    except Exception as e:
        _log("OWNER_LOOP_ERROR", "-", f"{type(e).__name__}: {str(e)[:140]}")
    # Background draft/extract sweep (item 3): collects a PRIOR tick's finished/timed out
    # resume draft or owner extract; never blocks on one still within its wall budget.
    try:
        OWNA.sweep_all_drafts(notify_winfred, _log)
    except Exception as e:
        _log("DRAFT_WORKER_SWEEP_ERROR", "-", f"{type(e).__name__}: {str(e)[:140]}")
    if last_rowid is None:
        # legacy-watermark migration tick with zero newer rows: everything on disk is
        # older than the old watermark, so pin at the newest row and move on.
        last_rowid = con.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
    _write_last(last_rowid)
    print(f"processed {len(rows)} new messages, {acted} engine actions, DRY_RUN={E.DRY_RUN}")

if __name__ == "__main__":
    run()
