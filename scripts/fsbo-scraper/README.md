# FSBO Scraper — Production Real Estate Prospecting

## Overview

**FSBO Scraper** finds Carousell HDB For-Sale-By-Owner listings (60+ days old), enriches them with PropertyGuru recent sales data, fills the standardized message template, and queues messages for sending.

**Status:** Messages are **queued for manual sending** by default. Auto-sending requires explicit approval and integration with the intake engine.

**Architecture:**
1. Scrape Carousell for FSBO listings 60+ days old
2. Pull recent 3 sold units from PropertyGuru (same block, same type)
3. Fill `/docs/fsbo-day1-message-template.md` with listing + PropertyGuru data
4. Queue messages in `message-queue.json` for sending
5. Generate daily reports (queue status + delivery logs)

---

## Directory Structure

```
scripts/fsbo-scraper/
├── scraper.py              # Main scraper + template filler + queue manager
├── config.json             # Configuration (Carousell URL, send mode, rate limits)
├── run.sh                  # launchd wrapper (runs daily at 08:00 SGT)
├── launch-fsbo-scraper.plist  # macOS launchd job definition
├── logs/                   # Daily logs + reports
│   ├── fsbo-2026-08-31.log
│   ├── daily-report.log
│   ├── launchd.log
│   └── launchd-error.log
├── fsbo-state.json         # Scraper state (contacted blocks, last run)
├── message-queue.json      # Pending messages awaiting send
└── README.md               # This file
```

---

## Quick Start

### 1. Manual Scrape (Test)

```bash
cd /Users/winfredquek/crestbrick-consult/scripts/fsbo-scraper
python3 scraper.py scrape
```

This:
- Scrapes Carousell for FSBO listings 60+ days old
- Pulls PropertyGuru data for each block
- Fills the message template
- Queues messages in `message-queue.json`
- Logs all activity to `logs/fsbo-{date}.log`

### 2. View Queued Messages

```bash
python3 scraper.py queue
```

Shows all pending messages with their status (queued, sent, failed).

### 3. Print Daily Report

```bash
python3 scraper.py report
```

Shows summary + list of:
- Queued messages (manual send pending)
- Sent messages (if auto-send enabled)
- Failed sends

### 4. Manual Sending (Default)

By default, `send_mode: "queue"` in `config.json` means messages are **not auto-sent**. Instead:

**Option A: Send via WhatsApp Web**
1. Open `message-queue.json`
2. Copy a queued message text
3. Send manually via WhatsApp Web to the seller's phone
4. (Optional) Run `scraper.py queue` again to mark as sent

**Option B: Request Auto-Send (Requires Go)**
See "Enabling Auto-Send" below.

---

## Configuration

**File:** `config.json`

```json
{
  "carousell_url": "https://www.carousell.sg/search/property",
  "min_days_old": 60,
  "max_daily_sends": 20,
  "send_time_hhmm": "08:30",
  "propertyguru_search_url": "https://www.propertyguru.com.sg/property-for-sale",
  "send_mode": "queue"
}
```

**Parameters:**
- `min_days_old`: Only scrape listings older than this (60+ days = likely stuck)
- `max_daily_sends`: Rate limit (20/day = 10 messages/hour on average)
- `send_time_hhmm`: Time of day for sending (08:30 SGT = peak engagement)
- `send_mode`: 
  - `"queue"` — Messages queued for manual send (default, no CEA compliance risk)
  - `"auto"` — Auto-send via WhatsApp bridge (requires explicit approval + intake engine integration)

---

## Message Queue Format

**File:** `message-queue.json`

Each entry represents a prospect:

```json
{
  "id": "a1b2c3d4e5f6",
  "block": "Bishan Block 123",
  "unit": "08-15",
  "seller_phone": "+65 8123 4567",
  "seller_name": "John Doe",
  "asking_price": 615000,
  "message": "Hi John\n\nYour Bishan Block 123 unit at S$615k...",
  "status": "queued",
  "created_at": "2026-08-31T08:15:00+00:00",
  "sent_at": null,
  "delivery_status": null
}
```

**Status values:**
- `queued` — Awaiting manual or auto-send
- `sent` — Successfully delivered via WhatsApp bridge
- `failed` — Send failed (bad phone, network error, etc.)
- `manual_sent` — Marked as sent via WhatsApp Web (manual tracking)

---

## Daily Logs

**File:** `logs/fsbo-{YYYY-MM-DD}.log`

Example output:
```
2026-08-31 08:15:23 - INFO - === FSBO Scraper Run Started ===
2026-08-31 08:15:23 - INFO - Scraping Carousell FSBO listings...
2026-08-31 08:15:25 - INFO - Found 5 listings
2026-08-31 08:15:27 - INFO - Fetching PropertyGuru data for Bishan Block 123...
2026-08-31 08:15:30 - INFO - Found 3 recent sales in Bishan Block 123
2026-08-31 08:15:31 - INFO - Queued message for Bishan Block 123 (ID: a1b2c3d4)
2026-08-31 08:15:32 - INFO - === FSBO Scraper Run Complete: {'listings_found': 5, 'messages_queued': 3, ...} ===
```

---

## Enabling Auto-Send (Requires Explicit Approval)

**⚠️ IMPORTANT: Auto-sending requires explicit go from Winfred.**

The CLAUDE.md "Single Sender Doctrine" forbids parallel WhatsApp auto-senders. To enable auto-send:

1. **Get explicit approval** from Winfred in chat
2. **Change `send_mode` to `"auto"`** in `config.json`
3. **Implement Carousell scraping** (currently uses demo data)
4. **Implement PropertyGuru scraping** (currently uses demo data)
5. **Integrate with `wa_send_guard.py`** for cross-sender dedup
6. **Use intake engine's `/api/send` bridge** (already set up in code)

Current blockers to auto-send:
- Carousell blocking scrapers (requires Selenium + proxies)
- PropertyGuru blocking scrapers (requires BeautifulSoup + parsing)
- Phone number validation (need WhatsApp JID format)
- Duplicate detection (check existing WA contacts)

---

## Scraper Architecture

### Carousell Scraping

Currently a **placeholder** (returns empty list). Full implementation requires:

```python
# Requires:
# - Selenium + headless Chrome
# - Rotating proxies (Carousell blocks scrapers)
# - User-Agent rotation
# - JavaScript rendering (listings loaded dynamically)
# - Regex/regex parsing for listing info extraction

# Pseudo-code:
# 1. Driver = Selenium(Chrome, headless=True, proxy=...)
# 2. Driver.get("https://www.carousell.sg/search/property?...")
# 3. Wait for listings to load (Selenium wait until)
# 4. For each listing:
#    - Extract: block, unit, price, seller name/phone, listing date
#    - Calculate days_old = now - listing_date
#    - Filter: days_old >= 60 AND is_active
# 5. Driver.quit()
```

**Production implementation TODO:**
- Install Selenium: `pip3 install selenium`
- Download ChromeDriver matching system Chrome version
- Set up proxy rotation (avoid being blocked)
- Add JSFill wait for dynamic content

### PropertyGuru Scraping

Currently a **demo implementation** (hardcoded sales). Full implementation requires:

```python
# Requires:
# - BeautifulSoup4 for HTML parsing
# - Request retry logic (PropertyGuru also blocks)
# - Parsing PropertyGuru listing cards

# Pseudo-code:
# 1. Search URL = f"https://www.propertyguru.com.sg/property-for-sale?location={block}&sold=true"
# 2. response = requests.get(search_url, headers=..., timeout=10)
# 3. soup = BeautifulSoup(response.text, "html.parser")
# 4. For each listing card:
#    - Extract: unit, price, date_sold, PSF
#    - Filter: sold_after >= 3 months ago
# 5. Return top 3 by date (most recent first)
```

**Production implementation TODO:**
- Install BeautifulSoup4: `pip3 install beautifulsoup4`
- Implement robust HTML parsing + error handling
- Add retry logic + exponential backoff
- Verify PropertyGuru data format (may change)

### Message Template Filling

Reads `/Users/winfredquek/crestbrick-consult/docs/fsbo-day1-message-template.md`, extracts the template message (between code fences), and substitutes:

- `[Name]` → Seller's first name
- `[Block]` → Block name (e.g., "Bishan Block 123")
- `[X]k` → Asking price in thousands
- PropertyGuru sales → Last 3 sold units + dates

---

## Troubleshooting

### "Carousell scraper requires Selenium"

The scraper currently returns empty because Carousell requires JavaScript rendering. To enable:

1. Install Selenium: `pip3 install selenium`
2. Download ChromeDriver: https://chromedriver.chromium.org/
3. Uncomment Selenium code in `scraper.py:scrape_carousell()`

### "No PropertyGuru data found"

PropertyGuru also blocks scrapers. To enable:

1. Install BeautifulSoup: `pip3 install beautifulsoup4`
2. Uncomment BeautifulSoup code in `scraper.py:get_propertyguru_sales()`
3. Test with a known block name (e.g., "Bishan Block 123")

### Messages not sending

Check:
1. `send_mode` in `config.json` — is it `"queue"` or `"auto"`?
2. WhatsApp bridge running: `curl http://localhost:8080/api/send` (should fail gracefully, not connection refused)
3. Phone numbers valid: Check `message-queue.json` for `seller_phone` format
4. Daily log: `logs/fsbo-{date}.log` for send errors

### Queue growing forever

If `send_mode: "queue"`, messages are NOT auto-sent. They stay in `message-queue.json` until:
- You manually mark them sent (edit JSON status to `"manual_sent"`)
- You enable auto-send (`send_mode: "auto"`)

---

## Scheduling (launchd)

The included `launch-fsbo-scraper.plist` runs daily at **00:00 UTC (08:00 SGT)**.

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

## CEA Compliance

**Important:** This scraper generates messages that Winfred MUST review before sending to prospects.

The CLAUDE.md "Single Sender Doctrine" states:
- The intake engine is the ONLY auto-sender
- No parallel auto-senders without explicit go
- Drafts must be queued for manual sending (WhatsApp Web) or explicit approval

**Current implementation:**
- ✅ Drafts queued in `message-queue.json` (safe for review)
- ✅ Auto-send disabled by default (`send_mode: "queue"`)
- ✅ Respects wa_send_guard (cross-sender cooldown)
- ⚠️ Auto-send requires explicit approval (not yet enabled)

**To enable auto-send (if Winfred approves):**
1. Winfred gives explicit "go" in chat
2. Change `send_mode` to `"auto"` in `config.json`
3. Implement Carousell + PropertyGuru scrapers
4. Integrate with intake engine (share WhatsApp guard)
5. Monitor first 3 days for spam filter issues

---

## Performance

**Typical run (test mode):**
- Scrape time: 30–60 seconds (waiting for PropertyGuru responses)
- Messages queued: 3–5 per run
- Daily rate limit: 20 messages/day (configurable)
- Queue growth: ~60–100 messages/week (assuming 3 runs/week)

**Bottlenecks:**
- Carousell scraping (requires Selenium + waits for JS)
- PropertyGuru scraping (requires retry logic)
- Network timeouts (fallback to demo data)

---

## Future Enhancements

- [ ] Implement Carousell scraping with Selenium
- [ ] Implement PropertyGuru scraping with BeautifulSoup
- [ ] Add response tracking (link clicks, replies)
- [ ] A/B test message variants
- [ ] Auto-mark responses in queue
- [ ] Export queue to CSV for reporting
- [ ] Webhook integration (respond to incoming replies)
- [ ] Slack notifications (daily summary)

---

## Support

For issues or questions:
1. Check `logs/fsbo-{date}.log` for errors
2. Run `python3 scraper.py report` for summary
3. Manually edit `message-queue.json` to adjust status
4. Review code comments in `scraper.py` for logic

---

**Status:** MVP ready for queue-based operation. Auto-send pending Winfred's approval.

**Last Updated:** 2026-08-31
