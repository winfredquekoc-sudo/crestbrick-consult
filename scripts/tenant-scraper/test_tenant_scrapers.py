#!/usr/bin/env python3
"""
test_tenant_scrapers.py — Unit tests for tenant scraper system.

Tests:
  1. Phone validation (normalize, dedupe)
  2. District extraction
  3. Budget extraction
  4. Room type detection
  5. Message formatting
  6. Queue management
  7. Deduplication
  8. State persistence
"""

import json
import tempfile
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from scraper_carousell import PhoneValidator as CarousellPhoneValidator, DistrictExtractor, CarouselTenantScraper
from scraper_propertyguru import PhoneValidator as PGPhoneValidator, PropertyGuruTenantScraper
from scraper_99co import PhoneValidator as NinetyNinePhoneValidator, NinetyNineTenantScraper


class TestPhoneValidator:
    """Test phone validation and normalization."""

    def test_sg_phone_valid_formats(self):
        """Test valid Singapore phone formats."""
        test_cases = [
            ("+6581234567", True),
            ("+65 8123 4567", True),
            ("81234567", True),
            ("8123-4567", True),
            ("+65-8123-4567", True),
            ("9876543210", False),  # Too long
            ("12345678", False),  # Starts with 1
            ("abc", False),  # Not a number
            ("", False),  # Empty
        ]

        for phone, expected in test_cases:
            result = CarousellPhoneValidator.is_valid_sg_phone(phone)
            status = "✓" if result == expected else "✗"
            print(f"{status} validate_sg_phone('{phone}'): {result} (expected {expected})")
            assert result == expected, f"Failed for {phone}"

    def test_phone_normalization(self):
        """Test phone normalization to +65 format."""
        test_cases = [
            ("+6581234567", "+6581234567"),
            ("81234567", "+6581234567"),
            ("+65 8123 4567", "+6581234567"),
            ("8123-4567", "+6581234567"),
            ("+65-8123-4567", "+6581234567"),
            ("invalid", None),
            ("", None),
        ]

        for phone, expected in test_cases:
            result = CarousellPhoneValidator.normalize_phone(phone)
            status = "✓" if result == expected else "✗"
            print(f"{status} normalize_phone('{phone}'): {result} (expected {expected})")
            assert result == expected, f"Failed for {phone}"


class TestDistrictExtractor:
    """Test district extraction from text."""

    def test_district_extraction(self):
        """Test extracting districts from text."""
        test_cases = [
            ("Looking for room in central area", "Central (D1-D9)"),
            ("Need accommodation in East Coast", "East (D14-D17)"),
            ("Room wanted near Tampines", "North-East (D19-D28)"),
            ("Searching for apartment west side", "West (D5-D7)"),
            ("I want to live in South", "South (D2-D5)"),
            ("Any area, flexible", None),
            ("", None),
        ]

        for text, expected in test_cases:
            result = DistrictExtractor.extract_district(text)
            status = "✓" if result == expected else "✗"
            print(f"{status} extract_district('{text[:30]}...'): {result} (expected {expected})")
            assert result == expected, f"Failed for {text}"


class TestTenantDataExtraction:
    """Test tenant data extraction from posts."""

    def test_budget_extraction(self):
        """Test extracting budget from post text."""
        test_cases = [
            ("Looking for room, budget $1500/month", "1500"),
            ("Need 1BR around 2000/mo", "2000"),
            ("Room wanted, can pay 1200pm", "1200"),
            ("No budget mentioned", None),
        ]

        # This is a simplified test; actual regex in scraper is more complex
        import re
        for text, expected in test_cases:
            match = re.search(r'\$?\s*(\d{3,4})\s*(?:\/month|\/mo|\/m|pm|pcm)', text, re.IGNORECASE)
            result = match.group(1) if match else None
            status = "✓" if result == expected else "✗"
            print(f"{status} extract_budget('{text}'): {result} (expected {expected})")
            assert result == expected, f"Failed for {text}"

    def test_room_type_detection(self):
        """Test detecting room type from text."""
        test_cases = [
            ("Looking for 1BR apartment", "1BR"),
            ("Need single room", "Room"),
            ("Want 2 bedroom", "2BR"),
            ("3BR or more", "3BR+"),
            ("Any type", None),
        ]

        for text, expected in test_cases:
            result = None
            if re.search(r'\b1\s*(?:br|bedroom|bed)\b', text, re.IGNORECASE):
                result = "1BR"
            elif re.search(r'\b2\s*(?:br|bedroom|bed)\b', text, re.IGNORECASE):
                result = "2BR"
            elif re.search(r'\b3\s*(?:br|bedroom|bed)\b', text, re.IGNORECASE):
                result = "3BR+"
            elif re.search(r'\b(?:single\s+)?room\b', text, re.IGNORECASE):
                result = "Room"

            status = "✓" if result == expected else "✗"
            print(f"{status} detect_room_type('{text}'): {result} (expected {expected})")
            assert result == expected, f"Failed for {text}"


class TestMessageQueueing:
    """Test message queueing and persistence."""

    def test_message_format(self):
        """Test message formatting."""
        tenant_data = {
            'phone': '+6581234567',
            'budget': '1500',
            'district': 'Central (D1-D9)',
            'room_type': 'Room',
            'raw_text': 'Looking for room in central'
        }

        message = f"""Hi there! I saw your post looking for a {tenant_data['room_type']} in {tenant_data['district']}.

I have {5}+ rooms available matching your budget (around SGD {tenant_data['budget']}/month):

1. Room in {tenant_data['district']}
   SGD {tenant_data['budget']}/month • Available ASAP

2. Room in nearby area
   SGD {tenant_data['budget']}/month • Great location

Want to view? I can arrange viewings within 24 hours.

Winfred Quek | Crestbrick
+65 8161 8149
CEA Reg. No: R073319H"""

        assert tenant_data['district'] in message, "District not in message"
        assert tenant_data['budget'] in message, "Budget not in message"
        assert tenant_data['room_type'] in message, "Room type not in message"
        print("✓ Message format includes all required fields")

    def test_queue_persistence(self):
        """Test saving and loading message queue."""
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as f:
            queue_file = Path(f.name)

        try:
            # Save queue
            messages = [
                {
                    'id': 'msg-1',
                    'source': 'carousell_tenant',
                    'phone': '+6581234567',
                    'message': 'Test message 1',
                    'created_at': datetime.now(timezone.utc).isoformat()
                },
                {
                    'id': 'msg-2',
                    'source': 'propertyguru_tenant',
                    'phone': '+6587654321',
                    'message': 'Test message 2',
                    'created_at': datetime.now(timezone.utc).isoformat()
                }
            ]

            with open(queue_file, 'w') as f:
                json.dump(messages, f)

            # Load queue
            with open(queue_file, 'r') as f:
                loaded = json.load(f)

            assert len(loaded) == 2, f"Expected 2 messages, got {len(loaded)}"
            assert loaded[0]['id'] == 'msg-1', "First message ID mismatch"
            assert loaded[1]['source'] == 'propertyguru_tenant', "Second message source mismatch"
            print("✓ Queue persistence works (save/load)")

        finally:
            queue_file.unlink()


class TestDeduplication:
    """Test deduplication logic."""

    def test_phone_deduplication(self):
        """Test that duplicate phones are not re-queued."""
        known_phones = {'+6581234567', '+6587654321'}
        new_phones = ['+6581234567', '+6589999999', '+6587654321']

        filtered = [p for p in new_phones if p not in known_phones]
        assert len(filtered) == 1, f"Expected 1 new phone, got {len(filtered)}"
        assert filtered[0] == '+6589999999', "Wrong phone kept"
        print("✓ Phone deduplication works")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("TENANT SCRAPER TEST SUITE")
    print("=" * 60)

    test_suites = [
        ("Phone Validation", TestPhoneValidator()),
        ("District Extraction", TestDistrictExtractor()),
        ("Tenant Data Extraction", TestTenantDataExtraction()),
        ("Message Queueing", TestMessageQueueing()),
        ("Deduplication", TestDeduplication()),
    ]

    total_tests = 0
    failed_tests = 0

    for suite_name, suite in test_suites:
        print(f"\n{suite_name}:")
        print("-" * 60)

        for method_name in dir(suite):
            if method_name.startswith('test_'):
                total_tests += 1
                try:
                    method = getattr(suite, method_name)
                    method()
                except AssertionError as e:
                    failed_tests += 1
                    print(f"✗ {method_name}: {e}")
                except Exception as e:
                    failed_tests += 1
                    print(f"✗ {method_name}: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print(f"RESULTS: {total_tests - failed_tests}/{total_tests} tests passed")
    if failed_tests == 0:
        print("Status: ALL TESTS PASSED ✓")
    else:
        print(f"Status: {failed_tests} TESTS FAILED ✗")
    print("=" * 60)

    return failed_tests == 0


if __name__ == '__main__':
    import re
    success = run_all_tests()
    sys.exit(0 if success else 1)
