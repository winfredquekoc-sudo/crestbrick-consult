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
SEND_START_MIN = 7 * 60 + 30   # start messaging at 07:30 SGT
SEND_END_MIN   = 24 * 60       # run through to midnight; hold 00:00–07:30 SGT

def _quiet_hours():
    import datetime
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    mins = now.hour * 60 + now.minute
    return mins < SEND_START_MIN or mins >= SEND_END_MIN

def _rowid_col(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()]
    return "id" if "id" in cols else "rowid"

def match_listing(text):
    t = (text or "").lower()
    for l in E.listing_reqs().values():
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
    "almost there", "when are you able to view", "by sharing these details you agree",
)

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

def _write_last(ts):
    """Atomic watermark write (tmp + os.replace) so a crash mid-write cannot brick the runner."""
    tmp = LASTF + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"last_ts": ts}, f)
    os.replace(tmp, LASTF)

def run():
    # single-instance lock: a slow run (bridge stalls) must not overlap the next 120s tick,
    # or two processes load the same state and double-send.
    lock_fh = open(os.path.expanduser("~/.claude/state/listing-templates/.wa-intake.lock"), "a+")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another wa-intake run is in progress — skipping this tick")
        return
    con = sqlite3.connect(MSG_DB)
    idc = _rowid_col(con)
    if not os.path.exists(LASTF):
        # first run: watermark at the newest timestamp, do not backfill old threads
        mx = con.execute("SELECT MAX(timestamp) FROM messages").fetchone()[0] or ""
        _write_last(mx)
        print("first run: watermark set at", mx, "(no backfill)")
        return
    if _quiet_hours():
        # hold WITHOUT advancing last_ts: overnight enquiries are served after 07:30 SGT
        print("quiet hours (SGT) — holding; overnight enquiries will be served after 07:30")
        return
    try:
        last_ts = json.load(open(LASTF)).get("last_ts", "")
    except Exception:
        # corrupt/half-written watermark -> re-derive rather than crash (and avoid backfill)
        last_ts = con.execute("SELECT MAX(timestamp) FROM messages").fetchone()[0] or ""
        print("watermark unreadable — re-derived from MAX(timestamp)")

    # >= plus per-conversation msg-id dedup: never miss or double-process a boundary message
    rows = con.execute(
        f"SELECT {idc}, chat_jid, is_from_me, content, timestamp FROM messages "
        f"WHERE timestamp >= ? AND chat_jid LIKE '%@lid' ORDER BY timestamp", (last_ts,)
    ).fetchall()

    state = E.load_state()
    acted = 0
    for rid, jid, ifm, content, ts in rows:
        last_ts = ts
        # the bridge echoes some of OUR bot sends with is_from_me=0. Do not treat those as a
        # prospect inbound (they poison the profile / self-trigger sends). A prospect's FILLED
        # form is not an echo and still flows.
        if not ifm and _is_our_echo(content):
            continue
        # per-row guard: a malformed message or transient error must not abort the whole run
        # (which would stall the pipeline and replay rows). Log, skip the row, keep going.
        try:
            ev = {"jid": jid, "msg_id": str(rid), "text": content or "", "is_from_me": bool(ifm)}
            if not ifm:
                ev["listing_key"] = match_listing(content)
            # a bot-template outbound is OUR send (no takeover); any other outbound = Winfred by hand
            ev["engine"] = E.is_bot_message(content) if ifm else False
            a = E.handle_event(state, ev)
            if not a:
                continue
            # the ONLY thing Winfred is pinged about: a prospect giving a date/time to view.
            if a.get("notify"):
                rec = state["conversations"].get(a["pn"], {})
                nm = rec.get("profile",{}).get("name") or a["pn"]
                lk = rec.get("listing_key") or "a listing"
                if a["type"] == "VIEWING_TIME_PROPOSED":
                    notify_winfred(f"Viewing time from a prospect.\n{nm} ({a['pn']}) for {lk} said:\n{a.get('when','')}\nReply to them to confirm.")
                elif a["type"] == "CONFIRM_VIEWING":
                    notify_winfred(f"Prospect confirmed a viewing.\n{nm} ({a['pn']}) accepted the slot for {lk}.")
                elif a["type"] == "ANSWER_QUESTION":
                    notify_winfred(f"Prospect question (reply by hand).\n{nm} ({a['pn']}) for {lk} asked:\n{a.get('question','')}")
                elif a["type"] == "COPILOT_VERDICT":
                    # Winfred is handling this chat by hand; the engine stays silent to the prospect
                    # but tells HIM the screening result so a qualified tenant is never missed.
                    v = a.get("verdict"); why = a.get("why") or []
                    if v == "QUALIFIED":
                        notify_winfred(f"Co-pilot (you are handling this chat):\n{nm} ({a['pn']}) is QUALIFIED for {lk}. Full profile in, fits the landlord's criteria. Worth offering a viewing.")
                    elif v == "NEEDS_INFO":
                        notify_winfred(f"Co-pilot (you are handling this chat):\n{nm} ({a['pn']}) for {lk} is almost there. Still unclear: {'; '.join(why)}.")
                    elif v == "DISQUALIFIED":
                        notify_winfred(f"Co-pilot (you are handling this chat):\n{nm} ({a['pn']}) does NOT fit {lk}. Reason: {'; '.join(why)}.")
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
            if E.DRY_RUN:
                for tx in texts:
                    _log("WOULD_SEND", a.get("pn"), a.get("type") + " :: " + tx.replace("\n"," / "))
            elif not _guard_reserve(jid):
                # atomic reserve failed: another sender (or our own just-completed send) holds
                # this person inside the shared cooldown — never double-send. State still advances.
                _log("GUARD_SKIP", a.get("pn"), a.get("type"))
            else:
                # reserve() already marked the send under one lock (no separate mark needed)
                allok = True
                for tx in texts:               # form_sent is already marked, so this fires once only
                    ok = _send(jid, tx)        # send to the chat handle we received from
                    allok = allok and ok
                    _log("SENT" if ok else "SEND_FAIL", a.get("pn"), a.get("type"))
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
    _write_last(last_ts)
    print(f"processed {len(rows)} new messages, {acted} engine actions, DRY_RUN={E.DRY_RUN}")

WINFRED_CHAT = "540127870"
TG_SEND = os.path.expanduser("~/.claude/bin/telegram_send.sh")
def notify_winfred(msg):
    """Telegram ping to Winfred. Fires even in DRY_RUN (it is a note to him, not a prospect send)."""
    import subprocess
    try:
        subprocess.run(["bash", TG_SEND, WINFRED_CHAT], input=msg, text=True,
                       timeout=15, capture_output=True)
    except Exception as e:
        _log("TG_FAIL", WINFRED_CHAT, str(e))

def _log(kind, pn, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {kind} | {pn} | {msg}\n"
    with open(PREVIEW, "a") as f: f.write(line)

if __name__ == "__main__":
    run()
