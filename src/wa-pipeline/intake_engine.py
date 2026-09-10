"""
intake_engine.py — unified, state-driven tenant intake engine (DRY RUN by default).

Fixes the three go-live blockers:
  1. Matching fail-open: qualify() handles ethnicity exclude AND only modes,
     no-single-males, null budget (NEEDS_INFO, never auto-pass).
  2. Wires the proven invariants into ONE callable engine (not a sandbox).
  3. ONE state store keyed by phone number (pn). @lid is always resolved to pn,
     so one human is one record.

Anti-spam is STATE-DRIVEN, not a cap:
  - each inbound WhatsApp message id is processed exactly once (event dedup)
  - the intake form is sent once (form_sent flag), the viewing question once
    (viewing_asked flag); a send never fires if its flag is set
  - reactive replies (answering a prospect question) are one-per-inbound and
    NOT capped, so a real conversation can continue
  - if Winfred replies by hand, manual_takeover latches and the engine goes silent

Nothing is sent while DRY_RUN is True; handle_event returns the action it WOULD take.

No hyphens or dashes in any tenant-facing copy (per Winfred's standing rule).
"""
import json, os, re, sqlite3, functools
import wa_intake_paths as _P

DRY_RUN = False  # LIVE 2026-06-17: restored after form_sent crash fix (backlog already drained in preview)
MAX_PROSPECT_MSGS = 10  # hard cap: at most this many prospect-facing messages per person (per qualification attempt)

# STEP 0 sandbox seal (9 Sep 2026 merge redo): kept as module constants for backward compat
# with existing mock.patch.object(intake_engine, "NAME", ...) tests (this module's own
# functions read these bare names directly, so that pattern keeps working); the _xxx()
# helpers below additionally resolve fresh from wa_intake_paths at call time, so a script
# that only sets WA_INTAKE_STATE_ROOT/WA_INTAKE_DATA_ROOT/WA_INTAKE_MSG_DB (no per-constant
# mock.patch at all) is sandboxed too. See wa_intake_paths.resolved's docstring.
WA_DB   = _P.paths()["whatsapp_db"]
_default_WA_DB = WA_DB
MSG_DB  = _P.paths()["messages_db"]
_default_MSG_DB = MSG_DB
IDX     = _P.paths()["listing_index"]
_default_IDX = IDX
AVAIL   = _P.paths()["viewing_availability"]
_default_AVAIL = AVAIL
STATE   = _P.paths()["intake_state"]
_default_STATE = STATE
TEMPLATES = _P.paths()["property_templates"]
_default_TEMPLATES = TEMPLATES
LANDLORD_DB = _P.paths()["landlord_db"]
_default_LANDLORD_DB = LANDLORD_DB


def _wa_db():
    return _P.resolved(globals(), "WA_DB", "whatsapp_db")


def _msg_db():
    return _P.resolved(globals(), "MSG_DB", "messages_db")


def _idx():
    return _P.resolved(globals(), "IDX", "listing_index")


def _avail():
    return _P.resolved(globals(), "AVAIL", "viewing_availability")


def _state():
    return _P.resolved(globals(), "STATE", "intake_state")


def _templates():
    return _P.resolved(globals(), "TEMPLATES", "property_templates")


def _landlord_db():
    return _P.resolved(globals(), "LANDLORD_DB", "landlord_db")

REQUIRED_FIELDS = ["name","nationality","ethnicity","gender",
                   "pass_type","no_of_pax","move_in_date","lease_term_months","budget"]

# Rides in FRONT of the intake form when message 1 offered a concrete slot: the form is
# framed as the ticket to the viewing, not a gate (Winfred, 11 Aug 2026). Must stay in
# _ENGINE_PREFIXES so the echoed send never latches manual takeover.
VIEWING_TICKET_PREFIX = ("To confirm your viewing slot with the landlord I just need your "
                         "profile \U0001F447\n\n")
VIEWING_TICKET_PREFIX_ZH = ("为了跟房东确认您的看房时间，我需要您的资料 \U0001F447\n\n")
INTAKE_FORM = (
    "Pls fill this in so I can send your profile to the landlord :)\n"
    "• Email address:\n"
    "• Name:\n"
    "• Nationality:\n"
    "• Ethnicity:\n"
    "• Gender:\n"
    "• Age:\n"
    "• Pass type (SC/PR/EP/S Pass/STP etc):\n"
    "• Occupation (your job/industry):\n"
    "• Employment type (permanent / fixed term / variable):\n"
    "• No. of pax:\n"
    "• Move in date:\n"
    "• Lease term:\n"
    "• Budget:\n"
    "• Preferred location:"
)

# Chinese variant (Winfred, 11 Sep 2026): sent instead of INTAKE_FORM the moment the
# prospect's first inbound (rec["first_inbound_text"]) carries any CJK character -- see
# _detect_lang() below. Same 14 fields, same order, bilingual labels in the style Maddie
# already pastes by hand (the Chinese word rides directly in front of the English label with
# no separator -- extract_profile()'s grab() already falls back to matching the English half
# of a bilingual line, and _FIELD_LABEL_ALT/_CN_FIELD_MARKERS already carry most of these
# Chinese words from the 9 Sep hand paste hardening), so no parser change was needed beyond
# a few missing markers (email/age/employment type/lease term -- see _CN_FIELD_MARKERS below).
CHINESE_INTAKE_FORM = (
    "请填写以下资料，方便我把您的资料发给房东 :)\n"
    "• 邮箱 Email address:\n"
    "• 姓名 Name:\n"
    "• 国籍 Nationality:\n"
    "• 种族 Ethnicity:\n"
    "• 性别 Gender:\n"
    "• 年龄 Age:\n"
    "• 准证类型 Pass type (SC/PR/EP/S Pass/STP etc):\n"
    "• 职业 Occupation:\n"
    "• 雇佣类型 Employment type (permanent / fixed term / variable):\n"
    "• 入住人数 No. of pax:\n"
    "• 入住日期 Move in date:\n"
    "• 租期 Lease term:\n"
    "• 预算 Budget:\n"
    "• 首选地点 Preferred location:"
)

# any Han character anywhere in the prospect's first inbound (or portal boilerplate riding
# with it -- boilerplate text is itself CJK when the source is Chinese, so one check covers
# both cases Winfred asked for) means the whole first touch goes out in Chinese.
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
def _detect_lang(text):
    return "zh" if _CJK_RE.search(text or "") else "en"

def _lang(rec):
    """rec["lang"] is stamped once, at first touch (see the SEND_FORM branch below), so
    every later fixed template picks the same language for the rest of the conversation.
    Records from before this field existed (or a supply/buyer record, which never gets one)
    fall back to English."""
    return rec.get("lang") or "en"

# ---------- buyer (sale) intake ----------
# A buyer (sale) enquiry must NEVER get the tenant form above. It gets this buyer form,
# identical for HDB and private except the financing-readiness line: HDB asks HFE, private
# asks IPA. No hyphens or dashes in prospect-facing copy (Winfred's standing rule).
BUYER_FORM_TEMPLATE = (
    "Thanks for your enquiry :) To match you to the right unit and the best deal, could you fill this in?\n"
    "• Name:\n"
    "• Citizenship (SC / PR / Foreigner):\n"
    "• Budget:\n"
    "• Timeline to buy:\n"
    "• Any property to sell first:\n"
    "• Paying with CPF / Cash / Loan:\n"
    "• {fin}:\n"
    "• Preferred area or district:\n"
    "• Property type (HDB / Condo / Landed):\n"
    "• Bedrooms needed:\n"
    "• For own stay or investment:"
)
BUYER_FORM_HDB     = BUYER_FORM_TEMPLATE.format(fin="HFE valid?")
BUYER_FORM_PRIVATE = BUYER_FORM_TEMPLATE.format(fin="IPA valid?")
BUYER_FORM_UNKNOWN = BUYER_FORM_TEMPLATE.format(fin="HFE valid? (if HDB) or IPA valid? (if private)")

# ---------- identity ----------
def resolve_pn(jid):
    """@lid or @s.whatsapp.net -> bare phone number. One human, one key."""
    if not jid: return None
    raw = jid.split("@")[0]
    if jid.endswith("@s.whatsapp.net"): return raw
    try:
        con = sqlite3.connect(_wa_db(), timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        r = con.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid=?", (raw,)).fetchone()
        con.close()
        if r and r[0]: return r[0]
    except Exception:
        pass
    return raw  # last resort; flagged unresolved by caller if needed

# ---------- reference data ----------
def _load(p, d):
    try: return json.load(open(p))
    except Exception: return d

def listing_reqs():
    return {l["listing_key"]: l for l in _load(_idx(), {"listings":[]})["listings"]}

def next_slot(listing_key):
    # the slot we OFFER must be in the future (today or later, SGT) — never a past-dated slot
    # that a prospect would 'confirm' and then turn up to nothing. Same floor as the enquiry msg.
    return next_future_slot(listing_key)

_WD = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
_WD_ABBR = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
_MON_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def _fixed_viewing_slot(listing_key, today_str):
    """If a listing has a HARD-CODED recurring viewing window (fixed_viewing in the listing
    registry), synthesize the next occurrence (this weekday or the next), ignoring the
    availability file. This is the ONLY slot ever offered for that listing — Winfred wants
    prospects strictly funnelled to one fixed weekly time."""
    import datetime
    l = listing_reqs().get(listing_key, {}) or {}
    fv = l.get("fixed_viewing") or (l.get("requirements", {}) or {}).get("fixed_viewing")
    if not fv:
        return None
    tgt = _WD.get(str(fv.get("weekday","")).strip().lower()[:3])
    if tgt is None:
        return None
    y, m, d = map(int, today_str.split("-"))
    base = datetime.date(y, m, d)
    nd = base + datetime.timedelta(days=(tgt - base.weekday()) % 7)   # today if already that weekday
    # if the slot is today but its start time has already passed (SGT), offer next week's
    # occurrence instead of a viewing that is already over.
    if nd == base:
        now_hm = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%H:%M")
        if str(fv.get("start") or "23:59") <= now_hm:
            nd = base + datetime.timedelta(days=7)
    ds = nd.strftime("%Y-%m-%d")
    label = f"{_WD_ABBR[nd.weekday()]} {nd.day} {_MON_ABBR[nd.month-1]}, {fv.get('time_label','')}".strip().rstrip(",")
    return {"slot_id": f"{listing_key}-fixed-{ds}-{str(fv.get('start','')).replace(':','')}",
            "date": ds, "start": fv.get("start"), "end": fv.get("end"),
            "label": label, "capacity": 99, "booked": 0, "status": "open", "fixed": True}

def next_future_slot(listing_key):
    """The soonest open, not full viewing slot dated today or later (SGT), or None.
    This is the landlord availability we surface to a new enquirer in the first message.
    A listing with a fixed_viewing rule ALWAYS returns that fixed weekly slot (overrides
    the availability file), so prospects are strictly offered only that time."""
    if not listing_key: return None
    import datetime
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    fixed = _fixed_viewing_slot(listing_key, today)
    if fixed:
        return fixed
    # the availability file is FLAT ({listing_key: {slots: []}}); older code expected a
    # {"slots": {...}} wrapper that never existed, so file slots were invisible. Read both.
    data = _load(_avail(), {})
    a = (data.get("slots") or {}).get(listing_key) or data.get(listing_key) or {}
    now_hm = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%H:%M")
    slots = [s for s in a.get("slots",[]) if s.get("status")=="open" and s.get("booked",0) < s.get("capacity",1)
             and (s.get("date","") > today or (s.get("date","") == today and str(s.get("start") or "23:59") > now_hm))]
    slots.sort(key=lambda s:(s.get("date",""), s.get("start","")))
    return slots[0] if slots else None

def _has_open_future_slot(listing_key):
    """True if the listing has an open future viewing slot. Decides whether to ask
    Winfred to capture the landlord's availability."""
    return next_future_slot(listing_key) is not None

def book_slot(listing_key, slot_id, path=None):
    """Atomic capacity guard. Increments booked iff booked < capacity. Returns True if booked,
    False if the slot just filled (so two YES on a one person slot can never both book)."""
    import fcntl
    if not slot_id: return False
    if path is None:
        path = _avail()
    try:
        f = open(path, "r+")
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
        except (ValueError, OSError):
            return False              # malformed availability file -> do not crash the caller
        entry = (data.get("slots") or {}).get(listing_key) or data.get(listing_key) or {}
        for s in entry.get("slots",[]):
            if s.get("slot_id") == slot_id:
                if s.get("booked",0) < s.get("capacity",1):
                    s["booked"] = s.get("booked",0) + 1
                    if s["booked"] >= s.get("capacity",1): s["status"] = "full"
                    f.seek(0); json.dump(data, f, indent=2, ensure_ascii=False); f.truncate()
                    return True
                return False
        return False
    finally:
        fcntl.flock(f, fcntl.LOCK_UN); f.close()

def apply_fixed_viewing(updates, path=None):
    """Atomic, flock guarded write of one or more `fixed_viewing` entries into the listing
    index (the SAME field _fixed_viewing_slot/next_future_slot already read -- viewing slot
    confirmation, extract_viewing_windows.py / apply_viewing_windows.py, and the self chat
    /slot command all funnel through this one choke point). `updates` is
    {listing_key: fixed_viewing_dict}; a listing_key not present in the index is reported in
    "missing", never silently dropped. A .bak copy of the index is written once, before the
    first mutation, whenever at least one update actually lands. Returns
    {"written": [...], "missing": [...]} (plus "error" if the index could not be read)."""
    import fcntl, shutil
    idx_path = path or _idx()
    written, missing = [], []
    try:
        f = open(idx_path, "r+")
    except OSError:
        return {"written": [], "missing": list(updates.keys()), "error": "index file not found"}
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
        except (ValueError, OSError):
            return {"written": [], "missing": list(updates.keys()), "error": "index unreadable"}
        by_key = {l.get("listing_key"): l for l in data.get("listings", [])}
        for lk, fv in updates.items():
            entry = by_key.get(lk)
            if entry is None:
                missing.append(lk)
                continue
            entry["fixed_viewing"] = fv
            written.append(lk)
        if written:
            shutil.copy2(idx_path, idx_path + ".bak")
            f.seek(0); json.dump(data, f, indent=2, ensure_ascii=False); f.truncate()
        return {"written": written, "missing": missing}
    finally:
        fcntl.flock(f, fcntl.LOCK_UN); f.close()

# ---------- profile extraction ----------
# a non SGD currency marker on the number (RM800, USD500, ₹15000...) must never be read as
# a bare SGD figure -- no trailing \b so a glued prefix like "RM800" still matches (P2 fix,
# 9 Sep 2026 cycle5 sc3: a ringgit budget auto qualified a prospect under the SGD floor).
_NON_SGD_CCY_RE = re.compile(r"\b(?:rm|myr|usd|inr|rmb)|[₹¥]", re.I)

def _to_int(s):
    if s is None: return None
    txt = str(s).lower().strip()
    if _NON_SGD_CCY_RE.search(txt):
        return None
    # 'k'/'m' multiply ONLY as a magnitude suffix on the number (e.g. 1.2k, 1.5m),
    # never just because the letter appears in the text ("ok", "looking" must not x1000).
    m = re.search(r"(\d[\d,\.]*)\s*([km])?\b", txt)
    if not m: return None
    try:
        val = float(m.group(1).replace(",", ""))
    except Exception:
        return None
    suf = m.group(2)
    mul = 1000 if suf == "k" else 1000000 if (suf == "m" or "million" in txt) else 1
    val = val * mul
    # guard against absurd magnitudes (a pasted long number) crashing int()/the caller
    if not (val == val) or val > 1e12:   # NaN check + ceiling
        return None
    return int(val)

_SUN_FACING_ADDR_RE = re.compile(
    r"(?i)sun\s*facing\s+for\s+(.+?)(?=\s+on\s+(?:your|the)\s+sun\s*facing\s*checker\b|"
    r"\s+on\s+sunfacing\.com\b|[.,\n]|$)"
)

# every recognised field label, LONGER/compound variants first in each alternation so a
# continuation word ("type", "term", "date") is consumed as part of the LABEL, never left
# behind to be captured as the value ("Pass type\nWork Permit" used to store "type").
# Reused both by grab()'s own stop-at-next-label lookahead (so one comma or newline joined
# form line never bleeds one field's value into the next) and by _looks_like_filled_form()
# below (merge provenance).
_FIELD_LABEL_ALT = (
    r"name|nationality|race|ethnic\w*|gender|sex|age|work\s*pass\s*type|work\s*pass|"
    r"pass\s*type|pass|visa|"
    r"no\.?\s*of\s*(?:pax|people|persons)|pax|persons?|occupant|move.?in(?:\s*date)?|"
    r"intended\s*move|"
    r"lease(?:\s*term)?|budget|rent|afford|preferred\s*location|preferred\s*area|location|"
    r"email(?:\s*address)?|occupation|profession|employment(?:\s*type)?|"
    # Chinese label synonyms (real filled forms replay 9 Sep 2026): 姓名 name, 国籍
    # nationality, 种族 ethnicity, 性别 gender, 证件类型 pass type, 职业 occupation,
    # 就业类型 employment type, 人数 pax, 入住日期 move in date, 租期 lease term, 预算 budget.
    r"姓名|国籍|种族|性别|证件类型|职业|就业类型|人数|入住日期|租期|预算"
)
# a real filled form can separate "label" from "value" with an ASCII colon/dash OR the
# fullwidth CJK colon "：" a Chinese keyboard actually types.
_SEP_CHARS = r":\-："
_NEXT_LABEL_STOP_RE = re.compile(
    r"[,，]\s*(?:" + _FIELD_LABEL_ALT + r")\b\s*[" + _SEP_CHARS + r"]?", re.I)
# same as above but the comma is OPTIONAL: a caption/OCR-style form with NO punctuation at all
# ("Name Alex Chua Nationality Singaporean Ethnicity Chinese...") still needs each field's
# value to stop at the next recognised label word alone (P1 fix, 9 Sep 2026 cycle4 c4ec07).
# Used ONLY when the current label itself consumed no separator (had_sep False) -- a real
# colon-labelled field ("Pass type (SC/PR/EP/S Pass/STP etc):") must keep using the comma
# required version above, or the word "Pass" inside its own format hint would truncate the
# value before the hint's closing paren is ever reached.
_NEXT_LABEL_STOP_NOSEP_RE = re.compile(
    r"\s(?:" + _FIELD_LABEL_ALT + r")\b\s*[" + _SEP_CHARS + r"]?", re.I)
# "form shaped": at least one recognised label immediately followed by a colon/dash anywhere
# in the message -- a real filled-in form line ("Name: Alex Tan"), never a casual sentence
# that merely happens to contain a field word ("...as ID, name Alex Tan, hope that helps.").
_FORM_LABEL_COLON_RE = re.compile(
    r"\b(?:" + _FIELD_LABEL_ALT + r")\b\s*[" + _SEP_CHARS + r"]", re.I)

def _looks_like_filled_form(text):
    return bool(_FORM_LABEL_COLON_RE.search(text or ""))

# every field label grab() recognises, in every script it recognises it in -- shared so a
# candidate VALUE that is itself one of these words (a blank label-only form where a label's
# own text leaks into the next field as its "value") is rejected everywhere, not only for the
# English labels (P1 fix, 9 Sep 2026 cycle4 sc5).
_LABEL_ONLY_RE = re.compile(
    r"^(name|nationality|ethnic|gender|sex|age|type\s+of\s+pass|pass|visa|"
    r"no\.?\s*of|pax|occupant|intended|move|preferred|lease|budget|rent|"
    r"email|occupation|employment|location|"
    r"姓名|国籍|种族|民族|性别|签证\w*|证件\w*|职业|入住\w*|租期|预算|人数|就业\w*|"
    r"பெயர்|தேசியம்|இனம்|பாலினம்|வீசா\s*வகை|வீசா|தொழில்|வீடு\s*மாறும்\s*தேதி)\b",
    re.I)

# a Lease term / 租期 value can arrive as a bare fraction (0.5), a half phrasing (half a
# year, 半年), a week count (2 weeks), or a CJK month suffix (12个月, 3个月) -- _to_int's own
# \b right after the digit run never matches when the very next character is itself a CJK
# word character (Python's \w treats every CJK ideograph as a word char, so "2" then "个" is
# NOT a boundary), and its int() truncation turns 0.5 into a falsy 0 that the caller then
# drops entirely (P1 fix, 11 Sep 2026 cycle attack replay: "Lease term: 0.5" on a genuine 2
# week request vanished with no verdict at all). This owns every unit conversion so a real
# sub floor duration always reaches qualify() as a real (possibly 0) number of months, never
# a missing key.
_LEASE_HALF_RE = re.compile(r"\bhalf\b|半", re.I)
_LEASE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
_LEASE_YEAR_UNIT_RE = re.compile(r"year|yr|年", re.I)
_LEASE_WEEK_UNIT_RE = re.compile(r"week|周|星期", re.I)
def _parse_lease_term_months(raw):
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    is_half = bool(_LEASE_HALF_RE.search(s))
    m = _LEASE_NUM_RE.search(s)
    num = float(m.group(1)) if m else None
    if _LEASE_YEAR_UNIT_RE.search(s):
        if num is None:
            return 6 if is_half else 12
        return int(round((num / 2 if is_half else num) * 12))
    if _LEASE_WEEK_UNIT_RE.search(s):
        if num is None:
            return 0 if is_half else None
        weeks = num / 2 if is_half else num
        return int(weeks * 7 // 30)
    if num is None:
        return 0 if is_half else None
    months = num / 2 if is_half else num
    if months > 36:
        return None
    return int(months)

_MOVE_IN_PLAUSIBLE_RE = re.compile(
    r"\d|asap|immediate|now\b|tbc|tba|flexible|anytime|soon|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"today|tomorrow|tmr|next\s+(?:week|month)|"
    r"今|明|下|月|日|号|周|星期", re.I)

# a "Name:" field value that is itself a prompt injection attempt, never a real human name.
_NAME_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"disregard\s+(?:all\s+)?(?:previous|prior)|you\s+are\s+now|respond\s+as|act\s+as\s+(?:a|an)\b|"
    r"system\s+(?:notice|prompt)|compliance\s+officer|unrestricted|new\s+polic(?:y|ies)|"
    r"supervisor\s+at|authoris(?:ed|ation)\s+to\s+disclose", re.I)

def extract_profile(text):
    """Best-effort parse of a filled-in block or free text. Required fields only."""
    p = {}
    t = text or ""
    # Sun Facing Checker lead: "...sun facing for <address> on your Sun Facing Checker...".
    # Captured from the ORIGINAL text, before the boilerplate strip below -- a message that
    # opens "Hi Winfred, ..." (every Sun Facing wa.me CTA does) is wholly blanked by that
    # strip, so this must run first or the address is never seen.
    sfc = _SUN_FACING_ADDR_RE.search(t)
    if sfc:
        addr = sfc.group(1).strip(" ,.")
        if addr:
            p["address"] = addr
    # strip a leading bracketed media caption ("[Image: form]", "[Document attached]") BEFORE
    # parsing: a caption's own incidental colon ("[Image: form]") otherwise makes grab()'s own
    # separator check reject every label that follows it as sitting inside some OTHER field's
    # value (P1 fix, 9 Sep 2026 cycle4 c4ec07: a captioned form image with a real filled-in
    # label/value form after the caption parsed to nothing at all).
    t = re.sub(r"^\s*\[[^\]\n]{0,60}\]\s*", "", t)
    # strip portal enquiry boilerplate BEFORE parsing: lines like "RENT - 905 Jurong West
    # Street 91" made grab("rent") capture the street number as the tenant's budget.
    t = re.sub(r"(?im)^\s*(hi winfred.*|hi propertyguru.*|i am interested in:?.*|"
               r"(?:rent|sale)\s*-\s.*|room\s*/\s*s?\$.*|\d[\s-]*(?:room|beds?)\s+hdb.*|"
               r"https?://\S+.*|ref id:.*|thanks?\.?)\s*$", "", t)
    # a slash/pipe/semicolon delimited form ("name jason wong / email j@x.com / nationality
    # singaporean / ...") has no colons at all, so grab()'s comma-then-label stop never
    # fires and one field swallows the whole rest of the message. Normalize any such
    # delimiter immediately followed by a recognised label into a newline BEFORE parsing --
    # grab() already stops a value at end of line, so this alone fixes every field boundary
    # (P2 fix, 9 Sep 2026 cycle 3 attack replay). Only when a label actually follows, so a
    # real "/" inside a date or fraction ("1/12", "and/or") is left untouched.
    t = re.sub(r"[ \t]*[/|;][ \t]*(?=(?:" + _FIELD_LABEL_ALT + r")\b)", "\n", t, flags=re.I)
    def grab(label):
        # anchor to a real WORD (never mid word -- "nickname" must not match "name"), and
        # track whether a colon/dash separator was actually consumed right after the label.
        # [^\S\n]* (never \s*) around that separator so it can never eat a newline and grab
        # the NEXT field's text as if it were this one's value.
        # the trailing boundary also accepts a lookahead for whitespace/separator/end -- a
        # \b alone fails right after a script whose last character is a combining mark (e.g.
        # Tamil "பெயர்" ends in a non-spacing virama, категория Mn, which Python's \w does not
        # count as a word char, so no \b exists between it and a following ":" -- both sides
        # read as non-word). Never widens matching for ASCII/CJK labels, where \b already
        # covers every real case (P1 fix, 9 Sep 2026 cycle4 sc5: every Tamil label failed to
        # match at all).
        lab_re = (r"\b(?:" + label + r")(?:\b|(?=[\s" + _SEP_CHARS + r"]|$))"
                  r"[^\S\n]*([" + _SEP_CHARS + r"])?[^\S\n]*")
        # PRIMARY: the label must sit at a real field start (start of message, its own line,
        # or right after a comma joined field) -- never mid value ("Work Pass" must not let
        # the PASS label hijack a different field just because the word "Pass" sits inside
        # someone ELSE's answer).
        m = re.search(r"(?:^|\n|[,，])\s*[•\-]?\s*" + lab_re, t, re.I | re.M)
        is_fallback = False
        if not m:
            # FALLBACK: a casual, unstructured sentence ("my name is Ruth") never anchors --
            # read the label anywhere, but only where it is NOT already sitting inside some
            # OTHER field's value (a separator earlier on the same line means this word is
            # part of an answer already introduced by a different label, e.g. the stray
            # "Pass" inside "设施类型： Work Pass" must never hijack the pass_type field).
            for cand in re.finditer(lab_re, t, re.I):
                line_start = t.rfind("\n", 0, cand.start()) + 1
                if not re.search(r"[" + _SEP_CHARS + r"]", t[line_start:cand.start()]):
                    m = cand
                    is_fallback = True
                    break
        if not m:
            return None
        had_sep = bool(m.group(1))
        nl = t.find("\n", m.end())
        line_end = nl if nl != -1 else len(t)
        same_line = t[m.end():line_end]
        if not same_line.strip():
            if had_sep or nl == -1:
                return None          # a real separator with nothing after it = a blank field
            # the label sat bare on its own line with NO separator ("Pass type\nWork Permit")
            # -- the value is the NEXT line, never the leftover continuation word.
            nl2 = t.find("\n", nl + 1)
            same_line = t[nl + 1: nl2 if nl2 != -1 else len(t)]
        elif (not had_sep and not re.search(r"[:：]", same_line) and _is_question(t)
              and (is_fallback or not re.match(r"(?i)^(?:is|was|'s)\b", same_line.strip()))):
            # no explicit separator, no colon hint further along the line, AND the message
            # itself is a question: a stray label word caught inside that question
            # ("nationality and sexe please?", "...given my pass situation") is rejected
            # outright rather than captured as a garbled value. A genuine declaration
            # ("name is Ruth") still parses -- but ONLY when the label sat at a real field
            # start (PRIMARY), never when it was only found by the loose FALLBACK scan: a
            # fallback match inside a question whose leftover happens to start with "is"
            # ("...if Dec move in is ok?") is exactly the same class of stray-word hijack as
            # the nationality/pass examples above, not a genuine field declaration, and must
            # never mutate the record qualify() reads (P1 fix, 11 Sep 2026 attack replay:
            # "actually nvm just want to know in general if Dec move in is ok" stored
            # move_in_date "ok"). Scoped to interrogative messages only -- a genuine
            # unlabeled slash/space form ("name jason wong / email ...") is a plain
            # declarative statement and must still parse (P2 fix, 9 Sep 2026 cycle 3 attack
            # replay).
            return None
        # stop the value at the next recognised field label on the SAME line -- a comma or
        # newline joined form ("Nationality: Singaporean, Ethnicity: Chinese, Gender: Male")
        # must never let one field's grab() swallow the other fields' text too. The bare
        # (comma-free) stop is used ONLY when this field has neither a separator right after
        # its label NOR a colon anywhere later on the line -- a hinted field ("Pass type
        # (SC/PR/EP/S Pass/STP etc):") has no separator immediately after the label either,
        # but DOES have a colon further along the line (the real separator, past the hint),
        # so it must keep using the comma-required stop or the word "Pass" inside its own
        # hint text would truncate the value before that colon is ever reached.
        _line_has_colon = bool(re.search(r"[:：]", same_line))
        stop = (_NEXT_LABEL_STOP_RE if (had_sep or _line_has_colon)
                else _NEXT_LABEL_STOP_NOSEP_RE).search(same_line)
        val = (same_line[:stop.start()] if stop else same_line).strip().lstrip("•").strip()
        # if a format hint sits between the label and the value (e.g. "Date (e.g. 1 Aug): 15 Aug"),
        # take what follows the LAST colon, then drop a leading "(...)" hint. A blank field that only
        # echoes the hint then collapses to empty -> None, so a hint is never read as a real value.
        if ":" in val or "：" in val:
            val = re.split(r"[:：]", val)[-1].strip()
        val = re.sub(r"^\([^)]*\)\s*", "", val).strip()
        # a label with no colon separator can still match inside ordinary prose ("my name is
        # Ruth", "budget is 500k") -- grab() has no way to tell "field:" apart from a sentence
        # containing the word. Strip a leading connector verb so "is/was/'s Ruth" -> "Ruth"
        # instead of poisoning the value. Fixed 31 Jul 2026 (garbled buyer names "is Ruth", "an").
        val = re.sub(r"(?i)^(?:is|was|'s)\s+", "", val).strip()
        val = val.strip(" \t,;:.!•-：，。").strip()   # stray punctuation off every stored value
        # reject an empty value or one that is itself another field label, in ANY script -- a
        # blank label-only form (a label sitting bare on its own line, its value the NEXT
        # line, which is itself the NEXT label) must parse to nothing, not to an off-by-one
        # profile where one label's word ends up stored as another field's value (P1 fix, 9
        # Sep 2026 cycle4 sc5: a blank Chinese label form stored "国籍" as the tenant's name).
        if not val or _LABEL_ONLY_RE.match(val):
            return None
        return val
    nm  = grab(r"name|姓名|பெயர்")
    if nm and (len(nm) > 60 or _NAME_INJECTION_RE.search(nm)):
        # a "Name:" field is free rendered into Telegram/landlord facing lines verbatim
        # elsewhere -- an implausibly long value or one carrying prompt injection wording
        # ("ignore all previous instructions...") must never be stored as the tenant's name
        # (P1 fix, 9 Sep 2026 cycle4 c4rm07: a whole injection sentence was stored and would
        # have rendered straight into a Telegram flag).
        nm = None
    p["name"]=nm
    nat = grab(r"nationality|国籍|தேசியம்");     p["nationality"]=nat
    eth = grab(r"race|ethnic\w*|种族|இனம்");  p["ethnicity"]=eth
    g   = grab(r"gender|sex|性别|பாலினம்")
    if g:
        gl=g.lower()
        # preserve couple / dual-sex phrasing so qualify() can apply the couple gate;
        # only collapse to a single token for an unambiguous single gender.
        has_f = bool(re.search(r"\bf(?:emale)?\b", gl))
        has_m = bool(re.search(r"\bm(?:ale)?\b", gl))
        if "couple" in gl or "married" in gl or (has_f and has_m):
            p["gender"] = g.strip()
        elif gl.startswith("f") or "女" in g: p["gender"] = "Female"
        elif gl.startswith("m") or "男" in g: p["gender"] = "Male"
        else: p["gender"] = g
    age = grab(r"age")
    if age and _to_int(age) and _to_int(age) < 120: p["age"]=_to_int(age)
    ps  = grab(r"work\s*pass\s*type|work\s*pass|pass\s*type|pass|visa|证件类型|வீசா\s*வகை|வீசா")
    if ps: p["pass_type"]=ps.strip()
    pax = grab(r"no\.?\s*of\s*(?:pax|people|persons)|入住人数|pax|persons?|occupant|人数")
    if pax and _to_int(pax) and _to_int(pax) < 12: p["no_of_pax"]=_to_int(pax)
    # free-text solo signals: "just me", "staying alone", "myself", "only me", "me only",
    # "1 pax" inline. A real prospect answered the pax nudge with "just me staying alone"
    # and was silently dropped because none of the label patterns matched. An EXPLICIT count
    # elsewhere in the same message ("just me and my wife, 2 pax") is checked FIRST and wins
    # -- "just me" alone is a solo signal, but "just me and X, N pax" names a second person
    # and a real count in the same breath, which the bare phrase match would otherwise
    # silently override to 1 (P0 fix, 9 Sep 2026 cycle4 c4ec01).
    if "no_of_pax" not in p:
        tl = t.lower()
        m2 = re.search(r"\b([2-9])\s*(?:pax|persons?|people|of us)\b", tl)
        if m2:
            p["no_of_pax"] = int(m2.group(1))
        elif re.search(r"\b(just me|only me|me only|by myself|myself only|stay(?:ing)? alone|"
                       r"alone|solo|single occupant|1 (?:pax|person|pp))\b", tl):
            p["no_of_pax"] = 1
    mv  = grab(r"move.?in(?:\s*date)?|intended\s*move(?:\s*in)?(?:\s*date)?|入住日期|வீடு\s*மாறும்\s*தேதி")
    # a labelled field can still capture pure conversational leftover ("...move in is ok"
    # -> "ok") when the message never actually names a date -- require the value to look
    # date-ish (a digit, a month/day word, or a known immediacy phrase) before it is ever
    # allowed to mutate the record qualify() reads (P1 fix, 11 Sep 2026 attack replay:
    # "actually nvm just want to know in general if Dec move in is ok" stored move_in_date
    # "ok", a bare word with zero date content).
    if mv and len(mv) <= 60 and _MOVE_IN_PLAUSIBLE_RE.search(mv):
        p["move_in_date"]=mv.strip()
    ls  = grab(r"lease(?:\s*term)?|租期")
    if ls:
        n = _parse_lease_term_months(ls)
        if n is not None: p["lease_term_months"]=n
    bud = grab(r"budget|rent|afford|预算")
    if bud:
        b=_to_int(bud)
        # a bare small decimal with no unit (e.g. "1.2", "1.5") means thousands -> 1200/1500.
        # scale the float directly because _to_int truncates 1.2 -> 1.
        mdec = re.search(r"\b(\d\.\d{1,2})\b", bud)
        if mdec and (b is None or b < 100):
            b = int(float(mdec.group(1)) * 1000)
        if b and 200 < b < 20000: p["budget"]=b
    loc = grab(r"preferred location|preferred area|location")
    if loc: p["preferred_location"]=loc.strip()
    em = grab(r"email(?:\s*address)?")
    if em and "@" in em and "." in em: p["email"]=em.strip()
    occ = grab(r"occupation|profession|职业|தொழில்")
    if occ: p["occupation"]=occ.strip()
    emp = grab(r"employment(?:\s*type)?|就业类型")
    if emp: p["employment_type"]=emp.strip()
    return {k:v for k,v in p.items() if v not in (None,"")}

# a numbered/positional answer to the intake form ("1) Alex 2) Singaporean 3) Chinese ...")
# carries no field labels at all -- maps 1:1 onto the intake template's own field order
# instead (P2 fix, 9 Sep 2026 cycle5 hg5-07: this used to parse to zero fields and the whole
# profile was discarded, even though the raw text still got flagged to Winfred).
_POSITIONAL_FIELD_ORDER = ("name", "nationality", "ethnicity", "gender", "pass_type",
                           "occupation", "employment_type", "no_of_pax", "move_in_date",
                           "lease_term_months", "budget")
_POSITIONAL_MARKER_RE = re.compile(r"(?:^|\s)(\d{1,2})[).:]\s*")

def _extract_positional_form(text):
    """Only trusted when it type checks -- pax/lease/budget numeric, a non empty date --
    so a genuinely unparseable numbered message still falls through to the existing
    unparseable-form flag with the raw text, never a guessed profile."""
    t = text or ""
    if _FORM_LABEL_COLON_RE.search(t):
        return {}   # a real labelled form -- the normal parser owns this, not positional
    marks = list(_POSITIONAL_MARKER_RE.finditer(t))
    if len(marks) < 8:
        return {}
    vals = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        vals.append(t[start:end].strip(" ,;"))
    out = {}
    for i, key in enumerate(_POSITIONAL_FIELD_ORDER):
        if i < len(vals) and vals[i]:
            out[key] = vals[i]
    if _to_int(out.get("no_of_pax")) is None: return {}
    if _to_int(out.get("lease_term_months")) is None: return {}
    if _to_int(out.get("budget")) is None: return {}
    if not out.get("move_in_date"): return {}
    out["no_of_pax"] = _to_int(out["no_of_pax"])
    out["lease_term_months"] = _to_int(out["lease_term_months"])
    out["budget"] = _to_int(out["budget"])
    return out

def _open_intake(listing):
    """True if a listing accepts all profiles (owner takes everyone) — only baby + the listing's
    own ethnicity/nationality gate apply, and enquirers get a short form straight to the viewing."""
    r = ((listing or {}).get("requirements") or (listing or {}))
    return bool(r.get("open_intake"))

OPEN_INTAKE_FORM = (
    "Happy to set up a viewing :) Just drop me:\n"
    "• Name:\n"
    "• Nationality:\n"
    "• No. of Pax (how many staying, and any infants):\n"
    "• Budget:\n"
    "• Lease term (landlord looks for 1 year minimum):\n\n"
    "Then I will lock in your slot."
)

def missing_required(profile, listing=None):
    r = (((listing or {}).get("requirements") or (listing or {})) if listing else {})
    if r.get("open_intake"):
        # owner accepts everyone -> fields needed to screen baby (pax), affordability and
        # the 1 year lease floor (budget + lease are must-knows, Winfred 11 Jul 2026), and,
        # if the listing keeps a nationality gate, nationality. Name to address them.
        req = ["name", "no_of_pax", "budget", "lease_term_months"]
        np = r.get("nationality_pref", {}) or {}
        if np.get("mode") in ("exclude", "only") and np.get("list"):
            req.append("nationality")
        return [f for f in req if profile.get(f) in (None, "")]
    req = list(REQUIRED_FIELDS)
    if listing is not None:
        # gate driven asks (rubric: "ask only what the bound listing's gates consume, plus
        # name, pax, budget, lease term"). The full 14 field form is still sent as is --
        # Winfred wants that regardless -- this only decides what the NUDGE and the
        # qualified verdict hold out for. Scoped to the case every one of the three
        # protected gates is "any" -- a listing that still gates on even one of them (e.g.
        # caspian: gender male_pref, ethnicity excludes Indian) keeps asking all three,
        # since a partial profile there could still turn out DISQUALIFIED (P1 fix, 11 Sep
        # 2026 attack replay: 6 fixtures with gender/ethnicity_rule/nationality_pref ALL
        # "any" still got nudged for ethnicity, gender and age -- a conversion tax and an
        # unnecessary PDPA surface for data nobody was ever going to gate on).
        all_any = (r.get("gender", "any") == "any"
                   and (r.get("ethnicity_rule") or {}).get("mode", "any") == "any"
                   and (r.get("nationality_pref") or {}).get("mode", "any") == "any")
        if all_any:
            req = [f for f in req if f not in ("gender", "ethnicity", "nationality")]
    return [f for f in req if profile.get(f) in (None,"")]

def _viewing_cta(slot, lang="en"):
    """The message 1 viewing CTA line, in the prospect's own language (Winfred, 11 Sep
    2026). The slot label itself (a date/time) is never translated. Chinese wording puts
    the fixed, distinctive lead words FIRST and the variable slot LAST, on purpose -- every
    engine-send matcher (_ENGINE_PREFIXES/BOT_SIGNATURES/_OUTBOUND_ONLY) matches on a fixed
    PREFIX, which only works if the variable part trails it."""
    if slot and slot.get("label"):
        if lang == "zh":
            return ("\n\n方便过来看房吗？我可以帮您安排，时间是 " + slot["label"] + " \U0001F642")
        return ("\n\nAre you free to view on " + slot["label"]
                 + "? I can arrange for viewing \U0001F642")
    if lang == "zh":
        return "\n\n本周都有安排看房。您方便哪天和几点？我会帮您跟房东安排。"
    return ("\n\nViewings are running this week. What day and time suit you? "
            "I will arrange it with the owner.")

def listing_unit_message(listing_key, lang="en"):
    """MESSAGE 1: unit info from the template + the landlord's available viewing slot.
    No intake form. The form is sent as a separate second message (see handle_event)."""
    d = _load(_templates(), {"listings":[]})
    for l in d.get("listings", []):
        if l.get("id") == listing_key and l.get("message"):
            msg = l["message"]
            i = msg.lower().find("pls fill this in")
            head = msg[:i].rstrip() if i > 0 else msg.rstrip()
            # drop the template's generic viewing line so the live slot is the single source
            head = re.sub(r'\s*(?:\U0001F5D3️|\U0001F5D3)?\s*viewing slots?:[^\n]*', '', head, flags=re.I).rstrip()
            # strip any CEA signoff baked into the template (no CEA in tenant-facing DMs)
            head = re.sub(r'\s*Winfred Quek\s*\|\s*CEA\s*R073319H', '', head, flags=re.I).rstrip()
            slot = next_future_slot(listing_key)
            # VIEWING-FIRST (Winfred, 11 Aug 2026): message 1 always carries an ACTIVE viewing
            # CTA — his own 60-day data has a specific slot converting 96.6% vs 30.7% for an
            # open ask. The form is the ticket to the slot, not a gate in front of it.
            return head + _viewing_cta(slot, lang)
    # no unit template for this listing: still lead with the slot CTA when one exists —
    # ang-mo-kio-539 (the push listing) had NO template and its first touch went out as a
    # bare form with no CTA at all (cycle-27 catch, 11 Aug 2026)
    slot = next_future_slot(listing_key)
    if slot and slot.get("label"):
        return _viewing_cta(slot, lang).lstrip("\n")
    return None

def listing_message(listing_key):
    """Combined unit + form. Kept for compatibility; the live flow sends two messages."""
    unit = listing_unit_message(listing_key)
    return (unit + "\n\n" + INTAKE_FORM) if unit else None

ENQUIRY_SIGNS = ("i am interested","interested in","interested:","learn more about this listing",
                 "still available","is this available","is the room available","is it available","available?",
                 "looking for a room","i want to rent","want to rent","room for rent","for rent","keen",
                 "can i view","can we view","like to view","want to view","viewing","view it","view the",
                 "see the room","see the unit","is the room","is this room","how much is the rent")
def is_enquiry(text):
    return any(s in (text or "").lower() for s in ENQUIRY_SIGNS)

# Organic (non-portal) tenant enquiries are often phrased outside ENQUIRY_SIGNS — e.g.
# "looking to rent a common room", "any common room available", a bare PropertyGuru link to
# one of our units, or a Chinese-language enquiry. Those were leaking to manual as "not a
# clear tenant enquiry". Broaden detection, but keep it high precision: a landlord-supply or
# negation marker ("not looking to rent", "my room posting", "looking for a tenant", 招租/房东)
# vetoes back to a human, so a landlord describing their own unit never gets the tenant form.
EXTRA_ENQUIRY_SIGNS = (
    "looking to rent","looking for a room","looking for a unit","looking for a place",
    "looking for a rental","interested to rent","keen to rent","like to rent",
    "any common room","any room available","room available for","available for rent",
    "is it still available","still vacant","do you have any room","do you have a room",
    "出租","看房","租房",
)
_SUPPLY_NEG = (
    "not looking","no longer looking","not renting","not interested",
    "my room posting","my posting","my place","my unit","my room","my property","my listing",
    "looking for a tenant","looking for tenant","i have a room","i have a unit",
    "i'm a landlord","im a landlord","i am the owner","i'm the owner","as a landlord","for my place",
    "招租","房东","我的房",
)
# Commission NEGOTIATION from the fee-payer's side. Only an owner paying the fee asks for it
# to come down; a co-broke agent leads with an agency name or "co-broke". "your commission"
# sitting in the AGENT keyword list muted a live landlord for 5 days ("But can your commission
# lower ?", GoldMind Circle 6 Aug 2026, engine state excluded:agent) — these phrases are
# LANDLORD signals and must win over the residual commission keywords in excluded_reason.
_LANDLORD_FEE_NEG = (
    "commission lower","lower your commission","lower the commission","reduce your commission",
    "reduce the commission","reduce commission","commission too high","commission so high",
    "commission can lower","can your commission","cheaper commission","commission cheaper",
    "commission negotiable","negotiate the commission","negotiate your commission",
    "waive the commission","waive your commission","waive commission",
    "half month commission","1/2 month commission","0.5 month commission",
    "half a month commission","half month comm","1/2 month comm","0.5 month comm",
    "佣金太高","佣金可以","佣金少","中介费太高","中介费可以","中介费少",
)
# HIGH-PRECISION landlord/owner-supply markers — safe to match across the whole thread
# (unlike the generic negations above, which only veto the single current message). A real
# tenant never types these, so reading them across a contact's first few messages catches a
# landlord whose latest line happens to be ambiguous.
_LANDLORD_SUPPLY = (
    "looking for a tenant","looking for tenant","i have a room","i have a unit",
    "i have a place to rent","i have a room to rent","i have a unit to rent",
    "i have a common room","i have a master room","i have a spare room","spare room to rent",
    "find tenant","find a tenant","find me a tenant","help me find tenant","find tenants",
    "room to rent out","unit to rent out","place to rent out","rent out my","renting out my",
    "to rent out","for rent out",
    "help me rent out","help rent out","help me rent","you can help me rent",
    "i'm a landlord","im a landlord","i am a landlord","i am the owner","i'm the owner",
    "as a landlord","as the owner","my room posting","my posting","my listing",
    "my property for rent","my unit for rent","my room for rent",
    # article variants real landlords actually type (replay 8 Aug 2026: "I am the landlord"
    # fell through while "I am a landlord" matched — 13 of 19 real openers missed; Brenda got
    # the tenant form off exactly this gap on 5 Aug 2026)
    "i am the landlord","i'm the landlord","im the landlord","i am landlord","i'm landlord",
    "im landlord","am the landlord",
    "looking tenant","looking for tenants","any good tenant","any good tenants",
    "interested tenants","you have ready tenants","have ready tenants",
    # possessive availability statements — only an owner says "MY room is available"
    "my room is available","my room is still","my room available","my unit is available",
    "my unit is still","my unit available","my place is available",
    "we have 2 room","we have 2 rooms","we have two rooms","i have 2 rooms","i have two rooms",
    "i have a whole flat","i got a whole flat","my whole flat",
    # renting TO someone = choosing a tenant, the landlord's side of the verb
    "like to rent to","want to rent to","prefer to rent to","willing to rent to",
    "looking to rent to",
    "i am not stay there","i am not staying there","i do not stay there","i dont stay there",
    "i don't stay there","i don't stay in the unit","i dont stay in the unit",
    # landlord-onboarding form echoes: anyone answering OUR landlord form field names is a
    # landlord (David +6596976160 forwarded a filled form and was merged into a tenant profile).
    # NOT "preferred lease duration" — that line also appears in other agencies' TENANT profile
    # templates (Sungha Song regression, 11 Aug 2026).
    "owner name:","sole owner","is mop met","asking rent and flexibility",
    "how to handle viewings",
    # landlord closures: "I have tenant move in soon" (Hannah Hoang) — only an owner says this
    "i have tenant move","have tenant moving in","i have tenant already","found tenant already",
    "i found tenant","tenant confirmed already",
    # "wld u b interested to check and evaluate the rooms first?" (Wen) — inviting US to assess
    "evaluate the room","evaluate the rooms","evaluate my room",
    # First-person / action phrasings only — bare 房东 ("landlord") and 我的房 ("my room")
    # matched TENANTS talking about their landlord or their rented room (曹廷溪, real-history
    # replay R3, 11 Aug 2026). Tenants say 房东说/问房东; only owners say 我是房东.
    "招租","房间出租","单位出租",
    "我是房东","我是屋主","找租客","帮我出租","我要出租","我想出租","我有房间",
)  # NOTE: fee-neg phrases are deliberately NOT part of supply — a TENANT asking "can your
# commission be lower?" must never get the landlord form (adversarial-review P1-4, 11 Aug 2026);
# excluded_reason uses _LANDLORD_FEE_NEG as a veto against the agent keywords only.
# "My kim keat ave room is still avail" — possessive + availability with words in between,
# unreachable by substring. Tenants say "the/your room", never "my room", about OUR listing.
_MY_ROOM_AVAIL_RE = re.compile(r"\bmy [a-z0-9 ]{0,24}\b(room|unit|flat|place)s? (is |are )?(still )?avail")
# a THIRD PARTY relationship claim ("I am the landlord's brother") contains the substring
# "i am the landlord" and would otherwise match _LANDLORD_SUPPLY, but it is a claim about
# someone else, never the owner speaking for themselves -- and a bare relationship claim,
# with no real supply content of its own (an actual offer to rent out, a rent figure, or an
# address), is exactly the shape a prospect fakes to get the unit number for free (P1 fix,
# 9 Sep 2026 cycle 3 attack replay). Vetoes the match outright unless real supply content
# rides along in the same blob.
_RELATIONSHIP_CLAIM_RE = re.compile(
    r"landlord'?s\s+(?:brother|sister|son|daughter|wife|husband|father|mother|friend|"
    r"nephew|niece|cousin|relative|agent|rep(?:resentative)?)\b|"
    r"\bon\s+behalf\s+of\s+the\s+landlord\b|\blandlord\s+(?:told|asked|said)\s+me\b", re.I)
_SUPPLY_REAL_CONTENT_RE = re.compile(
    r"\brent\s*out\b|\bfor\s*rent\b|\d{3,5}\s*(?:psf|per\s*month|/\s*mo|month(?:ly)?)|"
    r"\bblk?\.?\s*\d|\bblock\s*\d|#\d{1,3}[- ]\d{1,4}|\bpostal\s*\d|\bavenue\b|\bstreet\b|"
    r"\broad\b|\bdrive\b|\blane\b|\bcrescent\b", re.I)
# SELLER-supply markers (selling their own property). A seller is SUPPLY for a sale, the
# mirror of a landlord being supply for a rental, and must never get the buyer (demand) form.
# High precision: possessive "my" or explicit "to sell" intent only, so a buyer who says
# "looking at resale condos" is never caught here. (No sale listings today, but this keeps
# renting and selling cleanly separated the moment a seller appears.)
_SELLER_SUPPLY = (
    "sell my","selling my","i am selling","i'm selling","im selling","want to sell","wanna sell",
    "looking to sell","intend to sell","intending to sell","like to sell","to sell my",
    "put up for sale","putting up for sale","list my property","list my unit","list my flat","list my condo",
    "出售","卖房","我要卖","我想卖","卖我的",
)
# Portal enquiry boilerplate = DEMAND side, decisively. A tenant who forwards a PropertyGuru
# template and then asks "这个房型有几个房间出租?" must never be classed as supply — the CJK
# phrase 房间出租 appears inside their QUESTION about the unit (real misfire caught in replay).
_DEMAND_VETO = ("i am interested in", "rent -", "for rent -", "propertyguru", "99.co",
                # Chinese tenant-demand phrasings: 想租 (want to rent), 找房 (looking for a
                # place), 有…出租吗 (any rooms for rent?) — these contain or accompany the
                # supply nouns 房间出租/单位出租, so they must veto first (replay R3 class).
                "想租", "找房", "有房间出租吗", "有单位出租吗", "有房出租吗")
# LOW CONTEXT markers: a tenant mentioning where they saw the ad ("saw on carousell") or a
# vague "got tenant already" (could be reporting someone ELSE's unit, or a scam line) are not
# on their own strong enough for a confident owner read (misfire caught in replay 9 Sep 2026:
# "bayshore park still there? saw on carousell" is a TENANT asking about OUR listing, not the
# landlord who posted it). These only ever return a PROBABLE landlord (never auto-send the
# supply form on their own), and a same-message tenant availability question vetoes them
# entirely — see _LOW_CONTEXT_VETO below.
_LANDLORD_SUPPLY_LOW_CONTEXT = ("carousell", "carousel", "carosell", "carrousel",
                                "got tenant already")
_LOW_CONTEXT_VETO = ("still available", "still there", "still open", "can view", "or not")

def _supply_shaped(low):
    """True when a single piece of text (no history) itself carries any supply marker,
    high or low precision. Used to decide whether history may even be consulted."""
    return (any(m in low for m in _LANDLORD_SUPPLY) or _MY_ROOM_AVAIL_RE.search(low)
            or any(m in low for m in _SELLER_SUPPLY)
            or any(m in low for m in _LANDLORD_SUPPLY_LOW_CONTEXT))

def supply_side_kind(chat_jid, text, with_confidence=False, rec=None):
    """'landlord' (renting out), 'seller' (selling), or None. Reads across the current
    message AND the contact's recent history -- but history only ever AMPLIFIES a read the
    current message already shows some shape of; a one-off remark several turns back
    ("my brother has a spare room to rent, help him find a tenant") must never permanently
    flip a later, purely-demand message ("what time are you free for me to view?") into
    landlord onboarding (P1 fix, 9 Sep 2026 attack replay). High precision so a genuine
    tenant or buyer is never misread as supply. With with_confidence=True returns
    (kind, confident) — the image-only Carousell opener and the low-context markers are a
    PROBABLE landlord (flag a human, never auto-send a form), a high-precision phrase match
    is confident. Pass rec to veto supply outright once the record already holds a bound
    listing_key plus a tenant profile or a qualify verdict -- a screened, bound tenant is
    never re-read as an owner from stale history."""
    if rec is not None and rec.get("listing_key") and (rec.get("profile") or rec.get("qualify")):
        return (None, False) if with_confidence else None
    cur = (text or "").lower()
    hist = (recent_inbound_text(chat_jid) or "").lower() if _supply_shaped(cur) else ""
    blob = cur + " \n " + hist
    # a bare relationship claim ("landlord's brother") with no real supply content of its own
    # never counts as supply -- and once the record is already bound with a tenant form out
    # (FORM_SENT or later), it never flips on a relationship claim alone regardless of any
    # other phrase overlap; a real supply-side switch there still needs a human look.
    if _RELATIONSHIP_CLAIM_RE.search(blob) and not _SUPPLY_REAL_CONTENT_RE.search(blob):
        return (None, False) if with_confidence else None
    if rec is not None and rec.get("form_sent") and _RELATIONSHIP_CLAIM_RE.search(cur):
        return (None, False) if with_confidence else None
    kind, confident = None, False
    if any(v in blob for v in _DEMAND_VETO):
        pass                                # portal enquiry template -> demand side, never supply
    elif any(m in blob for m in _LANDLORD_SUPPLY): kind, confident = "landlord", True
    elif _MY_ROOM_AVAIL_RE.search(blob):           kind, confident = "landlord", True
    elif any(m in blob for m in _SELLER_SUPPLY):   kind, confident = "seller", True
    elif any(m in blob for m in _LANDLORD_SUPPLY_LOW_CONTEXT):
        # same-message tenant availability question ("still there?", "can view or not") means
        # this is a tenant asking about OUR listing, not the owner -- veto back to no supply.
        if not any(v in cur for v in _LOW_CONTEXT_VETO):
            kind, confident = "landlord", False
    elif not blob.strip():
        n_img, n_txt = recent_inbound_media(chat_jid)
        if n_img >= 1 and n_txt == 0:
            kind, confident = "landlord", False
    return (kind, confident) if with_confidence else kind

# availability phrasing tolerant of word order/spacing -- ENQUIRY_SIGNS/EXTRA_ENQUIRY_SIGNS
# are literal substrings, so "is it still available" matches but "May I know if this is
# available ?" or a bare "available ?" never did (P1 fix, 9 Sep 2026 cycle4 c4rm06 replay).
_AVAIL_ENQUIRY_RE = re.compile(
    r"\bavailable\b\s*\?|\b(?:is|are)\b[^?.!\n]{0,25}\bavailable\b|"
    r"\bavailable\b[^?.!\n]{0,25}\b(?:is|are)\b|\bstill\b[^?.!\n]{0,15}\bavailable\b", re.I)

def is_tenant_enquiry(text, listing_key=None):
    """Broader than is_enquiry: also accepts natural tenant phrasings, non-English rental
    enquiries, and any message bound to one of OUR listings that is not a sale. A landlord-
    supply / negation marker vetoes back to manual so we never form-blast a landlord."""
    low = (text or "").lower()
    if any(n in low for n in _SUPPLY_NEG):
        return False
    if (is_enquiry(text) or any(p in low for p in EXTRA_ENQUIRY_SIGNS)
            or _AVAIL_ENQUIRY_RE.search(text or "")):
        return True
    if listing_key:
        tx, _ = classify_transaction(text, listing_key)
        if tx != "sale":
            return True
    # a complete looking profile submission or an explicit booking affirmative is itself proof
    # of a genuine tenant, even with no enquiry keyword present and no listing bound yet -- this
    # is re-evaluated on EVERY inbound (the caller never latches "not_enquiry" as terminal), so
    # a filled form or a plain "YES" re-opens a thread that earlier messages left unclassified
    # (P1 fix, 9 Sep 2026 cycle4 c4rm06: a complete profile and an explicit YES both stayed
    # dead ended as "not a clear tenant enquiry").
    if len(extract_profile(text)) >= 3:
        return True
    if _is_affirmative(text):
        return True
    return False

# ---------- prospect withdrawal: "found another place" / "no longer renting" ----------
# An ACTIVE prospect who tells us they have found somewhere else, or no longer want to rent,
# should be closed immediately (terminal) so the bot never messages them again and Winfred is
# told once. HIGH PRECISION on purpose: only unambiguous withdrawal phrasing matches, and any
# sign of continued interest in OUR unit vetoes the close (a comparison shopper stays open).
_KEEP_OPEN = (
    "still keen","still interested","still want","still looking at","can i still","is it still available",
    "still available","want to view","like to view","can i view","can we view","i'll take","ill take",
    "i will take","want to proceed","keen to proceed","when can i view",
)
_WITHDRAW_PHRASES = (
    # found another / elsewhere
    "found another place","found another unit","found another room","found another apartment","found another flat",
    "found a new place","found a new unit","found a new room","found a new apartment",
    "found a place already","found a unit already","found a room already",
    "found somewhere else","found something else","found somewhere","found elsewhere","found one already",
    "already found a place","already found a room","already found a unit","already found somewhere","already found another",
    # secured / rented / booked / signed somewhere else
    "already got a place","already got another place","already secured a place","already secured another",
    "already booked a place","already booked another",
    "already rented a place","already rented another","already rented somewhere","already signed",
    "rented another","secured another","booked another","signed another","took another place","went with another","going with another",
    "taken another place","taken another unit","taken another room","taken another apartment",
    "taken a place already","taken a room already","taken a unit already","committed to another",
    # no longer renting / not interested anymore
    "no longer looking","no longer renting","no longer interested","no longer keen","no longer require",
    "not looking anymore","not renting anymore","not interested anymore","not keen anymore","not looking any more","not renting any more",
    "do not wish to rent","don't wish to rent","dont wish to rent","do not want to rent","don't want to rent","dont want to rent",
    "no longer wish to rent","no longer want to rent","decided not to rent",
    # not moving / staying put / withdrawing
    "not moving anymore","no longer moving","not relocating anymore",
    "renew my current","renewing my current","extend my current","extending my current","staying at my current","stay at my current",
    "sorted out my housing","sorted my housing","settled on another","decided on another",
    "withdraw my","withdrawing my","like to withdraw","wish to withdraw",
    # singlish / colloquial
    "found liao","settled liao","rented liao","got already","already got a place liao",
    "no need already","dun need already","dont need already","don't need already",
    # indirect: buying (as an alternative to renting) / passing -- "buy" alone is too broad
    "buy instead","buying instead","buy our own","buying our own","buy a place instead",
    "i'll pass","pass on this","give it a miss","give this a pass","give it a pass",
    # other languages (zh / ms)
    "租到了","已经租到","找到房","不租了","不找了","sudah dapat","dah dapat","dah jumpa",
)
# "no longer looking AT THE EAST" / "keen ON THE MASTER room" = narrowing the search to a place
# or a room, NOT withdrawing. Negative lookahead keeps real withdrawals ("...in renting", "...a place").
_NARROW_RE = re.compile(
    r"no longer (?:looking|keen|interested)\s+(?:at|in|on|around)\s+"
    r"(?!rent|a place|a unit|a room|the unit|the room|the place|the rental)", re.I)
# tokens that, inside a QUESTION, mean the prospect is asking about OUR unit / weighing options
# (availability, comparison, buy-vs-rent) rather than withdrawing.
_Q_TOKENS = ("yours","your unit","your place","your room","your listing","landlord","owner",
             "available","still got","got already","vacant","liao","buy")
# match ASCII phrases on WORD BOUNDARIES so "will pass" never trips "ill pass" and "forgot
# already" never trips "got already". CJK has no word boundaries -> matched as plain substrings.
_ASCII_PHRASES = tuple(p for p in _WITHDRAW_PHRASES if p.isascii())
_CJK_PHRASES   = tuple(p for p in _WITHDRAW_PHRASES if not p.isascii())
_PHRASE_RE = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(p) for p in _ASCII_PHRASES) + r")(?![a-z])", re.I)
def _phrase_hit(low):
    return bool(_PHRASE_RE.search(low)) or any(p in low for p in _CJK_PHRASES)
def withdrawal_signal(text):
    """True if an active prospect clearly signals they found another place or no longer wish to
    rent. Several vetoes keep precision high (a comparison shopper / search-narrower stays open)."""
    low = (text or "").lower()
    if not low:
        return False
    if any(k in low for k in _KEEP_OPEN):
        return False
    # renting OUT one's own place (an upgrader) is not withdrawing from ours.
    if "rented out" in low or "renting out" in low or "rent out" in low:
        return False
    # a Chinese question particle, or an availability/comparison/buy question about OUR unit
    # ("similar to yours?", "got already or not?"), is interest -> never auto-close on it.
    if "吗" in low:
        return False
    is_q = ("?" in low) or ("or not" in low)
    if is_q and any(t in low for t in _Q_TOKENS):
        return False
    if _NARROW_RE.search(low):
        return False
    return _phrase_hit(low)

# ---------- closing pleasantry (Winfred, 9 Sep 2026 merge redo) ----------
# A withdrawal, or a bare thanks/goodbye, gets ONE fixed reply -- never drafted (it needs no
# Haiku call, no human judgement), never a Telegram ping (the whole point is the bot closes
# small talk on its own without pulling Winfred in for every "thanks, bye"). Two templates:
# one names "the new place" when the prospect said they found/secured/bought somewhere else,
# the other is generic for "not keen"/"no longer renting"/a bare thanks or goodbye, where no
# new place is implied.
_FOUND_PLACE_PHRASES = (
    "found another place","found another unit","found another room","found another apartment","found another flat",
    "found a new place","found a new unit","found a new room","found a new apartment",
    "found a place already","found a unit already","found a room already",
    "found somewhere else","found something else","found somewhere","found elsewhere","found one already",
    "already found a place","already found a room","already found a unit","already found somewhere","already found another",
    "already got a place","already got another place","already secured a place","already secured another",
    "already booked a place","already booked another",
    "already rented a place","already rented another","already rented somewhere","already signed",
    "rented another","secured another","booked another","signed another","took another place","went with another","going with another",
    "taken another place","taken another unit","taken another room","taken another apartment",
    "taken a place already","taken a room already","taken a unit already","committed to another",
    "settled on another","decided on another",
    "found liao","settled liao","rented liao","got already","already got a place liao",
    "buy instead","buying instead","buy our own","buying our own","buy a place instead",
    "租到了","已经租到","找到房","sudah dapat","dah dapat","dah jumpa",
)
def _closing_implies_new_place(text):
    low = (text or "").lower()
    return any(p in low for p in _FOUND_PLACE_PHRASES)

CLOSING_TEXT_NEW_PLACE = "No worries, all the best with the new place \U0001F642 Reach out anytime if you need a room again."
CLOSING_TEXT_GENERIC = "No worries \U0001F642 Reach out anytime if you need a room again."
CLOSING_TEXT_NEW_PLACE_ZH = "没关系，祝您在新住处一切顺利 \U0001F642 以后需要房间可以随时联系我。"
CLOSING_TEXT_GENERIC_ZH = "没关系 \U0001F642 以后需要房间可以随时联系我。"

# Bare thanks/goodbye -- deliberately a WHOLE MESSAGE match, never a substring: "thanks, can
# you also tell me about parking?" must never be mistaken for a close. Punctuation/emoji are
# stripped and whitespace collapsed before comparing, so "Thanks!!" / "ok thanks :)" still hit.
_SIGNOFF_EXACT = frozenset((
    "thanks","thank you","ok thanks","okay thanks","thanks a lot","thank you so much",
    "thanks so much","noted thanks","ok noted thanks","alright thanks","thanks alot",
    "bye","goodbye","bye bye","cheers","ok bye","okay bye","thanks bye","no worries thanks",
    "谢谢","多谢","拜拜",
))
_SIGNOFF_STRIP_RE = re.compile(r"[^\w\s一-鿿]+", re.U)
def _signoff_signal(text):
    """True only when the ENTIRE message (after stripping punctuation/emoji, collapsing
    whitespace, lowercasing) is one of a small set of bare thanks/goodbye phrases -- never a
    substring match, so it can never fire mid-conversation on a message that happens to
    start with "thanks" but goes on to ask something."""
    clean = _SIGNOFF_STRIP_RE.sub(" ", (text or "").lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    return bool(clean) and clean in _SIGNOFF_EXACT

# a bare thanks/谢谢 right after the prospect proposed a viewing time, or while our own offer
# is still open, is a polite acknowledgement, not a withdrawal (Winfred, 11 Sep 2026 attack
# replay: a Chinese couple proposed Saturday 2pm, then said 谢谢 on the very next turn, and
# the record was auto closed WITHDRAWN with notify=False -- a live, about to be booked lead
# silently killed). _has_viewing_time() only reads English day/time words, so this carries
# its own broader check (Chinese weekday/clock phrasing included) rather than widening that
# shared detector's behaviour everywhere else it is used.
_PROPOSED_TIME_OR_OFFER_RE = re.compile(
    r"\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}[:.]\d{2}\b|"
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)\w*\b|\btoday\b|\btomorrow\b|\btmr\b|\bweekends?\b|"
    r"星期[一二三四五六日天]|周[一二三四五六日天]|礼拜[一二三四五六日天]|"
    r"\d{1,2}\s*点|[一二三四五六七八九十]+\s*点|今晚|明天|周末|下午|上午|晚上", re.I)
def _has_open_offer_or_proposed_time(rec, ev):
    if rec.get("viewing_asked") and not rec.get("viewing_confirmed"):
        return True
    cur = ev.get("text") or ""
    prev = rec.get("prev_inbound") or ""
    return bool(_PROPOSED_TIME_OR_OFFER_RE.search(cur) or _PROPOSED_TIME_OR_OFFER_RE.search(prev))

# ---------- terminal record re-notify ----------
# A terminal (closed) conversation must never send the prospect anything again -- but Winfred
# still needs to SEE a fresh enquiry, a mention of another open listing, or a complaint that
# lands on a chat the engine has gone quiet on (adversarial review 9 Sep 2026: a discrimination
# accusation and a listing-flip reply the redirect copy itself invites both vanished with no
# flag at all). notify-only, never a re-send: text always stays None.
_TERMINAL_COMPLAINT_RE = re.compile(
    r"discrimina|\bracist\b|\bracism\b|\bunfair\b|\bcomplain(?:t|ing)?\b|"
    r"report (?:you|this)\b|lodge a (?:complaint|report)|file a complaint", re.I)

# broader than _TERMINAL_COMPLAINT_RE above: also protected attribute FISHING ("does the
# landlord not like Indians, tell me honestly") and a legal threat/escalation, wherever it
# surfaces mid conversation (not only on an already terminal record). Shared by the short
# lease follow up latch so a sensitive message can never be swallowed as routine chatter.
_SENSITIVE_CONTENT_RE = re.compile(
    r"discrimina|\bracist\b|\bracism\b|\bunfair\b|\bcomplain(?:t|ing)?\b|"
    r"report (?:you|this)\b|lodge a (?:complaint|report)|file a complaint|"
    r"\bsue\b|\blawyer\b|legal\s+action|\bpolice\b|small\s+claims|"
    r"tak\s+suka|tidak\s+suka|dont\s+like|don'?t\s+like|doesn'?t\s+like|\bhate\b|"
    r"tell\s+me\s+honestly|be\s+honest|honestly\s+lah", re.I)

# superset of _SENSITIVE_CONTENT_RE: also landlord identity/contact fishing and a
# deposit/payment ask. Shared by the once-per-state notify latches below AND
# _terminal_worth_notifying, so any of these always breaks through a latch that has
# already fired once on unrelated content (P1 fix, 9 Sep 2026 cycle4 hg4-03: a legal-advice
# question and a landlord-name/number fishing attempt both vanished behind an already
# tripped "closed listing" notify latch).
_HIGH_RISK_CONTENT_RE = re.compile(
    _SENSITIVE_CONTENT_RE.pattern + r"|"
    r"landlord'?s?\s+(?:number|phone|handphone|mobile|contact|name|address)|"
    r"who\s+is\s+the\s+landlord|landlord\s+called|"
    r"(?:exact|unit)\s+(?:unit\s+)?(?:number|address)|"
    r"bank\s+transfer|account\s+number|paynow|pay\s+you\s+directly|"
    r"deposit\s+(?:amount|refund)|how\s+much\s+(?:is\s+the\s+)?deposit|"
    r"\bevict(?:ed|ion)?\b|\bcourt\b|what'?s?\s+the\s+(?:actual\s+)?law|\blaw\b|\billegal\b", re.I)

def _high_risk_escalate(rec, latch_key, text):
    """True if TEXT introduces high risk content (legal/advice, landlord identity or contact
    fishing, protected attribute questions, deposit/payment asks) not already seen under this
    latch -- so a once-per-state notify suppression still lets a later, materially different
    high risk message through instead of swallowing it as just another repeat."""
    if not _HIGH_RISK_CONTENT_RE.search(text or ""):
        return False
    sig = re.sub(r"\s+", " ", (text or "").strip().lower())[:120]
    seen_key = latch_key + "_hr_sigs"
    seen = rec.get(seen_key) or []
    if sig in seen:
        return False
    rec[seen_key] = (seen + [sig])[-20:]
    return True

def _terminal_worth_notifying(rec, text):
    """True if a new inbound on a TERMINAL record is a clear tenant enquiry, names a
    different OPEN listing, reads as a complaint/discrimination accusation/legal-advice/
    landlord-fishing question, or is itself a supply-side pivot ("my colleague has a room to
    rent out") -- any of which Winfred must see even though the engine stays silent to the
    prospect."""
    if _HIGH_RISK_CONTENT_RE.search(text or ""):
        return True
    low = (text or "").lower()
    # reuse supply_side_kind's own high-precision phrase sets directly, NOT supply_side_kind()
    # itself -- that function vetoes to None once a record is already bound+profiled, which is
    # exactly the terminal case here (P2 fix, 9 Sep 2026 cycle4 c4ec01: a landlord pivot on a
    # closed thread was dropped because _terminal_worth_notifying knew nothing of it, while a
    # near-identical message on a non-terminal thread already notified).
    if any(m in low for m in _LANDLORD_SUPPLY) or any(m in low for m in _SELLER_SUPPLY):
        return True
    lk = rec.get("listing_key")
    # a narrow, explicit enquiry phrase only (is_enquiry/EXTRA_ENQUIRY_SIGNS) -- NEVER
    # is_tenant_enquiry's listing-bound fallback, which reads almost any non-sale text on a
    # bound record as "an enquiry" and would notify on every single terminal message,
    # including a plain "yes 3pm" the engine must stay silent on.
    if is_enquiry(text) or any(p in low for p in EXTRA_ENQUIRY_SIGNS):
        return True
    reqs = listing_reqs()
    for other_lk in reqs:
        if other_lk == lk or _listing_unavailable(other_lk, reqs):
            continue
        if _text_mentions_listing(text, other_lk, reqs):
            return True
    return False

# ---------- transaction type: RENT vs SALE (two entirely different flows) ----------
# A rental tenant enquiry and a sale buyer enquiry are different things and must not be
# conflated: only a RENT enquiry should ever get the tenant intake form. Signals come
# straight from the portal templates: PropertyGuru emits "RENT - <addr>" / "SALE - <addr>",
# 99.co emits "for Room" (rental) / "for Sale", rentals quote "/mo", sales quote a lump sum.
_SALE_TOKEN_RE = re.compile(r"\bsale\b\s*[-–—:]|\bfor[\s-]+sale\b", re.I)
_RENT_TOKEN_RE = re.compile(r"\brent\b\s*[-–—:]|\bfor[\s-]+rent(?:al)?\b|\bfor[\s-]+room\b", re.I)
_PERMONTH_RE   = re.compile(r"/\s*mo\b|per\s+month|\bp\.?m\.?\b|\bmonthly\b", re.I)
_BEDS_RE       = re.compile(r"\b\d+\s*beds?\b", re.I)
_BUY_KW_RE     = re.compile(r"\b(buy|buying|purchase|purchasing|resale|own\s+stay|investment)\b", re.I)
_RENT_KW_RE    = re.compile(r"\b(rent|rental|renting|lease|leasing|tenant|move.?in|room\s+for\s+rent)\b", re.I)

def _max_sgd(text):
    """Largest S$ amount in the text (a lump-sum price signals a sale). Honours k/m
    suffixes so '$1.5m' is read as 1,500,000 (not 1) and correctly flagged a sale."""
    best = 0
    for m in re.finditer(r"(?:s\$?|\$)\s*(\d[\d,\.]*)\s*([km])?\b", text or "", re.I):
        v = _to_int(m.group(1) + (m.group(2) or ""))
        if v:
            best = max(best, v)
    return best

def classify_transaction(text, listing_key=None):
    """Return ('rent'|'sale'|'unknown', reason). Priority cascade, decisive signal wins.
    The explicit portal token is checked before the registry so that a sale enquiry which
    happens to keyword-match one of our rental listings is not mislabelled as rent."""
    t = text or ""
    s_tok, r_tok = _SALE_TOKEN_RE.search(t), _RENT_TOKEN_RE.search(t)
    if s_tok and not r_tok: return "sale", "portal token '" + s_tok.group(0).strip() + "'"
    if r_tok and not s_tok: return "rent", "portal token '" + r_tok.group(0).strip() + "'"
    # our own listing registry is authoritative for a token-less enquiry bound to a listing
    if listing_key:
        dt = (listing_reqs().get(listing_key, {}) or {}).get("deal_type")
        if dt in ("rent", "sale"): return dt, "registry deal_type"
    if _PERMONTH_RE.search(t): return "rent", "per-month price"
    amt = _max_sgd(t)
    if _BEDS_RE.search(t) and amt >= 100000: return "sale", "bedroom count + lump-sum price"
    if amt >= 50000: return "sale", "lump-sum price"
    if 0 < amt <= 20000: return "rent", "monthly-scale price"
    b, rk = _BUY_KW_RE.search(t), _RENT_KW_RE.search(t)
    if b and not rk: return "sale", "keyword"
    if rk and not b: return "rent", "keyword"
    return "unknown", "no decisive signal"

# HDB vs private decides whether the buyer form asks for HFE (HDB) or IPA (private).
_HDB_PT_RE  = re.compile(r"\b(hdb|bto|sbf|build.?to.?order|resale\s+flat|\bflat\b|hfe|\bmop\b|3\s*room|4\s*room|5\s*room|executive\s+flat|jumbo|dbss)\b", re.I)
_PRIV_PT_RE = re.compile(r"\b(condo|condominium|apartment|executive\s+condo|\bec\b|landed|terrace|semi.?d(?:etached)?|bungalow|freehold|\bprivate\b|\bipa\b|in.?principle|new\s+launch|penthouse|cluster\s+house)\b", re.I)

def classify_property_type(text, listing_key=None):
    """Return 'hdb' | 'private' | 'unknown'. Registry property_type wins; else keywords."""
    if listing_key:
        pt = str((listing_reqs().get(listing_key, {}) or {}).get("property_type") or "").lower()
        if pt:
            if "hdb" in pt: return "hdb"
            if any(x in pt for x in ("condo", "apartment", "landed", "private", "ec", "freehold")): return "private"
    t = text or ""
    h, p = _HDB_PT_RE.search(t), _PRIV_PT_RE.search(t)
    if h and not p: return "hdb"
    if p and not h: return "private"
    return "unknown"

# Every wa.me CTA on the site pre-fills a "Hi Winfred, ..." message with one of a fixed
# set of stock phrases (site-wide audit, 31 Jul 2026). Visitors rarely edit it before
# hitting send, so a match is a strong (not certain) signal of a website-originated lead.
_WEBSITE_CTA_RE = re.compile(
    r"(?i)^\s*hi\s+winfred\s*,?\s*.{0,60}?"
    r"(property question|i'?d like to discuss|i have a question about|i read your article|"
    r"book(?:ing)? (?:the|a) .{0,20}call|net proceeds analysis|valuation report|"
    r"ownership restructuring|asking about|portfolio enquiry)"
)

# The Sun Facing Checker tool (sunfacing.com, mirrored at winfredquek.com/sun-facing-checker)
# pre-fills its own wa.me CTA with either a generic opener or one naming the checked address
# ("...sun facing for <address> on your Sun Facing Checker..."). Checked ahead of the generic
# website CTA regex below so a Sun Facing lead is tagged precisely, not just "website".
_SUN_FACING_SOURCE_RE = re.compile(r"(?i)sun\s*facing\s*checker|sunfacing\.com")

def classify_lead_source(text, listing_key=None):
    """Best-effort FIRST-TOUCH attribution, not a compliance-grade field.
    'portal'             = tied to a PropertyGuru/99.co listing_key.
    'sun-facing-checker' = inbound text names the Sun Facing Checker tool or its domain.
    'website'            = inbound text matches the site's wa.me pre-filled CTA phrasing.
    'unknown' = everything else -- Carousell, referral, and organic WA are today
    indistinguishable from each other, this only rules those two IN when detectable."""
    if listing_key:
        return "portal"
    if text and _SUN_FACING_SOURCE_RE.search(text):
        return "sun-facing-checker"
    if text and _WEBSITE_CTA_RE.search(text):
        return "website"
    return "unknown"

def classify_property_type_ctx(chat_jid, text, listing_key=None):
    """HDB vs private from the current message, falling back to the chat history when the
    current line has no property-type cue (so a buy thread that earlier said 'condo' or
    'HDB' still picks IPA vs HFE)."""
    pt = classify_property_type(text, listing_key)
    if pt != "unknown":
        return pt
    hist = recent_inbound_text(chat_jid)
    if hist:
        return classify_property_type(hist, listing_key)
    return "unknown"

def buyer_form_for(ptype):
    if ptype == "hdb":     return BUYER_FORM_HDB
    if ptype == "private": return BUYER_FORM_PRIVATE
    return BUYER_FORM_UNKNOWN

# ---------- buyer stage 2: parse the returned form, nudge once, hand off to Winfred ----------
# Must-knows for a buyer: name, budget, financing readiness (HFE/IPA). The bot NEVER
# advises a buyer (CEA role boundary: admin/coordination only) — a complete profile is
# handed to Winfred and the buyer is told he will be in touch personally.
BUYER_REQUIRED = ["name", "budget", "financing"]
_FIN_LINE_RE  = re.compile(r"(?im)^[•\s]*(?:hfe|ipa)[^:\n]*[:：]\s*(\S.*)$")
# free-text answer: status word must NOT be the label echo ("HFE valid?:" is a question,
# not an answer — a partially copied form must never read as financing='valid')
_FIN_FREE_RE  = re.compile(r"\b(?:hfe|ipa)\b[^.\n?？]{0,30}?\b(valid|approved|done|yes|in progress|applying|applied|pending|expired|no|not yet|none|dont have|don't have)\b(?!\s*[?？:：])", re.I)
# negation BEFORE the token: "dont have hfe yet", "haven't applied for ipa"
_FIN_NEGFIRST_RE = re.compile(r"\b(no|not|don'?t have|dont have|haven'?t|havent|yet to(?: get| apply)?|applying for|waiting for)\b[^.\n]{0,25}?\b(hfe|ipa)\b", re.I)
_FIN_NEG_RE   = re.compile(r"\b(no|not|haven'?t|havent|dont|don't|yet to|pending|applying)\b", re.I)

_BUY_BUD_LINE = re.compile(r"(?im)^[•\s]*bu[dg]{1,3}et[^:\n]*[:：]\s*(\S.*)$")   # tolerates 'bugdet'/'budjet'
_BUY_BUD_FREE = re.compile(r"\b(?:budget|around|up to|max)\s*(?:is|of|:)?\s*\$?\s*([\d.,]+\s*(?:k|m|mil|million)?)\b", re.I)
_MONEY_TOKEN  = re.compile(r"\$\s*([\d.,]+\s*(?:k|m|mil|million)?)|\b([\d.,]+\s*(?:k|m|mil|million))\b", re.I)
def _one_amount(raw):
    if not raw: return None
    b = _to_int(re.sub(r"(?i)\b(mil|million)\b", "m", str(raw)))
    if b and 50_000 <= b <= 50_000_000: return b
    # bare decimal shorthand: 'Budget: 1.5' means 1.5M (whole-number bares stay ambiguous -> nudge)
    try:
        f = float(str(raw).replace(",", "").rstrip("."))
        if 0.3 <= f <= 9.9 and "." in str(raw): return int(f * 1_000_000)
    except ValueError:
        pass
    return None

def _buyer_budget(text):
    """Purchase budgets are 6 to 8 figures — tolerant of typo'd labels, $ prefixes, k/m
    suffixes, bare-decimal millions, and ranges (a range reads as the UPPER bound: that is
    their capacity). The tenant extractor's rent bound silently dropped all of these."""
    t = text or ""
    m = _BUY_BUD_LINE.search(t) or _BUY_BUD_FREE.search(t)
    if m:
        vals = [v for v in (_one_amount(g1 or g2) for g1, g2 in _MONEY_TOKEN.findall(m.group(1)))
                if v] or [_one_amount(m.group(1))]
        vals = [v for v in vals if v]
        if vals: return max(vals)
    # no recognizable label: any unambiguous purchase-scale money token in the message
    vals = [v for v in (_one_amount(g1 or g2) for g1, g2 in _MONEY_TOKEN.findall(t)) if v]
    return max(vals) if vals else None

_NAME_FREE_RE = re.compile(r"\b(?:i'?m|i am|this is|my name is|call me)\s+([A-Za-z][a-zA-Z]{1,20}(?:\s[A-Z][a-zA-Z]{1,20}){0,2})\b")
def extract_buyer(text):
    """Buyer-form fields from a filled block or free text. Reuses the tenant extractor
    for name (plus free-form 'im Ken'); buyer-scale budget; financing (HFE/IPA) status."""
    p = extract_profile(text)
    out = {}
    if p.get("name"):   out["name"] = p["name"]
    else:
        nm = _NAME_FREE_RE.search(text or "")
        if nm: out["name"] = nm.group(1).strip()
    bud = _buyer_budget(text)
    if bud: out["budget"] = bud
    t = text or ""
    neg = _FIN_NEGFIRST_RE.search(t)
    m = _FIN_LINE_RE.search(t) or _FIN_FREE_RE.search(t)
    if m and (m.group(1) or "").strip().rstrip("?？:："):
        val = (m.group(1) or "").strip()
        out["financing"] = ("not ready: " + val) if _FIN_NEG_RE.search(val) else val
    elif neg:
        out["financing"] = "not ready: " + neg.group(0).strip()
    for label, key in (("citizenship", "citizenship"), ("timeline", "timeline"),
                       ("area|district", "area"), ("bedrooms?", "bedrooms"),
                       ("own stay|investment", "purpose")):
        lm = re.search(r"(?im)^[•\s]*(?:" + label + r")[^:\n]*[:：]\s*(\S.*)$", t)
        if lm: out[key] = lm.group(1).strip()
    return out

def _buyer_followup(rec, ev, pn):
    """After the buyer form went out: merge fields, nudge ONCE for must-knows, then hand
    the complete profile to Winfred. All questions are flagged, never answered (no advice)."""
    b = rec.setdefault("buyer", {})
    for k, v in extract_buyer(ev.get("text", "")).items():
        if not b.get(k): b[k] = v
    # a buyer naming a day/time to view must reach Winfred, complete profile or not — the
    # old flow swallowed it (silent handoff still holds: no message goes to the buyer)
    if (_has_viewing_time((ev.get("text") or "").lower())
            and not rec.get("buyer_time_flagged")):
        rec["buyer_time_flagged"] = True
        return {"type": "VIEWING_TIME_PROPOSED", "pn": pn, "when": ev.get("text"),
                "notify": True, "text": None}
    if rec.get("buyer_complete"):
        if "?" in (ev.get("text") or ""):
            return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True, "text": None,
                    "question": ev.get("text")}
        return None
    miss = [k for k in BUYER_REQUIRED if not b.get(k)]
    if not miss:
        # complete -> SILENT handoff: no message to the buyer at all (Winfred's rule,
        # 11 Jul 2026) — just the structured ping; he takes the conversation from here.
        rec["buyer_complete"] = True
        rec["stage"] = "BUYER_COMPLETE"; rec["status"] = "buyer_complete"
        summary = "; ".join(f"{k}: {v}" for k, v in b.items())
        return {"type": "BUYER_COMPLETE", "pn": pn, "notify": True, "summary": summary,
                "text": None}
    if "?" in (ev.get("text") or ""):
        return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True, "text": None,
                "question": ev.get("text")}
    import time as _t
    if rec.get("buyer_nudged"):
        # GRACE: give them time to answer the nudge before flagging "still missing after
        # nudge" — the flag fired 7 seconds after the nudge on a message burst (12 Jul 2026).
        if rec.get("buyer_nudged_ts") and _t.time() - rec["buyer_nudged_ts"] < 180:
            return None
        if not rec.get("buyer_incomplete_flagged"):
            rec["buyer_incomplete_flagged"] = True
            return {"type": "FLAG_HUMAN", "pn": pn, "text": None, "notify": True,
                    "reason": "buyer still missing " + ", ".join(miss) + " after nudge; reply by hand"}
        return None
    # GRACE: same 3 minute rule as the tenant flow — a message that arrived alongside the
    # enquiry must not trigger an instant "Almost there" right behind the buyer form
    # (real case: buyer form then nudge 69 seconds apart, 12 Jul 2026).
    if rec.get("buyer_form_sent_ts") and _t.time() - rec["buyer_form_sent_ts"] < 180:
        return None
    rec["buyer_nudged"] = True
    rec["buyer_nudged_ts"] = _t.time()
    labels = {"name": "your name", "budget": "your budget",
              "financing": "your HFE or IPA status (valid / applying / not yet)"}
    return {"type": "BUYER_NUDGE", "pn": pn, "notify": False,
            "text": "Almost there :) I still need " + ", ".join(labels[k] for k in miss)
                    + " so Winfred can prepare properly before speaking with you."}

# ---------- supply side (landlord renting out / seller selling): send THEIR intake form ----------
@functools.lru_cache(maxsize=2)
def _supply_form(kind):
    """Landlord onboarding / seller intake form text, verbatim from _templates. Returns
    None if the template file is unavailable (caller falls back to flag-only)."""
    path = os.path.expanduser("~/crestbrick-consult/_templates/"
                              + ("landlord-onboarding.md" if kind == "landlord" else "seller-intake.md"))
    try:
        with open(path) as f:
            txt = f.read().strip()
        return txt or None
    except OSError:
        return None

# ========== LANDLORD ONBOARDING (extension of the supply side branch) ==========
# Reactive continuation AFTER the onboarding form (_supply_form) has already gone out and
# manual_takeover has latched. That latch means only "never re run the tenant/buyer flow on
# this record" here -- it is NOT "Winfred replied by hand" (human_takeover is the real signal
# for that, set only on a genuine hand reply). Extraction based throughout: never depends on
# the landlord echoing the onboarding form's own field labels back. Anti spam is a hard
# requirement -- every send below is capped at most once ever per landlord; a human reply
# silences the whole sequence immediately (checked by the caller before this runs).

LANDLORD_NUDGE_CAP = 1          # ONE missing fields nudge, ever. Then FLAG_HUMAN, no more sends.
LANDLORD_MEDIA_CHASE_CAP = 1    # ONE media chase, ever. Then FLAG_HUMAN, no more sends.
LANDLORD_MEDIA_CHASE_DELAY_SEC = 48 * 3600   # ~2 days after the media ask before chasing once

# Winfred, 6 Sep 2026: the six fields that gate "info complete". Block ONLY on these -- any
# other field the landlord volunteers is captured into supply_profile but never blocks.
LANDLORD_REQUIRED_FIELDS = ("address", "rent", "pax", "tenant_type", "gender_pref", "lease_months")
_LANDLORD_FIELD_ASK = {
    "address":      "the unit address",
    "rent":         "the asking rent",
    "pax":          "the max number of pax you'll allow",
    "tenant_type":  "what type of tenant you prefer (working professional, student, couple or family)",
    "gender_pref":  "your gender preference for the tenant",
    "lease_months": "the minimum lease you're willing to offer",
}

LANDLORD_MEDIA_ASK = (
    "Thanks, that is everything I need for now! Last thing, could you send a few photos of "
    "each room and the common areas, plus a short video walking through the unit? This helps "
    "me match the right tenants and cuts down on unnecessary viewings."
)
LANDLORD_MEDIA_CHASE = (
    "Just checking in, still keen to send a few photos and a short video of the unit when you "
    "have a moment? This really helps tenants picture the space before viewing."
)

_TENANT_TYPE_KEYWORDS = (
    ("working professional", "working professional"), ("professional", "working professional"),
    ("student", "student"), ("couple", "couple"), ("family", "family"),
    ("no preference", "any"), ("no pref", "any"), (" any ", "any"),
)

_MONEY_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?[km]?\b"

def _parse_landlord_rent(text):
    """Confident asking rent extraction only: a dollar amount, or a bare number tied to a
    rent/asking/monthly context word. An ambiguous number (a pax count, a postal code, a
    phone number) must never be silently read as the rent. The trailing \\b on _MONEY_NUM is
    load bearing -- without it "asking 1200, max 2 pax" greedily read the "m" off "max" as a
    million multiplier and silently produced a billion dollar rent (caught in testing)."""
    t = text or ""
    for pat in (
        r"asking\s*(?:rent|price)?\s*(?:is|:|=)?\s*\$?\s*(" + _MONEY_NUM + r")",
        r"rent(?:al)?\s*(?:is|:|=)?\s*\$\s*(" + _MONEY_NUM + r")",
        r"\$\s*(" + _MONEY_NUM + r")\s*(?:/|a|per)?\s*(?:month|mth|mo)?\b",
        r"(" + _MONEY_NUM + r")\s*(?:/|a|per)\s*(?:month|mth|mo)\b",
    ):
        m = re.search(pat, t, re.I)
        if m:
            v = _to_int(m.group(1))
            if v and 300 <= v <= 15000:
                return v
    return None

_STREET_WORDS = (r"\b(?:street|st|road|rd|avenue|ave|drive|dr|close|crescent|cres|lane|walk|way|"
                 r"park|place|pl|boulevard|blvd|terrace|view|hill|rise|grove|gardens?)\b")

def _parse_landlord_address(text):
    """Confident address extraction only: a block/street number next to a recognised street
    type word, OR a 6 digit SG postal code sitting alongside a street word or an explicit S
    prefix (bounded to a short window around the code, not the whole line/message). A bare
    number elsewhere (rent, pax, phone) must never be read as an address."""
    t = text or ""
    m2 = re.search(r"\b(?:blk|block)\s*\d+[a-z]?\b[^\n,]{0,60}?" + _STREET_WORDS + r"\b(?:\s*\d+)?", t, re.I)
    if m2:
        return m2.group(0).strip()[:120]
    m3 = re.search(r"\b\d{1,4}[a-z]?\s+[a-z][a-z\s]{2,30}?" + _STREET_WORDS + r"\b(?:\s*\d+)?", t, re.I)
    if m3:
        return m3.group(0).strip()[:120]
    for line in (t.splitlines() or [t]):
        m6 = re.search(r"\bS?(\d{6})\b", line)
        if m6 and (line[:m6.start()].strip().upper().endswith("S") or "s" + m6.group(1) in line.lower()
                   or re.search(_STREET_WORDS, line, re.I) or re.search(r"\bblk\b|\bblock\b", line, re.I)):
            start = max(0, m6.start() - 60)
            return line[start:m6.end()].strip()[:120]
    return None

def _parse_landlord_pax(text):
    t = (text or "").lower()
    m = re.search(r"\b(?:max|up\s*to|maximum)\s*(\d{1,2})\s*(?:pax|persons?|people|occupants?)\b", t)
    if not m:
        m = re.search(r"\b(\d{1,2})\s*(?:pax|persons?|people|occupants?)\s*(?:max|maximum)?\b", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 10:
            return n
    return None

def _parse_landlord_tenant_type(text):
    t = " " + (text or "").lower() + " "
    for kw, norm in _TENANT_TYPE_KEYWORDS:
        if kw in t:
            return norm
    return None

def _parse_landlord_gender_pref(text):
    """Landlord's TENANT gender preference. Adapted from
    scripts/sync_listing_index_from_landlords.py::parse_gender (kept local, not imported
    cross directory, to avoid coupling a live production module to a scripts/ helper --
    update BOTH if the label vocabulary changes)."""
    t = (text or "").strip()
    if not t:
        return None
    tl = t.lower()
    if "no male" in tl or "no males" in tl or "female only" in tl or "females only" in tl:
        return "female_only"
    if "no female" in tl or "no females" in tl or "male only" in tl or "males only" in tl:
        return "male_only"
    if "female pref" in tl or "prefer female" in tl or "females preferred" in tl:
        return "female_pref"
    if "male pref" in tl or "prefer male" in tl or "males preferred" in tl:
        return "male_pref"
    if re.search(r"\bany\b", tl) or "no pref" in tl or "either" in tl or "no restriction" in tl:
        return "any"
    return None

def _parse_landlord_lease_months(text):
    """Confident lease duration extraction only: a number tied to a lease/minimum/term
    context word plus a year/month unit. A bare number elsewhere (rent, pax, address) must
    never be read as the lease term."""
    t = (text or "").lower()
    m = (re.search(r"(?:min(?:imum)?|at\s*least)\s*(\d{1,2})\s*(year|yr|month|mth)s?\b", t)
         or re.search(r"\b(\d{1,2})\s*(year|yr|month|mth)s?\s*(?:lease|min(?:imum)?|term)\b", t)
         or re.search(r"\blease\s*(?:term|duration|of)?\s*(?:is|:|=)?\s*(\d{1,2})\s*(year|yr|month|mth)s?\b", t))
    if not m:
        return None
    n = int(m.group(1)); unit = m.group(2)
    months = n * 12 if unit.startswith("y") else n
    return months if 1 <= months <= 36 else None

def extract_landlord_supply_info(text):
    """Best effort, HIGH PRECISION extraction of the six onboarding readiness fields from the
    landlord's free text -- never depends on the landlord using the onboarding form's own
    field labels (a landlord who just types naturally must still be read correctly). A field
    that cannot be extracted with confidence is left out entirely -- never a guessed rent or
    address (a wrong value silently taken as the asking rent or address would poison
    matching and Winfred's downstream records)."""
    t = text or ""
    out = {}
    rent = _parse_landlord_rent(t)
    if rent is not None: out["rent"] = rent
    addr = _parse_landlord_address(t)
    if addr: out["address"] = addr
    pax = _parse_landlord_pax(t)
    if pax is not None: out["pax"] = pax
    tt = _parse_landlord_tenant_type(t)
    if tt: out["tenant_type"] = tt
    gp = _parse_landlord_gender_pref(t)
    if gp: out["gender_pref"] = gp
    lm = _parse_landlord_lease_months(t)
    if lm is not None: out["lease_months"] = lm
    return out

def _landlord_missing_fields(profile):
    return [f for f in LANDLORD_REQUIRED_FIELDS if (profile or {}).get(f) in (None, "")]

_LANDLORD_NEGOTIATION_HINTS = ("commission", "brokerage", "your fee", "the fee", "% fee", "lower your", "lower the")

def _landlord_looks_like_question(text):
    """A landlord question OR a negotiation attempt must always FLAG_HUMAN and never get an
    auto reply (CEA boundary: Claude never negotiates or advises on Winfred's behalf)."""
    t = (text or "").lower()
    if "?" in t:
        return True
    return any(k in t for k in _LANDLORD_NEGOTIATION_HINTS)

def _landlord_nudge_text(missing):
    labels = [_LANDLORD_FIELD_ASK[f] for f in missing if f in _LANDLORD_FIELD_ASK]
    if not labels:
        return None
    joined = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return "Almost there, I just need " + joined + " so I can start matching tenants for you."

@functools.lru_cache(maxsize=1)
def _phone_to_llid():
    """phone (digits only) -> LLxxx id, from the live landlord DB. Used only to check for
    curated media on disk once a landlord has been assigned an id by the nightly refresh or
    scripts/new_landlord.py -- the onboarding flow itself never allocates an id."""
    try:
        d = json.load(open(_landlord_db()))
    except Exception:
        return {}
    out = {}
    for l in d.get("landlords", []):
        ph = re.sub(r"\D", "", str(l.get("phone") or ""))
        lid = l.get("id")
        if ph and lid:
            out[ph] = str(lid)
    return out

MATCHMAKER_PHOTOS_DIR = os.path.expanduser("~/crestbrick-consult/scripts/matchmaker/deploy/photos")
MATCHMAKER_VIDEO_STILLS_DIR = os.path.expanduser("~/crestbrick-consult/scripts/matchmaker/deploy/video-stills")

def _landlord_media_status(pn, chat_jid):
    """(photos_received, video_received), best effort. PRIMARY signal: an image/video the
    landlord has actually sent in THIS WA chat -- works from message one, before any
    landlord-db id exists. SECONDARY: once the phone is linked to an LLID (assigned later,
    outside this flow), also check the curated filesystem folders
    (scripts/matchmaker/deploy/photos/<LLID>/, deploy/video-stills/<LLID>/) so a record whose
    photos get curated there is never chased again. Fails safe (False, False) on any error --
    never invents a completed media state."""
    photos = video = False
    try:
        con = sqlite3.connect(_msg_db(), timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        rows = con.execute(
            "SELECT media_type FROM messages WHERE chat_jid=? AND is_from_me=0 "
            "ORDER BY timestamp DESC LIMIT 200", (chat_jid,)).fetchall()
        con.close()
        photos = any((mt or "") == "image" for (mt,) in rows)
        video = any((mt or "") == "video" for (mt,) in rows)
    except Exception:
        pass
    llid = _phone_to_llid().get(re.sub(r"\D", "", str(pn or "")))
    if llid:
        try:
            pdir = os.path.join(MATCHMAKER_PHOTOS_DIR, llid)
            if os.path.isdir(pdir) and any(True for _ in os.scandir(pdir)):
                photos = True
        except Exception:
            pass
        try:
            vdir = os.path.join(MATCHMAKER_VIDEO_STILLS_DIR, llid)
            if os.path.isdir(vdir) and any(True for _ in os.scandir(vdir)):
                video = True
        except Exception:
            pass
    return photos, video

_LANDLORD_DB_SCHEMA_FIELDS = ("onboarding_stage", "info_complete", "photos_received",
                              "video_received", "media_requested_at", "followup_nudges_sent")
_landlord_db_backup_done = False   # process lifetime guard: one backup per run, not per write

def _backup_landlord_db_once():
    global _landlord_db_backup_done
    if _landlord_db_backup_done:
        return
    try:
        import shutil, time as _t
        landlord_db = _landlord_db()
        if os.path.exists(landlord_db):
            stamp = _t.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(landlord_db, landlord_db + ".bak-onboarding-" + stamp)
    except Exception:
        pass
    _landlord_db_backup_done = True

def _sync_landlord_db_fields(pn, rec):
    """Best effort mirror of onboarding progress onto an EXISTING landlord-db.json record
    (matched by phone). Never CREATES a record -- landlord ids are allocated solely by the
    nightly refresh / scripts/new_landlord.py, and inventing one here risks a collision with
    that pipeline. If no record exists yet, this is a no-op and the intake-state.json record
    stays the source of truth; a later sync call (next stage transition, or the sweep) tries
    again. Always preserves every sibling key -- the file is a dict, never dumped as a bare
    list. Backs up the file once per process before the first write."""
    landlord_db = _landlord_db()
    try:
        d = json.load(open(landlord_db))
    except Exception:
        return False
    target_ph = re.sub(r"\D", "", str(pn or ""))
    if not target_ph:
        return False
    hit = False
    for l in d.get("landlords", []):
        if re.sub(r"\D", "", str(l.get("phone") or "")) == target_ph:
            _backup_landlord_db_once()
            import time as _t
            l["onboarding_stage"] = rec.get("stage")
            l["info_complete"] = bool(rec.get("info_complete"))
            l["photos_received"] = bool(rec.get("photos_received"))
            l["video_received"] = bool(rec.get("video_received"))
            if rec.get("media_requested_at"):
                l["media_requested_at"] = _t.strftime("%Y-%m-%dT%H:%M:%S",
                                                      _t.localtime(rec["media_requested_at"]))
            l["followup_nudges_sent"] = int(rec.get("followup_nudges_sent") or 0)
            hit = True
    if not hit:
        return False
    tmp = landlord_db + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, landlord_db)
    return True

def _rec_supply_kind(rec):
    """'landlord' / 'seller' / None. supply_kind is the field new records carry; older
    records written before this field existed only have it encoded in status
    ('supply_side:landlord') -- derive it from there so nothing needs a migration."""
    if rec.get("supply_kind"):
        return rec["supply_kind"]
    st = str(rec.get("status") or "")
    if st.startswith("supply_side:"):
        return st.split(":", 1)[1]
    return None

def _landlord_onboarding_reaction(rec, ev, pn):
    """Reactive continuation of the landlord supply side flow, run for every inbound message
    on a record already past SEND_SUPPLY_FORM (manual_takeover latched for the supply side
    reason, never a Winfred hand reply -- the caller checks human_takeover first and never
    calls this once it is set). A question or negotiation always flags to Winfred and never
    auto answers (CEA boundary). Every send below fires at most once ever per landlord."""
    import time as _t
    text = ev.get("text") or ""
    sp = rec.setdefault("supply_profile", {})
    for k, v in extract_landlord_supply_info(text).items():
        if sp.get(k) in (None, ""):
            sp[k] = v                     # never overwrite a value already captured

    if rec.get("stage") == "SUPPLY_READY":
        # nothing left to automate; only a genuine question still deserves a fresh flag --
        # anything else would just re ping Winfred on every later message with no new state.
        if _landlord_looks_like_question(text):
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                    "reason": "landlord (already ready to list) asked a question; needs a human reply"}
        return None

    if _landlord_looks_like_question(text):
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": "landlord asked a question or raised terms mid onboarding; needs "
                          "a human reply (CEA boundary, never auto answered)"}

    photos, video = _landlord_media_status(pn, ev.get("jid"))
    if photos: rec["photos_received"] = True
    if video: rec["video_received"] = True

    missing = _landlord_missing_fields(sp)
    if missing:
        if rec.get("stage") != "SUPPLY_INFO_INCOMPLETE":
            rec["stage"] = "SUPPLY_INFO_INCOMPLETE"; rec["status"] = "supply_info_incomplete"
        if rec.get("followup_nudges_sent", 0) >= LANDLORD_NUDGE_CAP:
            _sync_landlord_db_fields(pn, rec)
            if rec.get("info_cap_flagged"):
                return None    # already flagged once -- never re ping Winfred every later message
            rec["info_cap_flagged"] = True
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                    "reason": "landlord onboarding info still incomplete after the nudge cap ("
                              + ", ".join(missing) + "); needs a human follow up"}
        rec["followup_nudges_sent"] = rec.get("followup_nudges_sent", 0) + 1
        _sync_landlord_db_fields(pn, rec)
        return {"type": "SUPPLY_INFO_NUDGE", "pn": pn, "notify": True,
                "text": _landlord_nudge_text(missing),
                "reason": "landlord onboarding info incomplete; nudged for " + ", ".join(missing)}

    # all six required fields present
    rec["info_complete"] = True
    if not rec.get("media_requested_at"):
        rec["stage"] = "SUPPLY_MEDIA_REQUESTED"; rec["status"] = "supply_media_requested"
        rec["media_requested_at"] = _t.time()
        _sync_landlord_db_fields(pn, rec)
        return {"type": "SUPPLY_MEDIA_ASK", "pn": pn, "notify": True, "text": LANDLORD_MEDIA_ASK,
                "reason": "landlord onboarding info complete; asked for photos and video"}

    if rec.get("photos_received"):
        rec["stage"] = "SUPPLY_READY"; rec["status"] = "supply_ready"
        _sync_landlord_db_fields(pn, rec)
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": "landlord ready to list: info complete and photos received"}

    age = _t.time() - rec["media_requested_at"]
    if age >= LANDLORD_MEDIA_CHASE_DELAY_SEC:
        if rec.get("media_chase_sent"):
            _sync_landlord_db_fields(pn, rec)
            if rec.get("media_cap_flagged"):
                return None    # already flagged once -- never re ping Winfred every later message
            rec["media_cap_flagged"] = True
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                    "reason": "landlord still has not sent photos/video after the chase; "
                              "needs a human follow up"}
        rec["media_chase_sent"] = True
        rec["stage"] = "SUPPLY_MEDIA_CHASE"; rec["status"] = "supply_media_chase"
        _sync_landlord_db_fields(pn, rec)
        return {"type": "SUPPLY_MEDIA_CHASE", "pn": pn, "notify": True, "text": LANDLORD_MEDIA_CHASE,
                "reason": "landlord onboarding media still missing after 2 days; sent one chase"}
    _sync_landlord_db_fields(pn, rec)
    return None   # too soon to chase yet, media still pending -- stay silent this turn

def get_landlord_media_chase_actions(state, now_ts=None):
    """PURE scan, never sends: landlords sitting in SUPPLY_MEDIA_REQUESTED past the ~2 day
    chase delay with no media and no chase sent yet. Returns a list of action dicts a caller
    (the runner's own tick, or a FUTURE launchd slot -- not wired by this change) can push
    through the SAME send gates as everything else (manual_takeover carve out, quiet hours,
    daily cap, DRY_RUN). Marks the state as soon as an action is decided (same "advance on
    decision, not on send" convention DRY_RUN relies on elsewhere in this engine). Skips any
    human_takeover or terminal record -- a hand reply silences this too."""
    import time as _t
    now_ts = now_ts if now_ts is not None else _t.time()
    out = []
    for pn, rec in (state.get("conversations") or {}).items():
        if not rec.get("supply_flagged") or _rec_supply_kind(rec) != "landlord":
            continue
        if rec.get("human_takeover") or rec.get("terminal"):
            continue
        if rec.get("stage") != "SUPPLY_MEDIA_REQUESTED":
            continue
        if rec.get("media_chase_sent") or rec.get("photos_received"):
            continue
        mra = rec.get("media_requested_at")
        if not mra or (now_ts - mra) < LANDLORD_MEDIA_CHASE_DELAY_SEC:
            continue
        rec["media_chase_sent"] = True
        rec["stage"] = "SUPPLY_MEDIA_CHASE"; rec["status"] = "supply_media_chase"
        _sync_landlord_db_fields(pn, rec)
        out.append({"type": "SUPPLY_MEDIA_CHASE", "pn": pn, "notify": True,
                    "text": LANDLORD_MEDIA_CHASE,
                    "reason": "landlord onboarding media still missing after 2 days (sweep)"})
    return out

def recent_inbound_text(chat_jid, limit=25):
    """Concatenate a contact's recent INBOUND messages (excludes our own echoed bot sends)
    so intent can be read from the whole thread, not just the latest line. Best-effort:
    any DB error returns '' (caller falls back to the single message)."""
    if not chat_jid:
        return ""
    try:
        con = sqlite3.connect(_msg_db(), timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        rows = con.execute(
            "SELECT content FROM messages WHERE chat_jid=? AND is_from_me=0 "
            "AND content IS NOT NULL AND content!='' ORDER BY timestamp DESC LIMIT ?",
            (chat_jid, limit)).fetchall()
        con.close()
    except Exception:
        return ""
    parts = [c for (c,) in rows if c and not is_bot_message(c)]
    return " \n ".join(reversed(parts))[:2500]

def recent_inbound_media(chat_jid, limit=25):
    """(n_image, n_text) over a contact's recent inbound messages. Landlords (often via
    Carousell) typically open with a PHOTO of their unit and no enquiry text, whereas a
    tenant/buyer leads with text (a portal link or a question)."""
    if not chat_jid:
        return (0, 0)
    try:
        con = sqlite3.connect(_msg_db(), timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        rows = con.execute(
            "SELECT media_type, content FROM messages WHERE chat_jid=? AND is_from_me=0 "
            "ORDER BY timestamp DESC LIMIT ?", (chat_jid, limit)).fetchall()
        con.close()
    except Exception:
        return (0, 0)
    n_img = sum(1 for mt, c in rows if (mt or "") == "image")
    n_txt = sum(1 for mt, c in rows if (c or "").strip() and not is_bot_message(c))
    return (n_img, n_txt)

def classify_intent(chat_jid, text, listing_key, rec):
    """Conversation-aware rent/sale. A DECISIVE signal in the current message always wins
    (precision preserved). An AMBIGUOUS current message is resolved from (1) the intent
    already locked earlier in this thread, then (2) the prospect's chat history. Mixed
    buy+rent history stays 'unknown' (classify_transaction needs one side, not both)."""
    tx, why = classify_transaction(text, listing_key)
    if tx != "unknown":
        rec["intent"] = tx
        return tx, why
    if rec.get("intent") in ("sale", "rent"):
        return rec["intent"], "thread intent (" + rec["intent"] + ")"
    hist = recent_inbound_text(chat_jid)
    if hist:
        htx, hwhy = classify_transaction(hist, listing_key)
        if htx != "unknown":
            rec["intent"] = htx
            return htx, "chat history (" + hwhy + ")"
    return "unknown", why

BOT_SIGNATURES = ("pls fill this in","fill this in","still available","✅ suits","📲 more listings","available viewing",
    "keen to view? i can put you in","are you free to view on","i can arrange for viewing",
    "to confirm your viewing slot with the landlord",
    "can i just check your","just need your profile above","ok can, your viewing is on",
    "what time will you be coming? i will keep","on your question, let me check with the owner",
                  "your viewing is confirmed","profile does not match","the next viewing is",
                  "you fit what the landlord","when are you able to view","i will send your profile",
                  "good news, there is a viewing","i will hold that slot","reply yes to take this slot",
                  "following up on your rental enquiry","let me confirm that slot with the owner",
                  # full nudge prefixes, not the bare "almost there" a prospect texts en route
                  "almost there :) to send your profile","almost there :) i still need",
                  "could you confirm this so i can send your profile",
                  "by sharing these details you agree",
                  "more rooms available on my rental channel",   # pre 11 Sep 2026 wording, kept for old rows
                  "i have more than 30 rooms available on my channel",
                  "i have many rooms available on my channel",
                  # landlord onboarding extension (never mistake our own send for a landlord reply)
                  "almost there, i just need",
                  "thanks, that is everything i need for now",
                  "just checking in, still keen to send a few photos",
                  # Chinese first touch (Winfred, 11 Sep 2026) -- mirrors every English
                  # signature above so an echoed Chinese send is never read as a manual reply.
                  "请填写以下资料，方便我把您的资料发给房东",
                  "为了跟房东确认您的看房时间，我需要您的资料",
                  "方便过来看房吗？我可以帮您安排，时间是",
                  "谢谢，您的条件符合房东的要求。我现在就把您的资料发给房东。",
                  "你好 :) 谢谢您提供的资料。在把您的资料发给房东之前",
                  "跟您分享一下，房东希望租期至少一年",
                  "我的频道里有超过30间房间可供选择",
                  "我的频道里有很多房间可供选择")
def is_bot_message(text):
    """True if an outbound message was sent by THIS engine (so it is not a manual reply by Winfred)."""
    return any(b in (text or "").lower() for b in BOT_SIGNATURES)

# Exact starts of messages ONLY the engine composes. Used to classify OUTBOUND rows: a manual
# reply by Winfred that merely CONTAINS a loose phrase ("still available", "almost there")
# must NOT be classified as an engine send, or manual_takeover never latches and the bot
# talks over him. Loose substring matching (BOT_SIGNATURES) remains for inbound echo detection.
_ENGINE_PREFIXES = (
    "pls fill this in so i can send your profile to the landlord",
    "to confirm your viewing slot with the landlord i just need your profile",
    "ok can, your viewing is on",
    "what time will you be coming? i will keep your slot",
    "see you then, i will send the unit number nearer",
    "on your question, let me check with the owner",
    "pls complete the form above so i can send your profile",
    "meanwhile pls complete the form above",
    "can i just check your",
    "just need your profile above and i can confirm",
    "no worries, which day and time would work better",
    "viewing slot:",
    "are you free to view on",
    "thanks for your enquiry :) to match you to the right unit",
    "happy to set up a viewing :) just drop me",
    "thanks. just need a couple more details to send to the landlord",
    "almost there :) to send your profile to the landlord i still need",
    "thanks for sending this :)",
    "no problem 🙂 i have another room nearby",
    "no worries 🙂 you can see my other available rooms",
    "thanks, you fit what the landlord is looking for",
    "more rooms available on my rental channel",   # pre 11 Sep 2026 wording, kept for old rows
    "i have more than 30 rooms available on my channel",
    "i have many rooms available on my channel",
    # landlord onboarding extension
    "almost there, i just need",
    "thanks, that is everything i need for now",
    "just checking in, still keen to send a few photos",
    # Chinese first touch (Winfred, 11 Sep 2026) -- exact starts of every Chinese send,
    # same reasoning as the English entries above.
    "请填写以下资料，方便我把您的资料发给房东",
    "为了跟房东确认您的看房时间，我需要您的资料",
    "方便过来看房吗？我可以帮您安排，时间是",
    "谢谢，您的条件符合房东的要求。我现在就把您的资料发给房东。",
    "你好 :) 谢谢您提供的资料。在把您的资料发给房东之前",
    "跟您分享一下，房东希望租期至少一年",
    "我的频道里有超过30间房间可供选择",
    "我的频道里有很多房间可供选择",
)
def _template_heads():
    """Cached lowercase first-80-chars of every listing unit message (message 1 sends)."""
    global _TPL_HEADS
    try:
        return _TPL_HEADS
    except NameError:
        pass
    heads = []
    d = _load(_templates(), {"listings": []})
    for l in d.get("listings", []):
        msg = (l.get("message") or "")
        i = msg.lower().find("pls fill this in")
        head = (msg[:i] if i > 0 else msg).strip().lower()
        if head: heads.append(head[:80])
    _TPL_HEADS = tuple(heads)
    return _TPL_HEADS

# Outbound rows composed by OTHER sanctioned automations on this number (not the engine,
# not Winfred's hands). These must NOT latch manual takeover: the PG enquiry auto-ack fires
# on every portal enquiry, and treating it as a hand reply muted the bot (copilot_muted)
# on every portal lead — found via 14-day replay, 26 Jul 2026. Both apostrophe variants.
_AUTOMATION_PREFIXES = (
    "hi, i'm winfred quek. i received your enquiry from",
    "hi, i’m winfred quek. i received your enquiry from",
)

# WhatsApp clients silently thread zero width / bidi formatting marks through a pasted
# bulleted list (word joiner around the bullet, a leading LTR mark on the whole message) —
# invisible, but they defeat an exact .startswith() prefix or label match. A 7 day replay
# (8 Sep 2026) found 47 of 66 outbound form pastes carrying them. Strip before comparing.
_INVISIBLE_CHARS = ("⁠", "​", "‌", "‍", "﻿", "‎", "‏")

def _strip_invisible(text):
    if not text: return ""
    s = text
    for ch in _INVISIBLE_CHARS:
        s = s.replace(ch, "")
    return s.replace(" ", " ")

def _normalize_outbound(text):
    """Invisible-char-stripped, whitespace-collapsed, lowercased text for OUTBOUND
    classification only. Collapsing newlines to a single space is safe here: every
    _ENGINE_PREFIXES / _AUTOMATION_PREFIXES entry and template head is one line, so a
    startswith() check is unaffected by folded line breaks."""
    s = _strip_invisible(text)
    return re.sub(r"\s+", " ", s).strip().lower()

# Tenant intake form field labels (English, INTAKE_FORM + OPEN_INTAKE_FORM) recognised when
# pasted back BLANK — a copy/paste re send of the form, not a filled profile.
_INTAKE_FIELD_LABELS = ("email address", "name", "nationality", "ethnicity", "gender", "age",
                        "pass type", "occupation", "employment type", "no. of pax", "no of pax",
                        "move in date", "lease term", "budget", "preferred location", "location")
# The Chinese variant Maddie pastes puts the Chinese label directly before the English one
# with no separator ("姓名Name:", "国籍 Nationality :") — same field, bilingual. Also the
# markers for CHINESE_INTAKE_FORM itself (11 Sep 2026): 邮箱 email, 年龄 age, 雇佣类型
# employment type, 租期 lease term — the rest were already covered by the 9 Sep hand paste
# hardening.
_CN_FIELD_MARKERS = ("姓名", "入住人数", "性别", "国籍", "种族", "职业", "工作准证类型",
                     "准证", "批准通过", "入住日期", "租赁期", "租期", "预算", "首选地点",
                     "邮箱", "年龄", "雇佣类型")

def _blank_form_lines(text):
    s = _strip_invisible(text or "").replace("：", ":")  # CJK full width colon -> ascii
    return [ln.strip(" \t-") for ln in re.split(r"[\n•]+", s) if ln.strip(" \t-")]

def is_pasted_blank_intake_form(text):
    """A pasted copy of the tenant intake form (English or the Chinese variant) with every
    bullet value left EMPTY -- e.g. Maddie re pasting the template by hand, with or without
    the 'Pls fill this in' header, sometimes with a custom note in front ('Possible ...',
    'Hi can help fill in so ...'). A single filled value anywhere (a real profile forwarded
    to a landlord) disqualifies it -- that is a human message, unchanged."""
    hits = 0
    for ln in _blank_form_lines(text):
        low = ln.lower()
        label_len = next((len(lab) for lab in _INTAKE_FIELD_LABELS if low.startswith(lab)), 0)
        if not label_len:
            marker = next((m for m in _CN_FIELD_MARKERS if ln.startswith(m)), None)
            if marker:
                m = re.match(r"^.{0,20}?:", ln)
                label_len = m.end() if m else len(ln)
        if not label_len:
            continue
        rest = ln[label_len:].strip()
        rest = re.sub(r"^\([^)]*\)", "", rest).strip()   # drop a "(SC/PR/EP...)" format hint
        rest = rest.lstrip(" :：-").strip()
        if rest:
            return False   # a real value anywhere -> filled profile forward, not a blank paste
        hits += 1
    return hits >= 5

def is_engine_outbound(text):
    """Strict classification for OUTBOUND rows: engine send iff it starts with an exact
    engine template prefix, a known sanctioned-automation prefix (PG auto-ack), a listing
    unit-message head, or is a pasted BLANK copy of the tenant intake form (engine
    equivalent -- see is_pasted_blank_intake_form). Everything else = Winfred by hand."""
    low = _normalize_outbound(text)
    if not low: return False
    if low.startswith(_ENGINE_PREFIXES): return True
    if low.startswith(_AUTOMATION_PREFIXES): return True
    if low.startswith("almost there. ") and "could you confirm this so i can send your profile" in low: return True
    if any(low.startswith(h) for h in _template_heads()): return True
    return is_pasted_blank_intake_form(text)

EXCLUDE_NAMES = ("wanni","shaw","madeleine","darren","amanda","don chuang")
def _contact_names(pn):
    """Returns (names_list, db_ok). db_ok is False if the contact DB read FAILED (e.g. lock),
    so the caller can fail-closed rather than treat a locked DB as 'no name = not excluded'."""
    out = []
    try:
        con = sqlite3.connect(_wa_db(), timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
    except Exception:
        return out, False
    try:
        r = con.execute("SELECT full_name,push_name,business_name FROM whatsmeow_contacts WHERE their_jid=?",
                        (pn+"@s.whatsapp.net",)).fetchone()
        if r: out += [x for x in r if x]
        lr = con.execute("SELECT lid FROM whatsmeow_lid_map WHERE pn=?", (pn,)).fetchone()
        if lr:
            r2 = con.execute("SELECT full_name,push_name FROM whatsmeow_contacts WHERE their_jid=?",
                             (lr[0]+"@lid",)).fetchone()
            if r2: out += [x for x in r2 if x]
        return out, True
    except Exception:
        return out, False        # DB error -> signal failure so excluded_reason fails closed
    finally:
        con.close()
@functools.lru_cache(maxsize=1)
def _landlord_pn_set():
    """Bare phone numbers (and lids) known to be landlords, from the authoritative landlord DB.
    A landlord must never be screened or sent the tenant form, even when the chat started
    outbound (Winfred messaging them) so manual_takeover latched before the name/agency gate
    was ever reached. Cached for the life of the short-lived runner process; the next run
    re-reads, so DB edits take effect within 120s.
    Returns None (NOT an empty set) when the DB is unreadable: an unreadable DB must fail
    CLOSED (defer sends) — an empty set would silently drop the landlord protection and
    form-blast landlords the moment the file is corrupted."""
    try:
        d = json.load(open(_landlord_db()))
    except Exception:
        return None
    out = set()
    for l in d.get("landlords", []):
        ph = re.sub(r"\D", "", str(l.get("phone") or ""))
        if ph: out.add(ph)
        cj = str(l.get("chat_jid") or "")
        if cj: out.add(cj.split("@")[0])
    return frozenset(out)

@functools.lru_cache(maxsize=1)
def _landlord_form_recipients():
    """pns + bare chat-jids of every chat WE have sent an owner intake form to (landlord
    onboarding or seller intake — both carry the '• Owner name:' field, which no tenant form
    has). Day-one protection: a landlord Winfred onboards by hand is shielded from the tenant
    flow the moment the form goes out, without waiting for the nightly DB refresh keyed on
    'Landlord' contact names (MI/Hannah/Wen sat unprotected for days, Aug 2026). Fails OPEN
    (empty set) like the cobroke gate — the landlord-DB gate stays the fail-closed one."""
    out = set()
    try:
        con = sqlite3.connect(_msg_db(), timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        jids = [j for (j,) in con.execute(
            "SELECT DISTINCT chat_jid FROM messages WHERE is_from_me=1 "
            "AND lower(content) LIKE '%owner name:%'")]
        con.close()
    except Exception:
        return frozenset()
    for j in jids:
        bare = str(j).split("@")[0]
        if bare: out.add(bare)
    if out:
        try:
            wcon = sqlite3.connect(_wa_db(), timeout=10)
            wcon.execute("PRAGMA busy_timeout=10000")
            for lid, pn in wcon.execute("SELECT lid, pn FROM whatsmeow_lid_map"):
                if str(lid).split("@")[0] in out:
                    p = re.sub(r"\D", "", str(pn))
                    if p: out.add(p)
            wcon.close()
        except Exception:
            pass                  # bare jids still protect when the event pn IS the jid user
    return frozenset(out)

COBROKE_DB = _P.paths()["cobroke_db"]
_default_COBROKE_DB = COBROKE_DB


def _cobroke_db():
    return _P.resolved(globals(), "COBROKE_DB", "cobroke_db")


@functools.lru_cache(maxsize=1)
def _cobroke_agent_pn_set():
    """Bare phone numbers of KNOWN agents from cobroke-agents.json (fed by /cobroke-dd and
    /cea-check). Cached per runner process; next tick re-reads (cache_clear() between
    sandbox scenarios -- see wa_intake_attack_harness.py). Fails OPEN (empty set) on an
    unreadable file: the agent gate is protective polish — a corrupt agents file must never
    block real tenants (the landlord gate stays the fail-closed one). Gap closed 26 Jul 2026;
    was name/keyword heuristics only."""
    try:
        d = json.load(open(_cobroke_db()))
    except Exception:
        return frozenset()
    out = set()
    for a in d.get("agents", []):
        j = re.sub(r"\D", "", str(a.get("jid") or "").split("@")[0])
        if j: out.add(j)
    return frozenset(out)

def excluded_reason(pn, text=""):
    """Never message a landlord, a co broke agent, or a colleague/personal contact.
    Returns a reason string, or None if clearly NOT excluded. On a DB read failure it returns
    'db_error' (fail-closed) so a transiently locked contact DB can never silently let a
    landlord/agent through the gate and receive the tenant form."""
    lset = _landlord_pn_set()
    if lset is None:                      # landlord DB unreadable -> cannot verify -> fail closed
        return "db_error"
    if pn and pn in lset:                 # authoritative landlord DB: catches outbound-first chats too
        return "landlord"
    if pn and pn in _landlord_form_recipients():
        return "landlord"                 # we sent them an owner intake form: supply side, day one
    names, db_ok = _contact_names(pn)
    nm = " ".join(names).lower()
    if "landlord" in nm: return "landlord"
    if pn and pn in _cobroke_agent_pn_set(): return "agent"    # phone gate, not just keywords
    if any(e in nm for e in EXCLUDE_NAMES): return "colleague"
    t = (text or "").lower()
    # unambiguous agent markers keep priority: an agency name or explicit co-broke language
    # is an agent no matter what else the message says
    if any(a in t or a in nm for a in ("propnex","huttons","orangetee","i take my own com",
                                       "co-broke","co broke","cobroke")):
        return "agent"
    # owner-supply or fee-negotiation signals BEAT the residual commission keywords: a landlord
    # asking to lower OUR fee ("But can your commission lower ?") is not an agent. Return None so
    # the supply-side branch classifies them and sends the LANDLORD form. (GoldMind Circle sat
    # excluded:agent for 5 days off exactly this, 6-11 Aug 2026.)
    if (db_ok and (any(m in t for m in _LANDLORD_FEE_NEG) or any(m in t for m in _LANDLORD_SUPPLY)
            or any(m in t for m in _SELLER_SUPPLY))):
        return None
    if any(a in t or a in nm for a in ("your commission","my commission","i charge")):
        return "agent"
    if not db_ok:
        return "db_error"        # could not verify the contact -> defer to a human, do not auto-send
    return None

# ---------- qualify (corrected, honours every mode) ----------
_CORRECTION_RE = re.compile(
    r"\b(sorry|oops|typo|actually|correction|i mean|meant|my bad|mistake|misspoke|"
    r"scratch that|ignore that|no wait|forget what i said)\b", re.I)

# maps a qualify() DISQUALIFIED reason string to the profile field it turns on, so a
# disqualify can be traced back to whichever field actually decided it.
_DISQ_FIELD_PATTERNS = (
    (re.compile(r"\bpax\b"), "no_of_pax"),
    (re.compile(r"female tenant only|male tenant only|\bgender\b"), "gender"),
    (re.compile(r"ethnicity|accepts only"), "ethnicity"),
    (re.compile(r"\bbudget\b"), "budget"),
    (re.compile(r"\boccupation\b"), "occupation"),
    (re.compile(r"\bnationality\b"), "nationality"),
)

def _disqualify_unsourced(rec, why):
    """True if a DISQUALIFIED verdict rests on a field whose only source is incidental free
    text -- never a form and never an explicit correction. Redirecting a prospect away on a
    value grab() merely guessed out of ordinary prose is not safe; Winfred should verify by
    hand instead (P0 fix, 9 Sep 2026 cycle4 c4ec01 replay)."""
    prov = rec.get("profile_provenance") or {}
    for reason in why or []:
        low = (reason or "").lower()
        for pat, field in _DISQ_FIELD_PATTERNS:
            if pat.search(low):
                if prov.get(field) not in ("form", "correction"):
                    return True
                break
    return False

def qualify(req, profile):
    r = req.get("requirements", req)
    if r.get("open_intake"):
        # owner accepts ALL profiles. Keep ONLY this listing's ethnicity/nationality gate
        # (baby is screened by policy_excluded). No gender/pax/lease/age/occupation/budget gate.
        er = r.get("ethnicity_rule", {}) or {}
        emode, elst = er.get("mode", "any"), [x.lower() for x in (er.get("list") or [])]
        eth = (profile.get("ethnicity") or "").lower()
        if eth and emode == "exclude" and elst and any(x in eth for x in elst):
            return "DISQUALIFIED", ["ethnicity not accepted by landlord"]
        if eth and emode == "only" and elst and not any(x in eth for x in elst):
            return "DISQUALIFIED", ["landlord accepts only " + ", ".join(er.get("list"))]
        np = r.get("nationality_pref", {}) or {}
        nmode, nlst = np.get("mode", "any"), [x.lower() for x in (np.get("list") or [])]
        nat = (profile.get("nationality") or "").lower()
        if nmode == "exclude" and nlst:
            if nat and any(x in nat for x in nlst): return "DISQUALIFIED", ["nationality not accepted by landlord"]
            if not nat: return "NEEDS_INFO", ["nationality"]
        elif nmode == "only" and nlst:
            if nat and any(x in nat for x in nlst): pass
            elif nat: return "DISQUALIFIED", ["landlord accepts only " + ", ".join(np.get("list"))]
            else: return "NEEDS_INFO", ["nationality"]
        # a budget_floor still applies even for open intake: it is the ROOM's price floor
        # (e.g. a $2,300 master), not a landlord preference — a $1,300 budget must never be
        # walked into a $2,300 viewing. Unknown budget stays low-friction (price is in the
        # unit message, prospects self-select).
        floor = r.get("budget_floor")
        bud = _to_int(profile.get("budget"))
        if floor and bud is not None:
            if bud >= floor: pass
            elif bud >= floor * 0.9:
                return "NEEDS_INFO", ["budget " + str(bud) + " just under " + str(floor)]
            else:
                return "DISQUALIFIED", ["budget below " + str(floor)]
        # global 1 year lease floor applies to open intake too (Winfred, 11 Jul 2026)
        lmin = max(12, r.get("lease_min_months") or 0)
        lt = profile.get("lease_term_months")
        if isinstance(lt, int) and lt < lmin:
            return "SHORT_LEASE", ["minimum lease " + str(lmin) + " months"]
        return "QUALIFIED", []
    fails, unknown = [], []
    pax = profile.get("no_of_pax")
    tg = (profile.get("gender") or "").lower()
    # whole-word gender tokens so "female" is NOT matched by the substring "male".
    has_female = bool(re.search(r"\bfemale\b", tg)) or tg.strip() == "f"
    has_male   = bool(re.search(r"\bmale\b", tg))   or tg.strip() == "m"
    is_female = has_female and not has_male
    is_male   = has_male and not has_female
    # a couple is an opposite-sex pair, or an explicit couple statement — NOT merely 2 pax
    # (two people of the same sex must still satisfy a gendered single gate).
    says_married = ("married" in tg) or ("husband" in tg and "wife" in tg)
    says_couple = ("couple" in tg) or says_married
    is_couple = says_couple or (has_male and has_female)
    gender_unknown = not has_female and not has_male and not says_couple

    g = r.get("gender","any")
    if g == "female_only":
        if is_couple and r.get("couple_ok"):
            # only ask to confirm marriage if they have NOT already said they are married
            if r.get("couple_must_be_married") and not says_married:
                unknown.append("confirm legally married couple")
        elif is_couple: fails.append("female tenant only (no couples)")  # couple but couple_ok is false
        elif is_male: fails.append("female tenant only")
        elif gender_unknown: unknown.append("gender")
    elif g == "male_only":
        if is_couple and r.get("couple_ok"): pass
        elif is_couple: fails.append("male tenant only (no couples)")    # couple but couple_ok is false
        elif is_female: fails.append("male tenant only")
        elif gender_unknown: unknown.append("gender")
    # *_pref and any: no hard gate

    er = r.get("ethnicity_rule",{}) or {}
    mode, lst = er.get("mode","any"), [x.lower() for x in (er.get("list") or [])]
    eth = (profile.get("ethnicity") or "").lower()
    if mode == "exclude" and lst:
        if eth and any(x in eth for x in lst): fails.append("ethnicity not accepted by landlord")
        elif not eth: unknown.append("ethnicity")
    elif mode == "only" and lst:
        if eth and any(x in eth for x in lst): pass
        elif eth: fails.append("landlord accepts only " + ", ".join(er.get("list")))
        else: unknown.append("ethnicity")

    mx = r.get("max_pax")
    if isinstance(mx,int) and isinstance(pax,int):
        # a couple_ok listing tolerates 2 pax, but does NOT become unlimited — cap, don't bypass
        effective_max = max(mx, 2) if (is_couple and r.get("couple_ok")) else mx
        if pax > effective_max:
            fails.append("max " + str(effective_max) + " pax")

    # GLOBAL 1 year lease floor (Winfred, 11 Jul 2026): every landlord wants 12 months
    # minimum; a listing may only raise it. A short lease is NOT a hard disqualify — the
    # prospect gets a kind note and a chance to accept the minimum (SHORT_LEASE verdict).
    lmin = max(12, r.get("lease_min_months") or 0); lt = profile.get("lease_term_months")
    short_lease = isinstance(lt, int) and lt < lmin
    lmax = r.get("lease_max_months")
    if isinstance(lmax,int) and isinstance(lt,int) and lt > lmax:
        unknown.append("lease over landlord max")

    # age gate removed (Winfred, 8 Sep 2026): age never disqualifies a tenant

    oc = r.get("occupation_rule",{}) or {}
    if oc.get("mode") == "exclude" and oc.get("list"):
        occ = (profile.get("occupation") or "").lower()
        if occ and any(x.lower() in occ for x in oc["list"]): fails.append("occupation not accepted")

    floor = r.get("budget_floor"); bud = profile.get("budget")
    if r.get("budget_unknown") or floor is None:
        unknown.append("listing rent not confirmed")
    elif isinstance(bud,int):
        if bud >= floor: pass
        elif bud >= floor*0.9: unknown.append("budget " + str(bud) + " just under " + str(floor))
        else: fails.append("budget below " + str(floor))
    else:
        unknown.append("budget")

    if fails: return "DISQUALIFIED", fails
    if short_lease: return "SHORT_LEASE", ["minimum lease " + str(lmin) + " months"]
    if unknown: return "NEEDS_INFO", unknown
    return "QUALIFIED", []

def _split_needs_info(why):
    """Split a NEEDS_INFO reason list into (askable, sensitive, listing_only):
      askable   - phrases the PROSPECT can answer ("your gender", "your budget", ...)
      sensitive - True if a gap is ethnicity/nationality (never isolated in a one-line ask;
                  the form already collects it alongside everything else)
      listing_only - True when every reason is a listing-side unknown ("listing rent not
                  confirmed", "lease over landlord max", ...) that the prospect has no way
                  to answer -- that copy must never reach prospect-facing text (judge catch,
                  an internal gap sent verbatim to a tenant kills the conversation)."""
    askable, sensitive = [], False
    for w in why:
        wl = str(w).lower()
        if wl.startswith("gender"): askable.append("your gender")
        elif wl.startswith("budget"): askable.append("your budget")
        elif "married" in wl: askable.append("whether you are a legally married couple")
        elif wl.startswith(("ethnicity", "nationality")): sensitive = True
        # anything else (listing rent not confirmed, lease over landlord max, ...) is a
        # listing-side unknown -- dropped here, never surfaced to the prospect
    listing_only = not askable and not sensitive
    return askable, sensitive, listing_only

def _ask_one_text(why, askable, sensitive, just_flagged_topic=None):
    """Phrase the ASK_ONE / book intent question. NEVER quotes a figure back at the tenant
    (review fix, 9 Sep 2026 final attack pass): a borderline budget gap ("budget 680 just
    under 700") used to reference the figure ALREADY given and ask if it was firm ("is your
    $680 budget firm, or could you stretch to $700?") -- that is a live negotiation Claude
    is not licensed to run (CEA role boundary), so it now falls through to the SAME
    figure-free ask as every other askable gap ("Can I just check your budget?"); a genuinely
    borderline case still reaches Winfred untouched via the caller's own FLAG_HUMAN path,
    it just never gets a quoted-figure tenant message from here.
    just_flagged_topic: the field (if any) the IMMEDIATELY preceding flagged inbound was
    about -- softens the ask so it never reads as ignoring what they just said (cosmetic
    only, the underlying flag to Winfred is unchanged)."""
    if sensitive:
        # never isolate ethnicity/nationality in a one-line ask — the form already collects
        # them alongside everything else
        return "Just need your profile above and I can confirm your slot \U0001F64F\U0001F3FB"
    lead = "Can I just check "
    if just_flagged_topic and any(just_flagged_topic in a for a in askable):
        lead = "Sorry, just to double check, "
    return lead + " and ".join(askable) + "? Then I can confirm your slot \U0001F642"

# ---------- state ----------
class StateCorrupt(RuntimeError):
    """intake-state.json exists but cannot be parsed. NEVER degrade this to an empty
    state: every latch (form_sent, manual_takeover, viewing dedup) would vanish and the
    next tick would re-form every past prospect and talk over Winfred's manual chats."""

def load_state():
    state = _state()
    if not os.path.exists(state):
        return {"version": 1, "conversations": {}}   # first install only
    try:
        s = json.load(open(state))
    except Exception as e:
        raise StateCorrupt(f"{state}: {type(e).__name__}: {e}")
    if not isinstance(s.get("conversations"), dict):
        raise StateCorrupt(f"{state}: parsed but 'conversations' is not a dict")
    return s
def save_state(s):
    state_path = _state()
    tmp = state_path + ".tmp"
    json.dump(s, open(tmp,"w"), indent=1, ensure_ascii=False)
    os.replace(tmp, state_path)   # atomic; one writer

def _rec(state, pn):
    rec = state["conversations"].setdefault(pn, {})
    for k, v in {
        "pn":pn, "listing_key":None, "stage":"NEW", "profile":{},
        "processed_ids":[], "form_sent":False, "asked_fields":[],
        "viewing_asked":False, "viewing_confirmed":False, "asked_tenant_time":False,
        "manual_takeover":False, "status":"new", "last_inbound":None,
        "last_inbound_ts":None,   # takeover resume /send cold guard (Winfred, 9 Sep 2026)
        "source":None, "fact_answered":False,
        # B established (review fix): listing_key provenance + first-touch direction, feeding
        # wa_intake_resume.is_established_prospect().
        "listing_key_source":None, "first_inbound_text":None, "lang":None,
        "outbound_before_first_inbound":False, "_any_outbound_seen":False,
        # landlord onboarding extension (never touched by the tenant/buyer flows)
        "supply_kind":None, "supply_profile":{}, "human_takeover":False,
        "info_complete":False, "photos_received":False, "video_received":False,
        "media_requested_at":None, "media_chase_sent":False,
        "followup_nudges_sent":0, "info_cap_flagged":False, "media_cap_flagged":False}.items():
        rec.setdefault(k, v)   # repair partial/legacy records, not just create new ones
    return rec

SERVE_EXCLUDE_NAT = ("indian", "india", "indian (india)")
_KIDRE = re.compile(r"\b(baby|babies|infant|toddler|newborn|child|children|kid|kids)\b", re.I)
def policy_excluded(profile, text="", open_intake=False):
    """Winfred's service policy: profiles his landlords will never take a room with, so do
    not serve or match them. Returns a short internal reason if excluded, else None. The
    reason is NEVER shown to the prospect (the redirect is the kind, neutral channel note).
      - nationality India          (SKIPPED for open_intake listings; gated per listing instead)
      - 3 pax or more on a budget under 1400 (cannot fit a single room; SKIPPED for open_intake)
      - 2 pax or more that includes a child or baby   (ALWAYS enforced, incl. open_intake)
    """
    nat = (profile.get("nationality") or "").strip().lower()
    if not open_intake and nat in SERVE_EXCLUDE_NAT: return "nationality"
    pax = _to_int(profile.get("no_of_pax"))
    bud = _to_int(profile.get("budget"))
    if not open_intake and pax and pax >= 3 and bud is not None and bud < 1400: return "pax_budget"
    blob = " ".join([text or "", profile.get("gender") or "", profile.get("occupation") or "",
                     str(profile.get("no_of_pax") or "")])
    if _KIDRE.search(blob) and pax and pax >= 2: return "family"
    return None

# ---------- A3: protected attribute declines are visible + neutral in state ----------
# Every decline that turns on ethnicity, nationality or gender (Winfred's own policy_excluded
# nationality rule, or a landlord's listing side qualify() gate) must (a) ping Winfred
# (notify=True -- CEA visibility) and (b) never leak the attribute word into rec["status"]
# or the Telegram flag text -- both carry a house_gate:<code> only. Prospect-facing redirect
# copy is unaffected (it was already neutral).
_HOUSE_GATE_CODE = {"gender": "G1", "ethnicity": "E1", "nationality": "N1"}

def _house_gate_status(attr):
    return "house_gate:" + _HOUSE_GATE_CODE.get(attr, "U1")

def _protected_attr_from_why(why, listing=None):
    """Which protected attribute (if any) a qualify() DISQUALIFIED reason list names."""
    r = ((listing or {}).get("requirements", listing) or {}) if listing else {}
    for w in (why or []):
        wl = str(w).lower()
        if "ethnicity" in wl: return "ethnicity"
        if "nationality" in wl: return "nationality"
        if "tenant only" in wl or "no couples)" in wl: return "gender"
        # an "only" mode gate names the accepted GROUP instead of the attribute ("landlord
        # accepts only Chinese") -- the attribute word never appears, so the old substring
        # test missed it and the decline escaped the house_gate path entirely: plain
        # "disqualified" status, no notify, and the group name itself in the Telegram flag.
        # Resolve the attribute from the listing's own rules (9 Sep 2026 review).
        if wl.startswith("landlord accepts only"):
            if (r.get("ethnicity_rule") or {}).get("mode") == "only": return "ethnicity"
            if (r.get("nationality_pref") or {}).get("mode") == "only": return "nationality"
            return "protected"     # attribute undeterminable -> generic house_gate:U1
    return None

# ---------- A2: a protected attribute gate needs landlord provenance ----------
def _gate_unverified_attrs(listing):
    r = (listing or {}).get("requirements", listing) or {}
    return set(r.get("gate_unverified") or [])

def _house_gate_redirect(pn, rec, listing, reqs, lk, attr, why_or_pol):
    """Shared A2 + A3 handling for a decline that names a protected attribute (ethnicity,
    nationality, gender). A3: always REDIRECT with notify=True, rec['status'] and the
    Telegram reason carry only a house_gate:<code>, never the attribute word. A2: when
    the listing's gate for THIS attribute lacks landlord provenance (gate_unverified),
    the decline is blocked entirely -- FLAG_HUMAN instead, no prospect text at all."""
    code = _house_gate_status(attr)
    # the raw qualify() reason ("ethnicity not accepted by landlord", "landlord accepts only
    # Chinese") is also persisted in rec["qualify"] by the callers above -- intake-state.json
    # is the durable record of WHY a tenant was declined, so it carries the code too.
    if rec.get("qualify"):
        rec["qualify"] = {"verdict": "DISQUALIFIED", "why": [code]}
    if attr in _gate_unverified_attrs(listing):
        rec["status"] = code
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": code, "profile_summary": _profile_summary(rec.get("profile", {}))}
    rec["terminal"] = True; rec["stage"] = "DISQUALIFIED"; rec["status"] = code
    return {"type": "REDIRECT", "pn": pn, "notify": True, "reason": code,
            "text": _redirect_text(why_or_pol, rec.get("profile", {}), reqs, lk)}

def _gate_unverified_offer_block(pn, rec, listing):
    """A2 for the QUALIFIED side: a listing carrying ANY unverified protected attribute gate
    must never auto OFFER_VIEWING off an 'any' gate that might not reflect the landlord's
    real preference -- Winfred confirms by hand instead. Returns a FLAG_HUMAN action, or
    None if the listing has no unverified gate (normal OFFER_VIEWING proceeds)."""
    gu = _gate_unverified_attrs(listing)
    if not gu:
        return None
    attr = sorted(gu)[0]
    code = _house_gate_status(attr)
    rec["status"] = code
    return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
            "reason": code, "profile_summary": _profile_summary(rec.get("profile", {}))}

def _text_mentions_listing(text, lk, reqs=None):
    """True if TEXT names the listing LK by any of its pg_url_keywords. Used to bind an
    unbound-but-complete profile only when the tenant's own words (or Winfred's / the
    automation ack's) actually named the one listing they qualify for -- never a silent
    guess off qualify() alone."""
    if not text or not lk:
        return False
    t = text.lower()
    l = (reqs or listing_reqs()).get(lk) or {}
    return any(kw and kw.lower() in t for kw in (l.get("pg_url_keywords") or []))

def _profile_summary(profile):
    """One line, human readable, for a Telegram flag -- never the raw dict."""
    parts = []
    for f in ("name", "nationality", "gender", "age", "no_of_pax", "budget", "move_in_date"):
        v = (profile or {}).get(f)
        if v not in (None, ""):
            parts.append(f + "=" + str(v))
    return ", ".join(parts) if parts else "profile incomplete"

# ---------- manual-takeover co-pilot ----------
def hot_matches(profile, exclude_key=None, limit=3):
    """Cross-listing screen for a COMPLETE profile: which OTHER live listings does
    this tenant qualify() for right now? Speed is Winfred's own conversion lever
    (a specific offer within minutes books 96.6% vs 30.7% for a slow open ask),
    and until now a new tenant was only ever screened against the one listing
    they enquired about — the other 18 rooms waited hours for the next batch
    refresh. Pure read, alert-feeding only: it never sends anything, and any
    failure returns [] rather than touching the intake flow."""
    out = []
    try:
        for lk, req in (listing_reqs() or {}).items():
            if lk == exclude_key:
                continue
            if _listing_unavailable(lk):
                continue
            v, _why = qualify(req, profile)
            if v == "QUALIFIED":
                out.append(lk)
                if len(out) >= limit:
                    break
    except Exception:
        return []
    return out


def _copilot_verdict(rec):
    """When Winfred is handling a chat by hand (manual_takeover), the engine stays silent to the
    prospect but still screens a COMPLETE, listing-bound profile and returns a COPILOT_VERDICT so
    Winfred gets the qualify() result on Telegram (never a prospect send). Fires once per distinct
    verdict (copilot_sig latch); never for a known landlord/agent/colleague."""
    if rec.get("terminal"):
        return None
    # A2 belt-and-suspenders: a record already excluded (self disclosed agent/landlord/
    # colleague) must never reach a prospect facing action here, whatever muted the chat.
    # Verdict-only notifications to Winfred still fire further down; only OFFER_VIEWING is
    # blocked (P0 fix, 9 Sep 2026 cycle5 c5ec07).
    if str(rec.get("status") or "").startswith("excluded:"):
        lk = rec.get("listing_key")
        listing = listing_reqs().get(lk) if lk else None
        if lk and listing and not missing_required(rec.get("profile", {}), listing):
            verdict, why = qualify(listing, rec["profile"])
            sig = "EXCLUDED|" + verdict + "|" + ",".join(why)
            if rec.get("copilot_sig") == sig:
                return None
            rec["copilot_sig"] = sig
            rec["qualify"] = {"verdict": verdict, "why": why}
            return {"type": "COPILOT_VERDICT", "pn": rec.get("pn"), "notify": True, "text": None,
                    "verdict": verdict, "why": why, "listing_key": lk,
                    "hot_matches": hot_matches(rec["profile"], exclude_key=lk)}
        return None
    lk = rec.get("listing_key")
    if not lk:
        # unbound but Winfred is handling this chat by hand: once the profile is complete
        # against the generic (no listing-specific) requirement set, tell him what other
        # open listings this person might fit, instead of staying silent forever.
        if missing_required(rec.get("profile", {})):
            return None
        if excluded_reason(rec.get("pn")) in ("landlord", "agent", "colleague", "db_error"):
            return None
        matches = hot_matches(rec.get("profile", {}), exclude_key=None)
        sig = "UNBOUND|" + ",".join(sorted(matches))
        if rec.get("copilot_sig") == sig:
            return None
        rec["copilot_sig"] = sig
        return {"type": "COPILOT_VERDICT", "pn": rec.get("pn"), "notify": True, "text": None,
                "verdict": "UNBOUND", "why": ["listing not bound"], "listing_key": None,
                "hot_matches": matches, "profile_summary": _profile_summary(rec.get("profile", {}))}
    listing = listing_reqs().get(lk)
    if not listing:
        return None
    if missing_required(rec.get("profile", {}), listing):
        return None                       # cheap gate first: wait for the (minimal) profile
    if excluded_reason(rec.get("pn")) in ("landlord", "agent", "colleague", "db_error"):
        return None                       # never co-pilot a landlord/agent (or on a locked contact DB)
    verdict, why = qualify(listing, rec["profile"])
    # A3: the co-pilot DISQUALIFIED ping is a decline notification like any other -- it must
    # carry the house_gate code, never the attribute word or the accepted group name. The
    # runner renders `why` verbatim into the Telegram line ("Reason: ...") and it is also
    # persisted in rec["qualify"], so neutralise it here, at the single source (9 Sep 2026).
    if verdict == "DISQUALIFIED":
        _pattr = _protected_attr_from_why(why, listing)
        if _pattr:
            why = [_house_gate_status(_pattr)]
    sig = verdict + "|" + ",".join(why)
    if rec.get("copilot_sig") == sig:
        return None                       # already surfaced this exact verdict to Winfred
    rec["copilot_sig"] = sig
    rec["qualify"] = {"verdict": verdict, "why": why}
    # Under manual takeover, a QUALIFIED prospect with an open slot gets the viewing offered
    # AUTOMATICALLY (the source-aware guard now lets the engine's own follow-up through). Winfred
    # is still pinged so he can step in. Fires once (viewing_asked latch).
    # NEVER once copilot_muted (Winfred replied by hand: the chat is his, verdicts only) and
    # never on a listing that closed since Stage 1.
    if (verdict == "QUALIFIED" and not rec.get("viewing_asked")
            and not rec.get("copilot_muted") and not _listing_unavailable(lk)):
        _gu_block = _gate_unverified_offer_block(rec.get("pn"), rec, listing)
        if _gu_block:
            return _gu_block
        slot = next_slot(lk)
        if slot:
            rec["viewing_asked"] = True
            rec["stage"] = "VIEWING_OFFERED"; rec["status"] = "viewing_offered"
            rec["offered_slot_id"] = slot.get("slot_id")
            rec["offered_slot_label"] = slot.get("label")
            return {"type": "OFFER_VIEWING", "pn": rec.get("pn"), "slot": slot,
                    "slot_id": rec["offered_slot_id"], "text": _viewing_text(slot, _lang(rec)),
                    "notify": True, "copilot": True, "listing_key": lk, "verdict": verdict,
                    "hot_matches": hot_matches(rec["profile"], exclude_key=lk)}
    # NEEDS_INFO / DISQUALIFIED, or QUALIFIED with no open slot -> notify Winfred only (he handles).
    return {"type": "COPILOT_VERDICT", "pn": rec.get("pn"), "notify": True, "text": None,
            "verdict": verdict, "why": why, "listing_key": lk,
            "hot_matches": hot_matches(rec["profile"], exclude_key=lk)}

@functools.lru_cache(maxsize=1)
def _landlord_by_id():
    """Master landlord DB records keyed by id (LL001…). Cached for the life of the
    short-lived runner process; the next 60s tick re-reads, so DB edits apply fast.
    Returns None when the DB is unreadable so callers can fail CLOSED (no suggestions)
    instead of treating corruption as 'no landlords'."""
    try:
        d = json.load(open(_landlord_db()))
    except Exception:
        return None
    return {str(l.get("id")): l for l in d.get("landlords", []) if l.get("id")}

def _master_status(lk, reqs=None):
    """The MASTER database sheet's landlord status for a listing (lowercase), or None when
    the listing is not linked to a landlord record. The master DB is the authoritative
    source (nightly refresh + Google Sheet); the listing index can drift between syncs."""
    l = (reqs or listing_reqs()).get(lk) or {}
    lid = l.get("landlord_id")
    if not lid:
        return None
    rec = (_landlord_by_id() or {}).get(str(lid))
    return str(rec.get("status", "")).lower() if rec else None

def _listing_unavailable(lk, reqs=None):
    """Status string ('closed…'/'hold') if the bound listing is no longer open, else None.
    Re-checked immediately before ANY viewing offer or confirmation: a listing can close
    MID-conversation (tenanted by someone else) after the Stage-1 gate already passed —
    the Stage-1 check alone let a viewing be confirmed on a room tenanted the day before.
    Cross-checks the MASTER landlord DB too (Winfred, 13 Jul 2026): if the master says the
    landlord is closed/archived, the room is gone even when the index still says active."""
    if not lk: return None
    l = (reqs or listing_reqs()).get(lk) or {}
    st = str(l.get("status", "")).lower()
    if st.startswith("closed") or st == "hold":
        return st
    ms = _master_status(lk, reqs)
    if ms and (ms.startswith("closed") or ms.startswith("archived")):
        return "closed (master db: " + ms + ")"
    return None

def _room_gone_action(rec, pn, st):
    """The room closed mid-flow. Cross sell once (same rebind as a unit rejection), else
    point at the channel and close. Never offer or confirm a dead room."""
    rec["viewing_asked"] = False; rec["viewing_confirmed"] = False
    rec["book_intent_asked"] = False    # intent never carries across listings
    rec["offered_slot_id"] = None; rec["offered_slot_label"] = None
    rec["exact_time_locked"] = False
    if not rec.get("alt_suggested"):
        rec["alt_suggested"] = True
        alt = suggest_alternative(rec.get("profile") or {}, rec.get("listing_key"))
        if alt:
            k2, alt_text = alt
            rec["listing_key"] = k2
            rec["listing_key_source"] = "hotmatch"   # engine cross sell, never tenant named
            rec["sent_count"] = 0; rec["cap_flagged"] = False   # fresh qualification attempt
            rec["stage"] = "ALT_SUGGESTED"; rec["status"] = "alt_suggested:" + k2
            return {"type": "SUGGEST_ALT", "pn": pn, "notify": True, "listing_key": k2,
                    "text": "So sorry, that room was just taken 🙏 I have another room nearby that may suit you:\n\n"
                            + alt_text + "\n\nKeen to take a look? I can arrange a viewing for you."}
    rec["terminal"] = True; rec["stage"] = "CLOSED_MID_FLOW"
    rec["status"] = "closed (listing " + (st.split()[0] if st else "closed") + " mid flow)"
    return {"type": "REDIRECT", "pn": pn, "notify": True, "reason": "listing closed mid flow",
            "text": "So sorry, that room was just taken 🙏 You can see my other available rooms here:\n"
                    + CHANNEL + "\nLet me know if anything catches your eye and I will arrange a viewing."}

_YES_WORD = re.compile(r"\b(yes|yeah|yup|yep)\b|\btake (it|the (room|slot))\b")
_CONFIRM_WORD_RE = re.compile(r"\bconfirm(?:ed)?\b")
_INTERROGATIVE_OPEN_RE = re.compile(r"^\s*(?:can|could|please|is|are|do)\b", re.I)
# a bare "confirm" only reads as consent when it IS the message's own main clause -- a short
# reply, not one clause folded inside a longer request/instruction sentence ("please confirm
# my profile was received and share the landlord's whatsapp"). Any embedded request verb, or
# a "?" anywhere (not just trailing), disqualifies it; length is the last guard against a
# long multi-clause message that just happens to carry the word "confirm".
_CONFIRM_REQUEST_RE = re.compile(
    r"\bplease\b|\bkindly\b|\bshare\b|\bsend\b|\bprovide\b|\bcan you\b|\bcould you\b|"
    r"\bcoordination system\b|\bas the\b", re.I)
_CONFIRM_MAX_WORDS = 8
def _is_affirmative(t):
    """A reply counts as viewing consent only when it IS the consent: an explicit yes/confirm
    anywhere, or a short standalone ok/sure. 'Ok thank you!' and 'ok noted' are polite
    acknowledgements, not bookings (a real prospect said exactly that and was auto confirmed)."""
    raw = (t or "").strip().lower()
    if _YES_WORD.search(raw):
        return True
    if _CONFIRM_WORD_RE.search(raw):
        # a bare "confirm" inside a clearly interrogative message ("please hold ya, confirm
        # can hold?") is asking WHETHER Winfred can confirm, never consent on its own -- only
        # counts alongside a standalone yes word, already handled above ("yes i confirm").
        if "?" in raw or _INTERROGATIVE_OPEN_RE.search(raw):
            pass
        elif _CONFIRM_REQUEST_RE.search(raw):
            pass
        elif len(raw.split()) > _CONFIRM_MAX_WORDS:
            pass
        else:
            return True
    core = " ".join(re.sub(r"[^a-z]+", " ", raw).split())
    if any(w in core.split() for w in ("thank", "thanks", "noted")):
        return False
    return bool(core) and len(core) <= 20 and re.fullmatch(
        r"(ok(?:ay|ie|ok)?|sure|deal|can)(\s+(please|pls|can|sure|deal))?", core) is not None

# ---------- short lease auto reply (Winfred, 8 Sep 2026) ----------
# One wording everywhere a short lease note goes out, whether the trigger is this free text
# scan (fires the moment the prospect ASKS for 6 months or less, bound or not) or the older
# qualify() verdict (fires once the full profile is in and lease_term_months is a filled in
# number below the floor). Both share the SAME lease_note_sent latch so only one note ever
# goes to a given prospect.
_LEASE_NOTE_TEXT = "Just to share, the landlord prefers a minimum 1 year lease \U0001F64F Would that work for you?"
_LEASE_NOTE_TEXT_ZH = "跟您分享一下，房东希望租期至少一年 \U0001F64F 请问这样可以吗？"
def _lease_note_text(lang="en"):
    return _LEASE_NOTE_TEXT_ZH if lang == "zh" else _LEASE_NOTE_TEXT

# a range or an "at least"/"minimum" phrasing states (or allows) a longer upper bound -- never
# a firm ask for 6 months or less, even when a small number sits right next to the unit word
# ("6 to 12 months", "at least 6 months").
_LEASE_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-|to|~|through|thru)\s*(\d{1,2})\s*(months?|mths?|mos?|years?|yrs?)\b", re.I)
_LEASE_ATLEAST_RE = re.compile(
    r"\b(?:at\s*least|min(?:imum)?)\s*\d{1,2}\s*(?:months?|mths?|mos?|years?|yrs?)\b", re.I)
# an explicit 1 year (or 12 months) mention always wins, even if a shorter number rode along
# earlier in the same message ("can't do 6 months, but 1 year works")
_LEASE_YEAR_TOKEN_RE = re.compile(
    r"\b(?:1\s*(?:year|yr)|one\s*year|12\s*(?:months?|mths?|mos?))\b|1\s*\u5e74|\u4e00\u5e74|12\s*\u4e2a\u6708", re.I)
# a month count that is NOT a lease ask: "6 months ago" (a past date), "6 month deposit" /
# "1 month notice" / "2 months advance" (money terms, every tenancy has them). Opus review,
# 9 Sep 2026 -- all four fired the note wrongly in the regex table.
_LEASE_EXPLICIT_MONTHS_RE = re.compile(
    r"\b([1-6])\s*[- ]?\s*(?:months?|mths?|mos?)\b"
    r"(?!\s*(?:ago|back|deposit|dep\b|notice|advance|advanced|in\s+advance))", re.I)
# the deposit/cap/notice wording can also sit BEFORE the month count ("is a 2 month deposit
# legal", "my friend said HDB caps it at 1 month") -- the lookahead above only reaches
# forward, so a trailing "at 1 month" slips through undetected. Python re has no variable
# width lookbehind, so this is a separate forward scan (keyword ... number) exactly like
# _LEASE_PAST_RE below, checked before the count is trusted as a real lease-length ask.
_LEASE_MONEYTERM_CONTEXT_RE = re.compile(
    r"\b(?:deposit|cap(?:s|ped)?|notice)\b[^.!?\n]{0,30}?\b[1-6]\s*[- ]?\s*"
    r"(?:months?|mths?|mos?)\b", re.I)
# an explicit legal-advice marker anywhere in the message means this is a question ABOUT the
# law, never a request for a short lease -- even when a stray month count also rides along
# ("is a 2 month deposit even legal, my friend said HDB caps it at 1 month"). Always veto so
# the message falls through to the normal fact-veto / FLAG_HUMAN path instead of being
# auto-answered as a lease-length FAQ (P1 fix, 9 Sep 2026 cycle 3 attack replay).
_LEASE_LEGAL_MARKER_RE = re.compile(
    r"\bis\s+it\s+legal\b|\b(?:il)?legal(?:ly)?\b|\bhdb\s+caps?\b|\bnotice\s+period\b|"
    r"\bevict(?:ed|ion)?\b|\bbreach\b|\bsmall\s+claims\b|\btenant'?s?\s+rights?\b|"
    r"\bkick\s+(?:us|me|him|her|them)?\s*out\b", re.I)
# past tense narration ("stayed 6 months at my last place", "I rented 3 months before") is a
# history statement, never a request for a short lease.
_LEASE_PAST_RE = re.compile(
    r"\b(?:stayed|staying\s+at\s+my\s+last|lived|rented|was|were|been|previously|"
    r"last\s+place|previous\s+place)\b[^.!?\n]{0,40}?\b[1-6]\s*(?:months?|mths?|mos?|weeks?)\b", re.I)
_LEASE_HALFYEAR_RE = re.compile(r"\bhalf\s*(?:an?\s*)?year\b", re.I)
_LEASE_KEYWORD_RE = re.compile(r"\bshort\s*(?:term|lease)\b|\bfew\s*months?\b|\btemporary\b|\u77ed\u79df", re.I)
# Chinese: "\u79df6\u4e2a\u6708" / "6\u4e2a\u6708" -- the same ask, typed the way half the pool types it.
_LEASE_CJK_MONTHS_RE = re.compile(r"[1-6]\s*\u4e2a\u6708")
# a week/day count ("2 weeks max", "a fortnight", "just a few days") is just as clear a short
# lease ask as an explicit month count, but the month-only detector above never covered it
# (P1 fix, 11 Sep 2026 attack replay: "just need it while my reno is ongoing, 2 weeks max"
# produced no lease note at all, only a generic unmatched flag). Same deposit/notice-term
# veto reused so "2 weeks notice" is never misread as a lease length ask.
_LEASE_EXPLICIT_WEEKS_RE = re.compile(
    r"\b([1-9]|1[0-9])\s*[- ]?\s*weeks?\b(?!\s*(?:ago|back|notice|deposit))|"
    r"\bfortnight\b|\ba\s+few\s+days\b|\bcouple\s+of\s+days\b|\bfew\s+weeks?\b", re.I)

def _short_lease_requested(text):
    """True when TEXT is a plain ask for a lease of 6 months or less (explicit month count 1
    to 6, half a year, short term/lease, few months, temporary). False for an open ended or
    long phrasing even when a small number appears in it ("6 to 12 months", "at least 6
    months", "1 year", "12 months") -- those never disqualify on their own so must never
    trip this reply."""
    t = (text or "").lower()
    if not t:
        return False
    if _LEASE_LEGAL_MARKER_RE.search(t):
        return False
    if _LEASE_YEAR_TOKEN_RE.search(t):
        return False
    m = _LEASE_RANGE_RE.search(t)
    if m:
        hi, unit = int(m.group(2)), m.group(3)
        hi_months = hi * 12 if unit.startswith(("year", "yr")) else hi
        if hi_months > 6:
            return False
    if _LEASE_ATLEAST_RE.search(t):
        return False
    if _LEASE_PAST_RE.search(t):
        return False
    if _LEASE_HALFYEAR_RE.search(t) or _LEASE_KEYWORD_RE.search(t):
        return True
    if _LEASE_CJK_MONTHS_RE.search(text or ""):
        return True
    if _LEASE_EXPLICIT_WEEKS_RE.search(t):
        return True
    if _LEASE_MONEYTERM_CONTEXT_RE.search(t):
        return False
    return bool(_LEASE_EXPLICIT_MONTHS_RE.search(t))

def _lease_note_pending_resolution(rec, ev, pn):
    """Interpret a reply to an already sent short lease note. Never auto reject (Winfred, 8
    Sep 2026): a decline or an insistence on staying short is FLAGGED to him, not redirected
    away on the engine's own say so. An acceptance fills lease_term_months (only if it was
    still empty) and lets the caller fall through to the normal flow. An ambiguous reply
    (neither) returns None with lease_note_resolved still unset -- the caller reads that as
    stay silent, one note only."""
    t_now = (ev.get("text") or "").lower()
    # SENSITIVE CONTENT always breaks through the one-flag-per-burst latches below: a
    # protected attribute fishing question, a dispute/complaint, or a legal threat must
    # reach Winfred regardless of where it lands in a "short lease follow up" burst -- the
    # routine-chatter latch (lease_ambiguous_flagged) is for exactly that, routine chatter,
    # never for this (P2 fix, 9 Sep 2026 cycle 3 attack replay: a discrimination-fishing
    # question sent 3rd in the burst produced zero action, zero Telegram, zero log line).
    # Own latch so a genuinely repeated identical message does not re-notify forever, but it
    # is checked and set independently of lease_q_flagged / lease_ambiguous_flagged.
    if _SENSITIVE_CONTENT_RE.search(t_now) and not rec.get("lease_sensitive_flagged"):
        rec["lease_sensitive_flagged"] = True
        return {"type": "FLAG_HUMAN", "pn": pn, "text": None, "notify": True,
                "reason": "sensitive content (protected attribute / dispute / legal) during "
                          "short lease follow up; reply by hand"}
    if re.search(r"\b(cannot|can'?t|cant|too long|shorter|short term|"
                 r"only \d+ ?(?:months?|mths?|mos?)|max(?:imum)? \d+ ?(?:months?|mths?|mos?))\b", t_now):
        if rec.get("lease_decline_flagged"):
            return None                      # already flagged once -> stay silent
        rec["lease_decline_flagged"] = True
        rec["status"] = "short_lease_declined_flagged"
        return {"type": "FLAG_HUMAN", "pn": pn, "text": None, "notify": True,
                "reason": "cannot meet the 1 year minimum lease; reply by hand"}
    if "?" in t_now:
        # a QUESTION about the minimum ("why must be 1 year?") is not acceptance. Only use
        # the lease-specific reason when the text actually references lease length -- a
        # different question ("can hold the room for me still right") mislabelled as a
        # lease question reads as a broken bot to Winfred (replay 9 Sep 2026).
        if rec.get("lease_q_flagged"):
            return None
        rec["lease_q_flagged"] = True
        reason = ("asked about the 1 year minimum lease; reply by hand"
                   if _FACT_LEASE_RE.search(t_now)
                   else "message during short lease follow up; reply by hand")
        return {"type": "FLAG_HUMAN", "pn": pn, "text": None, "notify": True, "reason": reason}
    if _is_affirmative(ev.get("text")) or re.search(
            r"\b(1 ?(?:year|yr)|one year|12 ?(?:months?|mths?|mos?)|"
            r"(?:1[3-9]|2[0-9]) ?(?:months?|mths?)|2 ?(?:years?|yrs?))\b", t_now):
        rec["lease_note_resolved"] = True
        # ALWAYS set to the accepted minimum, never only when empty -- a prospect who
        # earlier gave a short lease_term_months (that is WHY the note went out) and then
        # says YES to the landlord's 1 year minimum has just agreed to extend it. Leaving
        # the old short value in place made qualify() re-derive SHORT_LEASE on the very next
        # turn, the LEASE_NOTE latch then silently swallowed the repeat (one note only), and
        # the accepted prospect vanished into a bare "no automated reply matched" flag (P1
        # fix, 11 Sep 2026 attack replay: a bare "YES" to the note on a profile with
        # lease_term_months 3 produced total silence instead of QUALIFIED).
        rec["profile"]["lease_term_months"] = rec.get("lease_note_min", 12)
        return None                          # resolved; caller falls through to the normal flow
    # a completed profile form, or a media-only submission, must never be swallowed as
    # "ambiguous" -- let it fall through to the normal flow instead (9 Sep 2026 replay: a
    # full intake form and a bare photo both vanished behind this gate).
    if extract_profile(ev.get("text") or "") or (
            not (ev.get("text") or "").strip() and ev.get("media_type")):
        rec["lease_note_resolved"] = True
        return None
    # anything else ambiguous: flag once (neutral reason, this was never a lease reply),
    # never repeat, never silently eat it forever.
    if rec.get("lease_ambiguous_flagged"):
        return None
    rec["lease_ambiguous_flagged"] = True
    return {"type": "FLAG_HUMAN", "pn": pn, "text": None, "notify": True,
            "reason": "message during short lease follow up; reply by hand"}

# ---------- Category 1: factual auto-answers (never opinion/negotiation/legal) ----------
# Winfred's rule: some tenant questions have one true, boring answer sitting in the listing's
# own requirements (lease length, cooking, smoking, pets, rent) or in a facts sheet he fills in
# by hand (wifi, deposit, furnishing, aircon, mrt, availability, utilities). Those can be
# answered straight away instead of flagged. Anything with an opinion/negotiation/legal edge —
# "is it a good deal", "can you do less", "can I sublet" — must ALWAYS stay flagged to Winfred,
# even if a fact keyword also appears in the same message. One factual answer per prospect
# (rec["fact_answered"]); a second question always falls through to the human flag.
_FACT_VETO_RE = re.compile(
    r"good deal|worth\s+it|\bworth\b|\bsafe\b|\bdangerous\b|break\s+(?:\w+\s+){0,2}lease|"
    r"end\s+(?:\w+\s+){0,2}lease\s+early|\bterminate\b|"
    r"\bsublet(?:ting)?\b|stamp\s+duty|\bdiplomatic\b|deposit\s+refund\s+dispute|"
    # eviction/notice/law questions are legal advice, never a lease-length or other fact
    # lookup, even when "lease" also appears in the same message (P1 fix, 9 Sep 2026 cycle 3
    # attack replay: "can the landlord evict me early... what does the law say" auto answered
    # as a lease length FAQ)
    r"\bevict(?:ed|ion)?\b|notice\s+period|\blaw\b|\blegal(?:ly)?\b|\billegal\b|"
    r"tenant'?s?\s+rights?|\bbreach\b|\bcourt\b|small\s+claims|"
    # eligibility/policy questions (ethnic quota, HDB vs private, EIP) are never a rent/fact
    # lookup -- always Winfred's call, even when "rent" rides along as a bare verb (P2 fix,
    # 9 Sep 2026 cycle5 sc1: a quota eligibility question misfired the vague rent pivot).
    r"\bquota\b|ethnic\s+integration|\beip\b|\beligib(?:le|ility)\b|allowed\s+to\s+rent|"
    r"hdb\s+(?:vs\.?|versus|or)\s+private",
    re.I)
_FACT_LEASE_RE = re.compile(r"\blease\b|how\s+long|\bminimum\b|contract\s+length", re.I)
_FACT_COOK_RE = re.compile(r"\bcook(?:ing)?\b|\bkitchen\b", re.I)
_FACT_SMOKE_RE = re.compile(r"\bsmoke\b|\bsmoking\b", re.I)
_FACT_PET_RE = re.compile(r"\bpets?\b|\bdogs?\b|\bcats?\b", re.I)
_FACT_RENT_RE = re.compile(r"\brent\b|\bprice\b|how\s+much|\bcost\b|per\s+month|\bnego(?:tiable|tiate)?\b|\bcheaper\b|\blower\b|\bdiscount\b|flexib", re.I)
_FACT_RENT_NUMBER_RE = re.compile(r"\d{3,5}")
# facts-sheet lookups: (fact key, question-keyword pattern) — Winfred fills listing["facts"][key]
_FACT_SHEET_PATTERNS = (
    ("wifi", re.compile(r"\bwifi\b|\binternet\b", re.I)),
    ("deposit", re.compile(r"\bdeposit\b", re.I)),
    ("furnishing", re.compile(r"\bfurnish(?:ed)?\b", re.I)),
    ("aircon", re.compile(r"\baircon\b|\bair\s*con\b|\bair-con\b|\bservic", re.I)),
    ("mrt", re.compile(r"\bmrt\b|\btrain\b|\bstation\b|how\s+far", re.I)),
    ("available", re.compile(r"\bavailable\b|move\s*in|move-in|when\s+can", re.I)),
    ("utilities", re.compile(r"\butilities\b|\butility\b|\bbills\b", re.I)),
)
_FACT_UNKNOWN_VALS = ("unknown", "tbc", "n/a", "na", "")

def _fact_known(v):
    """A requirements value counts as a real, citable fact — not None and not a placeholder
    like 'unknown'/'TBC' (never fabricate an answer from an unfilled field)."""
    if v is None:
        return False
    if isinstance(v, str) and v.strip().lower() in _FACT_UNKNOWN_VALS:
        return False
    return True

def _fact_cooking_phrase(v):
    v = str(v).strip().lower()
    if v == "none":
        return "Sorry, no cooking is allowed in the unit \U0001F642"
    if v == "light":
        return "Light cooking only (no heavy cooking) is allowed in the unit \U0001F642"
    if v == "all":
        return "Cooking is allowed in the unit \U0001F642"
    return None

def _fact_smoking_phrase(v):
    v = str(v).strip().lower()
    if v == "no":
        return "Sorry, no smoking is allowed at the unit \U0001F642"
    if v == "any":
        return "Smoking is fine at the unit \U0001F642"
    return None

def _fact_pets_phrase(v):
    return ("Yes, you can bring your pet \U0001F642" if v
            else "Sorry, no pets allowed for this unit \U0001F642")

# the generic price pivot (no listing fact, no figure) never consumes the one-shot fact
# budget -- it is compared against verbatim below so the MRT/deposit/etc fact on file stays
# available for the prospect's next question (P2 fix, 9 Sep 2026 cycle5 sc1).
_RENT_PIVOT_TEXT = ("Rent is usually fixed \U0001F642 It's set by the landlord. Do come down to "
                    "view first, and shall I arrange a viewing for you?")

def _tenant_fact_answer(question_text, listing):
    """Return a truthful, Winfred-voice answer for a tenant's factual question, drawn ONLY from
    the listing's own data — never a fabricated or guessed answer. Returns None whenever the
    question carries any opinion/negotiation/legal edge (always flagged to Winfred instead), or
    when the fact it maps to simply is not on file for this listing."""
    t = question_text or ""
    if _FACT_VETO_RE.search(t):
        return None
    listing = listing or {}
    req = listing.get("requirements") or {}
    facts = listing.get("facts") or {}

    if _FACT_LEASE_RE.search(t):
        raw = req.get("lease_min_months")
        try:
            n = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            n = None
        N = max(12, n or 12)
        dur = "1 year" if N == 12 else f"{N} months"
        return f"The owner is looking for a minimum lease of {dur} \U0001F642"

    if _FACT_COOK_RE.search(t):
        v = req.get("cooking")
        return _fact_cooking_phrase(v) if _fact_known(v) else None

    if _FACT_SMOKE_RE.search(t):
        v = req.get("smoking")
        return _fact_smoking_phrase(v) if _fact_known(v) else None

    if _FACT_PET_RE.search(t):
        if "pets_tenant_may_bring" in req and req["pets_tenant_may_bring"] is not None:
            return _fact_pets_phrase(req["pets_tenant_may_bring"])
        return None

    # facts sheet BEFORE the rent pivot: "deposit how much" must never be read as a bare rent
    # negotiation just because it also contains "how much" (P0 fix, 9 Sep 2026 attack replay --
    # a deposit/refund question was misrouted into the vague rent pivot and auto-sent).
    for key, rx in _FACT_SHEET_PATTERNS:
        if rx.search(t):
            v = facts.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            return None

    if _FACT_RENT_RE.search(t):
        # a specific figure ("can you do 1400", "200 less") is a real negotiation -> Winfred handles it
        if _FACT_RENT_NUMBER_RE.search(t):
            return None
        # general price / negotiability: stay vague, never quote a figure, and pivot to a
        # viewing. No price-flexibility claim (P2 fix, 9 Sep 2026 cycle4 hg4-06): the bot has
        # no authority to represent what the landlord will or won't budge on -- rent is set by
        # the landlord, full stop, and the next step is a viewing.
        return _RENT_PIVOT_TEXT

    return None

# a tenant declining the OFFERED slot outright (no counter time of their own yet) -- distinct
# from _has_viewing_time, which fires when they DO name a day/time (a counter proposal).
_DECLINE_RE = re.compile(
    r"can\'?t\s+make|cannot\s+make|can\'?t\s+do|not\s+free|not\s+available"
    r"|another\s+day|some\s+other\s+time|busy\s+then|unable\s+to", re.I)
# a third party's unavailability ("she not free", "he can't make it") described mid message
# is never the TENANT declining their own slot (P2 fix, 9 Sep 2026 cycle5 sc2: a companion's
# unavailability corrupted state and nearly auto sent a non sequitur).
_DECLINE_THIRDPARTY_RE = re.compile(
    r"\b(?:she|he|they|her|him|them)\b[^.!?]{0,20}\b(?:not\s+free|can\'?t\s+make|"
    r"cannot\s+make|can\'?t\s+do|not\s+available|busy\s+then|unable\s+to)\b", re.I)

# a real question is not always punctuated ("can cook curry ah", "how far MRT from here") --
# a literal "?" is sufficient but never necessary. Shared by the pre-viewing question check
# below AND _viewing_reaction's post-offer question branch, so both stages read a question
# the same way (a bare "?" gate used to treat these two stages inconsistently).
# an interrogative token counts ANYWHERE in the message, not only in the first three words --
# "before i sign should i get a lawyer to check this, is that necessary" buries "should"/"is"
# past word 3 and was silently dropped (P1 fix, 9 Sep 2026 attack replay: a legal advice
# question got zero reply and zero flag). Singlish particles likewise count anywhere, not
# only as the final token ("anot leh can view" vs "can view anot leh").
# a modal auxiliary (can/is/will/...) alone is not enough: "1 year is actually fine for us" and
# "yes can" are plain acceptances, not questions -- a bare substring test on these words flags a
# plain acceptance to Winfred as if it were a question, with the wrong triage reason attached
# (P3 fix, 9 Sep 2026 cycle4 c4ec01). A modal only reads as a real (inverted) question when it
# is immediately followed by a subject -- "can I", "is there", "should i" -- wh-words (how/
# what/...) still count anywhere, no subject needed, since those are unambiguous. A false
# negative here still falls through to the dead-end catch-all flag, so erring narrow is safe.
_QUESTION_LEAD_RE = re.compile(
    r"\b(?:can|could|is|are|will|would|should)\b\s+"
    r"(?:i|you|we|they|he|she|it|there|this|that|the\s+landlord|the\s+owner)\b", re.I)
_QUESTION_WH_RE = re.compile(r"\b(?:how|what|when|why|who|where|which)\b", re.I)
# an aux/copula immediately followed by a wh word and a subject pronoun is a declarative
# RELATIVE clause ("that pin is where we stay"), never an interrogative -- a genuine wh
# question fronts the wh word, it does not follow a copula this way (P3 fix, 9 Sep 2026
# cycle5 sc4: a location pin caption misread "where" as a question).
_WH_RELATIVE_CLAUSE_RE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:how|what|when|why|who|where|which)\s+"
    r"(?:i|you|we|they|he|she|it)\b", re.I)
_QUESTION_TRAIL_RE = re.compile(r"\b(?:anot|or\s+not|ah|lah|leh|meh|sia|right)\b", re.I)

def _is_question(text):
    t = (text or "").strip()
    if not t:
        return False
    if "?" in t:
        return True
    if _QUESTION_LEAD_RE.search(t):
        return True
    if _QUESTION_WH_RE.search(t) and not _WH_RELATIVE_CLAUSE_RE.search(t):
        return True
    return bool(_QUESTION_TRAIL_RE.search(t))

# a genuine viewing/availability ask with no "?" and no lead/trail question word at all
# ("Trying to call you", "Hi any viewing") still means engagement, not silence -- shared by
# both the pre-complete PRE-VIEWING QUESTION CHECK and the post-nudge incomplete-profile
# flag, so neither dead ends a keen prospect just because they phrased it as a statement
# (P2 fix, 9 Sep 2026 cycle 3 attack replay).
_ENGAGEMENT_INTENT_RE = re.compile(
    r"\bany\s+(?:viewing|update|news)\b|\bstill\s+available\b|\bstill\s+there\b|"
    r"\btrying\s+to\s+call\b|\bcan\s+call\b", re.I)
def _is_engaged(text):
    return _is_question(text) or bool(_ENGAGEMENT_INTENT_RE.search(text or ""))

# ---------- stage 3 reaction (shared: autonomous flow + manual co-pilot after an auto-offer) ----------
def _no_slot_flag(pn):
    """An affirmative/time reply is about to build CONFIRM_VIEWING, but offered_slot_label is
    empty -- no slot was ever actually offered (a stray early YES, or the offer never landed).
    Confirming here would tell the prospect a viewing exists when it does not (P0, cycle4
    hg4-01). Send no tenant text at all and re-ask Winfred for the landlord's time, the same
    notice already sent when the enquiry first came in with no slot captured."""
    return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
            "reason": "prospect said yes to a viewing but no slot is bound for this listing; "
                      "reply with the landlord's next available viewing time so I can offer it"}


def _viewing_reaction(rec, ev, pn):
    """After a viewing has been offered, react to ONE prospect reply — confirm the slot, acknowledge a
    proposed time, or flag a question to Winfred. Used by the autonomous flow AND by the manual-takeover
    co-pilot once it has auto-offered, so a qualified tenant gets booked end-to-end. Every branch
    notifies Winfred so he can step in."""
    st = _listing_unavailable(rec.get("listing_key"))
    if st:
        return _room_gone_action(rec, pn, st)
    # RE-SCREEN on every reply (adversarial-review P0-1): viewing-first books on partial
    # profiles, so a later field can disqualify an existing booking ("No. of pax: 3" after
    # a 1-pax confirm). A booking is only as good as the latest profile.
    _lk_r = rec.get("listing_key")
    _listing_r = listing_reqs().get(_lk_r) if _lk_r else None
    if _listing_r:
        _pol_r = policy_excluded(rec.get("profile", {}), ev.get("text", ""),
                                 open_intake=_open_intake(_listing_r))
        _v_r, _why_r = qualify(_listing_r, rec.get("profile", {}))
        if _pol_r or _v_r == "DISQUALIFIED":
            _attr_r = "nationality" if _pol_r == "nationality" else _protected_attr_from_why(_why_r, _listing_r)
            if _attr_r:
                rec["viewing_confirmed"] = False
                act_r = _house_gate_redirect(pn, rec, _listing_r, listing_reqs(), _lk_r,
                                             _attr_r, _pol_r or _why_r)
                if act_r["type"] == "REDIRECT":
                    rec["stage"] = "DISQUALIFIED"
                return act_r
            if (not _pol_r and _disqualify_unsourced(rec, _why_r)
                    and not rec.get("unsourced_disqualify_flagged")):
                rec["unsourced_disqualify_flagged"] = True
                return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                        "reason": "post booking re screen would DISQUALIFY (" + "; ".join(_why_r)
                                  + ") but the deciding field is only sourced from incidental "
                                  "free text; verify by hand: " + _profile_summary(rec.get("profile", {}))}
            rec["terminal"] = True; rec["viewing_confirmed"] = False
            rec["stage"] = "DISQUALIFIED"; rec["status"] = "disqualified_post_booking"
            _reason_r = _pol_r or _why_r
            return {"type": "REDIRECT", "pn": pn, "notify": True, "reason": _reason_r,
                    "text": _redirect_text(_reason_r, rec.get("profile", {}),
                                           listing_reqs(), _lk_r)}
    txt = (ev.get("text") or "").lower()
    # a byte identical resend of the immediately previous message (someone double pasted the
    # same filled form, or hit send twice) is never a NEW proposed viewing time either -- the
    # label-count check in _has_viewing_time() alone only catches a form with 3+ fields.
    _is_resend = bool(ev.get("text")) and ev.get("text") == rec.get("prev_inbound")
    # tenant declines the offered slot outright ("can't make it that day" etc, no time of
    # their own yet) -> ask their preference ONCE, then let their NEXT reply (which will
    # carry a day/time) fall through to the _has_viewing_time branch below as a normal
    # counter proposal routed to Winfred via VIEWING_TIME_PROPOSED.
    if (not rec.get("asked_tenant_time") and not _has_viewing_time(txt)
            and _DECLINE_RE.search(txt)
            and not _DECLINE_THIRDPARTY_RE.search(txt)
            and not _is_question(ev.get("text"))):
        rec["asked_tenant_time"] = True
        rec["status"] = "asked_tenant_time"
        return {"type": "ASK_TENANT_TIME", "pn": pn, "notify": True,
                "text": "No worries \U0001F642 When are you free to view? Just let me know a day "
                        "and time and I will arrange it."}
    # viewing-first: once a slot is locked, chase whatever form fields are still missing —
    # after the booking, never in front of it (Winfred, 11 Aug 2026)
    _chase = ""
    try:
        if missing_required(rec.get("profile", {}), listing_reqs().get(rec.get("listing_key"))):
            _chase = (" Meanwhile pls complete the form above so the landlord has your "
                      "details \U0001F64F\U0001F3FB")
    except Exception:
        pass
    if _has_viewing_time(txt) and not _is_resend and not rec.get("exact_time_locked"):
        if rec.get("viewing_confirmed"):
            # they said YES earlier and are now answering "what time will you be coming?"
            if _proposed_date_in_past(ev.get("text") or ""):
                # never accept an impossible date unconditionally (replay 9 Sep 2026: "3 Sep"
                # said on 9 Sep was cheerfully noted as "see you then") -- drop the acceptance
                # text and let Winfred sort out the real day by hand.
                rec["status"] = "viewing_time_past_flagged"
                return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                        "reason": "proposed viewing time has already passed (\""
                                  + (ev.get("text") or "")[:80] + "\"); reply by hand"}
            rec["exact_time_locked"] = True; rec["exact_time"] = ev.get("text"); rec["status"] = "viewing_time_locked"
            return {"type": "VIEWING_TIME_PROPOSED", "pn": pn, "when": ev.get("text"), "notify": True,
                    "text": "Perfect, noted 🙂 See you then. I will send the unit number closer to the time."}
        # A YES that merely RESTATES the offered slot ("yes can, sunday works", "ok see you
        # sunday 3pm") is a confirmation, not a counter proposal (lifecycle test, 26 Jul 2026).
        # Guard: any hint of negotiation ("instead", "but", "another day") stays a proposal.
        _lbl_low = (rec.get("offered_slot_label") or "").lower()
        _days_said = set(re.findall(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*", txt))
        _days_said = {d[:3] for d in _days_said}
        _day_mismatch = bool(_days_said) and _lbl_low and not any(d in _lbl_low for d in _days_said)
        if (_is_affirmative(ev.get("text")) and not _day_mismatch
                and not re.search(r"\binstead\b|\bbut\b|\bchange\b|\banother\b|\bother (?:day|time)\b|\bcan'?n?o?t\b", txt)):
            if not rec.get("offered_slot_label") and not rec.get("offered_slot_id"):
                return _no_slot_flag(pn)
            rec["viewing_confirmed"] = True
            _lbl = rec.get("offered_slot_label")
            _win = (" The viewing window is " + _lbl + ".") if _lbl else ""
            _on = (" on " + rec.get("offered_slot_label")) if rec.get("offered_slot_label") else ""
            if re.search(r"\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}[:.]\d{2}\b", txt):
                rec["exact_time_locked"] = True; rec["exact_time"] = ev.get("text")
                rec["status"] = "viewing_time_locked"
                _texts = ["Ok can, your viewing is" + _on + " \U0001F642",
                          "See you then, I will send the unit number nearer the time."]
                if _chase: _texts.append(_chase.strip())
                return {"type": "CONFIRM_VIEWING", "pn": pn, "slot_id": rec.get("offered_slot_id"),
                        "notify": True, "texts": _texts, "text": _texts[0]}
            rec["status"] = "viewing_confirmed"
            _texts = ["Ok can, your viewing is" + _on + " \U0001F642",
                      "What time will you be coming? I will keep your slot and send the unit "
                      "number nearer the time."]
            if _chase: _texts.append(_chase.strip())
            return {"type": "CONFIRM_VIEWING", "pn": pn, "slot_id": rec.get("offered_slot_id"),
                    "notify": True, "texts": _texts, "text": _texts[0]}
        rec["status"] = "viewing_time_proposed"
        return {"type": "VIEWING_TIME_PROPOSED", "pn": pn, "when": ev.get("text"), "notify": True,
                "text": "Got it, let me confirm that slot with the owner and get back to you shortly."}
    if _is_question(ev.get("text")):
        if not rec["viewing_confirmed"] and _is_affirmative(ev.get("text")):
            if not rec.get("offered_slot_label") and not rec.get("offered_slot_id"):
                return _no_slot_flag(pn)
            # "yes, is there aircon?" — confirm the slot AND hold the question for Winfred
            rec["viewing_confirmed"] = True; rec["status"] = "viewing_confirmed"
            _on = (" on " + rec.get("offered_slot_label")) if rec.get("offered_slot_label") else ""
            _texts = ["Ok can, your viewing is" + _on + " \U0001F642",
                      "On your question, let me check with the owner and get back to you shortly."]
            if _chase: _texts.append(_chase.strip())
            return {"type": "CONFIRM_VIEWING", "pn": pn, "slot_id": rec.get("offered_slot_id"),
                    "notify": True, "question": ev.get("text"), "texts": _texts,
                    "text": _texts[0]}
        ans = (_tenant_fact_answer(ev.get("text"), listing_reqs().get(rec.get("listing_key")) or {})
               if not rec.get("fact_answered") else None)
        if ans:
            # the generic rent pivot never consumes the one-shot budget -- only a real
            # facts sheet / listing attribute answer does (P2 fix, 9 Sep 2026 cycle5 sc1).
            if ans != _RENT_PIVOT_TEXT:
                rec["fact_answered"] = True
            if _FACT_LEASE_RE.search(ev.get("text") or ""):
                rec["lease_fact_told"] = True
            return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True, "question": ev.get("text"), "text": ans}
        return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True, "question": ev.get("text"), "text": None}
    if not rec["viewing_confirmed"] and _is_affirmative(ev.get("text")):
        if not rec.get("offered_slot_label") and not rec.get("offered_slot_id"):
            return _no_slot_flag(pn)
        rec["viewing_confirmed"] = True; rec["status"] = "viewing_confirmed"
        _on = (" on " + rec.get("offered_slot_label")) if rec.get("offered_slot_label") else ""
        _texts = ["Ok can, your viewing is" + _on + " \U0001F642",
                  "What time will you be coming? I will keep your slot and send the unit "
                  "number nearer the time."]
        if _chase: _texts.append(_chase.strip())
        return {"type": "CONFIRM_VIEWING", "pn": pn, "slot_id": rec.get("offered_slot_id"),
                "notify": True, "texts": _texts, "text": _texts[0]}
    return None

# ---------- core handler: entry point enforces the per-prospect message cap ----------
_TRIVIAL_ACK_RE = re.compile(
    r"^(?:ok|okay|kk|k|noted|thanks|thank\s*you|thx|ty|👍|👌|🙏|❤️|😊|🙂)$", re.I)

def _is_trivial_inbound(text):
    """A bare emoji, ack, or blank message carries nothing to flag -- the dead end catch-all
    below must never fire on these, only on a real (if silently mishandled) tenant message."""
    t = (text or "").strip()
    if not t:
        return True
    if _TRIVIAL_ACK_RE.match(t):
        return True
    if not re.search(r"\w", t):        # punctuation/emoji only, no letters or digits at all
        return True
    return False

_DEAD_END_RATE_WINDOW = 4 * 3600

def _dead_end_catch_all(state, ev):
    """Shared safety net (P1 fix, 9 Sep 2026 cycle4): if the inbound is a real tenant message
    and every branch in _handle_event_inner fell through to None -- a true silent dead end, no
    send and no flag -- ping Winfred once instead of vanishing. Rate limited ONCE PER STATE per
    few hours: it shares its rate limit bookkeeping (_last_notified_status/_ts, stamped by
    handle_event after ANY notify, not only this catch-all) with every other gate's own
    once-per-record notify latch, so a dead end right after a real flag on the SAME status
    never double-notifies -- those dedicated gates already decided silence was correct for
    THAT exact repeat. Never produces tenant-facing text, so every existing send/silence gate
    (manual takeover, terminal notify, dedup, cap) is completely untouched -- this only ever
    adds a notify on top of an existing silent None."""
    pn = resolve_pn(ev.get("jid"))
    if not pn or ev.get("is_from_me"):
        return None
    rec = (state.get("conversations") or {}).get(pn)
    if rec is None:
        return None
    if rec.get("manual_takeover") or rec.get("terminal"):
        return None       # already silenced/handled by its own dedicated gate
    _text_pre = ev.get("text") or ""
    if _HIGH_RISK_CONTENT_RE.search(_text_pre) and _high_risk_escalate(rec, "dead_end", _text_pre):
        # high risk content (legal/advice, landlord identity/contact fishing, protected
        # attribute, deposit/payment, prompt injection) always breaks through EVERY carve out
        # and rate limit below -- including the incomplete-nudge and lease-note sub-flows,
        # which have no sensitive-content escalation of their own for a non-question message
        # (P1 fix, 9 Sep 2026 cycle4 c4rm03: a fake-authority injection immediately after
        # another flag on the same status was silently rate limited away).
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": "sensitive/high risk content, no automated reply matched: \""
                          + _text_pre[:120] + "\"; reply by hand"}
    if rec.get("nudged_incomplete") and not rec.get("incomplete_flagged"):
        # the incomplete-profile nudge already OWNS this state's silence policy: it already
        # sent the prospect the missing-fields text once, and deliberately stays silent
        # afterward unless they re-engage with a question/viewing time (its own dedicated
        # escalation, incomplete_flagged, handles that) -- the catch-all must not second
        # guess a silence that specific mechanism already chose on purpose.
        return None
    if rec.get("lease_note_sent") and not rec.get("lease_note_resolved"):
        # every inbound in this state is already routed exclusively through
        # _lease_note_pending_resolution (top of _handle_event_inner), which has its own
        # complete sensitive-content/decline/question/ambiguous-once policy -- a None from it
        # is that policy's own considered silence, not a gap the catch-all should fill.
        return None
    if rec.get("lease_note_unbound_flagged") and not (rec.get("form_sent") and rec.get("listing_key")):
        # the SHORT LEASE gate already flagged this exact "unbound, mentioned a short lease"
        # situation once, on purpose, and deliberately goes silent on every repeat until the
        # record actually binds -- not a gap either.
        return None
    text = ev.get("text") or ""
    if _is_trivial_inbound(text):
        return None
    now = __import__("time").time()
    status = rec.get("status") or ""
    # the once-per-state window only ever suppresses a genuine REPEAT of the exact same
    # inbound already notified under this status -- a different message on the same status
    # (P1 fix, 9 Sep 2026 cycle5 c5rm01: a statement phrased message right after two
    # question flags on the same "form_sent" status vanished with no flag at all) must
    # always still reach Winfred.
    if (rec.get("_last_notified_status") == status
            and rec.get("_last_notified_text") == text
            and (now - (rec.get("_last_notified_ts") or 0)) < _DEAD_END_RATE_WINDOW):
        return None
    return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
            "reason": "no automated reply matched (" + status + "): \""
                      + text[:120] + "\"; reply by hand"}

def handle_event(state, ev):
    """Entry point: run the engine, then enforce a hard cap of MAX_PROSPECT_MSGS prospect-facing
    messages per person across the whole qualification attempt. Beyond the cap the bot stops
    messaging them and pings Winfred once (CAP_REACHED). Notify-only actions (FLAG_HUMAN /
    COPILOT_VERDICT / ANSWER_QUESTION) carry no prospect text, so they never count and are never
    capped — a real back-and-forth that needs Winfred can still surface."""
    # peek BEFORE _handle_event_inner runs: it unconditionally appends msg_id to processed_ids
    # as part of normal processing, so checking membership AFTER the call can never tell a
    # genuine redelivery of an already-seen id apart from the very message just processed.
    _pn_probe = resolve_pn(ev.get("jid"))
    _rec_probe = (state.get("conversations") or {}).get(_pn_probe) if _pn_probe else None
    _mid = ev.get("msg_id")
    _redelivered = bool(_rec_probe and _mid and _mid in (_rec_probe.get("processed_ids") or ()))
    a = _handle_event_inner(state, ev)
    if a is None and not _redelivered:
        a = _dead_end_catch_all(state, ev)
    if a and (a.get("text") or a.get("texts")):
        rec = state.get("conversations", {}).get(a.get("pn"))
        if rec is not None:
            if rec.get("sent_count", 0) >= MAX_PROSPECT_MSGS:
                if rec.get("cap_flagged"):
                    a = None                          # already flagged once -> stay silent
                else:
                    rec["cap_flagged"] = True
                    a = {"type": "CAP_REACHED", "pn": a.get("pn"), "notify": True, "text": None,
                         "listing_key": rec.get("listing_key"),
                         "reason": "reached the " + str(MAX_PROSPECT_MSGS) + " message cap"}
            else:
                rec["sent_count"] = rec.get("sent_count", 0) + len(a.get("texts") or [a.get("text")])
    # shared "last notified" bookkeeping (P1 fix, 9 Sep 2026 cycle4): stamped on EVERY notify,
    # whatever branch produced it, so the dead end catch-all's once-per-state rate limit
    # correctly treats a repeat right after a real, dedicated-gate notify as already covered.
    if a and a.get("notify"):
        _pn2 = a.get("pn") or _pn_probe
        _rec2 = (state.get("conversations") or {}).get(_pn2) if _pn2 else None
        if _rec2 is not None:
            _rec2["_last_notified_status"] = _rec2.get("status")
            _rec2["_last_notified_text"] = ev.get("text") or ""
            _rec2["_last_notified_ts"] = __import__("time").time()
    return a

# ---------- inner handler: returns at most ONE action ----------
def _handle_event_inner(state, ev):
    """
    ev = {jid, msg_id, text, is_from_me, listing_key (optional)}
    Returns an action dict {type, pn, text?} or None. At most one per inbound.
    Pure: caller persists state and (if not DRY_RUN) performs the send.
    """
    pn = resolve_pn(ev["jid"])
    if not pn: return None
    # A known landlord must never become a tenant-intake record. On an outbound-first chat
    # (Winfred messaging them) manual_takeover would otherwise latch below, BEFORE the Stage-1
    # exclusion gate is ever reached, quietly re-polluting the funnel. Drop any stray record and
    # stay out, in either direction. (Unknown-name landlords are still caught at Stage 1 on an
    # inbound enquiry, and the nightly purge sweeps the rest.)
    if pn in (_landlord_pn_set() or frozenset()):   # None (unreadable DB) = skip nobody here;
        if isinstance(state.get("conversations"), dict):  # excluded_reason fails closed instead
            state["conversations"].pop(pn, None)
        return None
    rec = _rec(state, pn)

    # ----- our own / human outbound -----
    if ev.get("is_from_me"):
        # B established (review fix): ANY outbound (hand or engine) seen at all, so a later
        # first inbound can tell whether Winfred/automation spoke first in this chat -- an
        # outbound-first chat is his own contact, not a lead who found him.
        rec["_any_outbound_seen"] = True
        # if it is not an engine-tagged message, Winfred replied by hand -> go silent.
        # copilot_muted makes that silence REAL: after a hand reply the copilot may never
        # message this prospect again (no auto-offer, no confirm) — screening verdicts only.
        if not ev.get("engine"):
            rec["manual_takeover"] = True
            rec["copilot_muted"] = True
            rec["human_takeover"] = True   # genuine hand reply -- silences landlord onboarding too
            rec["status"] = "manual"
            # takeover resume clock: the runner waits 5 minutes from THIS reply before it
            # will offer to draft a follow up on Winfred's behalf, and a later hand reply
            # always restarts the wait (Winfred, 8 Sep 2026).
            if ev.get("ts"):
                rec["last_hand_reply_ts"] = ev["ts"]
        # bind from OUTBOUND too: Winfred's hand reply often names the address, and a
        # sanctioned automation ack (PG auto-ack) always does. Either can carry the listing
        # that a plain inbound "still available?" never named. Never overwrite an existing bind.
        # Sourced "outbound": never counts toward is_established_prospect() (review fix) --
        # Winfred/automation naming a listing is not proof the tenant enquired about it.
        if ev.get("listing_key") and not rec.get("listing_key"):
            rec["listing_key"] = ev["listing_key"]
            rec["listing_key_source"] = "outbound"
        if ev.get("text"):
            rec["last_outbound"] = ev["text"]
        return None

    # ----- inbound from prospect -----
    mid = ev.get("msg_id")
    if mid and mid in rec["processed_ids"]:
        return None                      # event dedup: read once
    if mid:
        rec["processed_ids"].append(mid)
        # dedup only ever needs the recent window (the watermark keeps old rows out of the
        # scan); unbounded growth reached 1,257 ids on one chatty record and bloats the state.
        if len(rec["processed_ids"]) > 200:
            rec["processed_ids"] = rec["processed_ids"][-200:]
    # snapshot BEFORE overwriting -- lets a later stage spot a byte identical resend (a
    # duplicate paste of the same filled form) versus the true previous message.
    rec["prev_inbound"] = rec.get("last_inbound")
    rec["last_inbound"] = ev.get("text")
    if ev.get("ts"):
        # cold guard timestamp for a /send-from-draft self-chat command (Winfred, 9 Sep
        # 2026) -- distinct from last_hand_reply_ts (that one is WINFRED's own reply clock).
        rec["last_inbound_ts"] = ev["ts"]
    if rec.get("first_inbound_text") is None:
        # B established (review fix): first-touch snapshot only -- portal boilerplate check
        # and the outbound-before-first-inbound flag are both anchored to THIS one message.
        rec["first_inbound_text"] = ev.get("text") or ""
        rec["outbound_before_first_inbound"] = bool(rec.get("_any_outbound_seen"))
    # language upgrade, one direction only (Winfred, 11 Sep 2026 attack replay): almost
    # every portal enquiry's very first inbound is the PropertyGuru/99.co auto boilerplate,
    # itself always English even for a Chinese speaking tenant -- "lang stamped once, at
    # first touch" then locked every later reply to English despite every one of the
    # tenant's own words being Chinese. Any CJK character on ANY inbound upgrades a record
    # that has not yet locked to Chinese; a genuinely Chinese record never reverts to
    # English just because a later reply happens to be in English (test_lang_stamped_once_
    # and_reused_on_later_messages already covers, and must keep covering, that direction).
    if rec.get("lang") != "zh" and _CJK_RE.search(ev.get("text") or ""):
        rec["lang"] = "zh"
    if rec.get("source") is None:
        # first-touch only: never re-classify once stamped, even if a later message
        # happens to match a CTA phrase (e.g. copy-pasted from an article by hand).
        rec["source"] = classify_lead_source(ev.get("text"), ev.get("listing_key"))
    # a sticker/location pin with no text on a record already past Stage 1 carries nothing
    # to reply to and nothing worth flagging Winfred about -- but it must still leave a
    # trace, never a silent drop (P3 fix, 9 Sep 2026 cycle5 sc4). The louder "could be a
    # landlord" flag above already owns the pre form_sent case.
    if (str(ev.get("media_type") or "") in ("image", "video", "sticker", "location")
            and not (ev.get("text") or "").strip()
            and (rec.get("form_sent") or rec.get("profile"))
            and not rec.get("terminal") and not rec.get("manual_takeover")
            # the lease note sub flow owns every inbound while it is pending -- a media only
            # message there is its own considered "falls through, resolved" policy, not a
            # gap this quiet log should fill.
            and not (rec.get("lease_note_sent") and not rec.get("lease_note_resolved"))):
        return {"type": "MEDIA_NO_TEXT", "pn": pn, "notify": False, "text": None,
                "reason": "media (" + str(ev.get("media_type") or "unknown") + ") with no "
                          "text; no reply needed"}
    if rec.get("terminal"):
        # engine never sends again on a terminal record, but a fresh enquiry, a mention of
        # another open listing, or a complaint still needs a human's eyes -- once per
        # distinct inbound (a repeated "still available???" burst pings Winfred once, not
        # once per message).
        _text_now = ev.get("text") or ""
        if _terminal_worth_notifying(rec, _text_now):
            _sig = re.sub(r"\s+", " ", _text_now.strip().lower())
            _seen = rec.get("terminal_notify_sigs") or []
            if _sig and _sig not in _seen:
                rec["terminal_notify_sigs"] = (_seen + [_sig])[-20:]
                return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                        "reason": "new message on a closed conversation (" + rec.get("status", "")
                                  + "): \"" + _text_now[:120] + "\""}
        return None                      # closed / terminal conversation -> engine never acts again
    _signoff = _signoff_signal(ev.get("text"))
    if _signoff and _has_open_offer_or_proposed_time(rec, ev):
        _signoff = False   # a bare thanks right after a proposed/open viewing time is not a withdrawal
    if withdrawal_signal(ev.get("text")) or _signoff:
        # a bare thanks/goodbye is a POLITE close, never a withdrawal (Winfred, 9 Sep 2026
        # merge redo): same fixed reply + terminal state, but framed and logged as such, and
        # a new place is never implied for it.
        _new_place = _closing_implies_new_place(ev.get("text"))
        rec["terminal"] = True; rec["stage"] = "WITHDRAWN"
        rec["status"] = "closed (found elsewhere)" if _new_place else "closed (not keen / signed off)"
        rec["closed_reason"] = ("auto: prospect signalled they found another place"
                                 if _new_place else
                                 "auto: prospect signalled not keen / said thanks / signed off")
        # notify=False: the fixed reply below closes the loop on its own -- no Telegram ping,
        # no draft (never route a closing pleasantry through the drafting flow).
        _closing_zh = _lang(rec) == "zh"
        if _closing_zh:
            _closing_text = CLOSING_TEXT_NEW_PLACE_ZH if _new_place else CLOSING_TEXT_GENERIC_ZH
        else:
            _closing_text = CLOSING_TEXT_NEW_PLACE if _new_place else CLOSING_TEXT_GENERIC
        return {"type": "AUTO_CLOSED", "pn": pn, "notify": False,
                "text": _closing_text,
                "listing_key": rec.get("listing_key"),
                "reason": rec["closed_reason"],
                "quote": (ev.get("text") or "")[:160]}
    if _protected_disclosure_question(ev.get("text")):
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": "self disclosed a protected attribute and asked if acceptable; "
                          "never auto decide, reply by hand"}
    if _unit_rejection(ev.get("text")) and not rec.get("alt_suggested") and not rec.get("buyer_form_sent"):
        # they rejected THIS unit but are still looking: cross sell once, same district,
        # then rebind the conversation to the suggested listing so the normal qualify ->
        # offer viewing -> YES -> exact time flow re-runs against the new unit.
        # buyer_form_sent excluded: a buyer commenting "too small"/"too far" on a PURCHASE
        # enquiry is feedback for _buyer_followup (capture profile + flag to Winfred), not a
        # rental unit rejection -- this gate used to fire first and silently kill the buyer
        # conversation with a rental "here are my other rooms" redirect (backtest, 5 Aug 2026).
        rec["alt_suggested"] = True
        alt = suggest_alternative(rec.get("profile") or {}, rec.get("listing_key"))
        if alt:
            k2, alt_text = alt
            rec["listing_key"] = k2
            rec["listing_key_source"] = "hotmatch"   # engine cross sell, never tenant named
            rec["viewing_asked"] = False; rec["viewing_confirmed"] = False
            rec["book_intent_asked"] = False    # intent never carries across listings
            rec["offered_slot_id"] = None; rec["offered_slot_label"] = None
            rec["exact_time_locked"] = False
            rec["sent_count"] = 0; rec["cap_flagged"] = False   # cap is per qualification attempt
            rec["stage"] = "ALT_SUGGESTED"; rec["status"] = "alt_suggested:" + k2
            return {"type": "SUGGEST_ALT", "pn": pn, "notify": True, "listing_key": k2,
                    "text": "No problem 🙂 I have another room nearby that may suit you better:\n\n"
                            + alt_text + "\n\nKeen to take a look? I can arrange a viewing for you."}
        rec["terminal"] = True; rec["stage"] = "CLOSED_UNIT_REJECTED"
        rec["status"] = "closed (unit rejected, no alternative)"
        # always notify: a terminal close silently drops an already confirmed viewing off
        # Winfred's radar otherwise (P0 fix, 9 Sep 2026 attack replay).
        return {"type": "REDIRECT", "pn": pn, "notify": True,
                "reason": "unit rejected, no alternative in district",
                "text": "No worries 🙂 You can see my other available rooms here:\n" + CHANNEL
                        + "\nLet me know if anything catches your eye and I'll arrange a viewing."}

    # always merge any profile data, even under manual takeover (log once).
    # track whether THIS inbound added a new required field (drives state change).
    merged = extract_profile(ev.get("text",""))
    _positional = {}
    if len(merged) < 3:
        _positional = _extract_positional_form(ev.get("text", ""))
        if _positional:
            merged = _positional
    new_data = False
    # provenance per field: a value grab() lifted out of ordinary prose ("...as ID, name Alex
    # Tan, hope that helps.") is a guess, never as trustworthy as a real filled labelled form
    # line ("Name: Alex Tan"). A later clean form is allowed to CORRECT an earlier free text
    # guess; free text may never overwrite anything, guess or form (9 Sep 2026 replay: a real
    # completed form was silently discarded because a stray "name" mid sentence got there
    # first under the old never overwrite rule).
    _form_now = _looks_like_filled_form(ev.get("text", "")) or bool(_positional)
    _correction_now = bool(_CORRECTION_RE.search(ev.get("text", "") or ""))
    _prov = rec.setdefault("profile_provenance", {})
    for k,v in merged.items():
        cur = rec["profile"].get(k)
        if cur in (None,""):
            rec["profile"][k] = v
            _prov[k] = "form" if _form_now else "free_text"
            if k in REQUIRED_FIELDS: new_data = True
        elif _form_now and v != cur:
            # a later CLEAN FORM resubmission always wins on the fields it explicitly states,
            # whether the stored value came from an earlier free text guess OR an earlier
            # form -- a prospect who sends contradictory duplicate forms is qualified on
            # their LATEST stated values, never a stale first one (P1 fix, 9 Sep 2026 cycle 3
            # attack replay: a 6 month/2 pax/1600 third form was discarded and the record
            # still qualified past the 12 month gate on the first form's numbers). Never
            # overwrite stays true only for fields THIS message does not restate -- those
            # simply are not in merged at all.
            rec["profile"][k] = v
            _prov[k] = "form"
            if k in REQUIRED_FIELDS: new_data = True
        elif _correction_now and v != cur and _prov.get(k) != "form":
            # an explicit correction ("sorry typo", "actually", "i mean") may overwrite a
            # value whose only source was incidental free text (grab() lifting a number out
            # of ordinary prose, never a labelled answer) -- but a FORM sourced value still
            # only yields to a later form, never to free text (P0 fix, 9 Sep 2026 cycle4
            # c4ec01 replay: "3 of us" -> "sorry typo ... 2 pax" left pax stuck at 3).
            rec["profile"][k] = v
            _prov[k] = "correction"
            if k in REQUIRED_FIELDS: new_data = True

    # SAFETY NET: a message carrying 5+ DISTINCT known field labels (whatever the punctuation
    # -- colon-per-line, a numbered list, a bare space-separated caption/OCR form, a language
    # grab() has no synonym for) that still only parsed under 3 fields is unparseable, not
    # empty -- flag it once instead of silently treating it as if nothing was sent (replay 9
    # Sep 2026: a fully completed Chinese label form fell through to nothing, dead silence;
    # P1 fix, 9 Sep 2026 cycle4: a numbered/space-separated form the same way).
    _label_word_count = len(set(w.lower() for w in re.findall(
        r"\b(?:" + _FIELD_LABEL_ALT + r")\b", ev.get("text", "") or "", re.I)))
    if (_label_word_count >= 5 and len(merged) < 3
            and not rec.get("unparseable_form_flagged")):
        rec["unparseable_form_flagged"] = True
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": "looks like a filled form (" + str(_label_word_count)
                          + " labelled fields) but only " + str(len(merged))
                          + " field(s) parsed; reply by hand"}

    # SAFETY NET 2: an unlabeled, delimiter separated (comma/slash/semicolon) form has no
    # colons at all, so the label-line count above never sees it -- but a message carrying an
    # email address plus several such values ("kevin tan,kevintan99@mail.com,singaporean,
    # chinese,male,26,sc,retail,permanent,1,1dec,12,850") is unmistakably a filled-in form,
    # not silence. Fail closed with the raw text in the reason rather than returning None and
    # stranding every follow up after it too (P1 fix, 9 Sep 2026 cycle 3 attack replay).
    _has_email = bool(re.search(r"[\w.+-]+@[\w.-]+\.\w+", ev.get("text", "")))
    _delim_vals = [v for v in re.split(r"[,/;]", ev.get("text", "")) if v.strip()]
    if (_has_email and len(_delim_vals) >= 6 and len(merged) < 3
            and not rec.get("unparseable_form_flagged")):
        rec["unparseable_form_flagged"] = True
        return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                "reason": "unlabeled delimiter separated form, could not auto parse: \""
                          + (ev.get("text", "") or "")[:200] + "\"; reply by hand"}

    if ev.get("listing_key") and not rec.get("listing_key"):
        # Sourced "inbound": the ONLY source is_established_prospect() trusts -- the tenant's
        # own text named this listing (review fix).
        rec["listing_key"] = ev["listing_key"]; new_data = True
        rec["listing_key_source"] = "inbound"
    # ev["resume"] is set ONLY by the runner's takeover resume trigger (a prospect reply that
    # Winfred never answered, 5+ minutes after his last hand reply): it runs THIS ONE inbound
    # through the normal autonomous flow below exactly as if manual_takeover were not latched,
    # so the SAME deterministic gates (qualify, policy_excluded, listing status, quiet hours
    # etc, all still enforced by the runner's send choke point) decide the reply. The runner
    # then allow lists which action TYPES that reply may actually reach a real send with --
    # anything else becomes a drafted suggestion instead. The latch itself is untouched: the
    # very next tick still treats this record as manual_takeover for every other purpose.
    if rec["manual_takeover"] and not ev.get("resume"):
        rec["status"] = "manual"
        # Landlord onboarding runs INSIDE this latch: supply side detection sets
        # manual_takeover purely to keep the record out of the tenant/buyer flows, not
        # because Winfred replied by hand. human_takeover is the real signal for that (set
        # only on a genuine hand reply) -- once it is set, this goes fully silent like every
        # other manual chat.
        if rec.get("supply_flagged") and _rec_supply_kind(rec) == "landlord":
            if rec.get("human_takeover"):
                return None
            return _landlord_onboarding_reaction(rec, ev, pn)
        # Once the co-pilot has auto-offered a viewing, it OWNS the rest of that flow: it reacts to the
        # prospect's reply (confirm the slot / acknowledge a proposed time / flag a question) exactly
        # like the autonomous path, while still pinging Winfred. Before any auto-offer it stays silent
        # to the prospect and only screens (and auto-offers once QUALIFIED + a slot exists).
        # copilot_muted overrides ALL of that: Winfred has replied by hand, so no prospect sends,
        # only screening verdicts.
        if rec.get("viewing_asked") and not rec.get("copilot_muted"):
            return _viewing_reaction(rec, ev, pn)
        return _copilot_verdict(rec)

    reqs = listing_reqs()

    # MID THREAD SELF DISCLOSURE (P1 fix, 9 Sep 2026 cycle4 hg4-05): excluded_reason() used to
    # run ONLY inside the stage-1 (not yet form_sent) branch below, so an agent, landlord, or
    # colleague who reveals themselves AFTER the form already went out sailed straight through
    # every later message -- including a message that would otherwise be swallowed into the
    # short-lease-followup sub-flow below and mislabelled as routine chatter (hg4-05: "I'm
    # actually a property agent too, co broke 50/50" landed as "message during short lease
    # follow up" instead of latching takeover). Checked BEFORE that sub-flow so it always
    # wins. _LANDLORD_FEE_NEG still vetoes inside excluded_reason(), so a landlord haggling
    # commission is never mislabelled an agent. db_error is left to the stage-1 path only: a
    # transient contact-DB lock must not latch takeover on a live thread.
    if rec.get("form_sent"):
        _mid_why = excluded_reason(pn, ev.get("text", ""))
        if _mid_why in ("landlord", "agent", "colleague"):
            rec["manual_takeover"] = True
            rec["copilot_muted"] = True   # P0 fix, 9 Sep 2026 cycle5 c5ec07: a self disclosed
            # agent/landlord/colleague who later recants and fills a tenant shaped profile
            # must never reach a real prospect facing OFFER_VIEWING through the co-pilot
            # verdict path -- mute it here exactly like the supply side branch already does.
            rec["status"] = "excluded:" + _mid_why
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True,
                    "reason": "excluded " + _mid_why, "text": None}

    # SHORT LEASE AUTO REPLY (Winfred, 8 Sep 2026): fires on ANY tenant inbound, bound or not,
    # form sent or not -- ahead of every other stage, since the whole point is to catch the
    # ask the moment it is typed rather than waiting for a complete profile. Never for a buyer
    # or a supply side (landlord/seller) record; never a second note (lease_note_sent latch,
    # shared with the qualify()-driven path further down so only one note ever goes out).
    if rec.get("lease_note_sent") and not rec.get("lease_note_resolved"):
        _act = _lease_note_pending_resolution(rec, ev, pn)
        if _act is not None:
            return _act
        if not rec.get("lease_note_resolved"):
            return None                      # ambiguous reply; stay silent, one note only
        # else: resolved this turn (accepted) -> fall through to the normal flow below
    elif (not rec.get("lease_note_sent") and not rec.get("buyer_form_sent")
            and not rec.get("supply_flagged")
            and (rec.get("form_sent") or excluded_reason(pn, ev.get("text", "")) is None)
            # already agreed to an acceptable term (12+ months) -> never re raise the note
            and not (isinstance(rec["profile"].get("lease_term_months"), int)
                     and rec["profile"]["lease_term_months"] >= 12)
            and _short_lease_requested(ev.get("text"))):
        # B1 (Sep 2026): the auto note names "the landlord" and their minimum lease, so it
        # must never fire until we actually KNOW which landlord that is -- a genuine tenant
        # prospect with the form already sent AND a listing bound. A casual "short term ok"
        # in a chat that only just got bound off Winfred's own outbound text (or never got
        # bound at all) flags him once instead (real incident, 8-9 Sep 2026: pn 6590590183,
        # wandering across 3 different properties with no confirmed listing_key).
        if rec.get("form_sent") and rec.get("listing_key"):
            rec["lease_note_min"] = 12
            rec["lease_note_sent"] = True
            if rec.get("lease_fact_told"):
                # the minimum lease was already stated once, as a direct fact answer to an
                # earlier question on THIS chat -- restating it now as a fresh "would that
                # work for you" reads as the bot repeating itself (Winfred, 11 Sep 2026
                # attack replay: "The owner is looking for a minimum lease of 1 year" then,
                # two messages later, "Just to share, the landlord prefers a minimum 1 year
                # lease" in the same short thread). Move straight on to the verdict instead.
                rec["lease_note_resolved"] = True
            else:
                rec["stage"] = "LEASE_NOTE"; rec["status"] = "short_lease_note"
                return {"type": "LEASE_NOTE", "pn": pn, "notify": False,
                        "reason": "asked for a lease of 6 months or less",
                        "text": _lease_note_text(_lang(rec))}
        # a genuine FIRST TOUCH enquiry that also names the listing (bound already, or this
        # very inbound names it) must still get its welcome + form -- never dead end silently
        # on a brand new prospect (P2 fix, 9 Sep 2026 cycle5 sc5). The short lease ask itself
        # is never auto answered; it rides as a notify on the SEND_FORM action once Stage 1
        # actually sends it (fall through, no return, past this whole gate). Genuinely
        # UNBOUND wandering (the real incident this gate was built for, pn 6590590183 across
        # 3 properties) keeps the original silent flag.
        if not (rec.get("listing_key") or ev.get("listing_key")):
            if rec.get("lease_note_unbound_flagged"):
                return None
            rec["lease_note_unbound_flagged"] = True
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                    "reason": "asked about a short lease, not yet a bound form sent prospect"}
        rec["pending_short_lease_notify"] = ev.get("text")

    # STAGE 1: first contact -> send the listing message (unit info + form) ONCE, with safety gates
    if not rec["form_sent"]:
        # a buyer who already has the buyer form is in the BUYER flow — parse/nudge/hand off.
        # EXCEPTION: a DECISIVE new-message signal (explicit RENT token, registry deal_type,
        # per-month price...) can still break them out into a genuine rental enquiry -- only an
        # AMBIGUOUS message (a buyer-form answer, a repeat sale ping) stays routed to the buyer
        # flow. Fixed 31 Jul 2026: this used to swallow a later unambiguous rental enquiry as a
        # silent buyer-flow ANSWER_QUESTION.
        if rec.get("buyer_form_sent"):
            tx_now, _ = classify_transaction(ev.get("text",""), ev.get("listing_key"))
            if tx_now != "rent":
                return _buyer_followup(rec, ev, pn)
        why = excluded_reason(pn, ev.get("text",""))
        if why == "db_error":                    # contact DB locked -> fail closed for THIS run,
            rec["status"] = "deferred_db_lock"   # but do NOT latch (a genuine prospect re-checks
            # keep the deferred enquiry text: their NEXT message may be just "any update?",
            # which alone would fail the tenant-enquiry gate and dead-end a real prospect.
            rec["deferred_text"] = ((rec.get("deferred_text") or "") + "\n" + (ev.get("text") or ""))[-1000:]
            return {"type":"FLAG_HUMAN", "pn":pn, # next run once the DB is readable). No send.
                    "reason":"contact DB unreadable; deferring, no auto-send", "text":None}
        if why:                                  # landlord, agent, or colleague -> never message
            rec["manual_takeover"] = True; rec["status"] = "excluded:" + why
            return {"type":"FLAG_HUMAN", "pn":pn, "reason":"excluded " + why, "text":None}
        # SUPPLY SIDE: read across the contact's first few messages. An owner — landlord (renting
        # out) OR seller (selling) — even one not yet in the DB, must never get a tenant or buyer
        # intake form. Flag once, stay silent. Renting and selling are kept distinct in the flag.
        _supply, _sup_conf = supply_side_kind(ev.get("jid"), ev.get("text",""), with_confidence=True, rec=rec)
        if _supply:
            if rec.get("supply_flagged"):
                return None
            rec["supply_flagged"] = True; rec["status"] = "supply_side:" + _supply; rec["supply_kind"] = _supply
            _label = "landlord (renting out)" if _supply == "landlord" else "seller (selling)"
            # send THEIR intake form once (landlord onboarding / seller intake, verbatim from
            # _templates), then go silent: manual takeover latches so the engine never messages
            # them again — Winfred and the nightly DB refresh own the rest of the relationship.
            # Only on a CONFIDENT phrase match: the image-only opener stays flag-to-human.
            form = _supply_form(_supply) if _sup_conf else None
            if form:
                rec["supply_form_sent"] = True
                rec["manual_takeover"] = True; rec["copilot_muted"] = True
                rec["stage"] = "SUPPLY_FORM_SENT"
                return {"type":"SEND_SUPPLY_FORM", "pn":pn, "notify":True, "supply":_supply,
                        "reason":"owner/supply side, " + _label + "; sent their intake form, engine silent hereafter",
                        "text": form}
            return {"type":"FLAG_HUMAN", "pn":pn, "notify":True,
                    "reason":"owner/supply side, " + _label + " (read across first messages); not a tenant or buyer", "text":None}
        # PHOTO with no text from a contact with no tenant profile = owner behaviour (Carousell
        # landlords open with unit photos; Song +6596479676 sat 6 days as a silent not_enquiry).
        # Flag LOUDLY once, never auto-send at a picture. A captioned photo now carries its
        # caption as text (bridge fix, 11 Aug 2026) and classifies normally above.
        if (str(ev.get("media_type") or "") in ("image", "video", "sticker", "location")
                and not (ev.get("text") or "").strip()
                and not rec.get("form_sent") and not rec.get("profile")
                and not rec.get("photo_flagged")):
            rec["photo_flagged"] = True; rec["status"] = "photo_no_text"
            return {"type":"FLAG_HUMAN", "pn":pn, "notify":True, "text":None,
                    "reason":"sent a photo/video with no text and has no tenant profile — could "
                             "be a landlord. Engine will not auto-send; check the chat."}
        # INTENT FIRST (before the tenant-enquiry gate): a buyer (sale) enquiry gets the BUYER
        # form (HDB asks HFE, private asks IPA), never the tenant form. Only a non-sale message
        # is then held to the "clear tenant enquiry" gate. Conversation-aware: an ambiguous line
        # is disambiguated from the thread + chat history, not just the single message.
        tx, txr = classify_intent(ev.get("jid"), ev.get("text",""), rec.get("listing_key"), rec)
        rec["transaction"] = tx
        if tx == "sale":
            # Send the buyer intake form ONCE. Do NOT mark form_sent (that is the tenant flag) so a
            # later genuine RENTAL enquiry from the same person still flows; a repeat stays silent.
            if rec.get("buyer_form_sent"):
                return None
            ptype = classify_property_type_ctx(ev.get("jid"), ev.get("text",""), rec.get("listing_key"))
            rec["buyer_form_sent"] = True
            rec["buyer_form_sent_ts"] = __import__("time").time()
            rec["stage"] = "BUYER_INTAKE"; rec["status"] = "buyer_intake:" + ptype
            text = buyer_form_for(ptype)
            # A listing with a fixed_viewing rule (same registry field rentals use) gets that
            # slot appended to the FIRST buyer message, own state (buyer_offered_slot_id) so it
            # never touches the rental viewing_asked/offered_slot_id machinery for this same
            # phone number. Buyer flow stays otherwise unchanged -- Winfred still coordinates the
            # actual viewing himself once the profile comes in (11 Jul 2026 silent-handoff rule).
            # Gated on the LISTING's own registry deal_type/status, not the classified intent: a
            # rental unit can still classify as tx=="sale" via an explicit portal SALE token
            # (classify_transaction checks that before the registry), which must never leak a
            # rental listing's fixed_viewing slot into a purchase intake message (backtest, 5 Aug
            # 2026). Same gate skips a hold/closed sale listing, so the bot never auto-commits a
            # buyer to a viewing for a property that is no longer available.
            lst = reqs.get(rec.get("listing_key"), {}) or {}
            lst_status = str(lst.get("status") or "").lower()
            slot = None
            if (rec.get("listing_key") and lst.get("deal_type") != "rent"
                    and not lst_status.startswith("closed") and lst_status != "hold"):
                slot = next_slot(rec.get("listing_key"))
            if slot:
                rec["buyer_offered_slot_id"] = slot.get("slot_id")
                rec["buyer_offered_slot_label"] = slot.get("label")
                text = text + "\n\nViewing: " + slot["label"] + ". Let me know if you'd like to come by."
            elif rec.get("listing_key"):
                # viewing-first for buyers too: no fixed slot -> ask for their window. Winfred
                # still runs the appointment himself (11 Jul 2026 silent-handoff rule); this
                # only collects the time, VIEWING_TIME_PROPOSED pings him with it.
                text = (text + "\n\nWhen are you free to view? Share a day and time and I "
                        "will line it up with the owner.")
            return {"type":"SEND_BUYER_FORM", "pn":pn, "text": text,
                    "listing_key": rec.get("listing_key"), "property_type": ptype,
                    "reason":"buyer enquiry (" + txr + ", " + ptype + "); sent buyer intake form"
                             + (" + fixed viewing slot" if slot else "")}
        # a message that arrived while we were deferred (contact DB locked) carries its
        # enquiry context forward: gate on the deferred text too, or "any update?" after
        # a deferral dead-ends a real prospect on a human flag.
        gate_text = ev.get("text","")
        if rec.get("deferred_text"):
            gate_text = gate_text + "\n" + rec["deferred_text"]
        if not is_tenant_enquiry(gate_text, rec.get("listing_key")):  # clear tenant enquiry only
            rec["status"] = "not_enquiry"
            # once-per-record latch: a burst of ambiguous messages pings Winfred once, not
            # once per message (12-message burst, replay 9 Sep 2026) -- but new high risk
            # content always breaks through regardless of the latch (P1 fix, 9 Sep 2026
            # cycle4 hg4-03).
            first = (not rec.get("not_enquiry_notified")
                     or _high_risk_escalate(rec, "not_enquiry", ev.get("text", "")))
            rec["not_enquiry_notified"] = True
            return {"type":"FLAG_HUMAN", "pn":pn, "notify": first,
                    "reason":"not a clear tenant enquiry", "text":None}
        rec.pop("deferred_text", None)           # served: the deferred context is spent
        # listing status gate: never auto-send the form for a listing that is closed
        # (tenanted) or on hold. The room is gone; flag to a human instead of intaking.
        lk0 = rec.get("listing_key")
        if lk0:
            lst0 = reqs.get(lk0, {})
            st0 = str(lst0.get("status", "")).lower()
            if st0.startswith("closed") or st0 == "hold":
                rec["status"] = "listing_" + (st0.split()[0] or "closed")
                # same content-aware override as the not_enquiry latch above (P1 fix, 9 Sep
                # 2026 cycle4 hg4-03: a legal-advice question and a landlord-name/number
                # fishing attempt both vanished behind an already-tripped closed-listing latch).
                first = (not rec.get("closed_listing_notified")
                         or _high_risk_escalate(rec, "closed_listing", ev.get("text", "")))
                rec["closed_listing_notified"] = True
                return {"type":"FLAG_HUMAN", "pn":pn, "notify": first,
                        "reason":"enquiry on a " + st0 + " listing (" + lk0 + "); room no longer available", "text":None}
        # service policy: if the opening message already reveals an excluded profile, do
        # not even send the form. Kind referral, once, no reason ever given.
        pol = policy_excluded(rec["profile"], ev.get("text",""), open_intake=_open_intake(reqs.get(lk0)))
        if pol:
            rec["form_sent"] = True; rec["terminal"] = True
            rec["stage"] = "POLICY_EXCLUDED"
            if pol == "nationality":   # A3: protected attribute -- neutral status, notify Winfred
                rec["status"] = _house_gate_status("nationality")
                return {"type":"REDIRECT", "pn":pn, "notify": True, "reason": _house_gate_status("nationality"),
                        "text":_redirect_text(pol, rec["profile"], reqs, lk0)}
            rec["status"] = "policy_excluded:" + pol
            return {"type":"REDIRECT", "pn":pn, "reason":pol,
                    "text":_redirect_text(pol, rec["profile"], reqs, lk0)}
        rec["form_sent"] = True
        rec["form_sent_ts"] = __import__("time").time()
        rec["stage"] = "FORM_SENT"; rec["status"] = "form_sent"
        # language pick, once, at first touch (Winfred, 11 Sep 2026): any CJK character in the
        # very first inbound (portal boilerplate riding along counts too, since it is itself
        # CJK when the source is Chinese) sends the Chinese form for the rest of this record.
        if rec.get("lang") is None:
            rec["lang"] = _detect_lang(rec.get("first_inbound_text"))
        lg = rec["lang"]
        lk = rec.get("listing_key")
        unit = listing_unit_message(lk, lg)
        # capture landlord availability at first enquiry: flag if this listing has no
        # upcoming viewing slot yet, so Winfred can grab the landlord's next slot.
        need_avail = bool(lk) and not _has_open_future_slot(lk)
        if missing_required(rec["profile"], reqs.get(lk)):
            # TWO messages, once only: (1) unit info, (2) the FULL intake form. ALWAYS the full
            # form (Winfred, 13 Jul 2026): the short open-intake form caused form-after-form
            # sequences and violated the standing full-form rule.
            _slotted = bool(lk) and _has_open_future_slot(lk)
            if lg == "zh":
                form = (VIEWING_TICKET_PREFIX_ZH + CHINESE_INTAKE_FORM) if (unit and _slotted) else CHINESE_INTAKE_FORM
            else:
                form = (VIEWING_TICKET_PREFIX + INTAKE_FORM) if (unit and _slotted) else INTAKE_FORM
            # the channel pitch rides the TAIL of the form send, never a standalone 3rd
            # message (Winfred, 11 Sep 2026 attack replay: first contact fired 3 auto sends
            # back to back -- unit info, form, then the channel plug -- before the prospect
            # had said anything beyond the portal enquiry; rubric caps first contact at 2).
            # Winfred still wants the "more than 30 rooms" line, so it is kept, just folded
            # into one send instead of its own message.
            form = form + "\n\n" + channel_pitch(lg)
            texts = [unit, form] if unit else [form]
            _act = {"type":"SEND_FORM", "pn":pn, "texts":texts, "text":texts[0],
                    "listing_key":lk, "capture_availability":need_avail}
            _pending_lease = rec.pop("pending_short_lease_notify", None)
            if _pending_lease:
                _act["notify"] = True
                _act["reason"] = ("also asked about a short lease alongside the enquiry (\""
                                  + _pending_lease[:100] + "\"); form sent, never auto answered")
            return _act
        # profile already complete (an earlier message supplied it, e.g. a pasted form riding
        # with the opener) -- never re-ask a blank 14-field template for data already on file
        # (P2 fix, 9 Sep 2026 attack replay). Send the unit intro (it already carries the slot
        # CTA) + channel pitch when a listing is bound; qualify()/OFFER_VIEWING then run as
        # usual on their next reply via the existing book-intent path below. Nothing bound yet
        # -> stay silent here, the unbound-profile flag fires on their next message instead.
        if not unit:
            return None
        _act = {"type":"SEND_FORM", "pn":pn, "texts":[unit, channel_pitch(lg)], "text":unit,
                "listing_key":lk, "capture_availability":need_avail}
        _pending_lease = rec.pop("pending_short_lease_notify", None)
        if _pending_lease:
            _act["notify"] = True
            _act["reason"] = ("also asked about a short lease alongside the enquiry (\""
                              + _pending_lease[:100] + "\"); form sent, never auto answered")
        return _act

    # SUPPLY RE-CHECK, post-form: "actually I am the landlord, help me rent out my room"
    # arriving AFTER the tenant form must flip the record to supply, not keep being chased
    # as a tenant (fuzz catch c41-60, 11 Aug 2026). Confident phrase match only.
    if rec.get("form_sent") and not rec.get("supply_flagged"):
        _sup2, _sup2_conf = supply_side_kind(ev.get("jid"), ev.get("text", ""),
                                             with_confidence=True, rec=rec)
        if _sup2 and _sup2_conf:
            rec["supply_flagged"] = True; rec["status"] = "supply_side:" + _sup2; rec["supply_kind"] = _sup2
            rec["manual_takeover"] = True; rec["copilot_muted"] = True
            rec["stage"] = "SUPPLY_FORM_SENT"
            form2 = _supply_form(_sup2)
            if form2:
                rec["supply_form_sent"] = True
                return {"type": "SEND_SUPPLY_FORM", "pn": pn, "notify": True, "supply": _sup2,
                        "reason": "owner/supply side detected mid-thread; sent their intake "
                                  "form, engine silent hereafter", "text": form2}
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                    "reason": "owner/supply side detected mid-thread; engine silent hereafter"}

    # STAGE 2: have form, not yet offered a viewing. Emit ONLY on a state change.
    if not rec["viewing_asked"] and not rec.get("terminal"):
        # PRE-VIEWING QUESTION CHECK: a tenant can ask a safety worry, a legal question, a
        # bot check, or a plain fact question at ANY point while the profile is still being
        # collected -- not just after a viewing is offered (that was _viewing_reaction's job
        # alone, so every one of these vanished with no reply and no flag until a viewing
        # existed to react from -- replay 9 Sep 2026). Skipped for anything that is itself
        # booking intent (an explicit yes, a proposed day/time) or that completes/advances
        # the profile -- those still flow through the normal paths below unchanged.
        _pretxt = ev.get("text") or ""
        _lk_pre = rec.get("listing_key")
        # a single incidental field (e.g. the free text solo signal "alone" inside "safe to
        # walk alone" tripping no_of_pax) must not silence a genuine question -- only skip
        # the pre-check for a message that is clearly ADVANCING the profile (2+ fields at
        # once, a real form reply).
        # P1 fix (9 Sep 2026 cycle4 c4rm03): this used to require _lk_pre (a bound listing)
        # before ANY of these checks ran, so protected-attribute fishing, authority
        # impersonation, and "call me"/"trying to call you" got zero reply AND zero flag on a
        # thread that never bound a listing. Run every check regardless of binding; only the
        # fact-answer lookup below genuinely needs a listing (it quotes the listing's own
        # data), so that alone stays gated on _lk_pre -- with no listing it falls straight to
        # FLAG_HUMAN, no tenant text.
        if (_is_engaged(_pretxt) and not _is_affirmative(_pretxt)
                and not _has_viewing_time(_pretxt.lower())
                and len(extract_profile(_pretxt)) < 2
                and missing_required(rec["profile"], reqs.get(_lk_pre) if _lk_pre else None)):
            _ans = (_tenant_fact_answer(_pretxt, reqs.get(_lk_pre) or {})
                    if (_lk_pre and not rec.get("fact_answered")) else None)
            if _ans:
                if _ans != _RENT_PIVOT_TEXT:
                    rec["fact_answered"] = True
                if _FACT_LEASE_RE.search(_pretxt or ""):
                    rec["lease_fact_told"] = True
                return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True,
                        "question": _pretxt, "text": _ans}
            # remember what this flagged question was ABOUT, so a re-ask for the same field
            # one turn later can soften its phrasing instead of reading as if it ignored
            # what they just said (P3 fix, 9 Sep 2026 cycle5 hg5-06).
            rec["last_flag_topic"] = "gender" if _GENDER_TOPIC_RE.search(_pretxt) else None
            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                    "reason": "question asked before the profile is complete; reply by hand"}
        # short-lease note pending/resolved is handled ONCE, at the top of this function
        # (fires regardless of stage) -- by the time we reach here it is either resolved
        # (fell through) or was never sent, so there is nothing left to interpret here.
        # VIEWING-FIRST (Winfred, 11 Aug 2026): booking intent (a YES to the message-1 CTA, or
        # any proposed day/time) is honoured the moment the landlord's HARD requirements pass —
        # qualify() only gates on fields the landlord actually rules on. The full form is
        # chased AFTER the slot is locked, not before: 389 prospects got the form and only 36
        # ever heard about a viewing under the old order.
        t_book = (ev.get("text") or "").lower()
        # a FORM reply's "Move in date: 1 Sep" is not a viewing time — suppress the time
        # trigger on form-style messages (the complete-profile path offers the viewing anyway)
        _formish = re.search(r"move.?in|email|pass type|no\.? of pax|employment|lease term|"
                             r"nationality|ethnicity|occupation|budget\s*:", t_book)
        # ...but an explicit view verb ("can i view tomorrow 7pm") IS intent, even when form
        # fields ride along in the same message
        _view_verb = re.search(r"\bview|come (?:by|down|over)|see the (?:room|unit|place)|drop by",
                               t_book)
        # DECLINE of the CTA: "no thanks" / "not keen" must never be answered with a viewing
        # offer — not by the booking path AND not by the stage-2 auto-offer below (cycle-14
        # catch, 11 Aug 2026). Explicit decline stems only; form replies are exempt ("No. of
        # pax" is data), a negation WITH a time is a reschedule and stays in, and questions
        # ("not sure, is aircon included?") flow to the question paths instead.
        _decline = (not _formish and not _has_viewing_time(t_book)
                    and "?" not in (ev.get("text") or "")
                    and not _is_affirmative(ev.get("text"))
                    and (len(t_book) <= 40 or re.search(r"\bview|\bslot\b|\bviewing\b", t_book))
                    and (re.search(r"\b(no thanks?|not keen|not interested|don'?t want|dont want|"
                                   r"no need|not looking|not proceeding|give (?:it|this) a miss|"
                                   r"pass on this)\b", t_book)
                         or re.fullmatch(r"\s*(no|nope|nah)[.! ]*", t_book)))
        # a "cannot make it" without a replacement time is a reschedule, not a decline
        if (not _formish and not _has_viewing_time(t_book)
                and re.search(r"\b(cannot|can'?t) make it\b", t_book)
                and not rec.get("resched_asked")):
            rec["resched_asked"] = True
            return {"type": "VIEWING_TIME_PROPOSED", "pn": pn, "notify": True,
                    "when": ev.get("text"),
                    "text": "No worries, which day and time would work better for you?"}
        if _decline:
            if not rec.get("cta_declined_flagged"):
                rec["cta_declined_flagged"] = True
                rec["status"] = "cta_declined"
                return {"type": "FLAG_HUMAN", "pn": pn, "notify": True,
                        "reason": "declined the viewing CTA (\"" + (ev.get("text") or "")[:60]
                                  + "\"); sent one closer, engine holding",
                        "text": "No worries \U0001F642 You can see my other available rooms "
                                "here:\n" + CHANNEL + "\nLet me know if anything catches your "
                                "eye and I will arrange a viewing."}
            return None
        # book intent PERSISTS: once they said yes and we asked for the hard fields, the
        # field reply itself books the slot — no second yes required
        if (_is_affirmative(ev.get("text"))
                or (_has_viewing_time(t_book) and (not _formish or _view_verb))
                or rec.get("book_intent_asked")):
            lk_b = rec.get("listing_key"); listing_b = reqs.get(lk_b)
            if listing_b and not _listing_unavailable(lk_b, reqs):
                # the booking shortcut must respect the SAME service policy as the full path
                # (adversarial-review P0-2): a policy-excluded profile never books
                pol_b = policy_excluded(rec["profile"], ev.get("text", ""),
                                        open_intake=_open_intake(listing_b))
                if pol_b:
                    rec["terminal"] = True; rec["stage"] = "POLICY_EXCLUDED"
                    if pol_b == "nationality":   # A3: neutral status, notify Winfred
                        rec["status"] = _house_gate_status("nationality")
                        return {"type": "REDIRECT", "pn": pn, "notify": True,
                                "reason": _house_gate_status("nationality"),
                                "text": _redirect_text(pol_b, rec["profile"], reqs, lk_b)}
                    rec["status"] = "policy_excluded:" + pol_b
                    return {"type": "REDIRECT", "pn": pn, "reason": pol_b,
                            "text": _redirect_text(pol_b, rec["profile"], reqs, lk_b)}
                v_b, why_b = qualify(listing_b, rec["profile"])
                if v_b == "QUALIFIED":
                    _gu_block = _gate_unverified_offer_block(pn, rec, listing_b)
                    if _gu_block:
                        return _gu_block
                    rec["viewing_asked"] = True
                    rec["stage"] = "VIEWING_OFFERED"; rec["status"] = "viewing_offered"
                    slot_b = next_slot(lk_b)
                    rec["offered_slot_id"] = slot_b.get("slot_id") if slot_b else None
                    rec["offered_slot_label"] = slot_b.get("label") if slot_b else None
                    act_q = _viewing_reaction(rec, ev, pn)
                    if act_q and act_q.get("type") == "ANSWER_QUESTION":
                        # question answered by hand first — no phantom offer left latched
                        # (adversarial-review P2-9)
                        rec["viewing_asked"] = False
                        rec["stage"] = "FORM_SENT"; rec["status"] = "form_sent"
                        rec["offered_slot_id"] = None; rec["offered_slot_label"] = None
                        return act_q
                    return act_q or \
                           {"type":"OFFER_VIEWING", "pn":pn, "slot":slot_b, "listing_key":lk_b,
                            "slot_id":rec["offered_slot_id"], "text":_viewing_text(slot_b, _lang(rec))}
                if v_b == "DISQUALIFIED":
                    # never book a profile the landlord would reject — kind referral as usual
                    _attr_b = _protected_attr_from_why(why_b, listing_b)
                    if _attr_b:
                        return _house_gate_redirect(pn, rec, listing_b, reqs, lk_b, _attr_b, why_b)
                    rec["terminal"] = True; rec["stage"] = "DISQUALIFIED"; rec["status"] = "disqualified"
                    return {"type":"REDIRECT", "pn":pn, "reason":why_b,
                            "text":_redirect_text(why_b, rec["profile"], reqs, lk_b)}
                if v_b == "NEEDS_INFO":
                    # split the gaps: things the PROSPECT can answer vs listing-side unknowns
                    # ("listing rent not confirmed") they cannot — 4 of 6 live listings have no
                    # budget_floor, and asking a tenant to reply with "listing rent not
                    # confirmed" kills every YES (judge catch, 11 Aug 2026)
                    _askable, _sensitive, _listing_only = _split_needs_info(why_b)
                    if _listing_only:
                        # only listing-side gaps -> book anyway, tell Winfred to settle the rent
                        rec["viewing_asked"] = True
                        rec["stage"] = "VIEWING_OFFERED"; rec["status"] = "viewing_offered"
                        slot_b = next_slot(lk_b)
                        rec["offered_slot_id"] = slot_b.get("slot_id") if slot_b else None
                        rec["offered_slot_label"] = slot_b.get("label") if slot_b else None
                        act_b = _viewing_reaction(rec, ev, pn)
                        if act_b and act_b.get("type") == "ANSWER_QUESTION":
                            rec["viewing_asked"] = False
                            rec["stage"] = "FORM_SENT"; rec["status"] = "form_sent"
                            rec["offered_slot_id"] = None; rec["offered_slot_label"] = None
                            return act_b
                        act_b = act_b or \
                               {"type":"OFFER_VIEWING", "pn":pn, "slot":slot_b, "listing_key":lk_b,
                                "slot_id":rec["offered_slot_id"], "text":_viewing_text(slot_b, _lang(rec))}
                        act_b["notify"] = True
                        act_b["reason"] = ((act_b.get("reason") or "") +
                                           " [listing gap: " + "; ".join(map(str, why_b))
                                           + " — confirm with the landlord]").strip()
                        return act_b
                    # share ONE "already asked this gap" latch with the NEEDS_INFO branch
                    # further down (needs_info_unknowns) -- book intent used to have its own
                    # separate book_intent_asked flag, so the identical question ("Can I just
                    # check your budget?") went out TWICE for the same gap (replay 9 Sep
                    # 2026). Skip the repeat unless they restated a number (a real answer).
                    _repeat_ask = (rec.get("needs_info_unknowns") == why_b
                                   and not re.search(r"\d", ev.get("text") or ""))
                    if not rec.get("book_intent_asked") and not _repeat_ask:
                        rec["book_intent_asked"] = True
                        rec["needs_info_unknowns"] = why_b
                        rec["stage"] = "BOOK_INTENT"; rec["status"] = "book_intent_hard_fields"
                        txt_b = _ask_one_text(why_b, _askable, _sensitive,
                                              rec.get("last_flag_topic"))
                        return {"type":"ASK_ONE", "pn":pn, "reason":why_b, "text":txt_b}
                # SHORT_LEASE (and a repeat NEEDS_INFO) fall through to the standard chase
        miss = missing_required(rec["profile"], reqs.get(rec.get("listing_key")))
        if miss:
            # GRACE PERIOD: never nudge within 3 minutes of the form going out — a message
            # that arrived alongside the enquiry must not trigger an instant "Almost there"
            # (real case: form + nudge 5 seconds apart read as two forms back to back).
            import time as _t
            if rec.get("form_sent_ts") and _t.time() - rec["form_sent_ts"] < 180:
                return None
            # nudge the prospect ONCE with the fields still missing (recovers partial fillers and
            # people who replied without using the form), then go silent. Anti-spam: one nudge.
            # BUT: if they keep engaging (a question or a viewing ask) while we are silent,
            # flag Winfred ONCE — a keen tenant must dead-end on a human, not on silence.
            if rec.get("nudged_incomplete"):
                txt_now = ev.get("text") or ""
                if (("?" in txt_now or _ENGAGEMENT_INTENT_RE.search(txt_now)
                        or _has_viewing_time(txt_now.lower()))
                        and not rec.get("incomplete_flagged")):
                    rec["incomplete_flagged"] = True
                    return {"type": "FLAG_HUMAN", "pn": pn, "text": None, "notify": True,
                            "reason": "keen but profile still incomplete (missing "
                                      + ", ".join(miss) + "); engine is silent post nudge, reply by hand"}
                return None
            rec["nudged_incomplete"] = True
            rec["stage"] = "PROFILE_PENDING"; rec["status"] = "incomplete"
            return {"type":"NUDGE_INCOMPLETE", "pn":pn,
                    "reason":"incomplete profile, missing " + ", ".join(miss),
                    "text":_nudge_text(miss, _lang(rec))}
        # profile complete
        # service policy: never match a profile the landlords will not take. Kind referral, once.
        pol = policy_excluded(rec["profile"], rec.get("last_inbound",""), open_intake=_open_intake(reqs.get(rec.get("listing_key"))))
        if pol:
            rec["terminal"] = True; rec["stage"] = "POLICY_EXCLUDED"
            if pol == "nationality":   # A3: neutral status, notify Winfred
                rec["status"] = _house_gate_status("nationality")
                return {"type":"REDIRECT", "pn":pn, "notify": True, "reason": _house_gate_status("nationality"),
                        "text":_redirect_text(pol, rec["profile"], reqs, rec.get("listing_key"))}
            rec["status"] = "policy_excluded:" + pol
            return {"type":"REDIRECT", "pn":pn, "reason":pol,
                    "text":_redirect_text(pol, rec["profile"], reqs, rec.get("listing_key"))}
        lk = rec.get("listing_key")
        listing = reqs.get(lk)
        if not listing:
            # unbound complete profile: screen against every OPEN listing instead of dead
            # ending. A single confident match that the tenant (or Winfred, or the automation
            # ack) actually named gets bound and falls through to the normal qualify path;
            # anything else is a human call, flagged once per distinct match signature so a
            # newly opened listing that now fits can re fire the flag.
            matches = [m for m in hot_matches(rec["profile"], exclude_key=None)
                       if not _listing_unavailable(m, reqs)]
            mention_blob = " ".join(filter(None, [ev.get("text"), rec.get("last_inbound"),
                                                   rec.get("last_outbound")]))
            if len(matches) == 1 and _text_mentions_listing(mention_blob, matches[0], reqs):
                rec["listing_key"] = lk = matches[0]
                rec["listing_key_source"] = "hotmatch"   # engine guess, never tenant named
                listing = reqs.get(lk)
                rec.pop("unbound_sig", None)
                rec["flagged_human"] = False
                # fall through to the normal qualify path below, now that lk/listing are bound
            else:
                sig = "UNBOUND|" + ",".join(sorted(matches))
                if rec.get("unbound_sig") == sig:
                    # same repeat enquiry on the same unbound profile -- already flagged once
                    # for this exact situation. But fresh ACTIONABLE content (a proposed time,
                    # a figure, a genuine question) must still reach Winfred once each, rather
                    # than being swallowed along with the genuine repeats (replay 9 Sep 2026: a
                    # viewing time, a policy question and a rent offer all vanished this way).
                    _txt_now = ev.get("text") or ""
                    _fresh = (_has_viewing_time(_txt_now.lower()) or _is_question(_txt_now)
                              or re.search(r"\d{3,5}", _txt_now))
                    if _fresh:
                        _sig2 = re.sub(r"\s+", " ", _txt_now.strip().lower())
                        _seen2 = rec.get("unbound_notify_sigs") or []
                        if _sig2 and _sig2 not in _seen2:
                            rec["unbound_notify_sigs"] = (_seen2 + [_sig2])[-20:]
                            return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                                    "reason": "fresh message on an unbound profile: \""
                                              + _txt_now[:100] + "\" | profile: "
                                              + _profile_summary(rec["profile"])}
                    return None
                rec["unbound_sig"] = sig; rec["status"] = "needs_listing"
                # seed the fresh-content dedup with THIS text too, so an exact repeat of the
                # very message that triggered this flag never re-notifies on its own.
                rec["unbound_notify_sigs"] = [re.sub(r"\s+", " ", (ev.get("text") or "").strip().lower())]
                reason = (("possible listings (best guess off the profile, not confirmed by "
                           "name): " + ", ".join(matches)) if matches
                          else "no open listing fits") + " | profile: " + _profile_summary(rec["profile"])
                return {"type":"FLAG_HUMAN", "pn":pn, "notify":True, "text":None,
                        "reason":reason, "hot_matches":matches}
        verdict, why = qualify(listing, rec["profile"])
        rec["qualify"] = {"verdict":verdict, "why":why}
        if verdict == "DISQUALIFIED":
            _attr = _protected_attr_from_why(why, listing)
            if _attr:
                return _house_gate_redirect(pn, rec, listing, reqs, lk, _attr, why)
            if _disqualify_unsourced(rec, why) and not rec.get("unsourced_disqualify_flagged"):
                rec["unsourced_disqualify_flagged"] = True
                return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                        "reason": "would DISQUALIFY (" + "; ".join(why) + ") but the deciding "
                                  "field is only sourced from incidental free text; verify by "
                                  "hand: " + _profile_summary(rec["profile"])}
            rec["terminal"] = True; rec["stage"] = "DISQUALIFIED"; rec["status"] = "disqualified"
            return {"type":"REDIRECT", "pn":pn, "reason":why,
                    "text":_redirect_text(why, rec["profile"], reqs, lk)}
        if verdict == "SHORT_LEASE":
            if rec.get("lease_note_sent"): return None      # one note only
            rec["lease_note_min"] = 12
            rec["lease_note_sent"] = True
            if rec.get("lease_fact_told"):
                # already stated the minimum once as a direct fact answer on this chat --
                # never restate it (lease-line-repeated-twice); the profile is now complete
                # and still under the floor, so flag it for a human decision instead.
                rec["lease_note_resolved"] = True
                rec["status"] = "short_lease_note"
                return {"type": "FLAG_HUMAN", "pn": pn, "notify": True, "text": None,
                        "reason": "profile confirms a lease under the minimum (already told "
                                  "once); reply by hand"}
            rec["stage"] = "LEASE_NOTE"; rec["status"] = "short_lease_note"
            return {"type": "LEASE_NOTE", "pn": pn, "notify": False, "reason": (why or [""])[0],
                    "text": _lease_note_text(_lang(rec))}
        _listing_gap = None
        if verdict == "NEEDS_INFO":
            if rec.get("needs_info_unknowns") == why:
                return None              # same gap already asked -> silent
            askable, sensitive, listing_only = _split_needs_info(why)
            rec["needs_info_unknowns"] = why
            if listing_only:
                # every reason is a listing-side unknown ("listing rent not confirmed") that
                # the prospect cannot answer — never surface that internal gap as ASK_ONE
                # copy (judge catch: a tenant getting "Almost there. listing rent not
                # confirmed." reads as a broken bot). Fall through to the QUALIFIED flow
                # below (book the viewing) and flag Winfred with the gap instead.
                _listing_gap = why
            else:
                rec["stage"] = "NEEDS_INFO"; rec["status"] = "needs_info"
                txt = _ask_one_text(why, askable, sensitive, rec.get("last_flag_topic"))
                return {"type":"ASK_ONE", "pn":pn, "reason":why, "text":txt}
        # QUALIFIED (or a listing-only NEEDS_INFO gap, booked anyway) -> offer the viewing
        # once. Re-check the listing is STILL open first: it can have closed since the
        # Stage-1 gate (tenanted mid-conversation).
        st_now = _listing_unavailable(lk, reqs)
        if st_now:
            return _room_gone_action(rec, pn, st_now)
        if verdict == "QUALIFIED":
            _gu_block = _gate_unverified_offer_block(pn, rec, listing)
            if _gu_block:
                return _gu_block
        # a QUESTION rides ahead of the auto-offer: answer it first (by hand), the offer
        # fires on their next message — never reply to "how much is this one?" with
        # "Reply YES to take this slot" (cycle-17 catch, 11 Aug 2026)
        if "?" in (ev.get("text") or ""):
            return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True,
                    "question": ev.get("text"), "text": None}
        rec["viewing_asked"] = True
        rec["stage"] = "VIEWING_OFFERED"; rec["status"] = "viewing_offered"
        slot = next_slot(lk)
        rec["offered_slot_id"] = slot.get("slot_id") if slot else None
        rec["offered_slot_label"] = slot.get("label") if slot else None
        act = {"type":"OFFER_VIEWING", "pn":pn, "slot":slot, "listing_key":lk,
               "slot_id":rec["offered_slot_id"], "text":_viewing_text(slot, _lang(rec)),
               "hot_matches": hot_matches(rec["profile"], exclude_key=lk)}
        if _listing_gap:
            act["notify"] = True
            act["reason"] = ("[listing gap: " + "; ".join(map(str, _listing_gap))
                              + " — confirm with the landlord]")
        return act

    # STAGE 3: viewing offered -> react to one reply per inbound (shared with the manual co-pilot path)
    return _viewing_reaction(rec, ev, pn)

# a bracketed media duration caption ("[Voice message, 0:42]", "[Video, 0:30]") is metadata
# about the clip length, never a time the sender is proposing (P1 fix, 9 Sep 2026 cycle5
# c5ec05: a voice note misread as VIEWING_TIME_PROPOSED off its own duration).
_MEDIA_DURATION_RE = re.compile(
    r"\[\s*(?:voice\s*(?:message|note)|video|audio)\s*,?\s*\d{1,2}[:.]\d{2}\s*\]", re.I)

# words that turn a day/time token into an unrelated activity, not a viewing proposal
# ("sat exam", "weekend job") -- the near miss that sent chat 6580900266's "can I view it
# tonight?" to a text-less ANSWER_QUESTION was the same class of gap in reverse (a real
# time word the old regex just did not know), so this list is checked from both directions.
_NOT_A_VIEWING_TIME = r"(?!\s+(?:job|exam|shift|duty|class|meeting|interview|test|practice))"
# a day/time word followed by a travel departure verb is the tenant leaving, not proposing
# a slot ("tonight I fly") -- narrow on purpose, only the verbs actually seen in the wild
_NOT_A_DEPARTURE = r"(?!\s+i\W*(?:m\s+)?(?:fly|flying|leave|leaving|depart|departing))"

def _has_viewing_time(t):
    """True if the prospect's reply names a day or a time to view (9 Sep 2026: also a bare
    immediacy word like "tonight"/"now" -- these used to fall through to the '?' branch as a
    plain question, so a prospect asking to view that same day got flagged with no reply)."""
    if not t: return False
    # a form shaped message (3+ recognised field labels, e.g. a RESENT copy of the intake
    # form) is never a proposed viewing date, even though "Move in date: 1 Oct" trips the
    # month/day pattern below -- that "1 Oct" is a form field, not a date they are offering
    # (replay 9 Sep 2026: a duplicate pasted form got read as VIEWING_TIME_PROPOSED).
    if len(_FORM_LABEL_COLON_RE.findall(t)) >= 3:
        return False
    t = t.lower()
    t2 = _MEDIA_DURATION_RE.sub(" ", t)
    has_ampm = bool(re.search(r"\b\d{1,2}\s*(?:am|pm)\b|\bnoon\b|after\s*\d"
                         r"|\bright\s+now\b|\b(?:come|view|check|see)\s+now\b"
                         r"|\bnow\b(?=\s*[?!.,]|$)", t2))
    has_colon = bool(re.search(r"\b\d{1,2}[:.]\d{2}\b", t2))
    has_day  = bool(re.search(r"\b(?:mon(?:day)?|tues?(?:day)?|wed(?:nesday)?|thur?s?(?:day)?|"
                         r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|today|tomorrow|tmr|"
                         r"weekends?)\b" + _NOT_A_VIEWING_TIME +
                         r"|\btonight\b" + _NOT_A_DEPARTURE +
                         r"|\bthis\s+evening\b|\bthis\s+afternoon\b"
                         r"|今晚|明天|周末"       # 今晚 / 明天 / 周末
                         r"|\b\d{1,2}\s*/\s*\d{1,2}\b"
                         r"|\b\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t2))
    # a bare h:mm with no am/pm is ambiguous on its own (a media duration, a random number) --
    # only counts as a real time when a day word rides along too.
    has_time = has_ampm or (has_colon and has_day)
    return bool(has_time or has_day)

_PROPOSED_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_MON_ABBR_LOW = [m.lower() for m in _MON_ABBR]

def _proposed_date_in_past(text):
    """Best effort: True only when the tenant named an explicit day+month ("3 Sep") that
    already resolves to a calendar date before today (SGT), current year. A bare weekday
    ("Monday") or relative day ("tomorrow") is never flagged -- those always mean the next
    occurrence, never a date that has already gone by."""
    m = _PROPOSED_DAY_MONTH_RE.search(text or "")
    if not m:
        return False
    import datetime
    day = int(m.group(1))
    try:
        mon = _MON_ABBR_LOW.index(m.group(2).lower()[:3]) + 1
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        proposed = datetime.date(now.year, mon, day)
    except (ValueError, IndexError):
        return False
    return proposed < now.date()

# ---------- tenant-facing copy (no hyphens or dashes) ----------
def _ask_text(fields):
    label = {"name":"your name","nationality":"your nationality","ethnicity":"your ethnicity",
             "gender":"your gender","age":"your age","pass_type":"your pass type",
             "no_of_pax":"how many people will stay","move_in_date":"your move in date",
             "lease_term_months":"your preferred lease term","budget":"your monthly budget"}
    asks = ", ".join(label.get(f,f) for f in fields)
    return "Thanks. Just need a couple more details to send to the landlord: " + asks + "."

_NUDGE_LABELS = {"name":"name","nationality":"nationality","ethnicity":"ethnicity",
                 "gender":"gender","age":"age","pass_type":"type of pass (PR/EP/S Pass/SC etc)",
                 "no_of_pax":"number of people staying","move_in_date":"move in date",
                 "lease_term_months":"preferred lease term","budget":"monthly budget (S$)",
                 "preferred_location":"preferred location"}
_NUDGE_LABELS_ZH = {"name":"姓名","nationality":"国籍","ethnicity":"种族","gender":"性别",
                    "age":"年龄","pass_type":"准证类型（PR/EP/S Pass/SC等）",
                    "no_of_pax":"入住人数","move_in_date":"入住日期",
                    "lease_term_months":"租期","budget":"预算","preferred_location":"首选地点"}
def _join_and(items):
    """Natural list join ('a', 'a and b', 'a, b and c') so the nudge reads like a person
    wrote it rather than a form validator."""
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return items[0] if len(items) == 1 else " and ".join([", ".join(items[:-1]), items[-1]])

# fixed leading clause of the Chinese nudge, registered verbatim in _ENGINE_PREFIXES /
# BOT_SIGNATURES / _OUTBOUND_ONLY -- the missing fields list trails it, never sits inside it,
# so the prefix match still works no matter which fields are missing.
_NUDGE_ZH_PREFIX = "你好 :) 谢谢您提供的资料。在把您的资料发给房东之前，可以请您告诉我以下资料："

def _nudge_text(miss, lang="en"):
    if lang == "zh":
        fields = "、".join(_NUDGE_LABELS_ZH.get(f, f) for f in miss)
        return _NUDGE_ZH_PREFIX + fields + "。我拿到后会马上帮您跟房东确认。"
    fields = _join_and([_NUDGE_LABELS.get(f, f) for f in miss])
    return ("Hi :) thanks for the details so far. Before I can send your profile over to the "
            "landlord, could you also share your " + fields +
            "? Once I have that I will check with the owner for you.")

def _needs_info_text(why):
    return "Almost there. " + "; ".join(why) + ". Could you confirm this so I can send your profile to the landlord?"

CHANNEL = "https://whatsapp.com/channel/0029VbCoWRs4inomDhoAAv0G"

# Sent as its OWN message right after the tenant intake form (Winfred, 18 Aug 2026, reworded
# 11 Sep 2026 to name the room count and ask for location + budget up front) — separate so
# the link keeps its WhatsApp preview instead of being buried under 14 fields. Its opening
# words are registered in _ENGINE_PREFIXES / BOT_SIGNATURES / the runner's _OUTBOUND_ONLY, or
# the echoed send would read as a manual reply and mute the engine.
CHANNEL_PITCH_EN_30 = ("I have more than 30 rooms available on my channel \U0001F642 Do let "
                       "me know your preferred location and budget as much as possible so I "
                       "can recommend the right room for you. " + CHANNEL)
CHANNEL_PITCH_EN_MANY = ("I have many rooms available on my channel \U0001F642 Do let me "
                         "know your preferred location and budget as much as possible so I "
                         "can recommend the right room for you. " + CHANNEL)
CHANNEL_PITCH_ZH_30 = ("我的频道里有超过30间房间可供选择 \U0001F642 请尽量告诉我您的首选地点"
                       "和预算，方便我为您推荐合适的房间。" + CHANNEL)
CHANNEL_PITCH_ZH_MANY = ("我的频道里有很多房间可供选择 \U0001F642 请尽量告诉我您的首选地点"
                         "和预算，方便我为您推荐合适的房间。" + CHANNEL)
# kept as the default constant (also what every pre existing test/import references) -- the
# >=30 open listing count is the normal case, so this stays the "more than 30" wording.
CHANNEL_PITCH = CHANNEL_PITCH_EN_30

def _open_listing_count():
    """Rooms currently open (not closed/hold) across the whole listing index -- the guard
    for the 30 rooms claim (Winfred, 11 Sep 2026): never say "more than 30" when it is not
    actually true right now."""
    n = 0
    for l in (listing_reqs() or {}).values():
        st = str(l.get("status") or "").lower()
        if not st.startswith("closed") and st != "hold":
            n += 1
    return n

def channel_pitch(lang="en"):
    thirty_plus = _open_listing_count() >= 30
    if lang == "zh":
        return CHANNEL_PITCH_ZH_30 if thirty_plus else CHANNEL_PITCH_ZH_MANY
    return CHANNEL_PITCH_EN_30 if thirty_plus else CHANNEL_PITCH_EN_MANY

_UNIT_REJECT = ("don't like","dont like","didn't like","didnt like","not suitable","too small",
                "too far","too old","not keen on this","not for me","give it a miss","give this a miss",
                "pass on this","not what i am looking","not what i'm looking","don't think this",
                "dont think this","prefer something else","looking for something else")
_UNIT_REJECT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m).replace(r"\ ", r"\s+") for m in _UNIT_REJECT) + r")\b", re.I)
# a rejection phrase sitting inside a hypothetical ("if I don't like it", "in case I don't
# like it") is not a rejection at all -- it is a condition on an ALREADY confirmed viewing
# (P0 fix, 9 Sep 2026 attack replay: "coming around 3pm... if I don't like it" silently
# killed a just-confirmed viewing with no flag to Winfred).
_UNIT_REJECT_HYPOTHETICAL_RE = re.compile(
    r"\b(?:if|unless|in\s+case)\b[^.!?]{0,40}\b(?:" +
    "|".join(re.escape(m).replace(r"\ ", r"\s+") for m in _UNIT_REJECT) + r")\b", re.I)
# a rejection phrase attributed to a THIRD PARTY ("some landlords here don't like", "the
# owner said not suitable") is a report of someone ELSE's preference, never the tenant
# rejecting the unit themselves (P0 fix, 9 Sep 2026 cycle5 c5rm04).
_UNIT_REJECT_THIRDPARTY_RE = re.compile(
    r"\b(?:landlords?|owners?|some\s+people|they|them|agents?)\b[^.!?]{0,40}\b(?:" +
    "|".join(re.escape(m).replace(r"\ ", r"\s+") for m in _UNIT_REJECT) + r")\b", re.I)

def _unit_rejection(text):
    """True when the prospect rejects THIS unit but is still renting (withdrawal_signal is
    checked first by the caller, so 'found a place' style closes never reach here). Vetoed
    when the phrase sits in a hypothetical clause, is attributed to a third party (the
    landlord's/some people's preference, not the tenant's own), or the same message also
    carries booking positive intent (a time, "coming", "see you") -- all of these mean this
    is not the tenant rejecting the unit."""
    t = text or ""
    if not _UNIT_REJECT_RE.search(t):
        return False
    if _UNIT_REJECT_HYPOTHETICAL_RE.search(t):
        return False
    if _UNIT_REJECT_THIRDPARTY_RE.search(t):
        return False
    low = t.lower()
    if _has_viewing_time(low) or re.search(r"\bcoming\b|\bsee\s+you\b", low):
        return False
    return True

# self disclosing a protected attribute AND asking whether it is acceptable must never be
# read as a unit rejection just because a third party's preference phrase rides along in
# the same breath ("some landlords here don't like") -- route to Winfred instead, no
# tenant facing text (P0 fix, 9 Sep 2026 cycle5 c5rm04).
_PROTECTED_SELF_DISCLOSE_RE = re.compile(
    r"\bi'?m\s+(?:an?\s+)?(?:chinese|malay|indian|eurasian|caucasian|filipino|filipina|"
    r"burmese|vietnamese|bangladeshi|myanmar(?:ese)?|african|nigerian|pakistani|"
    r"sri\s*lankan)\b|"
    r"\bi\s+am\s+(?:an?\s+)?(?:chinese|malay|indian|eurasian|caucasian|filipino|filipina|"
    r"burmese|vietnamese|bangladeshi|myanmar(?:ese)?|african|nigerian|pakistani|"
    r"sri\s*lankan)\b", re.I)
_GENDER_TOPIC_RE = re.compile(
    r"\bgender\b|\bladies\b|\bmen\s+only\b|\bwomen\s+only\b|\bmale\b|\bfemale\b|\bguy\b|"
    r"\bgirl\b|\bboy\b|\bfeminine\b|\bmasculine\b", re.I)
_ACCEPTABLE_CHECK_RE = re.compile(
    r"\bis\s+(?:that|this|it)\s+(?:ok(?:ay)?|fine|alright|acceptable|a\s+problem|an?\s+issue)\b|"
    r"\bwill\s+that\s+be\s+(?:ok(?:ay)?|fine|a\s+problem|an?\s+issue)\b|"
    r"\b(?:does|will)\s+the\s+landlord\s+mind\b", re.I)

def _protected_disclosure_question(text):
    """True when the same message self discloses a protected attribute AND asks whether it
    is acceptable -- must be flagged to Winfred, never closed as a unit rejection."""
    t = text or ""
    return bool(_PROTECTED_SELF_DISCLOSE_RE.search(t) and _ACCEPTABLE_CHECK_RE.search(t))

def suggest_alternative(profile, exclude_key):
    """Cross sell: the best OTHER active listing in the same district as the rejected or
    disqualified listing (or matching the tenant's stated preferred location), that the
    profile is not disqualified for and that has a unit post to send.
    Returns (listing_key, text) or None. The text carries the unit brief, the next viewing
    slot line (via listing_unit_message) and the portal listing link when one exists."""
    if _landlord_by_id() is None:
        return None       # master DB unreadable -> suggest nothing (fail closed on new exposure)
    reqs = listing_reqs()
    base = reqs.get(exclude_key, {}) or {}
    dist = base.get("district")
    pref = (profile.get("preferred_location") or "").lower()
    best = None
    for k, l in reqs.items():
        if k == exclude_key or l.get("status") != "active":
            continue
        # VERIFY AGAINST THE MASTER DATABASE SHEET before suggesting a unit to a tenant
        # (Winfred, 13 Jul 2026): a suggestion is new exposure, so it is held to a stricter
        # bar than the index alone — the linked landlord record must itself be active
        # (not closed/tenanted/stalled/dormant/archived). An unlinked listing keeps the
        # index-only behavior (the index is then the only source there is).
        ms = _master_status(k, reqs)
        if ms is not None and not ms.startswith("active"):
            continue
        same_dist = bool(dist) and l.get("district") == dist
        area_words = [w.strip().lower() for w in str(l.get("area","")).split(",") if w.strip()]
        area_hit = bool(pref) and any(w in pref for w in area_words)
        if not (same_dist or area_hit):
            continue
        v, _why = qualify(l, profile)
        if v == "DISQUALIFIED":
            continue
        unit = listing_unit_message(k)
        if not unit:
            continue
        score = ((0 if v == "QUALIFIED" else 1), (0 if _has_open_future_slot(k) else 1))
        if best is None or score < best[0]:
            best = (score, k, unit, l)
    if not best:
        return None
    _score, k, unit, l = best
    url = l.get("portal_url")
    return k, unit + (("\n\nFull listing: " + url) if url else "")

def _redirect_text(why, profile, reqs, exclude_key=None):
    # never reveal the reason or any protected attribute. kind note + channel referral,
    # upgraded with a concrete same-district alternative (unit post + next slot) when one fits.
    alt = suggest_alternative(profile or {}, exclude_key) if exclude_key else None
    if alt:
        _k, alt_text = alt
        return ("Thanks for sending this :) So sorry, your profile is not a fit for this unit 🙏\n"
                "But I have another room nearby that may suit you:\n\n" + alt_text + "\n\n"
                "Keen to take a look? More options here too: " + CHANNEL)
    return ("Thanks for sending this :) So sorry, your profile is not a fit for this unit 🙏\n"
            "You can take a look at my other available room rentals here:\n" + CHANNEL + "\n"
            "Did anything catch your eye? Let me know and I'll arrange a viewing for you 🙂")


# a viewing slot on every open listing (11 Sep 2026): 96.6% of prospects confirm a named day
# and time vs 30.7% for an open ask (Winfred's data), so a listing with no captured slot must
# never fall back to the weak "When are you able to view?" ask -- it holds the prospect and
# lets Winfred (or a landlord-side /slot) supply the real time instead of the bot guessing one.
VIEWING_HOLD_TEXT = ("Thanks, you fit what the landlord is looking for. I will send your "
                     "profile over now and confirm a viewing time with the owner shortly \U0001F642")

# fixed lead clause of the Chinese variant -- registered verbatim in _ENGINE_PREFIXES /
# BOT_SIGNATURES / _OUTBOUND_ONLY, so both the offer (slot) and hold (no slot) sends below
# share this exact prefix and are recognised by the echo/takeover matchers either way.
VIEWING_LEAD_ZH = "谢谢，您的条件符合房东的要求。我现在就把您的资料发给房东。"
# Chinese counterpart to VIEWING_HOLD_TEXT: same "no open slot yet" hold, never the open
# "when are you free" ask (11 Sep 2026 slot-on-every-listing change applies to both languages).
VIEWING_HOLD_TEXT_ZH = VIEWING_LEAD_ZH + "我会尽快和房东确认看房时间 \U0001F642"

def _viewing_text(slot, lang="en"):
    if lang == "zh":
        # fixed lead clause first, slot (if any) trails it -- same prefix matching reason as
        # _viewing_cta() above. Registered in _ENGINE_PREFIXES / BOT_SIGNATURES / _OUTBOUND_ONLY.
        if slot:
            return VIEWING_LEAD_ZH + "下一场看房时间是 " + slot["label"] + "。回复YES确认这个时间。"
        return VIEWING_HOLD_TEXT_ZH
    if slot:
        return "Thanks, you fit what the landlord is looking for. I will send your profile over now. " \
               "The next viewing is " + slot["label"] + ". Reply YES to take this slot."
    return VIEWING_HOLD_TEXT

# ========== LANDLORD FOLLOW-UP SEQUENCES ==========
# Extension: automatic follow-ups for landlords who have received the supply form.
# Day 0: Form sent (handled by normal flow)
# Day 3: Nudge if form incomplete
# Day 5: Request photos if form complete but no media
# Day 7: Final check-in if still silent

LANDLORD_SUPPLY_FORM = (
    "Hi, could you help me answer these questions so I can screen tenants before bringing them to you. I don't want to waste your time with the wrong profile 🙏\n\n"
    "Property\n"
    "• Owner name:\n"
    "• Address, unit type and size:\n"
    "• Nearest MRT and walking distance:\n"
    "• Available from (and is it vacant now?):\n"
    "• Which rooms are available now:\n"
    "• Sole owner, or jointly owned?:\n"
    "• Is the unit mortgaged (bank notification needed?):\n"
    "• HDB: is MOP met? Whole flat or room rental?:\n\n"
    "Rental Terms\n"
    "• Asking rent and flexibility:\n"
    "• Preferred lease duration (long term or short term?):\n"
    "• Deposit or upfront rent before moving in?:\n"
    "• Rent payment method and date:\n\n"
    "Unit and Bills\n"
    "• Furnishing (unfurnished, semi, or fully, and what is included?):\n"
    "• Utilities included or excluded? (electricity, water, gas, WiFi):\n"
    "• Aircon servicing, landlord or tenant?:\n"
    "• Minor repairs, who handles, and up to how much?:\n"
    "• Is the owner staying in the unit?:\n"
    "• How many existing housemates, and their gender?:\n"
    "• How many share the bathroom?:\n\n"
    "Tenant Preferences\n"
    "• Preferred gender:\n"
    "• Preferred nationality (any you prefer or exclude?):\n"
    "• Preferred tenant type (working professional, student, couple, family):\n"
    "• Max number of occupants:\n"
    "• Previous landlord references or income proof needed? (payslip, employment letter):\n\n"
    "House Rules\n"
    "• Cooking (allowed, not allowed, or negotiable?):\n"
    "• Pets allowed?:\n"
    "• Smoking allowed?:\n"
    "• Subletting allowed?:\n"
    "• Visitors and overnight guests policy:\n"
    "• Any other rules or concerns upfront? (noise, parties etc):\n\n"
    "Viewings\n"
    "• How to handle viewings (keys, lockbox, or accompanied?):\n"
    "• Share 3 to 5 available dates and times (I will coordinate at least 2 groups before bringing anyone):\n\n"
    "Lastly, could you send a few photos and a short video of the room and common areas? 📸 This helps me market it to the right tenants and cuts unnecessary viewings.\n\n"
    "Thank you! I will get started once I have these details 🙏"
)

LANDLORD_FOLLOW_UP_DAY_3 = (
    "Haven't heard back on the landlord form yet. Any questions or blockers? Happy to help walk you through it."
)

LANDLORD_FOLLOW_UP_DAY_5_PHOTOS = (
    "Thanks for the details. Could you share 3 to 5 photos and a short video of the room and common areas? This helps tenants get a better sense of the space."
)

LANDLORD_FOLLOW_UP_DAY_7 = (
    "Just checking in. If you've decided not to rent out right now, no worries. Feel free to reach out anytime."
)

LANDLORD_CAROUSELL_OBJECTION = (
    "I've tried downloading from Carousell before, but the photo quality is always poor. Professional photos and video will get you better qualified tenants much faster. Can you share high quality shots directly instead? Even phone photos are fine as long as they're clear and well lit."
)

def is_carousell_objection(text):
    """Detect if landlord is saying they'll send Carousell photos instead."""
    if not text:
        return False
    low = text.lower()
    return any(phrase in low for phrase in (
        "download from carousell",
        "send carousell photo",
        "photos are on carousell",
        "carousell photo",
        "from carousell",
        "carousell picture",
        "carousell image",
    ))

def is_supply_form_filled(text):
    """Best-effort: did the landlord fill out most of the supply form?
    Look for presence of key field labels with values (e.g. 'Owner name: John')."""
    if not text:
        return False
    # Rough heuristic: if at least 5-6 of the main sections have label:value patterns
    labels = (
        r"owner\s+name\s*:",
        r"address",
        r"asking\s+rent",
        r"available\s+from",
        r"rooms?\s+available",
        r"furnish",
    )
    matches = sum(1 for label in labels if re.search(label, text, re.I))
    return matches >= 5

def get_landlord_followup_action(rec, now_timestamp):
    """Check if a landlord record is due for a follow-up send.
    Returns the day number (3, 5, 7) if due, None otherwise.
    Assumes supply_form_sent=True and rec has follow_up_schedule initialized."""
    if not rec.get("supply_form_sent"):
        return None
    
    sched = rec.get("follow_up_schedule") or {}
    for day in (3, 5, 7):
        key = f"day_{day}"
        if key not in sched:
            continue
        info = sched[key]
        if info.get("sent"):
            continue  # already sent
        scheduled = info.get("scheduled")
        if scheduled and now_timestamp >= scheduled:
            return day
    return None

def init_landlord_followup_schedule(timestamp):
    """Initialize a fresh follow-up schedule based on supply_form_sent timestamp (day 0).
    Returns dict with day_0/3/5/7 entries."""
    import datetime
    base = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    
    schedule = {
        "day_0": {
            "sent": True,
            "timestamp": timestamp
        }
    }
    
    for day in (3, 5, 7):
        future = base + datetime.timedelta(days=day)
        schedule[f"day_{day}"] = {
            "sent": False,
            "scheduled": int(future.timestamp())
        }
    
    return schedule

# ========== LANDLORD TENANT MATCHING & 99.CO AUTO-LISTING INTEGRATION ==========
# STILL DORMANT -- called from nowhere (6 Sep 2026 landlord onboarding build deliberately did
# NOT wire this in). Winfred keeps tenant matching and 99.co listing manual; the onboarding
# flow above only gets a landlord to SUPPLY_READY (info complete + photos) and flags Winfred,
# it never auto matches or auto lists. Do not wire this without his explicit go.
# Called from wa_intake_runner.py when landlord form completion is detected.

def _try_import_matcher():
    """Safely import the matcher module; return None if import fails."""
    try:
        import landlord_tenant_matcher as matcher
        return matcher
    except ImportError:
        return None

def _try_import_99co_lister():
    """Safely import the 99.co lister module; return None if import fails."""
    try:
        import ninety_nine_co_lister as lister_99co
        return lister_99co
    except ImportError:
        return None

def on_landlord_form_completed(state, pn, form_text, landlord_name=""):
    """
    Trigger when a landlord's form is detected as complete.
    Matches against active tenants, sends notifications, and queues 99.co listing.

    Args:
        state: intake-state.json dict (mutable; will be updated with match tracking)
        pn: landlord's phone number
        form_text: the completed form text
        landlord_name: landlord's name (optional)

    Returns:
        List of action dicts: [
            {"type": "SEND_MATCH", "tenant_pn": "...", "text": "..."},
            {"type": "CONFIRM_LISTING", "text": "..."},
            ...
        ]
    """
    actions = []

    # Load matcher module
    matcher = _try_import_matcher()
    if not matcher:
        return [{"type": "FLAG_HUMAN", "notify": True, "reason": "Matcher module not available"}]

    # Run matching
    try:
        match_actions = matcher.on_landlord_form_completed(state, pn, form_text)
        actions.extend(match_actions)
    except Exception as e:
        actions.append({
            "type": "FLAG_HUMAN",
            "notify": True,
            "reason": f"Matching failed: {str(e)}"
        })

    # Try to create 99.co listing
    lister = _try_import_99co_lister()
    if lister:
        try:
            listing_result = lister.on_landlord_form_completed_for_99co(
                pn, form_text, landlord_name
            )
            if listing_result.get("success"):
                url = listing_result.get("url")
                listing_key = listing_result.get("listing_key")
                msg = (
                    f"Your room is now live on 99.co!\n"
                    f"View it here: {url or f'(listing key: {listing_key})'}\n\n"
                    f"I will also send matched tenants from my network. "
                    f"You will hear from them within 24 to 48 hours."
                )
                actions.append({
                    "type": "SEND_CONFIRMATION",
                    "landlord_pn": pn,
                    "text": msg,
                })
            elif listing_result.get("pending"):
                actions.append({
                    "type": "FLAG_HUMAN",
                    "notify": True,
                    "reason": (
                        f"99.co listing queued (not yet auto-created). "
                        f"Listing key: {listing_result.get('listing_key')}"
                    ),
                })
        except Exception as e:
            actions.append({
                "type": "FLAG_HUMAN",
                "notify": True,
                "reason": f"99.co listing creation failed: {str(e)}"
            })

    return actions

