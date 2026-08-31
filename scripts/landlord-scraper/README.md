# Landlord Acquisition Scraper System

Automated prospecting system for finding and contacting new landlords on Carousell, PropertyGuru, and 99.co.

## Architecture

- **Three scrapers**: One for each portal (Carousell, PropertyGuru, 99.co)
- **Message queueing**: All messages go to `message-queue-landlord.json` for manual review and sending
- **CEA compliance**: Single-sender doctrine enforced; all messages must go through `intake_engine.py` + WhatsApp bridge
- **Deduplication**: Landlords already in `landlord-db.json` are skipped

## Files

```
landlord-scraper/
├── scraper-carousell.py        # Carousell rental listing scraper
├── scraper-propertyguru.py     # PropertyGuru individual landlord listings
├── scraper-99co.py             # 99.co rental listings
├── config.json                 # Configuration (schedules, caps)
├── launch-carousell-scraper.plist    # launchd job (08:00 SGT daily)
├── launch-propertyguru-scraper.plist # launchd job (09:00 SGT daily)
├── launch-99co-scraper.plist         # launchd job (10:00 SGT daily)
├── message-queue-landlord.json # Output queue (shared across all scrapers)
├── logs/                       # Daily logs per scraper
└── README.md                   # This file
```

## Schedule

- **08:00 SGT**: Carousell scraper (50 msgs/day max)
- **09:00 SGT**: PropertyGuru scraper (40 msgs/day max)
- **10:00 SGT**: 99.co scraper (20 msgs/day max)

Total: ~80-110 messages/day queued for manual sending (quiet hours compliant).

## Installation

### 1. Install Python dependencies

```bash
pip install beautifulsoup4 requests
```

### 2. Register launchd jobs

```bash
# Carousell
launchctl load ~/crestbrick-consult/scripts/landlord-scraper/launch-carousell-scraper.plist

# PropertyGuru
launchctl load ~/crestbrick-consult/scripts/landlord-scraper/launch-propertyguru-scraper.plist

# 99.co
launchctl load ~/crestbrick-consult/scripts/landlord-scraper/launch-99co-scraper.plist
```

### 3. Verify installation

```bash
launchctl list | grep crestbrick.scraper
```

You should see three jobs listed.

## Usage

### Manual run

```bash
python3 ~/crestbrick-consult/scripts/landlord-scraper/scraper-carousell.py
python3 ~/crestbrick-consult/scripts/landlord-scraper/scraper-propertyguru.py
python3 ~/crestbrick-consult/scripts/landlord-scraper/scraper-99co.py
```

### Check queue

```bash
cat ~/crestbrick-consult/scripts/landlord-scraper/message-queue-landlord.json | jq '.'
```

### View logs

```bash
# Today's logs
tail -f ~/crestbrick-consult/scripts/landlord-scraper/logs/carousell-*.log

# All Carousell runs
ls ~/crestbrick-consult/scripts/landlord-scraper/logs/carousell-*.log
```

## Queue Format

```json
[
  {
    "pn": "+6581234567",
    "name": "Mr. Tan",
    "message": "Hi Mr. Tan, I saw your room on Carousell...",
    "listing": {
      "title": "Cozy room in Tiong Bahru",
      "rent_sgd": 1500,
      "location": "D3 - Tiong Bahru",
      "source": "carousell",
      "listing_url": "https://www.carousell.sg/p/...",
      "scraped_at": "2026-09-01T08:15:00+00:00"
    },
    "queued_at": "2026-09-01T08:15:30+00:00",
    "status": "pending"
  }
]
```

## Sending Messages

**IMPORTANT**: Messages are NOT auto-sent. They are queued for manual review.

To send:
1. Review message-queue-landlord.json
2. Open WhatsApp Web
3. Copy-paste messages and send manually (or use a WhatsApp tool if approved)
4. Mark status as "sent" when done

For future integration: once Winfred approves, messages can be auto-sent via `intake_engine.py` which respects CEA compliance and the single-sender doctrine.

## Deduplication

Before queuing a message, each scraper checks:
1. `landlord-db.json` for existing landlord records
2. If phone is found, the listing is skipped

This prevents re-contacting known landlords.

## Troubleshooting

### Scraper fails to run
```bash
python3 ~/crestbrick-consult/scripts/landlord-scraper/scraper-carousell.py
```
Check stderr output for missing dependencies or network issues.

### BeautifulSoup parse errors
Portal DOM may have changed. Check logs and update CSS selectors in `_parse_card()` methods.

### No messages queued
- Check if scrapers found any listings (view logs)
- Verify all listings are already in landlord-db.json (deduped)
- Check network connectivity (timeout errors in logs)

### launchd job not running
```bash
# Check job status
launchctl list | grep com.crestbrick.scraper

# Unload and reload
launchctl unload ~/crestbrick-consult/scripts/landlord-scraper/launch-carousell-scraper.plist
launchctl load ~/crestbrick-consult/scripts/landlord-scraper/launch-carousell-scraper.plist

# View error logs
cat ~/crestbrick-consult/scripts/landlord-scraper/logs/carousell-launchd-error.log
```

## CEA Compliance Notes

1. **No auto-sending**: Messages are queued, never auto-sent
2. **Single sender**: intake_engine.py is the only authorized WhatsApp sender
3. **Manual review**: Winfred reviews all queued messages before sending
4. **Opt-out**: All messages include contact info and no pressure to respond
5. **Phone validation**: Only valid Singapore numbers are contacted

## Future Enhancements

1. **Auto-send integration**: Once approved, messages can flow through intake_engine.py
2. **Response tracking**: Log replies from landlords
3. **Follow-up sequences**: Auto-nudge after 3 days if no response
4. **Photo harvesting**: Pull listing photos for presentation to tenants
5. **Lead scoring**: Prioritize high-quality landlords (high rent, good reviews)

## Support

For issues or updates:
- Check logs in `logs/` directory
- Review scraper source for portal-specific logic
- Contact Winfred for CEA or sending policy questions
