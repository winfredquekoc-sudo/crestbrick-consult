# Tenant Qualification Funnel Audit

**Date:** 2026-06-19
**Question asked:** "Why are we not qualifying prospective tenants?"
**Pipeline:** `src/wa-pipeline/wa_intake_runner.py` + `intake_engine.py` (launchd `com.crestbrick.wa-intake`, every 120s, `DRY_RUN=False` since 2026-06-17)

## TL;DR

Nothing was technically broken. WhatsApp MCP, the bridge, the form sender, and the profile parser all work. The automated qualifier barely ran for three reasons:

1. **The bot defers to Winfred, and Winfred replies to almost everything.** 150 of 189 conversations (79%) were in `manual_takeover`; 147 of those were genuine hand-typed replies from Winfred. By design the engine goes silent the moment he engages, so `qualify()` rarely got to run.
2. **Even complete profiles were intercepted.** 23 prospects had all 10 required fields, but 19 were under manual takeover, so only **2 ever reached an automated `QUALIFIED` verdict**.
3. **The intake state was polluted with non-tenants** — landlords Winfred was onboarding (e.g. "could you help me answer these questions so I can screen tenants") and one agent. They entered via outbound-first chats, which latch `manual_takeover` before the exclusion gate is ever reached.

## Verified funnel numbers (pre-change, 189 conversations)

| Metric | Count |
|---|---|
| Total tracked conversations | 189 |
| `manual_takeover` (engine silent) | 150 (79%) |
| — of which genuine Winfred hand-replies | 147 |
| — mis-latched on bot's own echo | 2 |
| Forms sent (`form_sent=True`) | 68 |
| Complete profile (all 10 fields) | 23 |
| Reached `QUALIFIED` verdict | **2** |

Profile completeness among the 68 who received a form:

| Fields filled | Prospects |
|---|---|
| 0 of 10 | 30 |
| 1–9 of 10 | 22 |
| 10 of 10 | 16 |

The parser is fine (16 fully parsed). Completion/attrition + manual interception are the gaps, not extraction.

## Root cause

`intake_engine.handle_event` (line ~554) returned `None` immediately under `manual_takeover` — it kept merging profile data but never screened or surfaced anything. Combined with Winfred being first into nearly every chat, the automation almost never completed the qualify → offer-viewing step. Landlords and agents inflated the denominator because outbound-first chats latch manual takeover before `excluded_reason()` runs.

## Changes made (2026-06-19)

### 1. Co-pilot mode — `intake_engine.py`, `wa_intake_runner.py`
The engine now stays silent to the prospect under manual takeover **but** still runs `qualify()` on a complete, listing-bound profile and returns a new `COPILOT_VERDICT` action. The runner Telegrams the verdict to Winfred ("X is QUALIFIED for Caspian, fits the landlord's criteria — worth offering a viewing"). It never messages the prospect (`text=None`) and fires once per distinct verdict (`copilot_sig` latch). Known landlords/agents are skipped.

### 2. Landlord exclusion by phone — `intake_engine.py`
`excluded_reason()` now consults the authoritative landlord DB (`_templates/landlord-db.json`) by phone/lid, not just the saved contact name. This catches landlords whose contact is saved under a first name and whose chats started outbound.

### 3. State purge — `scripts/clean_intake_state_landlords.py`
One-time, race-safe (holds the runner's flock), backed-up cleaner. Removed **30 of 190** non-tenant conversations:

| Reason | Count |
|---|---|
| In landlord DB | 17 |
| Landlord-side outbound phrasing | 12 |
| Already flagged agent | 1 |

Result: **160 tenant conversations** remain. Backup: `intake-state.json.bak-20260619-142103`.

## Verification

- `tests/wa-pipeline/test_intake_engine.py`: **129 passed, 0 failed** (added section 16 for co-pilot; existing manual-takeover and landlord-exclusion tests still green).
- Live runner ran once post-change: `processed N new messages, 0 engine actions, DRY_RUN=False`, exit 0.

## Update — made self-maintaining (same day)

The cleanup above was one-time, and within ~90 minutes 8 landlord records had already
re-accumulated (outbound-first chats latch `manual_takeover` before the exclusion gate). Closed
the loop so it does not rot back:

- **Engine source-guard:** `handle_event` now drops / never creates a record for any phone in
  `_landlord_pn_set()`, in either direction — the outbound-first re-pollution vector is closed.
  Verified: 0 re-pollution after a runner tick (was 8 in 90 min).
- **Nightly sweep + visibility:** `clean_intake_state_landlords.py` gained a `--quiet` funnel-health
  mode and is now invoked from the **existing** `~/.claude/bin/refresh-rental-dbs.sh` 00:00 job
  (no new launchd job). It `--apply`s the purge *after* the landlord DB is refreshed and Telegrams
  a one-line digest: `Active | Manual% | Qualified | Viewing | Incomplete | Forms sent`, plus
  purge counts and a count of landlords detected-but-not-in-the-DB (so they can be saved as
  contacts). Tests: 130 pass.

## Recommendations / follow-ups

1. **Watch the Telegram co-pilot pings for a few days.** If volume is high, tighten to QUALIFIED-only.
2. **Add the newly-found landlords to the landlord DB.** The conversational detector caught ~12 landlords not yet in `landlord-db.json` (matched by landlord-side outbound phrasing, not by phone). Re-run `scripts/clean_intake_state_landlords.py` to regenerate the list, then `refresh-rental-dbs` to fold them in so the phone gate covers them too.
3. **Decide the desired division of labour.** The bot is a co-pilot now (screens + tells Winfred). If full autopilot qualification is wanted, the behavioural change is for Winfred to let the form-reply cycle complete before replying by hand.
4. **Optional:** suppress `manual_takeover` mis-latch on the unit-info message when a listing has no live viewing slot (2 of 189 cases) — low priority.
