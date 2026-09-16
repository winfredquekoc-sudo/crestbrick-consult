#!/usr/bin/env python3
"""monday_brief.py — the six numbers from the 9 Sep 2026 business audit.

READ ONLY. Never writes, never sends, never prints names/phones/emails/
addresses — counts and aggregates only. Every source is best-effort: on any
missing/unreadable/empty source the section prints "n/a (why)" and the script
keeps going. Python 3 stdlib only, /usr/bin/python3 compatible.
Usage:
  scripts/ops/monday_brief.py            # markdown to stdout
  scripts/ops/monday_brief.py --json     # machine-readable JSON to stdout
"""
import argparse, glob, json, os, re, sqlite3, subprocess, sys
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".claude", "state")
REPO = os.path.join(HOME, "crestbrick-consult")
CLIENTS_DB = os.path.join(STATE, "clients.db")
MM_CLOSES = os.path.join(STATE, "matchmaker-closes.json")
LISTING_COVERAGE = os.path.join(REPO, "scripts", "listing_coverage.py")
INTAKE_STATE = os.path.join(STATE, "listing-templates", "intake-state.json")
INTAKE_DIR = os.path.dirname(INTAKE_STATE)
GSC_SUMMARY = os.path.join(STATE, "gsc-briefs", "brief_summary.json")
GSC_HISTORY = os.path.join(STATE, "gsc-briefs", "history")
HEALTH_HISTORY = os.path.join(STATE, "health-watchdog-history.json")
LAUNCHD_SNAPSHOTS = os.path.join(STATE, "launchd-snapshots")
MM_STATS = os.path.join(STATE, "matchmaker-stats.json")
MM_BUILD_HISTORY = os.path.join(STATE, "matchmaker-build-history.jsonl")
MM_DIGEST = os.path.join(REPO, "_local", "matchmaker-digest.html")
# Bridge script (not yet built) is expected to write this; both this brief and
# scripts/ops/log_deal.py's `import_mm` degrade gracefully when it is absent.
MM_CRM_SNAPSHOT = os.path.join(STATE, "matchmaker-crm-snapshot.json")
MM_FRESHNESS_HOURS = 26  # item 2/44 -- matches spec item 44's own digest staleness window
MM_BUILD_RECENCY_DAYS = 7  # item 6/48 -- below this, "digest missing" is a real warning
MM_PIPELINE_STAGES = {"agreed", "otp", "signed"}

def now():
    return datetime.now(SGT)

def na(reason):
    return {"ok": False, "lines": [f"n/a ({reason})"], "data": {"error": reason}}

def quarter_bounds(d):
    q = (d.month - 1) // 3
    start = d.replace(month=q * 3 + 1, day=1)
    end = (start.replace(month=q * 3 + 3, day=1) + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    return start.date().isoformat(), end.date().isoformat(), f"Q{q + 1} {d.year}"

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def nearest_backup(cutoff_dt, pattern):
    """Latest backup <= cutoff_dt; falls back to the earliest one available."""
    best = best_dt = earliest = earliest_dt = None
    for path in glob.glob(pattern):
        m = re.search(r"bak-(\d{8})-(\d{6})$", path)
        if not m:
            continue
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=SGT)
        if earliest_dt is None or dt < earliest_dt:
            earliest, earliest_dt = path, dt
        if dt <= cutoff_dt and (best_dt is None or dt > best_dt):
            best, best_dt = path, dt
    return (best, best_dt, True) if best else (earliest, earliest_dt, False)

def section_commission():
    try:
        con = sqlite3.connect(f"file:{CLIENTS_DB}?mode=ro", uri=True)
        cur = con.cursor()
        closed = "stage IN ('completion','keys','closed')"
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(commission_gross),0), "
                    f"COALESCE(SUM(commission_net),0) FROM deals WHERE {closed}")
        n_all, gross_all, net_all = cur.fetchone()
        qstart, qend, qlabel = quarter_bounds(now())
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(commission_gross),0), "
                    f"COALESCE(SUM(commission_net),0) FROM deals WHERE {closed} "
                    f"AND completion_date BETWEEN ? AND ?", (qstart, qend))
        n_q, gross_q, net_q = cur.fetchone()
        con.close()
        lines = [f"All time: {n_all} closed deals, gross ${gross_all:,}, net ${net_all:,}.",
                 f"{qlabel} (to date): {n_q} closed deals, gross ${gross_q:,}, net ${net_q:,}."]
        data = {"all_time": {"deals": n_all, "gross": gross_all, "net": net_all},
                "quarter": {"label": qlabel, "deals": n_q, "gross": gross_q, "net": net_q}}
    except Exception as e:
        return na(f"clients.db deals table unreadable: {e}")

    try:
        closes = load_json(MM_CLOSES)
        cutoff = (now() - timedelta(days=30)).date()
        recent = sum(1 for v in closes.values()
                     if _safe_date(v) and _safe_date(v) >= cutoff)
        lines.append(f"Rental closes (matchmaker): {len(closes)} total, {recent} in the last 30 days.")
        data["rental_closes"] = {"total": len(closes), "last_30d": recent}
    except Exception as e:
        lines.append(f"Rental closes: n/a (matchmaker-closes.json unreadable: {e})")
        data["rental_closes"] = {"error": str(e)}
    return {"ok": True, "lines": lines, "data": data}

def _safe_date(v):
    try:
        return datetime.fromisoformat(str(v)).date()
    except ValueError:
        return None

def section_portal_coverage():
    if not os.path.isfile(LISTING_COVERAGE):
        return na(f"{LISTING_COVERAGE} not found")
    try:
        out = subprocess.run([sys.executable, LISTING_COVERAGE, "--json"],
                              capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return na(f"listing_coverage.py exited {out.returncode}: {out.stderr.strip()[:200]}")
        rep = json.loads(out.stdout)
        posted = len(rep.get("posted", []))
        ready = len(rep.get("ready_unposted", []))
        incomplete = len(rep.get("incomplete", []))
        total = posted + ready + incomplete
        lines = [f"{posted} of {total} active rental landlords have a live portal listing "
                 f"({ready} ready-but-unposted, {incomplete} incomplete)."]
        return {"ok": True, "lines": lines,
                "data": {"posted": posted, "active_total": total,
                         "ready_unposted": ready, "incomplete": incomplete}}
    except Exception as e:
        return na(f"could not run/parse listing_coverage.py: {e}")

def _load_conversations():
    if not os.path.isfile(INTAKE_STATE):
        return None, na(f"{INTAKE_STATE} not found")
    try:
        return load_json(INTAKE_STATE).get("conversations", {}), None
    except Exception as e:
        return None, na(f"intake-state.json unreadable: {e}")

def section_leads_by_source():
    convs, err = _load_conversations()
    if err:
        return err
    pattern = os.path.join(INTAKE_DIR, "intake-state.json.bak-*")
    bpath, bdt, exact = nearest_backup(now() - timedelta(days=7), pattern)
    if not bpath:
        return na("no intake-state.json backups found to diff against for a 7 day window")
    try:
        old_keys = set(load_json(bpath).get("conversations", {}).keys())
    except Exception as e:
        return na(f"backup snapshot {os.path.basename(bpath)} unreadable: {e}")

    new_keys = set(convs.keys()) - old_keys
    counts = {}
    for k in new_keys:
        src = convs[k].get("source") or "unknown"
        counts[src] = counts.get(src, 0) + 1
    lines = [f"{len(new_keys)} new WhatsApp conversations in the last 7 days, by source:"]
    lines += [f"  {src}: {n}" for src, n in sorted(counts.items(), key=lambda kv: -kv[1])] or ["  (none)"]
    note = "exact 7 day snapshot" if exact else f"nearest available snapshot ({bdt.date()})"
    lines.append(f"[{note}]")
    return {"ok": True, "lines": lines,
            "data": {"new_total": len(new_keys), "by_source": counts, "baseline": note}}

def section_rental_funnel():
    convs, err = _load_conversations()
    if err:
        return err
    pattern = os.path.join(INTAKE_DIR, "intake-state.json.bak-*")
    bpath, bdt, exact = nearest_backup(now() - timedelta(days=30), pattern)
    lines, data = [], {}
    if not bpath:
        lines.append("Stage counts: n/a (no 30 day backup snapshot to diff against)")
        data["stage_counts"] = {"error": "no backup"}
    else:
        try:
            old_convs = load_json(bpath).get("conversations", {})
        except Exception:
            old_convs = {}
        touched = {k: v for k, v in convs.items() if k not in old_convs or old_convs.get(k) != v}
        counts = {}
        for v in touched.values():
            stage = v.get("stage") or "(none)"
            counts[stage] = counts.get(stage, 0) + 1
        note = "exact 30 day snapshot" if exact else f"nearest available snapshot ({bdt.date()})"
        lines.append(f"{len(touched)} conversation records touched in the last 30 days [{note}], by stage:")
        lines += [f"  {s}: {n}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1])]
        data["stage_counts"] = {"touched_total": len(touched), "by_stage": counts, "baseline": note}

    lines.append("Median days first contact to viewing confirmed: n/a (intake-state.json and "
                 "tenant-db.json carry no first-contact / viewing-confirmed timestamp fields).")
    data["median_days_to_viewing_confirmed"] = None
    return {"ok": True, "lines": lines, "data": data}

def section_commercial_intent():
    path = GSC_SUMMARY if os.path.isfile(GSC_SUMMARY) else None
    if not path:
        candidates = sorted(glob.glob(os.path.join(GSC_HISTORY, "*", "brief_summary.json")))
        path = candidates[-1] if candidates else None
    if not path:
        return na("no gsc-briefs/brief_summary.json (or history) found; GSC export not available offline")
    try:
        d = load_json(path)
        window = d.get("window") or [None, None]
        clicks, impr = d.get("clicks"), d.get("impressions")
        lines = [f"Search Console (window {window[0]} to {window[1]}): {clicks} clicks, {impr} impressions."]
        return {"ok": True, "lines": lines,
                "data": {"window": window, "clicks": clicks, "impressions": impr, "source_file": path}}
    except Exception as e:
        return na(f"{path} unreadable: {e}")

def section_health():
    lines, data = [], {}
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
        bad = []
        for row in out.stdout.splitlines():
            parts = row.split("\t")
            if len(parts) == 3 and "com.crestbrick" in parts[2] and parts[1] not in ("0", "-"):
                bad.append((parts[2], parts[1]))
        suffix = (": " + ", ".join(f"{l} ({s})" for l, s in bad)) if bad else "."
        lines.append(f"launchd: {len(bad)} com.crestbrick job(s) with a non-zero last exit status{suffix}")
        data["launchd_bad"] = [{"job": l, "status": s} for l, s in bad]
    except Exception as e:
        lines.append(f"launchd: n/a ({e})")
        data["launchd_bad"] = {"error": str(e)}

    for repo in ("crestbrick-consult", "sun-facing-checker"):
        try:
            out = subprocess.run(["gh", "pr", "list", "--repo", f"winfredquekoc-sudo/{repo}", "--json", "number"],
                                  capture_output=True, text=True, timeout=20)
            if out.returncode != 0:
                raise RuntimeError(out.stderr.strip()[:150])
            n = len(json.loads(out.stdout))
            lines.append(f"Open PRs on {repo}: {n}.")
            data[f"open_prs_{repo}"] = n
        except Exception as e:
            lines.append(f"Open PRs on {repo}: n/a ({e})")
            data[f"open_prs_{repo}"] = None

    try:
        newest = max(glob.glob(os.path.join(LAUNCHD_SNAPSHOTS, "*")), key=os.path.getmtime)
        ts = datetime.fromtimestamp(os.path.getmtime(newest), SGT).date().isoformat()
        lines.append(f"Newest launchd snapshot: {ts}.")
        data["newest_launchd_snapshot"] = ts
    except Exception as e:
        lines.append(f"Newest launchd snapshot: n/a ({e})")
        data["newest_launchd_snapshot"] = None

    try:
        hw = load_json(HEALTH_HISTORY)
        latest_ts, stale = None, 0
        for checks in hw.values():
            if not checks:
                continue
            last = checks[-1]
            t = datetime.fromisoformat(last["ts"])
            latest_ts = t if latest_ts is None or t > latest_ts else latest_ts
            if isinstance(last.get("value"), (int, float)) and last["value"] < 1.0:
                stale += 1
        lines.append(f"Health watchdog: last run {latest_ts.isoformat() if latest_ts else 'n/a'}, "
                     f"{len(hw)} checks tracked, {stale} below full freshness.")
        data["health_watchdog"] = {"last_run": latest_ts.isoformat() if latest_ts else None,
                                    "checks": len(hw), "below_full_freshness": stale}
    except Exception as e:
        lines.append(f"Health watchdog: n/a ({e})")
        data["health_watchdog"] = None
    return {"ok": True, "lines": lines, "data": data}

def _mm_build_history_lines(path):
    """Yields each parsed jsonl entry, corrupt lines skipped -- a build history
    file heals itself one bad line at a time (see build.py's own
    append_build_history() docstring on recovering a truncated tail)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def section_matchmaker():
    """item 2/44 -- read only, never calls the network: matchmaker-stats.json
    (build.py's own write_stats() output) + the tail of
    matchmaker-build-history.jsonl (build.py's build_history_entry(), item
    1/43 now carries pending_review + anomalies per entry) + an optional CRM
    snapshot bridge file for a forward looking pipeline line."""
    lines, data = [], {}
    try:
        stats = load_json(MM_STATS)
    except Exception as e:
        return na(f"{MM_STATS} unreadable: {e}")

    counts = stats.get("counts") or {}
    worklist_size = stats.get("worklist_size")
    lines.append(f"Listings {counts.get('available_listings', '?')}, tenants looking "
                 f"{counts.get('still_looking_tenants', '?')}, worklist size "
                 f"{worklist_size if worklist_size is not None else 'n/a'}.")
    data["counts"] = counts
    data["worklist_size"] = worklist_size

    history = list(_mm_build_history_lines(MM_BUILD_HISTORY))
    week_cutoff = (now() - timedelta(days=7)).isoformat()
    week_entries = [e for e in history if (e.get("ts") or "") >= week_cutoff]
    sizes = [e.get("worklist_size") for e in (week_entries or history[-7:])
             if isinstance(e.get("worklist_size"), int)]
    if sizes:
        direction = "up" if sizes[-1] > sizes[0] else "down" if sizes[-1] < sizes[0] else "flat"
        lines.append(f"Worklist size trend over the last {len(sizes)} build(s) this week: "
                     f"{sizes[0]} -> {sizes[-1]} ({direction}).")
        data["worklist_trend"] = {"first": sizes[0], "last": sizes[-1], "n": len(sizes)}
    else:
        lines.append("Worklist size trend: n/a (no build history yet).")
        data["worklist_trend"] = None

    last_anomaly = None
    for e in reversed(history):
        if e.get("anomalies"):
            last_anomaly = {"ts": e.get("ts"), "warnings": e["anomalies"]}
            break
    lines.append("Last anomaly warning: " +
                 (f"{last_anomaly['ts']} — {'; '.join(last_anomaly['warnings'])}"
                  if last_anomaly else "none found in build history"))
    data["last_anomaly"] = last_anomaly

    pending_review = (stats.get("health") or {}).get("pending_review")
    lines.append(f"Pending review stubs: {pending_review if pending_review is not None else 'n/a'}.")
    data["pending_review"] = pending_review

    stale_reasons = []
    gen_ts = stats.get("generated_ts")
    gen_dt = None
    if gen_ts:
        try:
            gen_dt = datetime.fromisoformat(gen_ts)
        except ValueError:
            gen_dt = None
    if gen_dt is None or (now() - gen_dt).total_seconds() > MM_FRESHNESS_HOURS * 3600:
        stale_reasons.append("stats file")
    # item 6/48 -- a machine that never runs build.py has no digest and never
    # will: warning "digest missing" every week on a box that is not part of
    # the build rotation is noise, not signal. Only treat a missing digest as
    # a freshness problem when the stats file itself shows a build inside the
    # last MM_BUILD_RECENCY_DAYS days (this machine is meant to be building);
    # otherwise say plainly this machine is not one that builds, once, and
    # leave it out of the freshness warning line entirely.
    recent_build = gen_dt is not None and (now() - gen_dt).total_seconds() <= MM_BUILD_RECENCY_DAYS * 86400
    not_built_here = False
    if os.path.isfile(MM_DIGEST):
        age_h = (now().timestamp() - os.path.getmtime(MM_DIGEST)) / 3600
        if age_h > MM_FRESHNESS_HOURS:
            stale_reasons.append("digest")
    elif recent_build:
        stale_reasons.append("digest missing")
    else:
        not_built_here = True
    lines.append(f"FRESHNESS WARNING: {', '.join(stale_reasons)} older than {MM_FRESHNESS_HOURS}h."
                 if stale_reasons else f"Freshness: within {MM_FRESHNESS_HOURS}h.")
    if not_built_here:
        lines.append("matchmaker not built on this machine")
    data["stale"] = stale_reasons

    if os.path.isfile(MM_CRM_SNAPSHOT):
        try:
            snap = load_json(MM_CRM_SNAPSHOT)
            deals = snap.get("deals") if isinstance(snap, dict) else snap
            deals = deals or []
            pipe = [d for d in deals if (d.get("stage") or "").lower() in MM_PIPELINE_STAGES]
            gross = sum(d.get("commission_gross") or d.get("gross") or 0 for d in pipe)
            gross_txt = f", potential gross ${gross:,.0f}" if gross else ""
            lines.append(f"Pipeline (not yet counted): {len(pipe)} deal(s) at agreed/otp/signed{gross_txt}.")
            data["pipeline_not_counted"] = {"count": len(pipe), "gross": gross}
        except Exception as e:
            lines.append(f"Pipeline (not yet counted): n/a ({MM_CRM_SNAPSHOT} unreadable: {e})")
            data["pipeline_not_counted"] = {"error": str(e)}
    else:
        lines.append("Pipeline (not yet counted): no CRM snapshot")
        data["pipeline_not_counted"] = None

    return {"ok": True, "lines": lines, "data": data}


SECTIONS = [
    ("Closes and commission", section_commission, "clients.db (deals table) + matchmaker-closes.json"),
    ("Portal coverage", section_portal_coverage, "scripts/listing_coverage.py --json (reused, read only)"),
    ("Leads by source (last 7 days)", section_leads_by_source, "listing-templates/intake-state.json diffed against its own backups"),
    ("Rental funnel (last 30 days)", section_rental_funnel, "listing-templates/intake-state.json diffed against its own backups; tenant-db.json"),
    ("Commercial intent", section_commercial_intent, "gsc-briefs/brief_summary.json (latest available Search Console pull)"),
    ("Health", section_health, "launchctl, gh pr list, launchd-snapshots/, health-watchdog-history.json"),
    ("Matchmaker", section_matchmaker,
     "matchmaker-stats.json + matchmaker-build-history.jsonl + matchmaker-crm-snapshot.json (bridge, optional)"),
]

def build_report():
    results = []
    for title, fn, source in SECTIONS:
        try:
            res = fn()
        except Exception as e:
            res = na(f"unexpected error: {e}")
        res["title"], res["source"] = title, source
        results.append(res)
    return results

def render_markdown(results):
    out = [f"# Monday brief {now().date().isoformat()}", ""]
    for i, r in enumerate(results, 1):
        out.append(f"## {i}. {r['title']}")
        out.extend(r["lines"])
        out.append(f"Source: {r['source']}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    results = build_report()
    if args.json:
        payload = {"generated": now().isoformat(), "sections": [
            {"title": r["title"], "ok": r["ok"], "data": r["data"], "source": r["source"]} for r in results]}
        print(json.dumps(payload, indent=2, default=str))
    else:
        sys.stdout.write(render_markdown(results))

if __name__ == "__main__":
    main()
