"""
wa_intake_owner.py -- the landlord side of the loop: "let me check with the owner" needs
someone to actually ask the owner, wait, and close the loop with the tenant. Winfred's own
words: "Always remember to treat the owner like a VIP."

Kept as a NEW module, not folded into intake_engine.py or wa_intake_runner.py, so the two
other branches working the tenant side (attack fixes, playbook replies) never conflict with
this file. wa_intake_runner.py only gets a couple of guarded call sites (see the bottom of
this docstring and the runner's own comments) -- everything else lives here.

Winfred's decisions, enforced in code (not just described) because this file is the ONLY
place an owner ever gets an automated WhatsApp message:
  - auto send FACTUAL owner questions only, VIP tone, ONE consolidated message per owner
    per day, only 09:00-21:00 SGT
  - never about commission, price or rent; never a tenant's name/phone/nationality/
    ethnicity/budget; never asks an owner to state a race/nationality/religion/gender rule
  - everything else is a draft for Winfred's /send (reuses wa_intake_resume's drafts.jsonl)

Queue file: one JSON object per line, fields id/landlord_id/listing_key/question_code/
question_text/source/created/status/asked_at/answer/evidence (plus optional source_jid,
chased_at -- documented below). status moves queued -> sent -> answered|chased -> expired,
or queued -> drafted (unclear landlord reply) any time after 'sent'.

HOOK for the category-2 reply layer (playbook branch, after merge): whenever it answers a
tenant with "let me check with the owner", call

    import wa_intake_owner as OWN
    OWN.enqueue_owner_question(landlord_id, listing_key, question_code, question_text,
                                source=tenant_pn, source_jid=tenant_jid)

question_code must be one of OWN.QUESTION_CODES. The function itself re-checks the FACTUAL/
VIP rules above (never trusts the caller) and silently drops (logging why) anything that
would violate them -- so a bug on the other branch can queue a bad question but can never
get Winfred's number to actually SEND it to an owner.
"""
import os, re, json, time, hashlib, datetime, subprocess

QUEUE_FILE = os.path.expanduser("~/.claude/state/listing-templates/owner-questions.jsonl")
LANDLORD_DB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
REFRESH_LOCK_DIR = os.path.expanduser("~/.claude/state/refresh-rental-dbs.lock.d")
REFRESH_LOCK_STALE_SEC = 30 * 60

QUESTION_CODES = ("PAX", "WIFI", "AIRCON", "UTILITIES", "MRT", "MOVE_IN", "VISITORS",
                  "COOKING", "PETS", "SMOKING", "AVAILABILITY", "VIEWING_WINDOW",
                  "HOUSE_RULES")

DAILY_CAP_PER_LANDLORD = 1              # ONE consolidated message per owner per day, hard
MAX_QUESTIONS_PER_MESSAGE = 3
ASK_WINDOW_START_MIN = 9 * 60           # 09:00 SGT
ASK_WINDOW_END_MIN = 21 * 60            # 21:00 SGT
NO_TALK_OVER_OUTBOUND_SEC = 60 * 60     # no send of ours in the last 60 minutes
NO_TALK_OVER_INBOUND_SEC = 6 * 60 * 60  # no unanswered inbound from him in the last 6 hours
ANSWER_WINDOW_SEC = 48 * 3600           # chase fires once this has elapsed with no answer
CHASE_GRACE_SEC = 48 * 3600             # then expire this long after the chase itself

# ---------- safety: never let a bad/borderline question reach an owner ----------
# Deliberately the same posture as wa_intake_resume.validate_draft: a false reject just
# costs Winfred one manual question; a false accept is a real VIP owner reading it.
_BANNED_TOPIC_RE = re.compile(
    r"commission|\bcomm\b|\bnego(?:tiat\w*)?\b|\bdiscount\b|\bcheaper\b|\blower\b|"
    r"\bdeposit\b|\bdispute\b|\brefund\b|\blegal\b|\blawyer\b|\bsue\b|\btribunal\b|"
    r"\brace\b|\bethnicity\b|\bnationality\b|\breligion\b|\bmuslim\b|\bchristian\b|"
    r"\bhindu\b|\bbuddhist\b|\bgender\b|\bmale only\b|\bfemale only\b|\bchinese only\b|"
    r"\bmalay\b|\bindian\b|\beurasian\b", re.I)
_MONEY_RE = re.compile(r"[$＄]\s*\d|\bsgd\b|\bs\$|\b\d{3,5}\s*(?:/|per\s*)?"
                       r"(?:mo|mth|month|pm|monthly)\b|\brent\b|\bprice\b|\bpricing\b|"
                       r"\basking\s+(?:rent|price)\b", re.I)
_TENANT_PII_RE = re.compile(
    r"\bhis name\b|\bher name\b|\bcalled\b.{0,20}\b(mr|ms|mdm)\b|\+65\s*\d|\b\d{8}\b", re.I)
_DASH_RE = re.compile(r"[-‐‑‒–—―－]")


def _question_is_safe(question_text):
    """None if the question is clean to ever send to an owner; otherwise a short reason.
    Checked at BOTH enqueue time (garbage in never even queues) and send time (defense in
    depth -- the queue file could in principle be edited by hand or by an older build)."""
    t = (question_text or "").strip()
    if not t:
        return "empty question"
    if _BANNED_TOPIC_RE.search(t):
        return "banned topic (commission/price/rent/deposit/race/nationality/religion/gender/dispute)"
    if _MONEY_RE.search(t):
        return "quotes or implies a figure"
    if _TENANT_PII_RE.search(t):
        return "may reveal tenant identity"
    return None


def _now_sgt():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def _today_sgt_str():
    return _now_sgt().strftime("%Y-%m-%d")


def _sgt_minute_of_day():
    n = _now_sgt()
    return n.hour * 60 + n.minute


def in_ask_window():
    return ASK_WINDOW_START_MIN <= _sgt_minute_of_day() < ASK_WINDOW_END_MIN


# ---------- queue persistence (same append-then-rewrite shape as drafts.jsonl) ----------
def _load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    out = []
    with open(QUEUE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue          # one corrupt line must not lose every other question
    return out


def _rewrite_queue(items):
    d = os.path.dirname(QUEUE_FILE)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, QUEUE_FILE)


def enqueue_owner_question(landlord_id, listing_key, question_code, question_text,
                            source="clarity-report", source_jid=None, log_fn=None):
    """THE hook (see module docstring). Returns the new entry's id, or None if the question
    was refused (logged via log_fn if given) -- never raises, so a caller mid tenant reply
    never crashes because a question turned out to be unsafe."""
    log_fn = log_fn or (lambda kind, who, msg: None)
    code = str(question_code or "").strip().upper()
    if code not in QUESTION_CODES:
        log_fn("OWNER_Q_REJECTED", landlord_id, f"unknown question_code {question_code!r}")
        return None
    bad = _question_is_safe(question_text)
    if bad:
        log_fn("OWNER_Q_REJECTED", landlord_id, f"{code} :: {bad} :: {(question_text or '')[:120]}")
        return None
    if not landlord_id:
        log_fn("OWNER_Q_REJECTED", landlord_id, "no landlord_id")
        return None
    qid = hashlib.sha1(f"{landlord_id}|{code}|{question_text}|{time.time()}".encode()).hexdigest()[:8]
    entry = {"id": qid, "landlord_id": landlord_id, "listing_key": listing_key,
              "question_code": code, "question_text": question_text.strip(),
              "source": source, "source_jid": source_jid, "created": time.time(),
              "status": "queued", "asked_at": None, "answer": None, "evidence": None}
    d = os.path.dirname(QUEUE_FILE)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(QUEUE_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log_fn("OWNER_Q_QUEUED", landlord_id, f"{qid} :: {code} :: {question_text.strip()[:120]}")
    return qid


def find_question(qid):
    for q in _load_queue():
        if q.get("id") == qid:
            return q
    return None


def mark_question(qid, status, **fields):
    items = _load_queue()
    changed = False
    for q in items:
        if q.get("id") == qid:
            q["status"] = status
            q.update(fields)
            changed = True
    if changed:
        _rewrite_queue(items)
    return changed


# ---------- landlord DB reads (fresh every call -- this module ticks once per minute,
# not per row, so there is no reason to cache and risk acting on a stale review_flag) ----------
def _load_landlord_db():
    try:
        return json.load(open(LANDLORD_DB))
    except Exception:
        return None


def landlord_by_id(lid, db=None):
    db = db if db is not None else _load_landlord_db()
    if db is None:
        return None
    for l in db.get("landlords", []):
        if str(l.get("id")) == str(lid):
            return l
    return None


def _landlord_eligible(l):
    """Active, no open review flag. Neither check trusts an absent field as a pass."""
    if not l:
        return False
    status = str(l.get("status") or "").strip().lower()
    if not status.startswith("active"):
        return False
    if l.get("review_flag"):
        return False
    return True


def _landlord_jid(l):
    cj = str(l.get("chat_jid") or "").strip()
    if cj:
        return cj
    ph = re.sub(r"\D", "", str(l.get("phone") or ""))
    return f"{ph}@s.whatsapp.net" if ph else None


# ---------- "never talk over him" ----------
def _recent_conversation_blocks_send(con, jid):
    """None if it is fine to message this owner right now; otherwise a short reason. Reads
    the SAME messages.db connection the runner already holds (read only in spirit -- this
    module never writes to it)."""
    if con is None or not jid:
        return "no db connection"
    try:
        # ORDER BY the table's own implicit rowid (insertion order), never the timestamp
        # column -- the bridge writes mixed +08:00/-04:00 offsets after Winfred travels, so
        # sorting by the raw timestamp STRING can rank an earlier local time above a later
        # one. rowid is monotonic regardless of what a row's own clock claims (same reason
        # wa_intake_resume.fetch_transcript orders by rowid, not timestamp).
        row = con.execute(
            "SELECT is_from_me, timestamp FROM messages WHERE chat_jid=? "
            "ORDER BY rowid DESC LIMIT 1", (jid,)).fetchone()
    except Exception:
        return "db_error"
    if not row:
        return None
    ifm, ts = row
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    except Exception:
        return None            # unparseable timestamp -> cannot judge, best effort, never blocks
    age = (_now_sgt() - dt).total_seconds()
    if ifm and age < NO_TALK_OVER_OUTBOUND_SEC:
        return f"we messaged him {int(age/60)}m ago"
    if not ifm and age < NO_TALK_OVER_INBOUND_SEC:
        return f"his own unanswered message is only {int(age/3600)}h old"
    return None


# landlord-db.json landlord_name sometimes carries an admin annotation in parentheses
# ("Kumar (proxy for landlord; brother-in-law is...)", "Jason Lim (Bespoke Habitat)") --
# never a greeting to actually send an owner. Use only the part before the first '(' (or
# comma), which is always the real name Winfred calls them by in his own chat history.
_NAME_ANNOTATION_RE = re.compile(r"\s*[(,].*$")


def _clean_owner_name(landlord_name):
    return _NAME_ANNOTATION_RE.sub("", (landlord_name or "").strip()).strip()


# ---------- VIP tone template ----------
def build_owner_message(landlord_name, questions):
    """questions: list of question_text (already safety-checked), max 3. Returns text, or
    None if the built message itself somehow fails the safety check (defense in depth)."""
    qs = [q.strip() for q in questions[:MAX_QUESTIONS_PER_MESSAGE] if (q or "").strip()]
    if not qs:
        return None
    name = _clean_owner_name(landlord_name)
    greeting = f"Hi {name}, thank you for your time" if name else "Hi, thank you for your time"
    lead = "A prospective tenant asked" if len(qs) == 1 else "A prospective tenant asked a few things"
    if len(qs) == 1:
        body = qs[0].rstrip("?") + "?"
    else:
        body = "\n".join(f"{i+1}. {q.rstrip('?')}?" for i, q in enumerate(qs))
    text = f"{greeting} \U0001F64F\n\n{lead}:\n{body}\n\nNo rush, whenever convenient \U0001F64F"
    if _DASH_RE.search(text):
        return None
    for q in qs:
        bad = _question_is_safe(q)
        if bad:
            return None
    return text


# ---------- sender tick ----------
def run_owner_asks(con, send_fn, guard_reserve_fn, log_fn, notify_fn):
    """Called once per runner tick. Sends AT MOST one consolidated message per landlord,
    only inside the 09:00-21:00 SGT window, only to an active/no-review-flag landlord with
    queued questions, no message already sent to them today, and no recent hand
    conversation either direction. Never raises -- a bad tick here must never touch the
    tenant pipeline's own watermark/state."""
    if not in_ask_window():
        return
    items = _load_queue()
    if not items:
        return
    db = _load_landlord_db()
    if db is None:
        log_fn("OWNER_ASK_SKIP", "-", "landlord-db.json unreadable, fail closed")
        return
    today = _today_sgt_str()
    by_landlord = {}
    for q in items:
        if q.get("status") == "queued":
            by_landlord.setdefault(q["landlord_id"], []).append(q)
    for lid, qs in by_landlord.items():
        already_today = any(
            it.get("landlord_id") == lid and it.get("asked_at")
            and str(it["asked_at"]).startswith(today) and it.get("status") != "queued"
            for it in items)
        if already_today:
            continue
        l = landlord_by_id(lid, db)
        if not _landlord_eligible(l):
            log_fn("OWNER_ASK_SKIP", lid, "not active or under review")
            continue
        jid = _landlord_jid(l)
        if not jid:
            log_fn("OWNER_ASK_SKIP", lid, "no phone/chat_jid on file")
            continue
        why = _recent_conversation_blocks_send(con, jid)
        if why:
            log_fn("OWNER_ASK_SKIP", lid, why)
            continue
        pick = sorted(qs, key=lambda q: q.get("created", 0))[:MAX_QUESTIONS_PER_MESSAGE]
        text = build_owner_message(l.get("landlord_name"), [q["question_text"] for q in pick])
        if not text:
            log_fn("OWNER_ASK_SKIP", lid, "message failed its own safety check")
            continue
        if not guard_reserve_fn(jid):
            log_fn("OWNER_ASK_SKIP", lid, "cross sender guard reserved elsewhere")
            continue
        ok = send_fn(jid, text)
        log_fn("OWNER_ASK" if ok else "OWNER_ASK_FAIL", lid, text.replace("\n", " / "))
        if ok:
            now_iso = _now_sgt().isoformat()
            for q in pick:
                mark_question(q["id"], "sent", asked_at=now_iso)
            notify_fn(f"Asked owner {l.get('landlord_name') or lid} ({len(pick)} question"
                      f"{'s' if len(pick) != 1 else ''}), no rush framing, will chase in 48h "
                      f"if quiet.")


# ---------- chase + expiry ----------
def run_owner_chases(con, send_fn, guard_reserve_fn, log_fn, notify_fn):
    """Never more than 2 messages per question: the original ask, then ONE gentle chase.
    48h after the chase with still no answer, the question expires and Winfred is flagged."""
    if not in_ask_window():
        return
    items = _load_queue()
    db = _load_landlord_db()
    if db is None:
        return
    now = time.time()
    by_landlord = {}
    for q in items:
        if q.get("status") in ("sent", "chased"):
            by_landlord.setdefault(q["landlord_id"], []).append(q)
    for lid, qs in by_landlord.items():
        l = landlord_by_id(lid, db)
        if not _landlord_eligible(l):
            continue
        jid = _landlord_jid(l)
        if not jid:
            continue
        to_expire = [q for q in qs if q.get("status") == "chased"
                     and _age_sec(q.get("chased_at")) is not None
                     and _age_sec(q.get("chased_at")) > CHASE_GRACE_SEC]
        for q in to_expire:
            mark_question(q["id"], "expired")
            log_fn("OWNER_Q_EXPIRED", lid, q["id"])
            notify_fn(f"No answer from {l.get('landlord_name') or lid} on: "
                      f"{q['question_text']}. Expired after one chase, reply by hand if "
                      f"you still need it.")
        to_chase = [q for q in qs if q.get("status") == "sent"
                    and _age_sec(q.get("asked_at")) is not None
                    and _age_sec(q.get("asked_at")) > ANSWER_WINDOW_SEC]
        if not to_chase:
            continue
        why = _recent_conversation_blocks_send(con, jid)
        if why:
            continue
        if not guard_reserve_fn(jid):
            continue
        text = "Just checking in when convenient \U0001F64F"
        ok = send_fn(jid, text)
        log_fn("OWNER_CHASE" if ok else "OWNER_CHASE_FAIL", lid, text)
        if ok:
            now_iso = _now_sgt().isoformat()
            for q in to_chase:
                mark_question(q["id"], "chased", chased_at=now_iso)


def _age_sec(iso_ts):
    if not iso_ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso_ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    except Exception:
        return None
    return time.time() - dt.timestamp()
