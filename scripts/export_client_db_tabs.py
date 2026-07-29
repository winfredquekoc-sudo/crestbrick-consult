#!/usr/bin/env python3
"""
export_client_db_tabs.py — build the three 2D arrays for the "Crestbrick Client
Database" Google Spreadsheet (live nightly sync) from the local JSON databases.

Tabs:
  Landlords Active  — every landlord NOT in a closed/dead/sale/channel state
  Landlords Closed  — closed / tenanted / dropped / dormant / no-response / cold / sale / channel
  Tenants           — all prospects EXCEPT those who found a place or are tenanted
                      (stale auto-closed prospects stay visible with their status)

Writes JSON arrays (header row first) to ~/.claude/state/client-db-export/
  landlords_active.json, landlords_closed.json, tenants.json
plus meta.json with row counts and the pinned spreadsheet id. The nightly skill
then pushes each array to its tab. Deterministic; no network access here.
"""
import json, os, datetime

ROOT = os.path.expanduser("~/crestbrick-consult")
OUT = os.path.expanduser("~/.claude/state/client-db-export")
SPREADSHEET_ID = "1WdCMc0ktexARRVtHqMUoeYrPdS_HFYAk8pnPFobSeik"

HEADER_L = ["ID", "Name", "Phone", "Address", "Property Type", "Deal Type",
            "Rent Summary", "Status", "Last Contact", "Gender Pref", "Ethnicity Pref",
            "Max Pax", "Lease Min (mo)", "Cooking", "Pets", "Smoking", "Commission",
            "Follow Up / Notes", "Chat JID"]

DEAD_LL = ("dormant", "no-response", "cold")


def flat_landlord(l):
    req = l.get("requirements") or {}
    return [
        l.get("id", ""),
        l.get("landlord_name") or l.get("name") or "",
        l.get("phone", ""),
        l.get("full_address", ""),
        l.get("property_type", ""),
        l.get("deal_type", ""),
        l.get("rooms_and_rent", ""),
        l.get("status", ""),
        l.get("last_contact") or "",
        req.get("gender", ""),
        req.get("ethnicity", ""),
        str(req.get("max_pax", "")),
        str(req.get("lease_min", "")),
        req.get("cooking", ""),
        req.get("pets", ""),
        req.get("smoking", ""),
        req.get("commission", ""),
        (l.get("follow_up", "") or "")[:800],
        l.get("chat_jid", ""),
    ]


def main():
    os.makedirs(OUT, exist_ok=True)

    ldb = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
    active, closed = [], []
    for l in ldb["landlords"]:
        status = (l.get("status") or "").strip().lower()
        deal = (l.get("deal_type") or "").strip().lower()
        row = flat_landlord(l)
        if ("closed" in status or status in DEAD_LL or status == "sale-active"
                or deal in ("sale", "sale-or-rent")):
            closed.append(row)
        else:
            active.append(row)

    tdb = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
    cols = tdb["columns"]
    tenants = []
    for t in tdb["tenants"]:
        st = (t.get("status") or "").strip().lower()
        cs = (t.get("contact_state") or "").strip().lower()
        if st in ("tenanted", "found_place") or cs == "found_place":
            continue
        row = []
        for c in cols:
            v = t.get(c, "")
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            row.append("" if v is None else str(v))
        tenants.append(row)

    files = {
        "landlords_active.json": [HEADER_L] + active,
        "landlords_closed.json": [HEADER_L] + closed,
        "tenants.json": [cols] + tenants,
    }
    for name, data in files.items():
        tmp = os.path.join(OUT, name + ".tmp")
        json.dump(data, open(tmp, "w"), ensure_ascii=False)
        os.replace(tmp, os.path.join(OUT, name))

    meta = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "spreadsheet_id": SPREADSHEET_ID,
        "tabs": {
            "Landlords Active": len(active) + 1,
            "Landlords Closed": len(closed) + 1,
            "Tenants": len(tenants) + 1,
        },
        "tenant_columns": len(cols),
    }
    json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), indent=1)
    print(f"exported: active={len(active)} closed={len(closed)} tenants={len(tenants)}"
          f" -> {OUT} (spreadsheet {SPREADSHEET_ID})")


if __name__ == "__main__":
    main()
