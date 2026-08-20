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
"""
import csv, json, os, shutil, sys

ROOT = os.path.expanduser("~/crestbrick-consult")
APPLY = "--apply" in sys.argv

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
        "json": os.path.join(ROOT, "_templates/landlord-db.json"),
        "rows_key": None,  # landlords OR records
        "csv": os.path.join(ROOT, "docs/landlord-database.csv"),
        "cols": ["landlord_name", "phone", "status", "do_not_contact", "district",
                 "full_address", "rent_min", "rent_max", "viewing_availability",
                 "viewing_availability_updated", "last_contact", "last_refreshed",
                 "deal_type", "property_type"],
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
    rows_j = d[job["rows_key"]] if job["rows_key"] else (d.get("landlords") or d.get("records") or [])
    by_id = {str(r.get("id")): r for r in rows_j if r.get("id")}
    with open(job["csv"], newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows_c = list(reader)
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
    print(f"{name}: {changed} cell(s) updated, {appended} row(s) appended"
          + ("" if APPLY else " [DRY RUN]"))
    if APPLY and (changed or appended):
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
