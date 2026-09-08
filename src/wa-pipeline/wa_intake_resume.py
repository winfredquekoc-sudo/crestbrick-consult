"""
wa_intake_resume.py -- takeover resume support for wa_intake_runner.py (Winfred, 8 Sep 2026).

Winfred's rule: after he replies to a prospect by hand, he usually stops typing. If the
prospect answers and he has NOT replied again within 5 minutes, the runner may help by
drafting a reply for him to review -- never sending anything to the prospect on its own
initiative. A small, fixed set of engine actions (the same canned templates the autonomous
flow already sends) ARE allowed straight through; everything else becomes a draft.

Kept as a sibling module, not folded into wa_intake_runner.py, purely to stay under the
repo's 500 line file guideline. It is imported and driven entirely by the runner; nothing
here runs standalone against live state, and nothing here sends a WhatsApp message itself
(the runner passes in its own _send/_guard_reserve so there is still only one send path).
"""
import os, re, json, time, hashlib, subprocess, datetime
import intake_engine as E

RESUME_WAIT_SEC = 5 * 60                 # Winfred's own stated wait: 5 minutes of silence
DRAFT_EXPIRY_SEC = 24 * 3600             # a draft older than this can no longer be /send
DRAFTS_FILE = os.path.expanduser("~/.claude/state/listing-templates/drafts.jsonl")
# Winfred's own connected WA number (self chat) -- confirmed against the bridge's device
# table (whatsmeow_device.jid = 6581618149:52@s.whatsapp.net); a chat with himself always
# carries this bare jid, never a device suffix or an @lid handle.
OWN_JID = "6581618149@s.whatsapp.net"

HAIKU_BIN = os.path.expanduser("~/.claude/bin/claude-guard")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MCP_CONFIG = os.path.expanduser("~/.claude/mcp-configs/none.json")
HAIKU_TIMEOUT_SEC = 25

# Action types the engine may return while resuming that are safe to send exactly as the
# autonomous flow already would -- fixed template copy, never free text. ANSWER_QUESTION is
# handled separately (allowed only when it actually carries an answer, i.e. a category 1
# fact); everything else (REDIRECT, SUGGEST_ALT, SEND_BUYER_FORM, an unanswerable
# ANSWER_QUESTION, or no action at all) needs a drafted suggestion instead.
ALLOWED_RESUME_TYPES = frozenset({
    "SEND_FORM", "NUDGE_INCOMPLETE", "ASK_ONE", "OFFER_VIEWING", "CONFIRM_VIEWING",
    "ASK_TENANT_TIME", "LEASE_NOTE",
})
# Notify-only outcomes: safe to let through ONLY while they carry no prospect facing text.
# FLAG_HUMAN and VIEWING_TIME_PROPOSED each have one branch that DOES carry a canned line
# (the declined-CTA channel closer, the "which day works better" reschedule question) --
# neither is on Winfred's approved resume list, so a textful one drafts instead of sending
# (Opus review, 9 Sep 2026: 37 of 41 replay "auto sends" were exactly this).
NOTIFY_ONLY_RESUME_TYPES = frozenset({"FLAG_HUMAN", "COPILOT_VERDICT", "VIEWING_TIME_PROPOSED"})


def _parse_ts(ts):
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        return dt
    except Exception:
        return None


def seconds_since(ts_earlier, ts_later):
    """Seconds from ts_earlier to ts_later, honouring each timestamp's own offset (the
    bridge writes mixed +08:00/-04:00 rows after travel). None on an unparseable pair --
    callers must treat that as 'not yet eligible', never as 'eligible'."""
    a, b = _parse_ts(ts_earlier), _parse_ts(ts_later)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def pn_for(rec, jid):
    """The phone number this record belongs to (records are keyed by it; jid is the fallback
    for a record shape that predates the key being stored)."""
    return rec.get("pn") or E.resolve_pn(jid)


def resume_reason_blocked(con, idc, jid, rec, inbound_rowid, inbound_ts):
    """None if this inbound should run in resume mode; otherwise a short reason string for
    the RESUME_SKIP log line. Split from a boolean so the runner can log WHY without
    re-deriving the same checks."""
    if not rec:
        return "no record"
    if not (rec.get("manual_takeover") or rec.get("human_takeover")):
        return "not under a hand takeover"
    if rec.get("supply_kind") or rec.get("supply_flagged") or rec.get("supply_form_sent"):
        return "landlord/seller onboarding, not a tenant resume"
    if rec.get("buyer_form_sent") or rec.get("buyer_flagged"):
        return "buyer record, not a tenant resume"
    if str(rec.get("status") or "").startswith("excluded:"):
        return "excluded contact (" + str(rec.get("status")) + ")"
    # authoritative re-check on EVERY resume, not just at stage 1: a chat Winfred hand
    # replied to before the engine ever classified it has no supply/excluded latch at all,
    # so without this a landlord, a co-broke agent or a colleague could be resumed as if
    # they were a tenant prospect. Fails CLOSED on an unreadable DB (Opus review, 9 Sep 2026).
    try:
        why = E.excluded_reason(pn_for(rec, jid), "")
    except Exception:
        why = "db_error"
    if why:
        return "excluded contact: " + str(why)
    last_hand = rec.get("last_hand_reply_ts")
    if not last_hand:
        return "no hand reply timestamp recorded yet"
    gap = seconds_since(last_hand, inbound_ts)
    if gap is None:
        return "unparseable timestamp"
    if gap < RESUME_WAIT_SEC:
        return f"only {gap:.0f}s since the hand reply (< {RESUME_WAIT_SEC}s)"
    answered = con.execute(
        f"SELECT 1 FROM messages WHERE chat_jid=? AND is_from_me=1 AND {idc} > ? LIMIT 1",
        (jid, inbound_rowid)).fetchone()
    if answered is not None:
        return "Winfred already answered this inbound"
    return None


def needs_draft(a):
    """True when the engine's action for a resumed inbound must NOT reach a real send and
    instead needs a drafted suggestion for Winfred to review."""
    if not a:
        return True
    t = a.get("type")
    if t == "ANSWER_QUESTION":
        return not a.get("text")     # category 1 fact answer has text; anything else drafts
    if t in NOTIFY_ONLY_RESUME_TYPES:
        return bool(a.get("text") or a.get("texts"))
    return t not in ALLOWED_RESUME_TYPES


def fetch_transcript(con, idc, jid, limit=80):
    """Last LIMIT text messages in this chat, oldest first, tagged ME (Winfred by hand) /
    BOT (the intake engine's own template sends) / THEM (the prospect)."""
    rows = con.execute(
        f"SELECT is_from_me, content FROM messages WHERE chat_jid=? "
        f"AND content IS NOT NULL AND content != '' ORDER BY {idc} DESC LIMIT ?",
        (jid, limit)).fetchall()
    rows.reverse()
    out = []
    for ifm, content in rows:
        who = "THEM"
        if ifm:
            who = "BOT" if E.is_engine_outbound(content) else "ME"
        out.append({"who": who, "text": content})
    return out


def build_prompt(name, phone, listing_key, listing, profile, transcript, last_inbound):
    facts = (listing or {}).get("facts") or {}
    req = (listing or {}).get("requirements") or {}
    convo = "\n".join(f"{m['who']}: {m['text']}" for m in transcript)
    return (
        "You are ghostwriting ONE WhatsApp reply as Winfred Quek, a Singapore property agent, "
        "to a rental prospect he is already talking to. He replied by hand earlier and has not "
        "answered their newest message yet.\n\n"
        f"Prospect: {name or 'unknown name'} ({phone})\n"
        f"Listing: {listing_key or 'not bound'}\n"
        f"Known profile: {profile}\n"
        f"Listing facts on file: lease_min_months={req.get('lease_min_months')}, "
        f"cooking={req.get('cooking')}, smoking={req.get('smoking')}, facts={facts}\n\n"
        "Chat so far (ME = Winfred, BOT = the automated intake engine, THEM = the prospect), "
        f"oldest first:\n{convo}\n\n"
        f"Their newest message to answer:\nTHEM: {last_inbound}\n\n"
        "Write Winfred's reply. Rules:\n"
        "- First person, as Winfred himself.\n"
        "- Plain, simple English, Singapore WhatsApp register.\n"
        "- At most 3 short sentences.\n"
        "- No sign off, no CEA number, no hyphens or dashes of any kind.\n"
        "- Never quote or negotiate a rent figure.\n"
        "- Never give legal, financial or property advice.\n"
        "- Never promise an availability or a slot that is not already in the listing data above.\n"
        "- Pivot to a viewing when that makes sense.\n"
        "- If this needs Winfred's own judgement call (a nuanced negotiation, a complaint, a "
        "legal question, anything you are not confident about), reply with EXACTLY the single "
        "line NEEDS_WINFRED followed by one short line explaining why, and nothing else.\n\n"
        "Reply with ONLY the message text (or NEEDS_WINFRED plus your one line), no preamble, "
        "no quotes.")


def call_haiku(prompt):
    """One claude-guard call, Haiku, no tools. Returns (text, None) or (None, error)."""
    try:
        r = subprocess.run(
            [HAIKU_BIN, "-p", prompt, "--model", HAIKU_MODEL, "--strict-mcp-config",
             "--mcp-config", HAIKU_MCP_CONFIG, "--output-format", "json"],
            capture_output=True, text=True, timeout=HAIKU_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return None, f"exit {r.returncode}: {(r.stderr or '')[:200]}"
    try:
        data = json.loads(r.stdout)
    except Exception:
        return None, "unparseable output"
    if data.get("is_error"):
        return None, "is_error: " + str(data.get("result"))[:200]
    text = (data.get("result") or "").strip()
    if not text:
        return None, "empty result"
    return text, None


# ---------- hard validator on every drafted line (Opus review, 9 Sep 2026) ----------
# A draft is a one tap /send away from a real client, so the model's output is never trusted
# on its own. Anything that trips a rule below is DISCARDED and Winfred is flagged instead --
# the sample pass produced "Tell me about it lol waste of time only" for a live tenant chat.
_BAD_WORD_RE = re.compile(
    r"\b(lol|lmao|lmfao|rofl|omg|wtf|ikr|nvm|haha+|hehe+|hahaha|yolo|bruh|sia|siao|"
    r"dulan|walao|wah\s*lau|knn|cb|damn|damm|crap|shit|sh\*t|fuck|f\*ck|fucking|bloody|"
    r"stupid|idiot|dumb|useless|waste\s+of\s+time|cannot\s+be\s+bothered)\b", re.I)
# advice is CEA regulated and never ours to give in an automated line
_ADVICE_RE = re.compile(
    r"\b(absd|bsd|ssd|stamp\s*duty|cpf|tdsr|msr|ltv|hfe|ipa|mortgage|refinanc\w*|"
    r"loan|interest\s*rate|sora|yield|capital\s*gain|appreciat\w*|invest\w*|"
    r"lawyer|legal|conveyanc\w*|sue|court|tribunal|i\s*(?:would\s*)?(?:advise|recommend|suggest)|"
    r"you\s+should\s+(?:buy|sell|invest|offer|negotiate))\b", re.I)
_CEA_RE = re.compile(r"\b(?:cea|r0?\d{5}[a-z]|l\d{7,9}[a-z])\b", re.I)
# any money figure at all: a rent number is Winfred's to quote, never a drafted line's
_MONEY_RE = re.compile(r"[$\uFF04]\s*\d|\bsgd\b|\bs\$|\b\d{3,5}\s*(?:/|per\s*)?"
                       r"(?:mo|mth|month|pm|monthly)\b|\b\d{4}\b", re.I)
_DASH_RE = re.compile(r"[-\u2010\u2011\u2012\u2013\u2014\u2015\uFF0D]")
DRAFT_MAX_CHARS = 400
DRAFT_MAX_SENTENCES = 3


def validate_draft(text):
    """None if TEXT is safe to offer Winfred as a one tap /send; otherwise a short reason.
    Deliberately strict: the cost of a false reject is one extra hand written reply, the cost
    of a false accept is a real client reading it."""
    t = (text or "").strip()
    if not t:
        return "empty draft"
    if len(t) > DRAFT_MAX_CHARS:
        return f"too long ({len(t)} chars)"
    if len([p for p in re.split(r"[.!?\n]+", t) if p.strip()]) > DRAFT_MAX_SENTENCES:
        return "more than 3 sentences"
    if _DASH_RE.search(t):
        return "contains a hyphen or dash"
    if _BAD_WORD_RE.search(t):
        return "slang or unprofessional wording"
    if _CEA_RE.search(t):
        return "mentions a CEA/licence number"
    if _MONEY_RE.search(t):
        return "quotes a figure"
    if _ADVICE_RE.search(t):
        return "strays into advice"
    if re.search(r"\b(?:https?://|www\.)", t, re.I):
        return "contains a link"
    return None


# ---------- draft persistence: id, pn, jid, listing, text, created, status ----------
def _load_drafts():
    if not os.path.exists(DRAFTS_FILE):
        return []
    out = []
    with open(DRAFTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue          # one corrupt line must not lose every other draft
    return out


def _rewrite_drafts(items):
    tmp = DRAFTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, DRAFTS_FILE)


def new_draft(pn, jid, listing_key, text):
    did = hashlib.sha1(f"{pn}|{listing_key}|{time.time()}".encode()).hexdigest()[:8]
    rec = {"id": did, "pn": pn, "jid": jid, "listing": listing_key, "text": text,
           "created": time.time(), "status": "pending"}
    with open(DRAFTS_FILE, "a") as f:      # append only: a brand new draft never needs the
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")   # read modify write below
    return did


def find_draft(did):
    for d in _load_drafts():
        if d.get("id") == did:
            return d
    return None


def mark_draft(did, status):
    items = _load_drafts()
    changed = False
    for d in items:
        if d.get("id") == did:
            d["status"] = status
            changed = True
    if changed:
        _rewrite_drafts(items)
    return changed


def process_draft_needed(con, idc, jid, pn, rec, listing, notify_fn, log_fn):
    """Build and persist a draft for a resumed inbound the allow list rejected, or FLAG_HUMAN
    Winfred with the last 3 messages if the draft helper fails or times out. Never raises --
    a draft failure must never block the tick (Winfred, 8 Sep 2026)."""
    name = (rec.get("profile") or {}).get("name") or pn
    listing_key = rec.get("listing_key")
    transcript = fetch_transcript(con, idc, jid, limit=80)
    last_inbound = rec.get("last_inbound") or ""
    prompt = build_prompt(name, pn, listing_key, listing, rec.get("profile") or {},
                          transcript, last_inbound)
    text, err = call_haiku(prompt)
    if err:
        log_fn("RESUME_DRAFT_FAIL", pn, f"haiku {err}; flagging instead")
        last3 = transcript[-3:]
        quote = " | ".join(f"{m['who']}: {m['text']}" for m in last3) or "(no recent text)"
        notify_fn(f"Could not draft a reply for {name} ({pn}), {listing_key or 'no listing'} "
                  f"(draft helper failed: {err}). Last messages:\n{quote}\nReply by hand.")
        return
    if text.startswith("NEEDS_WINFRED"):
        why = text[len("NEEDS_WINFRED"):].strip(" :\n") or "needs your own judgement call"
        log_fn("RESUME_DRAFT_NEEDS_WINFRED", pn, why)
        notify_fn(f"{name} ({pn}), {listing_key or 'no listing'} needs your own reply: {why}")
        return
    bad = validate_draft(text)
    if bad:
        log_fn("RESUME_DRAFT_REJECTED", pn, f"{bad} :: {text.replace(chr(10), ' / ')[:160]}")
        notify_winfred_reason = (
            f"{name} ({pn}), {listing_key or 'no listing'} needs your own reply "
            f"(drafted line rejected: {bad}). Last messages:\n"
            + (" | ".join(f"{m['who']}: {m['text']}" for m in transcript[-3:]) or "(no recent text)"))
        notify_fn(notify_winfred_reason)
        return
    did = new_draft(pn, jid, listing_key, text)
    log_fn("RESUME_DRAFT", pn, f"{did} :: {text.replace(chr(10), ' / ')}")
    notify_fn(f"Draft reply for {name} ({pn}), {listing_key or 'no listing'}:\n{text}\n\n"
              f"To send it, WhatsApp yourself: /send {did}. Or reply to them directly.")


# ---------- /send <id> and /drop <id>, only from Winfred's own self chat ----------
_SEND_RE = re.compile(r"^/send\s+([0-9a-f]{6,10})\s*$", re.I)
_DROP_RE = re.compile(r"^/drop\s+([0-9a-f]{6,10})\s*$", re.I)


def handle_self_chat_command(jid, text, send_fn, guard_reserve_fn, log_fn):
    """A from_me row in Winfred's OWN self chat. Returns True if this row was a recognised
    command (handled or deliberately ignored) so the runner never routes a self chat row
    into the tenant pipeline. '/send'/'/drop' typed in any OTHER chat is not seen here at
    all -- that row is a real message already visible to whoever is in that chat, and the
    runner's normal per-row skip for OWN_JID keeps this function from ever being asked
    about it."""
    t = (text or "").strip()
    m_send, m_drop = _SEND_RE.match(t), _DROP_RE.match(t)
    if not (m_send or m_drop):
        return False
    did = (m_send or m_drop).group(1).lower()
    d = find_draft(did)
    if not d:
        log_fn("SEND_CMD_UNKNOWN", jid, did)
        return True
    if m_drop:
        mark_draft(did, "dropped")
        log_fn("DRAFT_DROPPED", jid, did)
        return True
    # /send
    if d.get("status") != "pending":
        log_fn("SEND_CMD_STALE", jid, f"{did} status={d.get('status')}")
        return True
    if time.time() - d.get("created", 0) > DRAFT_EXPIRY_SEC:
        mark_draft(did, "expired")
        log_fn("SEND_CMD_EXPIRED", jid, did)
        return True
    bad = validate_draft(d.get("text"))
    if bad:
        mark_draft(did, "rejected")
        log_fn("SEND_CMD_REJECTED", jid, f"{did} :: {bad}")
        return True
    if not guard_reserve_fn(d["jid"]):
        log_fn("SEND_CMD_GUARD_SKIP", jid, did)
        return True
    ok = send_fn(d["jid"], d["text"])
    mark_draft(did, "sent" if ok else "send_failed")
    log_fn("DRAFT_SENT" if ok else "DRAFT_SEND_FAIL", d["pn"], did)
    return True
