# scripts/ci

Support scripts for `.github/workflows/gate.yml`, the merge gate for this repo.

- `pii_paths.txt` — the CI mirror of the PII path shapes blocked by the local
  pre-commit hook, `~/.claude/bin/pii_path_guard.sh`. That hook is the source of
  truth; this file must be updated by hand whenever the hook's `PII_PATTERNS`
  array changes, or the two will drift apart.
- `check_pii_paths.sh` — checks a list of changed file paths against
  `pii_paths.txt`; used by the `pii-paths` job.
- `check_content_qa.py` — runs `scripts/content-rulecheck.py`'s per-file check
  against just the `public/insights/*.html` files that changed in a diff; used
  by the `content-qa` job.

## This does not block merges by itself

A GitHub Actions workflow only reports a status on a PR — it does not stop
anyone from clicking Merge unless branch protection is turned on to require it.
That toggle is a GitHub repo setting, not something in this codebase, so only
Winfred (as repo owner) can turn it on:

**Settings → Branches → add a rule for `main` → Require status checks to pass
before merging → select `gate`.**

This was Decision 2 from the CI/merge-gate audit (9 Sep 2026): build the gate
workflow first, then flip branch protection on separately once it has proven
green on a few real PRs.
