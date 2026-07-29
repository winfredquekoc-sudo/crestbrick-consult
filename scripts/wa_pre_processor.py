#!/usr/bin/env python3
"""
Pre-processor: reads WA messages DB + state files, outputs structured tasks for Claude.
Claude never touches state files — this script and wa_post_processor.py own all state.
"""
import sqlite3, json, os, sys, re as _re
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wa_send_guard import can_send as guard_can_send

DB_PATH    = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
STATE_DIR  = os.path.expanduser("~/.claude/state/listing-templates")
CONV_STATE = os.path.join(STATE_DIR, "conversation-state.json")
REPLIED_LOG= os.path.join(STATE_DIR, "replied-jids.json")
TEMPLATES  = os.path.join(STATE_DIR, "property-templates.json")
LANDLORD_DB= os.path.expanduser("~/.claude/state/wa-agent/landlord-db.json")
CAROUSELL  = os.path.expanduser("~/.claude/state/wa-agent/carousell-landlord-jids.json")
SKIP_FILE  = os.path.expanduser("~/.claude/state/wa-personal-skip.json")
LAST_RUN_FILE = os.path.join(STATE_DIR, "pre-processor-last-run.json")

SGT = timezone(timedelta(hours=8))

AGENT_KEYWORDS = [
    "huttons","era","propnex","orangetee","knight frank","sri","savills",
    "co broke","co-broke","cobroke","serving tenants","agent from","huttons agent","era agent",
    "represent","my client","my buyer","my tenant","serving a client",
    "commission","share comm","split comm","hfe","cea r0","willing to co",
    "i'm an agent","i am an agent","hi agents","agents,",
]

LANDLORD_SELL_KEYWORDS = [
    "looking to sell","want to sell","selling my","put up for sale",
    "find me a buyer","help me sell","selling a property","sell my flat",
    "sell my condo","sell my hdb","sell my unit","thinking of selling",
    "want to offload","en bloc","enbloc",
]

BUYER_BUY_KEYWORDS = [
    "looking to buy","want to buy","buying a","purchase a",
    "looking for a condo to buy","looking for hdb to buy","looking to upgrade",
    "upgrade from hdb","first time buyer","first-time buyer",
    "want to purchase","keen to buy","interested to buy",
]


def classify_quick(content):
    """Fast keyword classification. Returns COBROKE, LANDLORD_SELL, BUYER_BUY, or None."""
    c = content.lower()
    if any(k in c for k in AGENT_KEYWORDS): return "COBROKE"
    if any(k in c for k in LANDLORD_SELL_KEYWORDS): return "LANDLORD_SELL"
    if any(k in c for k in BUYER_BUY_KEYWORDS): return "BUYER_BUY"
    return None

# Possessive keywords — sender clearly OWNS the property. No guard needed.
LANDLORD_POSSESSIVE = [
    "my room","my unit","my flat","my condo","my hdb","my place","my property","my master",
    "my common room","my listing","my ad","my post",
    "i have a room","i have a master","i have a common","i have a unit","i have a flat",
    "i have rooms","i have 2 room","i have 3 room","i have spare room","i have extra room",
    "i have 1 room","i have 4 room","i have 5 room",
    "got room","got master","got common room","got unit","got flat","got a room",
    "got 1 room","got 2 room","got 3 room","got spare","got extra room",
    "have master room","have common room",
    "renting out","renting my","rent my room","rent my unit","rent my flat",
    "want to rent out","wanna rent out","hope to rent out","looking to rent out",
    "i am renting out","i am the owner","i am owner","unit owner","room owner",
    "i am the landlord","i own","i own a","owner here",
    "looking for tenant","find me tenant","need tenant",
    "help find tenant","help me rent out","help rent out",
    "can you help rent","can you list","help me list","list my room","list my unit",
    "any update on my","any tenant for my","tenants for my",
    "any tenant yet","any lobang","how many enquiries",
    "any potential tenant","any prospect for",
    "prefer tenant","preferred tenant","suitable tenant","tenant preference",
    "still available for rent","room is still available","room is available",
    "unit is still available","unit is available","place is available",
    "still available, prefer","available, prefer",
    "may i know ur charge","may i know your charge","what is your commission",
    "what is the commission","commission per year","per year commission",
    "ready tenant","willing to rent","you contacted me","contacted me through carousell",
    "i have a property","looking for tenant","need tenant","any tenant for",
    "corporate tenant","company rent","3 individual","agent for my",
]

# Action keywords — could be landlord OR tenant asking. Apply false positive guard.
LANDLORD_ACTION = [
    "for rent","for rental",
    "room for rent","master for rent","common for rent","bedroom for rent",
    "unit for rent","flat for rent","studio for rent","place for rent",
    "en suite for rent","ensuite for rent",
    "available for rent","available now","vacant now","vacant from",
    "room available","unit available","flat available","place available",
    "available immediately","immediate occupancy",
    "find tenant","any tenant","any interested","any viewing","any news","any update",
]

# If message matches these, it is a TENANT asking — do not flag as landlord
TENANT_QUESTION_GUARD = [
    "is the room still available","is it still available","is there still",
    "any room available","room still available","rooms available",
    "is there a room for rent","do you have room","do you have any room",
    "looking for room","looking for a room","searching for room","need a room",
    "i am looking","i am interested","i'm interested","i'm looking",
    "is it available","still available?","still avail?",
    "want to rent","would like to rent","keen to rent","hope to rent",
    "enquire about","enquiring about","enquiry for",
]

KNOWN_LANDLORD_NAMES = [
    "ken ho","li xian","buva","rachel","fei","keith ng","frank","kirinkala",
    "juneiyana","yuling","normawati","lee"
]

# ── Profile extraction data (inlined from wa_prospect_agent.py) ──────────────
_NATIONALITY_WORDS = {
    "singaporean": "Singaporean",
    "malaysian": "Malaysian",
    "indonesian": "Indonesian",
    "vietnamese": "Vietnamese",
    "filipino": "Filipino",
    "korean": "Korean",
    "japanese": "Japanese",
    "burmese": "Burmese",
    "thai": "Thai",
    "australian": "Australian",
    "american": "American",
    "british": "British",
    "from malaysia": "Malaysian",
    "from china": "Chinese",
    "from india": "Indian",
    "from korea": "Korean",
    "from japan": "Japanese",
    "from indonesia": "Indonesian",
    "from vietnam": "Vietnamese",
    "from philippines": "Filipino",
    "from thailand": "Thai",
    "from myanmar": "Burmese",
    "from uk": "British",
    "from australia": "Australian",
    "from usa": "American",
    "malaysia": "Malaysian",
    "china": "Chinese",
    "india": "Indian",
    "philippines": "Filipino",
    "indonesia": "Indonesian",
    "vietnam": "Vietnamese",
    "myanmar": "Burmese",
    "uk": "British",
    "australia": "Australian",
    "usa": "American",
}

_PASS_WORDS = {
    " sc ": "SC", " sc,": "SC", "singaporean citizen": "SC",
    " pr ": "PR", " pr,": "PR", "permanent resident": "PR",
    " ep ": "EP", "employment pass": "EP",
    " s pass": "S Pass", "spass": "S Pass",
    "student pass": "STP", " stp": "STP",
    "work permit": "Work Permit",
    " dp ": "DP", "dependent pass": "DP",
    " ltvp": "LTVP",
}

_GENDER_MALE   = ["male", " man ", " men ", "gentleman", "i'm a guy", "i am a guy", "i'm male", "he/him"]
_GENDER_FEMALE = ["female", " woman ", " women ", "lady", "ladies", "i'm a girl", "i am a girl", "i'm female", "she/her"]


def extract_profile(history):
    """
    Parse conversation history and return a dict of known profile fields.
    Works on both form-filled and casual/inline text.
    Returns keys: name, nationality, pass_type, gender, age, pax, move_in, budget, lease_term, occupation, pets, smoking.
    Values are strings or None.
    Inlined from wa_prospect_agent.py to remove dependency.
    """
    # Collect only inbound text
    text_all = " ".join(
        (m.get("content") or "") for m in history if not m.get("is_from_me")
    )
    text_lower = text_all.lower()
    p = {}

    # ── Name ────────────────────────────────────────────────────────────────
    m = _re.search(r'(?:•\s*|name\s*[:\-]\s*)([A-Za-z][A-Za-z ]{1,35})', text_all, _re.IGNORECASE)
    if m:
        candidate = m.group(1).strip().split("\n")[0].strip("•⁠ \t")
        if candidate.lower() not in {"name", "n/a", "nil", "hi", "hello", "there", "winfred"}:
            p["name"] = " ".join(candidate.split()[:2])

    # ── Nationality ──────────────────────────────────────────────────────────
    for kw, val in _NATIONALITY_WORDS.items():
        if kw in text_lower:
            p["nationality"] = val
            break

    # ── Pass type ────────────────────────────────────────────────────────────
    for kw, val in _PASS_WORDS.items():
        if kw in text_lower:
            p["pass_type"] = val
            break
    if not p.get("pass_type") and p.get("nationality") == "Singaporean":
        p["pass_type"] = "SC"
    if p.get("pass_type") == "SC" and not p.get("nationality"):
        p["nationality"] = "Singaporean"

    # ── Gender ───────────────────────────────────────────────────────────────
    if any(kw in text_lower for kw in _GENDER_FEMALE):
        p["gender"] = "Female"
    elif any(kw in text_lower for kw in _GENDER_MALE):
        p["gender"] = "Male"
    gm = _re.search(r'gender\s*[:\-]\s*(male|female|m\b|f\b)', text_lower)
    if gm:
        p["gender"] = "Female" if gm.group(1).startswith("f") else "Male"

    # ── Age ──────────────────────────────────────────────────────────────────
    am = _re.search(r'\b(1[89]|[2-5]\d)\s*(?:year[s]?\s*old|yo\b|y\.?o\.?)', text_lower)
    if not am:
        am = _re.search(r'age\s*[:\-]\s*(\d{2})', text_lower)
    if am:
        p["age"] = am.group(1)

    # ── Pax (number of occupants) ────────────────────────────────────────────
    pm = _re.search(r'(?:pax|occupant|person|people)\s*[:\-]\s*(\d)', text_lower)
    if pm:
        p["pax"] = pm.group(1)
    else:
        if any(kw in text_lower for kw in ["just me", "just myself", "only me", "myself only", "1 pax", "1 person", "1 people", "one person", "alone", "by myself", "stay alone"]):
            p["pax"] = "1"
        elif any(kw in text_lower for kw in ["2 pax", "2 person", "2 people", "two person", "two people", "couple", "my partner", "my wife", "my husband", "my boyfriend", "my girlfriend", "both of us", "2 of us"]):
            p["pax"] = "2"
        elif any(kw in text_lower for kw in ["3 pax", "3 person", "3 people", "family of 3", "3 of us"]):
            p["pax"] = "3"
        elif any(kw in text_lower for kw in ["4 pax", "4 person", "4 people", "family of 4", "4 of us"]):
            p["pax"] = "4"

    # ── Pets ─────────────────────────────────────────────────────────────────
    if _re.search(r'no\s*pet|without\s*pet|don.t have.*pet|no animal|not have.*pet', text_lower):
        p["pets"] = "no"
    elif _re.search(r'\b(dog|cat|rabbit|hamster|bird|golden retriever|labrador|poodle|corgi|husky)\b', text_lower):
        p["pets"] = "yes"
    elif _re.search(r'\bpet\b', text_lower) and not _re.search(r'no\s*pet|without pet', text_lower):
        p["pets"] = "yes"

    # ── Smoking ──────────────────────────────────────────────────────────────
    sm_field = _re.search(r'smok(?:er|ing)?\s*[:\-]\s*(yes|no|y\b|n\b)', text_lower)
    if sm_field:
        p["smoking"] = "no" if sm_field.group(1).startswith("n") else "yes"
    elif _re.search(r'\bnon.?smok|\bdon.t smok|\bdo not smok|\bnot.a.smok|no\s+smok', text_lower):
        p["smoking"] = "no"
    elif _re.search(r'\bsmok', text_lower):
        p["smoking"] = "yes"

    # ── Move-in date ─────────────────────────────────────────────────────────
    if any(kw in text_lower for kw in ["immediate", "asap", "right away", "as soon as possible", "now", "straight away"]):
        p["move_in"] = "Immediate"
    else:
        dm = _re.search(
            r'(?:move[- ]?in|available|start|from|by)\s*(?:date\s*[:\-]\s*)?'
            r'((?:early|mid|end\s+of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*'
            r'(?:\s+\d{4})?|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*'
            r'|\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?)',
            text_lower
        )
        if not dm:
            dm = _re.search(
                r'(?:in|on|from|by|around|end of|early|mid)\s+'
                r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(?:\d{4})?\b',
                text_lower
            )
        if not dm:
            dm = _re.search(
                r'(?:move.in|move in|intended move)\s*(?:date)?\s*[:\-]\s*'
                r'(?:\d{1,2}(?:st|nd|rd|th)?\s+)?'
                r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*',
                text_lower
            )
        if dm:
            p["move_in"] = dm.group(1).capitalize()

    # ── Budget ───────────────────────────────────────────────────────────────
    bm = _re.search(r'\$\s*(\d[\d,]+)(?:\s*(?:to|-)\s*\$?\s*(\d[\d,]+))?', text_lower)
    if not bm:
        bm = _re.search(r'(\d[\d,]+)\s*(?:sgd|per month|\/mo|a month|pm\b)', text_lower)
    if bm:
        p["budget"] = bm.group(0).strip()

    # ── Lease term ───────────────────────────────────────────────────────────
    lm = _re.search(r'(\d+)\s*(?:year|yr)s?\s*(?:lease|tenancy|contract|term)', text_lower)
    if not lm:
        lm = _re.search(r'(?:lease|tenancy|stay|rent)\s*(?:for\s*)?(\d+)\s*(?:month|year|yr)', text_lower)
    if not lm:
        mm = _re.search(r'\b(\d+)\s*months?\b', text_lower)
        if mm and int(mm.group(1)) <= 24:
            lm = mm
    if lm:
        p["lease_term"] = lm.group(0).strip()

    # ── Occupation ───────────────────────────────────────────────────────────
    _OCC_STOP = {"the", "an", "a", "looking", "interested", "hoping", "trying",
                 "planning", "non", "not", "singaporean", "malaysian", "chinese",
                 "indian", "korean", "japanese", "filipino", "vietnamese", "thai"}
    om = _re.search(r'(?:occupation|work(?:ing)?\s*(?:as|in|at)|i(?:\'m| am)\s+(?:a|an))\s+([a-z][a-z ]{2,30})', text_lower)
    if om:
        occ = om.group(1).strip().rstrip(".,").split()[0]
        if occ not in _OCC_STOP and len(occ) > 2:
            words = om.group(1).strip().rstrip(".,").split()
            p["occupation"] = " ".join(words[:3]).title()
    if not p.get("occupation"):
        if _re.search(r'\b(phd|doctoral|postdoc|researcher)\b', text_lower):
            p["occupation"] = "PhD/Researcher"
        elif "student" in text_lower:
            p["occupation"] = "Student"

    return {k: v for k, v in p.items() if v}


def load_json(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except:
            pass
    return default

def is_individual(jid):
    return jid.endswith("@lid") or jid.endswith("@s.whatsapp.net")

def db_query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def recent_msgs(jid, limit=20):
    rows = db_query(
        "SELECT sender, content, timestamp, is_from_me FROM messages "
        "WHERE chat_jid=? ORDER BY timestamp DESC LIMIT ?", (jid, limit)
    )
    return list(reversed(rows))

def _catchup_minutes() -> int:
    """
    Return how far back Phase A should scan for new messages.
    Normally 20 min (safe overlap with 10-min launchd interval).
    If the bot was paused/crashed, returns minutes since last run + 5 min buffer,
    capped at 8 hours so we never flood Claude with a full day of messages.
    """
    try:
        data = json.load(open(LAST_RUN_FILE))
        last = datetime.fromisoformat(data["last_run_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = int((datetime.now(timezone.utc) - last).total_seconds() / 60) + 5
        return min(max(elapsed, 20), 480)   # floor 20, cap 480 (8 h)
    except Exception:
        return 60   # first run or corrupt file — scan last hour


def _record_run():
    """Write current UTC time as last-run timestamp."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LAST_RUN_FILE, "w") as f:
        json.dump({"last_run_at": datetime.now(timezone.utc).isoformat()}, f)


def inbound_since(minutes=15, query_str=None, limit=50):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S+00:00")
    sql = "SELECT chat_jid, sender, content, timestamp, is_from_me FROM messages WHERE is_from_me=0 AND timestamp>?"
    params = [cutoff]
    if query_str:
        sql += " AND content LIKE ?"
        params.append(f"%{query_str}%")
    sql += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    return db_query(sql, params)

_FORM_SENT_RE = __import__('re').compile(
    r'could you help me (fill|answer)|profile information|'
    r'property details.*rental terms|owner name:|asking rent|preferred gender|'
    r'thanks for reaching out.*full address|reason for selling|outstanding mortgage|'
    r'name:.*nationality:.*gender:|no\.\s*of\s*pax|'
    r'pls fill this in|fill this in so i can|send your profile to the landlord|'
    r'CEA R073319H|viewing slots:',
    __import__('re').I | __import__('re').S
)

def form_already_sent(jid, lookback_days=14):
    """Returns True if a form was already sent to this JID.

    Primary check: conversation-state.json — if the JID has any entry here,
    the bot already engaged this prospect. This is persistent and has no
    message-count limit (fixes the 60-message cliff).

    Fallback: regex scan of recent messages to catch cases where state was
    written but the file was lost or corrupted.
    """
    # Primary: state file (reliable, no message-count limit)
    try:
        state = load_json(CONV_STATE, {"conversations": {}})
        if jid in state.get("conversations", {}):
            return True
    except Exception:
        pass

    # Fallback: regex scan (covers state-file loss; capped at 30 msgs, not 60)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S+00:00")
    rows = db_query(
        "SELECT content FROM messages WHERE chat_jid=? AND timestamp>? ORDER BY timestamp DESC LIMIT 30",
        (jid, cutoff)
    )
    return any(_FORM_SENT_RE.search(r.get("content") or "") for r in rows)

def winfred_replied_manually(jid, minutes=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S+00:00")
    rows = db_query(
        "SELECT COUNT(*) as n FROM messages WHERE chat_jid=? AND is_from_me=1 AND timestamp>?",
        (jid, cutoff)
    )
    return rows[0]["n"] > 0 if rows else False

def is_landlord_signal(jid, content, carousell_set, landlord_db, all_recent=None):
    content_lower = (content or "").lower()

    # Signal 1: carousell blocklist
    if jid in carousell_set:
        return True, "carousell"

    # Signal 2: known by JID in DB
    if jid in landlord_db:
        return True, "known_db"

    # Signal 3: known name in outbound thread
    if all_recent:
        for m in all_recent:
            if m.get("is_from_me") and m.get("content"):
                mc = m["content"].lower()
                if any(n in mc for n in KNOWN_LANDLORD_NAMES):
                    return True, "known_name"

    # Signal 4a: possessive keywords — unambiguous, no guard needed
    if any(k in content_lower for k in LANDLORD_POSSESSIVE):
        return True, "keyword"

    # Signal 4b: action keywords — apply false positive guard first
    if any(g in content_lower for g in TENANT_QUESTION_GUARD):
        return False, None
    if any(k in content_lower for k in LANDLORD_ACTION):
        return True, "keyword"

    return False, None

def is_agent(content):
    content_lower = (content or "").lower()
    return any(k in content_lower for k in AGENT_KEYWORDS)

def parse_ts(ts_str: str) -> datetime:
    """Parse any DB timestamp string regardless of timezone offset."""
    return datetime.fromisoformat(ts_str.strip().replace(" ", "T"))


# Patterns that identify bot-sent messages appearing as is_from_me=0 due to WA bridge quirk.
# If a message matches any of these, it is our own message — not a prospect reply.
_BOT_MESSAGE_SIGNALS = [
    "could you help me fill this in",
    "pls help me fill this in",
    "hi! thanks for reaching out",
    "hi! the jellicoe road room",
    "hi! the caspian",
    "hi! the rivervale",
    "hi! the farrer park",
    "hi! the bedok north",
    "hi! the pine grove",
    "hi! the hougang",
    "hi! the tampines",
    "hi! the oxley edge",
    "hi! the edgefield",
    "hi! the sumang",
    "hi! the admiralty",
    "hi! the treasure",
    "hi! the 339a",
    "winfred quek | cea r073319h",
    "whatsapp.com/channel/0029vbcowrs4",
    "could you help me answer these questions so i can screen",
    "sorry, the landlord looking for",
    "sorry, profile not a match",
    "would you be keen to view this unit instead",
    "i have a few others, let me check what fits",
]

def _is_bot_message(content: str) -> bool:
    """True if this message was sent by the bot (appears as is_from_me=0 due to bridge quirk)."""
    c = (content or "").lower()
    return any(sig in c for sig in _BOT_MESSAGE_SIGNALS)


def needs_reply(jid, conversations, last_minutes=30):
    """
    Return True only if the prospect has sent a new message since the last reply.

    Uses the LATER of:
      - last_bot_reply stored in conversation-state (with 30s grace for DB write lag)
      - the most recent is_from_me=1 message in the DB (Winfred manual reply)

    Also skips messages that match bot template patterns — these are our own
    messages that appear as is_from_me=0 due to the WA bridge sync quirk.
    """
    msgs = recent_msgs(jid, limit=30)
    if not msgs:
        return False

    c = conversations.get(jid, {})
    last_bot_str = c.get("last_bot_reply")

    # Candidate 1: last_bot_reply from state + 240s grace period (covers 240s launchd interval)
    lb_state = None
    if last_bot_str:
        try:
            lb_state = parse_ts(last_bot_str)
            if lb_state.tzinfo is None:
                lb_state = lb_state.replace(tzinfo=SGT)
            lb_state = lb_state + timedelta(seconds=240)
        except Exception:
            lb_state = None

    # Candidate 2: most recent is_from_me=1 message in DB (Winfred manual reply)
    lb_db = None
    for m in reversed(msgs):
        if m["is_from_me"]:
            try:
                lb_db = parse_ts(m["timestamp"])
                if lb_db.tzinfo is None:
                    lb_db = lb_db.replace(tzinfo=timezone.utc)
            except Exception:
                pass
            break

    # Use the LATER of the two
    candidates = [t for t in [lb_state, lb_db] if t is not None]
    if not candidates:
        return any(not m["is_from_me"] and not _is_bot_message(m.get("content","")) for m in msgs)
    lb = max(candidates)

    # Find any genuine inbound message that arrived AFTER the last reply,
    # skipping messages that are actually the bot's own reflected messages.
    for m in reversed(msgs):
        if m["is_from_me"]:
            continue
        if _is_bot_message(m.get("content", "")):
            continue
        try:
            msg_ts = parse_ts(m["timestamp"])
            if msg_ts.tzinfo is None:
                msg_ts = msg_ts.replace(tzinfo=timezone.utc)
            if msg_ts > lb:
                return True
        except Exception:
            continue

    return False

def main():
    conv_state  = load_json(CONV_STATE, {"conversations": {}})
    conversations = conv_state.get("conversations", {})
    replied_set = set(load_json(REPLIED_LOG, {"replied": []}).get("replied", []))
    templates   = load_json(TEMPLATES, {"listings": []})
    landlord_db = load_json(LANDLORD_DB, {})
    carousell_set = set(load_json(CAROUSELL, []))
    personal_skip = set(load_json(SKIP_FILE, {}).keys())

    # paused_jids: used only for Phase A (new first-touch messages).
    # Phase B relies on winfred_replied_manually() — the 60-min rolling window is the
    # correct gate there, so we do NOT apply paused_jids to Phase B at all.
    # done=True alone means prospect_agent handed off — transparent to autoreply.
    prospect_state = load_json(
        os.path.expanduser("~/.claude/state/wa-agent/prospect_state.json"), {}
    )
    paused_jids = {
        jid for jid, ps in prospect_state.items()
        if ps.get("paused_by")   # explicit Winfred takeover — Phase A only
    }

    tasks = []
    seen  = set()
    now_sgt = datetime.now(SGT)

    # ── Phase A0: landlord cold inbound ─────────────────────────────────
    ll_queries = [
        "for rent","got room","have room","rent out","find tenant","room available",
        "ready tenant","willing to rent","carousell","you contacted me",
        "3 individual","3 pax","looking for tenant","need tenant","any tenant",
        "corporate tenant","company rent","agent for","i have a property",
    ]
    _lookback = _catchup_minutes()
    ll_candidates = {}
    for q in ll_queries:
        for m in inbound_since(minutes=_lookback, query_str=q, limit=30):
            jid = m["chat_jid"]
            if is_individual(jid) and jid not in personal_skip and jid not in ll_candidates:
                ll_candidates[jid] = m

    _ll_cap = 10 if _lookback > 20 else 3   # raise cap during catchup
    for jid, m in list(ll_candidates.items())[:_ll_cap]:
        if jid in seen or jid in replied_set or jid in conversations:
            continue
        if jid in paused_jids:
            continue
        if not guard_can_send(jid):
            continue
        if form_already_sent(jid):
            seen.add(jid)
            continue
        content = m.get("content","")
        if is_agent(content):
            continue
        msgs = recent_msgs(jid, limit=10)
        is_ll, ll_type = is_landlord_signal(jid, content, carousell_set, landlord_db, msgs)
        if not is_ll:
            continue
        tasks.append({
            "phase": "A0",
            "jid": jid,
            "trigger_message": content,
            "recent_messages": msgs,
            "is_known_landlord": True,
            "landlord_type": ll_type,
            "already_in_carousell_list": jid in carousell_set,
        })
        seen.add(jid)

    # ── Phase A: new tenant enquiries ────────────────────────────────────
    # Portal keywords (propertyguru / 99.co) are intentionally broad — they match
    # both rent and buy enquiries. Portal messages are secondary-classified below
    # to confirm they are rent enquiries before queuing as Phase A.
    import re as _re

    def _is_portal_buy(content):
        """Return True if a portal message contains 'sale' but not 'rent' — it's a buy enquiry."""
        c = (content or "").lower()
        if not any(p in c for p in ["propertyguru", "99.co"]):
            return False
        has_rent = bool(_re.search(r'\brent\b', c))
        has_sale = bool(_re.search(r'\bsale\b', c))
        return has_sale and not has_rent

    tenant_queries = [
        "I am interested in","interested in renting","still available",
        "can I view","when can I view","is it available","how much is the",
        "propertyguru","99.co","viewing","want to rent","looking for room",
    ]
    a_candidates = {}
    for q in tenant_queries:
        for m in inbound_since(minutes=_lookback, query_str=q, limit=50):
            jid = m["chat_jid"]
            if not is_individual(jid) or jid in personal_skip:
                continue
            if jid in replied_set or jid in conversations or jid in paused_jids:
                continue
            if jid not in a_candidates:
                a_candidates[jid] = m

    _a_cap = 15 if _lookback > 20 else 5   # raise cap during catchup
    for jid, m in list(a_candidates.items())[:_a_cap]:
        if jid in seen:
            continue
        if not guard_can_send(jid):
            continue
        if form_already_sent(jid):
            seen.add(jid)
            continue
        # Rate limit Phase A: skip if bot replied within 150 seconds (covers 120s interval + buffer)
        if jid in conversations:
            last_bot = conversations[jid].get("last_bot_reply", "")
            if last_bot:
                try:
                    lb = datetime.fromisoformat(last_bot)
                    if lb.tzinfo is None:
                        lb = lb.replace(tzinfo=SGT)
                    if (now_sgt - lb).total_seconds() < 150:
                        continue
                except:
                    pass
        content = m.get("content","")
        msgs = recent_msgs(jid, limit=15)
        all_content = " ".join(msg.get("content","") or "" for msg in msgs if not msg.get("is_from_me"))

        # Portal buy guard — portal messages with \bsale\b but not \brent\b are buyer enquiries.
        # Route them to A_BUY instead of treating as rental.
        if _is_portal_buy(content) or _is_portal_buy(all_content):
            if guard_can_send(jid):
                tasks.append({
                    "phase": "A_BUY",
                    "jid": jid,
                    "trigger_message": content,
                    "recent_messages": msgs,
                })
            seen.add(jid)
            continue

        # Classify early — skip cobrokes and buyers, route sellers separately
        enquiry_type = classify_quick(all_content) or classify_quick(content)
        if enquiry_type == "COBROKE":
            seen.add(jid)
            continue
        if enquiry_type == "BUYER_BUY":
            if form_already_sent(jid):
                seen.add(jid)
                continue
            if guard_can_send(jid):
                tasks.append({
                    "phase": "A_BUY",
                    "jid": jid,
                    "trigger_message": content,
                    "recent_messages": msgs,
                })
            seen.add(jid)
            continue
        if enquiry_type == "LANDLORD_SELL":
            tasks.append({
                "phase": "A_SELL",
                "jid": jid,
                "trigger_message": content,
                "recent_messages": msgs,
            })
            seen.add(jid)
            continue

        is_ll, ll_type = is_landlord_signal(jid, all_content, carousell_set, landlord_db, msgs)
        manual = winfred_replied_manually(jid, minutes=30)

        prior_listings = []
        for r in replied_set:
            if isinstance(r, dict) and r.get("jid") == jid:
                prior_listings.append(r.get("listing"))
        hunter = len(set(prior_listings)) >= 3

        tasks.append({
            "phase": "A",
            "jid": jid,
            "trigger_message": content,
            "recent_messages": msgs,
            "is_landlord": is_ll,
            "landlord_type": ll_type,
            "winfred_already_replied": manual,
            "property_hunter": hunter,
        })
        seen.add(jid)

    # ── Phase B: ongoing conversations ───────────────────────────────────
    skip_stages = {"viewing_confirmed","handed_off","dead","no_show",
                   "viewing_requested","video_call_requested"}

    # Stale cleanup
    stale_jids = []
    for jid, c in conversations.items():
        stage = c.get("stage","")
        last_bot = c.get("last_bot_reply","")
        if stage in ("dead","handed_off","no_show") and last_bot:
            try:
                lb = datetime.fromisoformat(last_bot)
                if lb.tzinfo is None:
                    lb = lb.replace(tzinfo=SGT)
                if (now_sgt - lb).days > 30:
                    stale_jids.append(jid)
            except:
                pass
        # Auto-dead: no reply in 14 days
        elif stage not in skip_stages and last_bot:
            try:
                lb = datetime.fromisoformat(last_bot)
                if lb.tzinfo is None:
                    lb = lb.replace(tzinfo=SGT)
                if (now_sgt - lb).days > 14:
                    conversations[jid]["stage"] = "dead"
            except:
                pass

    b_count = 0
    for jid, c in conversations.items():
        if jid in seen or jid in personal_skip or jid in carousell_set:
            continue
        # Phase B does NOT check paused_jids — winfred_replied_manually() below handles
        # the "Winfred is active" gate with a rolling 60-min window.
        if not guard_can_send(jid):
            continue
        if c.get("stage") in skip_stages:
            continue

        # Skip if Winfred manually replied in the last 60 minutes — he owns the chat
        if winfred_replied_manually(jid, minutes=60):
            continue

        # Rate limit: skip if bot replied within 9 minutes
        last_bot = c.get("last_bot_reply","")
        if last_bot:
            try:
                lb = datetime.fromisoformat(last_bot)
                if lb.tzinfo is None:
                    lb = lb.replace(tzinfo=SGT)
                if (now_sgt - lb).total_seconds() < 150:
                    continue
            except:
                pass

        if not needs_reply(jid, conversations):
            continue

        msgs = recent_msgs(jid, limit=30)

        # ── Always extract & merge profile from messages ───────────────────
        # Run extract_profile on ALL Phase B tasks regardless of stage.
        # This catches casual replies ("im indian female EP, budget 1200") that
        # don't match the structured form. Merge with stored profile so Claude
        # always has the most complete picture and never asks for fields already given.
        try:
            extracted = extract_profile(msgs)
            if extracted:
                stored = conversations[jid].get("profile") or {}
                # Merge: extracted fills gaps, stored takes precedence for existing fields
                merged = {**extracted, **{k: v for k, v in stored.items() if v}}
                conversations[jid]["profile"] = merged
                c = conversations[jid]
                # Auto-advance stage if profile now complete
                PROFILE_CORE = {"name", "nationality", "gender", "age", "pass_type", "pax"}
                filled = sum(1 for f in PROFILE_CORE if merged.get(f))
                if c.get("stage") == "template_sent" and filled >= 3:
                    conversations[jid]["stage"] = "profile_received"
                    c = conversations[jid]
        except Exception:
            pass

        tasks.append({
            "phase": "B",
            "jid": jid,
            "conversation_state": c,
            "recent_messages": msgs,
        })
        seen.add(jid)
        b_count += 1
        if b_count >= 10:
            break

    # ── Phase C: reminders ───────────────────────────────────────────────
    for jid, c in conversations.items():
        if c.get("stage") != "viewing_confirmed":
            continue
        slot = c.get("requested_slot")
        if not slot or not isinstance(slot, dict):
            continue
        reminders_sent = c.get("reminders_sent", {"24h": False, "2h": False})
        date_str = slot.get("date","")
        time_str = slot.get("time","")
        if not date_str or not time_str:
            continue
        try:
            viewing_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00+08:00")
            mins_until = (viewing_dt - now_sgt).total_seconds() / 60

            reminder_type = None
            if 90 <= mins_until <= 150 and not reminders_sent.get("2h"):
                reminder_type = "2h"
            elif 1320 <= mins_until <= 1500 and not reminders_sent.get("24h"):
                reminder_type = "24h"

            if reminder_type:
                msgs = recent_msgs(jid, limit=5)
                tasks.append({
                    "phase": "C",
                    "jid": jid,
                    "conversation_state": c,
                    "recent_messages": msgs,
                    "reminder_type": reminder_type,
                    "viewing_slot": slot,
                })
        except:
            pass

    # Save cleaned conversations back
    conv_state["conversations"] = conversations
    with open(CONV_STATE, "w", encoding="utf-8") as f:
        json.dump(conv_state, f, indent=2, ensure_ascii=False)

    # Record this run so next invocation knows how far back to scan
    _record_run()

    print(json.dumps({
        "tasks": tasks,
        "templates": templates,
        "landlord_db": landlord_db,
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
