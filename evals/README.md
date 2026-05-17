# Crestbrick Agent Evals

Golden-case eval suite for the 9 HOT agents + 1 skill (`/afford`, the
replacement for the deprecated `affordability-modeler` agent).

## Layout

```
evals/
  <target>/
    cases.jsonl          # one JSON object per line — see contract below
  run.py                 # harness — invokes target, scores response
  _run.sh                # launchd wrapper
  results/
    <YYYY-MM-DD>.jsonl   # one record per case, written each weekly run
  _logs/
    <date>-<target>-<id>.log   # full stdout/stderr per case for debugging
    run-<utc-stamp>.log        # wrapper log for each launchd run
  README.md
```

## Targets covered

Agents: `chief-of-staff`, `chief-operating-officer`, `developer`, `security`,
`financial-advisor`, `seo-content-agent`, `competitor-scanner`,
`market-scout`, `property-researcher`.

Skills: `afford` (replaces the deprecated `affordability-modeler` agent —
prompts now invoke `/afford` directly).

## Case contract (per JSONL line)

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Short unique identifier within the target's file. |
| `input` | string | Prompt sent to the target. For skills, include the slash command verbatim. |
| `expected_contains` | string[] | Case-insensitive substrings that MUST appear in stdout. |
| `expected_excludes` | string[] | Case-insensitive substrings that MUST NOT appear in stdout. |
| `max_duration_ms` | int | Hard ceiling on per-case wall time. The harness also enforces a 240s safety timeout. |
| `kind` | string (optional) | `"agent"` (default) or `"skill"`. Skills bypass the `Use the X agent:` prefix. |

A case **passes** iff: rc is 0, all `expected_contains` are present, no
`expected_excludes` are present, and `duration_ms <= max_duration_ms`.

## Running

```bash
# All targets
python3 evals/run.py

# One target
python3 evals/run.py developer

# Subset
python3 evals/run.py developer security afford

# Skip the Telegram summary (useful when running locally)
python3 evals/run.py --no-telegram
```

Exit code: `0` if every case passes, `1` if any case fails, `2` for unknown
target names.

## Weekly schedule

Loaded via `~/Library/LaunchAgents/com.crestbrick.agent-evals.plist`. Runs
**Sundays 17:00 SGT (09:00 UTC)**. Wrapper: `evals/_run.sh`.

Reload after edits:
```bash
launchctl bootout  gui/$(id -u) ~/Library/LaunchAgents/com.crestbrick.agent-evals.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.crestbrick.agent-evals.plist
launchctl list | grep crestbrick.agent-evals
```

Trigger a manual run via launchd:
```bash
launchctl kickstart -k gui/$(id -u)/com.crestbrick.agent-evals
```

## Telegram summary

After each run the harness invokes `~/.claude/bin/tg-send.sh` with a
one-liner: `Agent evals: pass=X/Y, regressions: [target/id, ...]`.
If the helper isn't present the summary is skipped silently.

## Adding new cases

1. Edit `evals/<target>/cases.jsonl` — one JSON object per line, no trailing
   commas, no comments.
2. Keep `expected_contains` minimal and load-bearing — they're the spec, not
   a thesaurus. Prefer 2-4 keywords per case.
3. `expected_excludes` should catch known failure modes (refusal boilerplate,
   hallucinated contradictions, unsafe ops like `push --force` or `drop database`).
4. For a brand-new target, create the directory + `cases.jsonl`, then add the
   target name to `TARGETS` in `run.py`. If it's a skill, set `"kind":"skill"`
   on each case so the harness sends the slash command verbatim.
5. Quick sanity check: `python3 evals/run.py <target> --no-telegram` and
   inspect the `_logs/` sidecar.

## Interpreting results

Each line in `results/<date>.jsonl` is one case:

- `passed` — overall pass/fail
- `rc` — `claude -p` exit code (0 = clean; 124 = harness timeout)
- `duration_ms` — wall time of the invocation
- `failures` — list of human-readable reasons (missing keywords, excludes
  hit, rc, duration). Empty when `passed` is true.
- `response_preview` — first 500 chars of stdout. Full output is in the
  matching `_logs/<date>-<target>-<id>.log`.

Triage flow when something fails:
1. Open the sidecar log to see the full prompt + stdout + stderr.
2. If the agent refused or returned policy boilerplate → the case prompt may
   need rewording, or the agent prompt may have regressed.
3. If keywords are missing but the answer is correct in spirit → tighten or
   loosen `expected_contains`. The eval is the spec; update it deliberately.
4. If `rc` is non-zero with no stdout → check `claude` CLI auth/version.

## Pinning model versions

The harness shells out to whatever `claude` CLI is on PATH. That CLI uses
the user's currently-selected model (e.g. Sonnet 4.6 vs Opus 4.7). Pin
explicitly when:

- Comparing eval scores across model upgrades (e.g. baseline Sonnet 4.6,
  then re-run on 4.7 to quantify regressions / wins).
- Running CI gates that must remain stable across CLI updates.

To pin, prepend the model flag in `_run.sh`'s python invocation, or set
`CLAUDE_BIN` to a wrapper script that injects `--model <id>`. Record the
pinned model and date at the top of the weekly results file as a comment
line (the harness ignores blank lines but `jq` consumers may not — prefer
recording in commit messages or a sibling `results/<date>.notes.md`).

## Conventions

- All prompts are realistic Singapore property scenarios (ABSD, BSD,
  TDSR/MSR, CPF accrued, district deep-dives, EC eligibility, etc).
- No PII in cases. Use synthetic incomes/budgets.
- `results/*.jsonl` and `_logs/` are gitignore candidates if storage grows.
