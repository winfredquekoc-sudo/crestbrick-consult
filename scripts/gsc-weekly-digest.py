#!/usr/bin/env python3
"""Weekly week-over-week digest from the GSC improvement briefs.

scripts/gsc-brief.py writes brief_summary/brief_ctr/brief_striking/
brief_cannibal.json to ~/.claude/state/gsc-briefs on every run (rolling
30-day window, refreshed weekly). This script snapshots that current set
into a dated history dir, diffs against the most recent PRIOR snapshot,
and composes a plain text Telegram digest: rolling clicks/impressions vs
last week's snapshot, top CTR movers, and striking-distance (position
11-21) churn.

Run scripts/gsc-brief.py first so the briefs being snapshotted are fresh.

Telegram send matches the existing pattern in ~/.claude/bin/wa-loose-ends.sh:
plain text POST to the Bot API sendMessage endpoint, no parse_mode -- an
unescaped Markdown send has silently dropped alerts before (see
project_telegram_markdown_dropped_alerts.md).

Usage: scripts/gsc-weekly-digest.py [--dry-run] [--briefs-dir DIR]
"""
import argparse, datetime, json, os, shutil, sys, urllib.parse, urllib.request

BRIEFS_DIR_DEFAULT = os.path.expanduser("~/.claude/state/gsc-briefs")
ENV_FILE = os.path.expanduser("~/.telegram-bot.env")
SNAPSHOT_FILES = ["brief_summary.json", "brief_ctr.json", "brief_striking.json", "brief_cannibal.json"]
TOP_N = 3
LIST_CAP = 8


def log(msg, briefs_dir):
    path = os.path.join(os.path.dirname(briefs_dir.rstrip("/")), "gsc-weekly-digest.log") \
        if os.path.basename(briefs_dir) == "gsc-briefs" else os.path.join(briefs_dir, "gsc-weekly-digest.log")
    now = datetime.datetime.now().isoformat()
    try:
        with open(path, "a") as fh:
            fh.write(f"[{now}] {msg}\n")
    except OSError:
        pass


def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_env_file():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


def snapshot_today(briefs_dir, today):
    """Copy today's briefs into history/YYYY-MM-DD. Returns (dest_dir, missing_files)."""
    dest = os.path.join(briefs_dir, "history", today)
    os.makedirs(dest, exist_ok=True)
    missing = []
    for name in SNAPSHOT_FILES:
        src = os.path.join(briefs_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, name))
        else:
            missing.append(name)
    return dest, missing


def find_prior_snapshot(briefs_dir, today):
    """Most recent history/YYYY-MM-DD dir that isn't today's."""
    hist = os.path.join(briefs_dir, "history")
    if not os.path.isdir(hist):
        return None
    dates = sorted(
        d for d in os.listdir(hist)
        if d != today and os.path.isdir(os.path.join(hist, d))
    )
    return os.path.join(hist, dates[-1]) if dates else None


def index_by_path(brief_json, key="targets"):
    if not brief_json:
        return {}
    return {row["path"]: row for row in brief_json.get(key, [])}


def fmt_num_delta(cur, prev):
    d = cur - prev
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:,}"


def fmt_pct_delta(cur, prev):
    d = cur - prev
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.2f}pp"


def section(title, lines, cap=LIST_CAP):
    if not lines:
        return None
    out = [f"{title} ({len(lines)})"]
    out += ["- " + l for l in lines[:cap]]
    if len(lines) > cap:
        out.append(f"  and {len(lines) - cap} more")
    return "\n".join(out)


def build_digest(cur_dir, prev_dir, today):
    cur_summary = load_json(os.path.join(cur_dir, "brief_summary.json"))
    if not cur_summary:
        return None, "no brief_summary.json in today's snapshot -- run scripts/gsc-brief.py first"

    cur_ctr = load_json(os.path.join(cur_dir, "brief_ctr.json"))
    cur_sd = load_json(os.path.join(cur_dir, "brief_striking.json"))
    cur_ctr_by_path = index_by_path(cur_ctr)
    cur_sd_by_path = index_by_path(cur_sd)
    # every page we have a comparable ctr_pct for this week, ctr-watchlist + striking-band
    cur_ctr_pool = {**cur_sd_by_path, **cur_ctr_by_path}

    lines = [f"GSC weekly digest, {datetime.date.today().strftime('%-d %b %Y')}"]
    win = cur_summary.get("window", ["?", "?"])
    lines.append(f"Window: {win[0]} to {win[1]} (rolling 30d)")

    if prev_dir is None:
        lines.append(f"\nClicks {cur_summary['clicks']:,} | Impressions {cur_summary['impressions']:,}")
        lines.append("\nNo prior snapshot yet -- this is the first run, week over week starts next time.")
        return "\n".join(lines), None

    prev_summary = load_json(os.path.join(prev_dir, "brief_summary.json"))
    prev_ctr = load_json(os.path.join(prev_dir, "brief_ctr.json"))
    prev_sd = load_json(os.path.join(prev_dir, "brief_striking.json"))
    prev_ctr_by_path = index_by_path(prev_ctr)
    prev_sd_by_path = index_by_path(prev_sd)
    prev_ctr_pool = {**prev_sd_by_path, **prev_ctr_by_path}
    prev_date = os.path.basename(prev_dir)

    if prev_summary:
        c, i = cur_summary["clicks"], cur_summary["impressions"]
        pc, pi = prev_summary["clicks"], prev_summary["impressions"]
        lines.append(f"\nClicks {c:,} ({fmt_num_delta(c, pc)} vs {prev_date}) | "
                      f"Impressions {i:,} ({fmt_num_delta(i, pi)} vs {prev_date})")
    else:
        lines.append(f"\nClicks {cur_summary['clicks']:,} | Impressions {cur_summary['impressions']:,} "
                      f"(prior snapshot at {prev_date} has no brief_summary.json to compare)")

    # ---- CTR movers: pages with a ctr_pct in both this week's and last week's pool ----
    movers = []
    for path, cur_row in cur_ctr_pool.items():
        prev_row = prev_ctr_pool.get(path)
        if prev_row is None:
            continue
        delta = cur_row["ctr_pct"] - prev_row["ctr_pct"]
        movers.append((delta, path, cur_row, prev_row))
    movers.sort(key=lambda m: -m[0])

    wins = [m for m in movers if m[0] > 0][:TOP_N]
    losses = sorted([m for m in movers if m[0] < 0], key=lambda m: m[0])[:TOP_N]

    def mover_line(m):
        delta, path, cur_row, prev_row = m
        return (f"{path}: {prev_row['ctr_pct']}% -> {cur_row['ctr_pct']}% "
                f"({fmt_pct_delta(cur_row['ctr_pct'], prev_row['ctr_pct'])}, pos {cur_row['pos']})")

    if wins:
        lines.append("\nTop CTR wins:")
        lines += ["- " + mover_line(m) for m in wins]
    if losses:
        lines.append("\nTop CTR losses:")
        lines += ["- " + mover_line(m) for m in losses]
    if not wins and not losses:
        lines.append(f"\nNo CTR movers -- no page appears in both this week's and {prev_date}'s "
                      f"tracked (ctr/striking) lists.")

    # ---- striking distance (11-21) churn ----
    prev_paths = set(prev_sd_by_path)
    cur_paths = set(cur_sd_by_path)

    confirmed_top10 = sorted(
        [p for p in prev_paths if p in cur_ctr_by_path],
        key=lambda p: cur_ctr_by_path[p]["pos"]
    )
    left_band_unconfirmed = sorted(prev_paths - cur_paths - set(confirmed_top10))
    new_entrants = [p for p in cur_sd["targets"]] if cur_sd else []
    new_entrants = [row for row in new_entrants if row["path"] not in prev_paths]

    top10_lines = [f"{p}: now pos {cur_ctr_by_path[p]['pos']}, {cur_ctr_by_path[p]['ctr_pct']}% CTR"
                    for p in confirmed_top10]
    s = section("Moved into top 10 (confirmed, still under clicked)", top10_lines)
    if s:
        lines.append("\n" + s)

    if left_band_unconfirmed:
        s = section(
            "Left the 11-21 band, unconfirmed why (could be top 10 with healthy CTR, "
            "or impressions fell below the tracking threshold -- check GSC directly)",
            left_band_unconfirmed)
        if s:
            lines.append("\n" + s)

    new_lines = [f"{row['path']}: pos {row['pos']}, {row['impr']:,} impr" for row in new_entrants]
    s = section("New striking distance entrants (11-21)", new_lines)
    if s:
        lines.append("\n" + s)

    return "\n".join(lines), None


def send_telegram(text):
    env = {**load_env_file(), **os.environ}
    tok = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_WINFRED_CHAT_ID", "540127870")
    if not tok:
        return False, "no TELEGRAM_BOT_TOKEN (checked ~/.telegram-bot.env and environment)"
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            urllib.parse.urlencode({"chat_id": chat, "text": text}).encode(),
            timeout=15,
        )
        return True, None
    except OSError as exc:
        return False, str(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the digest instead of sending it")
    ap.add_argument("--briefs-dir", default=BRIEFS_DIR_DEFAULT,
                     help="dir with brief_*.json (default ~/.claude/state/gsc-briefs)")
    a = ap.parse_args()

    today = datetime.date.today().isoformat()
    cur_dir, missing = snapshot_today(a.briefs_dir, today)
    if missing:
        log(f"snapshot missing files: {missing}", a.briefs_dir)
    prev_dir = find_prior_snapshot(a.briefs_dir, today)

    digest, err = build_digest(cur_dir, prev_dir, today)
    if err:
        log(f"build_digest failed: {err}", a.briefs_dir)
        print(f"[gsc-weekly-digest] {err}", file=sys.stderr)
        sys.exit(1)

    if a.dry_run:
        print(digest)
        log("dry-run, not sent", a.briefs_dir)
        return

    ok, err = send_telegram(digest)
    if ok:
        log("digest sent", a.briefs_dir)
    else:
        log(f"telegram send failed: {err}", a.briefs_dir)
        print(f"[gsc-weekly-digest] send failed: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
