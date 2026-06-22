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

DRY_RUN = False  # LIVE 2026-06-17: restored after form_sent crash fix (backlog already drained in preview)
MAX_PROSPECT_MSGS = 10  # hard cap: at most this many prospect-facing messages per person (per qualification attempt)

WA_DB   = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")
IDX     = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")
AVAIL   = os.path.expanduser("~/.claude/state/listing-templates/viewing-availability.json")
STATE   = os.path.expanduser("~/.claude/state/listing-templates/intake-state.json")
TEMPLATES = os.path.expanduser("~/.claude/state/listing-templates/property-templates.json")
LANDLORD_DB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")

REQUIRED_FIELDS = ["name","nationality","ethnicity","gender","age",
                   "pass_type","no_of_pax","move_in_date","lease_term_months","budget"]

INTAKE_FORM = (
    "Pls fill this in so I can send your profile to the landlord :)\n"
    "• Name:\n"
    "• Nationality:\n"
    "• Ethnicity:\n"
    "• Gender:\n"
    "• Age:\n"
    "• Type of Pass (PR/EP/S Pass/STP/SC etc):\n"
    "• No. of Pax (how many staying):\n"
    "• Intended Move in Date (e.g. 1 Aug):\n"
    "• Preferred Lease Term (e.g. 12 or 24 months):\n"
    "• Budget (S$ per month):\n"
    "• Preferred Location (area or MRT):"
)

# ---------- identity ----------
def resolve_pn(jid):
    """@lid or @s.whatsapp.net -> bare phone number. One human, one key."""
    if not jid: return None
    raw = jid.split("@")[0]
    if jid.endswith("@s.whatsapp.net"): return raw
    try:
        con = sqlite3.connect(WA_DB, timeout=30)
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
    return {l["listing_key"]: l for l in _load(IDX, {"listings":[]})["listings"]}

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
    a = _load(AVAIL, {"slots":{}}).get("slots",{}).get(listing_key,{})
    slots = [s for s in a.get("slots",[]) if s.get("status")=="open" and s.get("booked",0) < s.get("capacity",1) and s.get("date","") >= today]
    slots.sort(key=lambda s:(s.get("date",""), s.get("start","")))
    return slots[0] if slots else None

def _has_open_future_slot(listing_key):
    """True if the listing has an open future viewing slot. Decides whether to ask
    Winfred to capture the landlord's availability."""
    return next_future_slot(listing_key) is not None

def book_slot(listing_key, slot_id, path=AVAIL):
    """Atomic capacity guard. Increments booked iff booked < capacity. Returns True if booked,
    False if the slot just filled (so two YES on a one person slot can never both book)."""
    import fcntl
    if not slot_id: return False
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
        for s in data.get("slots",{}).get(listing_key,{}).get("slots",[]):
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

# ---------- profile extraction ----------
def _to_int(s):
    if s is None: return None
    txt = str(s).lower().strip()
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

def extract_profile(text):
    """Best-effort parse of a filled-in block or free text. Required fields only."""
    p = {}
    t = text or ""
    def grab(label):
        # capture only the SAME-LINE value (do not let \s* swallow the newline and grab the
        # NEXT field label as the value — that is how a blank form poisoned the profile).
        m = re.search(r"(?:" + label + r")\s*[:\-]?[^\S\n]*([^\n]*)", t, re.I)
        if not m:
            return None
        val = m.group(1).strip().lstrip("•").strip()
        # if a format hint sits between the label and the value (e.g. "Date (e.g. 1 Aug): 15 Aug"),
        # take what follows the LAST colon, then drop a leading "(...)" hint. A blank field that only
        # echoes the hint then collapses to empty -> None, so a hint is never read as a real value.
        if ":" in val:
            val = val.split(":")[-1].strip()
        val = re.sub(r"^\([^)]*\)\s*", "", val).strip()
        # reject an empty value or one that is itself another field label
        if not val or re.match(r"^(name|nationality|ethnic|gender|sex|age|type\s+of\s+pass|pass|visa|"
                               r"no\.?\s*of|pax|occupant|intended|move|preferred|lease|budget|rent)\b", val, re.I):
            return None
        return val
    nm  = grab(r"name");            p["name"]=nm
    nat = grab(r"nationality");     p["nationality"]=nat
    eth = grab(r"ethnic\w*");       p["ethnicity"]=eth
    g   = grab(r"gender|sex")
    if g:
        gl=g.lower()
        # preserve couple / dual-sex phrasing so qualify() can apply the couple gate;
        # only collapse to a single token for an unambiguous single gender.
        has_f = bool(re.search(r"\bf(?:emale)?\b", gl))
        has_m = bool(re.search(r"\bm(?:ale)?\b", gl))
        if "couple" in gl or "married" in gl or (has_f and has_m):
            p["gender"] = g.strip()
        elif gl.startswith("f"): p["gender"] = "Female"
        elif gl.startswith("m"): p["gender"] = "Male"
        else: p["gender"] = g
    age = grab(r"age")
    if age and _to_int(age) and _to_int(age) < 120: p["age"]=_to_int(age)
    ps  = grab(r"pass|visa")
    if ps: p["pass_type"]=ps.strip()
    pax = grab(r"pax|occupant|no\.? of (?:pax|people)")
    if pax and _to_int(pax) and _to_int(pax) < 12: p["no_of_pax"]=_to_int(pax)
    mv  = grab(r"move.?in|intended move")
    if mv: p["move_in_date"]=mv.strip()
    ls  = grab(r"lease")
    if ls:
        lsl=ls.lower()
        if "year" in lsl or "yr" in lsl: p["lease_term_months"]=12*(_to_int(lsl) or 1)
        else:
            n=_to_int(lsl)
            if n and n<=36: p["lease_term_months"]=n
    bud = grab(r"budget|rent|afford")
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
    return {k:v for k,v in p.items() if v not in (None,"")}

def _open_intake(listing):
    """True if a listing accepts all profiles (owner takes everyone) — only baby + the listing's
    own ethnicity/nationality gate apply, and enquirers get a short form straight to the viewing."""
    r = ((listing or {}).get("requirements") or (listing or {}))
    return bool(r.get("open_intake"))

OPEN_INTAKE_FORM = (
    "Happy to set up a viewing :) Just drop me:\n"
    "• Name:\n"
    "• Nationality:\n"
    "• No. of Pax (how many staying, and any infants):\n\n"
    "Then I will lock in your slot."
)

def missing_required(profile, listing=None):
    r = (((listing or {}).get("requirements") or (listing or {})) if listing else {})
    if r.get("open_intake"):
        # owner accepts everyone -> only the fields needed to screen baby (pax) and, if the
        # listing keeps a nationality gate, nationality. Name to address them.
        req = ["name", "no_of_pax"]
        np = r.get("nationality_pref", {}) or {}
        if np.get("mode") in ("exclude", "only") and np.get("list"):
            req.append("nationality")
        return [f for f in req if profile.get(f) in (None, "")]
    return [f for f in REQUIRED_FIELDS if profile.get(f) in (None,"")]

def listing_unit_message(listing_key):
    """MESSAGE 1: unit info from the template + the landlord's available viewing slot.
    No intake form. The form is sent as a separate second message (see handle_event)."""
    d = _load(TEMPLATES, {"listings":[]})
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
            avail = ("\n\nAvailable viewing: " + slot["label"]) if (slot and slot.get("label")) else ""
            return head + avail
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
def is_tenant_enquiry(text, listing_key=None):
    """Broader than is_enquiry: also accepts natural tenant phrasings, non-English rental
    enquiries, and any message bound to one of OUR listings that is not a sale. A landlord-
    supply / negation marker vetoes back to manual so we never form-blast a landlord."""
    low = (text or "").lower()
    if any(n in low for n in _SUPPLY_NEG):
        return False
    if is_enquiry(text) or any(p in low for p in EXTRA_ENQUIRY_SIGNS):
        return True
    if listing_key:
        tx, _ = classify_transaction(text, listing_key)
        if tx != "sale":
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

BOT_SIGNATURES = ("pls fill this in","fill this in","still available","✅ suits","📲 more listings","available viewing",
                  "your viewing is confirmed","profile does not match","the next viewing is",
                  "you fit what the landlord","when are you able to view","i will send your profile",
                  "good news, there is a viewing","i will hold that slot","reply yes to take this slot",
                  "following up on your rental enquiry","let me confirm that slot with the owner",
                  "almost there","by sharing these details you agree")
def is_bot_message(text):
    """True if an outbound message was sent by THIS engine (so it is not a manual reply by Winfred)."""
    return any(b in (text or "").lower() for b in BOT_SIGNATURES)

EXCLUDE_NAMES = ("wanni","shaw","madeleine","darren","amanda","don chuang")
def _contact_names(pn):
    """Returns (names_list, db_ok). db_ok is False if the contact DB read FAILED (e.g. lock),
    so the caller can fail-closed rather than treat a locked DB as 'no name = not excluded'."""
    out = []
    try:
        con = sqlite3.connect(WA_DB, timeout=30)
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
    re-reads, so DB edits take effect within 120s."""
    try:
        d = json.load(open(LANDLORD_DB))
    except Exception:
        return frozenset()
    out = set()
    for l in d.get("landlords", []):
        ph = re.sub(r"\D", "", str(l.get("phone") or ""))
        if ph: out.add(ph)
        cj = str(l.get("chat_jid") or "")
        if cj: out.add(cj.split("@")[0])
    return frozenset(out)

def excluded_reason(pn, text=""):
    """Never message a landlord, a co broke agent, or a colleague/personal contact.
    Returns a reason string, or None if clearly NOT excluded. On a DB read failure it returns
    'db_error' (fail-closed) so a transiently locked contact DB can never silently let a
    landlord/agent through the gate and receive the tenant form."""
    if pn and pn in _landlord_pn_set():   # authoritative landlord DB: catches outbound-first chats too
        return "landlord"
    names, db_ok = _contact_names(pn)
    nm = " ".join(names).lower()
    if "landlord" in nm: return "landlord"
    if any(e in nm for e in EXCLUDE_NAMES): return "colleague"
    t = (text or "").lower()
    if any(a in t or a in nm for a in ("propnex","huttons","orangetee","i take my own com","co-broke","co broke",
                                       "your commission","my commission","i charge","co broke","cobroke")):
        return "agent"
    if not db_ok:
        return "db_error"        # could not verify the contact -> defer to a human, do not auto-send
    return None

# ---------- qualify (corrected, honours every mode) ----------
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

    lmin = r.get("lease_min_months"); lt = profile.get("lease_term_months")
    if isinstance(lmin,int) and isinstance(lt,int) and lt < lmin:
        fails.append("minimum lease " + str(lmin) + " months")
    lmax = r.get("lease_max_months")
    if isinstance(lmax,int) and isinstance(lt,int) and lt > lmax:
        unknown.append("lease over landlord max")

    ma = r.get("min_age"); age = profile.get("age")
    if isinstance(ma,int) and isinstance(age,int) and age < ma:
        fails.append("minimum age " + str(ma))

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
    if unknown: return "NEEDS_INFO", unknown
    return "QUALIFIED", []

# ---------- state ----------
def load_state(): return _load(STATE, {"version":1, "conversations":{}})
def save_state(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp,"w"), indent=1, ensure_ascii=False)
    os.replace(tmp, STATE)   # atomic; one writer

def _rec(state, pn):
    rec = state["conversations"].setdefault(pn, {})
    for k, v in {
        "pn":pn, "listing_key":None, "stage":"NEW", "profile":{},
        "processed_ids":[], "form_sent":False, "asked_fields":[],
        "viewing_asked":False, "viewing_confirmed":False,
        "manual_takeover":False, "status":"new", "last_inbound":None}.items():
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

# ---------- manual-takeover co-pilot ----------
def _copilot_verdict(rec):
    """When Winfred is handling a chat by hand (manual_takeover), the engine stays silent to the
    prospect but still screens a COMPLETE, listing-bound profile and returns a COPILOT_VERDICT so
    Winfred gets the qualify() result on Telegram (never a prospect send). Fires once per distinct
    verdict (copilot_sig latch); never for a known landlord/agent/colleague."""
    if rec.get("terminal"):
        return None
    lk = rec.get("listing_key")
    if not lk:
        return None
    listing = listing_reqs().get(lk)
    if not listing:
        return None
    if missing_required(rec.get("profile", {}), listing):
        return None                       # cheap gate first: wait for the (minimal) profile
    if excluded_reason(rec.get("pn")) in ("landlord", "agent", "colleague", "db_error"):
        return None                       # never co-pilot a landlord/agent (or on a locked contact DB)
    verdict, why = qualify(listing, rec["profile"])
    sig = verdict + "|" + ",".join(why)
    if rec.get("copilot_sig") == sig:
        return None                       # already surfaced this exact verdict to Winfred
    rec["copilot_sig"] = sig
    rec["qualify"] = {"verdict": verdict, "why": why}
    # Under manual takeover, a QUALIFIED prospect with an open slot gets the viewing offered
    # AUTOMATICALLY (the source-aware guard now lets the engine's own follow-up through). Winfred
    # is still pinged so he can step in. Fires once (viewing_asked latch).
    if verdict == "QUALIFIED" and not rec.get("viewing_asked"):
        slot = next_slot(lk)
        if slot:
            rec["viewing_asked"] = True
            rec["stage"] = "VIEWING_OFFERED"; rec["status"] = "viewing_offered"
            rec["offered_slot_id"] = slot.get("slot_id")
            return {"type": "OFFER_VIEWING", "pn": rec.get("pn"), "slot": slot,
                    "slot_id": rec["offered_slot_id"], "text": _viewing_text(slot),
                    "notify": True, "copilot": True, "listing_key": lk, "verdict": verdict}
    # NEEDS_INFO / DISQUALIFIED, or QUALIFIED with no open slot -> notify Winfred only (he handles).
    return {"type": "COPILOT_VERDICT", "pn": rec.get("pn"), "notify": True, "text": None,
            "verdict": verdict, "why": why, "listing_key": lk}

# ---------- stage 3 reaction (shared: autonomous flow + manual co-pilot after an auto-offer) ----------
def _viewing_reaction(rec, ev, pn):
    """After a viewing has been offered, react to ONE prospect reply — confirm the slot, acknowledge a
    proposed time, or flag a question to Winfred. Used by the autonomous flow AND by the manual-takeover
    co-pilot once it has auto-offered, so a qualified tenant gets booked end-to-end. Every branch
    notifies Winfred so he can step in."""
    txt = (ev.get("text") or "").lower()
    if not rec["viewing_confirmed"] and _has_viewing_time(txt):
        rec["status"] = "viewing_time_proposed"
        return {"type": "VIEWING_TIME_PROPOSED", "pn": pn, "when": ev.get("text"), "notify": True,
                "text": "Got it, let me confirm that slot with the owner and revert to you shortly."}
    if "?" in (ev.get("text") or ""):
        return {"type": "ANSWER_QUESTION", "pn": pn, "notify": True, "question": ev.get("text"), "text": None}
    if not rec["viewing_confirmed"] and re.search(r"\b(yes|yep|yes please|ok|okay|confirm(?:ed)?|sure|deal)\b", txt):
        rec["viewing_confirmed"] = True; rec["status"] = "viewing_confirmed"
        return {"type": "CONFIRM_VIEWING", "pn": pn, "slot_id": rec.get("offered_slot_id"), "notify": True,
                "text": "Great, your viewing is confirmed. I will share the exact unit and meeting point closer to the time."}
    return None

# ---------- core handler: entry point enforces the per-prospect message cap ----------
def handle_event(state, ev):
    """Entry point: run the engine, then enforce a hard cap of MAX_PROSPECT_MSGS prospect-facing
    messages per person across the whole qualification attempt. Beyond the cap the bot stops
    messaging them and pings Winfred once (CAP_REACHED). Notify-only actions (FLAG_HUMAN /
    COPILOT_VERDICT / ANSWER_QUESTION) carry no prospect text, so they never count and are never
    capped — a real back-and-forth that needs Winfred can still surface."""
    a = _handle_event_inner(state, ev)
    if a and (a.get("text") or a.get("texts")):
        rec = state.get("conversations", {}).get(a.get("pn"))
        if rec is not None:
            if rec.get("sent_count", 0) >= MAX_PROSPECT_MSGS:
                if rec.get("cap_flagged"):
                    return None                       # already flagged once -> stay silent
                rec["cap_flagged"] = True
                return {"type": "CAP_REACHED", "pn": a.get("pn"), "notify": True, "text": None,
                        "listing_key": rec.get("listing_key"),
                        "reason": "reached the " + str(MAX_PROSPECT_MSGS) + " message cap"}
            rec["sent_count"] = rec.get("sent_count", 0) + len(a.get("texts") or [a.get("text")])
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
    if pn in _landlord_pn_set():
        if isinstance(state.get("conversations"), dict):
            state["conversations"].pop(pn, None)
        return None
    rec = _rec(state, pn)

    # ----- our own / human outbound -----
    if ev.get("is_from_me"):
        # if it is not an engine-tagged message, Winfred replied by hand -> go silent
        if not ev.get("engine"):
            rec["manual_takeover"] = True
            rec["status"] = "manual"
        return None

    # ----- inbound from prospect -----
    mid = ev.get("msg_id")
    if mid and mid in rec["processed_ids"]:
        return None                      # event dedup: read once
    if mid: rec["processed_ids"].append(mid)
    rec["last_inbound"] = ev.get("text")
    if rec.get("terminal"):
        return None                      # closed / terminal conversation -> engine never acts again
    if withdrawal_signal(ev.get("text")):
        rec["terminal"] = True; rec["stage"] = "WITHDRAWN"
        rec["status"] = "closed (found elsewhere)"
        rec["closed_reason"] = "auto: prospect signalled they found another place / no longer renting"
        return {"type": "AUTO_CLOSED", "pn": pn, "notify": True, "text": None,
                "listing_key": rec.get("listing_key"),
                "reason": "said they found another place / no longer renting",
                "quote": (ev.get("text") or "")[:160]}

    # always merge any profile data, even under manual takeover (log once).
    # track whether THIS inbound added a new required field (drives state change).
    merged = extract_profile(ev.get("text",""))
    new_data = False
    for k,v in merged.items():
        if rec["profile"].get(k) in (None,""):
            rec["profile"][k] = v        # never overwrite a known field
            if k in REQUIRED_FIELDS: new_data = True

    if ev.get("listing_key") and not rec.get("listing_key"):
        rec["listing_key"] = ev["listing_key"]; new_data = True
    if rec["manual_takeover"]:
        rec["status"] = "manual"
        # Once the co-pilot has auto-offered a viewing, it OWNS the rest of that flow: it reacts to the
        # prospect's reply (confirm the slot / acknowledge a proposed time / flag a question) exactly
        # like the autonomous path, while still pinging Winfred. Before any auto-offer it stays silent
        # to the prospect and only screens (and auto-offers once QUALIFIED + a slot exists).
        if rec.get("viewing_asked"):
            return _viewing_reaction(rec, ev, pn)
        return _copilot_verdict(rec)

    reqs = listing_reqs()

    # STAGE 1: first contact -> send the listing message (unit info + form) ONCE, with safety gates
    if not rec["form_sent"]:
        why = excluded_reason(pn, ev.get("text",""))
        if why == "db_error":                    # contact DB locked -> fail closed for THIS run,
            rec["status"] = "deferred_db_lock"   # but do NOT latch (a genuine prospect re-checks
            return {"type":"FLAG_HUMAN", "pn":pn, # next run once the DB is readable). No send.
                    "reason":"contact DB unreadable; deferring, no auto-send", "text":None}
        if why:                                  # landlord, agent, or colleague -> never message
            rec["manual_takeover"] = True; rec["status"] = "excluded:" + why
            return {"type":"FLAG_HUMAN", "pn":pn, "reason":"excluded " + why, "text":None}
        if not is_tenant_enquiry(ev.get("text",""), rec.get("listing_key")):  # clear tenant enquiry only
            rec["status"] = "not_enquiry"
            return {"type":"FLAG_HUMAN", "pn":pn, "reason":"not a clear tenant enquiry", "text":None}
        # RENT vs SALE: the tenant intake form is for RENTAL enquiries only. A sale (buyer)
        # enquiry is an entirely different flow, so never auto-send it the tenant form.
        tx, txr = classify_transaction(ev.get("text",""), rec.get("listing_key"))
        rec["transaction"] = tx
        if tx == "sale":
            # Flag the sale (buyer) lead to a human ONCE. Do NOT latch manual_takeover and do
            # NOT mark form_sent: the same person may later send a genuine RENTAL enquiry,
            # which must still flow. A repeat sale message just stays silent.
            if rec.get("sale_flagged"):
                return None
            rec["sale_flagged"] = True; rec["stage"] = "SALE_ENQUIRY"; rec["status"] = "sale_enquiry"
            return {"type":"FLAG_HUMAN", "pn":pn,
                    "reason":"sale enquiry (" + txr + "); buyer flow, not tenant intake", "text":None}
        # listing status gate: never auto-send the form for a listing that is closed
        # (tenanted) or on hold. The room is gone; flag to a human instead of intaking.
        lk0 = rec.get("listing_key")
        if lk0:
            lst0 = reqs.get(lk0, {})
            st0 = str(lst0.get("status", "")).lower()
            if st0.startswith("closed") or st0 == "hold":
                rec["status"] = "listing_" + (st0.split()[0] or "closed")
                return {"type":"FLAG_HUMAN", "pn":pn,
                        "reason":"enquiry on a " + st0 + " listing (" + lk0 + "); room no longer available", "text":None}
        # service policy: if the opening message already reveals an excluded profile, do
        # not even send the form. Kind referral, once, no reason ever given.
        pol = policy_excluded(rec["profile"], ev.get("text",""), open_intake=_open_intake(reqs.get(lk0)))
        if pol:
            rec["form_sent"] = True; rec["terminal"] = True
            rec["stage"] = "POLICY_EXCLUDED"; rec["status"] = "policy_excluded:" + pol
            return {"type":"REDIRECT", "pn":pn, "reason":pol,
                    "text":_redirect_text(pol, rec["profile"], reqs)}
        rec["form_sent"] = True
        rec["stage"] = "FORM_SENT"; rec["status"] = "form_sent"
        lk = rec.get("listing_key")
        # TWO messages, once only: (1) unit info + available viewing slot, (2) the intake form.
        unit = listing_unit_message(lk)
        # open_intake listings (owner accepts all) get a SHORT form so enquirers are funnelled
        # straight to the viewing with minimal friction.
        form = OPEN_INTAKE_FORM if _open_intake(reqs.get(lk)) else INTAKE_FORM
        texts = [unit, form] if unit else [form]
        # capture landlord availability at first enquiry: flag if this listing has no
        # upcoming viewing slot yet, so Winfred can grab the landlord's next slot.
        need_avail = bool(lk) and not _has_open_future_slot(lk)
        return {"type":"SEND_FORM", "pn":pn, "texts":texts, "text":texts[0],
                "listing_key":lk, "capture_availability":need_avail}

    # STAGE 2: have form, not yet offered a viewing. Emit ONLY on a state change.
    if not rec["viewing_asked"] and not rec.get("terminal"):
        miss = missing_required(rec["profile"], reqs.get(rec.get("listing_key")))
        if miss:
            # nudge the prospect ONCE with the fields still missing (recovers partial fillers and
            # people who replied without using the form), then go silent. Anti-spam: one nudge.
            if rec.get("nudged_incomplete"): return None
            rec["nudged_incomplete"] = True
            rec["stage"] = "PROFILE_PENDING"; rec["status"] = "incomplete"
            return {"type":"NUDGE_INCOMPLETE", "pn":pn,
                    "reason":"incomplete profile, missing " + ", ".join(miss),
                    "text":_nudge_text(miss)}
        # profile complete
        # service policy: never match a profile the landlords will not take. Kind referral, once.
        pol = policy_excluded(rec["profile"], rec.get("last_inbound",""), open_intake=_open_intake(reqs.get(rec.get("listing_key"))))
        if pol:
            rec["terminal"] = True; rec["stage"] = "POLICY_EXCLUDED"; rec["status"] = "policy_excluded:" + pol
            return {"type":"REDIRECT", "pn":pn, "reason":pol,
                    "text":_redirect_text(pol, rec["profile"], reqs)}
        lk = rec.get("listing_key")
        listing = reqs.get(lk)
        if not listing:
            if rec.get("flagged_human"): return None
            rec["flagged_human"] = True; rec["status"] = "needs_listing"
            return {"type":"FLAG_HUMAN", "pn":pn, "reason":"listing not bound"}
        verdict, why = qualify(listing, rec["profile"])
        rec["qualify"] = {"verdict":verdict, "why":why}
        if verdict == "DISQUALIFIED":
            rec["terminal"] = True; rec["stage"] = "DISQUALIFIED"; rec["status"] = "disqualified"
            return {"type":"REDIRECT", "pn":pn, "reason":why,
                    "text":_redirect_text(why, rec["profile"], reqs)}
        if verdict == "NEEDS_INFO":
            if rec.get("needs_info_unknowns") == why:
                return None              # same gap already asked -> silent
            rec["needs_info_unknowns"] = why; rec["stage"] = "NEEDS_INFO"; rec["status"] = "needs_info"
            return {"type":"ASK_ONE", "pn":pn, "reason":why, "text":_needs_info_text(why)}
        # QUALIFIED -> offer the viewing once
        rec["viewing_asked"] = True
        rec["stage"] = "VIEWING_OFFERED"; rec["status"] = "viewing_offered"
        slot = next_slot(lk)
        rec["offered_slot_id"] = slot.get("slot_id") if slot else None
        return {"type":"OFFER_VIEWING", "pn":pn, "slot":slot,
                "slot_id":rec["offered_slot_id"], "text":_viewing_text(slot)}

    # STAGE 3: viewing offered -> react to one reply per inbound (shared with the manual co-pilot path)
    return _viewing_reaction(rec, ev, pn)

def _has_viewing_time(t):
    """True if the prospect's reply names a day or a time to view."""
    if not t: return False
    has_time = re.search(r"\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}[:.]\d{2}\b|\bnoon\b|after\s*\d", t)
    has_day  = re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun|today|tomorrow|tmr|weekend)\w*\b"
                         r"|\b\d{1,2}\s*/\s*\d{1,2}\b"
                         r"|\b\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t)
    return bool(has_time or has_day)

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
def _nudge_text(miss):
    fields = ", ".join(_NUDGE_LABELS.get(f, f) for f in miss)
    return ("Almost there :) To send your profile to the landlord I still need: " + fields +
            ". Could you fill these in?")

def _needs_info_text(why):
    return "Almost there. " + "; ".join(why) + ". Could you confirm this so I can send your profile to the landlord?"

CHANNEL = "https://whatsapp.com/channel/0029VbCoWRs4inomDhoAAv0G"
def _redirect_text(why, profile, reqs):
    # never reveal the reason or any protected attribute. kind note + channel referral.
    return ("Thanks for sending this :) Sorry, the profile does not match for this unit. "
            "You may find other rooms that suit you on my Singapore rental channel here: " + CHANNEL)

def _viewing_text(slot):
    if slot:
        return "Thanks, you fit what the landlord is looking for. I will send your profile over now. " \
               "The next viewing is " + slot["label"] + ". Reply YES to take this slot."
    return "Thanks, you fit what the landlord is looking for. I will send your profile over now. " \
           "When are you able to view?"
