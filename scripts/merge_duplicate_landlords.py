#!/usr/bin/env python3
"""merge_duplicate_landlords.py — collapse landlord rows that share one phone.

Landlord twin of merge_duplicate_tenants.py, built 23 Aug 2026 after the
refresh content pass double added Grace (LL125/LL130), Nandhini (LL105/LL129),
Nannitha (LL106/LL131) and Parvathy (LL120/LL128) over one weekend.

Conservative by construction:
  - landlord to landlord SAME PHONE pairs only (normalized last 8 digits);
  - if either row is already closed as a duplicate, the pair is done;
  - a pair where either side is do_not_contact or an unregister tombstone is
    NEVER merged (tombstones must survive untouched so nothing re adds them);
  - names must not CONTRADICT — one blank/Unknown, equal, or one contains the
    other merges; two different real names are flagged for Winfred, never merged;
  - winner = the OLDEST id whose status is not closed, else the oldest id;
    richer fields from the loser fill only EMPTY winner fields (never overwrite);
  - the loser keeps every byte of its data, gains status
    "closed (duplicate of <winner>)" + dup_of, and never reopens.

Operates on the master (_templates/landlord-db.json). Run sync_db_csv.py
--apply afterwards to push statuses to the CSV mirror. Dry run by default;
--apply writes atomically with a dated .bak.
"""
import datetime, json, os, re, shutil, sys
from collections import defaultdict

ROOT = os.path.expanduser("~/crestbrick-consult")
LDB = os.path.join(ROOT, "_templates/landlord-db.json")
APPLY = "--apply" in sys.argv

FILL_FIELDS = ["landlord_name", "chat_jid", "deal_type", "property_type", "full_address",
               "district", "listing_key", "rooms_and_rent", "rent_min", "rent_max",
               "viewing_availability", "data_collected"]

UNKNOWNISH = re.compile(r"^\s*$|unknown|not captured|no name|name tbc", re.I)


def norm_phone(p):
    digits = re.sub(r"\D", "", str(p or ""))
    return digits[-8:] if len(digits) >= 8 else digits


def names_compatible(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if UNKNOWNISH.search(a) or UNKNOWNISH.search(b):
        return True
    al, bl = a.lower(), b.lower()
    return al == bl or al in bl or bl in al


def is_tombstone(r):
    s = (r.get("status") or "").lower()
    return "unregister" in s or "duplicate of" in s or bool(r.get("do_not_contact"))


def main():
    d = json.load(open(LDB))
    rows = d["landlords"]
    today = datetime.date.today().isoformat()

    by_phone = defaultdict(list)
    for r in rows:
        p = norm_phone(r.get("phone"))
        if p:
            by_phone[p].append(r)

    merged, flagged = [], []
    for p, group in by_phone.items():
        if len(group) < 2:
            continue
        if any(is_tombstone(r) for r in group):
            continue
        group.sort(key=lambda r: r.get("id") or "")
        if any(not names_compatible(group[0].get("landlord_name"), r.get("landlord_name")) for r in group[1:]):
            flagged.append((p, [(r.get("id"), r.get("landlord_name")) for r in group]))
            continue
        open_rows = [r for r in group if not (r.get("status") or "").lower().startswith("closed")]
        winner = (open_rows or group)[0]
        for loser in group:
            if loser is winner:
                continue
            for f in FILL_FIELDS:
                if not str(winner.get(f) or "").strip() and str(loser.get(f) or "").strip():
                    winner[f] = loser[f]
            lf = str(loser.get("follow_up") or "").strip()
            if lf and lf not in str(winner.get("follow_up") or ""):
                winner["follow_up"] = (str(winner.get("follow_up") or "") + " | [merged from " + str(loser.get("id")) + "] " + lf).strip(" |")
            if (winner.get("last_contact") or "") < (loser.get("last_contact") or ""):
                winner["last_contact"] = loser["last_contact"]
            merged.append((loser.get("id"), winner.get("id"), winner.get("landlord_name")))
            if APPLY:
                loser["status"] = f"closed (duplicate of {winner.get('id')}, merged {today})"
                loser["dup_of"] = winner.get("id")
                loser["follow_up"] = (str(loser.get("follow_up") or "") + f" | {today}: tombstoned as duplicate of {winner.get('id')}; never reopen, never re add").strip(" |")
                winner["last_refreshed"] = today

    mode = "APPLIED" if APPLY else "DRY RUN"
    print(f"landlord dup merge [{mode}]: {len(merged)} merged, {len(flagged)} flagged")
    for l, w, n in merged:
        print(f"  {l} -> {w}  ({n})")
    for p, g in flagged:
        print(f"  FLAG ...{p}: names contradict, Winfred to judge: {g}")

    if APPLY and merged:
        shutil.copy(LDB, LDB + f".bak-dupmerge-{today}")
        tmp = LDB + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, LDB)


if __name__ == "__main__":
    main()
