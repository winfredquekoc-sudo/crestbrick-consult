#!/bin/bash
set -e

# FSBO Scraper daily run wrapper
# Called by launchd daily at 00:00 SGT (StartCalendarInterval uses local time)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# System python3: launchd convention here (Homebrew python trips TCC prompts; curl_cffi installed for this interpreter)
PYTHON=/usr/bin/python3

# Run scraper in 'scrape' mode (find listings + queue messages)
$PYTHON scraper.py scrape

# If send_mode == 'auto' in config.json, also send queued messages
# Otherwise, messages stay queued for manual sending via WhatsApp Web
if grep -q '"send_mode": "auto"' config.json 2>/dev/null; then
    $PYTHON scraper.py send
fi

# Log queue status
$PYTHON scraper.py queue >> logs/daily-report.log 2>&1

echo "FSBO scraper run complete at $(date)"
