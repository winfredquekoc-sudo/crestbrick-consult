#!/usr/bin/env python3
"""Rebuild the offline Matchmaker app from the current rental databases.
Runs the data export, stamps today's date, injects into template.html, and writes
the self-contained app to _local/matchmaker.html (gitignored — it holds PII).

Run any time after the nightly DB refresh:  python3 scripts/matchmaker/build.py
"""
import subprocess, os, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.expanduser("~/crestbrick-consult")
subprocess.run(["python3", os.path.join(HERE, "export_data.py")], check=True)

data = json.load(open(os.path.join(HERE, "matchmaker-data.json")))
data["generated"] = datetime.date.today().isoformat()          # dynamic freshness stamp
payload = json.dumps(data, ensure_ascii=False)

tpl = open(os.path.join(HERE, "template.html")).read()
out = tpl.replace("__DATA__", payload)
assert "__DATA__" not in out, "template placeholder not replaced"

outdir = os.path.join(ROOT, "_local"); os.makedirs(outdir, exist_ok=True)
p = os.path.join(outdir, "matchmaker.html")
open(p, "w").write(out)
print(f"built {p}  {round(len(out)/1024,1)} KB")
print(f"listings {data['counts']['available_listings']}  tenants {data['counts']['still_looking_tenants']}  stamp {data['generated']}")
