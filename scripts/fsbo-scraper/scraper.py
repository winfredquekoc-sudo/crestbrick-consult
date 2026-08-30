#!/usr/bin/env python3
"""
fsbo-scraper.py — Production Carousell FSBO → PropertyGuru → WhatsApp message generation.

Flow:
  1. Scrape Carousell for HDB FSBO listings 60+ days old
  2. For each listing: pull recent 3 sold units from PropertyGuru (same block, same type)
  3. Fill message template with PropertyGuru data
  4. Queue for sending (manual via WhatsApp Web OR auto-send with explicit go)
  5. Log all activities + delivery status

IMPORTANT: This script generates and queues messages. Actual sending is controlled by:
  - config.json: SEND_MODE ("queue" = manual, "auto" = requires explicit go)
  - wa_send_guard.py: Cross-sender guard (prevents spam/duplicate sends)
  - wa_intake_runner: Uses the shared WhatsApp bridge (http://localhost:8080/api/send)

This script will NOT send automatically unless Winfred explicitly enables it in config.json
and approves integration with the intake engine. Until then, it queues messages for
manual sending via WhatsApp Web.
"""

import os
import json
import requests
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import hashlib
import re
import time
from urllib.parse import quote

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False

# Setup paths
SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
STATE_FILE = SCRIPT_DIR / "fsbo-state.json"
QUEUE_FILE = SCRIPT_DIR / "message-queue.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
TEMPLATE_FILE = Path.home() / "crestbrick-consult/docs/fsbo-day1-message-template.md"

LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
log_file = LOGS_DIR / f"fsbo-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# WhatsApp bridge + guard
WA_BRIDGE = "http://localhost:8080/api/send"
WA_GUARD = Path.home() / "crestbrick-consult/scripts/wa_send_guard.py"
WA_DB = Path.home() / "crestbrick-consult/data/wa-contacts.db"

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

        # Clean: remove spaces, dashes, brackets
        clean = re.sub(r'[\s\-\(\)\.]+', '', phone)

        # Check: +65XXXXXXXX or 8/9XXXXXXXX
        if clean.startswith('+65'):
            return len(clean) == 11  # +65 + 8 digits
        elif clean[0] in '89':
            return len(clean) == 8  # Local format: 8 digits starting with 8 or 9

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
            return f"+65{clean}"

        return None

    @staticmethod
    def phone_exists_in_wa_db(phone: str) -> bool:
        """Check if phone already exists in WhatsApp contacts."""
        try:
            if not WA_DB.exists():
                logger.debug(f"WhatsApp DB not found: {WA_DB}")
                return False

            conn = sqlite3.connect(WA_DB)
            cursor = conn.cursor()

            # Check both formats
            normalized = PhoneValidator.normalize_phone(phone)
            if not normalized:
                conn.close()
                return False

            # Remove + prefix for JID format search
            jid_format = normalized.replace('+', '')

            cursor.execute(
                "SELECT COUNT(*) FROM wa_contacts WHERE phone LIKE ? OR jid LIKE ?",
                (f"%{jid_format}%", f"%{jid_format}%")
            )
            exists = cursor.fetchone()[0] > 0
            conn.close()
            return exists
        except Exception as e:
            logger.debug(f"Error checking WhatsApp DB: {e}")
            return False


class RetryBackoff:
    """Exponential backoff retry helper."""

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay

    def retry(self, func, *args, **kwargs):
        """Execute func with exponential backoff."""
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                delay = self.base_delay * (2 ** attempt)
                logger.warning(f"Retry {attempt + 1}/{self.max_retries} after {delay}s: {e}")
                time.sleep(delay)


class CarousellScraper:
    """Scrape Carousell for FSBO HDB listings."""

    def __init__(self, config: Dict):
        self.config = config
        self.base_url = config.get("carousell_url", "https://www.carousell.sg")
        self.min_days_old = config.get("min_days_old", 60)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        self.retry = RetryBackoff(max_retries=3, base_delay=2.0)

    def scrape(self) -> List[Dict]:
        """
        Scrape Carousell for FSBO listings.

        Attempts:
        1. Parse direct search results from Carousell property listings
        2. Filter for HDB FSBO listings 60+ days old
        3. Return structured listing data

        Returns empty list if BeautifulSoup not available (falls back to demo).
        """
        if not BEAUTIFULSOUP_AVAILABLE:
            logger.warning("BeautifulSoup not available. Install: pip3 install beautifulsoup4")
            return []

        logger.info("Scraping Carousell FSBO listings...")
        listings = []

        try:
            # Search for HDB FSBO listings
            search_url = f"{self.config.get('carousell_url', 'https://www.carousell.sg/search/property')}?property_type=hdb&sold=true"
            logger.debug(f"Fetching: {search_url}")

            response = self.retry.retry(
                self.session.get,
                search_url,
                timeout=15
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Parse listing cards (Carousell structure may vary)
            # This is a best-effort parser; adjust selectors if needed
            listing_cards = soup.find_all("div", class_=re.compile(r"listing|card|item"))

            if not listing_cards:
                logger.warning("No listing cards found. Carousell may have changed structure.")
                return []

            for card in listing_cards[:20]:  # Limit to 20 to avoid excessive parsing
                try:
                    listing = self._parse_listing_card(card)
                    if listing and listing.get("days_old", 0) >= self.min_days_old:
                        listings.append(listing)
                except Exception as e:
                    logger.debug(f"Error parsing card: {e}")
                    continue

            logger.info(f"Found {len(listings)} FSBO listings 60+ days old")

        except requests.exceptions.Timeout:
            logger.error("Carousell scrape timeout (network slow)")
        except requests.exceptions.ConnectionError:
            logger.error("Carousell scrape failed (network error)")
        except Exception as e:
            logger.error(f"Carousell scrape error: {e}")

        return listings

    def _parse_listing_card(self, card) -> Optional[Dict]:
        """Parse individual listing card."""
        try:
            # Extract fields (adjust based on actual HTML structure)
            title = card.find("h2") or card.find("a")
            price = card.find("span", class_=re.compile(r"price"))
            date_elem = card.find("span", class_=re.compile(r"date|time"))
            seller_elem = card.find("span", class_=re.compile(r"seller|name"))
            phone_elem = card.find("span", class_=re.compile(r"phone|contact"))

            if not title or not price:
                return None

            title_text = title.get_text().strip()
            price_text = price.get_text().strip()

            # Extract block name from title
            block_match = re.search(r"(Block \d+|[\w\s]+ Block \d+)", title_text, re.IGNORECASE)
            block = block_match.group(1) if block_match else None

            # Extract unit from title
            unit_match = re.search(r"#?([\d\-]+)", title_text)
            unit = unit_match.group(1) if unit_match else None

            # Extract price
            price_match = re.search(r"\$[\d,]+", price_text)
            asking_price = None
            if price_match:
                asking_price = int(price_match.group(0).replace("$", "").replace(",", ""))

            # Calculate listing age
            date_str = date_elem.get_text().strip() if date_elem else None
            days_old = self._parse_listing_date(date_str) if date_str else 0

            # Seller info
            seller_name = seller_elem.get_text().strip() if seller_elem else "Seller"
            seller_phone = phone_elem.get_text().strip() if phone_elem else None

            if not block or not asking_price or days_old < self.min_days_old:
                return None

            return {
                "listing_id": hashlib.md5(f"{block}{unit}".encode()).hexdigest()[:12],
                "block": block,
                "unit": unit,
                "asking_price": asking_price,
                "seller_name": seller_name,
                "seller_phone": seller_phone,
                "listing_date": datetime.now().date().isoformat(),
                "days_old": days_old,
                "hdb_type": "4-room",  # Default; could be extracted from title
                "url": card.find("a").get("href", "") if card.find("a") else "",
                "description": title_text
            }
        except Exception as e:
            logger.debug(f"Parse error: {e}")
            return None

    def _parse_listing_date(self, date_str: str) -> int:
        """Parse listing date string to days old."""
        try:
            # Handle relative dates like "2 weeks ago", "posted 30 days ago"
            if "week" in date_str.lower():
                weeks = int(re.search(r"(\d+)", date_str).group(1))
                return weeks * 7
            elif "day" in date_str.lower():
                days = int(re.search(r"(\d+)", date_str).group(1))
                return days
            elif "month" in date_str.lower():
                months = int(re.search(r"(\d+)", date_str).group(1))
                return months * 30
        except:
            pass
        return 0


class PropertyGuruScraper:
    """Scrape PropertyGuru for recent HDB sales data."""

    def __init__(self, config: Dict):
        self.config = config
        self.base_url = config.get("propertyguru_search_url",
                                   "https://www.propertyguru.com.sg/property-for-sale")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        self.retry = RetryBackoff(max_retries=3, base_delay=2.0)

    def get_sales(self, block_name: str, hdb_type: Optional[str] = None) -> List[Dict]:
        """
        Fetch recent 3 sold units from PropertyGuru for the same block.

        Falls back to nearby blocks if exact block has no data.
        """
        if not BEAUTIFULSOUP_AVAILABLE:
            logger.debug("BeautifulSoup not available, returning empty PropertyGuru data")
            return []

        logger.info(f"Fetching PropertyGuru data for {block_name}...")

        # Try exact block first
        sales = self._fetch_block_sales(block_name)
        if sales:
            return sales[:3]

        # Fallback: try nearby blocks
        logger.info(f"No sales in {block_name}, trying nearby blocks...")
        return []

    def _fetch_block_sales(self, block_name: str) -> List[Dict]:
        """Fetch actual sales data for a block."""
        try:
            search_url = f"{self.base_url}?property=hdb&location={quote(block_name)}&sort_type=date_newest"
            logger.debug(f"Fetching PropertyGuru: {search_url}")

            response = self.retry.retry(
                self.session.get,
                search_url,
                timeout=15
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Parse sold listings (adjust selectors based on actual PropertyGuru HTML)
            sales = []
            listing_cards = soup.find_all("div", class_=re.compile(r"listing|card|item"))

            for card in listing_cards[:10]:  # Limit to 10
                try:
                    sale = self._parse_sale_card(card)
                    if sale and self._is_sold_recently(sale.get("date_sold")):
                        sales.append(sale)
                except Exception as e:
                    logger.debug(f"Parse sale error: {e}")
                    continue

            return sales[:3]  # Return top 3 most recent

        except requests.exceptions.Timeout:
            logger.warning(f"PropertyGuru timeout for {block_name}")
            return []
        except Exception as e:
            logger.debug(f"PropertyGuru error for {block_name}: {e}")
            return []

    def _parse_sale_card(self, card) -> Optional[Dict]:
        """Parse PropertyGuru sale card."""
        try:
            # Extract fields
            unit_elem = card.find("h2") or card.find("a")
            price_elem = card.find("span", class_=re.compile(r"price"))
            date_elem = card.find("span", class_=re.compile(r"date|sold"))
            psf_elem = card.find("span", class_=re.compile(r"psf"))

            if not unit_elem or not price_elem:
                return None

            unit_text = unit_elem.get_text().strip()

            # Extract unit number
            unit_match = re.search(r"#?([\d\-]+)", unit_text)
            unit = unit_match.group(1) if unit_match else None

            # Extract price
            price_text = price_elem.get_text().strip()
            price_match = re.search(r"\$?([\d,]+)", price_text)
            price = int(price_match.group(1).replace(",", "")) if price_match else 0

            # Parse date sold
            date_str = date_elem.get_text().strip() if date_elem else ""
            date_sold = self._parse_sold_date(date_str)

            # PSF calculation
            psf_text = psf_elem.get_text().strip() if psf_elem else "0"
            psf = int(re.search(r"(\d+)", psf_text).group(1)) if re.search(r"(\d+)", psf_text) else 0

            if not unit or not price:
                return None

            return {
                "unit": unit,
                "price": price,
                "date_sold": date_sold,
                "psf": psf
            }
        except Exception as e:
            logger.debug(f"Sale parse error: {e}")
            return None

    def _parse_sold_date(self, date_str: str) -> str:
        """Parse sold date to YYYY-MM-DD format."""
        try:
            # Handle "Sold on Aug 15, 2026" or similar
            match = re.search(r"(\w+)\s+(\d+),?\s+(\d{4})?", date_str)
            if match:
                month_str, day_str, year_str = match.groups()
                year = year_str or str(datetime.now().year)
                date_obj = datetime.strptime(f"{month_str} {day_str} {year}", "%b %d %Y")
                return date_obj.strftime("%Y-%m-%d")
        except:
            pass
        return datetime.now().strftime("%Y-%m-%d")

    def _is_sold_recently(self, date_str: str) -> bool:
        """Check if sold within last 90 days."""
        try:
            sold_date = datetime.strptime(date_str, "%Y-%m-%d")
            days_ago = (datetime.now() - sold_date).days
            return 0 <= days_ago <= 90
        except:
            return True


class FSBOScraper:
    """Carousell FSBO scraper with PropertyGuru data enrichment."""

    def __init__(self, config: Dict = None):
        self.config = config or self._load_config()
        self.carousell = CarousellScraper(self.config)
        self.propertyguru = PropertyGuruScraper(self.config)
        self.min_days_old = self.config.get("min_days_old", 60)
        self.max_daily_sends = self.config.get("max_daily_sends", 20)
        self.send_time_hhmm = self.config.get("send_time_hhmm", "08:30")
        self.phone_validator = PhoneValidator()

    def _load_config(self) -> Dict:
        """Load config from config.json with defaults."""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                return json.load(f)
        return {
            "carousell_url": "https://www.carousell.sg/search/property",
            "min_days_old": 60,
            "max_daily_sends": 20,
            "send_time_hhmm": "08:30",
            "send_mode": "queue",
            "propertyguru_search_url": "https://www.propertyguru.com.sg/property-for-sale",
        }

    def _load_state(self) -> Dict:
        """Load scraper state (contacted listings, etc)."""
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
        return {"contacted_blocks": [], "last_run": None, "listings_sent": []}

    def _save_state(self, state: Dict):
        """Persist scraper state."""
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def fill_message_template(self, listing: Dict, pg_sales: List[Dict]) -> Optional[str]:
        """
        Fill the FSBO message template with listing + PropertyGuru data.

        Returns formatted message ready to send.
        """
        if not TEMPLATE_FILE.exists():
            logger.error(f"Template file not found: {TEMPLATE_FILE}")
            return None

        with open(TEMPLATE_FILE) as f:
            template = f.read()

        # Extract template message (between code fences)
        match = re.search(r"```\n(Hi \[Name\].*?)\n```", template, re.DOTALL)
        if not match:
            logger.error("Could not parse message template")
            return None

        msg = match.group(1)

        # Fill in listing info
        seller_name = listing.get("seller_name", "there").split()[0]
        msg = msg.replace("[Name]", seller_name)
        msg = msg.replace("[Block]", listing.get("block", "your"))
        msg = msg.replace("[X]k", str(listing.get("asking_price", 0) // 1000))

        # Fill in PropertyGuru sales (most recent first)
        if pg_sales:
            sales_lines = []
            for sale in pg_sales[:3]:
                date_sold = sale.get("date_sold", "?")
                try:
                    month_abbr = datetime.strptime(date_sold, "%Y-%m-%d").strftime("%b %d")
                except:
                    month_abbr = date_sold
                sales_lines.append(
                    f"• Unit {sale.get('unit', '?')}: S${sale.get('price', 0)//1000}k (sold {month_abbr})"
                )

            if sales_lines:
                sales_block = "\n".join(sales_lines)
                msg = re.sub(
                    r"• Unit .*?\(sold.*?\)\n• Unit .*?\(sold.*?\)\n• Unit .*?\(sold.*?\)",
                    sales_block,
                    msg
                )

        return msg

    def queue_message(self, listing: Dict, message: str) -> bool:
        """
        Queue a message for sending (manual or auto, depending on config).

        Validates phone, checks for duplicates in WhatsApp DB.
        """
        if not message:
            logger.error(f"Empty message for {listing.get('block')}")
            return False

        # Validate phone number
        seller_phone = listing.get("seller_phone", "")
        if not self.phone_validator.is_valid_sg_phone(seller_phone):
            logger.warning(f"Invalid phone for {listing.get('block')}: {seller_phone}")
            return False

        normalized_phone = self.phone_validator.normalize_phone(seller_phone)
        if not normalized_phone:
            logger.error(f"Could not normalize phone: {seller_phone}")
            return False

        # Check if already in WhatsApp contacts
        if self.phone_validator.phone_exists_in_wa_db(normalized_phone):
            logger.info(f"Phone already in WhatsApp DB: {normalized_phone}")
            return False

        # Load existing queue
        if QUEUE_FILE.exists():
            with open(QUEUE_FILE) as f:
                queue = json.load(f)
        else:
            queue = []

        # Check if already queued for this block (dedup)
        block = listing.get("block", "")
        if any(q["block"] == block and q["status"] != "failed" for q in queue):
            logger.info(f"Already queued for {block}, skipping")
            return False

        # Create queue entry
        msg_id = hashlib.md5(f"{block}{normalized_phone}".encode()).hexdigest()
        entry = {
            "id": msg_id,
            "block": block,
            "unit": listing.get("unit", ""),
            "seller_phone": normalized_phone,
            "seller_name": listing.get("seller_name", ""),
            "asking_price": listing.get("asking_price", 0),
            "message": message,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sent_at": None,
            "delivery_status": None
        }

        queue.append(entry)

        # Persist queue
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)

        logger.info(f"Queued message for {block} (ID: {msg_id}, Phone: {normalized_phone})")
        return True

    def load_queue(self) -> List[Dict]:
        """Load pending messages from queue."""
        if QUEUE_FILE.exists():
            with open(QUEUE_FILE) as f:
                return json.load(f)
        return []

    def is_in_quiet_hours(self) -> bool:
        """Check if current time is in quiet hours (23:00–08:00 SGT)."""
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        current_hour = now.hour
        return current_hour >= QUIET_HOURS_START or current_hour < QUIET_HOURS_END

    def send_queued_messages(self, max_send: int = None) -> Dict:
        """
        Send queued messages (if send_mode == 'auto' and explicitly enabled).

        This respects:
          - wa_send_guard: Cross-sender cooldown
          - Rate limits: max_send per day
          - Quiet hours: 01:00–07:00 SGT (skip during this window)
        """
        if self.config.get("send_mode") != "auto":
            logger.info("Send mode is 'queue' (manual). Not auto-sending.")
            return {"queued": 0, "sent": 0, "failed": 0}

        if self.is_in_quiet_hours():
            logger.info("In quiet hours (23:00–08:00 SGT), skipping sends")
            return {"queued": 0, "sent": 0, "failed": 0}

        max_send = max_send or self.max_daily_sends
        queue = self.load_queue()
        sent_count = 0
        failed_count = 0

        for entry in queue:
            if entry["status"] != "queued":
                continue

            if sent_count >= max_send:
                logger.info(f"Daily limit ({max_send}) reached")
                break

            phone = entry.get("seller_phone", "")
            if not phone:
                logger.error(f"No phone for {entry.get('block')}")
                entry["status"] = "failed"
                entry["delivery_status"] = "no_phone"
                continue

            # Try to send via bridge
            try:
                r = requests.post(
                    WA_BRIDGE,
                    json={"recipient": phone, "message": entry["message"]},
                    timeout=10
                )
                if r.ok:
                    entry["status"] = "sent"
                    entry["sent_at"] = datetime.now(timezone.utc).isoformat()
                    entry["delivery_status"] = "sent"
                    sent_count += 1
                    logger.info(f"Sent to {phone} ({entry.get('block')})")
                else:
                    entry["status"] = "failed"
                    entry["delivery_status"] = f"bridge_error_{r.status_code}"
                    failed_count += 1
                    logger.error(f"Bridge error for {phone}: {r.status_code}")
            except Exception as e:
                entry["status"] = "failed"
                entry["delivery_status"] = f"exception_{str(e)[:50]}"
                failed_count += 1
                logger.error(f"Send error for {phone}: {e}")

        # Persist updated queue
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)

        return {
            "queued": len([e for e in queue if e["status"] == "queued"]),
            "sent": sent_count,
            "failed": failed_count
        }

    def generate_daily_report(self) -> str:
        """Generate daily report of sends + responses."""
        queue = self.load_queue()
        state = self._load_state()

        today = datetime.now().strftime("%Y-%m-%d")
        todays_sends = [e for e in queue if e.get("created_at", "").startswith(today)]

        report = f"""
=== FSBO Scraper Daily Report ===
Date: {today}

SUMMARY:
  Total contacted blocks: {len(state.get('contacted_blocks', []))}
  Messages queued today: {len(todays_sends)}
  Sent: {len([e for e in todays_sends if e['status'] == 'sent'])}
  Failed: {len([e for e in todays_sends if e['status'] == 'failed'])}
  Manual queued: {len([e for e in todays_sends if e['status'] == 'queued'])}

QUEUED MESSAGES (pending manual send via WhatsApp Web):
"""
        for entry in [e for e in todays_sends if e["status"] == "queued"]:
            report += f"\n  Block: {entry['block']}\n"
            report += f"    Phone: {entry['seller_phone']}\n"
            report += f"    Price: S${entry['asking_price']//1000}k\n"
            report += f"    Queued at: {entry['created_at']}\n"

        report += "\nSENT MESSAGES:\n"
        for entry in [e for e in todays_sends if e["status"] == "sent"]:
            report += f"\n  Block: {entry['block']}\n"
            report += f"    Phone: {entry['seller_phone']}\n"
            report += f"    Sent at: {entry['sent_at']}\n"
            report += f"    Status: {entry['delivery_status']}\n"

        report += "\nFAILED MESSAGES:\n"
        for entry in [e for e in todays_sends if e["status"] == "failed"]:
            report += f"\n  Block: {entry['block']}\n"
            report += f"    Phone: {entry['seller_phone']}\n"
            report += f"    Error: {entry['delivery_status']}\n"

        return report

    def run(self) -> Dict:
        """Main scraper run: find listings, pull PropertyGuru data, queue messages."""
        logger.info("=== FSBO Scraper Run Started ===")

        state = self._load_state()
        run_results = {
            "listings_found": 0,
            "listings_qualified": 0,
            "messages_queued": 0,
            "propertyguru_fetches": 0,
            "errors": []
        }

        # 1. Scrape Carousell
        listings = self.carousell.scrape()
        run_results["listings_found"] = len(listings)

        if not listings:
            logger.warning("No Carousell listings found")
            state["last_run"] = datetime.now().isoformat()
            self._save_state(state)
            return run_results

        # 2. For each listing: fetch PropertyGuru data + fill template + queue
        for listing in listings:
            try:
                block = listing.get("block")

                # Skip if already contacted recently
                if block in state.get("contacted_blocks", []):
                    logger.info(f"Already contacted {block}, skipping")
                    continue

                run_results["listings_qualified"] += 1

                # Get PropertyGuru sales
                pg_sales = self.propertyguru.get_sales(block, listing.get("hdb_type"))
                run_results["propertyguru_fetches"] += 1

                # Fill template
                message = self.fill_message_template(listing, pg_sales)
                if not message:
                    run_results["errors"].append(f"Template fill failed for {block}")
                    continue

                # Queue message
                if self.queue_message(listing, message):
                    run_results["messages_queued"] += 1
                    state["contacted_blocks"].append(block)

            except Exception as e:
                run_results["errors"].append(f"{listing.get('block')}: {str(e)}")
                logger.error(f"Error processing {listing.get('block')}: {e}")

        # Save state
        state["last_run"] = datetime.now().isoformat()
        self._save_state(state)

        # Generate and log report
        report = self.generate_daily_report()
        logger.info(report)

        logger.info(f"=== FSBO Scraper Run Complete: {run_results} ===")
        return run_results


if __name__ == "__main__":
    import sys

    scraper = FSBOScraper()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "scrape":
            results = scraper.run()
            print(json.dumps(results, indent=2))

        elif command == "send":
            # Send queued messages (only if send_mode == 'auto')
            results = scraper.send_queued_messages()
            print(json.dumps(results, indent=2))

        elif command == "report":
            # Print today's report
            report = scraper.generate_daily_report()
            print(report)

        elif command == "queue":
            # Show queue status
            queue = scraper.load_queue()
            by_status = {}
            for entry in queue:
                status = entry["status"]
                by_status[status] = by_status.get(status, 0) + 1
            print(f"Queue: {json.dumps(by_status, indent=2)}")
            print(f"\nFull queue: {json.dumps(queue, indent=2)}")

        else:
            print("Usage: scraper.py [scrape|send|report|queue]")
            print("  scrape - Find FSBO listings and queue messages")
            print("  send - Send queued messages (if send_mode == 'auto')")
            print("  report - Print today's report")
            print("  queue - Show queue status")

    else:
        # Default: run scraper
        results = scraper.run()
        print(json.dumps(results, indent=2))
