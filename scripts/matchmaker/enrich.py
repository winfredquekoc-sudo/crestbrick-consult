#!/usr/bin/env python3
"""Shared, side effect free helpers for the matchmaker data lane (export_data.py,
queue_drafts.py). Nothing here reads real paths at import time — every function
takes its inputs explicitly so tests can call these with fixtures. stdlib only,
must run under /usr/bin/python3."""
import json, os, re, sqlite3, datetime

MONTHS = {m[:3].lower(): i + 1 for i, m in enumerate([
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"])}

AGENT_MARKERS_DEFAULT = ["agent", "propnex", "era", "huttons", "orangetee", "realtor"]
LICENSE_RE = re.compile(r"\bR\d{6}[A-Z]\b", re.I)

CJK_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))


# ---------------------------------------------------------------- dates ----
def norm_date(raw):
    """Best effort YYYY-MM-DD normalizer. Unparseable input -> None, never raises."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)  # YYYY-MM-DD or YYYY-MM-DDT...
    if m:
        try:
            datetime.date.fromisoformat(m.group(1))
            return m.group(1)
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)  # DD/MM/YYYY or DD-MM-YYYY
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return datetime.date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


IMMEDIATE_RE = re.compile(r"^(?:immediately|asap|now|anytime)\b", re.I)
# "start"/"middle"/"end" accepted as everyday synonyms for early/mid/late —
# real tenant-db data uses "End of September 2026" and "end aug", not the
# word "late" itself.
MOVE_IN_QUALIFIER_DAY = {"early": 5, "start": 5, "mid": 15, "middle": 15, "late": 25, "end": 25}
MOVE_IN_QUALIFIER_RE = re.compile(
    r"\b(early|start|mid|middle|late|end)(?:\s+of)?\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?\b", re.I)
MOVE_IN_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?\b", re.I)
MOVE_IN_MONTH_YEAR_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b", re.I)
MOVE_IN_BARE_MONTH_RE = re.compile(r"^([A-Za-z]{3,9})$", re.I)
MOVE_IN_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def _move_in_year(today, month, day, year_tok):
    if year_tok:
        return int(year_tok)
    # No year written -- assume this year, same rollover rule as
    # find_available_from(): only jump to next year if that reading would
    # otherwise land far in the past (a bare "3 aug" read in November should
    # not resolve to a move in date 9 months ago).
    try:
        candidate = datetime.date(today.year, month, day)
    except ValueError:
        return today.year
    return today.year + 1 if (today - candidate).days > 200 else today.year


def norm_move_in(raw, today):
    """Best effort tenant move_in normalizer -> YYYY-MM-DD, or None if nothing
    recognisable (caller keeps the raw string and today's default scoring
    behavior in that case -- this never guesses). Checked in order:
      1. norm_date() -- an already clean YYYY-MM-DD / DD-MM-YYYY passes straight through.
      2. "immediately"/"asap"/"now"/"anytime" -> today (the build date).
      3. "early/mid/late <Month> [Year]" (start/middle/end accepted too) -> 5th/15th/25th.
      4. "D Month [Year]" -- an explicit day, just needs the month name parsed.
      5. "Month Year" / bare "Month" / bare "YYYY-MM" -- no day given -> the
         15th, the least wrong single day guess for a month only signal.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    clean = norm_date(s)
    if clean:
        return clean

    if IMMEDIATE_RE.match(s):
        return today.isoformat()

    def month_num(tok):
        return MONTHS.get(tok.strip().lower()[:3])

    m = MOVE_IN_QUALIFIER_RE.search(s)
    if m:
        month = month_num(m.group(2))
        if month:
            day = MOVE_IN_QUALIFIER_DAY[m.group(1).lower()]
            try:
                return datetime.date(_move_in_year(today, month, day, m.group(3)), month, day).isoformat()
            except ValueError:
                pass

    m = MOVE_IN_DAY_MONTH_RE.search(s)
    if m:
        day = int(m.group(1))
        month = month_num(m.group(2))
        if month and 1 <= day <= 31:
            try:
                return datetime.date(_move_in_year(today, month, day, m.group(3)), month, day).isoformat()
            except ValueError:
                pass

    m = MOVE_IN_MONTH_YEAR_RE.search(s)
    if m:
        month = month_num(m.group(1))
        if month:
            try:
                return datetime.date(int(m.group(2)), month, 15).isoformat()
            except ValueError:
                pass

    m = MOVE_IN_BARE_MONTH_RE.match(s)
    if m:
        month = month_num(m.group(1))
        if month:
            try:
                return datetime.date(_move_in_year(today, month, 15, None), month, 15).isoformat()
            except ValueError:
                pass

    m = MOVE_IN_YEAR_MONTH_RE.match(s)
    if m and 1 <= int(m.group(2)) <= 12:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), 15).isoformat()
        except ValueError:
            pass

    return None


def find_available_from(texts, today):
    """Scan free text fields (list, checked in order) for 'available from D Mon'
    style phrasing. Regex only — no match anywhere -> None, never a guess."""
    pats = [
        r"available\s+from\s+(\d{1,2})(?:/\d{1,2})?\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?",
        r"avail(?:able)?\.?\s+(\d{1,2})(?:/\d{1,2})?\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?",
        r"available\s+([A-Za-z]{3,9})\s+(\d{4})",
        r"\bfrom\s+(\d{1,2})(?:/\d{1,2})?\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?",
    ]
    for text in texts:
        if not text:
            continue
        for i, pat in enumerate(pats):
            m = re.search(pat, text, re.I)
            if not m:
                continue
            if i == 2:  # "available August 2026" — no day given, default to the 1st
                mon_tok, year = m.group(1), int(m.group(2))
                day = 1
            else:
                day = int(m.group(1))
                mon_tok = m.group(2)
                year = int(m.group(3)) if m.group(3) else today.year
            month = MONTHS.get(mon_tok.strip().lower()[:3])
            if not month:
                continue
            try:
                d = datetime.date(year, month, day)
            except ValueError:
                continue
            # a bare "D Mon" with no year rolls to next year only if it would
            # otherwise land far in the past (e.g. "from 1 Jan" read in November)
            year_was_assumed = (i != 2) and not m.group(3)
            if year_was_assumed and (today - d).days > 200:
                try:
                    d = datetime.date(year + 1, month, day)
                except ValueError:
                    pass
            return d.isoformat()
    return None


# --------------------------------------------------------------- phones ----
def normalize_phone(raw):
    p = re.sub(r"[^0-9]", "", raw or "")
    if len(p) == 8 and p[0] in "89":
        p = "65" + p
    return p


# --------------------------------------------------------------- budget ----
_NUM = r"S?\$\s?([\d,]+(?:\.\d+)?\s?k?)"
RANGE_RE = re.compile(_NUM + r"\s*(?:to|-|–|~)\s*S?\$?\s?([\d,]+(?:\.\d+)?\s?k?)", re.I)
SINGLE_RE = re.compile(_NUM, re.I)


def _tok_to_num(tok):
    t = tok.strip().lower().replace(",", "").replace(" ", "")
    try:
        if t.endswith("k"):
            return int(round(float(t[:-1]) * 1000))
        return int(round(float(t)))
    except ValueError:
        return None


def recover_budget(texts):
    """texts: list of free text fields to scan, in priority order. Requires a $
    sign so we never mistake a block/unit number for a rent figure. Returns
    (budget_min, budget_max, budget_note) or (None, None, None)."""
    for text in texts:
        if not text:
            continue
        m = RANGE_RE.search(text)
        if m:
            a, b = _tok_to_num(m.group(1)), _tok_to_num(m.group(2))
            if a is not None and b is not None and 300 <= a <= 20000 and 300 <= b <= 20000:
                lo, hi = min(a, b), max(a, b)
                return lo, hi, "parsed from " + m.group(1).strip() + " to " + m.group(2).strip()
        m = SINGLE_RE.search(text)
        if m:
            n = _tok_to_num(m.group(1))
            if n is not None and 300 <= n <= 20000:
                return n, n, "parsed from $" + m.group(1).strip()
    return None, None, None


# ------------------------------------------------------------ agent flag ---
def is_agent_suspect(name, markers=None):
    markers = markers if markers is not None else AGENT_MARKERS_DEFAULT
    n = (name or "")
    if LICENSE_RE.search(n):
        return True
    nl = n.lower()
    return any(re.search(r"\b" + re.escape(mk.lower()) + r"\b", nl) for mk in markers if mk)


# ------------------------------------------------------------- language ----
def _is_cjk(ch):
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in CJK_RANGES)


def _cjk_ratio(text):
    letters = [c for c in text if c.isalpha() or _is_cjk(c)]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _is_cjk(c)) / len(letters)


def detect_lang(inbound_texts):
    """inbound_texts: latest-first list of up to 3 inbound message bodies."""
    if not inbound_texts:
        return "en"
    predominant = sum(1 for t in inbound_texts if _cjk_ratio(t) > 0.3)
    return "zh" if predominant > len(inbound_texts) / 2 else "en"


# ------------------------------------------------------------- units[] -----
# "room" is deliberately separate from the specific labels: "Common room $1,200"
# should read as one "common" unit, not fragment into a same-position "room" match
# competing with "common" for the same price. The generic label only kicks in when
# no specific label appears anywhere in the text.
SPECIFIC_UNIT_LABELS = [
    (re.compile(r"\bwhole\s*(?:flat|unit|house)\b", re.I), "whole"),
    (re.compile(r"\bstudio\b", re.I), "studio"),
    (re.compile(r"\bmaster\b", re.I), "master"),
    # room codes landlords use as shorthand (e.g. LL088 "PR1 ... $800; CR3 ... $1,550"):
    # MBR = master bedroom, PR/CR/SC = pocket/common/small-common (all common-type).
    (re.compile(r"\bMBR\d*\b", re.I), "master"),
    (re.compile(r"\b(?:PR|CR|SC)\d+\b", re.I), "common"),
    (re.compile(r"\bcommon\b", re.I), "common"),
]
GENERIC_UNIT_LABEL = (re.compile(r"\broom\b", re.I), "room")
PRICE_RE = re.compile(r"\$\s?([\d,]+)")
UNIT_WINDOW = 60  # chars scanned after a label for that unit's price(s)


def _collect_unit_prices(text, label_patterns):
    """Every label occurrence (any type in label_patterns) gets a price search
    window bounded by the START of the NEXT label occurrence (so e.g. a Common
    price can never bleed into a following Master's price) or UNIT_WINDOW chars,
    whichever is smaller. All prices found in that window count for that label
    (handles '$1,400 (1pax) / $1,500 (2pax)' as one unit's rent range)."""
    occurrences = []
    for label_re, unit_type in label_patterns:
        for lm in label_re.finditer(text):
            occurrences.append((lm.start(), lm.end(), unit_type))
    occurrences.sort(key=lambda o: o[0])
    found = {}
    for i, (_start, end, unit_type) in enumerate(occurrences):
        next_start = occurrences[i + 1][0] if i + 1 < len(occurrences) else len(text)
        window = text[end:min(end + UNIT_WINDOW, next_start)]
        for pm in PRICE_RE.finditer(window):
            n = _tok_to_num(pm.group(1))
            if n is not None and 200 <= n <= 30000:
                found.setdefault(unit_type, []).append(n)
    return found


def parse_units(rooms_and_rent, property_type, rent_min, rent_max):
    """Best effort extraction of {unit_type, rent_min, rent_max} entries from the
    free text landlords actually type (see recon: highly inconsistent). Always
    returns >=1 entry; falls back to a single unit mirroring the listing's own
    rent range when nothing recognisable is found."""
    text = rooms_and_rent or ""
    found = _collect_unit_prices(text, SPECIFIC_UNIT_LABELS)
    if not found:
        found = _collect_unit_prices(text, [GENERIC_UNIT_LABEL])
    if found:
        units = []
        for unit_type, prices in found.items():
            units.append({"unit_type": unit_type, "rent_min": min(prices), "rent_max": max(prices)})
        return units
    pt = (property_type or "").lower()
    if "whole" in pt:
        fallback_type = "whole"
    elif "studio" in pt:
        fallback_type = "studio"
    elif "master" in pt:
        fallback_type = "master"
    elif "common" in pt:
        fallback_type = "common"
    else:
        fallback_type = "room"
    return [{"unit_type": fallback_type, "rent_min": rent_min, "rent_max": rent_max}]


# --------------------------------------------------------- addr / rent -----
def normalize_address(addr):
    a = re.sub(r"[^a-z0-9]+", " ", (addr or "").lower())
    return re.sub(r"\s+", " ", a).strip()


def rent_overlaps(a_min, a_max, b_min, b_max):
    if a_min is None and a_max is None:
        return True  # no price to disprove overlap with — lean on the address match
    if b_min is None and b_max is None:
        return True
    lo_a, hi_a = (a_min if a_min is not None else a_max), (a_max if a_max is not None else a_min)
    lo_b, hi_b = (b_min if b_min is not None else b_max), (b_max if b_max is not None else b_min)
    return lo_a <= hi_b and lo_b <= hi_a


# -------------------------------------------------------- listings.json ----
def abs_url(u, base="https://winfredquek.com"):
    if not u:
        return None
    u = str(u).strip()
    if not u:
        return None
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/"):
        return base + u
    return None


def load_photo_url_index(listings_json_path):
    """LLxxx (upper) -> {photos: [...]|None, listing_url: str|None}. Recon Q10:
    listings.json id slugs always end in the lowercased landlord id, e.g.
    '...-ll001'; that suffix is the only join key we trust."""
    if not listings_json_path or not os.path.exists(listings_json_path):
        return {}
    try:
        d = json.load(open(listings_json_path))
    except (OSError, ValueError):
        return {}
    by_ll = {}
    for entry in d.get("listings") or []:
        eid = (entry.get("id") or "").lower()
        m = re.search(r"-(ll\d+)$", eid)
        if not m:
            continue
        llid = m.group(1).upper()
        by_ll.setdefault(llid, []).append(entry)
    index = {}
    for llid, entries in by_ll.items():
        photos = []
        for e in entries:
            u = abs_url(e.get("image"))
            if u and u not in photos:
                photos.append(u)
        listing_url = None
        for e in entries:
            listing_url = abs_url(e.get("url"))
            if listing_url:
                break
        index[llid] = {"photos": photos or None, "listing_url": listing_url}
    return index


def load_fixed_viewing_index(listing_index_path):
    """listing_key -> fixed_viewing dict, per listing-templates/listing-index.json."""
    if not listing_index_path or not os.path.exists(listing_index_path):
        return {}
    try:
        d = json.load(open(listing_index_path))
    except (OSError, ValueError):
        return {}
    out = {}
    for entry in d.get("listings") or []:
        fv = entry.get("fixed_viewing") or (entry.get("requirements") or {}).get("fixed_viewing")
        if fv:
            out[entry.get("listing_key")] = fv
    return out


# ------------------------------------------------------------ WA bridge ----
def open_wa_bridge(db_path):
    """Read only connection, or None if the bridge is absent/locked. Never raises."""
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True, timeout=3)
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("SELECT 1").fetchone()
        return conn
    except sqlite3.Error:
        return None


def _norm_ts(raw):
    if not raw:
        return None
    s = str(raw).strip()
    return s.replace(" ", "T", 1) if "T" not in s and " " in s else s


def fetch_wa_info(conn, jid):
    """Returns (last_wa dict|None, lang 'en'|'zh') for one jid. conn may be None
    (bridge unavailable) -- always degrades to (None, 'en'), never raises."""
    if not conn or not jid:
        return None, "en"
    last_wa = None
    try:
        row = conn.execute(
            "SELECT content, timestamp, is_from_me FROM messages "
            "WHERE chat_jid=? AND content IS NOT NULL AND content!='' "
            "ORDER BY timestamp DESC LIMIT 1", (jid,)).fetchone()
        if row:
            content, ts, from_me = row
            last_wa = {"ts": _norm_ts(ts), "from_me": bool(from_me), "snippet": (content or "")[:80]}
    except sqlite3.Error:
        pass
    inbound = []
    try:
        rows = conn.execute(
            "SELECT content FROM messages WHERE chat_jid=? AND is_from_me=0 "
            "AND content IS NOT NULL AND content!='' ORDER BY timestamp DESC LIMIT 3", (jid,)).fetchall()
        inbound = [r[0] for r in rows]
    except sqlite3.Error:
        pass
    return last_wa, detect_lang(inbound)


if __name__ == "__main__":
    raise SystemExit("enrich.py is a helper module, not an entry point")
