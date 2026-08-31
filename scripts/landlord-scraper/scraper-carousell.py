#!/usr/bin/env python3
"""
scraper-carousell.py — Carousell landlord rental scraper for prospecting.

Flow:
  1. Scrape Carousell for rental room listings in Singapore
  2. Extract: landlord name, phone, listing title, rent, district, URL
  3. Dedupe: Check phone against landlord-db.json (skip if already known)
  4. Queue: Store message + metadata in message-queue-landlord.json
  5. Send timing: Daily 08:00 SGT (quiet hours compliant, manual send only)

This script QUEUES messages only. Actual sending is controlled by:
  - Single-sender doctrine: only intake_engine.py sends (via WhatsApp bridge)
  - Manual review: all messages reviewed before sending via WhatsApp Web
  - CEA compliance: no auto-send without explicit approval

IMPORTANT: This is outbound prospecting. All sends MUST go through the intake_engine
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
STATE_FILE = SCRIPT_DIR / "scraper-carousell-state.json"
QUEUE_FILE = SCRIPT_DIR / "message-queue-landlord.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
LANDLORD_DB = Path.home() / "crestbrick-consult/scripts/databases/landlord-db.json"

LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
log_file = LOGS_DIR / f"carousell-{datetime.now().strftime('%Y-%m-%d')}.log"
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

class CarousellScraper:
    """Scrape Carousell for rental listings."""

    # Carousell rooms rental page (Singapore)
    CAROUSELL_RENTAL_URL = "https://www.carousell.sg/search/property?category=rooms-rental"
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config = self._load_config(config_path)
        self.session = None
        self.known_landlords = self._load_known_landlords()

    def _load_config(self, path: Path) -> dict:
        """Load config.json."""
        if path.exists():
            try:
                return json.load(open(path))
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")
        return {
            "scrape_enabled": True,
            "send_mode": "queue",  # 'queue' = manual, 'auto' = requires explicit approval
            "max_daily_sends": 50,
            "rate_limit_sec": 2
        }

    def _load_known_landlords(self) -> set:
        """Load existing landlord phone numbers from landlord-db.json to dedupe."""
        if not LANDLORD_DB.exists():
            return set()
        try:
            db = json.load(open(LANDLORD_DB))
            # Extract all phone numbers from the landlord database
            phones = set()
            for record in db.values():
                if isinstance(record, dict):
                    phone = record.get("phone")
                    if phone:
                        phones.add(str(phone))
            return phones
        except Exception as e:
            logger.warning(f"Failed to load landlord DB: {e}")
            return set()

    def _init_session(self):
        """Initialize requests session."""
        if not REQUESTS_AVAILABLE:
            return False
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        return True

    def scrape(self) -> List[Dict]:
        """Scrape Carousell rental listings. Returns list of listing dicts."""
        if not BEAUTIFULSOUP_AVAILABLE or not REQUESTS_AVAILABLE:
            logger.error("BeautifulSoup or requests not installed")
            return []

        if not self._init_session():
            logger.error("Failed to initialize session")
            return []

        logger.info(f"Scraping Carousell rental listings from {self.CAROUSELL_RENTAL_URL}")
        listings = []

        try:
            resp = self.session.get(self.CAROUSELL_RENTAL_URL, timeout=10)
            if resp.status_code != 200:
                logger.error(f"HTTP {resp.status_code}: {self.CAROUSELL_RENTAL_URL}")
                return listings

            soup = BeautifulSoup(resp.content, "html.parser")

            # Carousell listing cards are in divs with class containing 'itemCard'
            # This is a best-effort extraction; Carousell's DOM changes frequently
            cards = soup.find_all("div", class_=re.compile(r"itemCard|listing-item", re.I))
            logger.info(f"Found {len(cards)} listing cards")

            for i, card in enumerate(cards[:50]):  # Limit to first 50
                try:
                    listing = self._parse_card(card)
                    if listing and listing.get("phone"):
                        listings.append(listing)
                except Exception as e:
                    logger.debug(f"Error parsing card {i}: {e}")

        except Exception as e:
            logger.error(f"Scrape error: {e}")

        return listings

    def _parse_card(self, card) -> Optional[Dict]:
        """Parse a single listing card."""
        try:
            # Title
            title_elem = card.find("h2") or card.find("a", class_=re.compile(r"title", re.I))
            title = title_elem.get_text(strip=True) if title_elem else None

            # Price (rent per month)
            price_elem = card.find("span", class_=re.compile(r"price", re.I))
            rent_str = price_elem.get_text(strip=True) if price_elem else None

            # Extract rent amount from "$1,500" format
            rent = None
            if rent_str:
                m = re.search(r"[\$\s]*(\d+(?:,\d{3})*)", rent_str)
                if m:
                    rent = int(m.group(1).replace(",", ""))

            # Location/district
            location_elem = card.find("span", class_=re.compile(r"location|district", re.I))
            location = location_elem.get_text(strip=True) if location_elem else None

            # Listing URL
            link_elem = card.find("a", href=re.compile(r"/p/", re.I))
            listing_url = link_elem.get("href") if link_elem else None
            if listing_url and not listing_url.startswith("http"):
                listing_url = "https://www.carousell.sg" + listing_url

            # Landlord name and phone (often in seller info section)
            # This is tricky on Carousell as seller info is in a modal. Best effort:
            seller_elem = card.find("div", class_=re.compile(r"seller|author", re.I))
            name = None
            phone = None
            if seller_elem:
                name_elem = seller_elem.find("span", class_=re.compile(r"name", re.I))
                name = name_elem.get_text(strip=True) if name_elem else None

                # Phone extraction (often displayed on card or in modal, but not always)
                phone_text = seller_elem.get_text(strip=True)
                phone_match = re.search(r"(\d[\d\s\-\(\)]{6,})", phone_text)
                if phone_match:
                    phone = phone_match.group(1).strip()

            if not phone:
                # Fallback: try to extract from title or description (some sellers include it)
                all_text = card.get_text(strip=True)
                phone_match = re.search(r"(?:contact|call|whatsapp|phone)[:\s]+(\d[\d\s\-\(\)]{6,})", all_text, re.I)
                if phone_match:
                    phone = phone_match.group(1).strip()

            # Validate phone
            if not phone or not PhoneValidator.is_valid_sg_phone(phone):
                logger.debug(f"Invalid/missing phone for '{title}'")
                return None

            phone = PhoneValidator.normalize_phone(phone)

            # Check if landlord already known
            if phone in self.known_landlords:
                logger.debug(f"Landlord {phone} already in database, skipping")
                return None

            if not title or not rent:
                logger.debug(f"Missing title or rent for {phone}")
                return None

            return {
                "title": title,
                "rent_sgd": rent,
                "location": location or "Singapore",
                "name": name or "Landlord",
                "phone": phone,
                "listing_url": listing_url or "",
                "source": "carousell",
                "scraped_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.debug(f"Card parse error: {e}")
            return None

    def queue_messages(self, listings: List[Dict]) -> int:
        """Queue WhatsApp messages for these listings. Returns count queued."""
        if not listings:
            return 0

        queue = []
        try:
            if QUEUE_FILE.exists():
                queue = json.load(open(QUEUE_FILE))
        except Exception as e:
            logger.warning(f"Failed to load queue: {e}")
            queue = []

        queued_count = 0
        for listing in listings:
            try:
                msg = self._build_message(listing)
                queue.append({
                    "pn": listing["phone"],
                    "name": listing["name"],
                    "message": msg,
                    "listing": listing,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                    "status": "pending"
                })
                queued_count += 1
                logger.info(f"Queued message for {listing['phone']} ({listing['name']})")
            except Exception as e:
                logger.error(f"Failed to queue message for {listing['phone']}: {e}")

        # Save queue
        try:
            with open(QUEUE_FILE, "w") as f:
                json.dump(queue, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {queued_count} messages to {QUEUE_FILE}")
        except Exception as e:
            logger.error(f"Failed to save queue: {e}")

        return queued_count

    def _build_message(self, listing: Dict) -> str:
        """Build WhatsApp message for a landlord listing."""
        return (
            f"Hi {listing['name']}, I saw your room on Carousell for SGD {listing['rent_sgd']}/month in {listing['location']}.\n\n"
            f"I match tenants with room rentals and I have an active pool of qualified tenants looking in your area right now. "
            f"Plus, I co-list on PropertyGuru and 99.co to expand your reach.\n\n"
            f"No upfront fee, just better, faster tenants. Interested?\n\n"
            f"Winfred Quek | Crestbrick\n"
            f"81618149\n"
            f"CEA Reg. No: R073319H"
        )

def main():
    """Run the scraper."""
    logger.info("=== Carousell Landlord Scraper ===")

    scraper = CarousellScraper()

    if not scraper.config.get("scrape_enabled"):
        logger.info("Scraper disabled in config")
        return

    # Scrape
    listings = scraper.scrape()
    if not listings:
        logger.info("No new listings found")
        return

    logger.info(f"Found {len(listings)} new landlord listings")

    # Queue messages
    queued = scraper.queue_messages(listings)
    logger.info(f"Successfully queued {queued} messages for manual sending")

if __name__ == "__main__":
    main()
