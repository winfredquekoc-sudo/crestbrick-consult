#!/usr/bin/env python3
"""listing_coverage.py — portal coverage + listing-readiness for the landlord DB.

Fixes three operational gaps for a solo agent:

  1. posted_on tracking   — a per-landlord record of what is actually live on
     PropertyGuru / 99.co, so coverage is a query, not a browser session.
     (`listing_key` in the DB is the winfredquek.com website slug, a DIFFERENT
     thing — this does not touch it.)
  2. listing-readiness    — which active rentals are ready to post (rent +
     address + photos) but are NOT yet on a portal (the money list), and which
     are INCOMPLETE, with a drafted follow-up ask for the missing field.
  3. reconciliation       — diff a captured live-portal snapshot against the DB:
     coverage gaps (in DB, not on portals) and orphans (on a portal, not in the
     DB = the off-WhatsApp onboarding blind spot).

Portals 403 headless (Cloudflare); the live snapshot must be captured from
Winfred's logged-in browser in-session, then fed here with --reconcile. This
tool is read-only about the world; it only writes the DB under --seed/--apply,
and it NEVER sends anything (drafts are printed for hand-sending).

Usage:
  listing_coverage.py                      # coverage summary + readiness report
  listing_coverage.py --draft-asks         # + WA follow-up drafts for incomplete
  listing_coverage.py --seed [--apply]     # add posted_on field + backfill known
  listing_coverage.py --reconcile snap.json [--apply]
  listing_coverage.py --json               # machine-readable report to stdout

Snapshot format (--reconcile): {"propertyguru": [ {..listing..} ], "99co": [ ... ]}
  each listing: {"address": "503 Ang Mo Kio Ave 5", "id_or_url": "500...", "rent": 1200}
"""
import argparse, json, os, re, sys, shutil, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "_templates", "landlord-db.json")
PHOTOS = os.path.join(ROOT, "scripts", "matchmaker", "deploy", "photos")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
RENTAL = ("rental", "rent")

# High-confidence backfill: the 14 room listings published on 99.co on 2 Sep 2026
# (this operator's own session log) + PropertyGuru listing ids known from records.
# Only certain rows — everything else is left for --reconcile to fill from ground truth.
SEED_99CO = {  # LLid: date published
    "LL157": "2026-09-02", "LL160": "2026-09-02", "LL142": "2026-09-02",
    "LL106": "2026-09-02", "LL111": "2026-09-02", "LL118": "2026-09-02",
    "LL122": "2026-09-02", "LL125": "2026-09-02", "LL136": "2026-09-02",
    "LL138": "2026-09-02", "LL148": "2026-09-02", "LL108": "2026-09-02",
    "LL161": "2026-09-02", "LL077": "2026-09-02",
}
SEED_PG = {  # LLid: PropertyGuru listing id
    "LL105": "500237103", "LL117": "500248838", "LL188": "500248513",
    "LL112": "500236749", "LL097": "500220113",
}


def load_db():
    with open(DB, encoding="utf-8") as f:
        return json.load(f)


def save_db(d):
    bak = DB + ".bak-coverage-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, bak)
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return bak


def photo_count(llid):
    p = os.path.join(PHOTOS, llid)
    if not os.path.isdir(p):
        return 0
    return len([f for f in os.listdir(p) if f.lower().endswith(IMG_EXT)])


def has_rent(r):
    if r.get("rent_min") or r.get("rent_max"):
        return True
    return bool(re.search(r"\$\s?\d{3,}", r.get("rooms_and_rent") or ""))


def has_address(r):
    a = (r.get("full_address") or "").strip().lower()
    if not a:
        return False
    bad = ("unknown", "tbc", "not yet", "awaiting", "(address tbc)")
    return not any(b in a for b in bad)


def is_posted(r):
    p = r.get("posted_on") or {}
    return bool(p.get("propertyguru") or p.get("99co"))


def first_name(r):
    n = (r.get("landlord_name") or "").strip()
    n = re.sub(r"\(.*?\)", "", n).strip()          # drop "(rep: ...)"
    tok = n.split()
    return tok[0] if tok and tok[0].lower() not in ("the", "unknown") else ""


def active_rentals(rows):
    return [r for r in rows if r.get("status") == "active" and r.get("deal_type") in RENTAL]


# ---- address normalisation for reconcile matching ----
def postal(s):
    m = re.search(r"\bS?(\d{6})\b", s or "")
    return m.group(1) if m else ""


def block(s):
    m = re.search(r"\b(?:blk\s*)?(\d{1,4}[a-z]?)\b", (s or "").lower())
    return m.group(1) if m else ""


def street_tokens(s):
    s = re.sub(r"#\d+-\d+", " ", (s or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    stop = {"blk", "block", "singapore", "s", "road", "rd", "street", "st",
            "avenue", "ave", "drive", "dr", "lane", "condo", "hdb", "room"}
    return {t for t in s.split() if t and t not in stop and not t.isdigit()}


def addr_match(portal_addr, row):
    da = row.get("full_address") or ""
    pp, dp = postal(portal_addr), postal(da)
    if pp and dp:
        return pp == dp                      # postal is authoritative when both have it
    # fallback (no postal on one side): same block AND a real street match, not just one
    # shared area word ("47 Marine Crescent" must NOT match "47 Marine Parade").
    pb, db_ = block(portal_addr), block(da)
    if pb and db_ and pb == db_:
        pt, dt_ = street_tokens(portal_addr), street_tokens(da)
        if pt and dt_ and (len(pt & dt_) >= 2 or pt <= dt_ or dt_ <= pt):
            return True
    return False


def cmd_report(rows, draft_asks=False, as_json=False):
    act = active_rentals(rows)
    ready_unposted, incomplete, posted = [], [], []
    for r in act:
        rec = {
            "id": r["id"], "name": r.get("landlord_name") or "",
            "address": r.get("full_address") or "",
            "rent": has_rent(r), "addr": has_address(r),
            "photos": photo_count(r["id"]), "posted": is_posted(r),
        }
        if rec["posted"]:
            posted.append(rec)
        elif rec["rent"] and rec["addr"] and rec["photos"] > 0:
            ready_unposted.append(rec)
        else:
            miss = []
            if not rec["rent"]: miss.append("rent")
            if not rec["addr"]: miss.append("address")
            if rec["photos"] == 0: miss.append("photos")
            rec["missing"] = miss
            incomplete.append(rec)

    if as_json:
        print(json.dumps({"ready_unposted": ready_unposted,
                          "incomplete": incomplete, "posted": posted}, indent=2))
        return

    print(f"ACTIVE RENTALS: {len(act)}  |  posted on a portal: {len(posted)}  "
          f"|  ready but UNPOSTED: {len(ready_unposted)}  |  incomplete: {len(incomplete)}\n")

    print(f"== READY TO POST NOW — {len(ready_unposted)} (rent+address+photos, not on a portal) ==")
    for x in sorted(ready_unposted, key=lambda z: z["id"]):
        print(f"  {x['id']:6} {str(x['photos'])+'ph':5} {x['address'][:60]}")

    print(f"\n== INCOMPLETE — {len(incomplete)} (fix before it can be listed) ==")
    for x in sorted(incomplete, key=lambda z: z["id"]):
        print(f"  {x['id']:6} missing {','.join(x['missing']):22} {(x['name'] or '')[:22]:22} {x['address'][:40]}")

    print(f"\n== ALREADY POSTED — {len(posted)} ==")
    for x in sorted(posted, key=lambda z: z["id"]):
        p = next(r for r in act if r["id"] == x["id"]).get("posted_on", {})
        where = ",".join(k for k in ("propertyguru", "99co") if p.get(k))
        print(f"  {x['id']:6} [{where}] {x['address'][:55]}")

    if draft_asks:
        print("\n== FOLLOW-UP DRAFTS (hand-send, Winfred voice) ==")
        for x in sorted(incomplete, key=lambda z: z["id"]):
            r = next(r for r in act if r["id"] == x["id"])
            nm = first_name(r)
            hi = f"hi {nm.lower()}, " if nm else "hi, "
            parts = []
            if "rent" in x["missing"]:
                parts.append("whats the asking rent for the room")
            if "address" in x["missing"]:
                parts.append("could you confirm the block and unit number")
            if "photos" in x["missing"]:
                parts.append("could you send me a few photos of the room and a short video")
            ask = ", and ".join(parts)
            tail = " so i can put your listing up on the property portals"
            print(f"  {x['id']} -> {hi}{ask}{tail}")


def cmd_seed(rows, apply):
    changed = 0
    for r in active_rentals(rows):
        if "posted_on" not in r:
            r["posted_on"] = {}
            changed += 1
    for llid, date in SEED_99CO.items():
        r = next((r for r in rows if r["id"] == llid), None)
        if r is not None:
            r.setdefault("posted_on", {}).setdefault("99co", date)
    for llid, pgid in SEED_PG.items():
        r = next((r for r in rows if r["id"] == llid), None)
        if r is not None:
            r.setdefault("posted_on", {}).setdefault("propertyguru", pgid)
    print(f"seed: posted_on field ensured on active rentals (+{changed} new); "
          f"backfilled {len(SEED_99CO)} 99.co + {len(SEED_PG)} PG known rows")
    return changed


def cmd_reconcile(rows, snap_path, apply):
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)
    act = active_rentals(rows)
    matched_ids = set()
    orphans = []
    for portal in ("propertyguru", "99co"):
        for L in snap.get(portal, []):
            addr = L.get("address", "")
            row = next((r for r in act if addr_match(addr, r)), None)
            if row is None:
                # also try all rows (may be a closed/sale row or truly unknown)
                row = next((r for r in rows if addr_match(addr, r)), None)
            if row is None:
                orphans.append((portal, addr, L.get("id_or_url", "")))
            else:
                matched_ids.add(row["id"])
                if apply and row.get("status") == "active":
                    row.setdefault("posted_on", {})[portal] = \
                        str(L.get("id_or_url") or dt.date.today().isoformat())

    gaps = [r for r in act if r["id"] not in matched_ids and not is_posted(r)]
    print(f"RECONCILE against {snap_path}")
    print(f"  portal listings: PG={len(snap.get('propertyguru', []))} "
          f"99co={len(snap.get('99co', []))}  |  matched to DB: {len(matched_ids)}\n")
    print(f"== COVERAGE GAPS — {len(gaps)} active rentals not found on any portal ==")
    for r in sorted(gaps, key=lambda z: z["id"]):
        print(f"  {r['id']:6} {photo_count(r['id'])}ph  {(r.get('full_address') or '')[:60]}")
    print(f"\n== ORPHANS — {len(orphans)} portal listings with NO DB match "
          f"(possible off-WhatsApp onboards) ==")
    for portal, addr, idu in orphans:
        print(f"  [{portal}] {addr[:55]}  {idu}")
    return orphans


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft-asks", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--reconcile", metavar="SNAPSHOT.json")
    ap.add_argument("--apply", action="store_true",
                    help="persist DB changes (with --seed / --reconcile); else dry-run")
    a = ap.parse_args()

    d = load_db()
    rows = d["landlords"]
    dirty = False

    if a.seed:
        cmd_seed(rows, a.apply)
        dirty = True
    if a.reconcile:
        cmd_reconcile(rows, a.reconcile, a.apply)
        dirty = a.apply
    if not a.seed and not a.reconcile:
        cmd_report(rows, draft_asks=a.draft_asks, as_json=a.json)

    if dirty and a.apply:
        bak = save_db(d)
        print(f"\nDB written. backup: {os.path.basename(bak)}")
    elif dirty:
        print("\n(dry-run — re-run with --apply to persist)")


if __name__ == "__main__":
    main()
