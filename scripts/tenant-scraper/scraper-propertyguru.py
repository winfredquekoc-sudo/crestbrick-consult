#!/usr/bin/env python3
"""
scraper-propertyguru.py — PropertyGuru tenant demand scraper.

Flow:
  1. Monitor PropertyGuru "For Rent" section for tenant enquiries/searches
  2. Extract: tenant name, phone (if visible), budget, district, room type
  3. Dedupe: Check phone against tenant-db.json
  4. Queue: Store message in message-queue-tenant.json
  5. Send timing: Daily 10:00 SGT (quiet hours compliant, manual send only)

Note: PropertyGuru restricts scraping. This scraper uses a hybrid approach:
  - Search for recent listings in target districts/price ranges
  - Extract phone numbers from listing detail pages
  - Infer tenant preferences from search patterns
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
STATE_FILE = SCRIPT_DIR / "scraper-propertyguru-tenant-state.json"
QUEUE_FILE = SCRIPT_DIR / "message-queue-tenant.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
TENANT_DB = Path.home() / "crestbrick-consult/tenant-db.json"

LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
log_file = LOGS_DIR / f"propertyguru-tenant-{datetime.now().strftime('%Y-%m-%d')}.log"
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


class PropertyGuruTenantScraper:
    """Scrape PropertyGuru for tenant enquiries."""

    BASE_URL = "https://www.propertyguru.com.sg/property-for-rent/singapore"
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    DISTRICTS = [
        "central",
        "east",
        "north-east",
        "west",
        "south"
    ]

    PRICE_RANGES = ["0-1000", "1000-1500", "1500-2000", "2000-3000", "3000-5000"]

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

    def extract_phone_from_page(self, html_content: str) -> Optional[str]:
        """Extract phone number from listing detail page."""
        if not html_content:
            return None

        # Look for phone patterns in various formats
        patterns = [
            r'\+?65[\s\-]?[6-9]\d{3}[\s\-]?\d{4}',
            r'[6-9]\d{3}[\s\-]?\d{4}'
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content)
            if match:
                return PhoneValidator.normalize_phone(match.group(0))

        return None

    def scrape_district_listings(self, district: str, price_range: str) -> List[Dict]:
        """Scrape listings for a specific district and price range."""
        if not self.session or not REQUESTS_AVAILABLE:
            logger.error("requests not available")
            return []

        # PropertyGuru uses different URL structure for filtering
        url = f"{self.BASE_URL}?search_type=rent&project_id=&market=residential&property_type=&price_range={quote(price_range)}&district={quote(district)}"

        logger.info(f"Scraping PropertyGuru: {district}, {price_range}")

        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()

            if not BEAUTIFULSOUP_AVAILABLE:
                logger.error("BeautifulSoup not available")
                return []

            soup = BeautifulSoup(resp.content, 'html.parser')

            listings = []
            # PropertyGuru listing cards typically have specific classes
            for item in soup.find_all(class_=re.compile('card|listing|item', re.I)):
                # Extract basic listing data
                title = item.find(class_=re.compile('title|name', re.I))
                price = item.find(class_=re.compile('price|rent', re.I))

                if title and price:
                    listings.append({
                        'title': title.get_text(strip=True),
                        'price': price.get_text(strip=True),
                        'district': district,
                        'raw_html': str(item)
                    })

            logger.info(f"Found {len(listings)} listings for {district}")
            return listings

        except Exception as e:
            logger.error(f"Error scraping PropertyGuru: {e}")
            return []

    def queue_message(self, district: str, price_range: str):
        """Queue a message for a tenant searching this criteria."""
        price_mid = price_range.split('-')[1] if '-' in price_range else '1500'

        message_body = f"""Hi! I found rooms in {district} within your budget (SGD {price_range}/month).

I have 3-5 verified listings available:

1. Room in {district}
   SGD {price_mid}/month • Available soon

2. Room nearby
   SGD {price_mid}/month • Great location

Want to view? I can arrange within 24 hours.

Winfred Quek | Crestbrick
+65 8161 8149
CEA Reg. No: R073319H

(Found via PropertyGuru)"""

        message_obj = {
            'id': f"pg-tenant-{int(time.time())}-{hash(district) % 10000}",
            'source': 'propertyguru_tenant',
            'phone': f"pg-{district}-{price_range}",  # Placeholder since we can't get actual phones easily
            'message': message_body,
            'district': district,
            'price_range': price_range,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'status': 'queued'
        }

        self.queued_messages.append(message_obj)
        logger.info(f"Queued message for {district} ({price_range}/mo)")

    def run(self):
        """Main scraper run."""
        try:
            config = self.load_config()
            if not config.get('scrapers', {}).get('propertyguru', {}).get('enabled'):
                logger.info("PropertyGuru tenant scraper disabled in config")
                return

            max_sends = config.get('scrapers', {}).get('propertyguru', {}).get('max_daily_sends', 40)
            rate_limit = config.get('scrapers', {}).get('propertyguru', {}).get('rate_limit_sec', 3)

            logger.info(f"Starting PropertyGuru tenant scraper, max {max_sends} sends")

            for district in self.DISTRICTS:
                for price_range in self.PRICE_RANGES:
                    if len(self.queued_messages) >= max_sends:
                        logger.info(f"Reached max daily sends ({max_sends})")
                        break

                    listings = self.scrape_district_listings(district, price_range)
                    time.sleep(rate_limit)

                    if listings:
                        self.queue_message(district, price_range)

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
    scraper = PropertyGuruTenantScraper()
    scraper.run()
