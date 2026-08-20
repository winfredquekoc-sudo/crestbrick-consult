#!/usr/bin/env python3
"""merge_duplicate_tenants.py — collapse tenant rows that share one phone number.

Tenant-to-tenant SAME-PHONE pairs only. A phone shared between a landlord row
and a tenant row is a different animal (often the same human on both sides, or
an agent) and is never touched here — compute_duplicate_phones() already
surfaces those for Winfred to judge.

Conservative by construction:
  - both rows must be tenants, neither excluded;
  - if either is already closed as a duplicate, the pair is done;
  - names must not CONTRADICT (one blank, equal, or one contains the other) —
    "Alex" vs "Alex Tan" merges, "Alex" vs "Priya" is flagged, never merged;
  - the winner is the richer profile (filled fields), ties to newer last_contact;
  - the loser keeps every byte of its data, gains status "closed (duplicate of
    <winner>)" + dup_of, and a tombstone note so no pass ever re-opens it.

Dry run by default; --apply writes tenant-db.json atomically with a .bak.
"""
import datetime, json, os, shutil, sys

ROOT = os.path.expanduser("~/crestbrick-consult")
TDB = os.path.join(ROOT, "_templates/tenant-db.json")
APPLY = "--apply" in sys.argv

FIELDS = ["name", "gender", "ethnicity", "nationality", "pass_type", "occupation",
          "no_of_pax", "move_in_date", "lease_term_months", "budget", "budget_min",
          "budget_max", "preferred_location", "district", "email"]


def norm_phone(p):
    digits = "".join(ch for ch in str(p or "") if ch.isdigit())
    return digits[-8:] if len(digits) >= 8 else ""


def richness(t):
    return sum(1 for f in FIELDS if str(t.get(f) or "").strip())


def names_compatible(a, b):
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    if not a or not b or a == b:
        return True
    return a in b or b in a


def main():
    d = json.load(open(TDB))
    tenants = d["tenants"]
    by_phone = {}
    for t in tenants:
        if t.get("excluded"):
            continue
        ph = norm_phone(t.get("phone"))
        if ph:
            by_phone.setdefault(ph, []).append(t)

    merged, flagged = [], []
    today = datetime.date.today().isoformat()
    for ph, group in by_phone.items():
        if len(group) < 2:
            continue
        live = [t for t in group if "duplicate of" not in str(t.get("status") or "")]
        if len(live) < 2:
            continue
        live.sort(key=lambda t: (richness(t), str(t.get("last_contact") or "")), reverse=True)
        winner = live[0]
        for loser in live[1:]:
            if not names_compatible(winner.get("name"), loser.get("name")):
                flagged.append((ph, winner.get("id"), winner.get("name"), loser.get("id"), loser.get("name")))
                continue
            merged.append((loser.get("id"), loser.get("name"), winner.get("id"), winner.get("name")))
            loser["prev_status"] = loser.get("status")
            loser["status"] = f"closed (duplicate of {winner.get('id')})"
            loser["dup_of"] = winner.get("id")
            loser["closed_reason"] = f"same phone as {winner.get('id')}, merged {today}"
            loser["follow_up"] = ((str(loser.get("follow_up") or "") + " | ").lstrip(" |")
                                  + "duplicate tombstone — never re-add")
            # winner absorbs any field it is missing that the loser has
            for f in FIELDS:
                if not str(winner.get(f) or "").strip() and str(loser.get(f) or "").strip():
                    winner[f] = loser[f]

    print(f"duplicate merge: {len(merged)} merged, {len(flagged)} flagged (conflicting names, left alone)"
          + ("" if APPLY else " [DRY RUN]"))
    for m in merged:
        print(f"  {m[0]} {m[1]!r} -> closed as duplicate of {m[2]} {m[3]!r}")
    for f in flagged:
        print(f"  ?? phone ...{f[0]}: {f[1]} {f[2]!r} vs {f[3]} {f[4]!r} — names conflict, decide by hand")

    if APPLY and merged:
        shutil.copy(TDB, TDB + ".bak-dupmerge-" + today.replace("-", ""))
        tmp = TDB + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, TDB)
        print(f"WROTE {TDB}")


if __name__ == "__main__":
    main()
