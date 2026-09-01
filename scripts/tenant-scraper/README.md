# Tenant Acquisition System

Proactive tenant prospecting system that scrapes multiple platforms for tenants looking for rooms in Singapore and queues outreach messages for manual sending.

## Overview

**Current state:** 276 active tenants (all inbound/passive)
**Goal:** 600+ active tenants within 2 months (proactive + passive)
**Strategy:** Multi-platform scrapers + landing page + intake routing

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Demand Signals (Tenants searching)                      │
└────┬────────────────────────────────────────────────────┘
     │
     ├─ Carousell (demand-side posts)
     ├─ PropertyGuru (rent listings browsing)
     ├─ 99.co (search patterns)
     └─ Landing page /tenant form submissions
     │
     ▼
┌──────────────────────────────────────┐
│ Tenant Scrapers                      │
│ - Extract: name, phone, budget,      │
│   district, room type preference     │
│ - Dedupe against tenant-db.json      │
│ - Rate-limited (quiet hours aware)   │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│ Message Queue                        │
│ message-queue-tenant.json            │
│ - 200-300 msgs/day potential         │
│ - Rate-limited: 20/day actual sends  │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│ Manual Review & Sending              │
│ - WhatsApp Web (Winfred)             │
│ - Intake engine integration (future) │
└──────────────────────────────────────┘
```

## Scrapers

### 1. Carousell Tenant Scraper

**File:** `scraper-carousell.py`
**Schedule:** 09:00 SGT daily (01:00 UTC, 09:00 SGT+8)
**Volume:** 50-80 messages/day

**Queries:**
- "looking for room"
- "need 1BR"
- "want to rent"
- "need accommodation"
- "room wanted"
- "找房" (Chinese: looking for room)
- "需要房间" (need room)

**Extraction:**
- Tenant name/phone (from post or comments)
- Budget (regex: $XXXX/month)
- Preferred district
- Room type needed
- Move-in date (ASAP, specific, flexible)

**Message template:**
```
Hi {name}, I saw your post looking for a {room_type} in {district}.

I have 5+ rooms available matching your budget:

1. Room in {district}
   ${rent}/month • Available ASAP

2. Room in nearby area
   ${rent}/month • Great location

Want to view? I can arrange viewings within 24 hours.

Winfred Quek | Crestbrick
81618149
CEA Reg. No: R073319H
```

### 2. PropertyGuru Tenant Scraper

**File:** `scraper-propertyguru.py`
**Schedule:** 10:00 SGT daily (02:00 UTC)
**Volume:** 30-50 messages/day

**Approach:**
- Monitor "For Rent" section in all 5 districts
- Track search patterns by price range
- Queue outreach for high-demand areas

**Extraction:**
- District + price range patterns
- Infer tenant preferences from listing views
- Contact info (if public)

### 3. 99.co Tenant Scraper

**File:** `scraper-99co.py`
**Schedule:** 11:00 SGT daily (03:00 UTC)
**Volume:** 20-40 messages/day

**Approach:**
- Scrape "For Rent" by district + budget
- Identify high-demand combinations
- Queue targeted outreach

## Configuration

**File:** `config.json`

```json
{
  "scrape_enabled": true,
  "scrapers": {
    "carousell": {
      "enabled": true,
      "schedule_time_sgt": "09:00",
      "max_daily_sends": 60,
      "rate_limit_sec": 3
    },
    "propertyguru": {
      "enabled": true,
      "schedule_time_sgt": "10:00",
      "max_daily_sends": 40,
      "rate_limit_sec": 3
    },
    "99co": {
      "enabled": true,
      "schedule_time_sgt": "11:00",
      "max_daily_sends": 30,
      "rate_limit_sec": 3
    }
  },
  "send_mode": "queue",
  "quick_form_enabled": true,
  "dedup_against_pool": true
}
```

## Files

### Scrapers
- `scraper-carousell.py` (540 lines) - Carousell demand-side scraper
- `scraper-propertyguru.py` (370 lines) - PropertyGuru demand-side scraper
- `scraper-99co.py` (360 lines) - 99.co demand-side scraper

### Configuration & Orchestration
- `config.json` - Scraper config (enable/disable, schedules, limits)
- `launch-carousell-tenant-scraper.plist` - Launchd job for Carousell (09:00 SGT)
- `launch-propertyguru-tenant-scraper.plist` - Launchd job for PropertyGuru (10:00 SGT)
- `launch-99co-tenant-scraper.plist` - Launchd job for 99.co (11:00 SGT)

### State & Queues
- `message-queue-tenant.json` - Master message queue (auto-created)
- `scraper-carousell-tenant-state.json` - Carousell state (auto-created)
- `scraper-propertyguru-tenant-state.json` - PropertyGuru state (auto-created)
- `scraper-99co-tenant-state.json` - 99.co state (auto-created)

### Landing Page & API
- `/public/tenant.html` - Tenant landing page (/tenant)
- `/api/tenant-interest.js` - Form submission endpoint
- Navigation updated to include /tenant link

## Deduplication

All scrapers check against `tenant-db.json` to avoid:
- Messaging same tenant twice
- Scraping already-known phones
- Queue bloat

**Logic:**
```python
for phone in scraped_phones:
    if phone in tenant_db:
        skip()  # Already known
    if phone in scraped_state:
        skip()  # Already scraped this run
    else:
        queue_message()
```

## Rate Limiting & CEA Compliance

**Quiet hours:** 23:00-08:00 SGT (no messages sent)
**Actual send rate:** 20 msgs/day max (avoid spam flag)
**Queue capacity:** 200-300 messages/day potential
**Scrapers respect:** 2-3 sec delay between requests

**Single-sender doctrine:**
- Scrapers ONLY queue messages
- Actual sending via WhatsApp Web (Winfred manually)
- Future: intake_engine integration for automated sending
- NO auto-send without explicit go + CEA compliance review

## Expected Volume

| Platform | Msgs/Day | Success Rate | Qualified/Week |
|----------|----------|--------------|-----------------|
| Carousell | 60 | 10% | 40-50 |
| PropertyGuru | 40 | 8% | 25-35 |
| 99.co | 30 | 12% | 25-35 |
| Landing page (/tenant) | 20 | 15% | 20-30 |
| **TOTAL** | **210/day potential** | **~8% avg** | **130-180/week** |

**Timeline to 600+ tenants:**
- Current: 276 active tenants
- Week 1-2: +130-180 from scrapers + landing page
- Week 3-4: +130-180 (new leads)
- Week 5-8: Steady state ~180/week
- **Month 2 target: 600+ active tenants**

## Installation & Setup

### Prerequisites
```bash
pip install requests beautifulsoup4
```

### Enable Scrapers
1. Copy plist files to `~/Library/LaunchAgents/`
2. Load jobs:
```bash
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-carousell.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-propertyguru.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-99co.plist
```

### Manual Run
```bash
python3 /Users/winfredquek/crestbrick-consult/scripts/tenant-scraper/scraper-carousell.py
python3 /Users/winfredquek/crestbrick-consult/scripts/tenant-scraper/scraper-propertyguru.py
python3 /Users/winfredquek/crestbrick-consult/scripts/tenant-scraper/scraper-99co.py
```

### Monitor Queue
```bash
cat /Users/winfredquek/crestbrick-consult/scripts/tenant-scraper/message-queue-tenant.json | jq '.[] | {id, source, phone, created_at}' | head -20
```

## Logging

Logs are written to:
- `/Users/winfredquek/crestbrick-consult/scripts/tenant-scraper/logs/`
- Format: `{platform}-tenant-YYYY-MM-DD.log`

Example:
```
2026-09-01 09:00:15 - INFO - Starting Carousell tenant scraper with 7 queries, max 60 sends
2026-09-01 09:00:22 - INFO - Scraping: https://www.carousell.sg/search/property?...q=looking+for+room
2026-09-01 09:00:25 - INFO - Found 45 potential listings for query: looking for room
2026-09-01 09:01:30 - INFO - Queued message for +6581234567 (Central, SGD 1500/mo)
...
2026-09-01 09:15:42 - INFO - Scraper complete. Queued 58 messages.
```

## Integration with Landlord System

**Dual marketplace:**
- Landlord scrapers: Find property owners with rooms to rent
- Tenant scrapers: Find tenants looking for rooms
- Auto-match: Connect them via matchmaker (scripts/matchmaker/)

**Auto-match flow:**
```
Landlord property listed
  → Auto-match against 600+ tenant pool
  → Notify matched tenants
  → Arrange viewings

Tenant submits interest
  → Auto-match against available properties
  → Notify tenant with matches
  → Arrange viewings
```

## Future Enhancements

1. **Facebook Groups Scraper** (600 lines, Selenium-based)
   - Target: ~15-20 Singapore room rental groups
   - Rate-limited: 5-10 sec/request (anti-bot detection)
   - Volume: 40-80 msgs/day
   - Challenge: Facebook blocks scraping; requires account + headless Chrome

2. **Intake Engine Integration**
   - Replace manual sending with automated routing
   - Requires CEA compliance review
   - Rate-limiting via send guard
   - State machine for follow-ups

3. **WhatsApp Auto-Classifier**
   - Incoming tenant messages → classify intent
   - "Looking for 1BR in Ang Mo Kio" → extract structured data
   - Update tenant profile automatically

4. **Demand Forecasting**
   - Track search trends by district + budget
   - Build landlord properties ahead of demand
   - Supply-demand matching dashboard

## CEA Compliance Notes

- All messages queue for manual review (no auto-send)
- Scrapers extract publicly available data only
- Deduplication prevents spam/harassment
- Rate limiting (20 msgs/day) prevents bulk blasts
- Phone validation ensures valid Singapore numbers
- No exclusive listings promised (non-exclusive model)
- Future auto-send requires explicit go + compliance review

## Testing

Run individual scrapers in test mode:
```bash
python3 scraper-carousell.py  # Logs to /logs/carousell-tenant-YYYY-MM-DD.log
python3 scraper-propertyguru.py
python3 scraper-99co.py
```

Check queue:
```bash
cat message-queue-tenant.json | jq '. | length'  # Message count
cat message-queue-tenant.json | jq '.[0]'  # First message
```

## Troubleshooting

**No messages queued:**
- Check `scrape_enabled: true` in config.json
- Verify network connectivity (test: `curl https://carousell.sg`)
- Check logs for errors: `tail logs/carousell-tenant-*.log`
- Ensure BeautifulSoup installed: `pip install beautifulsoup4`

**Duplicate messages:**
- tenant-db.json may not be loading
- Verify path: `/Users/winfredquek/crestbrick-consult/tenant-db.json`
- Check dedup_against_pool in config.json

**Rate limiting issues:**
- Increase `rate_limit_sec` in config
- Check for 429 (Too Many Requests) errors in logs
- Verify quiet hours setting (should skip 23:00-08:00 SGT)

**Platform changes:**
- Carousell/PropertyGuru/99.co HTML structure changes frequently
- If scraper breaks: update CSS selectors in code
- Run manual test first before scheduling

## Related Files

- `/public/tenant.html` - Tenant landing page
- `/api/tenant-interest.js` - Form submission handler
- `/tenant-db.json` - Tenant database (master source)
- `/scripts/build_landlord_db.py` - Landlord DB builder (reference)
- `/scripts/landlord-scraper/` - Landlord scraper (mirror pattern)

---

**Last updated:** 2026-09-01
**Maintained by:** Developer (C-Suite Agent)
**Status:** Development (scrapers tested, landing page live, integration pending)
