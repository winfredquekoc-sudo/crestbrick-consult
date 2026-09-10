"""
wa_intake_echo.py -- bot echo detection, split out of wa_intake_runner.py (8 Sep 2026) to
keep that file under the repo's 500 line guideline. Pure text matching, no I/O, no state;
imported straight back into wa_intake_runner's namespace so every existing call site (and
every test that reaches these via wa_intake_runner.<name>) is unaffected.
"""
import re

# Filled-profile detection: a prospect's COMPLETED form contains values after the labels,
# unlike the blank form the bot sends (which the bridge sometimes echoes as is_from_me=0).
_FILLED_RE = re.compile(r"(name|nationality|ethnicity|gender|age|pass|budget|occupation)\s*[:：][^\S\n]*\S", re.I)
# Phrases ONLY the bot ever sends — a prospect would never type these.
_OUTBOUND_ONLY = (
    "your viewing is confirmed", "you fit what the landlord", "the next viewing is",
    "i will send your profile", "good news, there is a viewing", "i will hold that slot",
    "reply yes to take this slot", "profile does not match", "✅ suits", "📲 more listings",
    "following up on your rental enquiry", "let me confirm that slot with the owner",
    # full nudge prefixes, NOT the bare phrase "almost there" — that is exactly what a
    # prospect texts when they are on the way to a viewing, and it must not be dropped.
    "almost there :) to send your profile", "almost there :) i still need",
    "could you confirm this so i can send your profile",
    "when are you able to view", "by sharing these details you agree",
    # viewing-first texts (11 Aug 2026) — echoed engine sends must never read as inbound
    "keen to view? i can put you in", "are you free to view on", "i can arrange for viewing",
    "to confirm your viewing slot with the landlord",
    "can i just check your", "just need your profile above", "ok can, your viewing is on",
    "your viewing is on",   # question branch variant (review fix 1, 11 Sep 2026)
    "thanks, that fits what we are looking for",   # buyer QUALIFIED offer
    "i am the agent helping the landlord",   # own/agent disclosure
    "what time will you be coming? i will keep", "see you then, i will send the unit number",
    "on your question, let me check with the owner", "viewing slot:",
    "no worries, which day and time would work better",
    "more rooms available on my rental channel",   # pre 11 Sep 2026 wording, kept for old rows
    "i have more than 30 rooms available on my channel",
    "i have many rooms available on my channel",
    # landlord onboarding extension (never mistake our own send for a landlord reply)
    "almost there, i just need",
    "thanks, that is everything i need for now",
    "just checking in, still keen to send a few photos",
    # Chinese first touch (Winfred, 11 Sep 2026) -- mirrors intake_engine.py's
    # _ENGINE_PREFIXES/BOT_SIGNATURES so an echoed Chinese send is never read as a
    # manual reply by Winfred (which would wrongly latch manual_takeover).
    "为了跟房东确认您的看房时间，我需要您的资料",
    "方便过来看房吗？我可以帮您安排，时间是",
    "谢谢，您的条件符合房东的要求。我现在就把您的资料发给房东。",
    "你好 :) 谢谢您提供的资料。在把您的资料发给房东之前",
    "跟您分享一下，房东希望租期至少一年",
    "我的频道里有超过30间房间可供选择",
    "我的频道里有很多房间可供选择",
    # remaining gap c2mix04 / c4rm04 (11 Sep 2026) -- same reasoning: an echoed engine send
    # must never be misread as Winfred's own hand reply (intake_engine.py's
    # BOT_SIGNATURES/_ENGINE_PREFIXES carry the same three phrases).
    "我是帮房东处理这个单位的中介",
    "which unit were you enquiring about",
    "请问您看到的是哪个单位",
)

# Landlord onboarding action types: manual_takeover is latched the moment supply side is
# detected (to keep the record out of the tenant/buyer flows), so every send this sequence
# makes needs the SAME carve out SEND_SUPPLY_FORM already had (runner-integration catch c74,
# 11 Aug 2026). Module level (not inline in run()) so it is inspectable without a live tick.
_LANDLORD_ONBOARDING_TYPES = ("SEND_SUPPLY_FORM", "SUPPLY_INFO_NUDGE",
                              "SUPPLY_MEDIA_ASK", "SUPPLY_MEDIA_CHASE")

def _is_our_echo(content):
    """True when a is_from_me=0 row is actually our OWN bot message echoed back by the
    bridge (it stores some bot sends with is_from_me=0). Such a row must NOT be treated as a
    prospect inbound — otherwise it poisons the profile (extract_profile on the blank form)
    or self-triggers ANSWER_QUESTION/CONFIRM_VIEWING. A prospect's FILLED form (same 'fill
    this in' text but WITH values) is NOT an echo and must still be processed."""
    t = (content or "").lower()
    if not t:
        return False
    # bot-only markers (incl. the unit-info message 1, which carries "✅ suits" / "📲 more
    # listings" / "available viewing"). These are phrases a prospect never types — unlike the
    # ambiguous "still available", which a prospect DOES say, so we must NOT match on that.
    if any(s in t for s in _OUTBOUND_ONLY):
        return True
    # the blank intake form echoed back: contains the prompt but no filled-in values
    if "fill this in" in t and not _FILLED_RE.search(content or ""):
        return True
    # Chinese form header, same reasoning (Winfred, 11 Sep 2026) -- _FILLED_RE already
    # catches a FILLED Chinese form because every bilingual field label carries its English
    # half too ("姓名 Name: 张三" still matches the "name\s*[:：]\S" pattern).
    if "请填写以下资料" in t and not _FILLED_RE.search(content or ""):
        return True
    return False
