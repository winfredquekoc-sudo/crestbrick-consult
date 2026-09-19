"""
wa_intake_listing_match.py -- match_listing() and its keyword/fallback ranking helpers,
split out of wa_intake_runner.py (9 Sep 2026 merge review) to keep that file under the
repo's 500 line guideline. Pure move, byte identical logic -- re-imported straight back
into wa_intake_runner's namespace so every existing call site (including tests that
reach these via wa_intake_runner.<name>) keeps working unchanged.
"""
import re
import intake_engine as E

def _listing_open(l):
    st = str((l or {}).get("status", "")).lower()
    return not (st.startswith("closed") or st == "hold")

# a BLOCK/UNIT number specifically -- not just any digit in the keyword (a plain avenue/street
# number like "ave 10" is not a block number and must not tie with a real "blk 405" hit).
_KW_HAS_NUM_RE = re.compile(r"\bblk\.?\s*\d+|\bblock\s*\d+|#\d+", re.I)

_FALLBACK_POSTAL_RE = re.compile(r"\b(\d{6})\b")
_FALLBACK_PAREN_RE = re.compile(r"\(([^)]*)\)")

_TOK_RE_CACHE = {}

def _tok_re(tok):
    """Word bounded matcher for a tier 0 fallback token, cached per token. A tier 0 token is
    a PARSED word (a project name, a distinctive word), never a token Winfred configured
    himself (that is pg_url_keywords, tier 1/2) -- a raw 'tok in text' substring check lets a
    real word swallow it whole ('pending' inside 'depending', 'ea' inside 'lease'/'area'/
    'overseas'/'earlier' before the length filter above), silently autobinding a cold chat to
    a real listing it never mentioned (P3 fix, 11 Sep 2026 pkg B)."""
    p = _TOK_RE_CACHE.get(tok)
    if p is None:
        p = re.compile(r"\b" + re.escape(tok) + r"\b")
        _TOK_RE_CACHE[tok] = p
    return p

# generic Singapore condo/estate suffix words -- never distinctive enough to stand alone as
# a fallback keyword (a listing whose project name is just "X Park" must not bind off any
# message that happens to say "park").
_FALLBACK_GENERIC_WORDS = {
    "park", "court", "garden", "gardens", "view", "views", "heights", "residence",
    "residences", "residency", "tower", "towers", "place", "walk", "green", "greens",
    "hill", "hills", "rise", "gate", "vale", "mansion", "mansions", "house", "apartments",
    "apartment", "condo", "condominium", "hdb", "block", "blk", "road", "street", "avenue",
    "ave", "drive", "close", "crescent", "terrace", "lane", "the", "singapore", "estate",
    "suites", "suite", "common", "master", "studio", "spacious", "bedroom", "bedrooms",
    "corner", "premium", "shared", "rental", "rented", "tenant", "tenants", "landlord",
}

def _fallback_distinctive_word(name):
    best = ""
    for w in re.findall(r"[a-zA-Z]+", name or ""):
        wl = w.lower()
        if len(wl) >= 6 and wl not in _FALLBACK_GENERIC_WORDS and len(wl) > len(best):
            best = wl
    return best

def _fallback_tokens(l):
    """Lower specificity tokens derived from block_address / property_name (postal code,
    block+street, condo/project name) -- a listing whose pg_url_keywords is empty is
    otherwise structurally unbindable from inbound text at all (P1 fix, 9 Sep 2026 cycle5
    hg5-03: 12 index rows currently have empty pg_url_keywords), and even a listing WITH
    keywords can carry ones too specific for how a tenant actually phrases it ('Bayshore'
    vs the keyword 'blk 62 bayshore', c5rm03). Never outranks a real pg_url_keywords hit
    (see _match_pass tier 0 vs 1/2); a tie among fallback hits still returns None."""
    addr = str(l.get("block_address") or "")
    name = str(l.get("property_name") or "")
    toks = []
    pm = _FALLBACK_POSTAL_RE.search(addr) or _FALLBACK_POSTAL_RE.search(name)
    if pm:
        toks.append(pm.group(1))
    paren = _FALLBACK_PAREN_RE.search(addr) or _FALLBACK_PAREN_RE.search(name)
    if paren:
        first = paren.group(1).split(",")[0].strip().lower()
        # a real project name in parens ("High Oak Condo") is always several letters long;
        # a bare unit type marker ("EA", "MBR", "WC") is 2-3 letters and, worse, a raw
        # substring of ordinary English words ("EA" inside lease/area/overseas/earlier) --
        # exactly the false autobind the attack replay caught (P3 fix, 11 Sep 2026 pkg B,
        # findings unbound-chat-autobound-to-real-listing and 3 siblings).
        if len(first) >= 4:
            toks.append(first)
    main = _FALLBACK_PAREN_RE.sub(" ", addr)
    main = re.sub(r"#.*", "", main)
    main = re.sub(r"^\s*(blk|block)\.?\s+", "", main, flags=re.I)
    main = main.split(",")[0].strip()
    m = re.match(r"(\d+[a-z]?)\s+(.+)", main, re.I)
    if m:
        toks.append((m.group(1) + " " + m.group(2)).lower())
    # the distinctive-word scan never looks inside parens -- that content is either a
    # descriptive note ("HDB common room") or already captured whole by the paren phrase
    # extraction above (a real project name, "High Oak Condo"); scanning it word by word too
    # is what let a generic word like "common" leak out as its own fallback token.
    dw = (_fallback_distinctive_word(_FALLBACK_PAREN_RE.sub(" ", name))
          or _fallback_distinctive_word(_FALLBACK_PAREN_RE.sub(" ", addr)))
    if dw:
        toks.append(dw)
    return [t for t in toks if t]

def _match_pass(pool, t):
    """Rank hits within ONE pool (open, or closed): a keyword carrying a number (a block or
    street number) ranks above a bare street-name-only keyword hit -- a same-street listing
    with a DIFFERENT block must never silently outrank the block the tenant actually named
    (P2 fix, 9 Sep 2026 cycle4 hg4-03: a same-street keyword on a closed listing beat the
    block number the tenant actually stated, silently binding to the wrong unit). Two
    listings tied at the SAME best tier are genuinely ambiguous -- return None so the
    caller's own needs_listing disambiguation takes over, rather than silently picking one
    (and possibly auto closing the thread as "listing closed" on a guess). A listing with no
    real keyword hit falls back to tier 0 (_fallback_tokens) -- always dominated by a real
    hit elsewhere, so this only ever resolves an otherwise dead enquiry, never overrides one."""
    best_tier, hits = -1, []
    for l in pool:
        tier = -1
        for kw in (l.get("pg_url_keywords") or []):
            if kw and kw.lower() in t:
                tier = max(tier, 2 if _KW_HAS_NUM_RE.search(kw) else 1)
        if tier < 0:
            for kw in _fallback_tokens(l):
                if kw and _tok_re(kw).search(t):
                    tier = 0
                    break
        if tier < 0:
            continue
        if tier > best_tier:
            best_tier, hits = tier, [l]
        elif tier == best_tier:
            hits.append(l)
    if len(hits) == 1:
        return hits[0]["listing_key"]
    return None

def match_listing(text, reqs=None):
    """A4 (Sep 2026): a stale keyword can survive on a CLOSED index row that also matches a
    live OPEN one (the review found "ang mo kio ave 3" on both) -- OPEN listings are always
    matched first, in TWO passes, so match order never depends on dict iteration order.
    A CLOSED listing is only ever returned when nothing OPEN matches. Within each pass, a
    tie at the same specificity tier (see _match_pass) returns None rather than guessing."""
    t = (text or "").lower()
    listings = list((reqs if reqs is not None else E.listing_reqs()).values())
    # INTENT GATE (P3 fix, 11 Sep 2026 pkg B): a sale shaped message must never bind to a
    # rent listing and a rent shaped one must never bind to a sale listing (attack finding
    # buyer-enquiry-bound-to-unrelated-live-rental-listing). Only a DECISIVE signal gates --
    # classify_transaction's own lowest tier is a bare keyword ("lease", "investment") that
    # is too easily an offhand remark inside an otherwise ordinary enquiry; gating on that
    # tier would wrongly exclude every listing of one type from a normal message that merely
    # mentions the other word once.
    txn, txn_reason = E.classify_transaction(text)
    if txn in ("rent", "sale") and "keyword" not in txn_reason:
        opposite = "sale" if txn == "rent" else "rent"
        listings = [l for l in listings if (l.get("deal_type") or "") != opposite]
    r = _match_pass([l for l in listings if _listing_open(l)], t)
    if r:
        return r
    return _match_pass([l for l in listings if not _listing_open(l)], t)
