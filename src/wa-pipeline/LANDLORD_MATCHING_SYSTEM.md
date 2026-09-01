# Landlord Tenant Matching System + 99.co Auto-Listing

## Overview

Two interconnected systems for the landlord pipeline:

1. **Auto-Match Tenants to Properties** — Matches active tenants against landlord properties, sends notifications
2. **99.co Auto-Listing** — Auto-creates listings on 99.co when landlord form is complete

Both trigger when a landlord completes their onboarding form (all 6 sections filled).

## System 1: Tenant-Property Matching

### Flow

```
Landlord submits form (Day 0)
    ↓
intake_engine detects supply_side + sends landlord form
    ↓
Landlord fills & submits form
    ↓
Runner detects form completion (via is_supply_form_filled)
    ↓
on_landlord_form_completed() is called
    ↓
Extracts property data from form
    ↓
Loads active tenant pool (~276 tenants with completed profiles)
    ↓
Matching algorithm scores each tenant (0-6 criteria)
    ↓
Tenants scoring ≥4/6 are matched
    ↓
WhatsApp notifications sent to matched tenants
    ↓
Match records tracked in intake-state.json
    ↓
Viewing can be arranged within 24-48 hours (goal)
```

### Matching Criteria (6 points)

Each tenant is scored on:

1. **District Match** (1 pt) — Tenant's preferred location includes landlord's property district
2. **Budget Match** (1 pt) — Tenant's budget ≥ landlord's asking rent (within 5%)
3. **Gender Match** (1 pt) — Landlord's gender preference includes tenant
4. **Nationality Match** (1 pt) — Landlord's nationality preference includes tenant
5. **Occupancy Match** (1 pt) — Tenant group size ≤ landlord's max occupants
6. **Lifestyle Match** (1 pt) — Tenant's lifestyle aligns with house rules (cooking, pets, smoking, visitors)

**Threshold:** Match if score ≥ 4/6 (high precision; reduces false positives)

### Sample Notification

```
Hi! I found a room that matches your search 🏠

📍 Blk 123 Tampines Ave 1 #12-34
💰 $2,100/month (semi-furnished)
🚇 Tampines MRT, 5 mins walk
✅ Matches your search: gender: match (female), occupancy: 1 ≤ 1, lifestyle: visitors OK
📅 Available 1 September

Interested? Say YES and I can arrange a viewing for you 🙏
```

### Key Features

- **Bi-directional tracking:** Matches recorded in both tenant and landlord records
- **CEA-compliant:** Uses single intake_engine sender; no parallel auto-responders
- **Soft match scores:** 4/6 threshold minimizes false negatives while maintaining precision
- **Graceful degradation:** Unknown fields count as matches (no false negatives on incomplete profiles)

### State Schema Extensions

**Tenant record** (in intake-state.json):
```json
{
  "conversations": {
    "65591234567": {
      "property_matches": [
        {
          "landlord_pn": "65581234567",
          "matched_at": 1693497600,
          "score": 5,
          "notified_at": 1693497602
        }
      ]
    }
  }
}
```

**Landlord record** (in intake-state.json):
```json
{
  "conversations": {
    "65581234567": {
      "supply_form_sent": true,
      "form_complete_detected_at": 1693497600,
      "matched_tenants": [
        "65591234567",
        "65592134567"
      ],
      "tenant_notifications_sent": true,
      "matching_score": 5
    }
  }
}
```

## System 2: 99.co Auto-Listing

### Flow

```
Landlord form complete (detected by System 1)
    ↓
on_landlord_form_completed_for_99co() is called
    ↓
Property data extracted from form
    ↓
Listing generated from extracted fields
    ↓
99.co API attempt (if credentials available)
    ↓ (success)
Listing URL stored in listing-index.json
    ↓
Landlord notified of live listing
```

### Listing Generation

**Template:** "{room_type} Room in {district}, ${rent}/month"

**Description:** Includes
- Full address
- Furnishing level
- Utilities included/excluded
- Available date
- House rules (cooking, pets, smoking, subletting, visitors)
- Landlord preferences (gender, occupancy, tenant type)
- Contact (WhatsApp + landlord name)

### Data Flow

1. **Extract:** Property fields from form via `extract_landlord_property()`
2. **Generate:** 99.co-formatted listing via `generate_listing_for_99co()`
3. **Create:** POST to 99.co API (or queue for manual if API unavailable)
4. **Track:** Store listing URL + metadata in listing-index.json

### 99.co API Integration (Future)

When 99.co credentials available in `~/.claude/secrets/99co-credentials.json`:

```json
{
  "api_key": "your_99co_api_key",
  "user_id": "your_user_id",
  "endpoint": "https://api.99.co/v1"
}
```

Then `NinetyNineCoAPI.create_listing()` will POST directly.

**For now:** Listings queued with `status: "pending_99co"` + flag to Winfred for manual creation.

### State Schema Extensions

**listing-index.json:**
```json
{
  "listings": [
    {
      "listing_key": "blk-123-tampines",
      "landlord_phone": "+6581234567",
      "property_name": "Blk 123 Tampines Ave 1",
      "deal_type": "rent",
      "status": "pending_99co",
      "ninety_nine_co_url": "https://www.99.co/sg/listing/...",
      "ninety_nine_co_pending": false,
      "created_at": 1693497600
    }
  ]
}
```

## Integration Points

### intake_engine.py

Added to end of file:

```python
# On landlord form completion:
from intake_engine import on_landlord_form_completed

state = load_state()
actions = on_landlord_form_completed(
    state, 
    landlord_pn,      # "+6581234567"
    form_text,        # full form filled by landlord
    landlord_name     # "John Tan" (optional)
)

# actions: [
#   {"type": "SEND_MATCH", "tenant_pn": "65591234567", "text": "..."},
#   {"type": "SEND_CONFIRMATION", "landlord_pn": "...", "text": "..."},
#   {"type": "FLAG_HUMAN", "reason": "..."}
# ]

save_state(state)
for action in actions:
    if action["type"] == "SEND_MATCH":
        # Send via intake_engine's single WhatsApp sender
        ...
```

### wa_intake_runner.py (Integration Point)

In the main event loop, after calling `intake_engine.handle_event()`:

```python
from intake_engine import (
    is_supply_form_filled, 
    on_landlord_form_completed,
    resolve_pn
)

# After each landlord message is processed:
if rec.get("supply_form_sent") and not rec.get("form_complete_flagged"):
    if is_supply_form_filled(ev.get("text")):
        rec["form_complete_flagged"] = True
        pn = resolve_pn(ev["jid"])
        actions = on_landlord_form_completed(
            state,
            pn,
            ev.get("text"),
            contact_name_from_db(pn)  # lookup landlord name
        )
        # Send actions (SEND_MATCH, SEND_CONFIRMATION, etc)
        for action in actions:
            # Use intake_engine's single sender
            ...
```

## Testing

Run integration tests:

```bash
cd /Users/winfredquek/crestbrick-consult/src/wa-pipeline
python3 test_landlord_matcher.py
```

Expected output:
- Property extraction: ✓
- Tenant matching (60+ matches from ~276 pool): ✓
- Notification formatting: ✓
- 99.co listing generation: ✓
- Full integration: ✓

## Modules

### landlord_tenant_matcher.py

Core matching logic:

- `extract_landlord_property(form_text)` — Parse form → property dict
- `load_active_tenants(state_path)` — Load all tenants with form_sent=true
- `score_match(tenant_profile, landlord_prop)` — Score 0-6 criteria
- `find_tenant_matches(landlord_prop, min_score=4)` — Find all matches
- `format_match_notification(...)` — Generate WhatsApp message
- `on_landlord_form_completed(state, pn, form_text)` — Main entry point

### ninety_nine_co_lister.py

Listing generation:

- `generate_listing_for_99co(landlord_prop, landlord_phone, name)` — Create listing dict
- `add_listing_to_index(listing_data, landlord_phone)` — Store in listing-index.json
- `update_listing_url(listing_key, url_99co)` — Update with confirmed URL
- `NinetyNineCoAPI` — Wrapper for 99.co API (when available)
- `on_landlord_form_completed_for_99co(...)` — Main entry point

### intake_engine.py (Extensions)

Integration hooks:

- `on_landlord_form_completed(state, pn, form_text, name)` — Orchestrates both systems
- `_try_import_matcher()` — Safe lazy import
- `_try_import_99co_lister()` — Safe lazy import

### test_landlord_matcher.py

Integration tests covering full flow.

## Success Metrics

- **Matching precision:** Find tenants for 80%+ of new properties
- **Response time:** Tenants notified within 5 minutes of form completion
- **Conversion:** 40%+ of matched tenants express interest
- **Vacancy fill:** Reduce from 8-10 days → 2-3 days (target)

## Known Limitations & Future Work

1. **District proximity:** Currently simplified; should use actual MRT network graph
2. **99.co API:** Not yet implemented; manual form-fill fallback via Selenium (roadmap)
3. **Photo upload:** Landlord photos currently optional; auto-sync from messages.db pending
4. **Lifestyle matching:** Hard-coded rules; could be enriched from tenant conversation history
5. **Occupancy:** Assumes 1-person rooms; doesn't handle couples/families yet
6. **Re-matching:** Tenants matched once per property; future: match to multiple properties

## Deployment Checklist

- [ ] Test end-to-end with real landlord + tenant data
- [ ] Verify WhatsApp notifications send via intake_engine sender
- [ ] Confirm state tracking works (matches recorded in intake-state.json)
- [ ] Monitor false positive rate (should be <5%)
- [ ] Set up 99.co API credentials (or confirm manual fallback works)
- [ ] Document 99.co listing flow for Winfred
- [ ] Add metrics/logging to track performance

## Support & Debugging

**Q: Tenants not being matched?**
- Check if active tenant pool is populated (should be 100-300 tenants)
- Verify form extraction parsed correctly (check landlord_prop dict)
- Lower min_score threshold from 4 to 3 for broader matches
- Check intake-state.json for profile gaps (especially budget, gender)

**Q: Wrong tenants being matched?**
- Adjust weighting in score_match() function
- Refine lifestyle matching criteria
- Implement district proximity using actual MRT graph

**Q: 99.co listing not created?**
- Check if API credentials in ~/.claude/secrets/99co-credentials.json
- Verify listing-index.json is writable
- Check runner logs for API errors

## Code Quality Notes

- No hyphens in tenant-facing copy (Winfred's standing rule)
- CEA-compliant: single intake_engine sender only
- Graceful degradation: matches still fire even with missing fields
- State-driven: all tracking via intake-state.json (no side effects)
- Fail-closed: unknown data defaults to "any", never disqualifies
