# Tenant Intake Pipeline — Setup & Integration Guide

## Status: COMPLETE ✓

The tenant intake → matching automation is **fully built and tested**.

### What's Fixed

**Before:** Intro sent → **[STOPS HERE]** ❌  
**Now:** Intro → Intake form → Response parsing → Matching ✓

## What It Does

```
1. Send intro message ("Hi, rooms available at...")
         ↓
2. Auto-send intake form (after 30s delay)
         ↓
3. Tenant fills form with profile (name, budget, pass type, etc.)
         ↓
4. Parse response automatically
         ↓
5. Run 3-tier matching against all landlords
         ↓
6. Store matches in tenant_db.json
         ↓
[SKIPPED: Landlord notification - manual review step instead]
```

## Files Created

```
src/tenant-pipeline/
├── tenant-db.json                  # Tenant profiles & matches
├── send-tenant-intake.mjs          # Queue intake form for sending
├── parse-tenant-responses.mjs      # Parse filled forms
├── match-tenants.mjs               # Run 3-tier matching
├── tenant-workflow.mjs             # Orchestrator (main CLI)
└── README.md                        # Component reference
```

## Testing (Already Done ✓)

```bash
# 1. Register tenant after intro
node src/tenant-pipeline/tenant-workflow.mjs register "test-jid@s.whatsapp.net" "Property Name"

# 2. Process response
node src/tenant-pipeline/tenant-workflow.mjs respond "test-jid@s.whatsapp.net" \
  "• Name: John Doe
   • Email: john@example.com
   • Nationality: Indian
   • Budget: 1300
   ..."

# 3. View status
node src/tenant-pipeline/tenant-workflow.mjs status
```

**Test Result:** ✓ Successfully matched Dhruv Singh to Mrs. Chong (Oxley Edge)

## Integration Checklist

### [A] Auto-send intake form (30s after intro)

**What:** Listen for "intro sent" event → wait 30s → send intake template

**Where:** WhatsApp listener hook / n8n workflow

**How:**
```javascript
// Trigger: Message sent to tenant
// Delay: 30 seconds
// Action:
import { sendTenantIntake } from './src/tenant-pipeline/send-tenant-intake.mjs';
await sendTenantIntake(tenantJID, propertyName);
```

### [B] Listen for responses & auto-parse

**What:** Incoming message → detect form pattern → parse & match

**Where:** WhatsApp message listener / n8n workflow

**How:**
```javascript
// Trigger: Message received from tenant (contains "•")
// Action:
import { processTenantResponse } from './src/tenant-pipeline/parse-tenant-responses.mjs';
import { matchTenant } from './src/tenant-pipeline/match-tenants.mjs';

const parseResult = processTenantResponse(tenantJID, messageText);
if (parseResult.success) {
  const matchResult = matchTenant(tenantJID);
  // Matches stored in tenant-db.json
}
```

### [C] Manual review of matches

**What:** Check tenant_db.json for matched tenants

**Where:** Telegram notification or dashboard

**How:**
```bash
# View matched tenants
node src/tenant-pipeline/tenant-workflow.mjs status | jq '.matched_tenants'

# Or read directly
cat src/tenant-pipeline/tenant-db.json | jq '.tenants[] | select(.status=="matched_pending_review")'
```

### [D] (Future) Landlord notification

**Skipped for now as requested** ✓

When ready, trigger:
```javascript
// Send tenant profile to matched landlords
import { getReadyForLandlordNotification } from './src/tenant-pipeline/match-tenants.mjs';
const readyForNotif = getReadyForLandlordNotification();
// Send to landlords via WhatsApp
```

## Database Structure

### tenant-db.json

```json
{
  "total_tenants": 1,
  "tenants": [
    {
      "jid": "1234567890@s.whatsapp.net",
      "property_name": "Oxley Edge",
      "status": "matched_pending_review",
      "profile": {
        "name": "Dhruv Singh",
        "email": "dhruv@example.com",
        "nationality": "Indian",
        "budget": "1300",
        "lease_term": "24",
        "pass_type": "Student Pass",
        "received_at": "2026-06-10T02:15:00Z"
      },
      "matches": [
        {
          "landlord_id": "LL_001",
          "landlord_name": "Mrs. Chong",
          "tier": "Tier 2 (Good Match)",
          "score": 3,
          "property": "Oxley Edge, #04-02",
          "rent_range": "SGD 1100-2300",
          "flags": ["Long lease (24mo) >> landlord preference", "Student pass"]
        }
      ]
    }
  ],
  "intake_status": {
    "pending_intake_response": [],
    "intake_complete": ["1234567890@s.whatsapp.net"],
    "matched_pending_landlord_approval": []
  }
}
```

## Matching Logic

### Tier 1: Hard Requirements (Pass/Fail)
- ❌ Pets when landlord disallows
- ❌ Pax exceeds max
- ❌ Lease below min or above max
- ❌ Gender mismatch (if specified)

### Tier 2: Soft Scoring (+Points)
- +3 points: Budget 20%+ above asking
- +2 points: Long lease (≥landlord preference)
- +1 point: Budget within ±10% of asking
- +1 point: Student pass
- +1 point: Professional occupation

### Tier 3: Fast-Track (5+ points)
- Automatically prioritized for viewing

## Status Levels

| Status | Meaning |
|--------|---------|
| `pending_intake_response` | Intake form sent, waiting for response |
| `intake_complete` | Response received & parsed |
| `matched_pending_review` | Matches found, ready for next step |
| `matched_pending_landlord_approval` | (Future) Awaiting landlord response |
| `viewing_scheduled` | Both parties confirmed viewing |
| `lease_signed` | Tenancy agreement executed |

## Next Steps

1. **Hook intake send** — Set up WhatsApp listener to auto-send form 30s after intro
2. **Hook response listener** — Set up WhatsApp listener to detect and parse responses
3. **Add landlord notification** (when ready) — Notify matched landlords via WhatsApp
4. **Viewing coordination** — After both parties match, coordinate viewing time
5. **Lease tracking** — Track signing and move-in dates

## Example: Manual Trigger

```bash
# 1. Tenant sent intro (manual or external)
node src/tenant-pipeline/tenant-workflow.mjs register "ph@s.whatsapp.net" "Oxley Edge"

# 2. (Wait for response from tenant via WhatsApp...)

# 3. When response arrives, parse and match
node src/tenant-pipeline/tenant-workflow.mjs respond "ph@s.whatsapp.net" \
  "• Email: tenant@example.com
   • Name: John Doe
   • Nationality: Thai
   • Pass type: Student Pass
   • Budget: 1300
   • Lease term: 12
   • Move in date: 1 Aug 2026"

# 4. Check results
node src/tenant-pipeline/tenant-workflow.mjs status
```

## Troubleshooting

**Q: Response not parsing?**  
A: Check format — must have bullets (•) before each field

**Q: No matches found?**  
A: Likely Tier 1 hard reject (pets, pax, lease term, gender mismatch)

**Q: Want to see detailed match logic?**  
A: Check `src/tenant-pipeline/tenant-workflow.mjs status` output

## Architecture Overview

```
WhatsApp Messages
    ↓
[Listener Hook A] → Detect intro sent
    ↓ (30s delay)
[Listener Hook B] → send-tenant-intake.mjs
    ↓
Tenant receives form, fills it
    ↓
[Listener Hook C] → Detect response received
    ↓
[Listener Hook D] → parse-tenant-responses.mjs
    ↓
[Auto] → match-tenants.mjs
    ↓
Results stored in tenant-db.json
    ↓
[Manual Review] → Telegram notification (no auto landlord notification)
```

---

**Status:** Ready for integration hooks ✓  
**Last Updated:** 2026-06-10
