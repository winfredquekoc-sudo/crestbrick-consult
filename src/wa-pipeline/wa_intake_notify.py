"""
wa_intake_notify.py -- Telegram alerting + the preview log writer, split out of
wa_intake_runner.py (8 Sep 2026) to keep that file under the repo's 500 line guideline.
Re-imported straight back into wa_intake_runner's namespace so every existing call site
keeps working unchanged (including tests that reach these via wa_intake_runner.<name>).
"""
import os, re, json, time, subprocess
import intake_engine as E

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
        # routine FLAG_HUMAN style ping (redirect/house_gate/edge case) -- coalesced
        # per chat (see notify_winfred_coalesced above); a dispute or protected attribute
        # flag inside it still goes out immediately, the function detects that itself
        # from the reason text.
        notify_winfred_coalesced(a.get("pn"),
            f"{a['type']}: {nm} ({a['pn']}) on {lk} — {a.get('reason','')}")
