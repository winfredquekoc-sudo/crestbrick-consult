#!/usr/bin/env python3
"""Loader for the landlord seed map, which lives outside the repo.

The map pairs a landlord's WhatsApp identifier with their name and listing.
Both are personal data, so the file is kept in the local state directory and
never committed. scripts/landlord-seed.example.json documents the schema.
"""
import json
import os

SEED_PATH = os.path.expanduser("~/.claude/state/wa-agent/landlord-seed.json")


def load_landlords(path=SEED_PATH):
    """Return the {jid: {name, listing_id}} seed map.

    Raises rather than returning {} on a missing or malformed file: silently
    seeding zero landlords would let the nightly refresh publish an empty
    database over a good one.
    """
    if not os.path.exists(path):
        raise SystemExit(
            f"landlord seed map not found at {path}\n"
            "It holds landlord names and WhatsApp identifiers, so it is not in\n"
            "the repo. Copy scripts/landlord-seed.example.json there and fill it\n"
            "in, or restore it from your backup."
        )
    with open(path, encoding="utf-8") as f:
        seed = json.load(f)
    if not isinstance(seed, dict) or not seed:
        raise SystemExit(f"landlord seed map at {path} is empty or not a JSON object")
    for jid, info in seed.items():
        if not isinstance(info, dict) or "name" not in info or "listing_id" not in info:
            raise SystemExit(f"landlord seed entry {jid} needs both name and listing_id")
    return seed
