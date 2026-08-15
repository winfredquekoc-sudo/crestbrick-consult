#!/usr/bin/env python3
"""CTR experiment ledger for winfredquek.com title/meta description rewrites.

State: ~/.claude/state/ctr-ledger.jsonl (one JSON object per line):
  {"date": "YYYY-MM-DD", "path": "insights/foo", "field": "title"|"description",
   "old": "...", "new": "...", "reason": "..."}

Subcommands:
  log             append one entry by hand
  seed-from-commit  script the extraction of title/meta description changes out
                    of a git commit (a squash-merged PR) into ledger entries --
                    used to backfill the SEO agents' meta rewrites without
                    retyping them
  report          join ledger entries against the GSC brief JSONs
                  (~/.claude/state/gsc-briefs, see scripts/gsc-brief.py and
                  scripts/gsc-weekly-digest.py's history snapshots) by path,
                  print CTR before/after once >=14 days of post-change data
                  exists. Prints "pending" otherwise -- this is expected for
                  anything logged before the history/ snapshot mechanism
                  existed, since there is no pre-change snapshot to compare.

Usage:
  scripts/ctr-ledger.py log --path insights/foo --field title \\
      --old "Old Title" --new "New Title" --reason "why" [--date YYYY-MM-DD]
  scripts/ctr-ledger.py seed-from-commit <repo_path> <commit_sha> \\
      [--against <parent_sha>] [--reason "text"]
  scripts/ctr-ledger.py report [--briefs-dir DIR]
"""
import argparse, datetime, json, os, re, subprocess, sys

LEDGER = os.path.expanduser("~/.claude/state/ctr-ledger.jsonl")
BRIEFS_DIR_DEFAULT = os.path.expanduser("~/.claude/state/gsc-briefs")
MIN_DAYS_FOR_REPORT = 14

TITLE_RE = re.compile(r'^([+-])\s*<title>(.*?)</title>\s*$')
DESC_RE = re.compile(r'^([+-])\s*<meta name="description" content="([^"]*)"')


def append_entry(entry):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_ledger():
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def cmd_log(a):
    date = a.date or datetime.date.today().isoformat()
    entry = {"date": date, "path": a.path.strip("/"), "field": a.field,
              "old": a.old, "new": a.new, "reason": a.reason}
    append_entry(entry)
    print(f"[ctr-ledger] logged {entry['field']} change for {entry['path']} on {date}")


def repo_path_to_url_path(file_path):
    """public/insights/foo.html -> insights/foo, matching gsc-brief.py's
    rec()['path'] (https://winfredquek.com/insights/foo -> insights/foo)."""
    p = file_path
    if p.startswith("public/"):
        p = p[len("public/"):]
    if p.endswith(".html"):
        p = p[: -len(".html")]
    if p.endswith("/index"):
        p = p[: -len("/index")]
    return p


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def cmd_seed_from_commit(a):
    repo, commit = a.repo_path, a.commit
    against = a.against or f"{commit}^"

    try:
        subject = git(repo, "log", "-1", "--format=%s", commit).strip()
        commit_date = git(repo, "log", "-1", "--format=%cs", commit).strip()
    except subprocess.CalledProcessError as exc:
        sys.exit(f"[ctr-ledger] can't read commit {commit} in {repo}: {exc.stderr.strip()}")

    reason = a.reason or subject

    changed = git(repo, "diff", "--name-only", against, commit).strip().splitlines()
    html_files = [f for f in changed if f.startswith("public/") and f.endswith(".html")]
    if not html_files:
        print(f"[ctr-ledger] no public/*.html changes between {against} and {commit}")
        return

    seeded = 0
    for f in html_files:
        diff = git(repo, "diff", against, commit, "--", f)
        old_title = new_title = old_desc = new_desc = None
        for line in diff.splitlines():
            m = TITLE_RE.match(line)
            if m:
                if m.group(1) == "-":
                    old_title = m.group(2)
                else:
                    new_title = m.group(2)
                continue
            m = DESC_RE.match(line)
            if m:
                if m.group(1) == "-":
                    old_desc = m.group(2)
                else:
                    new_desc = m.group(2)

        path = repo_path_to_url_path(f)
        if old_title is not None and new_title is not None and old_title != new_title:
            append_entry({"date": commit_date, "path": path, "field": "title",
                           "old": old_title, "new": new_title, "reason": reason})
            seeded += 1
        if old_desc is not None and new_desc is not None and old_desc != new_desc:
            append_entry({"date": commit_date, "path": path, "field": "description",
                           "old": old_desc, "new": new_desc, "reason": reason})
            seeded += 1

    print(f"[ctr-ledger] seeded {seeded} entries from {commit} ({subject!r}, {commit_date}) "
          f"across {len(html_files)} changed page(s)")


def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def pool_for_snapshot(dir_path):
    """path -> {ctr_pct, pos, impr, clicks} from that snapshot's ctr+striking briefs."""
    pool = {}
    for name in ("brief_striking.json", "brief_ctr.json"):  # ctr last so it wins ties
        data = load_json(os.path.join(dir_path, name))
        if data:
            for row in data.get("targets", []):
                pool[row["path"]] = row
    return pool


def list_snapshots(briefs_dir):
    hist = os.path.join(briefs_dir, "history")
    if not os.path.isdir(hist):
        return []
    return sorted(d for d in os.listdir(hist) if os.path.isdir(os.path.join(hist, d)))


def cmd_report(a):
    entries = load_ledger()
    if not entries:
        print(f"[ctr-ledger] no entries in {LEDGER} yet -- run `log` or `seed-from-commit` first")
        return

    snapshots = list_snapshots(a.briefs_dir)
    # also consider the live (un-snapshotted) briefs as the freshest "after" point
    live_pool = pool_for_snapshot(a.briefs_dir)
    today = datetime.date.today()
    first_snapshot_date = datetime.date.fromisoformat(snapshots[0]) if snapshots else None

    print(f"{'date':<11} {'field':<12} {'path':<55} status")
    print("-" * 110)
    for e in entries:
        try:
            change_date = datetime.date.fromisoformat(e["date"])
        except ValueError:
            change_date = None

        days = (today - change_date).days if change_date else None
        row_id = f"{e['date']:<11} {e['field']:<12} {e['path'][:55]:<55}"

        if days is None:
            print(f"{row_id} pending (unparseable date)")
            continue
        if days < MIN_DAYS_FOR_REPORT:
            print(f"{row_id} pending ({days}/{MIN_DAYS_FOR_REPORT}d since change)")
            continue

        before_dir = None
        for d in snapshots:
            if datetime.date.fromisoformat(d) < change_date:
                before_dir = os.path.join(a.briefs_dir, "history", d)
        if before_dir is None:
            note = (f"no pre-change snapshot (history/ starts {first_snapshot_date})"
                     if first_snapshot_date else "no snapshot history yet")
            print(f"{row_id} pending ({days}d elapsed, but {note})")
            continue

        before_pool = pool_for_snapshot(before_dir)
        before = before_pool.get(e["path"])
        after = live_pool.get(e["path"])
        # fall back to the most recent snapshot strictly after the change date
        if after is None:
            for d in reversed(snapshots):
                if datetime.date.fromisoformat(d) >= change_date:
                    after = pool_for_snapshot(os.path.join(a.briefs_dir, "history", d)).get(e["path"])
                    if after:
                        break

        if before is None or after is None:
            missing = "before" if before is None else "after"
            print(f"{row_id} pending ({days}d elapsed, but no {missing}-change CTR data for this "
                  f"path -- it may be outside the tracked ctr/striking bands)")
            continue

        delta = after["ctr_pct"] - before["ctr_pct"]
        sign = "+" if delta >= 0 else ""
        print(f"{row_id} {before['ctr_pct']}% (pos {before['pos']}) -> "
              f"{after['ctr_pct']}% (pos {after['pos']})  [{sign}{delta:.2f}pp]")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_log = sub.add_parser("log", help="append one ledger entry by hand")
    p_log.add_argument("--path", required=True)
    p_log.add_argument("--field", required=True, choices=["title", "description"])
    p_log.add_argument("--old", required=True)
    p_log.add_argument("--new", required=True)
    p_log.add_argument("--reason", required=True)
    p_log.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    p_log.set_defaults(func=cmd_log)

    p_seed = sub.add_parser("seed-from-commit", help="extract title/meta description changes from a commit")
    p_seed.add_argument("repo_path")
    p_seed.add_argument("commit")
    p_seed.add_argument("--against", default=None, help="ref to diff against, default <commit>^")
    p_seed.add_argument("--reason", default=None, help="default: the commit subject")
    p_seed.set_defaults(func=cmd_seed_from_commit)

    p_report = sub.add_parser("report", help="CTR before/after per experiment")
    p_report.add_argument("--briefs-dir", default=BRIEFS_DIR_DEFAULT)
    p_report.set_defaults(func=cmd_report)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
