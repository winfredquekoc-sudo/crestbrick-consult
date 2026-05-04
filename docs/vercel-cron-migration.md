# Vercel Cron Migration Plan — heavy `claude -p` jobs

**Status:** scaffolding only. Nothing activated. Winfred reviews + approves before deploy.

## Why move

Today, ~15 scripts in `~/.claude/bin/` shell out to `claude -p` for 1–10 minutes each. They run via launchd on Winfred's Mac. Problems:

- Mac must be awake (or scheduled to wake) at fire time.
- Each run pins ~1–2 GB RAM + a CPU core during the LLM call.
- Single Mac = single point of failure. Travel/power loss = silent skip.
- Hard to observe — log files, no centralized retry/alert.

Moving stateless content jobs to Vercel Cron Functions:

- Runs in the cloud. Mac can sleep / be elsewhere.
- Free tier (Hobby) covers ~100K invocations/month — Winfred's needs are <1K/month.
- Vercel auto-retries on failure, surfaces logs in the dashboard.
- Same `ANTHROPIC_API_KEY` → identical cost (just billed from Vercel's egress, not Mac's).

## Candidate scoring

Walked all `~/.claude/bin/*.sh` scripts that call `claude -p`. Scored on:

| Criterion | Why it matters |
|---|---|
| No `clients.db` access | Vercel can't reach local SQLite |
| No `~/.claude/state` write-and-read across runs | Vercel functions are stateless; would need KV/Blob |
| No WhatsApp bridge dependency | WA bridge is Mac-local Go binary |
| Output deliverable from cloud | Telegram API + Vercel deploy hook = yes; Mac-only side-effects = no |
| Runtime <300 s | Vercel Function maxDuration cap on Pro; Hobby is 60s default |
| Deterministic input → output | Easier to validate parity during cutover |

### Top candidates (move first)

| Script | Why it's a fit | Output target | State concerns |
|---|---|---|---|
| `4pillar-daily.sh` | Pure web research → markdown → Telegram. Local archive (`~/.claude/state/4pillar-daily/`) is nice-to-have, not required. | Telegram (Winfred) | Drop local archive OR keep a thin Mac-side mirror that pulls from Vercel KV. |
| `daily-property-news.sh` | News scan → top 3 → Telegram (Winfred + channel). Mac-side dedup against `morning-calendly-brief.log` is the only friction. | Telegram (Winfred + channel) | Accept slight overlap initially; reintroduce dedup via Vercel KV later. |
| `overnight-new-launch-pov.sh` | Reads `public/new-launches.html` (already deployed) + adds POV. | Telegram (Winfred) | Fetch deployed page over HTTPS instead of local file. |

### Second wave (after 7-day soak of wave 1)

| Script | Notes |
|---|---|
| `daily-newsletter.sh` | Pulls `public/rates.json` + `~/.claude/state/mortgage-rates.json`. Rates JSON is already deployed; mortgage history would need migration to Vercel Blob/KV. Newsletter HTML is currently emailed via Mac-side `nodemailer` — needs to call same email path from Vercel function. |
| `competitor-listing-spy.sh` | Reads `~/.claude/state/new-launches/watchlist.json`. Migrate watchlist to a committed JSON in repo or to Vercel KV. |
| `daily-rates-update.sh` | If it writes back to `public/rates.json`, needs a Vercel deploy hook trigger after update (commit via GH App or write to Blob + serve from a route). |
| `daily-what-changed.sh` | If site-only, easy. If reads CRM, stays on Mac. |

### Stays on Mac (DO NOT migrate)

Anything that:
- Reads/writes `~/clients.db` — CRM, lead, deal pipeline, commission scripts (the 200+ k4xx scripts, `crm-*`, `client-*`, `deal-*`, `commission-*`).
- Talks to WhatsApp bridge (`wa-*`).
- Talks to Telegram bot daemon (`telegram_poll.sh` etc.) — bot itself is Mac-local for now.
- Touches Calendly/Gmail through MCP servers running on Mac.

## Scaffolding delivered

```
~/crestbrick-consult/api/cron/
  _lib.js                       — shared helpers (Anthropic call, Telegram send, auth, SGT date)
  4pillar-daily.js              — port of 4pillar-daily.sh
  daily-property-news.js        — port of daily-property-news.sh
  overnight-new-launch-pov.js   — port of overnight-new-launch-pov.sh
  CRONS.json                    — proposed schedule entries to splice into vercel.json
```

The functions:
- Use `fetch` directly against `api.anthropic.com/v1/messages` — no SDK, no claude CLI.
- Use Anthropic's server-side `web_search_20250305` tool to replicate the WebSearch behavior the CLI gave.
- Verify `Authorization: Bearer ${CRON_SECRET}` so endpoints aren't publicly callable.
- Always JSON-respond and Telegram-ping Winfred on failure (`cronHandler` wrapper).
- Set `export const config = { maxDuration: 300 }` (Vercel Pro: 300s; Hobby caps at 60s — see Cost section).

## Required env vars (set in Vercel project settings)

| Var | Source | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic console (same key claude CLI uses) | All cron functions |
| `TELEGRAM_BOT_TOKEN` | Existing — copy from `~/.telegram-bot.env` | All cron functions |
| `TELEGRAM_WINFRED_CHAT_ID` | Existing — copy from `~/.telegram-bot.env` | All cron functions |
| `TELEGRAM_CHANNEL_ID` | Existing — copy from `~/.telegram-bot.env` | `daily-property-news.js` (channel broadcast) |
| `CRON_SECRET` | Generated; set once in Vercel UI | `_lib.js` auth check (Vercel sends as Bearer) |

Loop in `security` agent before pasting `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` into Vercel env.

## Cost analysis

**Anthropic API:** unchanged. `claude -p` on the Mac was already calling the same API on the same key. Cost is per-token, not per-runtime-host. Estimated current daily spend across the 3 candidates: ~$0.20–$0.60/day (sonnet-4-5, ~10–30K input tokens including web_search results, ~2–4K output). Monthly: ~$10–$20.

**Vercel:**
- Hobby tier: free, **but** function `maxDuration` capped at 60s — too tight for web_search jobs that can take 90–180s. Crons are limited to 2 daily on Hobby.
- Pro tier: $20/month base. `maxDuration` up to 300s (matches our config). Unlimited crons. Includes 1000 GB-hours of function execution (3 jobs × 180s × 30 days × ~1 GB ≈ 4.5 GB-hours/month — negligible).
- **Recommendation:** Pro is needed once we activate. Net new cost: **$20/month**. Justification: replaces single-point-of-failure on a Mac that's also Winfred's daily driver.

**Total delta:** +$20/month, ~zero ops overhead, 3× higher reliability.

## Phasing

Do NOT activate all 3 at once. One per week, in order:

1. **Week 1 — `4pillar-daily`**
   - Set Vercel env vars.
   - Add `crons` block to `vercel.json` for `4pillar-daily` only.
   - Deploy. Verify first scheduled run (07:00 SGT next day) lands in Telegram.
   - Disable Mac launchd job: `launchctl unload ~/Library/LaunchAgents/com.crestbrick.4pillar-daily.plist` (or the actual plist name — confirm before unloading).
   - Soak 7 days. Watch Vercel function logs daily.

2. **Week 2 — `daily-property-news`** — same pattern.

3. **Week 3 — `overnight-new-launch-pov`** — same pattern.

After all 3 stable for 14 days, reassess wave 2 candidates.

## Rollback plan

For any single migrated job:

1. **Stop Vercel cron:** remove that entry from `crons` in `vercel.json`, push. Deploy disables the schedule within ~60s.
2. **Re-enable Mac launchd:** `launchctl load ~/Library/LaunchAgents/com.crestbrick.<name>.plist`.
3. **Verify:** wait for next scheduled fire window, confirm log written + Telegram received.

Keep launchd plists on disk (don't `rm`) for the entire migration window. Only delete plists after 30 days of clean Vercel runs.

If Vercel-wide outage: Mac jobs still work. If Mac is offline: Vercel jobs still work. Belt-and-suspenders during cutover.

## Open questions for Winfred

1. OK with $20/month Vercel Pro?
2. OK to drop the Mac-side markdown archive for `4pillar-daily`, or want it pulled back via a separate sync job?
3. Channel broadcasts from Vercel — same `TELEGRAM_CHANNEL_ID`, or a "test" channel for the soak period?
4. Should failure pings go to Winfred's chat, or to a dedicated `cron-alerts` chat to reduce noise?

## Files touched

- `/Users/winfredquek/crestbrick-consult/api/cron/_lib.js` (new)
- `/Users/winfredquek/crestbrick-consult/api/cron/4pillar-daily.js` (new)
- `/Users/winfredquek/crestbrick-consult/api/cron/daily-property-news.js` (new)
- `/Users/winfredquek/crestbrick-consult/api/cron/overnight-new-launch-pov.js` (new)
- `/Users/winfredquek/crestbrick-consult/api/cron/CRONS.json` (new — proposal, not active)
- `/Users/winfredquek/crestbrick-consult/docs/vercel-cron-migration.md` (this file)

`vercel.json` is **unchanged** until Winfred approves activation.
