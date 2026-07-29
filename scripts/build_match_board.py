#!/usr/bin/env python3
"""
build_match_board.py — v2: the LANDLORD to TENANT matching engine, now SCORED.

For every ACTIVE listing it runs the real qualify() gates against every ACTIVE,
still-looking tenant, then ranks survivors with a 0..100 fit score:

  budget fit    0..30  right-sized beats big-overshoot (a $3.7k budget on a $980 room
                        usually wants a whole unit, not that room)
  location      0..25  exact preferred district > adjacent district > no signal
  lease         0..15  12mo+ full marks; under 12 flagged "needs 1yr acceptance"
                        (mirrors the engine's SHORT_LEASE note flow)
  move-in       0..15  wants to move within ~a month beats months away
  freshness     0..15  spoke to us this week beats a 2 month old profile

NEEDS_INFO survivors are kept, capped, and carry their missing fields as the note.
Verdict + score never message anyone — this is Winfred's working shortlist.

Usage: build_match_board.py [--apply]   (writes _templates/match-board.json + docs/match-board.csv)
"""
import json, os, sys, csv, re, datetime, importlib.util

ROOT = os.path.expanduser("~/crestbrick-consult")
sys.path.insert(0, os.path.join(ROOT, "src/wa-pipeline"))
import intake_engine as E
spec = importlib.util.spec_from_file_location("blm", os.path.join(ROOT, "scripts/build_tenant_location_map.py"))
blm = importlib.util.module_from_spec(spec); spec.loader.exec_module(blm)
APPLY = "--apply" in sys.argv
LOOKING = {"rejected", "cold", "form-sent", "profile-received", "viewed", "open"}
TODAY = datetime.date.today()

ADJACENT = {
    "D1": ["D2","D4","D6","D7"], "D2": ["D1","D3","D4"], "D3": ["D2","D4","D5","D10"],
    "D4": ["D1","D2","D3","D5"], "D5": ["D3","D4","D10","D21","D22"], "D6": ["D1","D7","D9"],
    "D7": ["D1","D6","D8","D14"], "D8": ["D7","D9","D11","D12","D13"], "D9": ["D6","D8","D10","D11"],
    "D10": ["D3","D5","D9","D11","D21"], "D11": ["D8","D9","D10","D12","D20"],
    "D12": ["D8","D11","D13","D20"], "D13": ["D8","D12","D14","D19","D20"],
    "D14": ["D7","D13","D15","D16","D19"], "D15": ["D14","D16"], "D16": ["D14","D15","D17","D18"],
    "D17": ["D16","D18"], "D18": ["D16","D17","D19"], "D19": ["D13","D14","D18","D20","D28"],
    "D20": ["D11","D12","D13","D19","D26","D28"], "D21": ["D5","D10","D23"],
    "D22": ["D5","D23","D24"], "D23": ["D21","D22","D24","D25"], "D24": ["D22","D23","D25"],
    "D25": ["D23","D24","D27"], "D26": ["D20","D27","D28"], "D27": ["D25","D26","D28"],
    "D28": ["D19","D20","D26","D27"],
}

def _days_since(d):
    try: return (TODAY - datetime.date(*map(int, str(d)[:10].split("-")))).days
    except Exception: return None

def _days_until(d):
    try: return (datetime.date(*map(int, str(d)[:10].split("-"))) - TODAY).days
    except Exception: return None

def score_budget(budget, rent_min):
    if not isinstance(budget, (int, float)) or not isinstance(rent_min, (int, float)) or rent_min <= 0:
        return 12, "budget unknown"
    r = budget / rent_min
    if r < 0.9:  return -1, "under budget"          # caller drops on -1
    if r < 1.0:  return 18, "stretch budget (within 10%)"
    if r <= 1.25: return 30, ""
    if r <= 1.8: return 22, ""
    if r <= 3.0: return 10, "budget far above room (may want bigger)"
    return 5, "budget suggests whole unit, not a room"

def score_location(listing_d, prefs):
    if not prefs: return 6, "location preference unconfirmed"
    if listing_d in prefs: return 25, ""
    if any(listing_d in ADJACENT.get(p, []) for p in prefs): return 12, "adjacent district"
    return -1, "wrong area"                          # caller drops on -1

def score_lease(months):
    if not isinstance(months, (int, float)): return 7, "lease term unknown"
    if months >= 12: return 15, ""
    if months >= 6:  return 5, "needs 1yr acceptance"
    return -1, "sub 6 month lease"

def score_movein(mv):
    d = _days_until(mv)
    if d is None:
        low = str(mv or "").lower()
        return (13, "") if any(k in low for k in ("asap", "soon", "immediate", "now")) else (8, "move-in date unclear")
    if d < -30: return 8, "stated move-in has passed (reconfirm)"
    if d <= 30: return 15, ""
    if d <= 60: return 10, ""
    return 4, f"move-in {d} days away"

def score_freshness(lc):
    d = _days_since(lc)
    if d is None: return 5, "last contact unknown"
    if d <= 7:  return 15, ""
    if d >= 60: return 0, f"stale ({d}d since contact)"
    return round(15 * (60 - d) / 53), (f"{d}d since contact" if d > 21 else "")

def main():
    tdb = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
    ldb = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
    ll = {x["id"]: x for x in ldb["landlords"]}
    idx = blm.listing_entries()
    reqs = E.listing_reqs()

    # active listings: open/active in the index, landlord not closed/archived. District comes
    # from the index, the landlord record (tightened 2026-07-11), or the legacy hardcode.
    listings = []
    for lk, e in idx.items():
        st = str(e.get("status", "")).lower()
        if st.startswith(("closed", "hold", "archived")): continue
        lo = ll.get(e.get("landlord_id"), {})
        if str(lo.get("status", "")).startswith(("closed", "archived")): continue
        dist = e.get("district") or lo.get("district") or blm.LISTING_DISTRICT.get(lk, ("", None))[1]
        area = e.get("area") or blm.LISTING_DISTRICT.get(lk, ("", None))[0] or lo.get("full_address", "")
        if not dist: continue
        listings.append((lk, area, dist, lo, e))

    pool = [x for x in tdb["tenants"]
            if not x.get("excluded") and x.get("contact_state") == "active"
            and x.get("status") in LOOKING]

    rows = []
    for lk, area, dist, lo, e in listings:
        req = reqs.get(lk)
        if not req: continue
        # budget_floor first: it tracks the room that is ACTUALLY available (e.g. bayshore's
        # $2,300 master), while the landlord's rent_min may include already-tenanted rooms.
        rent_min = (req.get("requirements", {}) or {}).get("budget_floor") or lo.get("rent_min")
        rent_max = lo.get("rent_max") or rent_min
        for t in pool:
            prof = {k: t.get(k) for k in ("gender","ethnicity","nationality","pass_type",
                                          "no_of_pax","age","lease_term_months","budget")}
            verdict, why = E.qualify(req, prof)
            if verdict == "DISQUALIFIED": continue
            parts, notes = [], []
            for s, n in (score_budget(t.get("budget"), rent_min),
                         score_location(dist, t.get("preferred_districts") or []),
                         score_lease(t.get("lease_term_months")),
                         score_movein(t.get("move_in_date")),
                         score_freshness(t.get("last_contact"))):
                if s < 0: parts = None; break
                parts.append(s)
                if n: notes.append(n)
            if parts is None: continue
            score = sum(parts)
            if verdict in ("NEEDS_INFO", "SHORT_LEASE"):
                score = max(0, score - 10)
                notes.append("missing: " + ", ".join(map(str, why)) if verdict == "NEEDS_INFO"
                             else "short lease (engine will send the 1yr note)")
            rows.append([lk, area, dist, rent_min or "", rent_max or "", lo.get("landlord_name", ""),
                         t.get("id"), t.get("name") or "", t.get("budget") or "",
                         t.get("no_of_pax") or "", t.get("lease_term_months") or "",
                         t.get("district", ""), t.get("status"), verdict, score, "; ".join(notes)])

    rows.sort(key=lambda r: (r[0], -r[14]))
    header = ["listing_key","area","district","rent_min","rent_max","landlord","tenant_id",
              "tenant_name","tenant_budget","tenant_pax","tenant_lease","tenant_district",
              "tenant_status","verdict","score","note"]

    per = {}
    for r in rows: per.setdefault(r[0], []).append(r)
    print(f"active listings {len(listings)} | tenant pool {len(pool)} | scored matches {len(rows)}\n")
    for lk in sorted(per, key=lambda k: -len(per[k])):
        print(f"{lk} ({per[lk][0][2]}, from ${per[lk][0][3]}) — {len(per[lk])} candidates, top 3:")
        for r in per[lk][:3]:
            print(f"   {r[14]:3d}  {r[7] or r[6]:24.24s} bud ${r[8] or '?'} pax {r[9] or '?'} "
                  f"lease {r[10] or '?'}mo [{r[12]}] {('· ' + r[15]) if r[15] else ''}")

    if not APPLY:
        print("\nDRY RUN. Re-run with --apply to write the match board.")
        return
    out = {"source": "build_match_board.py v2; qualify() gates + 0..100 fit score "
                     "(budget 30, location 25, lease 15, move-in 15, freshness 15)",
           "generated_at": TODAY.isoformat(),
           "matches": [dict(zip(header, r)) for r in rows]}
    p = os.path.join(ROOT, "_templates/match-board.json")
    json.dump(out, open(p + ".tmp", "w"), indent=1, ensure_ascii=False)
    os.replace(p + ".tmp", p)
    with open(os.path.join(ROOT, "docs/match-board.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"\nWROTE _templates/match-board.json and docs/match-board.csv ({len(rows)} matches)")

if __name__ == "__main__":
    main()
