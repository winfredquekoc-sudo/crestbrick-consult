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
import wa_intake_owner as OWN   # owner side of the loop -- see _enqueue_owner_question below
import wa_money_gate as MG      # shared price/deposit/injection/agent vocabulary -- see its docstring

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
# The module agnostic vocabulary (injection, bot check, protected attribute, advice/
# legal, deposit, price/negotiation, agent) lives in wa_money_gate.py, shared with
# intake_engine.py's own category 1 branches so neither drifts out of sync. Kept as
# module level aliases here so the rest of this file (and any external caller) reads
# unchanged.
_INJECTION_RE = MG.INJECTION_RE
_BOT_CHECK_RE = MG.BOT_CHECK_RE
_PROTECTED_ATTR_RE = MG.PROTECTED_ATTR_RE
_ADVICE_LEGAL_RE = MG.ADVICE_LEGAL_RE
_DEPOSIT_RE = MG.DEPOSIT_RE
_PRICE_TRIGGER_RE = MG.PRICE_TRIGGER_RE
_AGENT_RE = MG.AGENT_RE

def _is_stays_human(t):
    """Any hit here means: classify only, never answer. Checked BEFORE every category 2
    branch so an overlapping keyword (e.g. 'cheaper' inside an otherwise pax like message)
    always wins toward the human, never toward an auto reply."""
    if MG.core_stays_human(t):
        return True
    if E._FACT_VETO_RE.search(t):
        return True
    if E._FACT_RENT_RE.search(t) and E._FACT_RENT_NUMBER_RE.search(t):
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

_UNIT_RE = re.compile(
    r"#\s*[\w]{1,4}\s*-\s*[\w]{1,4}|"
    r"\bunit\s*#?\s*[\w]{1,4}\s*-\s*[\w]{1,4}\b", re.I)
_POSTAL_RE = re.compile(r"\bsingapore\s+\d{6}\b|\b\d{6}\b", re.I)
# a speculative or unconfirmed placeholder ("address not stated in chat", "(context
# suggests X; to confirm)") must never pass through as if it were a stated fact -- these
# only ever show up when the listing's own address field is itself a guess (P0 fix, 11
# Sep 2026 cycle5 c5s05: an injection attempt on an unrelated closed listing got back a
# real unit number plus a speculative building name presented as fact).
_SPECULATIVE_PAREN_RE = re.compile(
    r"\(\s*(?:context\s+suggests|to\s+confirm|not\s+(?:stated|confirmed)|unconfirmed)[^)]*\)", re.I)
_SPECULATIVE_CLAUSE_RE = re.compile(r",?\s*address\s+not\s+stated[^,;.]*", re.I)

def _address_no_unit(block_address):
    """block/street only -- strip any '#xx-xx' / 'Unit xx-xx' unit token, any speculative
    or unconfirmed placeholder text, and any 6 digit postal code. Never reveal a unit
    number or a guessed building name before a confirmed viewing (standing rule)."""
    s = block_address or ""
    s = _UNIT_RE.sub("", s)
    s = _SPECULATIVE_PAREN_RE.sub("", s)
    s = _SPECULATIVE_CLAUSE_RE.sub("", s)
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
    # no "shortly" (P3 fix, 11 Sep 2026): nothing in the engine actually sends media, so a
    # timing word on a promise nobody is tracking reads as broken if the follow up is missed.
    return ("Sure, let me get some photos and a short video over to you \U0001F642 "
            "Meanwhile, are you " + _free_to_view_phrase(lk) + "?")

_PAX_UNKNOWN_TEXT = "Let me check with the owner how many can stay and get back to you \U0001F642"

def _reply_pax_or_friends(rec, text):
    lk, listing = _listing_for(rec)
    maxp = (listing.get("requirements") or {}).get("max_pax")
    if maxp:
        return (f"This room is for up to {maxp} pax \U0001F642 Are you "
                + _free_to_view_phrase(lk) + "?")
    return _PAX_UNKNOWN_TEXT

def _reply_address_or_unit(rec, text):
    lk, listing = _listing_for(rec)
    addr = (listing.get("block_address") or "").strip()
    restricted = bool(listing.get("marketing_restrictions"))
    cleaned = _address_no_unit(addr) if addr else ""
    if lk and cleaned and not restricted:
        return ("It is at " + cleaned + " \U0001F642 I will send the exact "
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

# ---------- owner side of the loop: whenever the reply above is genuinely "let me check
# with the owner" (never when a fact was already answered from the listing's own data),
# queue the SAME question for Winfred's VIP owner ask (see wa_intake_owner.py's module
# docstring for the hook contract and the safety net that re-checks every one of these
# before it can ever reach a real send).
_OWNER_QUESTION_CODE = {
    "pax_or_friends": "PAX",
    "FACT_MOVEIN": "MOVE_IN",
    "FACT_UTILITIES": "UTILITIES",
    "FACT_MRT": "MRT",
    "FACT_VISITORS": "VISITORS",
    "FACT_COOKING": "COOKING",
    "FACT_PETS": "PETS",
    "FACT_SMOKING": "SMOKING",
    "follow_up_chaser": "AVAILABILITY",
}
_OWNER_QUESTION_TEXT = {
    "PAX": "A tenant is asking how many people are allowed to stay in this room. Could "
           "you confirm the maximum number of occupants?",
    "MOVE_IN": "A tenant is asking about the move in date for this room. Could you "
               "confirm when it is available from?",
    "UTILITIES": "A tenant is asking about utilities, wifi and aircon for this room. "
                 "Could you confirm what is included?",
    "MRT": "A tenant is asking how far this unit is from the nearest MRT station. Could "
           "you confirm the walking distance?",
    "VISITORS": "A tenant is asking about the visitor and overnight guest policy for "
                "this unit. Could you confirm the house rules?",
    "COOKING": "A tenant is asking whether cooking is allowed in this unit. Could you confirm?",
    "PETS": "A tenant is asking whether pets are allowed in this unit. Could you confirm?",
    "SMOKING": "A tenant is asking whether smoking is allowed in this unit. Could you confirm?",
    "AVAILABILITY": "A tenant is following up on their enquiry. Could you confirm this "
                     "room is still available and share your latest viewing availability?",
}

def _enqueue_owner_question(rec, rtype, code):
    """Best effort, never raises and never blocks the tenant reply: no landlord_id (listing
    unlinked, or none on this record) just means no question gets queued -- the tenant still
    gets their acknowledgement text either way."""
    owner_code = _OWNER_QUESTION_CODE.get(code) or _OWNER_QUESTION_CODE.get(rtype)
    if not owner_code:
        return
    lk, listing = _listing_for(rec)
    landlord_id = listing.get("landlord_id")
    if not landlord_id:
        return
    try:
        OWN.enqueue_owner_question(landlord_id, lk, owner_code,
                                   _OWNER_QUESTION_TEXT[owner_code],
                                   source=rec.get("pn"))
    except Exception:
        pass   # the tenant facing reply must never fail because the owner side hiccupped

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

    # rec["status"] is stamped "listing_closed"/"listing_hold" by intake_engine's own
    # Stage-1 gate (never re-derived here from the master landlord DB -- that lookup is
    # keyed on real landlord ids and has no place running inside a category 2 reply
    # builder). A closed/on hold listing must never get an address, unit, photo, or pax
    # answer manufactured from stale listing data -- one neutral "taken" line, once, never
    # a unit number or a guessed building name (P0 fix, 11 Sep 2026 cycle5 c5s05: an
    # injection attempt landed on a closed listing and still got back a real unit).
    if str(rec.get("status") or "").startswith("listing_"):
        if not _fire_once(rec, "closed_listing_reply", "CLOSED_LISTING"):
            return action
        out = dict(action)
        out["text"] = _ROOM_GONE_TEXT
        out["notify"] = True
        out["category2_code"] = "CLOSED_LISTING"
        out["reason"] = (out.get("reason") or "")
        out["reason"] = (out["reason"] + " " if out["reason"] else "") + "[category2:CLOSED_LISTING]"
        return out

    rtype = classify(text)
    if rtype is None or rtype not in _BUILDERS:
        return action

    builder, code = _BUILDERS[rtype]
    if not _fire_once(rec, rtype, code):
        return action
    reply = builder(rec, text)
    if not reply:
        return action

    # only when the tenant reply above is genuinely "let me check with the owner" -- a
    # fact the listing's own data already answered (a normal _reply_fact hit) never queues
    # anything, and the follow up chaser always does (it never has any other text).
    if ((rtype == "pax_or_friends" and reply == _PAX_UNKNOWN_TEXT)
            or (reply == _FACT_FALLBACK and code in _OWNER_QUESTION_CODE)
            or rtype == "follow_up_chaser"):
        _enqueue_owner_question(rec, rtype, code)

    out = dict(action)
    out["text"] = reply
    out["notify"] = True
    out["category2_code"] = code
    out["reason"] = (out.get("reason") or "")
    out["reason"] = (out["reason"] + " " if out["reason"] else "") + f"[category2:{code}]"
    return out
