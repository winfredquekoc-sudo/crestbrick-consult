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


def _attempt_send_draft(d, state, send_fn, guard_reserve_fn, log_fn, notify_fn):
    """Run every remaining gate on one PENDING/APPROVED draft and either send it or refuse
    it, always telling Winfred the outcome. Shared by the immediate /send path and the
    queued-drafts sweep (drafts he approved while quiet hours were still in effect) so the
    two can never drift apart. Returns True if the draft reached a terminal state (sent,
    failed, rejected, excluded, cold, expired); False if it is left exactly as it was for a
    later retry (cap reached today, or the cross sender guard has it reserved right now)."""
    did, pn = d["id"], d.get("pn")
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


def sweep_approved_drafts(state, send_fn, guard_reserve_fn, log_fn, notify_fn):
    """Called once per runner tick, ONLY after that tick has already confirmed quiet hours
    are over -- retries every draft Winfred approved with /send while it was still quiet
    hours (status 'approved'). A draft that aged past its 24h expiry while queued is expired
    here, never sent (Winfred, 9 Sep 2026)."""
    for d in RES._load_drafts():
        if d.get("status") != "approved":
            continue
        if time.time() - d.get("created", 0) > RES.DRAFT_EXPIRY_SEC:
            RES.mark_draft(d["id"], "expired")
            log_fn("SEND_CMD_EXPIRED", d.get("pn"), d["id"])
            notify_fn(f"Draft {d['id']} for {d.get('pn')} expired before it could go out; not sent.")
            continue
        _attempt_send_draft(d, state, send_fn, guard_reserve_fn, log_fn, notify_fn)


def handle_self_chat_command(jid, text, send_fn, guard_reserve_fn, log_fn,
                              notify_fn=None, quiet_hours_fn=None, state=None):
    """A from_me row in Winfred's OWN self chat. Returns True if this row was a recognised
    command (handled or deliberately ignored) so the runner never routes a self chat row
    into the tenant pipeline. '/send'/'/drop' typed in any OTHER chat is not seen here at
    all -- that row is a real message already visible to whoever is in that chat, and the
    runner's normal per-row skip for OWN_JID keeps this function from ever being asked
    about it. notify_fn/quiet_hours_fn/state default to permissive no-ops so an existing
    caller that does not pass them keeps behaving as it always did."""
    notify_fn = notify_fn or (lambda *a, **k: None)
    quiet_hours_fn = quiet_hours_fn or (lambda: False)
    t = (text or "").strip()
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
    _attempt_send_draft(d, state, send_fn, guard_reserve_fn, log_fn, notify_fn)
    return True
