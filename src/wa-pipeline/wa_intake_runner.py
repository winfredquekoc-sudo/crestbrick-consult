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

MSG_DB  = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
LASTF   = os.path.expanduser("~/.claude/state/listing-templates/runner-last.json")
PREVIEW = os.path.expanduser("~/.claude/state/listing-templates/dry-run-preview.log")
BRIDGE  = "http://localhost:8080/api/send"

# Quiet hours: stay live, but never message prospects overnight. Outside this window the
# runner holds and does NOT advance its cursor, so enquiries that arrive at night are
# preserved and served together the next morning (no 3am pings to clients).
QUIET_START_MIN = 1 * 60       # quiet hours 01:00–07:00 SGT (Winfred, 13 Jul 2026;
SEND_START_MIN  = 7 * 60       # was 00:00–07:30). Messaging runs 07:00 through 01:00.

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

def match_listing(text, reqs=None):
    t = (text or "").lower()
    for l in (reqs if reqs is not None else E.listing_reqs()).values():
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

# Filled-profile detection: a prospect's COMPLETED form contains values after the labels,
# unlike the blank form the bot sends (which the bridge sometimes echoes as is_from_me=0).
_FILLED_RE = re.compile(r"(name|nationality|ethnicity|gender|age|pass|budget|occupation)\s*[:：][^\S\n]*\S", re.I)
# Phrases ONLY the bot ever sends — a prospect would never type these.
_OUTBOUND_ONLY = (
    "your viewing is confirmed", "you fit what the landlord", "the next viewing is",
    "i will send your profile", "good news, there is a viewing", "i will hold that slot",
    "reply yes to take this slot", "profile does not match", "✅ suits", "📲 more listings",
    "following up on your rental enquiry", "let me confirm that slot with the owner",
    # full nudge prefixes, NOT the bare phrase "almost there" — that is exactly what a
    # prospect texts when they are on the way to a viewing, and it must not be dropped.
    "almost there :) to send your profile", "almost there :) i still need",
    "could you confirm this so i can send your profile",
    "when are you able to view", "by sharing these details you agree",
    # viewing-first texts (11 Aug 2026) — echoed engine sends must never read as inbound
    "keen to view? i can put you in", "are you free to view on", "i can arrange for viewing",
    "to confirm your viewing slot with the landlord",
    "can i just check your", "just need your profile above", "ok can, your viewing is on",
    "what time will you be coming? i will keep", "see you then, i will send the unit number",
    "on your question, let me check with the owner", "viewing slot:",
    "no worries, which day and time would work better",
    "more rooms available on my rental channel",
    # landlord onboarding extension (never mistake our own send for a landlord reply)
    "almost there, i just need",
    "thanks, that is everything i need for now",
    "just checking in, still keen to send a few photos",
)

# Landlord onboarding action types: manual_takeover is latched the moment supply side is
# detected (to keep the record out of the tenant/buyer flows), so every send this sequence
# makes needs the SAME carve out SEND_SUPPLY_FORM already had (runner-integration catch c74,
# 11 Aug 2026). Module level (not inline in run()) so it is inspectable without a live tick.
_LANDLORD_ONBOARDING_TYPES = ("SEND_SUPPLY_FORM", "SUPPLY_INFO_NUDGE",
                              "SUPPLY_MEDIA_ASK", "SUPPLY_MEDIA_CHASE")

def _is_our_echo(content):
    """True when a is_from_me=0 row is actually our OWN bot message echoed back by the
    bridge (it stores some bot sends with is_from_me=0). Such a row must NOT be treated as a
    prospect inbound — otherwise it poisons the profile (extract_profile on the blank form)
    or self-triggers ANSWER_QUESTION/CONFIRM_VIEWING. A prospect's FILLED form (same 'fill
    this in' text but WITH values) is NOT an echo and must still be processed."""
    t = (content or "").lower()
    if not t:
        return False
    # bot-only markers (incl. the unit-info message 1, which carries "✅ suits" / "📲 more
    # listings" / "available viewing"). These are phrases a prospect never types — unlike the
    # ambiguous "still available", which a prospect DOES say, so we must NOT match on that.
    if any(s in t for s in _OUTBOUND_ONLY):
        return True
    # the blank intake form echoed back: contains the prompt but no filled-in values
    if "fill this in" in t and not _FILLED_RE.search(content or ""):
        return True
    return False

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
    lock_fh = open(os.path.expanduser("~/.claude/state/listing-templates/.wa-intake.lock"), "a+")
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

        if "last_rowid" in wm:
            rows = con.execute(
                f"SELECT rowid, {idc}, chat_jid, is_from_me, content, timestamp, media_type FROM messages "
                f"WHERE rowid > ? AND chat_jid LIKE '%@lid' ORDER BY rowid", (wm["last_rowid"],)
            ).fetchall()
        else:
            # ONE-TIME migration from the legacy timestamp watermark. datetime() normalizes
            # the mixed +08:00/-04:00 offsets, so rows the string comparison hid (real leads)
            # are recovered here; the stale-row guard below keeps old history out.
            rows = con.execute(
                f"SELECT rowid, {idc}, chat_jid, is_from_me, content, timestamp, media_type FROM messages "
                f"WHERE datetime(timestamp) > datetime(?) AND chat_jid LIKE '%@lid' ORDER BY rowid",
                (wm.get("last_ts", ""),)
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
    acted = 0
    # PRE-PASS: if this batch contains a MANUAL outbound reply from Winfred in a chat, latch
    # manual_takeover for that chat BEFORE acting on any of its inbound rows. Without this,
    # a backlog replay (e.g. after quiet hours) processes the prospect's 1am enquiry first
    # and form-blasts someone Winfred already answered by hand at 2am.
    landlords = E._landlord_pn_set()
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
                if not E.is_engine_outbound(_content):
                    if not _rec.get("manual_takeover") or not _rec.get("copilot_muted"):
                        _rec["manual_takeover"] = True
                        _rec["copilot_muted"] = True   # mute BEFORE any inbound row in this batch acts
                        _rec["human_takeover"] = True  # genuine hand reply -- silences landlord onboarding too
                        _log("PRELATCH", _pn, "manual reply found later in batch")
        except Exception as _e:
            _log("PRELATCH_ERR", _jid, f"{type(_e).__name__}: {str(_e)[:100]}")
    last_rowid = wm.get("last_rowid")
    for rowid, rid, jid, ifm, content, ts, mtype in rows:
        last_rowid = rowid if (last_rowid is None or rowid > last_rowid) else last_rowid
        # stale backfill guard: the bridge re-syncs reconnect gaps with old-stamped rows.
        # Genuinely old history must never be auto-served as a fresh enquiry.
        if _real_age_hours(ts) > STALE_ROW_HOURS:
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
            if _pn0 and _pn0 in landlords:
                continue
            ev = {"jid": jid, "msg_id": str(rid), "text": content or "", "is_from_me": bool(ifm),
                  "media_type": mtype or ""}
            # run on BOTH directions: an inbound enquiry rarely names the exact listing, but
            # Winfred's own hand reply or a sanctioned automation ack (PG auto-ack) often does.
            ev["listing_key"] = match_listing(content, reqs_tick)
            # a bot-template outbound is OUR send (no takeover); any other outbound = Winfred by hand.
            # STRICT prefix matching: a manual reply that merely contains "still available" must
            # latch takeover, so only exact engine template starts count as engine sends.
            ev["engine"] = E.is_engine_outbound(content) if ifm else False
            a = E.handle_event(state, ev)
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
            if (_grec.get("manual_takeover") and not a.get("copilot")
                    and a.get("type") not in _LANDLORD_ONBOARDING_TYPES):
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
    if last_rowid is None:
        # legacy-watermark migration tick with zero newer rows: everything on disk is
        # older than the old watermark, so pin at the newest row and move on.
        last_rowid = con.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
    _write_last(last_rowid)
    print(f"processed {len(rows)} new messages, {acted} engine actions, DRY_RUN={E.DRY_RUN}")

WINFRED_CHAT = "540127870"
TG_SEND = os.path.expanduser("~/.claude/bin/telegram_send.sh")
NOTIFY_Q = os.path.expanduser("~/.claude/state/listing-templates/notify-queue.json")

def _tg_send(msg):
    """One Telegram send attempt. True only on a confirmed delivery (script exit 0 AND the
    API replied ok:true) — curl reaching Telegram but the API rejecting still counts failed."""
    try:
        r = subprocess.run(["bash", TG_SEND, WINFRED_CHAT], input=msg, text=True,
                           timeout=15, capture_output=True)
        return r.returncode == 0 and '"ok":true' in (r.stdout or "")
    except Exception:
        return False

def _hot_line(a):
    """One extra Telegram line when the engine's cross-listing screen found other
    live rooms this profile qualifies for (see intake_engine.hot_matches)."""
    hm = a.get("hot_matches") or []
    return ("\n🔥 Also fits: " + ", ".join(hm)) if hm else ""

def notify_winfred(msg):
    """Telegram ping to Winfred. Fires even in DRY_RUN (it is a note to him, not a prospect
    send). A FAILED ping is queued and retried at the start of every later run: a viewing
    confirmation or takeover flag must never be silently lost to a Telegram outage."""
    if _tg_send(msg):
        return
    _log("TG_FAIL", WINFRED_CHAT, "queued for retry :: " + msg[:80].replace("\n", " / "))
    try:
        q = json.load(open(NOTIFY_Q)) if os.path.exists(NOTIFY_Q) else []
        q = (q if isinstance(q, list) else []) + [{"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "msg": msg}]
        q = q[-50:]
        tmp = NOTIFY_Q + ".tmp"
        json.dump(q, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, NOTIFY_Q)
    except Exception as e:
        _log("TG_QUEUE_FAIL", WINFRED_CHAT, str(e)[:100])

def _drain_notify_queue():
    """Re-attempt queued Telegram pings from earlier outages. Failures stay queued (cap 50)."""
    try:
        if not os.path.exists(NOTIFY_Q):
            return
        q = json.load(open(NOTIFY_Q))
        if not isinstance(q, list):
            raise ValueError("queue not a list")
    except Exception:
        try: os.remove(NOTIFY_Q)          # unreadable queue: drop it rather than crash every tick
        except OSError: pass
        return
    left = [it for it in q if it.get("msg") and not _tg_send("(delayed from " + str(it.get("ts")) + ")\n" + it["msg"])]
    try:
        if left:
            tmp = NOTIFY_Q + ".tmp"
            json.dump(left[-50:], open(tmp, "w"), ensure_ascii=False, indent=1)
            os.replace(tmp, NOTIFY_Q)
        else:
            os.remove(NOTIFY_Q)
    except OSError:
        pass

def _alert_hourly(key, msg):
    """notify_winfred, rate-limited to once per hour per key (for persistent conditions
    like a corrupt file, which would otherwise ping every 60s tick)."""
    mark = os.path.expanduser("~/.claude/state/listing-templates/.alert-" + key)
    try:
        if os.path.exists(mark) and time.time() - os.path.getmtime(mark) < 3600:
            return
        open(mark, "w").write(str(time.time()))
    except OSError:
        pass
    notify_winfred(msg)

def _log(kind, pn, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {kind} | {pn} | {msg}\n"
    with open(PREVIEW, "a") as f: f.write(line)

if __name__ == "__main__":
    run()
