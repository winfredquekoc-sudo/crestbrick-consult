# FSBO Scraper — Production Real Estate Prospecting

## Overview

**FSBO Scraper** finds Carousell "for sale by owner" HDB and condo listings, classifies each seller as owner vs agent, dedupes against Winfred's existing databases, fills the standard day-1 outreach message, and queues everything for Winfred to review and send by hand.

**Status:** Messages are always queued for manual sending. There is no automatic-send path anywhere in this codebase — see "Single Sender Doctrine" below.

**Pipeline:**
1. Search a handful of FSBO queries on Carousell (`carousell_client.py`, via curl_cffi with `impersonate="chrome"` — plain `requests` + BeautifulSoup never got past Cloudflare's 403).
2. Fetch the detail page for each new listing and extract title/price/description/seller/phone/posted date.
3. Filter out rental posts that slipped into sale search results, and listings younger than `min_days_old`.
4. Classify owner vs agent (`classify.py`): hard regex signals first, an Ollama fallback for anything ambiguous, else `UNSURE`.
5. Dedupe (`dedupe.py`) against `fsbo-state.json`, `docs/seller-database.csv`, the read-only landlord database, and a do-not-engage list.
6. Fill the day-1 message template, dropping the PropertyGuru sold-comps paragraph gracefully (no fake numbers) — this build has no comps enrichment.
7. Append qualified leads to `message-queue.json` and post a run digest to Telegram.

---

## Directory Structure

```
scripts/fsbo-scraper/
├── scraper.py                 # Orchestrator: search, filter, classify, dedupe, queue, digest
├── carousell_client.py        # curl_cffi fetch layer + JSON island parsing
├── classify.py                # Owner vs agent classification (regex + Ollama fallback)
├── dedupe.py                  # Cross-database dedupe + do-not-engage list
├── config.json                # Search queries, thresholds, rate limiting
├── run.sh                     # launchd wrapper (daily 00:00 SGT local time)
├── launch-fsbo-scraper.plist  # macOS launchd job definition
├── test-demo.py               # Legacy demo script predating this pipeline (see note below)
├── logs/
│   ├── fsbo-{date}.log        # Per-run activity log
│   ├── daily-report.log       # Running scraper.py queue snapshots (see "Logs" section)
│   ├── launchd.log            # launchd stdout
│   └── launchd-error.log      # launchd stderr
├── fsbo-state.json            # seen_listings + last_run (persisted)
├── message-queue.json         # Queued leads awaiting Winfred's review
├── README.md                  # This file
└── ARCHITECTURE.md            # Design rationale, compliance, file map
```

---

## Quick Start

```bash
cd /Users/winfredquek/crestbrick-consult/scripts/fsbo-scraper
python3 scraper.py scrape
```

This runs the full pipeline (search → fetch → filter → classify → dedupe → queue), appends qualified leads to `message-queue.json`, writes to `logs/fsbo-{date}.log`, posts a summary digest to Telegram (if `TELEGRAM_BOT_TOKEN` is configured), and prints a JSON run summary to stdout.

### View queue status

```bash
python3 scraper.py queue
```

Prints the total queued count broken down by classification (OWNER / AGENT / UNSURE).

### Manual sending (the only sending path)

`scraper.py` never sends anything itself. Review `message-queue.json`, and send each `drafted_message` by hand — via WhatsApp Web if a `phone` was extracted, or via Carousell chat if `needs_carousell_dm` is true.

```bash
python3 scraper.py send
# → "This scraper never auto-sends. Messages are queued in message-queue.json for manual review."
```

That's a hardcoded no-op, not a stub — see "Single Sender Doctrine" below. There is no `report` subcommand; the only three are `scrape`, `queue`, and `send`.

---

## Configuration

**File:** `config.json`

```json
{
  "carousell_url": "https://www.carousell.sg/search",
  "min_days_old": 60,
  "fsbo_search_queries": [
    "hdb for sale by owner",
    "direct owner hdb",
    "no agent hdb for sale",
    "condo for sale owner"
  ],
  "max_listing_fetches_per_run": 30,
  "fetch_delay_range_sec": [2.0, 4.0]
}
```

**Fields the code actually reads:**
- `carousell_url` — search base URL; each query in `fsbo_search_queries` is appended as a path segment.
- `min_days_old` — skip listings newer than this (default 60 days = likely stuck). Listings whose post date can't be determined are never skipped by this filter.
- `fsbo_search_queries` — list of Carousell search phrases run each pass.
- `max_listing_fetches_per_run` — cap on how many new listing IDs get a detail-page fetch in one run.
- `fetch_delay_range_sec` — random delay range, in seconds, before each HTTP request.

**Fields present in `config.json` but no longer read by any code:** `max_daily_sends`, `send_time_hhmm`, `propertyguru_search_url` — leftovers from the pre-rewrite design. `send_mode` is also loaded into memory but never branches any Python behavior — see "Single Sender Doctrine" below for why that's safe to leave as-is rather than a bug to fix. `notes` is a plain human-readable string field, not consumed by code.

---

## Message Queue Format

**File:** `message-queue.json` — a flat JSON array, one object per queued lead:

```json
{
  "listing_id": "1234567890",
  "url": "https://www.carousell.sg/p/1234567890/",
  "title": "HDB 4-room, direct owner, no agent",
  "price": "S$615,000",
  "address_hint": "Blk 123 Example Ave",
  "phone": "+65 9XXX XXXX",
  "seller_name": "Example Seller",
  "classification": "OWNER",
  "evidence": "owner phrasing: 'direct owner'",
  "drafted_message": "Hi Example\n\n...",
  "needs_review": false,
  "needs_carousell_dm": false,
  "days_old": 74,
  "date_unknown": false,
  "queued_at": "2026-08-31T20:36:36+00:00"
}
```

- `classification` is `OWNER`, `AGENT`, or `UNSURE` — only `OWNER` and `UNSURE` ever reach the queue. `AGENT`-classified listings are dropped and never queued, though they're still recorded in `fsbo-state.json` so they aren't re-fetched next run.
- `needs_review` is true whenever `classification` is `UNSURE` — including cases downgraded from `OWNER` because the same seller name posted 2+ sale listings in the same run (a same-run dealer-detection heuristic in `scraper.py`, separate from the phone/ID dedupe in `dedupe.py`).
- `needs_carousell_dm` is true when no phone number could be extracted from the listing description, seller name, or username — message that seller via Carousell chat instead of WhatsApp.
- `address_hint` and `phone` are best-effort regex extractions and can be `null`.

There is no `status`, `sent`, `sent_at`, or `delivery_status` field — those belonged to the old auto-send design. Nothing writes a "sent" marker back to this file; if Winfred wants to track what's been sent, that has to happen outside this file today (e.g. once contact is made, in the seller database).

---

## Fetch Layer

`carousell_client.py` fetches with `curl_cffi`, `impersonate="chrome"` — plain `requests` + BeautifulSoup returned a Cloudflare 403 on every attempt and never worked. Carousell embeds one `<script type="application/json">` island per page holding the whole Redux store; the client extracts it with a regex rather than a full HTML/JS parser (safe because Carousell escapes every `/` in that payload, so a literal `</script>` can never appear inside the JSON content).

- `search(query)` fetches page 1 only of the search results and returns `[{id, title}, ...]`.
- `fetch_detail(listing_id)` fetches `/p/<id>/` and returns the full listing record, including `time_created` — the only reliable age signal (search-result bump timestamps reflect promotion activity, not the post date).
- Every request retries once on a non-200 or exception, with a randomized delay before each attempt. A failed fetch returns `None` rather than raising; the caller treats that as transient and retries it next run.

---

## Classification

`classify.py` layers three passes, in order:

1. **Hard AGENT signals** — content-marketing phrasing, a CEA registration number pattern, co-broke language, known agency/brand names, agent self-identification phrases ("my client", "my listing", etc.). Checked before owner phrasing, because a real agent's post can still say "seller"/"owner wants X".
2. **Hard OWNER phrasing** — phrases like "direct owner", "no agent", "sale by owner". Downgraded to `UNSURE` instead of trusted outright when the seller's account name looks dealer-style, for manual verification.
3. **Ollama fallback** — for anything still ambiguous, a local Ollama call (model picked dynamically as the first entry in `ollama list`) is asked to answer OWNER/AGENT/UNSURE. If Ollama is unreachable or returns nothing parseable, the result is `UNSURE`. **Known current limitation:** on this machine the first-listed model is `moondream` (a vision model), which returns an empty completion for plain-text prompts — so in practice, anything reaching this fallback resolves to `UNSURE` until a text-capable model is installed or reordered ahead of it.

`scraper.py` adds one more layer after a run: if the same `seller_name` appears on 2+ queued leads in the same run, any `OWNER` entries from that seller are downgraded to `UNSURE` (one seller pushing multiple sale posts reads as a dealer, not a private owner).

---

## Dedupe

`dedupe.py` checks, in this order, before anything is queued:

1. **`fsbo-state.json`** — `seen_listings` (every listing ID processed before, any classification) plus `listings_sent` (currently always empty, but still checked).
2. **`docs/seller-database.csv`** — phone numbers of sellers already in Winfred's seller database.
3. **`_templates/landlord-db.json`** — phone numbers from the landlord database. Read-only from this scraper's side.
4. **A hardcoded do-not-engage set** — specific phone numbers that must never be re-contacted, checked independently of the databases above.

Phone matching normalizes to `+65XXXXXXXX` before comparing, so `+65 9123 4567`, `91234567`, and `6591234567` all match. A listing can also be dropped purely by listing ID — that check doesn't need a phone number at all.

---

## Message Template

`scraper.py` fills `~/crestbrick-consult/docs/fsbo-day1-message-template.md` (the `Hi [Name]...` block between the code fence) with the seller's first name — falling back to "there" if the extracted name doesn't look like a real name — the address hint, and the price in thousands.

The template's PropertyGuru sold-comps paragraph, close-price prediction line, and "recent closes" checkmark line are stripped out by regex before the message is queued. This build has no PropertyGuru integration, so rather than leave the template's placeholder example figures in a message sent to a real prospect, that content is dropped. The "recent closes" line is replaced with a generic, non-numeric claim instead of being left blank or filled with invented numbers.

---

## Logs

- `logs/fsbo-{YYYY-MM-DD}.log` — full per-run activity log, e.g.:
  ```
  2026-09-01 08:00:05,123 - INFO - === FSBO Scraper Run Started ===
  2026-09-01 08:00:41,558 - INFO - 1234567890 classified OWNER (owner phrasing: 'direct owner'), queued
  2026-09-01 08:01:02,004 - INFO - === FSBO Scraper Run Complete: fetched=12 queued=3 ===
  ```
  (Figures above are illustrative, not from a real run.)
- `logs/launchd.log` / `logs/launchd-error.log` — stdout/stderr from the launchd job.
- `logs/daily-report.log` — `run.sh` appends the output of `scraper.py queue` to this file after every run, so it accumulates a running history of queue-status snapshots (total queued count broken down by classification) rather than a per-run activity report. `scraper.py` itself has no `report` subcommand — only `scrape`, `queue`, and `send`.

A run also posts a plain-text digest to Telegram (`send_telegram()`) — query/hit counts, a fetch/classification/drop breakdown, and one line per newly queued lead. Plain text, no `parse_mode`, deliberately: an unescaped Markdown send has silently dropped alerts before. Requires `TELEGRAM_BOT_TOKEN` in `~/.telegram-bot.env` or the environment; if missing, the digest is just logged as a warning and the run itself still completes normally.

---

## Single Sender Doctrine

This scraper never sends a WhatsApp or Carousell message. There is no code path anywhere in this directory that sends anything automatically:

- `scraper.py send` is a hardcoded no-op print statement.
- `run.sh` only calls `scraper.py send` if `config.json` literally contains `"send_mode": "auto"` — and even then, that call is still the no-op above. Flipping that string in `config.json` does not turn on sending; there's nothing left to enable.
- Every queued entry sits in `message-queue.json` until Winfred (or someone he delegates to) copies `drafted_message` and sends it by hand.

This is intentional, not an in-progress gap: per this repo's single-sender doctrine, `com.crestbrick.wa-intake` is the only thing that ever auto-sends on Winfred's WhatsApp number, and this scraper does not attempt to become a second one. See `ARCHITECTURE.md` for the full rationale.

---

## Troubleshooting

**No listings found / search returns nothing**
Carousell may have changed its page structure (no JSON island found), or the query genuinely has no matches right now. Check `logs/fsbo-{date}.log` for `"No JSON island on search page"` or `"Unexpected search JSON shape"` warnings.

**Fetches failing / suspect Cloudflare block**
`curl_cffi` with `impersonate="chrome"` is what gets past Cloudflare here. If fetches start failing consistently, check whether `curl_cffi` needs updating before assuming the query itself is bad — Cloudflare's fingerprinting changes over time.

**Everything comes back UNSURE**
Check whether Ollama is running (`curl http://127.0.0.1:11434/api/tags`) and whether the first model in `ollama list` is actually a text model — see "Classification" above.

**Telegram digest not arriving**
Check `~/.telegram-bot.env` has `TELEGRAM_BOT_TOKEN` set, and check the day's log for `"Telegram digest not sent"`.

**A listing keeps reappearing every run**
If it's younger than `min_days_old`, that's expected — "too recent" listings are deliberately not marked seen, so they're retried once they age past the threshold. If it's already past `min_days_old` and still reappearing, check whether it's failing to fetch (look for `fetch_failed` in the run summary) rather than being classified and stored.

---

## Scheduling (launchd)

`launch-fsbo-scraper.plist` runs `run.sh` daily at **00:00 SGT** (launchd's `StartCalendarInterval` runs in the system's local timezone, not UTC — confirmed by `logs/launchd.log` timestamps).

### Install
```bash
cp launch-fsbo-scraper.plist ~/Library/LaunchAgents/com.crestbrick.fsbo-scraper.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.fsbo-scraper.plist
```

### Verify
```bash
launchctl list | grep fsbo
```

### Check logs
```bash
tail -f logs/launchd.log
tail -f logs/launchd-error.log
```

### Disable
```bash
launchctl unload ~/Library/LaunchAgents/com.crestbrick.fsbo-scraper.plist
```

---

## Not Planned

- **Auto-send.** Ruled out by design, not a pending decision — see "Single Sender Doctrine" above.
- **PropertyGuru sold-comps enrichment.** Removed; the message template omits that section instead of faking it. Re-adding it would need a working PropertyGuru fetch layer, which doesn't exist in this codebase.

---

## Support

For issues or questions:
1. Check `logs/fsbo-{date}.log` for errors.
2. Run `python3 scraper.py queue` for current queue status.
3. Review the inline comments in `classify.py` and `dedupe.py` — most edge cases (dealer-style names, ambiguous phrasing) are explained at the point they're handled.

---

**Status:** Fetch/classify/dedupe/queue pipeline in production use. Sending is, and will remain, manual.

**Last Updated:** 2026-09-01
