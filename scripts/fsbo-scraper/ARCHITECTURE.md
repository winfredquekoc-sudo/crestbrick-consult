# FSBO Scraper Architecture & Integration Strategy

## Overview

The FSBO (For Sale By Owner) scraper is a prospecting engine for Carousell HDB and condo listings. It has four components:

1. **Fetch** (`carousell_client.py`) — search + detail-page fetch via curl_cffi, past Cloudflare.
2. **Classify** (`classify.py`) — owner vs agent, regex-first with an Ollama fallback.
3. **Dedupe** (`dedupe.py`) — cross-checked against every phone/listing source Winfred already has.
4. **Queue** (`scraper.py`) — fills the day-1 message template and appends qualified leads to `message-queue.json`.

**Current status:** Messages are always queued for manual sending. This isn't a default that happens to be set to "off" — there is no send implementation in this codebase to turn on. See "Sending Strategy" below.

---

## Component Architecture

### 1. Fetch (`carousell_client.py`)

**Goal:** Find HDB/condo FSBO listings on Carousell.

**Why curl_cffi, not requests + BeautifulSoup:** the original design used plain `requests` + BeautifulSoup. It never worked — Carousell returned a Cloudflare 403 on every attempt, every time, with no listing ever recovered. `curl_cffi` with `impersonate="chrome"` (TLS/HTTP fingerprint impersonation) gets past that. No headless browser, no proxy rotation.

**How listing data is recovered:** Carousell has no `__NEXT_DATA__`. Each page embeds exactly one `<script type="application/json">` island holding the whole Redux store. Because Carousell escapes every `/` in that payload as `/`, a literal `</script>` can never occur inside the JSON string content — so a non-greedy regex extraction (`JSON_ISLAND_RE`) is safe, not just lucky, and needs no real HTML/JS parser.

**Two different shapes matter:**
- Search page — `SearchListing.searchCache.<requestKey>.results[].listingCard` has title/price for the grid, but its bump timestamps (`active_bump`/`expired_bump`) reflect promotion activity, not the post date.
- Detail page (`/p/<id>/`) — `Listing.listingsMap[id]` is fully populated, including the true `time_created`, which is the only reliable age signal and what `min_days_old` filtering is based on.

**Resilience:** each request retries once on a non-200 or exception, with a randomized delay (`fetch_delay_range_sec`) before every attempt. `_get()` never raises — a failed fetch just returns `None`, and the caller treats that as transient (not marked seen, retried next run).

**Known limitation:** `search()` reads page 1 of results only, per query — there's no pagination.

### 2. Classify (`classify.py`)

**Goal:** Decide OWNER vs AGENT vs UNSURE per listing, so agent posts never get queued as leads.

**Layering, and why the order matters:** hard AGENT signals are checked before hard OWNER signals, because a real agent's post can still use owner-ish language ("seller wants X", "owner is motivated"). Checking OWNER phrases first would let agent posts using that language slip through.
- AGENT signals: content-marketing phrasing, a CEA registration number pattern in the listing text, co-broke language, known agency/brand names, agent self-identification phrases ("my client", "my listing", etc.).
- OWNER signals: phrases like "direct owner", "no agent", "sale by owner". A phrase match is downgraded to `UNSURE` instead of trusted outright when the seller's account name itself looks dealer-style (matches a rental/property/agent/homes-type pattern) — the wording says owner, the account looks like a business, so it goes to manual review rather than either bucket.

**Ollama fallback:** anything not resolved by hard signals goes to a local Ollama call (`http://127.0.0.1:11434`). Model selection is dynamic — whichever model `ollama list` returns first, so there's no hardcoded model name to keep in sync — but that has a real consequence documented in the source: on this machine, the first-listed model currently resolves to `moondream`, a vision-tuned model that returns an empty completion for plain-text prompts. The fallback is deliberately "best effort" — if Ollama is unreachable, or returns nothing parseable, the result is `UNSURE` rather than guessed. In practice, that means everything that reaches this fallback currently ends up `UNSURE`, until a text-capable model is installed or reordered ahead of `moondream`.

**Cross-listing heuristic (lives in `scraper.py`, not `classify.py`):** after a run finishes, if the same `seller_name` appears on 2+ queued leads from that run, any `OWNER` verdicts from that seller are downgraded to `UNSURE` — one person listing multiple properties for sale in the same batch reads as a dealer, not a private seller, regardless of what each individual listing's text said.

### 3. Dedupe (`dedupe.py`)

**Goal:** Never queue a lead Winfred already has a relationship with, and never re-contact someone on a do-not-engage list.

**Sources checked, in this order:**
1. `fsbo-state.json` — `seen_listings` (every listing ID processed before, any classification) plus `listings_sent`.
2. `docs/seller-database.csv` — phone numbers already in the seller database.
3. `_templates/landlord-db.json` — phone numbers from the landlord database. This file is **read-only** from this scraper's side — `dedupe.py` never writes to it, and never iterates its top-level dict directly (it's a dict with a `landlords` key plus sibling metadata keys, not a bare list of records).
4. A small hardcoded do-not-engage phone set, checked independently of the two databases above.

Phones are normalized to `+65XXXXXXXX` before any comparison, so `+65 9123 4567`, `91234567`, and `6591234567` all match each other.

**Why listing ID and phone are checked separately:** a listing-ID match is available before any page is even fetched (cheap pre-fetch skip via `already_seen()`); a phone match can only be known after fetching and parsing the detail page. `check()` is the fuller post-classification pass that covers both.

### 4. Queue + Template Fill (`scraper.py`)

**Goal:** Turn a classified, deduped listing into a ready-to-send draft, without ever sending it.

**PropertyGuru enrichment is gone, on purpose.** The day-1 message template (`docs/fsbo-day1-message-template.md`) has a sold-comps paragraph ("Just checked PropertyGuru..." plus three bullet lines), a close-price prediction line, and a "recent closes" checkmark line — all originally meant to be filled from a PropertyGuru sold-comps lookup. That lookup was never built, and isn't planned. Rather than leave the template's placeholder example figures in a message sent to a real prospect, `fill_message_template()` strips the comps paragraph and the prediction line entirely via regex, and replaces the numeric "recent closes" line with a generic, non-numeric claim ("Track record of fast closes in similar blocks nearby"). No fake, estimated, or stale figures are ever substituted in — the section is just omitted when there's no real data.

The seller's first name is used if it parses as a plausible name (alphabetic, reasonable length, not a generic word like "rental" or "owner"); otherwise the message falls back to "there".

---

## Sending Strategy (CEA Compliance Critical)

### The Constraint: Single Sender Doctrine

From this repo's `CLAUDE.md`:

> The intake engine (src/wa-pipeline, com.crestbrick.wa-intake) is the ONLY thing that ever auto-sends WhatsApp messages on Winfred's number. No session, script, or agent may create a second automated sender, auto-responder, or parallel enquiry/intake form.
>
> Drafts for prospects/clients are queued for human sending (WhatsApp Web) or Winfred's approval.

### Current Implementation: Queue-Only, By Design

There is exactly one sending mode, and it is manual:

1. The scraper finds listings, classifies, dedupes, and fills the template.
2. Entries are appended to `message-queue.json`. Nothing is sent.
3. Winfred (or someone he delegates to) reviews `drafted_message` per entry and sends it by hand — WhatsApp Web if `phone` is set, Carousell chat if `needs_carousell_dm` is true.

`config.json` still carries a `send_mode` key, and `run.sh` still greps `config.json` for the literal string `"send_mode": "auto"` to decide whether to call `scraper.py send` — but `scraper.py send` is a hardcoded no-op:

```python
elif command == "send":
    print("This scraper never auto-sends. Messages are queued in message-queue.json for manual review.")
```

There is no `send_via_bridge()`, no `wa_send_guard` integration, no `/api/send` call anywhere in this directory. Editing `send_mode` to `"auto"` in `config.json` would flip that grep and cause `run.sh` to invoke `scraper.py send` — but that call still only prints the line above. There is nothing left to enable by editing config. Treat this as a closed design decision, not a placeholder awaiting implementation.

**Why this is permanent, not an MVP stopgap:** a second automated WhatsApp sender is exactly what the single-sender doctrine exists to prevent — spam-filter triggers, duplicate sends, and CEA review requirements all argue against it. Nothing about the fetch/classify/dedupe rewrite changes that calculus, so an auto-send path was deliberately not rebuilt alongside the rest of the pipeline.

---

## CEA Compliance Checklist

- ✅ Messages are never sent without Winfred's manual action — there is no code path in this directory that sends anything.
- ✅ The day-1 template carries Winfred's own CEA registration and full-service framing; this scraper doesn't alter that part of the template.
- ✅ No advice is given and no negotiation happens in the drafted message — it's a prospecting message, not deal terms.
- ✅ No fabricated sold-comps data — the comps section is omitted, never faked, when there's no real data to fill it with (see "Queue + Template Fill" above).
- ✅ Classification errors fail toward caution: ambiguous listings resolve to `UNSURE` and are flagged `needs_review` rather than auto-queued as confident OWNER leads.

---

## Files & State

### Core pipeline
- `scraper.py` — orchestrator: search, filter, classify, dedupe, template fill, queue, digest.
- `carousell_client.py` — fetch layer (curl_cffi + JSON island extraction).
- `classify.py` — owner/agent classification.
- `dedupe.py` — cross-database dedupe + do-not-engage list.

### Configuration
- `config.json` — search queries, `min_days_old`, fetch pacing, fetch cap, plus a human-readable `notes` field. Also still carries a few keys nothing in the code reads (`max_daily_sends`, `send_time_hhmm`, `propertyguru_search_url` — leftovers from the pre-rewrite design) and a `send_mode` key that's loaded but never branches any Python behavior — see "Sending Strategy" above for why that's safe rather than a bug to fix.
- `fsbo-state.json` — `seen_listings` (by listing ID, with classification + phone recorded for every classification including `AGENT` and `NOT_A_SALE`, not just queued leads), `listings_sent` (folded into the same seen-ID set as `seen_listings` — wired into `DedupeIndex`, but nothing currently appends to it, so it's always empty in practice), `last_run`. Also still carries a `contacted_blocks` key that nothing in the current code reads or writes at all — fully vestigial, unlike `listings_sent`.
- `message-queue.json` — queued leads; schema documented in `README.md`.

### Scheduling
- `launch-fsbo-scraper.plist` — launchd job, daily at 00:00 SGT (launchd's `StartCalendarInterval` runs in the system's local timezone, not UTC).
- `run.sh` — wrapper: runs `scraper.py scrape`, conditionally runs `scraper.py send` (always a no-op — see "Sending Strategy" above), then appends `scraper.py queue` output to `logs/daily-report.log` as a running queue-status snapshot. `scraper.py` has no `report` subcommand — only `scrape`, `queue`, and `send`.

### Logs
- `logs/fsbo-{date}.log` — per-run activity log.
- `logs/launchd.log` / `logs/launchd-error.log` — launchd stdout/stderr.
- `logs/daily-report.log` — see the `run.sh` note above; a running history of `scraper.py queue` snapshots, not a per-run activity report.

### Documentation
- `README.md` — usage, configuration, troubleshooting.
- `ARCHITECTURE.md` — this file.
- `test-demo.py` — a demo script that predates this rewrite. It fabricates its own sample listings and PropertyGuru sales data with a schema (`block`, `unit`, `asking_price`, `seller_phone`, `status: "queued"`) that no longer matches `message-queue.json`, and it doesn't import or exercise `scraper.py`, `carousell_client.py`, `classify.py`, or `dedupe.py` at all — it's fully self-contained. It still runs standalone, but it doesn't exercise or demonstrate current behavior; treat it as historical reference, not a smoke test for this pipeline.

---

## Installation & Scheduling

### 1. Verify Directory Structure
```bash
ls -la /Users/winfredquek/crestbrick-consult/scripts/fsbo-scraper/
```

### 2. Run the Scraper
```bash
python3 /Users/winfredquek/crestbrick-consult/scripts/fsbo-scraper/scraper.py scrape
```
Searches Carousell, fetches new listings, filters/classifies/dedupes, and queues qualified leads to `message-queue.json`.

### 3. Install the launchd Schedule
```bash
cp /Users/winfredquek/crestbrick-consult/scripts/fsbo-scraper/launch-fsbo-scraper.plist \
   ~/Library/LaunchAgents/com.crestbrick.fsbo-scraper.plist

launchctl load ~/Library/LaunchAgents/com.crestbrick.fsbo-scraper.plist
```
Runs daily at 00:00 SGT (local time — see the `run.sh` note under "Files & State").

### 4. Monitor
```bash
tail -f /Users/winfredquek/crestbrick-consult/scripts/fsbo-scraper/logs/fsbo-*.log
```

### 5. Disable
```bash
launchctl unload ~/Library/LaunchAgents/com.crestbrick.fsbo-scraper.plist
```

---

## Design Decisions Already Settled

The previous version of this document carried a list of open questions for Winfred — sending strategy, whether to implement the scrapers at all, rate limits, template customization. Those aren't open anymore:

- **Sending strategy:** queue-only, permanently — not a toggle. See "Sending Strategy" above.
- **Fetch implementation:** built, using curl_cffi. No Selenium, no browser automation, no proxy rotation — none of that turned out to be necessary.
- **PropertyGuru enrichment:** not built, and not planned. The template gracefully omits that section instead.
- **Rate limits:** `max_listing_fetches_per_run` (30) and `fetch_delay_range_sec` (2.0–4.0s) are plain values in `config.json`, editable directly — they don't need a design decision, just an edit.

What's still genuinely open is ordinary engineering follow-up, not a policy question — see "Known Gaps" below.

---

## Production Readiness

### Implemented
- ✅ Carousell search + detail fetch (curl_cffi, past Cloudflare)
- ✅ Owner/agent classification (regex layers + Ollama fallback)
- ✅ Dedupe against state file, seller database, landlord database, and do-not-engage list
- ✅ Rental-post and under-price filtering (keeps sale search results clean of rental listings)
- ✅ Message template fill, with graceful comps omission (no fabricated data)
- ✅ Same-run dealer detection (multiple sale posts, same seller name)
- ✅ Daily file logging + Telegram run digest
- ✅ launchd scheduling infrastructure
- ✅ Queue-only sending, permanently (no send path exists to misconfigure)

### Known Gaps
- `carousell_client.py`'s `search()` reads page 1 of results only, per query — no pagination.
- The Ollama fallback is effectively inert on this machine until a text-capable model is installed or reordered ahead of `moondream` (see "Classify" above).
- Nothing marks a queued entry as sent. Tracking what's actually gone out to a prospect is entirely outside this codebase today.

### Explicitly Not Planned (a decision, not a gap)
- PropertyGuru sold-comps enrichment.
- Any automatic-send path.

---

## Why This Design

### Queue-Only Is Permanent, Not a Default

CLAUDE.md forbids a second automated WhatsApp sender; the intake engine is the single sender. This scraper was never going to grow a send path, so none of the plumbing for one — bridge integration, cross-sender guard, rate-limited send loop — was built. The previous version of this document's "Option B: Auto-Send" design was aspirational; it's now explicitly out of scope, not deferred.

### curl_cffi Over Selenium

The original plan assumed Carousell needed full browser automation (Selenium + proxy rotation) to get past its bot detection. In practice, TLS/HTTP fingerprint impersonation was enough on its own — no headless browser, no proxies, no JS rendering required. That keeps the fetch layer dependency-light and fast.

### Fail-Closed Classification

Every classification path that isn't a confident hard-signal match resolves to `UNSURE` rather than guessing: Ollama down, Ollama returning junk, dealer-style account name on owner phrasing, or multiple sale posts from the same seller in one run. `UNSURE` listings still get queued — nothing is silently dropped — but are flagged `needs_review`, keeping the actual owner/agent judgment call with Winfred wherever the automation isn't confident.

### No Fabricated Data

The comps-block removal is the clearest example: rather than reuse the template's placeholder PropertyGuru figures as if real, or invent replacement numbers, the code drops that section entirely. This principle runs through the rest of the pipeline too — nothing here fills in a value it doesn't actually have.

---

## Support

For issues or questions:
1. Check `logs/fsbo-{date}.log` for errors.
2. Run `python3 scraper.py queue` for current queue status.
3. Review the inline comments in `classify.py` and `dedupe.py` — most non-obvious decisions are explained at the point they're made.

---

**Last Updated:** 2026-09-01 (rewritten to describe the curl_cffi / classify.py / dedupe.py pipeline; PropertyGuru enrichment and auto-send are documented as removed and ruled out, not deferred.)
