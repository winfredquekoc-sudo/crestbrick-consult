"""
wa_money_gate.py -- shared "this inbound is money or human territory" vocabulary.

Both intake_engine.py (category 1: lease note, confirm viewing, book intent, fact
answer) and wa_intake_replies.py (category 2: acknowledge and pivot) must refuse to
auto act on the same set of triggers -- price/negotiation, deposit/fee/waiver/
commission, protected attribute, prompt injection, "are you a bot", legal/advice, and
co broke/agent self disclosure. Defined once here so neither module drifts out of sync
(Winfred, 11 Sep 2026 money gate ordering fixes: price and deposit questions were
winning past the lease note / confirm viewing / book intent / ask one branches instead
of the other way around).
"""
import re

INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+|your\s+|ur\s+|previous\s+|prior\s+)*(?:instructions?|script|prompt|rules)|"
    r"disregard\s+(?:all\s+|your\s+)*instructions|system\s*prompt|forget\s+(?:all\s+|your\s+|previous\s+)*instructions|"
    r"you\s+are\s+now\b|reveal\s+(?:your|the)\s+(?:prompt|instructions?|(?:listing\s+)?owner)|jailbreak|"
    r"pretend\s+(?:you\s+are|to\s+be)|act\s+as\s+(?:a|an)?\s*(?:dan|unfiltered|different)|"
    r"^\s*system\s*:|authoriz(?:ed|ation)\s+override", re.I)
BOT_CHECK_RE = re.compile(
    r"are\s+you\s+a\s+bot|is\s+this\s+(?:an?\s+)?(?:auto|bot)|real\s+person|chatbot|"
    r"am\s+i\s+(?:talking|chatting)\s+to\s+a\s+bot|is\s+this\s+automated", re.I)
PROTECTED_ATTR_RE = re.compile(
    r"\bethnicity\b|\brace\b|\bnationality\b|\breligion\b|\breligious\b|\bmuslim\b|\bchristian\b|"
    r"\bhindu\b|\bbuddhist\b|chinese\s+only|malay\s+only|indian\s+only|any\s+race|any\s+nationality",
    re.I)
ADVICE_LEGAL_RE = re.compile(r"\blegal\b|should\s+i\b|allowed\s+to\s+(?:kick|evict)", re.I)
DEPOSIT_RE = re.compile(r"\bdeposit\b|\brefund\b", re.I)
# nego/cheaper/discount/lower/reduce/flexib were the original set; the rest were added
# after a batch of attack replays showed a haggle in Singlish rarely uses those exact
# words -- "waive", "lowest price", "do better", "meet halfway", "settle a number",
# "instalment", "upfront" (fee waiver bait), "agent fee"/"commission" (a direct CEA money
# question) all need the same silent-and-flag treatment.
PRICE_TRIGGER_RE = re.compile(
    r"\bnego(?:tiable|tiate)?\b|\bcheaper\b|\bdiscount\b|\blower\b|\breduce\b|flexib|"
    r"\bwaive[rd]?\b|\bwaiver\b|lowest\s+price|do\s+(?:you\s+)?better|meet\s+(?:you\s+)?halfway|"
    r"settle\s+(?:a\s+|the\s+)?(?:number|price|figure)|\bcash\b|\binstal(?:l)?ment\b|\bupfront\b|"
    r"agent\s*fee|\bcommission\b|price\s+(?:so\s+)?high|"
    r"can\s+(?:the\s+\w+\s+|they\s+|you\s+|it\s+)?(?:go|do)\s+\d|"
    # a valuation figure or a bare "will you consider" is a negotiation push (11 Sep 2026
    # cycle4 c4rm03: "if you sell below valuation, will you consider liao?" and "how much
    # below can we talk about" both slipped past the original word list untouched); a
    # deposit ask ("pay now"/"transfer"/"paynow"/"lock it in") is the same money-gate
    # territory as an explicit deposit word (c1-08: "will pay deposit now if needed").
    # 11 Sep 2026 review fix: the bare tokens \bconsider\b and \btransfer(?:red)?\b
    # swallowed ordinary tenant messages ("consider me for the room", "need to transfer
    # line" re MRT) that carry no money content at all. Scoped to actual negotiation
    # ("will you consider", "consider my offer/price/$n") and actual payment ("transfer
    # the deposit/money/payment", "bank transfer", or a same day payment promise --
    # "transfer today/tomorrow/now/tonight").
    r"\bvaluation\b|how\s+much\s+(?:below|lower|less|off)|"
    r"(?:will|would|can|could)\s+(?:you|they|the\s+\w+)\s+consider|"
    r"consider\s+(?:my\s+)?(?:offer|price|\$?\d)|"
    r"\btransfer\s+(?:the\s+)?(?:deposit|money|payment|today|tomorrow|now|tonight|\$?\d)|"
    r"bank\s+transfer|"
    r"\bpay(?:ing)?\s+now\b|\bpaynow\b|lock\s+it\s+in",
    re.I)
AGENT_RE = re.compile(
    r"co[\s-]?broke|cobroke|commission\s+split|\bera\b|propnex|orangetee|huttons|propertylimbrothers|"
    r"i'?m\s+an?\s+agent|from\s+era\b|co[\s-]?list(?:ing)?|my\s+client\s+(?:is|wants|would)|i\s+have\s+a\s+client",
    re.I)
# a request for the landlord's own contact details -- never a proposed viewing time even
# when a bare immediacy word ("now") rides along in the same message.
CONTACT_DETAIL_ASK_RE = re.compile(
    r"landlord'?s?\s+(?:number|phone|handphone|hp|mobile|contact|whatsapp|wa\b)|"
    r"owner'?s?\s+(?:number|phone|handphone|hp|mobile|contact|whatsapp)|"
    r"(?:share|give|send|get)\s+(?:me\s+|your\s+)?(?:the\s+)?(?:landlord|owner)'?s?\s+"
    r"(?:number|contact|hp\s*num(?:ber)?)|"
    # Singlish shorthand ("hp num"/"handphone number") near, not necessarily adjacent to,
    # "landlord"/"owner" (review fix, 11 Sep 2026 cycle5 c5s04: "Hi landlord can i get your
    # hp num pls, wanna come view tomorrow afternoon?" has no possessive "landlord's" so the
    # patterns above missed it and the ask got read as a viewing time instead).
    r"\b(?:landlord|owner)\b[^.?!]{0,25}\b(?:hp|handphone)\s*num(?:ber)?\b",
    re.I)


def core_stays_human(text):
    """The module agnostic half of "stays human": injection, bot check, protected
    attribute, advice/legal, deposit, price/negotiation, agent/co broke. Callers with
    their own extra fact-veto or rent+number combo (intake_engine's _FACT_VETO_RE /
    _FACT_RENT_RE) layer those on top of this, never instead of it."""
    t = text or ""
    if not t.strip():
        return False
    if INJECTION_RE.search(t) or BOT_CHECK_RE.search(t) or PROTECTED_ATTR_RE.search(t):
        return True
    if ADVICE_LEGAL_RE.search(t):
        return True
    if DEPOSIT_RE.search(t):
        return True
    if PRICE_TRIGGER_RE.search(t):
        return True
    if AGENT_RE.search(t):
        return True
    return False
