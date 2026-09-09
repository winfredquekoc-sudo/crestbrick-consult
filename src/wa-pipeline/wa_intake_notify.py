"""
wa_intake_notify.py -- Telegram alerting + the preview log writer, split out of
wa_intake_runner.py (8 Sep 2026) to keep that file under the repo's 500 line guideline.
Re-imported straight back into wa_intake_runner's namespace so every existing call site
keeps working unchanged (including tests that reach these via wa_intake_runner.<name>).
"""
import os, json, time, subprocess

PREVIEW  = os.path.expanduser("~/.claude/state/listing-templates/dry-run-preview.log")
WINFRED_CHAT = "540127870"
TG_SEND = os.path.expanduser("~/.claude/bin/telegram_send.sh")
NOTIFY_Q = os.path.expanduser("~/.claude/state/listing-templates/notify-queue.json")

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

ALLOW_FILE = os.path.expanduser("~/.claude/state/listing-templates/notify-allow.json")
MUTED_LOG = os.path.expanduser("~/.claude/state/listing-templates/notify-muted.log")

def _allowed(msg):
    # Winfred (9 Sep 2026): stop the useless pings. When the allow file exists, only messages
    # starting with one of its prefixes go to Telegram; the rest are kept in a local digest.
    try:
        if not os.path.exists(ALLOW_FILE):
            return True
        prefixes = json.load(open(ALLOW_FILE)).get("prefixes") or []
        head = (msg or "").lstrip()[:120].lower()
        return any(head.startswith(p.lower()) for p in prefixes)
    except Exception:
        return True

def notify_winfred(msg):
    """Telegram ping to Winfred. Fires even in DRY_RUN (it is a note to him, not a prospect
    send). A FAILED ping is queued and retried at the start of every later run: a viewing
    confirmation or takeover flag must never be silently lost to a Telegram outage."""
    if not _allowed(msg):
        try:
            with open(MUTED_LOG, "a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + (msg or "").replace("\n", " / ")[:400] + "\n")
        except Exception:
            pass
        return
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
