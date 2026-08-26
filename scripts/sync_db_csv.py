#!/usr/bin/env python3
"""sync_db_csv.py — one-way sync, template JSON -> docs/ CSV mirrors.

The JSONs (_templates/tenant-db.json, landlord-db.json) are what every script
and the matchmaker actually read; the CSVs are Winfred's human mirror. Nothing
deterministic ever wrote the CSVs — only the AI refresh passes did — so every
backstop status change (stale sweeps, derive corrections, merges) left the CSV
lying until the next full AI pass. Found 21 Aug 2026: 493 stale cells.

Scalar whitelist only: complex fields (requirements dicts, free-text blobs the
AI curates) are left to the AI passes. Rows present in JSON but missing from
the CSV are appended with the whitelisted columns filled. CSV-only rows are
left alone (never deleted here). Dry run by default; --apply writes, with a
dated .bak alongside.

Landlord mirror is split by deal_type (23 Aug 2026): sale / sale-or-rent rows
route to docs/seller-database.csv, everything else stays in
docs/landlord-database.csv. A master row now routed to the other sheet is
dropped from this sheet's CSV — it is not a genuine CSV-only row, so the
never-delete rule above does not protect it.
"""
import csv, json, os, shutil, sys

ROOT = os.path.expanduser("~/crestbrick-consult")
APPLY = "--apply" in sys.argv

LANDLORD_JSON = os.path.join(ROOT, "_templates/landlord-db.json")
LANDLORD_CSV = os.path.join(ROOT, "docs/landlord-database.csv")
SELLER_CSV = os.path.join(ROOT, "docs/seller-database.csv")
LANDLORD_COLS = ["landlord_name", "phone", "status", "do_not_contact", "district",
                 "full_address", "rent_min", "rent_max", "viewing_availability",
                 "viewing_availability_updated", "last_contact", "last_refreshed",
                 "deal_type", "property_type"]


def is_sale(deal_type):
    return "sale" in (deal_type or "")


JOBS = [
    {
        "json": os.path.join(ROOT, "_templates/tenant-db.json"),
        "csv": os.path.join(ROOT, "docs/tenant-database.csv"),
        "rows_key": "tenants",
        "cols": ["name", "phone", "gender", "ethnicity", "nationality", "pass_type",
                 "occupation", "no_of_pax", "move_in_date", "lease_term_months",
                 "budget", "budget_min", "budget_max", "preferred_location", "status",
                 "last_contact", "district", "contact_state", "contact_state_updated",
                 "match_status"],
    },
    {
        "json": LANDLORD_JSON,
        "rows_key": None,  # landlords OR records
        "csv": LANDLORD_CSV,
        "cols": LANDLORD_COLS,
        "route": lambda dt: not is_sale(dt),
        "sibling_csv": SELLER_CSV,
    },
    {
        "json": LANDLORD_JSON,
        "rows_key": None,
        "csv": SELLER_CSV,
        "cols": LANDLORD_COLS,
        "route": is_sale,
        "sibling_csv": LANDLORD_CSV,
    },
]


def sval(v):
    if v is None or v is False:
        return ""
    if v is True:
        return "True"
    return str(v)


def sync(job, stamp):
    d = json.load(open(job["json"]))
    rows_all = d[job["rows_key"]] if job["rows_key"] else (d.get("landlords") or d.get("records") or [])
    route = job.get("route")
    rows_j = [r for r in rows_all if route(r.get("deal_type"))] if route else rows_all
    by_id = {str(r.get("id")): r for r in rows_j if r.get("id")}
    other_ids = (({str(r.get("id")) for r in rows_all if r.get("id")} - set(by_id))
                 if route else set())

    if os.path.exists(job["csv"]):
        with open(job["csv"], newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            rows_c = list(reader)
    else:
        sib = job.get("sibling_csv")
        fields = (csv.DictReader(open(sib)).fieldnames if sib and os.path.exists(sib)
                   else ["id"] + [c for c in job["cols"] if c != "id"])
        rows_c = []
    if route:
        rows_c = [r for r in rows_c if str(r.get("id")) not in other_ids]

    cols = [c for c in job["cols"] if c in fields]
    changed, appended = 0, 0
    seen = set()
    for r in rows_c:
        j = by_id.get(str(r.get("id")))
        if not j:
            continue
        seen.add(str(r.get("id")))
        for c in cols:
            jv = sval(j.get(c))
            if jv and r.get(c) != jv:
                r[c] = jv
                changed += 1
    for rid, j in by_id.items():
        if rid in seen:
            continue
        new = {k: "" for k in fields}
        new["id"] = rid
        for c in cols:
            new[c] = sval(j.get(c))
        rows_c.append(new)
        appended += 1
    name = os.path.basename(job["csv"])
    print(f"{name}: {changed} cell(s) updated, {appended} row(s) appended, "
          f"{len(rows_c)} row(s) total" + ("" if APPLY else " [DRY RUN]"))
    if APPLY and (changed or appended):
        if os.path.exists(job["csv"]):
            shutil.copy(job["csv"], job["csv"] + ".bak-sync-" + stamp)
        tmp = job["csv"] + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows_c)
        os.replace(tmp, job["csv"])


def main():
    import datetime
    stamp = datetime.date.today().strftime("%Y%m%d")
    for job in JOBS:
        sync(job, stamp)


if __name__ == "__main__":
    main()
