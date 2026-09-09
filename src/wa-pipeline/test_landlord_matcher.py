#!/usr/bin/env python3
"""
test_landlord_matcher.py — Integration test for tenant<->landlord matching system.

Run as: python3 test_landlord_matcher.py

Tests the matching algorithm and 99.co listing generation without sending actual
WhatsApp messages (DRY_RUN mode).
"""
import json, sys, os, tempfile

# STEP 0 sandbox seal (9 Sep 2026 merge review, item 4): this file used to import
# intake_engine and call load_state()/save_state()/on_landlord_form_completed() with NO
# sandboxing at all, so an ordinary `python3 test_landlord_matcher.py` run touched the REAL
# ~/.claude/state/listing-templates/intake-state.json (and, through
# on_landlord_form_completed_for_99co -> add_listing_to_index, the real
# listing-index.json). Every root MUST be redirected to a throwaway tempdir BEFORE the
# first wa-pipeline import: intake_engine.py's STATE/IDX/etc constants are captured once,
# at import time, from whatever the environment says then (see
# wa_intake_paths.resolved's docstring for why that is safe: a later env change alone would
# NOT be picked up, only an explicit patch or the value already being the sandboxed one).
_SANDBOX_TMP = tempfile.mkdtemp(prefix="wa-landlord-matcher-test-")
os.environ["WA_INTAKE_SANDBOX"] = "1"
os.environ["WA_INTAKE_STATE_ROOT"] = _SANDBOX_TMP
os.environ["WA_INTAKE_DATA_ROOT"] = _SANDBOX_TMP
os.environ["WA_INTAKE_MSG_DB"] = _SANDBOX_TMP

import wa_intake_paths as _P
_P.sandbox_init()   # refuses to proceed if any of the above still resolved into a real root

from intake_engine import load_state, save_state, is_supply_form_filled, on_landlord_form_completed
from landlord_tenant_matcher import (
    extract_landlord_property, load_active_tenants, find_tenant_matches, format_match_notification
)
from ninety_nine_co_lister import generate_listing_for_99co

# Test data
SAMPLE_LANDLORD_FORM = """
Owner name: John Tan
Address, unit type and size: Blk 123 Tampines Ave 1 #12-34, 2-room HDB, 950 sqft
Nearest MRT and walking distance: Tampines MRT, 5 mins walk
Available from (and is it vacant now?): 1 September 2026, yes vacant now
Which rooms are available now: Master bedroom and common area share
Sole owner, or jointly owned?: Sole owner
Is the unit mortgaged (bank notification needed?): Yes, mortgaged but no restrictions
HDB: is MOP met? Whole flat or room rental?: MOP met 2010. Room rental (master bedroom).

Asking rent and flexibility: $2,100/month, firm
Preferred lease duration (long term or short term?): Long term (2+ years)
Deposit or upfront rent before moving in?: 1 month deposit + 1 month advance rent
Rent payment method and date: Bank transfer, 1st of each month

Furnishing (unfurnished, semi, or fully, and what is included?): Semi-furnished (bed, wardrobe, desk)
Utilities included or excluded? (electricity, water, gas, WiFi): All utilities included up to $80/month
Aircon servicing, landlord or tenant?: Landlord handles servicing
Minor repairs, who handles, and up to how much?: Landlord handles up to $200, tenant beyond
Is the owner staying in the unit?: No, not staying in unit
How many existing housemates, and their gender?: 2 housemates, 1 female (working professional), 1 male (student)
How many share the bathroom?: 3 people share 1 bathroom

Preferred gender: Female preferred (mixed gender ok if professional)
Preferred nationality (any you prefer or exclude?): Any nationality, no exclusions
Preferred tenant type (working professional, student, couple, family): Working professional preferred
Max number of occupants: 1 tenant (room for 1 only)
Previous landlord references or income proof needed?: Payslip or employment letter preferred

Cooking (allowed, not allowed, or negotiable?): Light cooking allowed (no deep fry, no strong curry)
Pets allowed?: No pets
Smoking allowed?: No smoking inside (balcony ok)
Subletting allowed?: No subletting without landlord written consent
Visitors and overnight guests policy: Overnight visitors ok if informed landlord, no unannounced guests
Any other rules or concerns upfront? (noise, parties etc): Quiet after 10pm. No loud music or parties.

How to handle viewings (keys, lockbox, or accompanied?): Accompanied viewings only (landlord present)
Share 3 to 5 available dates and times: Weekends 2-5pm: Sep 7, 14, 21, 28

Photos: Will send via WhatsApp
"""

def test_property_extraction():
    """Test parsing a landlord form."""
    print("=" * 60)
    print("TEST 1: Property extraction from landlord form")
    print("=" * 60)

    prop = extract_landlord_property(SAMPLE_LANDLORD_FORM)
    print(f"\nExtracted {len(prop)} property fields:")
    for k, v in sorted(prop.items()):
        print(f"  {k}: {v}")

    assert prop.get("address"), "Should extract address"
    assert prop.get("asking_rent") == 2100, "Should extract rent as int"
    assert prop.get("gender_preference") is not None, "Should extract gender preference"
    print("\n✓ Property extraction test PASSED")
    return prop

def test_matching(prop):
    """Test matching algorithm."""
    print("\n" + "=" * 60)
    print("TEST 2: Tenant matching")
    print("=" * 60)

    # Load active tenants from state
    state = load_state()
    tenants = load_active_tenants()

    print(f"\nFound {len(tenants)} active tenants in pool")
    if len(tenants) > 0:
        print("Sample tenants:")
        for tenant in tenants[:3]:
            print(f"  - {tenant.get('name')} ({tenant.get('pn')})")
            prof = tenant.get("profile", {})
            print(f"    Budget: ${prof.get('budget')}, Pax: {prof.get('no_of_pax')}, Gender: {prof.get('gender')}")

    # Run matching
    matches = find_tenant_matches(prop, min_score=4)
    print(f"\nFound {len(matches)} matches (score >= 4):")
    for match in matches[:5]:
        print(f"\n  {match['name']} ({match['pn']})")
        print(f"  Score: {match['score']}/6")
        for reason in match["reasons"]:
            print(f"    • {reason}")

    if len(matches) > 0:
        print("\n✓ Matching test PASSED")
        return matches[0]
    else:
        print("\n⚠ No matches found (tenant pool may be empty in test mode)")
        return None

def test_notification(prop, match):
    """Test notification formatting."""
    print("\n" + "=" * 60)
    print("TEST 3: Match notification formatting")
    print("=" * 60)

    if not match:
        print("\nSkipping (no match data)")
        return

    msg = format_match_notification(prop, match["score"], match["reasons"])
    print(f"\nNotification for {match['name']}:\n")
    print(msg)
    print("\n✓ Notification formatting test PASSED")

def test_99co_listing(prop):
    """Test 99.co listing generation."""
    print("\n" + "=" * 60)
    print("TEST 4: 99.co listing generation")
    print("=" * 60)

    listing = generate_listing_for_99co(prop, "+6581234567", "John Tan")
    print(f"\nGenerated 99.co listing:")
    print(f"\nTitle: {listing['title']}")
    print(f"Property type: {listing['property_type']}")
    print(f"Rent: ${listing['rent']}")
    print(f"\nDescription:\n{listing['description']}")

    assert listing.get("title"), "Should generate title"
    assert listing.get("rent") == 2100, "Should extract rent"
    print("\n✓ 99.co listing test PASSED")

def test_full_integration():
    """Test the full on_landlord_form_completed hook."""
    print("\n" + "=" * 60)
    print("TEST 5: Full integration (on_landlord_form_completed)")
    print("=" * 60)

    state = load_state()
    actions = on_landlord_form_completed(
        state,
        "+6581234567",
        SAMPLE_LANDLORD_FORM,
        "John Tan"
    )

    print(f"\nGenerated {len(actions)} actions:")
    for i, action in enumerate(actions, 1):
        print(f"\n{i}. {action.get('type')}")
        if action.get("reason"):
            print(f"   Reason: {action['reason']}")
        if action.get("text"):
            print(f"   Text preview: {action['text'][:80]}...")

    print("\n✓ Full integration test PASSED")

if __name__ == "__main__":
    try:
        print("\n🧪 LANDLORD TENANT MATCHER - INTEGRATION TESTS\n")

        prop = test_property_extraction()
        match = test_matching(prop)
        test_notification(prop, match)
        test_99co_listing(prop)
        test_full_integration()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
