"""
wa_intake_replies.py -- category 2: acknowledge and pivot.

intake_engine.py (category 1) answers a narrow set of factual questions from a listing's
own data and otherwise returns FLAG_HUMAN / ANSWER_QUESTION with no prospect facing text --
correct and safe, but it leaves a keen tenant hanging on a plain "still available?" or
"any photos?" until Winfred gets to it by hand.

This module is a THIN LAYER on top of that result, never a second pipeline: augment_action()
is called AFTER intake_engine.handle_event() has already run every gate it always runs
(manual takeover, quiet hours tick guard, cold guard, dispute block, exclusions, resume
rules, daily cap -- all enforced by wa_intake_runner.py around the SAME action dict this
module hands back). It only ever swaps in text for a result the pipeline was already about
to leave silent to the prospect, on a confirmed tenant record, and only once per request
type per chat -- so a second "still available?" in the same chat gets silence again, not a
second bot reply, exactly like every other one-shot template in the engine.

Anything with a negotiation, legal, identity, or safety edge (price, deposit, advice/legal,
co broke, "are you a bot", a question about the landlord's or housemates' race/ethnicity/
nationality/religion, or a prompt injection attempt) is classified but NEVER answered here --
the original FLAG_HUMAN/ANSWER_QUESTION(text=None) passes through untouched, exactly as
category 1 already leaves it.

No hyphens or dashes in any tenant facing copy (same standing rule as intake_engine.py).
"""
import re
import intake_engine as E

# ---------- gate: only a genuine tenant prospect, never landlord/agent/colleague/supply/buyer ----------
def _is_non_tenant(rec):
    """True when the record is already known to be something other than a tenant prospect --
    a landlord, co broke agent, colleague, supply side (landlord/seller) contact, a buyer
    (sale) enquiry, or a first message the engine itself could not read as a clear tenant
    enquiry at all. Mirrors the status strings intake_engine.py stamps at each of those
    exclusion points (excluded_reason, supply_side_kind, is_tenant_enquiry, photo gate)."""
    status = str(rec.get("status") or "")
    return bool(
        status.startswith("excluded:") or status == "deferred_db_lock"
        or status.startswith("supply_side:") or rec.get("supply_flagged")
        or status == "photo_no_text" or status == "not_enquiry"
        or rec.get("buyer_form_sent") or rec.get("transaction") == "sale"
    )

# ---------- stays human: classified but never auto answered ----------
_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+|your\s+|previous\s+|prior\s+)*instructions|"
    r"disregard\s+(?:all\s+|your\s+)*instructions|system\s*prompt|forget\s+(?:all\s+|your\s+|previous\s+)*instructions|"
    r"you\s+are\s+now\b|reveal\s+your\s+(?:prompt|instructions)|jailbreak|"
    r"pretend\s+(?:you\s+are|to\s+be)|act\s+as\s+(?:a|an)?\s*(?:dan|unfiltered|different)",
    re.I)
_BOT_CHECK_RE = re.compile(
    r"are\s+you\s+a\s+bot|is\s+this\s+(?:an?\s+)?(?:auto|bot)|real\s+person|chatbot|"
    r"am\s+i\s+(?:talking|chatting)\s+to\s+a\s+bot|is\s+this\s+automated", re.I)
_PROTECTED_ATTR_RE = re.compile(
    r"\bethnicity\b|\brace\b|\bnationality\b|\breligion\b|\breligious\b|\bmuslim\b|\bchristian\b|"
    r"\bhindu\b|\bbuddhist\b|chinese\s+only|malay\s+only|indian\s+only|any\s+race|any\s+nationality",
    re.I)
_ADVICE_LEGAL_RE = re.compile(r"\blegal\b|should\s+i\b|allowed\s+to\s+(?:kick|evict)", re.I)
_DEPOSIT_RE = re.compile(r"\bdeposit\b|\brefund\b", re.I)
_PRICE_TRIGGER_RE = re.compile(r"\bnego(?:tiable|tiate)?\b|\bcheaper\b|\bdiscount\b|\blower\b|\breduce\b|flexib", re.I)
_AGENT_RE = re.compile(
    r"co[\s-]?broke|cobroke|commission\s+split|\bera\b|propnex|orangetee|huttons|propertylimbrothers|"
    r"i'?m\s+an?\s+agent|from\s+era\b|co[\s-]?list(?:ing)?|my\s+client\s+(?:is|wants|would)|i\s+have\s+a\s+client",
    re.I)

def _is_stays_human(t):
    """Any hit here means: classify only, never answer. Checked BEFORE every category 2
    branch so an overlapping keyword (e.g. 'cheaper' inside an otherwise pax like message)
    always wins toward the human, never toward an auto reply."""
    if _INJECTION_RE.search(t) or _BOT_CHECK_RE.search(t) or _PROTECTED_ATTR_RE.search(t):
        return True
    if E._FACT_VETO_RE.search(t) or _ADVICE_LEGAL_RE.search(t):
        return True
    if _DEPOSIT_RE.search(t):
        return True
    if _PRICE_TRIGGER_RE.search(t):
        return True
    if E._FACT_RENT_RE.search(t) and E._FACT_RENT_NUMBER_RE.search(t):
        return True
    if _AGENT_RE.search(t):
        return True
    return False

# ---------- category 2 classification (ordered, first match wins) ----------
_FOLLOWUP_RE = re.compile(
    r"\bany\s+update\b|\bstill\s+there\b|\bfollowing\s+up\b|\bany\s+news\b|\bu\s+there\b|"
    r"\bhihi+\b|just\s+checking\s+in|^\s*hello+[?]+\s*$|^\s*\?+\s*$", re.I)
_AVAILABILITY_RE = re.compile(
    r"still\s+available|is\s+it\s+(?:still\s+)?available|any\s+room\s+available|"
    r"available\s+or\s+not|is\s+this\s+(?:taken|gone|still\s+here)|room\s+still\s+there|"
    r"got\s+room\s+or\s+not|还有(?:房间|房)?吗|masih\s+ada", re.I)
_ADDRESS_RE = re.compile(
    r"\baddress\b|unit\s+number|which\s+(?:block|unit)\b|postal\s+code|where\s+is\s+it|"
    r"detailed\s+address|exact\s+(?:address|location)", re.I)
_PAX_RE = re.compile(
    r"\bpax\b|\bfriend(?:s)?\b|\broommate\b|how\s+many\s+people|\boccupants?\b|\bcouple\b|"
    r"we\s+2\b|2\s+of\s+us|入住人数", re.I)
_PHOTOS_RE = re.compile(r"\bphotos?\b|\bpics?\b|\bpictures?\b|\bvideos?\b|send\s+(?:pic|photo)", re.I)
_MOVEIN_RE = re.compile(
    r"move\s*-?\s*in\s+date|when\s+can\s+i\s+move\s+in|available\s+from\b|start\s+date|"
    r"check\s*-?\s*in\s+date|入住日期", re.I)
_UTILITIES_RE = re.compile(
    r"\butilit(?:y|ies)\b|\bwifi\b|\baircon\b|air\s*-?\s*con\b|\belectricity\b|\bbills?\b|\binternet\b", re.I)
_MRT_RE = re.compile(
    r"\bmrt\b|\btrain\s+station\b|how\s+far|near\s+(?:the\s+)?mrt|walk(?:ing)?\s+distance|"
    r"which\s+area|\blocation\b", re.I)
_VISITORS_RE = re.compile(
    r"\bvisitors?\b|\bboyfriend\b|\bgirlfriend\b|\bpartner\b|\bovernight\b|stay\s*over|sleep\s*over", re.I)

# Ordered (type_key, regex) -- first match wins. Stays-human is checked separately, first,
# by the caller. Order here mirrors the corpus classifier (classify.py): the more specific
# / higher precision types first, the broad location/fact catch alls last.
_CATEGORY2_RULES = (
    ("availability", _AVAILABILITY_RE),
    ("address_or_unit", _ADDRESS_RE),
    ("move_in_date", _MOVEIN_RE),
    ("pax_or_friends", _PAX_RE),
    ("cooking", E._FACT_COOK_RE),
    ("visitors_overnight", _VISITORS_RE),
    ("pets", E._FACT_PET_RE),
    ("utilities_wifi_aircon", _UTILITIES_RE),
    ("mrt_location", _MRT_RE),
    ("photos_video", _PHOTOS_RE),
    ("smoking", E._FACT_SMOKE_RE),
    ("follow_up_chaser", _FOLLOWUP_RE),
)

def classify(text):
    """Returns a category 2 type key, or None (no match -- leave the original action alone).
    Never returns a type for anything _is_stays_human() flags."""
    t = text or ""
    if not t.strip():
        return None
    if _is_stays_human(t):
        return None
    for key, rx in _CATEGORY2_RULES:
        if rx.search(t):
            return key
    return None

# ---------- reply text builders ----------
_ROOM_GONE_TEXT = ("So sorry, that room was just taken \U0001F64F You can see my other "
                   "available rooms here:\n" + E.CHANNEL +
                   "\nLet me know if anything catches your eye and I will arrange a viewing.")

_UNIT_RE = re.compile(r"#\s*[\w]{1,4}\s*-\s*[\w]{1,4}")
_POSTAL_RE = re.compile(r"\bsingapore\s+\d{6}\b|\b\d{6}\b", re.I)

def _address_no_unit(block_address):
    """block/street only -- strip any '#xx-xx' unit token and any 6 digit postal code.
    Never reveal a unit number before a confirmed viewing (standing rule)."""
    s = block_address or ""
    s = _UNIT_RE.sub("", s)
    s = _POSTAL_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,")
    return s

def _free_to_view_phrase(lk):
    slot = E.next_future_slot(lk) if lk else None
    if slot and slot.get("label"):
        return "free to view on " + slot["label"]
    return "free to view this week"

def _listing_for(rec):
    lk = rec.get("listing_key")
    if not lk:
        return lk, {}
    return lk, (E.listing_reqs().get(lk) or {})

def _reply_availability(rec, text):
    lk, listing = _listing_for(rec)
    if not lk:
        return ("Which unit are you asking about? Send me the listing link or address and "
                "I will check for you \U0001F642")
    st = E._listing_unavailable(lk)
    if st:
        return _ROOM_GONE_TEXT
    return "Yes still available \U0001F642 Are you " + _free_to_view_phrase(lk) + "?"

def _reply_photos_video(rec, text):
    lk, _ = _listing_for(rec)
    return ("Sure, let me get some photos and a short video over to you shortly \U0001F642 "
            "Meanwhile, are you " + _free_to_view_phrase(lk) + "?")

def _reply_pax_or_friends(rec, text):
    lk, listing = _listing_for(rec)
    maxp = (listing.get("requirements") or {}).get("max_pax")
    if maxp:
        return (f"This room is for up to {maxp} pax \U0001F642 Are you "
                + _free_to_view_phrase(lk) + "?")
    return "Let me check with the owner how many can stay and get back to you \U0001F642"

def _reply_address_or_unit(rec, text):
    lk, listing = _listing_for(rec)
    addr = (listing.get("block_address") or "").strip()
    restricted = bool(listing.get("marketing_restrictions"))
    if lk and addr and not restricted:
        return ("It is at " + _address_no_unit(addr) + " \U0001F642 I will send the exact "
                "unit once your viewing is confirmed.")
    return ("I will share the exact address once your viewing is confirmed \U0001F642 Are "
            "you " + _free_to_view_phrase(lk) + "?")

_FACT_FALLBACK = "Let me check with the owner and get back to you shortly."

def _reply_fact(rec, text):
    _, listing = _listing_for(rec)
    ans = E._tenant_fact_answer(text, listing)
    return ans if ans else _FACT_FALLBACK

def _reply_follow_up_chaser(rec, text):
    return ("Sorry for the wait, still checking with the owner. I will update you as soon "
            "as I hear back \U0001F64F")

_PHOTO_ONLY_KNOWN_TEXT = "Thanks for sending this \U0001F642 What would you like to know about the unit?"

# type_key -> (builder, notify code)
_BUILDERS = {
    "availability": (_reply_availability, "AVAILABILITY"),
    "photos_video": (_reply_photos_video, "MEDIA_REQUEST"),
    "pax_or_friends": (_reply_pax_or_friends, "PAX"),
    "address_or_unit": (_reply_address_or_unit, "ADDRESS"),
    "move_in_date": (_reply_fact, "FACT_MOVEIN"),
    "utilities_wifi_aircon": (_reply_fact, "FACT_UTILITIES"),
    "mrt_location": (_reply_fact, "FACT_MRT"),
    "visitors_overnight": (_reply_fact, "FACT_VISITORS"),
    "cooking": (_reply_fact, "FACT_COOKING"),
    "pets": (_reply_fact, "FACT_PETS"),
    "smoking": (_reply_fact, "FACT_SMOKING"),
    "follow_up_chaser": (_reply_follow_up_chaser, "CHASER"),
}

def _fire_once(rec, rtype, code):
    """One fire per request type per chat. Returns False (do not fire) on a repeat."""
    fired = rec.setdefault("category2_fired", {})
    if fired.get(rtype):
        return False
    fired[rtype] = True
    return True

def _is_bare_flag(action):
    return bool(action) and action.get("type") in ("FLAG_HUMAN", "ANSWER_QUESTION") \
        and action.get("text") is None and not action.get("texts")

def augment_action(state, ev, action):
    """Called right after intake_engine.handle_event(). Returns the SAME action unchanged
    unless: (1) the record is a confirmed tenant prospect (never landlord/agent/colleague/
    supply/buyer), (2) the engine's own result was a bare FLAG_HUMAN/ANSWER_QUESTION (no
    prospect text) or, for a known tenant's bare photo, no action at all, and (3) the
    inbound text classifies to a category 2 type not already fired for this chat.
    Every other gate (manual takeover, quiet hours, cold guard, dispute, daily cap, resume
    allow list) is enforced by the caller around the returned action exactly as it already
    is for every other engine action -- this function only ever changes 'text'/'notify'/
    'reason'/'category2_code' on the SAME action dict shape the caller already understands."""
    pn = E.resolve_pn(ev.get("jid"))
    if not pn:
        return action
    rec = (state.get("conversations") or {}).get(pn)
    if rec is None or _is_non_tenant(rec):
        return action

    text = ev.get("text") or ""
    media = str(ev.get("media_type") or "")

    # item 7: a bare photo/video from an ALREADY known tenant (form sent or bound listing).
    # The engine's own photo gate only flags an UNKNOWN sender (no form_sent, no profile);
    # a known tenant's bare photo either falls through the normal flow with no action at all,
    # or (rarely) still reaches FLAG_HUMAN/ANSWER_QUESTION with no text -- either way this is
    # the one case this module also fires on a None action.
    if (not text.strip() and media in ("image", "video")
            and (rec.get("form_sent") or rec.get("listing_key"))
            and (action is None or _is_bare_flag(action))):
        if not _fire_once(rec, "photo_only_known", "PHOTO_ONLY_KNOWN"):
            return action
        base = action or {"type": "ANSWER_QUESTION", "pn": pn, "text": None}
        out = dict(base)
        out["text"] = _PHOTO_ONLY_KNOWN_TEXT
        out["notify"] = True
        out["category2_code"] = "PHOTO_ONLY_KNOWN"
        out["reason"] = (out.get("reason") or "photo from a known tenant") + " [category2:PHOTO_ONLY_KNOWN]"
        return out

    if not _is_bare_flag(action):
        return action

    rtype = classify(text)
    if rtype is None or rtype not in _BUILDERS:
        return action

    builder, code = _BUILDERS[rtype]
    if not _fire_once(rec, rtype, code):
        return action
    reply = builder(rec, text)
    if not reply:
        return action

    out = dict(action)
    out["text"] = reply
    out["notify"] = True
    out["category2_code"] = code
    out["reason"] = (out.get("reason") or "")
    out["reason"] = (out["reason"] + " " if out["reason"] else "") + f"[category2:{code}]"
    return out
