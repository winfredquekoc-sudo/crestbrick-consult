#!/bin/bash
set -e

# FSBO Scraper daily run wrapper
# Called by launchd at 00:00 UTC (08:00 SGT)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Python 3 from Homebrew (not system)
PYTHON=/opt/homebrew/bin/python3

# Run scraper in 'scrape' mode (find listings + queue messages)
$PYTHON scraper.py scrape

# If send_mode == 'auto' in config.json, also send queued messages
# Otherwise, messages stay queued for manual sending via WhatsApp Web
if grep -q '"send_mode": "auto"' config.json 2>/dev/null; then
    $PYTHON scraper.py send
fi

# Log report
$PYTHON scraper.py report >> logs/daily-report.log 2>&1

echo "FSBO scraper run complete at $(date)"
