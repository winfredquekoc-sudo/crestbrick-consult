#!/usr/bin/env python3
"""tenant_sale_lead_report.py — read only. Flags "Still looking" RENTAL
prospects whose profile or chat carries sale/purchase markers (buy, purchase,
BTO, resale flat, condo purchase, or a $ amount above SGD 100,000 — a rental
budget never gets near that; a resale/BTO/condo PURCHASE price does) so
Winfred can move them out of the rental pool by hand.

Never writes anything, never sends anything, never prints a name or phone —
counts and tenant ids only. "Still looking" uses export_data.py's own
looking() so this agrees with what the matchmaker app itself considers active,
without needing wa_conn/today/area_keywords (looking() takes only the tenant
dict).

Usage:
    /usr/bin/python3 scripts/tenant_sale_lead_report.py --json
    /usr/bin/python3 scripts/tenant_sale_lead_report.py --no-chat   # profile fields only, no WA read
"""
import argparse
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "matchmaker"))
import enrich  # noqa: E402
import export_data as ed  # noqa: E402

ROOT = os.path.expanduser("~/crestbrick-consult")
DEFAULT_TENANT_DB = os.path.join(ROOT, "_templates/tenant-db.json")
DEFAULT_MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")

SALE_AMOUNT_THRESHOLD = 100000

MARKER_RE = re.compile(
    r"\b(buy|buying|buyer|purchase|purchasing|bto|resale\s+flat|condo\s+purchase|"
    r"option\s+to\s+purchase|\botp\b)\b", re.I)
AMOUNT_RE = re.compile(r"\$\s?([\d,]+\.?\d*\s?[km]?)\b", re.I)


def amount_over_threshold(text):
    for m in AMOUNT_RE.finditer(text or ""):
        n = enrich._tok_to_num(m.group(1))
        if n is not None and n > SALE_AMOUNT_THRESHOLD:
            return True
    return False


def has_sale_markers(text):
    if not text:
        return False
    return bool(MARKER_RE.search(text)) or amount_over_threshold(text)


def profile_text(t):
    parts = [t.get("listing_enquired") or "", t.get("preferred_location") or ""]
    return "\n".join(p for p in parts if p)


def chat_text(con, jid, limit=200):
    if con is None or not jid:
        return ""
    try:
        rows = con.execute(
            "SELECT content FROM messages WHERE chat_jid=? AND content IS NOT NULL "
            "AND content!='' ORDER BY timestamp DESC LIMIT ?", (jid, limit)).fetchall()
        return "\n".join(c for (c,) in rows)
    except Exception:
        return ""


def open_msg_db(path):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        con.execute("SELECT 1 FROM messages LIMIT 1")
        return con
    except Exception:
        return None


def run_report(tenants, con):
    still_looking = 0
    flagged_ids = []
    for t in tenants:
        if ed.looking(t) != "Still looking":
            continue
        still_looking += 1
        text = profile_text(t)
        chat = chat_text(con, t.get("jid"))
        if has_sale_markers(text) or has_sale_markers(chat):
            flagged_ids.append(t.get("id"))
    return still_looking, flagged_ids


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant-db", default=DEFAULT_TENANT_DB)
    ap.add_argument("--messages-db", default=DEFAULT_MSG_DB)
    ap.add_argument("--no-chat", action="store_true", help="skip WA chat scan, profile fields only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.tenant_db) as f:
            db = json.load(f)
    except (OSError, ValueError) as e:
        print(f"tenant_sale_lead_report: failed to read tenant db: {e}", file=sys.stderr)
        return 2

    tenants = db.get("tenants", [])
    con = None if args.no_chat else open_msg_db(args.messages_db)
    if not args.no_chat and con is None:
        print("warning: WhatsApp bridge unavailable/locked — profile fields only this run",
              file=sys.stderr)
    chat_scanned = con is not None

    still_looking, flagged_ids = run_report(tenants, con)
    if con:
        con.close()

    summary = {
        "tenants_total": len(tenants),
        "still_looking_scanned": still_looking,
        "chat_scanned": chat_scanned,
        "flagged_count": len(flagged_ids),
        "flagged_ids": flagged_ids,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"scanned {summary['still_looking_scanned']} still looking tenants "
              f"(chat scan: {'on' if chat_scanned else 'off'})")
        print(f"flagged {summary['flagged_count']} possible sale/purchase leads:")
        for tid in flagged_ids:
            print(f"  {tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
