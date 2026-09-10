"""
wa_intake_selfchat.py -- '/send <id>' and '/drop <id>' self chat command handling, split out
of wa_intake_resume.py (9 Sep 2026) to keep that file under the repo's 500 line guideline.
Depends on wa_intake_resume (drafts persistence, validate_draft, seconds_since) one way only
-- resume.py never imports this module -- so there is no import cycle.

Opus review, 9 Sep 2026: /send used to be a bare bypass -- no cold guard, no daily cap, no
excluded_reason recheck, no quiet hours, and no counter increment. A tap of /send is still a
message to a live prospect, so everything below runs the SAME gates the autonomous send
choke point runs (wa_intake_runner.py), in the same order, and Winfred hears the outcome
either way.
"""
import re, time, datetime
import intake_engine as E
import wa_intake_resume as RES

_SEND_RE = re.compile(r"^/send\s+([0-9a-f]{6,10})\s*$", re.I)
_DROP_RE = re.compile(r"^/drop\s+([0-9a-f]{6,10})\s*$", re.I)
_SLOT_RE = re.compile(r"^/slot\s+(.*)$", re.I)

# ---------- /slot <listing_key> <weekday> <time>[ to <time>] (Winfred's own self chat only --
# any other chat never reaches this function, see wa_intake_runner.py's OWN_JID gate) ----------
# a viewing slot on every open listing (11 Sep 2026): the fastest way for Winfred to fill a
# listing's fixed_viewing without opening the slot file by hand. Writes under the SAME flock
# as extract_viewing_windows.py / apply_viewing_windows.py (intake_engine.apply_fixed_viewing).
_WD_ALIASES = {
    "mon": "mon", "monday": "mon", "tue": "tue", "tues": "tue", "tuesday": "tue",
    "wed": "wed", "weds": "wed", "wednesday": "wed", "thu": "thu", "thur": "thu",
    "thurs": "thu", "thursday": "thu", "fri": "fri", "friday": "fri",
    "sat": "sat", "saturday": "sat", "sun": "sun", "sunday": "sun",
}
_SLOT_TIME_TOK = r"\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm)"
_SLOT_BODY_RE = re.compile(
    rf"^\s*([a-z]+)\s+({_SLOT_TIME_TOK})(?:\s*(?:to|-|–)\s*({_SLOT_TIME_TOK}))?\s*$", re.I)


def _slot_clock(tok):
    m = re.match(r"^(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)$", tok.strip(), re.I)
    if not m:
        return None
    h, mm, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if h == 12 and ap == "am":
        h = 0
    elif ap == "pm" and h != 12:
        h += 12
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None
    return h, mm


def _slot_hm(clock):
    return f"{clock[0]:02d}:{clock[1]:02d}"


def _slot_label(clock):
    h, m = clock
    h12 = h % 12 or 12
    frac = f".{m:02d}" if m else ""
    if h == 12 and m == 0:
        return f"{h12}{frac}noon"
    return f"{h12}{frac}" + ("am" if h < 12 else "pm")


def parse_slot_command(rest):
    """'<listing_key> <weekday> <time>[ to <time>]' -> (listing_key, fixed_viewing_dict, None)
    on success, or (None, None, error_message) on anything unparseable -- an invalid /slot is
    always rejected with a reason, never guessed into a slot."""
    parts = (rest or "").strip().split(None, 1)
    if len(parts) < 2:
        return None, None, ("usage: /slot <listing_key> <weekday> <time>, "
                             "e.g. /slot cherryhill Sat 11am")
    listing_key, body = parts[0], parts[1]
    m = _SLOT_BODY_RE.match(body)
    if not m:
        return None, None, (f"could not read a weekday and time from '{body}'; "
                             f"try /slot {listing_key} Sat 11am")
    day_word, start_tok, end_tok = m.group(1).lower(), m.group(2), m.group(3)
    wd = _WD_ALIASES.get(day_word)
    if not wd:
        return None, None, f"'{day_word}' is not a weekday (mon/tue/wed/thu/fri/sat/sun)"
    start_c = _slot_clock(start_tok)
    if start_c is None:
        return None, None, f"could not read the time '{start_tok}'"
    end_c = None
    if end_tok:
        end_c = _slot_clock(end_tok)
        if end_c is None:
            return None, None, f"could not read the time '{end_tok}'"
    label = _slot_label(start_c) + (" to " + _slot_label(end_c) if end_c else "")
    fv = {"weekday": wd, "start": _slot_hm(start_c),
          "end": _slot_hm(end_c) if end_c else None, "time_label": label}
    return listing_key, fv, None


def _handle_slot_command(rest, log_fn, notify_fn):
    listing_key, fv, err = parse_slot_command(rest)
    if err:
        log_fn("SLOT_CMD_REJECTED", "self", (rest or "")[:80] + " :: " + err)
        notify_fn(f"/slot rejected: {err}")
        return True
    result = E.apply_fixed_viewing({listing_key: fv})
    if listing_key in (result.get("missing") or []):
        log_fn("SLOT_CMD_UNKNOWN_LISTING", "self", listing_key)
        notify_fn(f"/slot rejected: no listing '{listing_key}' found in the index.")
        return True
    if result.get("error"):
        log_fn("SLOT_CMD_ERROR", "self", str(result["error"]))
        notify_fn(f"/slot for {listing_key} failed: {result['error']}.")
        return True
    log_fn("SLOT_CMD_SET", "self", f"{listing_key} :: {fv}")
    notify_fn(f"Got it, {listing_key} viewing slot set to "
              f"{fv['weekday'].title()} {fv['time_label']}. The next qualified prospect will "
              f"be offered this time.")
    return True
SEND_MAX_INBOUND_AGE_HOURS = 5 * 24   # mirrors wa_intake_runner.SEND_MAX_INBOUND_AGE_HOURS --
                                       # duplicated (not imported) to avoid a circular import
DAILY_SEND_CAP = 2                    # mirrors wa_intake_runner.DAILY_SEND_CAP, same reason


def _now_iso_sgt():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()


def _cold_reason(rec):
    """None if REC's last inbound is fresh enough for a /send; otherwise a short reason
    naming the last message date. No record, or no timestamp on file, returns None (cannot
    judge age -> best effort, never blocks) -- a resume draft's record always carries one in
    practice (last_inbound_ts is stamped on every prospect inbound), so this only matters for
    a hand rolled test fixture or a draft whose record has since been pruned."""
    if not rec:
        return None
    ts = rec.get("last_inbound_ts")
    if not ts:
        return None
    gap = RES.seconds_since(ts, _now_iso_sgt())
    if gap is None or gap <= SEND_MAX_INBOUND_AGE_HOURS * 3600:
        return None
    return f"lead gone cold, last message {ts}"


def _cap_reason(rec):
    """None if REC still has daily touch cap headroom for a /send; otherwise a short reason.
    No record -> cannot judge -> best effort, never blocks (same posture as _cold_reason)."""
    if not rec:
        return None
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    if rec.get("sends_today_date") == today and int(rec.get("sends_today") or 0) >= DAILY_SEND_CAP:
        return f"daily touch cap ({DAILY_SEND_CAP}) already reached today"
    return None


def _bump_cap(rec):
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    if rec.get("sends_today_date") != today:
        rec["sends_today_date"] = today
        rec["sends_today"] = 0
    rec["sends_today"] = int(rec.get("sends_today") or 0) + 1


def _attempt_send_draft(d, state, send_fn, guard_reserve_fn, log_fn, notify_fn, con):
    """Run every remaining gate on one PENDING/APPROVED draft and either send it or refuse
    it, always telling Winfred the outcome. Shared by the immediate /send path and the
    queued-drafts sweep (drafts he approved while quiet hours were still in effect) so the
    two can never drift apart. Returns True if the draft reached a terminal state (sent,
    failed, rejected, excluded, disputed, cold, expired); False if it is left exactly as it
    was for a later retry (cap reached today, or the cross sender guard has it reserved
    right now).

    con: the runner's own sqlite messages.db connection. Review fix: neither /send nor the
    approved-drafts sweep ever re-checked dispute language before this -- a one tap /send
    could reach a prospect mid dispute. Re-run dispute_language_recent over the chat's last
    10 messages HERE, at actual send time, regardless of whether the record is currently
    under manual_takeover (dispute_language_recent itself fails CLOSED on a query error, so
    passing con=None also refuses rather than silently sending)."""
    did, pn = d["id"], d.get("pn")
    if RES.dispute_language_recent(con, d["jid"]):
        RES.mark_draft(did, "disputed")
        log_fn("SEND_CMD_DISPUTE", pn, did)
        notify_fn(f"Draft {did} for {pn} NOT sent: dispute/legal language recently in this "
                  f"chat. Reply by hand.")
        return True
    bad = RES.validate_draft(d.get("text"))
    if bad:
        RES.mark_draft(did, "rejected")
        log_fn("SEND_CMD_REJECTED", pn, f"{did} :: {bad}")
        notify_fn(f"Draft {did} for {pn} NOT sent: {bad}. Reply by hand instead.")
        return True
    try:
        why = E.excluded_reason(pn, "")
    except Exception:
        why = "db_error"
    if why:
        RES.mark_draft(did, "excluded")
        log_fn("SEND_CMD_EXCLUDED", pn, f"{did} :: {why}")
        notify_fn(f"Draft {did} for {pn} NOT sent: contact is excluded ({why}).")
        return True
    rec = None
    if state:
        rec = (state.get("conversations") or {}).get(pn)
    cold = _cold_reason(rec)
    if cold:
        RES.mark_draft(did, "cold")
        log_fn("SEND_CMD_COLD", pn, f"{did} :: {cold}")
        notify_fn(f"draft {did} not sent: {cold}")
        return True
    cap = _cap_reason(rec)
    if cap:
        log_fn("SEND_CMD_CAP_SKIP", pn, f"{did} :: {cap}")
        notify_fn(f"Draft {did} for {pn} not sent yet: {cap}. It will retry, or send it by hand.")
        return False
    if not guard_reserve_fn(d["jid"]):
        log_fn("SEND_CMD_GUARD_SKIP", pn, did)
        notify_fn(f"Draft {did} for {pn} not sent: another sender has them reserved right now. It will retry shortly.")
        return False
    ok = send_fn(d["jid"], d["text"])
    RES.mark_draft(did, "sent" if ok else "send_failed")
    log_fn("DRAFT_SENT" if ok else "DRAFT_SEND_FAIL", pn, did)
    if ok:
        if rec is not None:
            _bump_cap(rec)
        notify_fn(f"Sent your drafted reply to {pn} ({did}).")
    else:
        notify_fn(f"Draft {did} for {pn} FAILED to send (bridge error). Still pending, /send {did} to retry.")
    return True


def sweep_approved_drafts(state, send_fn, guard_reserve_fn, log_fn, notify_fn, con=None):
    """Called once per runner tick, ONLY after that tick has already confirmed quiet hours
    are over -- retries every draft Winfred approved with /send while it was still quiet
    hours (status 'approved'). A draft that aged past its 24h expiry while queued is expired
    here, never sent (Winfred, 9 Sep 2026).

    con: the runner's own sqlite messages.db connection, passed straight through to
    _attempt_send_draft so a queued draft re-checks dispute language at the actual moment
    it goes out, same as an immediate /send (review fix)."""
    for d in RES._load_drafts():
        if d.get("status") != "approved":
            continue
        if time.time() - d.get("created", 0) > RES.DRAFT_EXPIRY_SEC:
            RES.mark_draft(d["id"], "expired")
            log_fn("SEND_CMD_EXPIRED", d.get("pn"), d["id"])
            notify_fn(f"Draft {d['id']} for {d.get('pn')} expired before it could go out; not sent.")
            continue
        _attempt_send_draft(d, state, send_fn, guard_reserve_fn, log_fn, notify_fn, con)


def handle_self_chat_command(jid, text, send_fn, guard_reserve_fn, log_fn,
                              notify_fn=None, quiet_hours_fn=None, state=None, con=None):
    """A from_me row in Winfred's OWN self chat. Returns True if this row was a recognised
    command (handled or deliberately ignored) so the runner never routes a self chat row
    into the tenant pipeline. '/send'/'/drop' typed in any OTHER chat is not seen here at
    all -- that row is a real message already visible to whoever is in that chat, and the
    runner's normal per-row skip for OWN_JID keeps this function from ever being asked
    about it. notify_fn/quiet_hours_fn/state default to permissive no-ops so an existing
    caller that does not pass them keeps behaving as it always did.

    con: the runner's own sqlite messages.db connection, threaded down to
    _attempt_send_draft for the send-time dispute recheck (review fix). Omitting it fails
    CLOSED (dispute_language_recent treats a bad connection as dispute present), so a
    caller that forgets to pass it gets a hard refuse, never a silent bypass."""
    notify_fn = notify_fn or (lambda *a, **k: None)
    quiet_hours_fn = quiet_hours_fn or (lambda: False)
    t = (text or "").strip()
    m_slot = _SLOT_RE.match(t)
    if m_slot:
        return _handle_slot_command(m_slot.group(1), log_fn, notify_fn)
    m_send, m_drop = _SEND_RE.match(t), _DROP_RE.match(t)
    if not (m_send or m_drop):
        return False
    did = (m_send or m_drop).group(1).lower()
    d = RES.find_draft(did)
    if not d:
        log_fn("SEND_CMD_UNKNOWN", jid, did)
        return True
    if m_drop:
        RES.mark_draft(did, "dropped")
        log_fn("DRAFT_DROPPED", jid, did)
        return True
    # /send
    if d.get("status") not in ("pending", "approved"):
        log_fn("SEND_CMD_STALE", jid, f"{did} status={d.get('status')}")
        return True
    if time.time() - d.get("created", 0) > RES.DRAFT_EXPIRY_SEC:
        RES.mark_draft(did, "expired")
        log_fn("SEND_CMD_EXPIRED", jid, did)
        notify_fn(f"Draft {did} for {d.get('pn')} expired (over 24h old) and was not sent.")
        return True
    if quiet_hours_fn():
        # Winfred's own approval still waits out quiet hours -- queued, never dropped. The
        # runner's sweep_approved_drafts retries it on the first tick after 07:00, provided
        # it is still under 24h old by then.
        if d.get("status") != "approved":
            RES.mark_draft(did, "approved")
            log_fn("SEND_CMD_QUEUED", jid, did)
            notify_fn(f"Got it, draft {did} for {d.get('pn')} is queued and will go out at 07:00.")
        return True
    _attempt_send_draft(d, state, send_fn, guard_reserve_fn, log_fn, notify_fn, con)
    return True
