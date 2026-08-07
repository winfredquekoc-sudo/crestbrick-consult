#!/usr/bin/env python3
"""Pull live Search Console data and write the improvement briefs.

Produces four briefs the nightly build (and any human) can work from:
  brief_ctr.json       pages ranking top 10 but under clicked
  brief_striking.json  pages at position 11 to 21, one push from page one
  brief_cannibal.json  one query served by three or more of our own pages
  brief_summary.json   headline numbers, for trend tracking over time

Credentials come from ~/.claude/.gsc.env (mode 600, never committed).
Run ~/.claude/bin/gsc-auth-refresh.sh first so the access token is fresh.
"""
import argparse, collections, datetime, json, os, sys, urllib.parse, urllib.request

ENV = os.path.expanduser("~/.claude/.gsc.env")
API_HOST = "https://searchconsole.googleapis.com/webmasters/v3/sites"


def load_env():
    if not os.path.exists(ENV):
        sys.exit(f"[gsc-brief] {ENV} missing")
    env = {}
    for line in open(ENV):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    for k in ("GSC_OAUTH_TOKEN", "GSC_PROPERTY_URL"):
        if not env.get(k):
            sys.exit(f"[gsc-brief] {k} not set in {ENV}")
    return env


def query(env, dims, start, end, limit=25000):
    url = (f"{API_HOST}/{urllib.parse.quote(env['GSC_PROPERTY_URL'], safe='')}"
           f"/searchAnalytics/query")
    body = {"startDate": start, "endDate": end, "dimensions": dims, "rowLimit": limit}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {env['GSC_OAUTH_TOKEN']}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r).get("rows", [])
    except Exception as exc:
        sys.exit(f"[gsc-brief] API call failed for {dims}: {exc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.claude/state/gsc-briefs"))
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    env = load_env()
    # GSC lags about two days; asking for today returns a thin, misleading window.
    end = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()

    pages = query(env, ["page"], start, end)
    qp = query(env, ["query", "page"], start, end)
    devices = query(env, ["device"], start, end)

    pq = collections.defaultdict(list)
    for r in qp:
        pq[r["keys"][1]].append({"q": r["keys"][0], "impr": round(r["impressions"]),
                                 "clicks": round(r["clicks"]),
                                 "pos": round(r["position"], 1)})
    for k in pq:
        pq[k].sort(key=lambda x: -x["impr"])

    def rec(r, n=8):
        u = r["keys"][0]
        return {"url": u, "path": u.replace("https://winfredquek.com/", "").rstrip("/"),
                "impr": round(r["impressions"]), "clicks": round(r["clicks"]),
                "ctr_pct": round(100 * r["clicks"] / max(r["impressions"], 1), 2),
                "pos": round(r["position"], 1), "queries": pq.get(u, [])[:n]}

    pages.sort(key=lambda r: -r["impressions"])
    ctr = [rec(r) for r in pages
           if r["impressions"] >= 400 and r["position"] <= 10.9
           and (100 * r["clicks"] / max(r["impressions"], 1)) < 2.6]
    sd = [rec(r) for r in pages if 10.9 < r["position"] <= 21 and r["impressions"] >= 150]

    m = collections.defaultdict(list)
    for r in qp:
        m[r["keys"][0]].append(
            {"page": r["keys"][1].replace("https://winfredquek.com/", ""),
             "impr": round(r["impressions"]), "pos": round(r["position"], 1)})
    groups = [{"query": k, "total_impr": sum(x["impr"] for x in v),
               "pages": sorted(v, key=lambda x: -x["impr"])}
              for k, v in m.items()
              if len(v) >= 3 and sum(x["impr"] for x in v) >= 25]
    groups.sort(key=lambda g: -g["total_impr"])

    window = [start, end]
    out = {
        "brief_ctr.json": {"note": "Ranks top 10, under clicked. Rewrite title and "
                                   "meta description only.",
                           "window": window, "targets": ctr},
        "brief_striking.json": {"note": "Position 11 to 21. Answer the query the page "
                                        "ranks for but never addresses.",
                                "window": window, "targets": sd},
        "brief_cannibal.json": {"note": "One query, three or more of our own pages. "
                                        "Pick a winner, refocus or redirect the rest.",
                                "window": window, "groups": groups[:40]},
    }
    for name, payload in out.items():
        with open(os.path.join(a.out, name), "w") as fh:
            json.dump(payload, fh, indent=1)

    summary = {"window": window,
               "clicks": round(sum(r["clicks"] for r in devices)),
               "impressions": round(sum(r["impressions"] for r in devices)),
               "by_device": {r["keys"][0]: {"clicks": round(r["clicks"]),
                                            "impr": round(r["impressions"]),
                                            "pos": round(r["position"], 1)}
                             for r in devices},
               "ctr_targets": len(ctr), "striking_targets": len(sd),
               "cannibal_groups": len(groups)}
    with open(os.path.join(a.out, "brief_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)

    print(f"[gsc-brief] {window[0]} to {window[1]}: "
          f"{summary['clicks']} clicks, {summary['impressions']} impressions | "
          f"ctr:{len(ctr)} striking:{len(sd)} cannibal:{len(groups)} -> {a.out}")


if __name__ == "__main__":
    main()
