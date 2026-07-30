#!/usr/bin/env python3
"""
build_buyer_db.py — materializes a real buyer-leads database.

Buyer-form responses only ever lived inside the WA intake engine's raw working
state (~/.claude/state/listing-templates/intake-state.json), with no CSV, no
Sheet, nothing reviewable — unlike the tenant and landlord sides. This script
reads that same state (read-only, never touches the live pipeline) and writes
docs/buyer-database.csv + _templates/buyer-db.json, mirroring the tenant/
landlord convention.

Deliberately NOT a new AI-extraction pipeline: the intake engine already
parses these fields correctly (as of the 31 Jul 2026 name-extraction fix);
this only materializes what it already extracted into something reviewable.

Usage: build_buyer_db.py [--apply]
  --apply   actually write docs/buyer-database.csv + _templates/buyer-db.json
            (omit for a dry-run preview of what would be written)
"""
import json, os, sys, csv, datetime

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "crestbrick-consult")
STATE = os.path.join(HOME, ".claude/state/listing-templates/intake-state.json")
CSV_OUT = os.path.join(ROOT, "docs/buyer-database.csv")
JSON_OUT = os.path.join(ROOT, "_templates/buyer-db.json")
APPLY = "--apply" in sys.argv

FIELDS = ["phone", "name", "citizenship", "budget", "timeline", "financing",
          "property_type", "area", "bedrooms", "purpose", "source", "listing_key",
          "stage", "complete", "status", "form_sent_date", "missing_fields"]

BUYER_REQUIRED = ["name", "budget", "financing"]  # mirrors intake_engine.BUYER_REQUIRED


def _date_from_ts(ts):
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def buyer_rows():
    with open(STATE) as f:
        state = json.load(f)
    rows = []
    for phone, rec in state.get("conversations", {}).items():
        if not isinstance(rec, dict):
            continue
        b = rec.get("buyer")
        if not b:
            continue
        missing = [k for k in BUYER_REQUIRED if not b.get(k)]
        rows.append({
            "phone": phone,
            "name": b.get("name", ""),
            "citizenship": b.get("citizenship", ""),
            "budget": b.get("budget", ""),
            "timeline": b.get("timeline", ""),
            "financing": b.get("financing", ""),
            "property_type": b.get("property_type", ""),
            "area": b.get("area", ""),
            "bedrooms": b.get("bedrooms", ""),
            "purpose": b.get("purpose", ""),
            "source": rec.get("source") or "unknown",
            "listing_key": rec.get("listing_key") or "",
            "stage": rec.get("stage", ""),
            "complete": "yes" if rec.get("buyer_complete") else "no",
            "status": rec.get("status", ""),
            "form_sent_date": _date_from_ts(rec.get("buyer_form_sent_ts")),
            "missing_fields": ",".join(missing),
        })
    rows.sort(key=lambda r: (r["complete"] != "yes", r["form_sent_date"] or "9999"))
    return rows


def main():
    rows = buyer_rows()
    print(f"buyer records found: {len(rows)}")
    complete = sum(1 for r in rows if r["complete"] == "yes")
    print(f"  complete (name+budget+financing): {complete}")
    print(f"  incomplete: {len(rows) - complete}")
    for r in rows:
        flag = "OK" if r["complete"] == "yes" else f"missing: {r['missing_fields']}"
        print(f"  {r['phone']}  {r['name'] or '(no name)':30s}  source={r['source']:8s}  {flag}")

    if not APPLY:
        print("\n(dry run — pass --apply to write docs/buyer-database.csv + _templates/buyer-db.json)")
        return

    os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(JSON_OUT, "w") as f:
        json.dump({"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "buyers": rows}, f, indent=2)
    print(f"\nwrote {len(rows)} rows to {CSV_OUT}")
    print(f"wrote mirror to {JSON_OUT}")


if __name__ == "__main__":
    main()
