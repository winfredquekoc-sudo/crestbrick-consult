"""
wa_intake_send.py -- low level send/guard primitives and quiet-hours/watermark helpers,
split out of wa_intake_runner.py (9 Sep 2026 merge review) to keep that file under the
repo's 500 line guideline. Pure move, byte identical logic -- re-imported straight back
into wa_intake_runner's namespace so every existing call site (including tests that reach
these via wa_intake_runner.<name>) keeps working unchanged.
"""
import os, json, subprocess
import intake_engine as E

LASTF = os.path.expanduser("~/.claude/state/listing-templates/runner-last.json")
BRIDGE = "http://localhost:8080/api/send"
GUARD = os.path.expanduser("~/crestbrick-consult/scripts/wa_send_guard.py")

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

# ---------- daily send cap decision (pure function, split out of wa_intake_runner.run()
# 9 Sep 2026 merge review to keep that file under the repo's 500 line guideline) ----------
# DAILY CAP: at most DAILY_SEND_CAP automated touches per client per SGT day (a touch = one
# engine action; SEND_FORM's unit-info + form pair counts as one).
# CONFIRM_VIEWING, OFFER_VIEWING and ASK_ONE are always exempt -- each is a direct reply to
# the prospect's own message in the booking flow (the viewing-first happy path is 3 touches,
# and capping it at 2 dropped the offer right after a YES, adversarial-review P2-8).
# REDIRECT (unit gone / policy excluded / cross sell), LEASE_NOTE and the tenant-time-
# proposal replies (VIEWING_TIME_PROPOSED / ASK_TENANT_TIME) are each a direct, one-shot
# reply to something the tenant just said -- holding them for the ordinary cap dead-ends a
# prospect who was mid conversation (P2 fix, 9 Sep 2026 cycle 3 attack replay; time-reply
# incident pn 6589824485 / 6584553538). But unlike CONFIRM_VIEWING/OFFER_VIEWING/ASK_ONE
# they are NOT unconditionally exempt forever -- each is bounded to ONE extra touch a
# client a SGT day via its own date stamp (rec[<field>]), separate from the ordinary
# sends_today counter, so a second one the same day still waits for Winfred same as before
# this fix (9 Sep 2026 merge review: an earlier cut made REDIRECT/LEASE_NOTE fully exempt,
# which a stress test showed could be replayed repeatedly against the same chat).
_BOUNDED_ONCE_FIELD = {
    "VIEWING_TIME_PROPOSED": "time_reply_sent_date",
    "ASK_TENANT_TIME": "time_reply_sent_date",
    "REDIRECT": "redirect_sent_date",
    "LEASE_NOTE": "lease_note_reply_sent_date",
}

def daily_cap_should_skip(a, grec, today_sgt, bound_field, daily_send_cap):
    """Returns (log_suffix, notify_msg_or_None) if this action must be held back this tick,
    or None if it should proceed as normal. bound_field is _BOUNDED_ONCE_FIELD.get(a['type'])
    -- the caller keeps it in scope after this call too, to stamp the date on a successful
    send (see wa_intake_runner.run(), 'latch the once-a-day bound' comment). notify_msg is
    only ever set for the ORDINARY ceiling, never the bounded-once carve outs, which stay
    silent exactly as before this fix -- a held reply must still reach Winfred, but a chat
    that already got its one time reply/redirect/lease note today needs no extra ping."""
    if bound_field:
        if grec.get(bound_field) == today_sgt:
            return a.get("type") + " :: already sent one today", None
        return None
    if a.get("type") in ("CONFIRM_VIEWING", "OFFER_VIEWING", "ASK_ONE"):
        return None
    if (grec.get("sends_today_date") == today_sgt
            and int(grec.get("sends_today") or 0) >= daily_send_cap):
        nm = grec.get("profile", {}).get("name") or a.get("pn")
        lk = grec.get("listing_key") or "a listing"
        msg = (f"Daily touch cap reached for {nm} ({a.get('pn')}) on {lk}: held a "
               f"{a.get('type')} reply, reply by hand if it needs to go out today.")
        return a.get("type") + f" :: already {daily_send_cap} touches today", msg
    return None

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
