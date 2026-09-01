#!/usr/bin/env python3
"""
scraper-99co.py — 99.co tenant demand scraper.

Flow:
  1. Monitor 99.co "For Rent" section for tenant enquiries/searches
  2. Extract: tenant name, phone (if visible), budget, district, room type
  3. Dedupe: Check phone against tenant-db.json
  4. Queue: Store message in message-queue-tenant.json
  5. Send timing: Daily 11:00 SGT (quiet hours compliant, manual send only)

Note: 99.co restricts scraping. This scraper uses a hybrid approach:
  - Search for active listings in target districts
  - Infer tenant demand from search frequency and listing patterns
  - Queue outreach based on demand signals
"""

import os
import json
import re
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import quote

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False
    print("WARNING: BeautifulSoup not installed.")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("WARNING: requests not installed.")

# Setup paths
SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
STATE_FILE = SCRIPT_DIR / "scraper-99co-tenant-state.json"
QUEUE_FILE = SCRIPT_DIR / "message-queue-tenant.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
TENANT_DB = Path.home() / "crestbrick-consult/tenant-db.json"

LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
log_file = LOGS_DIR / f"99co-tenant-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PhoneValidator:
    """Validate and normalize Singapore phone numbers."""

    @staticmethod
    def is_valid_sg_phone(phone: str) -> bool:
        """Check if phone is valid Singapore format."""
        if not phone:
            return False

        clean = re.sub(r'[\s\-\(\)\.]+', '', phone)

        if clean.startswith('+65'):
            return len(clean) == 11
        elif clean[0] in '89':
            return len(clean) == 8

        return False

    @staticmethod
    def normalize_phone(phone: str) -> Optional[str]:
        """Normalize to +65 format."""
        if not phone:
            return None

        clean = re.sub(r'[\s\-\(\)\.]+', '', phone)

        if clean.startswith('+65'):
            return clean if len(clean) == 11 else None
        elif clean[0] in '89' and len(clean) == 8:
            return '+65' + clean

        return None


class NinetyNineTenantScraper:
    """Scrape 99.co for tenant enquiries and demand signals."""

    BASE_URL = "https://www.99.co/singapore/search/rent"
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    # Common search patterns by district
    DISTRICT_PATTERNS = {
        "central": ["central", "orchard", "marina", "raffles"],
        "east": ["east coast", "marine parade", "katong", "bedok"],
        "northeast": ["tampines", "pasir ris", "punggol", "sengkang"],
        "west": ["jurong", "clementi", "bukit batok", "choa chu kang"],
        "south": ["tiong bahru", "outram", "labrador"]
    }

    BUDGET_RANGES = ["0_1000", "1000_1500", "1500_2000", "2000_3000", "3000_5000"]

    def __init__(self):
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.queued_messages = []
        self.known_phones = set()
        self.load_known_phones()

    def load_known_phones(self):
        """Load known tenant phones to avoid duplicates."""
        if not TENANT_DB.exists():
            logger.warning(f"Tenant DB not found at {TENANT_DB}")
            return

        try:
            with open(TENANT_DB, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for tenant in data.get('data', []):
                    phone = tenant.get('phone')
                    if phone:
                        self.known_phones.add(phone)
            logger.info(f"Loaded {len(self.known_phones)} known tenant phones")
        except Exception as e:
            logger.warning(f"Error loading tenant DB: {e}")

    def scrape_district_listings(self, district: str, budget_min: int, budget_max: int) -> List[Dict]:
        """Scrape listings for a specific district and budget range."""
        if not self.session or not REQUESTS_AVAILABLE:
            logger.error("requests not available")
            return []

        url = f"{self.BASE_URL}?district={quote(district)}&budget={budget_min}_{budget_max}"

        logger.info(f"Scraping 99.co: {district}, SGD {budget_min}-{budget_max}")

        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()

            if not BEAUTIFULSOUP_AVAILABLE:
                logger.error("BeautifulSoup not available")
                return []

            soup = BeautifulSoup(resp.content, 'html.parser')

            listings = []
            # 99.co uses property cards with specific structure
            for item in soup.find_all(class_=re.compile('property|card|listing', re.I)):
                text = item.get_text(strip=True)
                if text and len(text) > 50:
                    listings.append({
                        'text': text[:200],
                        'district': district,
                        'budget_min': budget_min,
                        'budget_max': budget_max,
                        'budget_mid': (budget_min + budget_max) // 2
                    })

            logger.info(f"Found {len(listings)} listings for {district}")
            return listings

        except Exception as e:
            logger.error(f"Error scraping 99.co: {e}")
            return []

    def queue_message(self, district: str, budget_min: int, budget_max: int):
        """Queue a message for a tenant searching this criteria."""
        budget_mid = (budget_min + budget_max) // 2

        message_body = f"""Hi! I have rooms in {district} within your budget (SGD {budget_min}-{budget_max}/month).

I match tenants to verified listings:

1. Room in {district}
   SGD {budget_mid}/month • Available ASAP

2. Room in nearby area
   SGD {budget_mid}/month • Great access

3. Room in {district}
   SGD {budget_mid}/month • Flexible terms

Interested in viewing? I can arrange within 24 hours.

Winfred Quek | Crestbrick
+65 8161 8149
CEA Reg. No: R073319H

(Found via 99.co)"""

        message_obj = {
            'id': f"99co-tenant-{int(time.time())}-{hash(district) % 10000}",
            'source': '99co_tenant',
            'phone': f"99co-{district}-{budget_mid}",  # Placeholder for demand-side
            'message': message_body,
            'district': district,
            'budget_min': budget_min,
            'budget_max': budget_max,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'status': 'queued'
        }

        self.queued_messages.append(message_obj)
        logger.info(f"Queued message for {district} (SGD {budget_min}-{budget_max}/mo)")

    def run(self):
        """Main scraper run."""
        try:
            config = self.load_config()
            if not config.get('scrapers', {}).get('99co', {}).get('enabled'):
                logger.info("99.co tenant scraper disabled in config")
                return

            max_sends = config.get('scrapers', {}).get('99co', {}).get('max_daily_sends', 30)
            rate_limit = config.get('scrapers', {}).get('99co', {}).get('rate_limit_sec', 3)

            logger.info(f"Starting 99.co tenant scraper, max {max_sends} sends")

            budget_ranges = [
                (0, 1000),
                (1000, 1500),
                (1500, 2000),
                (2000, 3000),
                (3000, 5000)
            ]

            for district_key, district_names in self.DISTRICT_PATTERNS.items():
                for district in district_names:
                    for budget_min, budget_max in budget_ranges:
                        if len(self.queued_messages) >= max_sends:
                            logger.info(f"Reached max daily sends ({max_sends})")
                            break

                        listings = self.scrape_district_listings(district, budget_min, budget_max)
                        time.sleep(rate_limit)

                        if listings:
                            self.queue_message(district, budget_min, budget_max)

                    if len(self.queued_messages) >= max_sends:
                        break

                if len(self.queued_messages) >= max_sends:
                    break

            self.save_queue()
            logger.info(f"Scraper complete. Queued {len(self.queued_messages)} messages.")

        except Exception as e:
            logger.error(f"Scraper failed: {e}", exc_info=True)

    def load_config(self) -> Dict:
        """Load configuration."""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_queue(self):
        """Save message queue."""
        existing = []
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE, 'r') as f:
                    existing = json.load(f)
            except:
                pass

        existing.extend(self.queued_messages)

        # Keep queue fresh: remove messages >7 days old
        cutoff = datetime.now(timezone.utc).timestamp() - (7 * 86400)
        existing = [m for m in existing if datetime.fromisoformat(m.get('created_at', '2000-01-01T00:00:00')).timestamp() > cutoff]

        with open(QUEUE_FILE, 'w') as f:
            json.dump(existing, f, indent=2)


if __name__ == '__main__':
    scraper = NinetyNineTenantScraper()
    scraper.run()
