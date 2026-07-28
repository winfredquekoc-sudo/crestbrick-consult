# refresh-rental-dbs skill review and proposed code patches

Date authored: read from the system clock at review time (this file was produced 2026-06-16, SGT).
Reviewer scope: adversarial hardening of the `refresh-rental-dbs` skill that runs nightly via headless `claude -p`.

This file holds PROPOSED code patches only. No `.sh`, `.plist`, or `.py` file was edited, because the pipeline is LIVE and a run may still be finishing. The only file actually changed in this review is `SKILL.md` (it is read fresh each run, so it is safe to edit). Apply the patches below by hand when the pipeline is quiescent.

No hyphens, em dashes, or en dashes are used in any prose intended to be copied into data or messages.

---

## Confirmed bug observed in the live run

The run marker `~/.claude/state/refresh-rental-dbs-last.json` and the landlord DB both carry a FABRICATED timestamp:

```
marker  ran_at:       "2026-06-16T00:30:00+08:00"
landlord last_updated: "2026-06-16T00:30:00+08:00"
```

The real run finished around 04:58 SGT (verified by the newest `messages.db` rows at `2026-06-16 04:52:33+08:00` and by `date`). The model invented a round midnight time instead of reading the real system clock. The marker also reports `slots_captured: 10` alongside `landlords_updated: 10`, a one-slot-per-landlord pattern that is statistically implausible against the actual `viewing-availability.json` (most listings have zero slots), so the slot count is also suspect.

The primary remedy is in SKILL.md (now applied): every timestamp must be sourced from `bash date`, never composed by the model. The patches below harden the surrounding `.sh` so the wrapper does not silently accept a fabricated or partial run.

---

## Patch 1 (HIGH): wrapper should reject a fabricated future or stale marker

File: `~/.claude/bin/refresh-rental-dbs.sh`
Location: the light guard block, lines 17 to 21.

Rationale: today the guard only checks that `"date"` equals today and `"ok": true`. It does not look at `ran_at`, so a fabricated `ran_at` (or a marker from a run that never really executed) is accepted as a clean run and the next night is skipped. Add a sanity check that `ran_at` is within a recent window of the wall clock so an invented midnight value on a 5am run is caught and logged.

BEFORE:

```bash
# Light guard: if the marker already shows a successful run today, skip (skill also guards).
if [ -f "$MARKER" ] && grep -q "\"date\": *\"$TODAY\"" "$MARKER" 2>/dev/null && grep -q "\"ok\": *true" "$MARKER" 2>/dev/null; then
  log "already ran today — skipping"
  exit 0
fi
```

AFTER:

```bash
# Light guard: if the marker already shows a successful run today, skip (skill also guards).
if [ -f "$MARKER" ] && grep -q "\"date\": *\"$TODAY\"" "$MARKER" 2>/dev/null && grep -q "\"ok\": *true" "$MARKER" 2>/dev/null; then
  # Sanity check ran_at against the real clock: catch a fabricated round value.
  RAN_AT=$(grep -o '"ran_at": *"[^"]*"' "$MARKER" 2>/dev/null | sed 's/.*"\([^"]*\)"$/\1/')
  RAN_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "${RAN_AT%+*}+0800" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  SKEW=$(( NOW_EPOCH - RAN_EPOCH ))
  if [ "$RAN_EPOCH" -eq 0 ] || [ "$SKEW" -lt -3600 ] || [ "$SKEW" -gt 90000 ]; then
    log "marker ran_at ($RAN_AT) is implausible vs now (skew ${SKEW}s) — not trusting it, re-running"
  else
    log "already ran today — skipping"
    exit 0
  fi
fi
```

Note: the `date -j -f` form is the BSD (macOS) syntax. Validate on the target host before applying. If parsing proves brittle, a simpler safe fallback is to drop the epoch math and only re-run when `RAN_AT` is empty or literally ends in `00:00:00+08:00` (the classic fabricated value).

---

## Patch 2 (MEDIUM): pass the real clock into the prompt so the model has a reference

File: `~/.claude/bin/refresh-rental-dbs.sh`
Location: the `PROMPT` heredoc, lines 25 to 34, plus a new variable near `TODAY` at line 11.

Rationale: a headless model has no reliable wall clock unless it shells out. Giving it the exact ISO timestamp in the prompt removes the temptation to invent one and gives SKILL.md a value to echo. SKILL.md (applied) still instructs the model to prefer a fresh `date` call, but this is belt and braces.

BEFORE (near line 11):

```bash
TODAY=$(TZ=Asia/Singapore date +%Y-%m-%d)
```

AFTER:

```bash
TODAY=$(TZ=Asia/Singapore date +%Y-%m-%d)
NOW_SGT=$(TZ=Asia/Singapore date "+%Y-%m-%dT%H:%M:%S%z")
```

BEFORE (first line of PROMPT, line 25):

```bash
PROMPT="Run the refresh-rental-dbs skill now. This is the scheduled nightly midnight run.
```

AFTER:

```bash
PROMPT="Run the refresh-rental-dbs skill now. This is the scheduled nightly midnight run.
The real current time is $NOW_SGT (Asia/Singapore). Use the system clock (bash date) for every timestamp you write. Do NOT invent or round any time. Today is $TODAY.
```

---

## Patch 3 (MEDIUM): single-writer lock for the runner (OWNER ALREADY PLANS THIS)

File: `~/.claude/bin/refresh-rental-dbs.sh`
Status: the owner is already implementing a concurrency lock separately, so this is NOT applied in the `.sh` and is documented here only for completeness.

Concern: if the nightly job and a manual on-demand invocation overlap, two `claude -p` processes can both write `landlord-db.json`, `tenant-db.json`, and `viewing-availability.json`. The Python writers use atomic temp-then-move and `book_slot` uses `flock`, but the SKILL.md writers are sequential read-modify-write done by the model and can interleave at the file level. A `flock`-based lock on a lockfile around the whole `claude -p` invocation prevents this. Recommended shape (for the owner to fold into their planned lock):

```bash
LOCK="$HOME/.claude/state/refresh-rental-dbs.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another refresh-rental-dbs run holds the lock — exiting"
  exit 0
fi
```

No action requested in this review beyond noting it.

---

## Patch 4 (LOW): timezone note on the SQL since-window (verification, likely no code change)

File: none required, but verify in `wa_intake_runner.py` reads and the SKILL.md SQL.

Observation: `messages.db.timestamp` is stored as `2026-06-16 04:52:33+08:00` (space separated, SGT with explicit offset). SKILL.md compares it against `:since` derived from `last_contact` (a date like `2026-06-15`). Lexical string comparison `'2026-06-16 04:52:33+08:00' >= '2026-06-14'` is correct because the date prefix sorts first, so the existing approach works. The risk is only if anyone ever passes `:since` as a UTC ISO value with a `T` separator or a `Z` suffix, which would still sort correctly for the date prefix but is confusing. SKILL.md (applied) now states explicitly that `:since` must be a plain `YYYY-MM-DD` SGT date string and that `messages.db` is already SGT, so no UTC conversion is needed. No `.py` change required.

---

## Summary of code patches

| # | Priority | File | Applied here | Effect |
|---|----------|------|--------------|--------|
| 1 | HIGH | refresh-rental-dbs.sh | No (proposed) | Reject fabricated or stale `ran_at`, force a real re-run |
| 2 | MEDIUM | refresh-rental-dbs.sh | No (proposed) | Inject real ISO clock into the prompt |
| 3 | MEDIUM | refresh-rental-dbs.sh | No (owner already planning) | Single-writer lock |
| 4 | LOW | none | No (verify only) | Confirm SGT since-window, no UTC skew |

All SKILL.md changes are applied directly in `~/crestbrick-consult/.claude/skills/refresh-rental-dbs/SKILL.md`.
