# Tenant Acquisition System — Quick Start

Get the tenant acquisition system up and running in 5 minutes.

## Prerequisites

```bash
# Verify Python 3 is available
python3 --version

# Install required packages (if not already installed)
pip install requests beautifulsoup4
```

## 1. Test the Landing Page

The `/tenant` landing page is already live. Visit:
```
http://localhost:3000/tenant  (local dev)
https://winfredquek.com/tenant  (production)
```

Expected:
- Form with 5 fields (district, budget, room type, move-in date, WhatsApp)
- Featured listings carousel (auto-loads 6-8 rooms)
- Trust signals (CEA license, 600+ rooms, 2-3 day placement)
- FAQs section

Submit the form to test:
- Should show success message: "Got it. I will WhatsApp you within 24 hours..."
- Should send Telegram notification to Winfred
- Should redirect to `/tenant?sent=1`

## 2. Test the Scrapers (Manual Run)

### Run all scrapers at once:
```bash
cd /Users/winfredquek/crestbrick-consult/scripts/tenant-scraper

python3 scraper-carousell.py
python3 scraper-propertyguru.py
python3 scraper-99co.py
```

### Check the queue:
```bash
# See message count
cat message-queue-tenant.json | jq '. | length'

# See first message
cat message-queue-tenant.json | jq '.[0]'

# See all messages (pretty-printed)
jq . message-queue-tenant.json
```

### Check logs:
```bash
# List all logs
ls -lh logs/

# View today's Carousell log
tail -50 logs/carousell-tenant-$(date +%Y-%m-%d).log

# Follow live (Carousell)
tail -f logs/carousell-tenant-$(date +%Y-%m-%d).log
```

## 3. Enable Automatic Scheduling (Optional)

Copy plist files to launchd:
```bash
cd /Users/winfredquek/crestbrick-consult/scripts/tenant-scraper

# Copy to LaunchAgents
cp launch-carousell-tenant-scraper.plist ~/Library/LaunchAgents/
cp launch-propertyguru-tenant-scraper.plist ~/Library/LaunchAgents/
cp launch-99co-tenant-scraper.plist ~/Library/LaunchAgents/

# Load jobs
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-carousell.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-propertyguru.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-99co.plist

# Verify they're loaded
launchctl list | grep tenant-scraper
```

Expected output:
```
-     0  com.crestbrick.tenant-scraper-carousell
-     0  com.crestbrick.tenant-scraper-propertyguru
-     0  com.crestbrick.tenant-scraper-99co
```

Jobs will run automatically at:
- 09:00 SGT (Carousell)
- 10:00 SGT (PropertyGuru)
- 11:00 SGT (99.co)

## 4. Monitor Queue Growth

Daily checklist:
```bash
# Count messages in queue
wc -l scripts/tenant-scraper/message-queue-tenant.json

# Check latest log entries
tail logs/carousell-tenant-*.log | grep "Queued message"
tail logs/propertyguru-tenant-*.log | grep "Queued message"
tail logs/99co-tenant-*.log | grep "Queued message"

# Sample a message
cat scripts/tenant-scraper/message-queue-tenant.json | jq '.[-1]'
```

## 5. Send Messages (Manual Review)

Messages are queued but NOT sent automatically. To send:

1. Open WhatsApp Web (https://web.whatsapp.com)
2. Review message from queue:
   ```bash
   cat scripts/tenant-scraper/message-queue-tenant.json | jq '.[0:3]'
   ```
3. Copy the message body
4. Find the tenant's phone in WhatsApp
5. Paste and send

Future: Automate via intake_engine (pending CEA compliance review)

## 6. Troubleshooting

### No queue file created?
- Check logs: `tail logs/carousell-tenant-*.log`
- Verify config: `cat config.json | jq '.scrapers.carousell.enabled'`
- Ensure it returns `true`

### Scraper fails immediately?
- Check network: `curl -I https://carousell.sg` (should return 200)
- Check imports: `python3 -c "import requests; import bs4"`
- If missing: `pip install requests beautifulsoup4`

### No messages in queue?
- Check if dedup is filtering everything:
  ```bash
  # Load tenant DB and show first 5 phones
  jq '.data[0:5] | .[] | {phone, status}' tenant-db.json
  ```
- Manually disable dedup to test:
  ```bash
  # Edit config.json
  "dedup_against_pool": false
  ```

### Launchd job not running?
```bash
# Check if loaded
launchctl list | grep tenant-scraper

# Unload and reload
launchctl unload ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-carousell.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.tenant-scraper-carousell.plist

# Check logs
tail /Users/winfredquek/.claude/logs/tenant-scraper-carousell.log
```

## 7. Configuration Tweaks

Edit `config.json` to adjust:

```json
{
  "scrapers": {
    "carousell": {
      "max_daily_sends": 60,       // Increase for more volume
      "rate_limit_sec": 3          // Increase if getting blocked
    }
  },
  "send_mode": "queue"             // Leave as "queue" (manual review)
}
```

Common adjustments:
- **More aggressive**: Increase `max_daily_sends` to 80-100
- **Fewer messages**: Decrease `max_daily_sends` to 30-40
- **Getting rate-limited**: Increase `rate_limit_sec` to 5-10
- **Disable a scraper**: Set `enabled: false` for that platform

## 8. Expected Results

**After 1 week:**
- 800-1000+ messages in queue
- Log files show successful scrapes
- Sample 5-10 messages, verify quality

**After 2 weeks:**
- 1600-2000+ messages in queue
- Tenant inquiries should be coming through `/tenant` form
- Queue ready for production sending (intake_engine integration)

**After 1 month:**
- 6000-8000+ messages queued
- Sent ~500-600 high-quality tenant prospects
- Tenant pool should grow 276 → 400-500

## 9. Next Steps

1. Test landing page & form submission
2. Run scrapers manually 2-3 times to verify
3. Enable launchd jobs for automatic scheduling
4. Monitor queue for 1 week
5. Sample & verify message quality
6. Prepare for intake_engine integration (auto-sending)

## File Locations (Reference)

```
/Users/winfredquek/crestbrick-consult/
├── public/tenant.html                           (landing page)
├── api/tenant-interest.js                       (form endpoint)
├── scripts/tenant-scraper/
│   ├── config.json                              (configuration)
│   ├── scraper-carousell.py                     (Carousell scraper)
│   ├── scraper-propertyguru.py                  (PropertyGuru scraper)
│   ├── scraper-99co.py                          (99.co scraper)
│   ├── test_tenant_scrapers.py                  (unit tests)
│   ├── README.md                                (comprehensive docs)
│   ├── QUICKSTART.md                            (this file)
│   ├── launch-*-tenant-scraper.plist            (launchd jobs)
│   ├── message-queue-tenant.json                (message queue, auto-created)
│   ├── scraper-*-tenant-state.json              (state files, auto-created)
│   └── logs/                                    (daily log files)
│       ├── carousell-tenant-2026-09-01.log
│       ├── propertyguru-tenant-2026-09-01.log
│       └── 99co-tenant-2026-09-01.log
└── tenant-db.json                               (tenant database, used for dedup)
```

## Support

For issues:
1. Check logs: `scripts/tenant-scraper/logs/`
2. Read docs: `scripts/tenant-scraper/README.md`
3. Review this guide

Last updated: 2026-09-01
