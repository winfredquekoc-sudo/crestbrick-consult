#!/usr/bin/env python3
"""
extract_viewing_windows.py -- parses every active landlord's free text viewing_availability
into structured, unambiguous weekly slots the intake engine can offer (fixed_viewing, see
intake_engine._fixed_viewing_slot / next_future_slot), so a name day and time replaces the
weak "When are you able to view?" OFFER_VIEWING fallback.

WHY: only 6 of 60 open listings carry a viewing window today; a named day and time converts
96.6% vs 30.7% for an open ask (Winfred's data). 40 landlord records carry free text like
"Viewing weekday after 3.30pm, Saturday 9 to 11am, Sunday 9am to noon" that nothing has ever
turned into a slot.

READ ONLY: this script never writes _templates/landlord-db.json or the listing index. It
prints a proposal for Winfred to review in one pass -- <out>/viewing-windows-proposed.md (a
table per listing) and <out>/viewing-windows-proposed.json (the same data, for
apply_viewing_windows.py --confirm). Nothing here is auto applied.

PARSING RULE (never invent a window): a segment of the free text counts as unambiguous only
when it names an actual weekday (or the "weekday"/"weekend" class) AND a time or time range.
Everything else -- vague text, a time with no day, a day with no time, "anytime", "by
appointment" -- is carried through as unparsed with the raw quote so Winfred decides by hand.

fixed_viewing only holds ONE weekly slot per listing (intake_engine's schema, unchanged here),
so a landlord whose text describes several distinct windows gets several CANDIDATES in the
JSON, each with "confirm": true/false -- the first (highest confidence) candidate defaults to
true, matching the table's top row; Winfred flips others to true / this one to false while
reviewing the one pass table, edits nothing else, then apply_viewing_windows.py --confirm
writes whichever candidate(s) are still confirm:true (one per listing) into the index.
"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "wa-pipeline"))
import wa_intake_paths as P  # noqa: E402

_DAY_WORDS = {
    "mon": "mon", "monday": "mon",
    "tue": "tue", "tues": "tue", "tuesday": "tue",
    "wed": "wed", "weds": "wed", "wednesday": "wed",
    "thu": "thu", "thur": "thu", "thurs": "thu", "thursday": "thu",
    "fri": "fri", "friday": "fri",
    "sat": "sat", "saturday": "sat",
    "sun": "sun", "sunday": "sun",
}
_WEEKDAY_CLASS = {
    "weekday": ["mon", "tue", "wed", "thu", "fri"], "weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "weekend": ["sat", "sun"], "weekends": ["sat", "sun"],
}
_ALL_DAY_TOKENS = sorted(list(_DAY_WORDS) + list(_WEEKDAY_CLASS), key=len, reverse=True)
_DAY_TOKEN_RE = re.compile(r"\b(" + "|".join(_ALL_DAY_TOKENS) + r")\b", re.I)
_CLASS_WORD_RE = re.compile(r"\bweekdays?\b|\bweekends?\b", re.I)

# standalone match: am/pm (or noon/midnight) is MANDATORY -- a bare number is never, on its
# own, mistaken for a time (avoids false positives on prices, ages, dates, room counts).
_TIME_TOK = r"\b\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm)\b|\bnoon\b|\bmidnight\b"
# range component: am/pm is OPTIONAL on either half ("9 to 11am", "9-11am") -- _clock() below
# borrows the other half's am/pm, and rejects the match outright if neither half ever states
# one (kept ambiguous, never guessed).
_TIME_TOK_LOOSE = r"\b\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm)?\b|\bnoon\b|\bmidnight\b"
_RANGE_RE = re.compile(rf"({_TIME_TOK_LOOSE})\s*(?:to|until|-|–)\s*({_TIME_TOK_LOOSE})", re.I)
_AFTER_RE = re.compile(rf"\bafter\s+({_TIME_TOK})", re.I)
_SINGLE_RE = re.compile(rf"({_TIME_TOK})", re.I)
_HAS_AMPM_RE = re.compile(r"am|pm", re.I)
# a segment naming an explicit calendar date ("25 Aug", "24 Aug 2026") or completed/confirmed
# past tense language is a ONE OFF log entry (a past or already booked viewing), not the
# landlord's standing weekly availability -- still shown as a candidate (the quote is real),
# but never auto confirmed as the default weekly slot (confidence forced to "low").
_LOG_ENTRY_RE = re.compile(
    r"\bcompleted\b|\bconfirmed\b|\bawaiting\s+feedback\b|\boffered\s+by\b|\blapsed\b|"
    r"\b\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b", re.I)


def _clock(tok, borrow_ap=None):
    """'3.30pm' / '9am' / 'noon' / 'midnight' -> (hour, minute), borrowing an am/pm word from
    the paired half of a range when this token did not carry its own. None if unparseable or
    genuinely ambiguous (no am/pm anywhere to borrow)."""
    t = tok.strip().lower()
    if t == "noon":
        return (12, 0)
    if t == "midnight":
        return (0, 0)
    m = re.match(r"^(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)?$", t)
    if not m:
        return None
    h = int(m.group(1))
    mm = int(m.group(2) or 0)
    ap = m.group(3) or borrow_ap
    if ap is None:
        return None
    if h == 12 and ap == "am":
        h = 0
    elif ap == "pm" and h != 12:
        h += 12
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None
    return (h, mm)


def _hm(clock):
    return f"{clock[0]:02d}:{clock[1]:02d}"


def _label_clock(clock, ap_word=True):
    h, m = clock
    h12 = h % 12 or 12
    frac = f".{m:02d}" if m else ""
    if h == 12 and m == 0:
        return f"{h12}{frac}noon" if ap_word else f"{h12}{frac}"
    if not ap_word:
        return f"{h12}{frac}"
    return f"{h12}{frac}" + ("am" if h < 12 else "pm")


def _segment_days(seg):
    """All distinct weekday tokens named in seg, in first seen order; a 'weekday'/'weekend'
    class expands to its member days (Mon-Fri / Sat-Sun) -- still an unambiguous match, just
    one that yields several single day candidates (fixed_viewing only holds one weekday)."""
    days = []
    for tok in _DAY_TOKEN_RE.findall(seg):
        t = tok.lower()
        members = [_DAY_WORDS[t]] if t in _DAY_WORDS else _WEEKDAY_CLASS.get(t, [])
        for d in members:
            if d not in days:
                days.append(d)
    return days


def _segment_time(seg):
    """Returns (start_hm, end_hm_or_None, label, confidence) for the FIRST recognised time
    expression in seg (range, then 'after X', then a bare time), or None if none is found."""
    m = _RANGE_RE.search(seg)
    if m:
        s_tok, e_tok = m.group(1), m.group(2)
        e_clock = _clock(e_tok)
        if e_clock is None:
            return None
        e_ap = "am" if e_clock[0] < 12 else "pm"
        s_borrow = None if _HAS_AMPM_RE.search(s_tok) else e_ap
        s_clock = _clock(s_tok, borrow_ap=s_borrow)
        if s_clock is None:
            return None
        s_has_ap = bool(_HAS_AMPM_RE.search(s_tok)) or s_tok.strip().lower() in ("noon", "midnight")
        label = _label_clock(s_clock, ap_word=s_has_ap) + " to " + _label_clock(e_clock, ap_word=True)
        return _hm(s_clock), _hm(e_clock), label, "high"
    m = _AFTER_RE.search(seg)
    if m:
        c = _clock(m.group(1))
        if c is None:
            return None
        return _hm(c), None, "after " + _label_clock(c, ap_word=True), "high"
    m = _SINGLE_RE.search(seg)
    if m:
        c = _clock(m.group(1))
        if c is None:
            return None
        return _hm(c), None, _label_clock(c, ap_word=True), "medium"
    return None


def parse_viewing_availability(text):
    """One landlord's free text viewing_availability -> (parsed, unparsed). parsed is a list
    of candidate slot dicts {weekday, start, end, time_label, recurring, date, source_quote,
    confidence}; unparsed is [{"raw": segment}] for every segment that named no day, no time,
    or neither -- never guessed, always carried verbatim for Winfred to read himself."""
    parsed, unparsed = [], []
    text = (text or "").strip()
    if not text:
        return parsed, unparsed
    for raw_seg in re.split(r"[;,]", text):
        seg = raw_seg.strip().strip(".")
        if not seg:
            continue
        days = _segment_days(seg)
        timeinfo = _segment_time(seg)
        if days and timeinfo:
            start_hm, end_hm, label, confidence = timeinfo
            if _CLASS_WORD_RE.search(seg):
                confidence = "medium"   # a day CLASS (weekday/weekend), not a single named day
            if _LOG_ENTRY_RE.search(seg):
                confidence = "low"     # reads like a one off past/booked viewing, not a rule
            for d in days:
                parsed.append({
                    "weekday": d, "start": start_hm, "end": end_hm,
                    "time_label": label, "recurring": True, "date": None,
                    "source_quote": seg, "confidence": confidence,
                })
        else:
            unparsed.append({"raw": seg})
    return parsed, unparsed


def _open_listing_keys(index_data):
    return {
        l.get("listing_key")
        for l in index_data.get("listings", [])
        if str(l.get("status") or "").strip().lower() in ("open", "active")
    }


def _has_fixed_viewing(listing_by_key, lk):
    l = listing_by_key.get(lk) or {}
    return bool(l.get("fixed_viewing") or (l.get("requirements", {}) or {}).get("fixed_viewing"))


def build_proposals(index_data, landlord_data):
    """Pure planning pass over already loaded index/landlord data (injectable for tests).
    Returns {listing_key: {landlord, id, status, raw_text, parsed, unparsed}} for every
    ACTIVE landlord with an OPEN listing that does not already carry a fixed_viewing slot."""
    open_keys = _open_listing_keys(index_data)
    listing_by_key = {l.get("listing_key"): l for l in index_data.get("listings", [])}
    out = {}
    for ll in landlord_data.get("landlords", []):
        lk = ll.get("listing_key")
        if not lk or lk not in open_keys:
            continue
        if str(ll.get("status") or "").strip().lower().startswith("closed"):
            continue
        if _has_fixed_viewing(listing_by_key, lk):
            continue   # already funnelled to one weekly time, nothing to propose
        raw_text = (ll.get("viewing_availability") or "").strip()
        parsed, unparsed = parse_viewing_availability(raw_text)
        if not raw_text:
            unparsed = [{"raw": "(no viewing_availability text on file)"}]
        # default confirm: true on the single highest confidence candidate only (one weekly
        # slot per listing) -- Winfred flips this while reviewing the table, nothing else.
        # "low" (a one off dated/completed viewing, not a standing rule) is NEVER auto
        # confirmed, even if it is the only candidate -- Winfred must pick by hand.
        _rank = {"high": 2, "medium": 1, "low": 0}
        best = None
        for cand in parsed:
            cand["confirm"] = False
            if best is None or _rank[cand["confidence"]] > _rank[best["confidence"]]:
                best = cand
        if best is not None and best["confidence"] != "low":
            best["confirm"] = True
        out[lk] = {
            "landlord": ll.get("landlord_name") or "",
            "id": ll.get("id") or "",
            "status": ll.get("status") or "",
            "raw_text": raw_text,
            "parsed": parsed,
            "unparsed": unparsed,
        }
    return out


def _render_markdown(proposals):
    lines = [
        "# Viewing windows proposed from landlord free text -- review in one pass",
        "",
        "Confirm by flipping `confirm` in the JSON (default: the top/highest confidence "
        "candidate per listing), then run `apply_viewing_windows.py --confirm "
        "viewing-windows-proposed.json`.",
        "",
        "| listing_key | landlord | proposed slot(s) | source quote | confidence |",
        "|---|---|---|---|---|",
    ]
    for lk in sorted(proposals):
        p = proposals[lk]
        if p["parsed"]:
            slot_cell = "<br>".join(
                f"{'[confirm] ' if c['confirm'] else ''}{c['weekday'].title()} {c['time_label']}"
                for c in p["parsed"])
            quote_cell = "<br>".join(c["source_quote"] for c in p["parsed"])
            conf_cell = "<br>".join(c["confidence"] for c in p["parsed"])
        else:
            slot_cell = "(none parsed)"
            quote_cell = "<br>".join(u["raw"] for u in p["unparsed"]) or "(no text)"
            conf_cell = "unparsed"
        landlord_cell = f"{p['landlord']} ({p['id']})" if p["id"] else p["landlord"]
        lines.append(f"| {lk} | {landlord_cell} | {slot_cell} | {quote_cell} | {conf_cell} |")
    lines.append("")
    lines.append(f"Listings with a parsed window: "
                 f"{sum(1 for p in proposals.values() if p['parsed'])} / {len(proposals)}")
    unparsed_only = [lk for lk, p in proposals.items() if not p["parsed"]]
    if unparsed_only:
        lines.append("")
        lines.append("## Unparsed only (no candidate slot -- raw text below)")
        for lk in sorted(unparsed_only):
            p = proposals[lk]
            raws = "; ".join(u["raw"] for u in p["unparsed"]) or "(no text)"
            lines.append(f"- **{lk}** ({p['landlord']}): {raws}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=None,
                     help="listing index JSON (default: resolved via wa_intake_paths, "
                          "i.e. WA_INTAKE_STATE_ROOT/listing-index.json if set)")
    ap.add_argument("--landlord-db", default=None,
                     help="landlord db JSON (default: resolved via wa_intake_paths)")
    ap.add_argument("--out-md", default=None, help="markdown output path")
    ap.add_argument("--out-json", default=None, help="JSON output path")
    args = ap.parse_args(argv)

    paths = P.paths()
    index_path = args.index or paths["listing_index"]
    landlord_path = args.landlord_db or paths["landlord_db"]
    scratch = "/private/tmp/claude-501/-Users-winfredquek-crestbrick-consult/" \
              "92b405a9-4a65-471d-bd8e-97356c5a5b42/scratchpad"
    out_md = args.out_md or os.path.join(scratch, "viewing-windows-proposed.md")
    out_json = args.out_json or os.path.join(scratch, "viewing-windows-proposed.json")

    index_data = json.load(open(index_path))
    landlord_data = json.load(open(landlord_path))
    proposals = build_proposals(index_data, landlord_data)

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w") as f:
        f.write(_render_markdown(proposals))
    with open(out_json, "w") as f:
        json.dump(proposals, f, indent=2, ensure_ascii=False)

    parsed_n = sum(1 for p in proposals.values() if p["parsed"])
    print(f"{len(proposals)} listing(s) considered; {parsed_n} got a parsed window, "
          f"{len(proposals) - parsed_n} unparsed only.")
    print(f"Markdown: {out_md}")
    print(f"JSON:     {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
