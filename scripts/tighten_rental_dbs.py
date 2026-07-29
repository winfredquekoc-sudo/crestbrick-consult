#!/usr/bin/env python3
"""
tighten_rental_dbs.py — one-shot DB hygiene for landlord-db.json + tenant-db.json.

Landlord DB:
  - parse rent_min/rent_max from rooms_and_rent text where the numeric fields are empty
  - derive `district` from postal code (SG sector map) or area keywords (TOWN_DISTRICT)
  - archive ghost records (no name AND no address AND not active) so matching/refresh skip them

Tenant DB:
  - re-derive preferred_districts from preferred_location for pool tenants missing them

Usage: tighten_rental_dbs.py [--apply]   (dry run by default; --apply backs up then writes)
"""
import json, os, re, sys, shutil, datetime, importlib.util

ROOT = os.path.expanduser("~/crestbrick-consult")
LDB = os.path.join(ROOT, "_templates/landlord-db.json")
TDB = os.path.join(ROOT, "_templates/tenant-db.json")
APPLY = "--apply" in sys.argv

spec = importlib.util.spec_from_file_location("blm", os.path.join(ROOT, "scripts/build_tenant_location_map.py"))
blm = importlib.util.module_from_spec(spec); spec.loader.exec_module(blm)

# SG postal sector (first 2 digits) -> district
_SECTORS = {
    "D1": "01 02 03 04 05 06", "D2": "07 08", "D3": "14 15 16", "D4": "09 10",
    "D5": "11 12 13", "D6": "17", "D7": "18 19", "D8": "20 21", "D9": "22 23",
    "D10": "24 25 26 27", "D11": "28 29 30", "D12": "31 32 33", "D13": "34 35 36 37",
    "D14": "38 39 40 41", "D15": "42 43 44 45", "D16": "46 47 48", "D17": "49 50 81",
    "D18": "51 52", "D19": "53 54 55 82", "D20": "56 57", "D21": "58 59",
    "D22": "60 61 62 63 64", "D23": "65 66 67 68", "D24": "69 70 71",
    "D25": "72 73", "D26": "77 78", "D27": "75 76", "D28": "79 80",
}
SECTOR_TO_D = {s: d for d, ss in _SECTORS.items() for s in ss.split()}

def district_from_postal(txt):
    m = re.search(r"\b[Ss]?(\d{6})\b", str(txt or ""))
    return SECTOR_TO_D.get(m.group(1)[:2]) if m else None

_RENT_RE = re.compile(r"\$\s?([\d,]{3,5})|(?<![\d.$])([1-9]\d{2,3})(?=\s*(?:/|per\s|pm\b|/mo|month))", re.I)
def rents_from_text(txt):
    vals = []
    for m in _RENT_RE.finditer(str(txt or "")):
        v = int((m.group(1) or m.group(2)).replace(",", ""))
        if 300 <= v <= 6000: vals.append(v)
    return (min(vals), max(vals)) if vals else (None, None)

def is_ghost(l):
    nm = str(l.get("landlord_name") or "").strip()
    ad = str(l.get("full_address") or "").strip().lower()
    no_name = nm in ("", "?", "(name unknown)", "(unidentified)")
    no_addr = ad in ("", "unknown", "?") or ad.startswith("unknown")
    return no_name and no_addr

def main():
    ldb = json.load(open(LDB)); tdb = json.load(open(TDB))
    changes = []

    for l in ldb["landlords"]:
        st = str(l.get("status", ""))
        # 1. rent numbers from free text
        if not l.get("rent_min") and not l.get("rent_max"):
            lo, hi = rents_from_text(l.get("rooms_and_rent"))
            if lo:
                l["rent_min"], l["rent_max"] = lo, hi
                changes.append(f"{l['id']}: rent {lo}..{hi} parsed from rooms_and_rent")
        # 2. district
        if not l.get("district"):
            d = (district_from_postal(l.get("full_address")) or
                 district_from_postal((l.get("requirements") or {}).get("postal")) or
                 (blm.districts_for(str(l.get("full_address", "")) + " " + str(l.get("property_type", ""))) or [None])[0])
            if d:
                l["district"] = d
                changes.append(f"{l['id']}: district -> {d}")
        # 3. ghost archive (never touch active records)
        if is_ghost(l) and not st.startswith(("active", "channel")) and not st.startswith("archived"):
            l["status"] = "archived (ghost: no name, no address; " + st + ")"
            changes.append(f"{l['id']}: archived ghost (was {st})")

    for t in tdb["tenants"]:
        if not t.get("preferred_districts") and t.get("preferred_location"):
            pd = blm.districts_for(t["preferred_location"])
            if pd:
                t["preferred_districts"] = pd
                if not t.get("district"): t["district"] = pd[0]
                changes.append(f"{t['id']}: districts {pd} from '{str(t['preferred_location'])[:40]}'")

    print(f"{len(changes)} change(s):")
    for c in changes: print("  ·", c)
    if not APPLY:
        print("\nDRY RUN — re-run with --apply to write (backups taken).")
        return
    stamp = datetime.date.today().strftime("%Y%m%d")
    shutil.copy(LDB, LDB + f".bak-tighten-{stamp}")
    shutil.copy(TDB, TDB + f".bak-tighten-{stamp}")
    for p, d in ((LDB, ldb), (TDB, tdb)):
        json.dump(d, open(p + ".tmp", "w"), indent=1, ensure_ascii=False)
        os.replace(p + ".tmp", p)
    print(f"\nWROTE both DBs (backups *.bak-tighten-{stamp})")

if __name__ == "__main__":
    main()
