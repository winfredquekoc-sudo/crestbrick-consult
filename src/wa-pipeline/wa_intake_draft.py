"""
wa_intake_draft.py -- draft generation and validation for the takeover resume flow,
split out of wa_intake_resume.py (9 Sep 2026 merge review) to keep that file under the
repo's 500 line guideline. Pure move, byte identical logic -- re-imported straight back
into wa_intake_resume's namespace so every existing call site (including tests that
reach these via wa_intake_resume.<name>) keeps working unchanged.

Builds the Haiku prompt from a chat transcript + listing/profile data, calls claude-guard
(Haiku, no tools), and hard validates the result before it is ever offered to Winfred as
a one tap /send -- see validate_draft's own docstring for the full rule set.
"""
import os, re, json, subprocess
import intake_engine as E

HAIKU_BIN = os.path.expanduser("~/.claude/bin/claude-guard")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MCP_CONFIG = os.path.expanduser("~/.claude/mcp-configs/none.json")
HAIKU_TIMEOUT_SEC = 25

def fetch_transcript(con, idc, jid, limit=80):
    """Last LIMIT text messages in this chat, oldest first, tagged ME (Winfred by hand) /
    BOT (the intake engine's own template sends) / THEM (the prospect)."""
    rows = con.execute(
        "SELECT is_from_me, content FROM messages WHERE chat_jid=? "
        "AND content IS NOT NULL AND content != '' ORDER BY rowid DESC LIMIT ?",
        (jid, limit)).fetchall()      # rowid, not idc: see resume_reason_blocked
    rows.reverse()
    out = []
    for ifm, content in rows:
        who = "THEM"
        if ifm:
            who = "BOT" if E.is_engine_outbound(content) else "ME"
        out.append({"who": who, "text": content})
    return out


# 12 of Winfred's own real replies from the 60 day WhatsApp corpus (winfred_replies.json),
# picked for being short, warm, and pivoting to a viewing -- seeded into the draft prompt so
# Haiku writes in his actual register instead of a generic agent voice. Verbatim, no PII
# (no real address, unit number, or phone number survived the pick).
STYLE_EXAMPLES = (
    "When would you like to view?",
    "What time please?",
    "tonight what time can view?",
    "Ok may I know when you want to views",
    "Let me send you a video first , if you keen we can do physical viewing",
    "Hello , thank you for contacting me. Are you interested to view this place ?",
    "possible to view tonight?",
    "Sure I understand :) would you like to view\nIt tonight?",
    "Yes, still available:)",
    "let me check",
    "Let me check with landlord see which one works :)",
    "Hi I have video if you keen can do physical viewing",
)


def build_prompt(name, phone, listing_key, listing, profile, transcript, last_inbound):
    facts = (listing or {}).get("facts") or {}
    req = (listing or {}).get("requirements") or {}
    convo = "\n".join(f"{m['who']}: {m['text']}" for m in transcript)
    style = "\n".join(f"- {ex}" for ex in STYLE_EXAMPLES)
    return (
        "You are ghostwriting ONE WhatsApp reply as Winfred Quek, a Singapore property agent, "
        "to a rental prospect he is already talking to. He replied by hand earlier and has not "
        "answered their newest message yet.\n\n"
        f"Prospect: {name or 'unknown name'} ({phone})\n"
        f"Listing: {listing_key or 'not bound'}\n"
        f"Known profile: {profile}\n"
        f"Listing facts on file: lease_min_months={req.get('lease_min_months')}, "
        f"cooking={req.get('cooking')}, smoking={req.get('smoking')}, facts={facts}\n\n"
        "Winfred's own real replies, for style only (short, warm, pivots to a viewing) -- "
        f"never copy one verbatim unless it genuinely fits this exact message:\n{style}\n\n"
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
    """One claude-guard call, Haiku, no tools. Returns (text, None) or (None, error).

    WA_INTAKE_SANDBOX=1 short circuits before the real subprocess call (STEP 0 sandbox seal,
    9 Sep 2026 merge redo) -- see wa_intake_owner_answers.call_haiku_extract's matching
    docstring. A harness that wants real draft-generation behaviour patches this function
    directly instead (see wa_intake_attack_harness.py's RES.call_haiku patch, which supplies
    a canned draft)."""
    if os.environ.get("WA_INTAKE_SANDBOX") == "1":
        return None, "sandboxed: real Haiku subprocess call suppressed"
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
# any money figure at all: a rent number is Winfred's to quote, never a drafted line's.
#
# Dates must never count as a money figure (11 Sep 2026, item 4) -- the old bare \b\d{4}\b
# check flagged "16 Sep 2026" for the same reason it flagged "$1400", because a plain year is
# a bare 3+ digit number too. _is_money_figure below only ever rejects (a) "$"/"S$" directly
# against digits, (b) a 3-5 digit run directly against a per-month/per-mo/pm/monthly/k
# keyword, or (c) any OTHER bare 3-5 digit run -- UNLESS that exact run is the year half of a
# recognised "<day> <Month> <year>" / "<Month> <year>" date, which is exempted. A genuine
# date/time/pax mention never reaches a bare 3-5 digit run in the first place ("7.30pm" splits
# into 7 and 30, "2 pax" is one digit, "1 October" carries no digit run at all), so the
# exemption only ever has to cover the year case.
_DOLLAR_RE = re.compile(r"[$\uFF04]\s*\d|\bs\$\s*\d", re.I)
_MONEY_KEYWORD_NUM_RE = re.compile(
    r"\b\d{3,5}\s*k\b|\b\d{3,5}\s*(?:/|per\s*)?(?:mo|mth|month|pm|monthly)\b", re.I)
_BARE_NUM_RE = re.compile(r"\b\d{3,5}\b")
_MONTHS_RE_PART = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_YEAR_RE = re.compile(
    rf"\b(?:\d{{1,2}}\s+)?(?:{_MONTHS_RE_PART})[a-z]*\s+(\d{{3,5}})\b", re.I)


def _is_money_figure(t):
    if _DOLLAR_RE.search(t):
        return True
    if _MONEY_KEYWORD_NUM_RE.search(t):
        return True
    bare_nums = list(_BARE_NUM_RE.finditer(t))
    if not bare_nums:
        return False
    date_year_spans = {m.span(1) for m in _DATE_YEAR_RE.finditer(t)}
    for m in bare_nums:
        if m.span() not in date_year_spans:
            return True
    return False


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
    if _is_money_figure(t):
        return "quotes a figure"
    if _ADVICE_RE.search(t):
        return "strays into advice"
    if re.search(r"\b(?:https?://|www\.)", t, re.I):
        return "contains a link"
    return None


