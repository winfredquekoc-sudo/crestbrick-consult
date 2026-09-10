"""
wa_intake_notify.py -- Telegram alerting + the preview log writer, split out of
wa_intake_runner.py (8 Sep 2026) to keep that file under the repo's 500 line guideline.
Re-imported straight back into wa_intake_runner's namespace so every existing call site
keeps working unchanged (including tests that reach these via wa_intake_runner.<name>).
"""
import os, re, json, time, subprocess
import intake_engine as E
import wa_intake_paths as _P

# STEP 0 sandbox seal (9 Sep 2026 merge redo): PREVIEW/NOTIFY_Q/COALESCE_FILE/ALLOW_FILE/
# MUTED_LOG kept as module constants for backward compat with existing
# `mock.patch.object(wa_intake_notify, "NAME", ...)` tests; every function below resolves
# the CURRENT path via the _xxx() helpers at call time. Previously these five were hardcoded
# absolute paths independent of STATE_DIR/E.STATE, so patching STATE_DIR (the harness's own
# belt-and-suspenders line) never actually redirected them -- _drain_notify_queue() in
# particular could silently os.remove() the LIVE notify-queue.json on every sandboxed tick
# (every queued item "succeeds" once Telegram is suppressed, so the queue always empties).
PREVIEW  = _P.paths()["dry_run_preview"]
_default_PREVIEW = PREVIEW
WINFRED_CHAT = "540127870"
TG_SEND = os.path.expanduser("~/.claude/bin/telegram_send.sh")
NOTIFY_Q = _P.paths()["notify_queue"]
_default_NOTIFY_Q = NOTIFY_Q
COALESCE_FILE = _P.paths()["notify_coalesce"]
_default_COALESCE_FILE = COALESCE_FILE


def _preview():
    return _P.resolved(globals(), "PREVIEW", "dry_run_preview")


def _notify_q():
    return _P.resolved(globals(), "NOTIFY_Q", "notify_queue")


def _coalesce_file():
    return _P.resolved(globals(), "COALESCE_FILE", "notify_coalesce")
COALESCE_WINDOW_SEC = 30 * 60   # merge review 9 Sep 2026: attack fixes raised FLAG_HUMAN
                                 # style ping volume ~5x/day; hold routine ones together

# ---------- Telegram kill switch (incident, 9 Sep 2026 merge redo) ----------
# A sandbox harness run reached Winfred's real phone with synthetic scenario numbers and
# listing keys. Root cause: the harness only patched wa_intake_runner.notify_winfred, a
# SEPARATE `from wa_intake_notify import notify_winfred` binding in that other module's
# namespace -- it never touched this module's own notify_winfred/notify_winfred_coalesced/
# notify_for_action, which call the name `notify_winfred` (and _tg_send) as globals resolved
# in THIS module's own dict. notify_for_action and _flush_stale_coalesce_windows fire real
# pings from calls that live entirely inside this file, so patching the wrong module's
# binding left them wide open. Rather than rely on every call site being patched correctly,
# the kill switch lives at the one physical send call (_tg_send) that every path funnels
# through -- it cannot be bypassed by patching the wrong name again.
STATE_DIR = _P.paths()["state_root"]     # module constant a sandbox can patch directly
_default_STATE_DIR = STATE_DIR
_REAL_STATE_DIR = os.path.expanduser("~/.claude/state/listing-templates")

def _effective_state_dir():
    """The state dir actually in effect right now: STATE_DIR if patched away from real
    (either directly via mock.patch or via WA_INTAKE_STATE_ROOT, resolved at call time --
    see wa_intake_paths.resolved), else intake_engine.STATE's own directory (the harness
    already sandboxes E.STATE for every scenario, so this catches that case too without
    needing a second patch)."""
    sd = _P.resolved(globals(), "STATE_DIR", "state_root")
    if sd != _REAL_STATE_DIR:
        return sd
    try:
        d = os.path.dirname(E.STATE)
        if d and d != _REAL_STATE_DIR:
            return d
    except Exception:
        pass
    return sd

def _telegram_suppressed():
    """None when a real Telegram send may proceed; otherwise a short reason string logged
    alongside TG_SUPPRESSED. Three independent signals, any one is enough: an explicit env
    var (set by every harness/replay script at import time), a state dir that no longer
    matches the real one (sandboxes monkeypatch STATE_DIR or E.STATE), or a SANDBOX marker
    file dropped into whatever dir is currently in effect."""
    if os.environ.get("WA_INTAKE_NO_TELEGRAM") == "1":
        return "env"
    eff = _effective_state_dir()
    if eff != _REAL_STATE_DIR:
        return "state_dir"
    if os.path.exists(os.path.join(eff, "SANDBOX")):
        return "marker"
    return None

def _log(kind, pn, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {kind} | {pn} | {msg}\n"
    with open(_preview(), "a") as f: f.write(line)

def _tg_send(msg):
    """One Telegram send attempt. True only on a confirmed delivery (script exit 0 AND the
    API replied ok:true) — curl reaching Telegram but the API rejecting still counts failed.
    A suppressed sandbox call logs TG_SUPPRESSED and reports success (True) so callers never
    queue a sandbox no-op for real-world retry. The log write itself only ever lands under a
    genuinely sandboxed dir (STATE_DIR/E.STATE patched away from real) -- an env-var-only
    suppression (the common harness/test case) never touches disk at all, so it can never
    write into the real, live ~/.claude/state/listing-templates even for a log line."""
    why = _telegram_suppressed()
    if why:
        eff = _effective_state_dir()
        if eff != _REAL_STATE_DIR:
            try:
                with open(os.path.join(eff, "dry-run-preview.log"), "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | TG_SUPPRESSED | "
                            f"{WINFRED_CHAT} | [{why}] "
                            f"{(msg or '')[:160].replace(chr(10), ' / ')}\n")
            except Exception:
                pass
        return True
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

ALLOW_FILE = _P.paths()["notify_allow"]
_default_ALLOW_FILE = ALLOW_FILE
MUTED_LOG = _P.paths()["notify_muted"]
_default_MUTED_LOG = MUTED_LOG


def _allow_file():
    return _P.resolved(globals(), "ALLOW_FILE", "notify_allow")


def _muted_log():
    return _P.resolved(globals(), "MUTED_LOG", "notify_muted")


def _allowed(msg):
    # Winfred (9 Sep 2026): stop the useless pings. When the allow file exists, only messages
    # starting with one of its prefixes go to Telegram; the rest are kept in a local digest.
    try:
        allow_file = _allow_file()
        if not os.path.exists(allow_file):
            return True
        prefixes = json.load(open(allow_file)).get("prefixes") or []
        head = (msg or "").lstrip()[:120].lower()
        return any(head.startswith(p.lower()) for p in prefixes)
    except Exception:
        return True

def notify_winfred(msg):
    """Telegram ping to Winfred. Fires even in DRY_RUN (it is a note to him, not a prospect
    send). A FAILED ping is queued and retried at the start of every later run: a viewing
    confirmation or takeover flag must never be silently lost to a Telegram outage.

    A suppressed (sandbox/test) call skips the allowlist/mute/queue logic entirely and goes
    straight to _tg_send, which is where the actual kill switch lives -- those file paths
    (ALLOW_FILE, MUTED_LOG, NOTIFY_Q) are real, live ~/.claude/state/listing-templates paths,
    and reading or writing them from a sandbox run would itself be a live-state touch."""
    if _telegram_suppressed():
        _tg_send(msg)
        return
    if not _allowed(msg):
        try:
            with open(_muted_log(), "a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | "
                        + (msg or "").replace("\n", " / ")[:400] + "\n")
        except Exception:
            pass
        return
    if _tg_send(msg):
        return
    _log("TG_FAIL", WINFRED_CHAT, "queued for retry :: " + msg[:80].replace("\n", " / "))
    try:
        notify_q = _notify_q()
        q = json.load(open(notify_q)) if os.path.exists(notify_q) else []
        q = (q if isinstance(q, list) else []) + [{"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "msg": msg}]
        q = q[-50:]
        tmp = notify_q + ".tmp"
        json.dump(q, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, notify_q)
    except Exception as e:
        _log("TG_QUEUE_FAIL", WINFRED_CHAT, str(e)[:100])

def _drain_notify_queue():
    """Re-attempt queued Telegram pings from earlier outages. Failures stay queued (cap 50).

    Suppressed early-return (STEP 0 sandbox seal): with Telegram suppressed there is nothing
    to drain TO -- every queued item would "succeed" via _tg_send's suppressed short circuit,
    so the old code always took the `else: os.remove(NOTIFY_Q)` branch. Combined with NOTIFY_Q
    previously being a hardcoded absolute path independent of STATE_DIR, a sandboxed run could
    silently delete the LIVE retry queue. Path is now call-time resolved via _notify_q() too,
    so this is belt-and-suspenders, not the only guard."""
    if _telegram_suppressed():
        return
    try:
        notify_q = _notify_q()
        if not os.path.exists(notify_q):
            return
        q = json.load(open(notify_q))
        if not isinstance(q, list):
            raise ValueError("queue not a list")
    except Exception:
        try: os.remove(notify_q)          # unreadable queue: drop it rather than crash every tick
        except OSError: pass
        return
    left = [it for it in q if it.get("msg") and not _tg_send("(delayed from " + str(it.get("ts")) + ")\n" + it["msg"])]
    try:
        if left:
            tmp = notify_q + ".tmp"
            json.dump(left[-50:], open(tmp, "w"), ensure_ascii=False, indent=1)
            os.replace(tmp, notify_q)
        else:
            os.remove(notify_q)
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
        d = json.load(open(_coalesce_file()))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_coalesce(d):
    coalesce_file = _coalesce_file()
    tmp = coalesce_file + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, coalesce_file)

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
    like a corrupt file, which would otherwise ping every 60s tick). The marker path is
    derived from _effective_state_dir(), not hardcoded to the real state dir (kill switch
    hardening, 9 Sep 2026 merge redo) -- a sandboxed/test call must never leave a stray
    .alert-* file under the real, live ~/.claude/state/listing-templates."""
    mark = os.path.join(_effective_state_dir(), ".alert-" + key)
    try:
        if os.path.exists(mark) and time.time() - os.path.getmtime(mark) < 3600:
            return
        open(mark, "w").write(str(time.time()))
    except OSError:
        pass
    notify_winfred(msg)

# ---------- per-action Telegram ping (split out of wa_intake_runner.run(), 9 Sep 2026 merge
# review, to keep that file under the repo's 500 line guideline) -- pure move, byte
# identical output, just parameterized on (a, state) instead of the loop's own locals.
# Re-imported straight back into wa_intake_runner's namespace so every existing call site
# keeps working unchanged. ----------
def _slot_confirm_count(state, slot_id):
    """How many conversations have CONFIRMED this exact slot (incl. the one just confirmed)."""
    if not slot_id: return 1
    return sum(1 for r in (state.get("conversations") or {}).values()
               if r.get("viewing_confirmed") and r.get("offered_slot_id") == slot_id) or 1

def notify_stale_backfill(skipped, by_pn, stale_row_hours):
    """One aggregated ping per run, never one per row -- a reconnect backfill can carry
    dozens of stale rows in a single tick. Names every affected chat (phone + its own
    skipped count), the way the STALE_BACKFILL_SKIP log line already does per row --
    otherwise Winfred has no way to tell which chats to review by hand (P3 fix, 9 Sep 2026
    cycle 3 attack replay: a multi day outage backfill named no chat at all). No-ops when
    nothing was skipped this tick. Pure move out of wa_intake_runner.run(), 9 Sep 2026
    merge review, to keep that file under the repo's 500 line guideline."""
    if not skipped:
        return
    chats_line = ", ".join(f"{pn} ({n})" for pn, n in by_pn.items())
    notify_winfred(f"{skipped} backfilled chat message(s) were older than "
                   f"{stale_row_hours}h this run and were skipped (never auto-served): "
                   f"{chats_line}. Check these chats by hand if any were real.")

def notify_viewing_slot_needed(a, state):
    """A viewing slot on every open listing (11 Sep 2026): a QUALIFIED prospect who hit
    OFFER_VIEWING with no slot captured gets the hold line (intake_engine.VIEWING_HOLD_TEXT)
    instead of the weak "When are you able to view?" ask. Winfred is pinged ONCE PER LISTING
    PER DAY (never once per prospect -- a slot starved listing could otherwise spam him on
    every qualifying tenant), mirroring the existing "availability_pinged" per-listing-per-day
    latch in wa_intake_runner.py. Uncoalesced (goes straight to notify_winfred, not the 30
    minute per-chat window) -- a fresh listing hitting this for the first time today is exactly
    as time sensitive as the existing availability ping.

    The message starts with "Viewing slot needed" -- deliberately NOT "Viewing time from a
    prospect" (that existing prefix is for a TENANT's own proposed time, VIEWING_TIME_PROPOSED,
    a different situation). Winfred needs to add "Viewing slot needed" to the prefixes list in
    his own live notify-allow.json himself -- this module never writes that live file.

    Requires a real listing_key on the action (every genuine engine-produced OFFER_VIEWING
    carries one) -- a bare test double / stand-in action missing it is never treated as a
    real hold-line offer, so this never fires on an unrelated synthetic action."""
    if (a.get("type") != "OFFER_VIEWING" or a.get("slot") or a.get("copilot")
            or not a.get("listing_key")):
        return
    lk = a["listing_key"]
    today = time.strftime("%Y-%m-%d")
    sp = state.setdefault("viewing_slot_pinged", {})
    if sp.get(lk) == today:
        return
    sp[lk] = today
    notify_winfred(f"Viewing slot needed for {lk}: a qualified prospect is waiting on a "
                   f"viewing time and no slot is captured for this listing. Reply with the "
                   f"slot in the file, or type /slot {lk} Sat 11am (weekday + time) in your "
                   f"own WhatsApp chat.")

def notify_for_action(a, state):
    """Builds and sends (or coalesces) the ONE Telegram ping for an engine action that
    asked for one (a.get('notify')). No-ops for anything that did not ask."""
    if not a.get("notify"):
        return
    rec = state["conversations"].get(a["pn"], {})
    nm = rec.get("profile",{}).get("name") or a["pn"]
    lk = rec.get("listing_key") or "a listing"
    if a.get("category2_code"):
        # category 2 auto reply already sent -- a human still closes the loop,
        # but the ping says so it never reads like a silent unanswered flag.
        _q = a.get("question") or a.get("reason") or ""
        notify_winfred(f"Auto reply sent [{a['category2_code']}].\n{nm} ({a['pn']}) for {lk} asked:\n{_q}\nBot replied: {a.get('text','')}\nClose the loop by hand if it needs more.")
    elif a["type"] == "SEND_BUYER_FORM":
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
    # AUTO_CLOSED (closing pleasantry, Winfred 9 Sep 2026 merge redo): always carries
    # notify=False -- the fixed reply closes the loop on its own, so this branch is
    # deliberately absent. notify_for_action already returned above on notify=False; there
    # is no code path left that would reach an elif for it.
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
        # routine FLAG_HUMAN style ping (redirect/house_gate/edge case) -- coalesced
        # per chat (see notify_winfred_coalesced above); a dispute or protected attribute
        # flag inside it still goes out immediately, the function detects that itself
        # from the reason text.
        notify_winfred_coalesced(a.get("pn"),
            f"{a['type']}: {nm} ({a['pn']}) on {lk} — {a.get('reason','')}")
