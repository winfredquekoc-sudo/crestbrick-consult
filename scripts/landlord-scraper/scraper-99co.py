#!/usr/bin/env python3
"""
scraper-99co.py — 99.co landlord rental scraper for prospecting.

Scrapes individual landlord rental listings (not agency listings).
"""

import os
import json
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
QUEUE_FILE = SCRIPT_DIR / "message-queue-landlord.json"
LANDLORD_DB = Path.home() / "crestbrick-consult/scripts/databases/landlord-db.json"

LOGS_DIR.mkdir(exist_ok=True)

log_file = LOGS_DIR / f"99co-{datetime.now().strftime('%Y-%m-%d')}.log"
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
    @staticmethod
    def is_valid_sg_phone(phone: str) -> bool:
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
        if not phone:
            return None
        clean = re.sub(r'[\s\-\(\)\.]+', '', phone)
        if clean.startswith('+65'):
            return clean if len(clean) == 11 else None
        elif clean[0] in '89' and len(clean) == 8:
            return '+65' + clean
        return None

class CoScraper:
    """Scrape 99.co for individual landlord rental listings."""

    NINCO_RENTAL_URL = "https://www.99.co/singapore/search/rent"
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    def __init__(self):
        self.session = None
        self.known_landlords = self._load_known_landlords()

    def _load_known_landlords(self) -> set:
        if not LANDLORD_DB.exists():
            return set()
        try:
            db = json.load(open(LANDLORD_DB))
            phones = set()
            for record in db.values():
                if isinstance(record, dict):
                    phone = record.get("phone")
                    if phone:
                        phones.add(str(phone))
            return phones
        except Exception:
            return set()

    def _init_session(self):
        if not REQUESTS_AVAILABLE:
            return False
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        return True

    def scrape(self) -> List[Dict]:
        if not BEAUTIFULSOUP_AVAILABLE or not REQUESTS_AVAILABLE:
            logger.error("BeautifulSoup or requests not installed")
            return []

        if not self._init_session():
            return []

        logger.info(f"Scraping 99.co rental listings")
        listings = []

        try:
            resp = self.session.get(self.NINCO_RENTAL_URL, timeout=10)
            if resp.status_code != 200:
                logger.error(f"HTTP {resp.status_code}")
                return listings

            soup = BeautifulSoup(resp.content, "html.parser")

            # 99.co listing cards
            cards = soup.find_all("div", class_=re.compile(r"listing|card|property", re.I))
            logger.info(f"Found {len(cards)} listing cards")

            for i, card in enumerate(cards[:50]):
                try:
                    listing = self._parse_card(card)
                    if listing and listing.get("phone"):
                        listings.append(listing)
                except Exception as e:
                    logger.debug(f"Parse error on card {i}: {e}")

        except Exception as e:
            logger.error(f"Scrape error: {e}")

        return listings

    def _parse_card(self, card) -> Optional[Dict]:
        try:
            title_elem = card.find("h2") or card.find("a", class_=re.compile(r"title", re.I))
            title = title_elem.get_text(strip=True) if title_elem else None

            price_elem = card.find("span", class_=re.compile(r"price|rent", re.I))
            rent_str = price_elem.get_text(strip=True) if price_elem else None

            rent = None
            if rent_str:
                m = re.search(r"[\$\s]*(\d+(?:,\d{3})*)", rent_str)
                if m:
                    rent = int(m.group(1).replace(",", ""))

            location_elem = card.find("span", class_=re.compile(r"location|district", re.I))
            location = location_elem.get_text(strip=True) if location_elem else None

            link_elem = card.find("a", href=re.compile(r"/property/", re.I))
            listing_url = link_elem.get("href") if link_elem else None
            if listing_url and not listing_url.startswith("http"):
                listing_url = "https://www.99.co" + listing_url

            seller_elem = card.find("div", class_=re.compile(r"seller|agent|author|contact", re.I))
            name = None
            phone = None

            if seller_elem:
                name_elem = seller_elem.find("span", class_=re.compile(r"name", re.I))
                name = name_elem.get_text(strip=True) if name_elem else None

                phone_text = seller_elem.get_text(strip=True)
                phone_match = re.search(r"(\d[\d\s\-\(\)]{6,})", phone_text)
                if phone_match:
                    phone = phone_match.group(1).strip()

            if not phone:
                all_text = card.get_text(strip=True)
                phone_match = re.search(r"(?:contact|call|whatsapp)[:\s]+(\d[\d\s\-\(\)]{6,})", all_text, re.I)
                if phone_match:
                    phone = phone_match.group(1).strip()

            if not phone or not PhoneValidator.is_valid_sg_phone(phone):
                return None

            phone = PhoneValidator.normalize_phone(phone)

            if phone in self.known_landlords:
                logger.debug(f"Landlord {phone} already known, skipping")
                return None

            if not title or not rent:
                return None

            return {
                "title": title,
                "rent_sgd": rent,
                "location": location or "Singapore",
                "name": name or "Landlord",
                "phone": phone,
                "listing_url": listing_url or "",
                "source": "99co",
                "scraped_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.debug(f"Card parse error: {e}")
            return None

    def queue_messages(self, listings: List[Dict]) -> int:
        if not listings:
            return 0

        queue = []
        try:
            if QUEUE_FILE.exists():
                queue = json.load(open(QUEUE_FILE))
        except Exception:
            queue = []

        queued_count = 0
        for listing in listings:
            try:
                msg = (
                    f"Hi {listing['name']}, I saw your rental on 99.co for SGD {listing['rent_sgd']}/month in {listing['location']}.\n\n"
                    f"I specialize in matching qualified tenants and co-list on PropertyGuru and my network. "
                    f"Faster fill, better vetting, no upfront fees.\n\n"
                    f"Keen to discuss?\n\n"
                    f"Winfred Quek | Crestbrick\n"
                    f"81618149\n"
                    f"CEA Reg. No: R073319H"
                )
                queue.append({
                    "pn": listing["phone"],
                    "name": listing["name"],
                    "message": msg,
                    "listing": listing,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                    "status": "pending"
                })
                queued_count += 1
                logger.info(f"Queued message for {listing['phone']}")
            except Exception as e:
                logger.error(f"Failed to queue for {listing['phone']}: {e}")

        try:
            with open(QUEUE_FILE, "w") as f:
                json.dump(queue, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {queued_count} 99.co messages to queue")
        except Exception as e:
            logger.error(f"Failed to save queue: {e}")

        return queued_count

def main():
    logger.info("=== 99.co Landlord Scraper ===")
    scraper = CoScraper()
    listings = scraper.scrape()
    if listings:
        queued = scraper.queue_messages(listings)
        logger.info(f"Queued {queued} messages from 99.co")
    else:
        logger.info("No new 99.co listings found")

if __name__ == "__main__":
    main()
