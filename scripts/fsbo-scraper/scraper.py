#!/usr/bin/env python3
"""
scraper.py — Carousell FSBO seller-lead scraper: fetch, classify, dedupe, queue.

Flow:
  1. Search a handful of FSBO queries on Carousell (curl_cffi, impersonate=chrome
     -- plain requests+BeautifulSoup gets a Cloudflare 403 every time and never
     finds a listing; see carousell_client.py).
  2. For each NEW listing (not already in fsbo-state.json): fetch the detail page,
     extract title/price/description/seller/phone/posted-age.
  3. Classify owner vs agent (classify.py): hard regex signals first, Ollama as a
     best-effort fallback for anything ambiguous, else UNSURE.
  4. Dedupe against fsbo-state.json, docs/seller-database.csv, the read-only
     landlord DB, and the do-not-engage list (dedupe.py).
  5. Append qualified leads (OWNER + flagged UNSURE) to message-queue.json with a
     drafted message filled from docs/fsbo-day1-message-template.md.

This script only ever QUEUES messages for manual review/sending via WhatsApp Web
or Carousell chat -- it never sends anything itself. Per the single-sender
doctrine, the WA intake engine is the only thing that auto-sends on Winfred's
number; adding a send path here would violate that.
"""

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from carousell_client import CarousellClient, compute_listing_age, extract_address_hint, extract_phone
from classify import classify_listing
from dedupe import DedupeIndex, load_landlord_db_phones, load_seller_db_phones

# Setup paths
SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
STATE_FILE = SCRIPT_DIR / "fsbo-state.json"
QUEUE_FILE = SCRIPT_DIR / "message-queue.json"
QUARANTINE_FILE = SCRIPT_DIR / "message-queue-quarantine.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
TEMPLATE_FILE = Path.home() / "crestbrick-consult/docs/fsbo-day1-message-template.md"
SELLER_DB = Path.home() / "crestbrick-consult/docs/seller-database.csv"
LANDLORD_DB = Path.home() / "crestbrick-consult/_templates/landlord-db.json"

LOGS_DIR.mkdir(exist_ok=True)

log_file = LOGS_DIR / f"fsbo-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

DEFAULT_QUERIES = [
    "hdb for sale by owner",
    "direct owner hdb",
    "no agent hdb for sale",
    "condo for sale owner",
]

# Telegram send -- matches the existing pattern in gsc-weekly-digest.py /
# ~/.claude/bin/wa-loose-ends.sh: plain text POST, no parse_mode. An unescaped
# Markdown send has silently dropped alerts before.
ENV_FILE = os.path.expanduser("~/.telegram-bot.env")


def load_env_file():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


def send_telegram(text):
    env = {**load_env_file(), **os.environ}
    tok = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_WINFRED_CHAT_ID", "540127870")
    if not tok:
        return False, "no TELEGRAM_BOT_TOKEN (checked ~/.telegram-bot.env and environment)"
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            urllib.parse.urlencode({"chat_id": chat, "text": text}).encode(),
            timeout=15,
        )
        return True, None
    except OSError as exc:
        return False, str(exc)


# Comps block is fabricated-example text in the template ("• Unit 15-23: S$618k
# ...") meant to be replaced with real PropertyGuru data. This build has no
# PropertyGuru integration, so it's dropped entirely rather than shipped as if
# real -- same for the dependent close-price prediction and the "4 units...
# S$615-618k" line, both specific invented figures.
COMPS_BLOCK_RE = re.compile(r"Just checked PropertyGuru[^\n]*:\n(?:•[^\n]*\n){3}\n?")
CLOSE_PREDICTION_RE = re.compile(r"\s*Should close at S\$[\d,\-]+k?,\s*\d+-\d+\s*days\.")
RECENT_CLOSES_RE = re.compile(r"✓ Recent closes: \d+ units? in your block, S\$[\d,\-]+k?, \d+-\d+ days\n")

# Target is people selling their property, not renting it out -- but Carousell's
# text search for e.g. "no agent hdb for sale" also surfaces room/whole-unit
# rental posts using identical "no agent"/"direct owner" phrasing (observed
# live: "Common Room for Rent $900", a whole-unit rental priced "S$3,700").
# Catch both the keyword tells and the price magnitude (every real HDB/condo
# sale price is comfortably above this floor; every rental is comfortably
# below it).
RENTAL_SIGNAL_RE = re.compile(
    r"\b(?:for rent|to let|tenancy|monthly rent|common room|master room|room for rent|per month)\b"
    r"|\$\s*\d[\d,]*\s*/\s*(?:mo|month)\b",
    re.IGNORECASE,
)

# Narrower than RENTAL_SIGNAL_RE on purpose -- that regex's "tenancy" would
# false-positive on legit sale titles like "selling with tenancy"; titles get
# this stricter list instead, screened before a candidate consumes a fetch slot.
# "rental" excludes a trailing "yield" so investor-pitch sale titles ("High
# rental yield! Selling...") aren't mistaken for a rental listing.
TITLE_RENTAL_RE = re.compile(
    r"\b(?:for rent|rental(?!\s*yield)|roommate|room avail\w*|co-?living|per month|monthly)\b"
    r"|/\s*(?:mo|month)\b",
    re.IGNORECASE,
)
MIN_SALE_PRICE = 150_000
SELLER_NAME_FALLBACK = "Seller"  # _process_one's seller_name default when both first/last name are blank


class FSBOScraper:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or self._load_config()
        self.min_days_old = self.config.get("min_days_old", 60)
        self.client = CarousellClient(
            search_base_url=self.config.get("carousell_url", "https://www.carousell.sg/search"),
            logger=logger,
            delay_range=tuple(self.config.get("fetch_delay_range_sec", [2.0, 4.0])),
        )

    def _load_config(self) -> Dict:
        defaults = {
            "carousell_url": "https://www.carousell.sg/search",
            "min_days_old": 60,
            "fsbo_search_queries": DEFAULT_QUERIES,
            "max_listing_fetches_per_run": 30,
            "fetch_delay_range_sec": [2.0, 4.0],
            "send_mode": "queue",
        }
        if CONFIG_FILE.exists():
            try:
                defaults.update(json.load(open(CONFIG_FILE)))
            except Exception as e:
                logger.warning(f"Failed to load config, using defaults: {e}")
        return defaults

    def _load_state(self) -> Dict:
        if STATE_FILE.exists():
            try:
                return json.load(open(STATE_FILE))
            except Exception as e:
                logger.warning(f"Failed to load state, starting fresh: {e}")
        return {"contacted_blocks": [], "last_run": None, "listings_sent": [], "seen_listings": {}, "watchlist": {}}

    def _save_state(self, state: Dict):
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def _load_queue(self) -> List[Dict]:
        if QUEUE_FILE.exists():
            try:
                return json.load(open(QUEUE_FILE))
            except Exception as e:
                logger.warning(f"Failed to load queue, starting fresh: {e}")
        return []

    def _save_queue(self, queue: List[Dict]):
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)

    def _load_quarantine(self) -> List[Dict]:
        if QUARANTINE_FILE.exists():
            try:
                return json.load(open(QUARANTINE_FILE))
            except Exception as e:
                logger.warning(f"Failed to load quarantine, starting fresh: {e}")
        return []

    def _save_quarantine(self, quarantine: List[Dict]):
        with open(QUARANTINE_FILE, "w") as f:
            json.dump(quarantine, f, indent=2, ensure_ascii=False)

    def _format_price(self, detail: Dict):
        currency = detail.get("currency_symbol") or "S$"
        formatted = detail.get("price_formatted") or ""
        try:
            raw = float(detail.get("price") or 0)
        except (TypeError, ValueError):
            raw = 0.0
        price_k = int(raw // 1000)
        if formatted:
            display = f"{currency}{formatted}"
        elif raw:
            display = f"{currency}{int(raw):,}"
        else:
            display = "price not listed"
        return display, price_k

    def fill_message_template(self, seller_name: str, address_hint: Optional[str], price_k: int) -> Optional[str]:
        if not TEMPLATE_FILE.exists():
            logger.error(f"Template file not found: {TEMPLATE_FILE}")
            return None
        template = TEMPLATE_FILE.read_text()
        match = re.search(r"```\n(Hi \[Name\].*?)\n```", template, re.DOTALL)
        if not match:
            logger.error("Could not parse message template")
            return None

        msg = match.group(1)
        first = (seller_name.split() or [""])[0]
        NON_NAMES = {"happy", "rental", "property", "sg", "hdb", "home", "direct", "owner", "seller", "sale"}
        name_ok = first.isalpha() and 2 <= len(first) <= 20 and first.lower() not in NON_NAMES
        msg = msg.replace("[Name]", first.title() if name_ok else "there")
        msg = msg.replace("Your [Block] unit", f"Your {address_hint} unit" if address_hint else "Your unit")
        msg = msg.replace("[X]k", f"{price_k}k")

        msg = COMPS_BLOCK_RE.sub("", msg)
        msg = CLOSE_PREDICTION_RE.sub("", msg)
        msg = re.sub(r"You're positioned at S\$\S+\.?\n?", "", msg)
        msg = RECENT_CLOSES_RE.sub("✓ Track record of fast closes in similar blocks nearby\n", msg)
        return msg.strip() + "\n"

    def _process_one(self, listing_id: str, dedupe: DedupeIndex, results: Dict) -> Optional[Dict]:
        detail = self.client.fetch_detail(listing_id)
        if not detail:
            results["fetch_failed"] += 1
            return None  # transient -- not marked seen, retried next run

        title = detail.get("title", "")
        description = detail.get("description") or detail.get("flattened_description") or ""
        seller = detail.get("seller", {})
        username = seller.get("username", "")
        seller_name = f"{seller.get('first_name', '')} {seller.get('last_name', '')}".strip() or username or SELLER_NAME_FALLBACK

        try:
            price_raw = float(detail.get("price") or 0)
        except (TypeError, ValueError):
            price_raw = 0.0
        if RENTAL_SIGNAL_RE.search(f"{title} {description}") or 0 < price_raw < MIN_SALE_PRICE:
            logger.info(f"{listing_id} looks like a rental, not a sale, dropping")
            return {"status": "not_a_sale"}

        days_old, date_unknown = compute_listing_age(detail)
        if not date_unknown and days_old < self.min_days_old:
            logger.info(f"{listing_id} too recent ({days_old}d < {self.min_days_old}d), skipping")
            return {"status": "too_recent", "days_old": days_old}

        classification, evidence = classify_listing(f"{title} {description}", username, seller_name, logger)
        phone = extract_phone(description, seller_name, username)

        if classification == "AGENT":
            logger.info(f"{listing_id} classified AGENT ({evidence}), dropping")
            return {"status": "agent", "classification": classification, "phone": phone}

        is_dup, reason = dedupe.check(listing_id, phone)
        if is_dup:
            logger.info(f"{listing_id} dropped as duplicate: {reason}")
            return {"status": "duplicate", "classification": classification, "phone": phone}

        price_display, price_k = self._format_price(detail)
        address_hint = extract_address_hint(title, description)
        message = self.fill_message_template(seller_name, address_hint, price_k)

        entry = {
            "listing_id": listing_id,
            "url": f"https://www.carousell.sg/p/{listing_id}/",
            "title": title,
            "price": price_display,
            "address_hint": address_hint,
            "phone": phone,
            "seller_name": seller_name,
            "classification": classification,
            "evidence": evidence,
            "drafted_message": message,
            "needs_review": classification == "UNSURE",
            "needs_carousell_dm": phone is None,
            "days_old": days_old,
            "date_unknown": date_unknown,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"{listing_id} classified {classification} ({evidence}), queued")
        return {"status": "queued", "classification": classification, "phone": phone, "entry": entry}

    def _watchlist_touch(self, watchlist: Dict, listing_id: str, days_old: int):
        """Record/refresh a too-recent listing's next recheck date. Preserves any
        existing failed-fetch attempt count -- this only fires on a successful
        fetch that's still too young, never on a fetch failure."""
        attempts = watchlist.get(listing_id, {}).get("attempts", 0)
        recheck_on = datetime.now(timezone.utc).date() + timedelta(days=self.min_days_old - days_old + 1)
        watchlist[listing_id] = {
            "age_at_check": days_old,
            "recheck_on": recheck_on.isoformat(),
            "attempts": attempts,
        }

    def build_digest(self, r: Dict) -> str:
        c = r["classification_counts"]
        lines = [
            "FSBO Scraper Run",
            f"Searched {len(r['queries'])} queries, {r['search_hits_total']} hits, {r['unique_candidates']} new listings",
            f"Fetched: {r['fetched']} (failed: {r['fetch_failed']})",
            f"Classified: OWNER {c['OWNER']} / AGENT {c['AGENT']} / UNSURE {c['UNSURE']}",
            f"Dropped: too recent {r['dropped_too_recent']}, not a sale {r['dropped_not_sale']}, duplicate {r['dropped_duplicate']}, pre-skipped rental {r['pre_skipped_rental']}",
            f"Queued: {r['queued']}",
        ]
        if r["watchlist_waiting"] + r["watchlist_checked"] > 0:
            lines.append(f"Watchlist: waiting {r['watchlist_waiting']}, checked {r['watchlist_checked']}")
        if r["quarantined_stale"] > 0:
            lines.append(f"Quarantined stale AGENT entries: {r['quarantined_stale']}")
        if r["queued_leads"]:
            lines.append("")
            lines.append("New leads:")
            for e in r["queued_leads"]:
                phone_note = e["phone"] or "no phone (DM via Carousell)"
                lines.append(f"- [{e['classification']}] {e['title'][:60]} | {e['price']} | {phone_note} | {e['url']}")
        if r["errors"]:
            lines.append("")
            lines.append(f"Errors: {len(r['errors'])}")
        return "\n".join(lines)

    def run(self) -> Dict:
        logger.info("=== FSBO Scraper Run Started ===")
        state = self._load_state()
        seen_listings = state.setdefault("seen_listings", {})
        listings_sent = state.get("listings_sent", [])
        watchlist = state.setdefault("watchlist", {})

        seller_phones = load_seller_db_phones(SELLER_DB, logger)
        landlord_phones = load_landlord_db_phones(LANDLORD_DB, logger)
        dedupe = DedupeIndex(seen_listings, listings_sent, seller_phones, landlord_phones)

        queries = self.config.get("fsbo_search_queries", DEFAULT_QUERIES)
        max_fetches = self.config.get("max_listing_fetches_per_run", 30)

        results = {
            "queries": queries,
            "search_hits_total": 0,
            "unique_candidates": 0,
            "fetched": 0,
            "classification_counts": {"OWNER": 0, "AGENT": 0, "UNSURE": 0},
            "dropped_too_recent": 0,
            "dropped_not_sale": 0,
            "dropped_duplicate": 0,
            "pre_skipped_rental": 0,
            "quarantined_stale": 0,
            "fetch_failed": 0,
            "queued": 0,
            "queued_leads": [],
            "errors": [],
        }

        # Watchlist entries due for a recheck (maturing past min_days_old) get first
        # claim on this run's fetch budget, capped at half of it so a big backlog
        # can't starve fresh candidates -- it just spreads across more nights.
        today_iso = datetime.now(timezone.utc).date().isoformat()
        due_ids = [lid for lid, w in watchlist.items() if w.get("recheck_on", "9999-99-99") <= today_iso]
        due_ids = due_ids[: max_fetches // 2]
        due_id_set = set(due_ids)

        # insertion-ordered dedupe (not a set()) so fetch budget follows query
        # order + search rank, not Python's hash-randomized set iteration order
        candidates = {}  # id -> title, first occurrence wins
        for q in queries:
            try:
                hits = self.client.search(q)
            except Exception as e:
                results["errors"].append(f"search '{q}': {e}")
                logger.error(f"Search failed for '{q}': {e}")
                continue
            results["search_hits_total"] += len(hits)
            for h in hits:
                candidates.setdefault(h["id"], h.get("title", ""))

        not_seen_ids = [lid for lid in candidates if lid not in watchlist and not dedupe.already_seen(lid)]
        results["unique_candidates"] = len(not_seen_ids)

        # Pre-fetch title screen: skip obvious rentals before they consume a fetch
        # slot. Stateless on purpose -- a missing/empty title never pre-skips (no
        # info to go on), and a skip here doesn't touch seen_listings, so a regex
        # misread just gets re-evaluated next run instead of losing the listing.
        new_ids = []
        for lid in not_seen_ids:
            title = candidates[lid]
            if title and TITLE_RENTAL_RE.search(title):
                results["pre_skipped_rental"] += 1
                logger.info(f"{lid} title looks like a rental, pre-skipping (not fetched): {title[:60]!r}")
                continue
            new_ids.append(lid)
        new_ids = new_ids[: max(max_fetches - len(due_ids), 0)]

        fetch_ids = due_ids + new_ids
        queue = self._load_queue()

        for listing_id in fetch_ids:
            try:
                outcome = self._process_one(listing_id, dedupe, results)
            except Exception as e:
                results["errors"].append(f"{listing_id}: {e}")
                logger.error(f"Unhandled error processing {listing_id}: {e}")
                continue

            if outcome is None:
                if listing_id in due_id_set:
                    watched = watchlist.get(listing_id)
                    if watched is not None:
                        watched["attempts"] = watched.get("attempts", 0) + 1
                        if watched["attempts"] >= 3:
                            watchlist.pop(listing_id, None)
                            logger.info(f"{listing_id} dropped from watchlist after 3 failed fetch attempts (likely sold/removed)")
                continue

            results["fetched"] += 1
            if outcome["status"] == "too_recent":
                results["dropped_too_recent"] += 1
                self._watchlist_touch(watchlist, listing_id, outcome["days_old"])
                continue

            if outcome["status"] == "not_a_sale":
                results["dropped_not_sale"] += 1
                seen_listings[listing_id] = {
                    "first_seen": datetime.now(timezone.utc).date().isoformat(),
                    "classification": "NOT_A_SALE",
                    "phone": None,
                }
                watchlist.pop(listing_id, None)
                continue

            cls = outcome["classification"]
            results["classification_counts"][cls] += 1
            seen_listings[listing_id] = {
                "first_seen": datetime.now(timezone.utc).date().isoformat(),
                "classification": cls,
                "phone": outcome.get("phone"),
            }
            watchlist.pop(listing_id, None)

            if outcome["status"] == "duplicate":
                results["dropped_duplicate"] += 1
            elif outcome["status"] == "queued":
                queue.append(outcome["entry"])
                results["queued"] += 1
                results["queued_leads"].append(outcome["entry"])

        # one seller pushing multiple sale posts this run = dealer, not an owner --
        # but the seller_name fallback itself must never drive a downgrade, or two
        # unrelated sellers who both hit the fallback get merged into one "dealer"
        by_seller = {}
        for e in results["queued_leads"]:
            seller = e.get("seller_name", "")
            if seller and seller != SELLER_NAME_FALLBACK:
                by_seller.setdefault(seller, []).append(e)
        for seller, entries in by_seller.items():
            if len(entries) >= 2:
                for e in entries:
                    if e.get("classification") == "OWNER":
                        e["classification"] = "UNSURE"
                        e["evidence"] += f" | downgraded: {len(entries)} sale posts by same seller this run"
                        e["needs_review"] = True

        # Queue hygiene: an AGENT-classified entry should never have been queued,
        # but old data (or a future bug) might still have one sitting in the queue --
        # quarantine rather than hard-delete so nothing is silently lost.
        keep_queue, newly_quarantined = [], []
        for e in queue:
            if e.get("classification") == "AGENT":
                e["quarantined_at"] = datetime.now(timezone.utc).isoformat()
                e["reason"] = "agent_classification"
                newly_quarantined.append(e)
            else:
                keep_queue.append(e)
        queue = keep_queue
        if newly_quarantined:
            quarantine = self._load_quarantine()
            quarantine.extend(newly_quarantined)
            self._save_quarantine(quarantine)
            results["quarantined_stale"] = len(newly_quarantined)

        results["watchlist_waiting"] = len(watchlist)
        results["watchlist_checked"] = len(due_ids)

        self._save_queue(queue)
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)

        digest = self.build_digest(results)
        logger.info(digest)
        ok, err = send_telegram(digest)
        if not ok:
            logger.warning(f"Telegram digest not sent: {err}")

        logger.info(f"=== FSBO Scraper Run Complete: fetched={results['fetched']} queued={results['queued']} ===")
        return results


if __name__ == "__main__":
    import sys

    scraper = FSBOScraper()
    command = sys.argv[1] if len(sys.argv) > 1 else "scrape"

    if command == "scrape":
        run_results = scraper.run()
        print(json.dumps({k: v for k, v in run_results.items() if k != "queued_leads"}, indent=2))

    elif command == "queue":
        loaded_queue = scraper._load_queue()
        by_status: Dict[str, int] = {}
        for entry in loaded_queue:
            key = entry.get("classification", "unknown")
            by_status[key] = by_status.get(key, 0) + 1
        print(f"Queue ({len(loaded_queue)} total): {json.dumps(by_status, indent=2)}")

    elif command == "send":
        print("This scraper never auto-sends. Messages are queued in message-queue.json for manual review.")

    else:
        print("Usage: scraper.py [scrape|queue|send]")
        print("  scrape - Find FSBO listings, classify, dedupe, and queue leads")
        print("  queue  - Show queue status")
        print("  send   - No-op (this scraper never auto-sends)")
