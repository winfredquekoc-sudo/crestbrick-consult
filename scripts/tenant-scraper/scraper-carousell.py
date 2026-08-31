#!/usr/bin/env python3
"""
scraper-carousell.py — Carousell tenant demand scraper for prospecting.

Flow:
  1. Scrape Carousell "For Rent" section for tenant enquiry posts
  2. Extract: tenant name, phone (if visible), budget, district, room type preferences
  3. Dedupe: Check phone against tenant-db.json (skip if already known)
  4. Queue: Store message + metadata in message-queue-tenant.json
  5. Send timing: Daily 09:00 SGT (quiet hours compliant, manual send only)

This script QUEUES messages only. Actual sending is controlled by:
  - Single-sender doctrine: only intake_engine.py sends (via WhatsApp bridge)
  - Manual review: all messages reviewed before sending via WhatsApp Web
  - CEA compliance: no auto-send without explicit approval

IMPORTANT: This is demand-side prospecting. All sends MUST go through the intake_engine
state machine to respect CEA compliance and the single-sender gate.
"""

import os
import json
import re
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from urllib.parse import quote

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False
    print("WARNING: BeautifulSoup not installed. Install with: pip install beautifulsoup4")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("WARNING: requests not installed. Install with: pip install requests")

# Setup paths
SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
STATE_FILE = SCRIPT_DIR / "scraper-carousell-tenant-state.json"
QUEUE_FILE = SCRIPT_DIR / "message-queue-tenant.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
TENANT_DB = Path.home() / "crestbrick-consult/tenant-db.json"

LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
log_file = LOGS_DIR / f"carousell-tenant-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Singapore quiet hours: 23:00–08:00 SGT
QUIET_HOURS_START = 23
QUIET_HOURS_END = 8

class PhoneValidator:
    """Validate and normalize Singapore phone numbers."""

    @staticmethod
    def is_valid_sg_phone(phone: str) -> bool:
        """Check if phone is valid Singapore format."""
        if not phone:
            return False

        clean = re.sub(r'[\s\-\(\)\.]+', '', phone)

        if clean.startswith('+65'):
            return len(clean) == 11  # +65 + 8 digits
        elif clean[0] in '89':
            return len(clean) == 8  # Local format: 8 digits

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


class DistrictExtractor:
    """Extract Singapore district from text."""

    DISTRICTS = {
        "central": "Central (D1-D9)",
        "east": "East (D14-D17)",
        "northeast": "North-East (D19-D28)",
        "north-east": "North-East (D19-D28)",
        "west": "West (D5-D7)",
        "south": "South (D2-D5)",
    }

    @staticmethod
    def extract_district(text: str) -> Optional[str]:
        """Extract district from text."""
        if not text:
            return None

        text_lower = text.lower()

        for key, value in DistrictExtractor.DISTRICTS.items():
            if key in text_lower:
                return value

        return None


class CarouselTenantScraper:
    """Scrape Carousell for tenant enquiries."""

    BASE_URL = "https://www.carousell.sg/search/property?category=rooms-rental"
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    def __init__(self):
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        self.session.headers.update({"User-Agent": self.USER_AGENT}) if self.session else None
        self.queued_messages = []
        self.known_phones = set()
        self.load_known_phones()
        self.load_state()

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

    def load_state(self):
        """Load scraper state to avoid re-scraping."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.known_phones.update(state.get('scraped_phones', []))
                logger.info(f"Loaded state with {len(state.get('scraped_phones', []))} scraped phones")
            except Exception as e:
                logger.warning(f"Error loading state: {e}")

    def save_state(self):
        """Save scraper state."""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump({
                    'last_run': datetime.now(timezone.utc).isoformat(),
                    'scraped_phones': list(self.known_phones)
                }, f)
        except Exception as e:
            logger.warning(f"Error saving state: {e}")

    def extract_tenant_data(self, post_text: str) -> Optional[Dict]:
        """Extract tenant data from post text."""
        if not post_text or len(post_text) < 20:
            return None

        # Try to extract phone number
        phone_match = re.search(r'(\+?65[\s\-]?[6-9]\d{3}[\s\-]?\d{4}|[6-9]\d{3}[\s\-]?\d{4})', post_text)
        phone = None
        if phone_match:
            phone = PhoneValidator.normalize_phone(phone_match.group(0))

        # Skip if already known
        if phone and phone in self.known_phones:
            return None

        # Extract budget (look for SGD amounts)
        budget_match = re.search(r'\$?\s*(\d{3,4})\s*(?:\/month|\/mo|\/m|pm|pcm)', post_text, re.IGNORECASE)
        budget = budget_match.group(1) if budget_match else None

        # Extract district
        district = DistrictExtractor.extract_district(post_text)

        # Detect room type
        room_type = None
        if re.search(r'\b1\s*(?:br|bedroom|bed)\b', post_text, re.IGNORECASE):
            room_type = "1BR"
        elif re.search(r'\b2\s*(?:br|bedroom|bed)\b', post_text, re.IGNORECASE):
            room_type = "2BR"
        elif re.search(r'\b3\s*(?:br|bedroom|bed)\b', post_text, re.IGNORECASE):
            room_type = "3BR+"
        elif re.search(r'\b(?:single\s+)?room\b', post_text, re.IGNORECASE):
            room_type = "Room"

        # Only return if we have meaningful data
        if phone or (budget and district):
            return {
                'phone': phone,
                'budget': budget,
                'district': district,
                'room_type': room_type or 'Flexible',
                'raw_text': post_text[:300]  # Keep first 300 chars for reference
            }

        return None

    def scrape_listings(self, query: str) -> List[Dict]:
        """Scrape Carousell for listings matching query."""
        if not self.session or not REQUESTS_AVAILABLE:
            logger.error("requests not available")
            return []

        search_url = f"{self.BASE_URL}&q={quote(query)}"
        logger.info(f"Scraping: {search_url}")

        try:
            resp = self.session.get(search_url, timeout=10)
            resp.raise_for_status()

            if not BEAUTIFULSOUP_AVAILABLE:
                logger.error("BeautifulSoup not available")
                return []

            soup = BeautifulSoup(resp.content, 'html.parser')

            # Find all listing containers (Carousell uses various selectors)
            listings = []
            for item in soup.find_all(class_=re.compile('ListingCard|listing|item', re.I)):
                text = item.get_text(strip=True)
                if text and len(text) > 20:
                    listings.append(text)

            logger.info(f"Found {len(listings)} potential listings for query: {query}")
            return listings

        except Exception as e:
            logger.error(f"Error scraping Carousell: {e}")
            return []

    def queue_message(self, tenant_data: Dict, query: str):
        """Queue a message for manual sending."""
        phone = tenant_data.get('phone') or f"unknown-{hash(tenant_data.get('raw_text', 'default'))}"
        budget = tenant_data.get('budget') or 'flexible'
        district = tenant_data.get('district') or 'flexible'
        room_type = tenant_data.get('room_type', 'Room')

        message_body = f"""Hi there! I saw your post looking for a {room_type} in {district}.

I have {5 if tenant_data.get('phone') else 3}+ rooms available matching your budget (around SGD {budget}/month):

1. Room in {district}
   SGD {budget}/month • Available ASAP

2. Room in nearby area
   SGD {budget}/month • Great location

Want to view? I can arrange viewings within 24 hours.

Winfred Quek | Crestbrick
+65 8161 8149
CEA Reg. No: R073319H

(Found via Carousell - query: {query})"""

        message_obj = {
            'id': f"carousell-{int(time.time())}-{hash(phone) % 10000}",
            'source': 'carousell_tenant',
            'phone': phone,
            'message': message_body,
            'tenant_data': tenant_data,
            'query': query,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'status': 'queued'
        }

        self.queued_messages.append(message_obj)
        logger.info(f"Queued message for {phone} ({district}, SGD {budget}/mo)")

    def run(self):
        """Main scraper run."""
        try:
            config = self.load_config()
            if not config.get('scrapers', {}).get('carousell', {}).get('enabled'):
                logger.info("Carousell tenant scraper disabled in config")
                return

            queries = config.get('scrapers', {}).get('carousell', {}).get('queries', [])
            max_sends = config.get('scrapers', {}).get('carousell', {}).get('max_daily_sends', 60)

            logger.info(f"Starting Carousell tenant scraper with {len(queries)} queries, max {max_sends} sends")

            for query in queries:
                if len(self.queued_messages) >= max_sends:
                    logger.info(f"Reached max daily sends ({max_sends})")
                    break

                listings = self.scrape_listings(query)
                time.sleep(config.get('scrapers', {}).get('carousell', {}).get('rate_limit_sec', 3))

                for listing_text in listings:
                    if len(self.queued_messages) >= max_sends:
                        break

                    tenant_data = self.extract_tenant_data(listing_text)
                    if tenant_data:
                        self.queue_message(tenant_data, query)

            # Save queued messages
            self.save_queue()
            self.save_state()

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
    scraper = CarouselTenantScraper()
    scraper.run()
