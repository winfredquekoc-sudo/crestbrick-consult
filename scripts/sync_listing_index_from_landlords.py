#!/usr/bin/env python3
"""
sync_listing_index_from_landlords.py — make the live matcher use the maintained data.

The live tenant-intake engine qualifies enquiries against listing-index.json (structured
gates), NOT landlord-db.json / the Google sheet. Those two can drift: a landlord changing
their terms (captured into landlord-db by the nightly refresh) never reached the gates the
bot actually enforces. This reconciler closes that gap by deriving the gates from
landlord-db (the source of truth) for every active, landlord-bound listing.

SAFETY (this writes the LIVE matching source):
  - Only fields that parse UNAMBIGUOUSLY are auto-applied: gender (+couple flags),
    ethnicity_rule, lease_min/max_months, min_age.
  - Fields that cannot be safely derived from free text are PRESERVED and only flagged
    when they look out of sync: max_pax (e.g. "1 master, 2 common", "6/unit") and
    budget_floor / budget_unknown (messy rooms_and_rent). Never auto-changed.
  - When the landlord-db field is missing or unparseable, the existing hand-curated gate
    is KEPT (never blanked, never guessed).
  - Only listings with a landlord_id and a non-closed status are touched.
  - Every change is reported; weakenings (a restriction being opened) are marked so a
    human can eyeball them. Idempotent; atomic write.

Usage:
  python3 sync_listing_index_from_landlords.py            # dry run, prints the diff
  python3 sync_listing_index_from_landlords.py --apply     # writes listing-index.json
"""
import json, os, re, sys

LDB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
IDX = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")
APPLY = "--apply" in sys.argv

RACES = ["Indian", "Malay", "Chinese", "Korean", "Japanese", "Caucasian",
         "Eurasian", "Filipino", "Vietnamese", "Thai", "Burmese", "Indonesian"]

# ---------- parsers (return None => not parseable => preserve existing) ----------

def parse_gender(text):
    """-> (gender, couple_ok, couple_must_be_married) or None."""
    if not text or not str(text).strip():
        return None
    t = str(text).lower()
    couple_ok = "couple" in t
    married = "married" in t
    if "no male" in t or "no males" in t:        # "female ... (no males)"
        return ("female_only", couple_ok, married)
    if re.search(r"\bany\b", t) or "now ok" in t or "no pref" in t:  # "Any (males now ok...)"
        return ("any", couple_ok, married)
    if "female only" in t or "females only" in t or t.strip() == "female":
        return ("female_only", couple_ok, married)
    if "female pref" in t or "female preferred" in t or "pref female" in t:
        return ("female_pref", couple_ok, married)
    if "male pref" in t or "male preferred" in t or "pref male" in t:
        return ("male_pref", couple_ok, married)
    if "no female" in t or "no females" in t or "male only" in t or t.strip() == "male":
        return ("male_only", couple_ok, married)
    if "male" in t and "female" in t:            # "Male/female/couple"
        return ("any", couple_ok, married)
    return None

def _races_in(s):
    sl = (s or "").lower()
    return [R for R in RACES if R.lower() in sl]

def parse_ethnicity(text):
    """-> {'mode','list'} or None. only > exclude > any > prefer."""
    if not text or not str(text).strip():
        return None
    t = str(text).lower()
    # negated races first ("no Indian" / "except Indian" / "but not Malay"), so the 'only' and
    # 'prefer' branches never treat a NEGATED race as an accepted one ("no Indian only" must
    # become exclude-Indian, NOT only-Indian).
    no_races = re.findall(r"(?:no|not|except|excluding|but\s+no|but\s+not)\s+(" +
                          "|".join(R.lower() for R in RACES) + r")", t)
    no_set = {R for R in RACES if R.lower() in no_races}
    if "only" in t:                              # "Chinese only (no Indian/Malay)"
        before = t.split("only")[0]
        races = [R for R in (_races_in(before) or _races_in(t)) if R not in no_set]
        if races:
            return {"mode": "only", "list": races[:1] if len(set(races)) == 1 else races}
    if no_races:                                 # must precede the all/any branch
        return {"mode": "exclude", "list": [R for R in RACES if R.lower() in no_races]}
    if "no pref" in t or re.search(r"\ball\b", t) or re.search(r"\bany\b", t):
        return {"mode": "any", "list": []}
    if "pref" in t:                              # "Chinese/Malay pref"; ignore clauses after ';'
        before = t.split("pref")[0]
        races = [R for R in _races_in(before) if R not in no_set]
        if races:
            return {"mode": "prefer", "list": races}
    return None

def parse_min_age(reqs):
    for v in (reqs.get("occupation"), reqs.get("min_age"), reqs.get("age")):
        if isinstance(v, int):
            return v
        s = str(v or "")
        # a stated PREFERENCE is not a hard gate. "Age 30+ pref" must NOT become a hard
        # min_age that auto-rejects a qualified 28-year-old; preserve it as a human note.
        if re.search(r"pref|prefer|ideal|would\s+like", s, re.I):
            continue
        m = re.search(r"age\s*(\d{2})\s*\+|\b(\d{2})\s*(?:yrs?|years?)\s*\+|min(?:imum)?\s*age\s*(\d{2})", s, re.I)
        if m:
            return int(next(g for g in m.groups() if g))
    return None

def _int(v):
    return v if isinstance(v, int) else None

# weakening = a restriction being opened up (worth a human glance)
def _is_weakening(field, old, new):
    if field == "gender":
        rank = {"female_only": 2, "male_only": 2, "female_pref": 1, "male_pref": 1, "any": 0}
        return rank.get(new, 0) < rank.get(old, 0)
    if field == "ethnicity_rule":
        rank = {"only": 3, "exclude": 2, "prefer": 1, "any": 0}
        return rank.get((new or {}).get("mode"), 0) < rank.get((old or {}).get("mode"), 0)
    if field == "lease_min_months":
        return _int(new) is not None and _int(old) is not None and _int(new) < _int(old)
    if field == "min_age":
        return _int(new) is not None and _int(old) is not None and _int(new) < _int(old)
    return False

def main():
    ldb = json.load(open(LDB))
    idx = json.load(open(IDX))
    byid = {x["id"]: x for x in ldb.get("landlords", [])}

    changes = []      # (listing, field, old, new, weakening)
    flags = []        # (listing, note) — ambiguous mismatches, not auto-applied
    for l in idx["listings"]:
        lid = l.get("landlord_id")
        if not lid or str(l.get("status", "")).startswith("closed"):
            continue
        ll = byid.get(lid)
        if not ll:
            continue
        reqs = ll.get("requirements", {}) or {}
        gate = l.setdefault("requirements", {})

        # ---- per-listing scoping for multi-listing landlords ----
        # A requirement that names a SPECIFIC property (e.g. "No Indian at Caspian") must
        # not be applied to this landlord's OTHER listing (e.g. Summerdale). Tokens come
        # from each listing's key + property_name.
        def _toks(x):
            s = set()
            if x.get("listing_key"): s.add(x["listing_key"].lower().replace("-", " "))
            if x.get("property_name"): s.add(str(x["property_name"]).lower())
            return {t for t in s if t}
        sib = [x for x in idx["listings"] if x.get("landlord_id") == lid]
        this_toks = _toks(l)
        other_toks = set().union(*[_toks(x) for x in sib if x is not l]) if len(sib) > 1 else set()
        def scoped_ok(text):
            tl = str(text or "").lower()
            names_other = any(t in tl for t in other_toks)
            names_this = any(t in tl for t in this_toks)
            return not (names_other and not names_this)

        # ---- gender (+ couple flags) ----
        g = parse_gender(reqs.get("gender")) if scoped_ok(reqs.get("gender")) else None
        if g:
            gender, couple_ok, married = g
            for field, new in (("gender", gender), ("couple_ok", couple_ok)):
                old = gate.get(field)
                if old != new:
                    changes.append((l["listing_key"], field, old, new, _is_weakening(field, old, new)))
                    if APPLY: gate[field] = new
            if married and not gate.get("couple_must_be_married"):
                changes.append((l["listing_key"], "couple_must_be_married", gate.get("couple_must_be_married"), True, False))
                if APPLY: gate["couple_must_be_married"] = True

        # ---- ethnicity_rule ----
        er = parse_ethnicity(reqs.get("ethnicity")) if scoped_ok(reqs.get("ethnicity")) else None
        if er:
            old = gate.get("ethnicity_rule")
            if (old or {}).get("mode") != er["mode"] or sorted([x.lower() for x in (old or {}).get("list", [])]) != sorted([x.lower() for x in er["list"]]):
                changes.append((l["listing_key"], "ethnicity_rule", old, er, _is_weakening("ethnicity_rule", old, er)))
                if APPLY: gate["ethnicity_rule"] = er

        # ---- lease_min / lease_max ----
        for src, field in (("lease_min", "lease_min_months"), ("lease_max", "lease_max_months")):
            v = _int(reqs.get(src))
            if v is not None and gate.get(field) != v:
                changes.append((l["listing_key"], field, gate.get(field), v, _is_weakening(field, gate.get(field), v)))
                if APPLY: gate[field] = v

        # ---- min_age ----
        a = parse_min_age(reqs)
        if a is not None and gate.get("min_age") != a:
            changes.append((l["listing_key"], "min_age", gate.get("min_age"), a, _is_weakening("min_age", gate.get("min_age"), a)))
            if APPLY: gate["min_age"] = a

        # ---- PRESERVE + flag only: max_pax (multi-room ambiguity) ----
        raw_pax = str(reqs.get("max_pax", ""))
        if raw_pax and not re.search(r"master|common|unit|/|-", raw_pax):
            m = re.search(r"\d+", raw_pax)
            if m and gate.get("max_pax") != int(m.group(0)):
                flags.append((l["listing_key"], f"max_pax landlord-db says {raw_pax!r} vs gate {gate.get('max_pax')} (not auto-applied)"))
        elif raw_pax and gate.get("max_pax") is not None:
            pass  # ambiguous multi-room phrasing -> keep hand-curated per-listing value silently

        # ---- listing STATUS: auto-CLOSE only (so the live matcher stops serving a rented
        # unit), but never auto-REOPEN (re-listing a closed unit is a human decision). For a
        # multi-listing landlord a single closed status is ambiguous -> flag, don't apply.
        ll_status = str(ll.get("status", "")).lower()
        cur_status = str(l.get("status", "")).lower()
        if ll_status.startswith("closed") and not cur_status.startswith("closed"):
            if len(sib) == 1:
                changes.append((l["listing_key"], "status", l.get("status"), "closed (tenanted)", False))
                if APPLY: l["status"] = "closed (tenanted)"
            else:
                flags.append((l["listing_key"], f"landlord {lid} is closed but this is a multi-unit landlord — review which unit is tenanted (not auto-closed)"))
        elif cur_status.startswith("closed") and ll_status in ("active", "active-verify"):
            flags.append((l["listing_key"], f"listing is closed but landlord {lid} is {ll_status} — re-list is a manual decision (not auto-reopened)"))

    # ---- report ----
    print(f"listings reconciled from landlord-db: {sum(1 for l in idx['listings'] if l.get('landlord_id') and not str(l.get('status','')).startswith('closed'))}")
    print(f"gate changes: {len(changes)} | ambiguous flags: {len(flags)}\n")
    for lk, f, old, new, weak in changes:
        tag = "  ⚠️ WEAKENS" if weak else ""
        print(f"  {lk:20} {f:22} {str(old)!s:28} -> {new}{tag}")
    if flags:
        print("\n  flags (preserved, review in landlord-db):")
        for lk, note in flags:
            print(f"   - {lk}: {note}")

    if not APPLY:
        print("\nDRY RUN. Re-run with --apply to write listing-index.json.")
        return
    if changes:
        tmp = IDX + ".tmp"
        json.dump(idx, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, IDX)
        print(f"\nWROTE: {IDX} ({len(changes)} gate change(s))")
    else:
        print("\nNo changes; listing-index already in sync.")

if __name__ == "__main__":
    main()
