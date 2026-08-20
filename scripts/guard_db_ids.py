#!/usr/bin/env python3
"""guard_db_ids.py — detect tenant/landlord ID collisions before they eat a record.

Why: IDs are assigned by the AI refresh passes as max+1, which is only safe while
no row ever disappears. Twice in Aug 2026 a wiped row let a NEW contact take an
old id (LL112 reused after Anne's row was wiped; TN611 nearly double-assigned) —
the tombstone and the newcomer then fight over one identity.

This keeps a monotonic watermark (~/.claude/state/id-watermark.json) of every id
ever seen with the phone it belonged to. Each run it:
  1. alerts on duplicate ids WITHIN a database (two rows, one id),
  2. alerts on an id whose phone CHANGED (a reused id — the collision class),
  3. raises the watermark and records new ids (never lowers, never forgets).

Read-only against the databases; only the watermark file is written. Alerts go
to Winfred's Telegram and the exit code stays 0 — a guard must never kill the
refresh that would fix the data.
"""
import json, os, subprocess, sys

ROOT = os.path.expanduser("~/crestbrick-consult")
TDB = os.path.join(ROOT, "_templates/tenant-db.json")
LDB = os.path.join(ROOT, "_templates/landlord-db.json")
WM = os.path.expanduser("~/.claude/state/id-watermark.json")
TG = os.path.expanduser("~/.claude/bin/telegram_send.sh")
TG_CHAT = "540127870"


def norm_phone(p):
    return "".join(ch for ch in str(p or "") if ch.isdigit())[-8:]


def idnum(s, prefix):
    s = str(s or "")
    if s.startswith(prefix) and s[len(prefix):].isdigit():
        return int(s[len(prefix):])
    return None


def scan(rows, prefix, wm_known, wm_max, problems):
    seen = {}
    top = wm_max
    for r in rows:
        rid = str(r.get("id") or "")
        if not rid:
            continue
        if rid in seen:
            problems.append(f"DUPLICATE ID {rid}: '{seen[rid]}' and '{r.get('landlord_name') or r.get('name')}' share it")
        seen[rid] = r.get("landlord_name") or r.get("name") or "?"
        n = idnum(rid, prefix)
        if n is not None:
            top = max(top, n)
        ph = norm_phone(r.get("phone"))
        old = wm_known.get(rid)
        if old and ph and old != ph:
            problems.append(f"REUSED ID {rid}: phone was ...{old}, now ...{ph} — an old id was reassigned to a different person")
        if ph:
            wm_known[rid] = ph
    return top


def main():
    try:
        wm = json.load(open(WM))
    except (OSError, ValueError):
        wm = {}
    known_t = wm.get("tenant_known") or {}
    known_l = wm.get("landlord_known") or {}
    problems = []

    t_rows = json.load(open(TDB))["tenants"]
    l_key = json.load(open(LDB))
    l_rows = l_key.get("landlords") or l_key.get("records") or []
    t_max = scan(t_rows, "TN", known_t, int(wm.get("tenant_max") or 0), problems)
    l_max = scan(l_rows, "LL", known_l, int(wm.get("landlord_max") or 0), problems)

    out = {"tenant_max": t_max, "landlord_max": l_max,
           "tenant_known": known_t, "landlord_known": known_l}
    tmp = WM + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, WM)
    print(f"id guard: next TN{t_max + 1} / LL{l_max + 1} | tenants {len(t_rows)} landlords {len(l_rows)} | problems: {len(problems)}")
    for p in problems:
        print("  !! " + p)
    if problems and os.path.exists(TG):
        msg = "⚠️ Matchmaker DB id guard:\n" + "\n".join(problems[:6])
        try:
            subprocess.run(["bash", TG, TG_CHAT], input=msg.encode(), timeout=20)
        except Exception:
            pass


if __name__ == "__main__":
    main()
