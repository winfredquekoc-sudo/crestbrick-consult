"""
wa_intake_notify.py -- Telegram alerting + the preview log writer, split out of
wa_intake_runner.py (8 Sep 2026) to keep that file under the repo's 500 line guideline.
Re-imported straight back into wa_intake_runner's namespace so every existing call site
keeps working unchanged (including tests that reach these via wa_intake_runner.<name>).
"""
import os, re, json, time, subprocess

PREVIEW  = os.path.expanduser("~/.claude/state/listing-templates/dry-run-preview.log")
WINFRED_CHAT = "540127870"
TG_SEND = os.path.expanduser("~/.claude/bin/telegram_send.sh")
NOTIFY_Q = os.path.expanduser("~/.claude/state/listing-templates/notify-queue.json")
COALESCE_FILE = os.path.expanduser("~/.claude/state/listing-templates/notify-coalesce.json")
COALESCE_WINDOW_SEC = 30 * 60   # merge review 9 Sep 2026: attack fixes raised FLAG_HUMAN
                                 # style ping volume ~5x/day; hold routine ones together

def _log(kind, pn, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {kind} | {pn} | {msg}\n"
    with open(PREVIEW, "a") as f: f.write(line)

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

# ---------- per-chat notify coalescing (merge review, 9 Sep 2026) ----------
# The attack fix rounds added many new FLAG_HUMAN style Telegram pings (protected attribute
# declines, edge case redirects, held-by-cap replies) -- each individually correct, but
# together they raised Winfred's notify volume from ~5 to ~27 a day. Routine FLAG_HUMAN
# style pings for the SAME chat are now coalesced: the first one in a 30 minute window still
# goes out immediately (so a genuinely new situation is never silently delayed), any more for
# that SAME chat inside the window are held and rolled into ONE digest sent at the window end.
# A dispute/complaint/protected attribute/legal-threat flag, or anything the caller marks
# bypass=True (VIEWING_TIME_PROPOSED and other time critical pings), always goes out
# immediately and never enters a window -- see notify_winfred_coalesced's docstring.
_DISPUTE_OR_P0_RE = re.compile(
    r"dispute|discrimina|racist|racism|complain|legal\s+threat|sensitive\s+content", re.I)

def _is_dispute_or_p0(reason):
    return bool(_DISPUTE_OR_P0_RE.search(str(reason or "")))

def _load_coalesce():
    try:
        d = json.load(open(COALESCE_FILE))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_coalesce(d):
    tmp = COALESCE_FILE + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, COALESCE_FILE)

def notify_winfred_coalesced(pn, msg, bypass=False):
    """Routine FLAG_HUMAN style ping for one chat (pn): at most one immediate Telegram ping
    per chat per COALESCE_WINDOW_SEC (30 min); any further ones for the SAME chat inside
    that window are held and rolled into one digest when the window ends (see
    _flush_stale_coalesce_windows, called once per runner tick -- a window with nothing held
    when it ends just closes with no extra ping). Pass bypass=True (or msg text carrying a
    dispute/protected-attribute/legal-threat marker -- auto-detected too) to go straight to
    notify_winfred every time, uncoalesced: those must never wait behind routine chatter."""
    if bypass or not pn or _is_dispute_or_p0(msg):
        notify_winfred(msg)
        return
    now = time.time()
    d = _load_coalesce()
    w = d.get(pn)
    if w is None or now - w.get("window_start", 0) >= COALESCE_WINDOW_SEC:
        notify_winfred(msg)
        d[pn] = {"window_start": now, "held": []}
        _save_coalesce(d)
        return
    w.setdefault("held", []).append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "msg": msg})
    d[pn] = w
    _save_coalesce(d)

def _flush_stale_coalesce_windows():
    """Called once per runner tick (before or after the main loop -- order does not matter,
    it only ever acts on windows that have already ended). Any chat whose 30 minute window
    has ended with held messages gets ONE consolidated digest; a window that ended with
    nothing held (the common case -- most chats only ever produce a single flag) is just
    dropped, no extra ping."""
    d = _load_coalesce()
    now = time.time()
    changed = False
    for pn in list(d.keys()):
        w = d[pn]
        if now - w.get("window_start", 0) >= COALESCE_WINDOW_SEC:
            held = w.get("held") or []
            if held:
                lines = "\n".join("- " + h["msg"].replace("\n", " ") for h in held)
                notify_winfred(f"{len(held)} more update(s) for {pn} in the last 30 "
                               f"minutes, held together:\n{lines}")
            del d[pn]
            changed = True
    if changed:
        _save_coalesce(d)

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
