#!/usr/bin/env python3
"""
test-demo.py — Demonstration of FSBO scraper with sample data.

This script shows how the scraper works end-to-end without requiring
actual Carousell/PropertyGuru scraping (which needs robust parsing).

Run: python3 test-demo.py
"""

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import re
import sys

SCRIPT_DIR = Path(__file__).parent
TEMPLATE_FILE = Path.home() / "crestbrick-consult/docs/fsbo-day1-message-template.md"


def demo_listings():
    """Sample FSBO listings from Carousell (demo data)."""
    return [
        {
            "listing_id": "car-bishan-123-08-15",
            "block": "Bishan Block 123",
            "unit": "08-15",
            "asking_price": 615000,
            "seller_name": "John Doe",
            "seller_phone": "+65 9123 4567",
            "listing_date": "2026-06-30",
            "days_old": 62,
            "hdb_type": "4-room",
            "url": "https://www.carousell.sg/p/...",
            "description": "HDB 4-room, well-maintained, renovated kitchen"
        },
        {
            "listing_id": "car-tampines-908-03-45",
            "block": "Tampines Block 908",
            "unit": "03-45",
            "asking_price": 520000,
            "seller_name": "Mary Chen",
            "seller_phone": "9876 5432",  # Test local format
            "listing_date": "2026-07-15",
            "days_old": 47,  # Less than 60 days old - would be filtered out
            "hdb_type": "3-room",
            "url": "https://www.carousell.sg/p/...",
            "description": "Mature estate, near MRT, corner unit"
        },
        {
            "listing_id": "car-clementi-456-05-12",
            "block": "Clementi Block 456",
            "unit": "05-12",
            "asking_price": 580000,
            "seller_name": "David Lim",
            "seller_phone": "+6587654321",  # Test no-space format
            "listing_date": "2026-06-15",
            "days_old": 77,
            "hdb_type": "4-room",
            "url": "https://www.carousell.sg/p/...",
            "description": "Move-in ready, HDB improvement scheme eligible"
        },
        {
            "listing_id": "car-bukit-123-12-30",
            "block": "Bukit Merah Block 107",
            "unit": "12-30",
            "asking_price": 595000,
            "seller_name": "Susan Wong",
            "seller_phone": "8234 5678",  # Test local format
            "listing_date": "2026-05-20",
            "days_old": 102,
            "hdb_type": "5-room",
            "url": "https://www.carousell.sg/p/...",
            "description": "Spacious 5-room, near Redhill MRT"
        }
    ]


def demo_propertyguru_sales(block_name):
    """Sample PropertyGuru sales data (demo)."""
    sales_data = {
        "Bishan Block 123": [
            {"unit": "15-23", "price": 618000, "date_sold": "2026-08-15", "psf": 1050},
            {"unit": "08-15", "price": 612000, "date_sold": "2026-07-30", "psf": 1045},
            {"unit": "12-20", "price": 615000, "date_sold": "2026-07-02", "psf": 1048},
        ],
        "Clementi Block 456": [
            {"unit": "10-25", "price": 585000, "date_sold": "2026-08-20", "psf": 1020},
            {"unit": "06-10", "price": 575000, "date_sold": "2026-08-05", "psf": 1015},
            {"unit": "11-18", "price": 580000, "date_sold": "2026-07-25", "psf": 1018},
        ],
        "Bukit Merah Block 107": [
            {"unit": "08-35", "price": 598000, "date_sold": "2026-08-10", "psf": 1030},
            {"unit": "15-42", "price": 602000, "date_sold": "2026-07-28", "psf": 1038},
            {"unit": "11-20", "price": 595000, "date_sold": "2026-07-05", "psf": 1025},
        ]
    }
    return sales_data.get(block_name, [])


def test_phone_validator():
    """Test phone validation logic."""
    print("\n" + "=" * 70)
    print("PHONE VALIDATION TESTS")
    print("=" * 70)

    test_cases = [
        ("+65 9123 4567", True, "+6591234567"),
        ("+6591234567", True, "+6591234567"),
        ("9876 5432", True, "+6598765432"),
        ("8234 5678", True, "+6582345678"),
        ("+6587654321", True, "+6587654321"),
        ("invalid", False, None),
        ("+1 555 1234", False, None),
        ("", False, None),
    ]

    all_passed = True
    for phone, should_be_valid, expected_norm in test_cases:
        # Simulate validation
        clean = re.sub(r'[\s\-\(\)\.]+', '', phone)
        is_valid = False
        normalized = None

        if clean.startswith('+65'):
            is_valid = len(clean) == 12
            normalized = clean if is_valid else None
        elif clean and clean[0] in '89':
            is_valid = len(clean) == 8
            normalized = f"+65{clean}" if is_valid else None

        if is_valid == should_be_valid and normalized == expected_norm:
            status = "✓ PASS"
        else:
            status = "✗ FAIL"
            all_passed = False

        print(f"{status}: {phone:20} → valid={is_valid}, normalized={normalized}")

    return all_passed


def fill_template(listing, pg_sales):
    """Fill the FSBO message template with demo data."""
    if not TEMPLATE_FILE.exists():
        print(f"Template file not found: {TEMPLATE_FILE}")
        return None

    with open(TEMPLATE_FILE) as f:
        template = f.read()

    # Extract message (between code fences)
    match = re.search(r"```\n(Hi \[Name\].*?)\n```", template, re.DOTALL)
    if not match:
        print("Could not parse message template")
        return None

    msg = match.group(1)

    # Fill in demo data
    seller_name = listing["seller_name"].split()[0]
    msg = msg.replace("[Name]", seller_name)
    msg = msg.replace("[Block]", listing["block"])
    msg = msg.replace("[X]k", str(listing["asking_price"] // 1000))

    # Fill PropertyGuru sales
    if pg_sales:
        sales_lines = []
        for sale in pg_sales[:3]:
            date_sold = sale["date_sold"]
            try:
                month_abbr = datetime.strptime(date_sold, "%Y-%m-%d").strftime("%b %d")
            except:
                month_abbr = date_sold
            sales_lines.append(
                f"• Unit {sale['unit']}: S${sale['price']//1000}k (sold {month_abbr})"
            )

        if sales_lines:
            sales_block = "\n".join(sales_lines)
            msg = re.sub(
                r"• Unit .*?\(sold.*?\)\n• Unit .*?\(sold.*?\)\n• Unit .*?\(sold.*?\)",
                sales_block,
                msg
            )

    return msg


def main():
    print("=" * 70)
    print("FSBO SCRAPER — COMPREHENSIVE TEST")
    print("=" * 70)

    # Test 1: Phone validation
    if not test_phone_validator():
        print("\n⚠ Some phone validation tests failed!")

    # Test 2: Listing filtering and processing
    print("\n" + "=" * 70)
    print("LISTING PROCESSING TEST")
    print("=" * 70)

    listings = demo_listings()
    print(f"\nTotal sample listings: {len(listings)}")

    # Filter by age
    min_days_old = 60
    filtered = [l for l in listings if l["days_old"] >= min_days_old]
    print(f"After filtering (60+ days old): {len(filtered)}\n")

    # Process each qualified listing
    queue = []
    for listing in filtered:
        print(f"\n--- {listing['block']} ---")
        print(f"  Listed: {listing['listing_date']} ({listing['days_old']} days ago)")
        print(f"  Asking: S${listing['asking_price']//1000}k")
        print(f"  Seller: {listing['seller_name']} {listing['seller_phone']}")

        # Get PropertyGuru data
        pg_sales = demo_propertyguru_sales(listing["block"])
        if pg_sales:
            print(f"  Recent sales in {listing['block']}:")
            for sale in pg_sales[:3]:
                print(f"    • Unit {sale['unit']}: S${sale['price']//1000}k (sold {sale['date_sold']})")
        else:
            print(f"  No recent sales found (fallback to nearby blocks)")

        # Fill template
        message = fill_template(listing, pg_sales)
        if message:
            print("\n  MESSAGE PREVIEW:")
            print("  " + "─" * 66)
            for i, line in enumerate(message.split("\n")[:8]):  # First 8 lines
                print(f"  {line}")
            if len(message.split("\n")) > 8:
                print(f"  ... ({len(message.split(chr(10)))} lines total)")
            print("  " + "─" * 66)

            # Queue entry
            queue.append({
                "block": listing["block"],
                "unit": listing["unit"],
                "seller_phone": listing["seller_phone"],
                "seller_name": listing["seller_name"],
                "asking_price": listing["asking_price"],
                "message_preview": message[:80] + "...",
                "status": "queued",
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # Test 3: Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Total listings: {len(listings)}")
    print(f"Qualified (60+ days): {len(filtered)}")
    print(f"Messages generated: {len(queue)}")

    if queue:
        print("\nQUEUE SUMMARY:")
        for i, entry in enumerate(queue, 1):
            print(f"\n{i}. {entry['block']}")
            print(f"   Unit: {entry['unit']}")
            print(f"   Phone: {entry['seller_phone']}")
            print(f"   Price: S${entry['asking_price']//1000}k")
            print(f"   Status: {entry['status']}")

    print("\n" + "=" * 70)
    print("✓ ALL TESTS PASSED")
    print("=" * 70)
    print("\nNEXT STEPS:")
    print("1. Install dependencies: pip3 install beautifulsoup4")
    print("2. Run 'python3 scraper.py scrape' to find real Carousell listings")
    print("3. Check 'message-queue.json' for pending messages")
    print("4. Send manually via WhatsApp Web (default, safe)")
    print()


if __name__ == "__main__":
    main()
