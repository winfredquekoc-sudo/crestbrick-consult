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
import wa_intake_resume as RES
import wa_intake_selfchat as RESC
# split out 8 Sep 2026 to keep this file under the repo's 500 line guideline; re-imported
# here so every existing call site (incl. tests reaching them via wa_intake_runner.<name>)
# keeps working unchanged.
from wa_intake_notify import (PREVIEW, WINFRED_CHAT, TG_SEND, NOTIFY_Q, _log, _tg_send,
                              _hot_line, notify_winfred, _drain_notify_queue, _alert_hourly)

MSG_DB  = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
LASTF   = os.path.expanduser("~/.claude/state/listing-templates/runner-last.json")
LOCKF   = os.path.expanduser("~/.claude/state/listing-templates/.wa-intake.lock")
BRIDGE  = "http://localhost:8080/api/send"

# Quiet hours: stay live, but never message prospects overnight. Outside this window the
# runner holds and does NOT advance its cursor, so enquiries that arrive at night are
# preserved and served together the next morning (no 3am pings to clients).
QUIET_START_MIN = 2 * 60       # quiet hours 02:00–07:00 SGT (Winfred, 8 Sep 2026;
SEND_START_MIN  = 7 * 60       # was 01:00–07:00). Messaging runs 07:00 through 02:00.

def _quiet_hours():
    import datetime
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    mins = now.hour * 60 + now.minute
    return QUIET_START_MIN <= mins < SEND_START_MIN

def _rowid_col(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()]
    return "id" if "id" in cols else "rowid"

def _slot_confirm_count(state, slot_id):
    """How many conversations have CONFIRMED this exact slot (incl. the one just confirmed)."""
    if not slot_id: return 1
    return sum(1 for r in (state.get("conversations") or {}).values()
               if r.get("viewing_confirmed") and r.get("offered_slot_id") == slot_id) or 1

def _prelatch_decision(content):
    """Pure classification for a PRE-PASS outbound row (no state, no I/O -- unit testable
    in isolation): 'LATCH' (a genuine hand reply -- latch manual_takeover), 'FORM_PASTED'
    (a hand paste of the blank intake form -- engine equivalent, stamp form_sent instead
    of latching), or None (a normal engine template send, already accounted for)."""
    if not E.is_engine_outbound(content):
        return "LATCH"
    if E.is_pasted_blank_intake_form(content):
        return "FORM_PASTED"
    return None

def _listing_open(l):
    st = str((l or {}).get("status", "")).lower()
    return not (st.startswith("closed") or st == "hold")

def match_listing(text, reqs=None):
    """A4 (Sep 2026): a stale keyword can survive on a CLOSED index row that also matches a
    live OPEN one (the review found "ang mo kio ave 3" on both) -- OPEN listings are always
    matched first, in TWO passes, so match order never depends on dict iteration order.
    A CLOSED listing is only ever returned when nothing OPEN matches."""
    t = (text or "").lower()
    listings = list((reqs if reqs is not None else E.listing_reqs()).values())
    for l in listings:
        if not _listing_open(l):
            continue
        for kw in (l.get("pg_url_keywords") or []):
            if kw and kw.lower() in t:
                return l["listing_key"]
    for l in listings:
        if _listing_open(l):
            continue
        for kw in (l.get("pg_url_keywords") or []):
            if kw and kw.lower() in t:
                return l["listing_key"]
    return None

import subprocess
GUARD = os.path.expanduser("~/crestbrick-consult/scripts/wa_send_guard.py")

def _send(pn, text):
    """Real send via the bridge. Only called when not DRY_RUN. ANY bridge error is a
    failed send (returns False), never an exception — an exception here would propagate
    out of run() before state+watermark persist and replay the row, re-sending the form
    every 120s during a bridge hiccup."""
    import requests
    try:
        r = requests.post(BRIDGE, json={"recipient": pn, "message": text}, timeout=10)
        return r.ok
    except Exception:
        return False

def _guard_reserve(jid):
    """Atomically check-and-reserve on the shared cross-sender guard (single flock, no
    TOCTOU). Returns True if reserved (caller should send), False if another sender already
    has this person inside the cooldown. Fail-open if the guard binary is unavailable —
    the engine's form_sent flag remains the primary per-person gate."""
    try:
        return subprocess.run(["python3", GUARD, "reserve", jid, "wa-intake"],
                              capture_output=True, text=True).returncode == 0
    except Exception:
        return True

# Echo detection (_is_our_echo/_FILLED_RE/_OUTBOUND_ONLY) and the landlord onboarding action
# type list live in wa_intake_echo.py (split out 8 Sep 2026 to keep this file under the
# repo's 500 line guideline); re-imported here so every existing call site (and every test
# that reaches them via wa_intake_runner.<name>) keeps working unchanged.
from wa_intake_echo import (_FILLED_RE, _OUTBOUND_ONLY, _LANDLORD_ONBOARDING_TYPES,
                            _is_our_echo)

def _write_last(rowid):
    """Atomic watermark write (tmp + os.replace) so a crash mid-write cannot brick the runner.
    The watermark is the messages.db ROWID, not a timestamp: the bridge writes timestamps in
    the Mac's CURRENT timezone offset (+08:00 rows and -04:00 rows coexist after travel), and
    string-compared mixed offsets silently hid a real Bayshore enquiry on 12 Jul 2026. It also
    backfills reconnect gaps with old-stamped rows BEHIND a timestamp watermark. Insertion
    order (rowid) is immune to both."""
    tmp = LASTF + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"last_rowid": rowid}, f)
    os.replace(tmp, LASTF)

def _real_age_hours(ts):
    """Hours since a bridge timestamp, honouring its embedded offset. Unparseable -> 0
    (treat as fresh: better to process a weird row than silently drop a lead)."""
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 0

STALE_ROW_HOURS = 48   # backfilled history older than this is skipped (never auto-served)
SEND_MAX_INBOUND_AGE_HOURS = 5 * 24   # never message anyone whose triggering reply is >5 days old
DAILY_SEND_CAP = 2     # max automated touches per client per SGT day (Winfred, 29 Jul 2026)

def run():
    # single-instance lock: a slow run (bridge stalls) must not overlap the next 120s tick,
    # or two processes load the same state and double-send.
    lock_fh = open(LOCKF, "a+")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another wa-intake run is in progress — skipping this tick")
        return
    _drain_notify_queue()                 # deliver any pings lost to an earlier Telegram outage
    try:
        con = sqlite3.connect(MSG_DB, timeout=30)
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
        if not os.path.exists(LASTF):
            # first run: watermark at the newest row, do not backfill old threads
            mx = con.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
            _write_last(mx)
            print("first run: watermark set at rowid", mx, "(no backfill)")
            return
        try:
            wm = json.load(open(LASTF))
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
    for rowid, rid, jid, ifm, content, ts, mtype in rows:
        last_rowid = rowid if (last_rowid is None or rowid > last_rowid) else last_rowid
        # stale backfill guard: the bridge re-syncs reconnect gaps with old-stamped rows.
        # Genuinely old history must never be auto-served as a fresh enquiry -- this only
        # makes the drop VISIBLE (a runner outage used to swallow a backlog with no log line
        # and no flag at all, P3 attack-harness finding 9 Sep 2026); no-auto-serve is unchanged.
        if _real_age_hours(ts) > STALE_ROW_HOURS:
            stale_backfill_skipped += 1
            _log("STALE_BACKFILL_SKIP", jid,
                 f"row {rid} is {_real_age_hours(ts)/24:.1f}d old (>{STALE_ROW_HOURS}h backfill guard); never auto-served")
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
            # the ONLY thing Winfred is pinged about: a prospect giving a date/time to view.
            if a.get("notify"):
                rec = state["conversations"].get(a["pn"], {})
                nm = rec.get("profile",{}).get("name") or a["pn"]
                lk = rec.get("listing_key") or "a listing"
                if a["type"] == "SEND_BUYER_FORM":
                    _pt = a.get("property_type")
                    _fin = "HFE" if _pt == "hdb" else "IPA" if _pt == "private" else "HFE/IPA"
                    notify_winfred(f"Buyer enquiry — sent the buyer intake form.\n{nm} ({a['pn']}) looks like a {_pt or 'unknown-type'} buyer, so I sent the buyer form (asks {_fin}). Take over by hand if you want.")
                elif a["type"] == "VIEWING_TIME_PROPOSED":
                    notify_winfred(f"Viewing time from a prospect.\n{nm} ({a['pn']}) for {lk} said:\n{a.get('when','')}\nReply to them to confirm.")
                elif a["type"] == "CONFIRM_VIEWING":
                    # fixed slots have no capacity file entry, so nothing ever caps them —
                    # tell Winfred how full the slot is getting (26 Jul 2026)
                    _n = _slot_confirm_count(state, a.get("slot_id"))
                    _crowd = f" This is confirmation #{_n} for this slot." if _n > 1 else ""
                    _warn = " Slot is getting crowded — consider pointing new confirmations to next week." if _n >= 4 else ""
                    notify_winfred(f"Prospect confirmed a viewing.\n{nm} ({a['pn']}) accepted the slot for {lk}.{_crowd}{_warn}")
                elif a["type"] == "ANSWER_QUESTION":
                    notify_winfred(f"Prospect question (reply by hand).\n{nm} ({a['pn']}) for {lk} asked:\n{a.get('question','')}")
                elif a["type"] == "COPILOT_VERDICT":
                    # Winfred is handling this chat by hand; the engine stays silent to the prospect
                    # but tells HIM the screening result so a qualified tenant is never missed.
                    v = a.get("verdict"); why = a.get("why") or []
                    if v == "QUALIFIED":
                        notify_winfred(f"Co-pilot (you are handling this chat):\n{nm} ({a['pn']}) is QUALIFIED for {lk}. Full profile in, fits the landlord's criteria. Worth offering a viewing.{_hot_line(a)}")
                    elif v == "NEEDS_INFO":
                        notify_winfred(f"Co-pilot (you are handling this chat):\n{nm} ({a['pn']}) for {lk} is almost there. Still unclear: {'; '.join(why)}.{_hot_line(a)}")
                    elif v == "DISQUALIFIED":
                        notify_winfred(f"Co-pilot (you are handling this chat):\n{nm} ({a['pn']}) does NOT fit {lk}. Reason: {'; '.join(why)}.{_hot_line(a)}")
                elif a["type"] == "OFFER_VIEWING" and a.get("copilot"):
                    notify_winfred(f"Co-pilot offered a viewing (you are handling this chat):\n{nm} ({a['pn']}) is QUALIFIED for {lk}, so I sent them the next slot and asked them to reply YES. Step in if you want to take it from here.{_hot_line(a)}")
                elif a["type"] == "OFFER_VIEWING" and a.get("hot_matches"):
                    # the auto-offer itself needs no ping, but a fresh tenant who fits OTHER
                    # live rooms too is a hot lead Winfred should hear about within a tick,
                    # not at the next 3-hourly batch refresh
                    notify_winfred(f"Hot prospect: {nm} ({a['pn']}) qualified for {lk} (viewing slot offered automatically).{_hot_line(a)}")
                elif a["type"] == "SUGGEST_ALT":
                    notify_winfred(f"Cross sell: {nm} ({a['pn']}) rejected the unit, so I suggested {a.get('listing_key')} (same district) with its post and next slot. Conversation rebound to the new listing.")
                elif a["type"] == "CAP_REACHED":
                    notify_winfred(f"Auto-message cap ({E.MAX_PROSPECT_MSGS}) reached for {nm} ({a['pn']}) on {lk}. The bot will stop messaging them now — take over by hand if you want to keep going.")
                elif a["type"] == "AUTO_CLOSED":
                    notify_winfred(f"Auto-closed a prospect.\n{nm} ({a['pn']}) for {lk} said:\n\"{a.get('quote','')}\"\nI marked them closed (found elsewhere); the bot will not message them again. Reopen by hand if that's wrong.")
                elif a["type"] == "SEND_SUPPLY_FORM":
                    _sk = a.get("supply", "landlord")
                    notify_winfred(f"New {_sk} detected: {nm} ({a['pn']}). I sent them the "
                                   f"{'landlord onboarding' if _sk == 'landlord' else 'seller intake'} form and the bot "
                                   f"goes silent on this chat — their answers are yours to work "
                                   f"(nightly refresh will capture landlord details).")
                elif a["type"] == "BUYER_COMPLETE":
                    notify_winfred(f"Buyer profile complete — take over now (nothing was sent to them).\n"
                                   f"{nm} ({a['pn']}): {a.get('summary','')}")
                elif a["type"] in ("SUPPLY_INFO_NUDGE", "SUPPLY_MEDIA_ASK", "SUPPLY_MEDIA_CHASE"):
                    notify_winfred(f"Landlord onboarding — {a['type']}. {nm} ({a['pn']}): {a.get('reason','')}")
                elif a.get("notify"):
                    notify_winfred(f"{a['type']}: {nm} ({a['pn']}) on {lk} — {a.get('reason','')}")
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
            # DAILY CAP: at most DAILY_SEND_CAP automated touches per client per SGT day
            # (a touch = one engine action; SEND_FORM's unit-info + form pair counts as one).
            # CONFIRM_VIEWING is exempt — it answers a tenant's explicit YES to a slot;
            # holding it overnight dead-ends a converting lead, which is not spam.
            _today_sgt = time.strftime("%Y-%m-%d",
                         time.gmtime(time.time() + 8 * 3600))
            # ASK_ONE and OFFER_VIEWING are direct replies to a prospect's own message in
            # the booking flow — the viewing-first happy path is 3 touches, and capping it
            # at 2 dropped the offer right after a YES (adversarial-review P2-8)
            if a.get("type") not in ("CONFIRM_VIEWING", "OFFER_VIEWING", "ASK_ONE"):
                if (_grec.get("sends_today_date") == _today_sgt
                        and int(_grec.get("sends_today") or 0) >= DAILY_SEND_CAP):
                    _log("DAILY_CAP_SKIP", a.get("pn"),
                         a.get("type") + f" :: already {DAILY_SEND_CAP} touches today")
                    E.save_state(state); acted += 1; continue
            if E.DRY_RUN:
                for tx in texts:
                    _log("WOULD_SEND", a.get("pn"), a.get("type") + " :: " + tx.replace("\n"," / "))
            elif not _guard_reserve(jid):
                # atomic reserve failed: another sender (or our own just-completed send) holds
                # this person inside the shared cooldown — never double-send. State still advances.
                _log("GUARD_SKIP", a.get("pn"), a.get("type"))
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
    if stale_backfill_skipped:
        # one aggregated ping per run, never one per row -- a reconnect backfill can carry
        # dozens of stale rows in a single tick.
        notify_winfred(f"{stale_backfill_skipped} backfilled chat message(s) were older than "
                       f"{STALE_ROW_HOURS}h this run and were skipped (never auto-served); "
                       "check the affected chats by hand if any were real.")
    if last_rowid is None:
        # legacy-watermark migration tick with zero newer rows: everything on disk is
        # older than the old watermark, so pin at the newest row and move on.
        last_rowid = con.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
    _write_last(last_rowid)
    print(f"processed {len(rows)} new messages, {acted} engine actions, DRY_RUN={E.DRY_RUN}")

if __name__ == "__main__":
    run()
