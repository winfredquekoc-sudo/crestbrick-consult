"""Dedupe helpers for FSBO leads -- checked against every phone/listing source
Winfred already has before anything gets queued.

landlord-db.json is READ ONLY here: it's a dict with a "landlords" key
holding the record array (plus ~17 sibling metadata keys) -- never iterate
db.values() directly, that walks metadata strings/counts, not records, and
never write back to this file.
"""

import csv
import json
import logging
import re
from pathlib import Path
from typing import Dict, Set, Tuple

logger = logging.getLogger(__name__)

# Do-not-engage phone suppression list -- maintained outside this repo (state
# directory, not version control) since it holds real contact PII.
DO_NOT_ENGAGE_FILE = Path.home() / ".claude/state/do-not-engage.json"


def load_do_not_engage(path: Path = DO_NOT_ENGAGE_FILE) -> Set[str]:
    """JSON array of E.164 phone strings maintained outside the repo. Missing or
    unreadable file -> empty set + a warning, never a crash (an empty suppression
    list is dangerous but must not block a run)."""
    try:
        with open(path) as f:
            entries = json.load(f)
        return {e.strip() for e in entries if isinstance(e, str) and e.strip()}
    except Exception as e:
        logger.warning(f"Failed to load do-not-engage suppression list from {path}: {e} -- running with an EMPTY do-not-engage set")
        return set()


DO_NOT_ENGAGE = load_do_not_engage()


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone or "")
    if digits.startswith("+65"):
        return digits
    if digits.startswith("65") and len(digits) == 10:
        return f"+{digits}"
    if len(digits) == 8 and digits[0] in "89":
        return f"+65{digits}"
    return digits


def load_seller_db_phones(path: Path, logger) -> Set[str]:
    phones = set()
    if not path.exists():
        return phones
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                p = row.get("phone", "")
                if p:
                    phones.add(normalize_phone(p))
    except Exception as e:
        logger.warning(f"Failed to read seller-database.csv: {e}")
    return phones


def load_landlord_db_phones(path: Path, logger) -> Set[str]:
    phones = set()
    if not path.exists():
        return phones
    try:
        db = json.load(open(path))
        for row in db.get("landlords", []):
            p = row.get("phone", "")
            if p:
                phones.add(normalize_phone(p))
    except Exception as e:
        logger.warning(f"Failed to read landlord-db.json: {e}")
    return phones


class DedupeIndex:
    def __init__(self, seen_listings: Dict, listings_sent: list, seller_phones: Set[str], landlord_phones: Set[str]):
        self.seen_ids = set(seen_listings.keys()) | {str(x) for x in (listings_sent or [])}
        self.known_phones = seller_phones | landlord_phones | DO_NOT_ENGAGE

    def already_seen(self, listing_id: str) -> bool:
        return str(listing_id) in self.seen_ids

    def check(self, listing_id: str, phone: str = None) -> Tuple[bool, str]:
        """Returns (is_duplicate, reason). Call after already_seen() pre-fetch skip,
        this is the post-classification check that also covers phone matches."""
        if str(listing_id) in self.seen_ids:
            return True, "listing_id already in seen_listings"
        if phone:
            norm = normalize_phone(phone)
            if norm in DO_NOT_ENGAGE:
                return True, "do-not-engage number"
            if norm in self.known_phones:
                return True, "phone already known (seller-database.csv or landlord-db.json)"
        return False, ""
