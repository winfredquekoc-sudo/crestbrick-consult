# Proposed fix: deterministic step 5b discovery for refresh-rental-dbs

Status: PROPOSED PATCH ONLY. Nothing in `~/.claude/bin/refresh-rental-dbs.sh` (the live,
5x/day script, outside this repo) has been touched. Apply the two snippets below by hand
when the pipeline is quiescent (between slots), then watch the next run's log.

## What this fixes

`SKILL.md` Part A step 5b asks the headless model itself to author a raw SQL scan of
`messages.db`, resolve `@lid` to phone, dedupe against `landlord-db.json`,
`tenant-db.json`, and `dm-followup-exclude.json`, and only then classify each surviving
chat as landlord / tenant / agent / colleague / ambiguous. Since the 27 Aug 2026 switch
to Haiku for every slot, this has intermittently produced `landlords_scanned=0` in the
run marker for days in a row, and nothing was watching that field across runs.

This repo now has two new deterministic scripts (committed on this branch, not yet
wired into the live wrapper):

- `scripts/discovery_candidates.py` — does the SQL scan, the lid resolution, and the
  three-way dedupe deterministically. Emits a narrowed candidate list as JSON. The model
  still classifies; it never authors SQL or dedupe logic again.
- `scripts/discovery_watchdog.py` — appends a counts-only snapshot to a rolling history
  file every slot and, if the scanned metric has been zero for N consecutive daytime
  slots while inbound WhatsApp volume was non-zero, prints a distinct
  `DISCOVERY_WATCHDOG_ALERT:` line. Detection only; sends nothing itself.

## Patch 1: run discovery_candidates.py right after the delta pre-extractor, fold its
output into the same delta file the model already reads

File: `~/.claude/bin/refresh-rental-dbs.sh`
Location: immediately after the `PYDELTA` heredoc closes (the block that writes
`$DELTA`), before the `MCPCFG=` line.

BEFORE:

```bash
json.dump({"generated": dt.datetime.now().isoformat(), "since": since,
           "chats_with_new_activity": len(chats), "id_allocation": id_alloc,
           "chats": out_chats},
          open(out, "w"), ensure_ascii=False, indent=1)
print(f"delta: {len(chats)} chats with new activity since {since} -> {out}")
PYDELTA

# ALL slots: the delta file (plus read-only sqlite3 on the 21:00 slot) replaces WhatsApp
# MCP access, and no Sheets push happens in the main pass, so load NO MCP servers —
# google-workspace alone is ~110 tool schemas of per-turn context the model re-reads on
# every single turn. The 21:00 Sheets sync runs as its own small call further down.
MCPCFG="$HOME/.claude/mcp-configs/none.json"
```

AFTER:

```bash
json.dump({"generated": dt.datetime.now().isoformat(), "since": since,
           "chats_with_new_activity": len(chats), "id_allocation": id_alloc,
           "chats": out_chats},
          open(out, "w"), ensure_ascii=False, indent=1)
print(f"delta: {len(chats)} chats with new activity since {since} -> {out}")
PYDELTA

# Deterministic replacement for step 5b's model-authored SQL: scan messages.db for
# chats whose FIRST EVER message is within 14 days, resolve lid to phone, and dedupe
# against both databases and the exclusion list -- all in plain Python, zero tokens.
# The model's only remaining job for step 5b is CLASSIFYING what survives (never
# authoring SQL again). Non-fatal: a failed scan degrades to "no candidates this run"
# rather than blocking the slot, and is visible in the log either way.
CANDIDATES="$HOME/.claude/state/discovery-candidates.json"
if /usr/bin/python3 "$PROJECT/scripts/discovery_candidates.py" --since-days 14 --out "$CANDIDATES" >> "$LOG" 2>&1; then
  log "discovery_candidates ran"
else
  log "discovery_candidates FAILED (non-fatal, step 5b sees zero candidates this run)"
fi
# Fold the candidate list into the SAME delta file the model already reads, so no new
# file wiring is needed in the prompt beyond naming the new key.
/usr/bin/python3 - "$DELTA" "$CANDIDATES" <<'PYFOLD' >> "$LOG" 2>&1 || true
import json, sys
delta_path, cand_path = sys.argv[1], sys.argv[2]
try:
    cand = json.load(open(cand_path)).get("candidates", [])
except Exception:
    cand = []
d = json.load(open(delta_path))
d["discovery_candidates"] = cand
tmp = delta_path + ".tmp"
json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
import os; os.replace(tmp, delta_path)
print(f"folded {len(cand)} discovery candidate(s) into delta")
PYFOLD

# ALL slots: the delta file (plus read-only sqlite3 on the 21:00 slot) replaces WhatsApp
# MCP access, and no Sheets push happens in the main pass, so load NO MCP servers —
# google-workspace alone is ~110 tool schemas of per-turn context the model re-reads on
# every single turn. The 21:00 Sheets sync runs as its own small call further down.
MCPCFG="$HOME/.claude/mcp-configs/none.json"
```

### Matching prompt change

Location: the `PROMPT=` heredoc, the bullet that currently reads (non-21:00 branch):

```
- Do NOT query messages.db or any other message store; work ONLY from the delta file. No MCP tools are loaded this run.
```

AFTER (both the 21:00 and non-21:00 branches — replace step 5b's instruction to write
SQL with an instruction to classify the pre-built list):

```
- Do NOT query messages.db or any other message store; work ONLY from the delta file. No MCP tools are loaded this run.
- Part A step 5b is now PRE-SCANNED: $DELTA's "discovery_candidates" array already has every unlabelled chat inside the 14 day window, deduped against both databases and the exclusion list, with a resolved phone, contact name, first/last message date, and a few message snippets. Do NOT write or simulate any SQL for step 5b. Your ONLY job for step 5b is to CLASSIFY each entry in that array using the exact landlord / tenant / agent / colleague / ambiguous rules in SKILL.md step 5b, and add confirmed landlords exactly per those conventions (contact_label_source: "content sweep (contact not yet labelled)"). If discovery_candidates is missing or empty, say so in the digest instead of guessing.
```

## Patch 2: run the watchdog once per slot, right after the marker is written

File: `~/.claude/bin/refresh-rental-dbs.sh`
Location: right after `printf '%s' "$RUNSTART" > "$LASTTS_FILE"` and the `tail -c 1200
"$TMP" >> "$LOG"` cleanup that follows the `claude -p` call (i.e. as soon as the model
run has finished and its marker write is trusted) — before the sanitizer backstop block.

BEFORE:

```bash
log "claude run finished"
rm -f "$CKPT"
printf '%s' "$RUNSTART" > "$LASTTS_FILE"
tail -c 1200 "$TMP" >> "$LOG" 2>/dev/null || true
rm -f "$TMP" "$TMP_ERR"

# Deterministic backstop: never let a false "URA quota" rental reason persist in the
```

AFTER:

```bash
log "claude run finished"
rm -f "$CKPT"
printf '%s' "$RUNSTART" > "$LASTTS_FILE"
tail -c 1200 "$TMP" >> "$LOG" 2>/dev/null || true
rm -f "$TMP" "$TMP_ERR"

# scanned=0 alarm: record this slot's discovery counts and alert if step 5b has gone
# quiet for a full day's worth of slots while WhatsApp was not quiet. Detection only —
# never sends anything itself; the wrapper does the one Telegram line on an alert.
WD_LINE=$(/usr/bin/python3 "$PROJECT/scripts/discovery_watchdog.py" --slot "$SLOT" \
  --candidates-file "$HOME/.claude/state/discovery-candidates.json" \
  --metric candidates_found --min-consecutive-zero 5 2>&1) || true
printf '%s\n' "$WD_LINE" >> "$LOG"
case "$WD_LINE" in
  DISCOVERY_WATCHDOG_ALERT:*)
    printf '%s\nWinfred Quek | CEA R073319H' "$WD_LINE" | bash "$HOME/.claude/bin/telegram_send.sh" 540127870 2>/dev/null || true
    ;;
esac

# Deterministic backstop: never let a false "URA quota" rental reason persist in the
```

Note: `--metric candidates_found` uses the NEW deterministic signal from Patch 1 (harder
to fake than a model-reported count). If Patch 1 is applied later than Patch 2, or is
skipped, drop `--metric candidates_found` to fall back to the existing marker field
`landlords_scanned` (the default), which still catches the original symptom on its own,
just on a signal the model itself computes.

## Review checklist for Winfred

- [ ] Confirm `scripts/discovery_candidates.py` and `scripts/discovery_watchdog.py` read
      correctly against the real `messages.db` / `whatsapp.db` paths on this Mac (both
      default to the paths already used elsewhere in the wrapper).
- [ ] Apply Patch 1 and Patch 2 by hand to `~/.claude/bin/refresh-rental-dbs.sh` while no
      slot is running (check the lock dir `~/.claude/state/refresh-rental-dbs.lock.d`
      is absent first).
- [ ] Watch one full slot's log (`~/.claude/bin/refresh-rental-dbs.log`) for
      `discovery_candidates ran` and `discovery_watchdog: OK ...` lines.
- [ ] After 5+ slots, confirm `~/.claude/state/discovery-watchdog-history.json` is
      accumulating one entry per slot (counts only, no PII).
- [ ] Nothing here sends a WhatsApp message or Telegram anything except the one new
      alert line, and only when the alarm actually fires.
