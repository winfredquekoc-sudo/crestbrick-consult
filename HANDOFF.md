# Crestbrick / Winfred Quek — Project Handoff

Quick-start context for any new Claude session. Read this before touching anything.

---

## Who / What

**Winfred Quek** — Singapore property advisor, CEA licence R073319H, under PropNex.
**Brand**: Winfred Quek (not "Crestbrick" in public-facing copy).
**Live site**: https://winfredquek.com (Vercel project: `crestbrick-consult`)
**Repo**: `/Users/winfredquek/crestbrick-consult/` (main branch: `main`)

---

## Infrastructure at a Glance

| Thing | Where |
|---|---|
| Vercel project | `crestbrick-consult` — auto-deploys from `main` |
| Telegram bot | `@Maddieprop_bot` (token in `~/.telegram-bot.env`) |
| Winfred's private chat ID | `540127870` (`TELEGRAM_WINFRED_CHAT_ID`) |
| Public channel | `@winwithwinfred` (`TELEGRAM_CHANNEL_ID = -1003915353882`) |
| WhatsApp bridge | `~/whatsapp-mcp/whatsapp-bridge/` — REST API on `http://127.0.0.1:8080` |
| WA messages DB | `~/whatsapp-mcp/whatsapp-bridge/store/messages.db` (SQLite) |
| CRM DB | `~/.claude/state/clients.db` (SQLite) |
| Mac scripts | `~/.claude/bin/` (372+ launchd jobs) |
| Env files | `~/.telegram-bot.env`, `~/.n8n-webhook.env` |
| ANTHROPIC_API_KEY | In shell env (not in any dotfile — already exported) |
| n8n | `winfredquekoc.app.n8n.cloud` — handles drip sequences |

---

## Repo Structure

```
api/                  Vercel serverless functions
  audit.js            Portfolio Strategy Audit form → Telegram ping + JSON response
  contact.js          Contact form → Telegram
  drip-signup.js      Drip track signup (empire/equity/reinvest) → n8n + Telegram
  lead-magnet.js      eBook download capture → Telegram + n8n + Resend
  hot-lead.js         Second-visit beacon → Telegram
  unsubscribe.js      Unsubscribe handler → Telegram
  portal-data.js      Client portal data endpoint
  portal-magic-link.js Magic link generator
  cron/
    _lib.js           Shared helpers (claudeCall, tgWinfred, tgChannel, cronHandler)
    4pillar-daily.js          07:00 SGT — top SG property story × 4-pillar analysis
    daily-property-news.js    14:30 SGT — top 3 news → Winfred + channel
    overnight-new-launch-pov.js  02:00 SGT — new launch investor POV
    night-brief.js            22:00 SGT — EOD market wrap + tomorrow watchlist
db/
  schema.sql          Postgres (Neon) schema — mirrors clients.db
  migrations/         Incremental SQL migrations
public/               Static HTML pages (no framework — plain HTML/CSS/JS)
_api_archive/         Old API handlers kept for reference — NOT deployed
_templates/           HTML page templates
```

---

## Cron Schedule (all SGT)

| Time | File | What it does |
|---|---|---|
| 02:00 | `overnight-new-launch-pov.js` | Investor POV on one live launch → Winfred |
| 07:00 | `4pillar-daily.js` | Top SG property story + 4-pillar analysis → Winfred |
| 14:30 | `daily-property-news.js` | Top 3 actionable news → Winfred + channel |
| 22:00 | `night-brief.js` | EOD wrap + tomorrow watchlist + macro signal → Winfred |

Crons are activated in `vercel.json` under `"crons"`. Schedules in UTC.
`_lib.js` default model: `claude-sonnet-4-6`. Cron handlers auto-ping Winfred on failure.

---

## Telegram Patterns

All API files use `TELEGRAM_WINFRED_CHAT_ID` (not `TELEGRAM_CHAT_ID` — that var doesn't exist).
All user-supplied fields in Telegram messages use `parse_mode: 'HTML'` with an `esc()` helper
— never `'Markdown'` (breaks on names/sources with `*`, `_`, backticks).

```js
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
```

Mac scripts use `~/.claude/bin/_telegram-helper.sh` → `send_tg "msg"`.
Cron functions use `tgWinfred(text)` from `_lib.js`.

---

## WhatsApp Bridge

- SQLite DB: `~/whatsapp-mcp/whatsapp-bridge/store/messages.db`
- Tables: `messages` (id, chat_jid, sender, content, timestamp, is_from_me, media_type, ...), `chats` (jid, name, last_message_time)
- Individual DMs: JID ends in `@s.whatsapp.net`
- Group chats: `@g.us` | Broadcast lists: `@broadcast` | Linked devices: `@lid` | Channels: `@newsletter`
- **Always filter to `@s.whatsapp.net` when you want 1:1 DMs only**
- `chats.name` = WA-saved contact name (may be empty for unsaved numbers)
- Contact name resolution order: CRM `display_name` by phone → `chats.name` → bare phone number

Runbook for restarts: `~/crestbrick-consult/docs/runbook-whatsapp-bridge.md`

---

## CRM (clients.db)

SQLite at `~/.claude/state/clients.db`. Key tables: `clients`, `touchpoints`, `deals`, `properties`.
Phones stored in E.164 format (`+6591234567`). Many clients have no phone populated yet.
Postgres migration in `db/` is provisioned via Vercel Neon (not yet live-synced — SQLite is the source of truth).

---

## Key Mac Scripts (in `~/.claude/bin/`)

| Script | What it does |
|---|---|
| `dm-followup-nudge.sh` | Hourly: DMs >24h unanswered → Telegram with thread history + Haiku catchup summary |
| `stale-pipeline-nudge.sh` | Daily: top 3 clients to call today by gap × health score |
| `client-followup-radar.sh` | Reads `~/real-estate-agent/clients/` markdown files, not DB |
| `auto-crm-from-wa.sh` | Hourly: Haiku scans WA for CRM-stage signals → Telegram for approval |
| `warm-lead-nurture-trigger.sh` | Monday 09:00: warm leads >7d no touch → WA draft |
| `wa-group-listing-scraper.sh` | Every 30min: scans WA agent groups for new listings |
| `_telegram-helper.sh` | Sourced by all scripts — provides `send_tg()` |
| `_logging-lib.sh` | Sourced for `log()` helper |

### dm-followup-nudge.sh — how it works
1. Queries `messages.db` for `@s.whatsapp.net` DMs where inbound > last outbound, within 14 days
2. Fetches last 10 messages (both sides) per contact for thread history
3. Resolves name: CRM phone lookup → `chats.name` → bare phone
4. Calls Claude Haiku API directly (via urllib) with full thread — returns 2 lines per contact:
   `Context: ...` and `Waiting: ...`
5. Sends formatted Telegram: name + age + context + waiting + last message preview

---

## ENV Files

```
~/.telegram-bot.env       TELEGRAM_BOT_TOKEN, TELEGRAM_WINFRED_CHAT_ID (540127870),
                          TELEGRAM_CHANNEL_ID (-1003915353882), bot username etc.
~/.n8n-webhook.env        N8N webhook URLs
~/.groq.env               Groq API key
~/.higgsfield.env         Higgsfield API key
ANTHROPIC_API_KEY         Exported in shell env (not in a dotfile)
```

Vercel env vars managed via `vercel env` CLI or Vercel dashboard.
Required for crons: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WINFRED_CHAT_ID`,
`TELEGRAM_CHANNEL_ID`, `CRON_SECRET`.

---

## Content / Brand Rules

- Always "Winfred Quek" or "Winfred's" — not "Crestbrick" — in public-facing copy
- CEA number always: `R073319H`
- Bank rates: **1.5%** (not 2–3%)
- "Families advised": **20+** (not 120+, not any inflated number)
- Use "restructuring" not "decoupling" in user-facing copy
- Use "enquiry" not "audit" in customer-facing text; "audit" is fine in internal/technical context
- No agency logos on listings
- CTAs default to 30-min Calendly booking
- Dark navy + gold for premium PDFs (no white-bg + grey-text)

---

## What Was Done in This Session (2026-05-17)

**Vercel API fixes (committed to `claude/interesting-bouman-a9c92a`)**:
- `api/drip-signup.js` — wrong env var `TELEGRAM_CHAT_ID` → `TELEGRAM_WINFRED_CHAT_ID`
- `api/drip-signup.js` + `api/lead-magnet.js` — `parse_mode: 'Markdown'` → `'HTML'` with `esc()` on user fields
- `api/audit.js` — `pingTelegram()` was defined but never called; restored
- `api/contact.js` — stale domain `crestbrick-consult.vercel.app` → `winfredquek.com`

**Cron jobs deployed**:
- Copied + activated 3 archived cron handlers from `_api_archive/cron/` into `api/cron/`
- Built new `api/cron/night-brief.js` (22:00 SGT EOD summary)
- Wired `vercel.json` with `"crons"` array — all 4 active on next prod deploy
- Updated model from `claude-sonnet-4-5` → `claude-sonnet-4-6` in `_lib.js`

**Mac script fixes**:
- `~/.claude/bin/dm-followup-nudge.sh` — full rewrite:
  - Was showing raw JIDs (codes) and including groups/broadcasts/`@lid`
  - Now filters to `@s.whatsapp.net` only, resolves real names, shows thread history
  - Calls Haiku for Context + Waiting summary per contact
- `~/.claude/bin/auto-crm-from-wa.sh` — SQL JOIN with `chats` so Claude sees names not JIDs

---

## Gotchas

- `client-followup-radar.sh` reads markdown files in `~/real-estate-agent/clients/` — not `clients.db`. The two are separate systems.
- `clients.db` has rich schema but most clients lack phone numbers — CRM phone lookups often miss.
- WA `@lid` JIDs are linked device identifiers, not contacts. Always filter them out.
- `chats` table only has names for saved contacts. Unknown numbers show as bare phone.
- Mac scripts use `launchd` (not cron). Check `~/Library/LaunchAgents/` for plists.
- `_api_archive/` is a graveyard — don't deploy from there, copy and review first.
- Vercel crons require `CRON_SECRET` env var set in Vercel dashboard to be auth-protected.
- `clients.db` `ALTER TABLE` statements at the top of some scripts are safe to re-run (uses `2>/dev/null || true`).
