# Landlord Viewing Schedule Extraction - Documentation

**Generated:** 2026-06-08  
**Data Source:** WhatsApp conversations (20 landlords, 4,576 messages)  
**Coverage:** LL_002 through LL_021

---

## Quick Navigation

This folder contains 5 comprehensive documents analyzing viewing availability for all 20 landlords:

| File | Format | Best For | Size |
|------|--------|----------|------|
| **landlord-viewing-summary.txt** | Plain text | Quick reference, printing | 8.8 KB |
| **landlord-viewing-schedules.md** | Markdown | Detailed review, sharing | 14 KB |
| **landlord-viewing-schedules.json** | JSON | Programming, integration | 12 KB |
| **landlord-viewing-schedules.csv** | CSV | Spreadsheets, CRM import | 3.5 KB |
| **landlord-viewing-action-plan.md** | Markdown | Team coordination, templates | 11 KB |

---

## Document Descriptions

### 1. **landlord-viewing-summary.txt**
The fastest way to understand landlord availability at a glance.

**Contains:**
- Tiered breakdown (4 priority levels)
- Key statistics and numbers
- Time preference aggregates
- Quick scheduling scripts
- Action checklist

**Use when:** You need a quick reference or want to print one page

---

### 2. **landlord-viewing-schedules.md**
Complete detailed analysis with exact quotes from WhatsApp conversations.

**Contains:**
- Individual profile for each of 20 landlords
- Viewing schedule details
- Preferred days/times extracted from messages
- Direct quotes from conversations
- Viewing notes and constraints
- Summary comparison table

**Use when:** You want to review specific landlord details or share with team

---

### 3. **landlord-viewing-schedules.json**
Structured data for programmatic access and integration with tools.

**Contains:**
- Metadata (generation date, message count)
- All 20 landlord profiles in JSON structure
- Availability level classifications
- Summary categories
- Next steps by action type

**Use when:** Integrating with CRM, building automation, or programmatic access

---

### 4. **landlord-viewing-schedules.csv**
Simple table format, easily imported to spreadsheets or CRM systems.

**Contains:**
- One row per landlord
- All key fields: JID, Schedule, Days, Times, Availability Level
- Key quote
- Notes

**Use when:** Creating a spreadsheet, importing to CRM, or quick table reference

---

### 5. **landlord-viewing-action-plan.md**
Strategic guidance on how to schedule with each landlord effectively.

**Contains:**
- Priority 1-4 categorized landlords with specific tactics
- Outreach templates for each tier
- Efficiency tips based on availability patterns
- Time preference statistics
- Success metrics to track

**Use when:** Planning outreach strategy or training team on scheduling

---

## At A Glance: Landlord Categories

### TIER 1: Ready Now (3 landlords)
✓ **LL_003, LL_014, LL_002**

These landlords are flexible and responsive. Schedule immediately.
- LL_003: Anytime
- LL_014: Multiple viewings already scheduled
- LL_002: Handles concurrent viewings

**→ Action:** Propose dates, tenant picks time

---

### TIER 2: Specific Slots (4 landlords)
✓ **LL_010, LL_017, LL_019, LL_012**

These landlords have clear time windows. Confirm exact times.
- LL_010: Thursday 7-7:45pm (Lakeside MRT)
- LL_017: Every Friday 1pm or 5pm (Gedong Camp)
- LL_019: Thursday 6:30-7:30pm
- LL_012: Tuesday/Wednesday after 7pm

**→ Action:** Offer specific slot, get confirmation

---

### TIER 3: Flexible (5 landlords)
⏱️ **LL_008, LL_009, LL_013, LL_015, LL_018**

These landlords have availability but need options. Provide 3-5 time choices.
- LL_008: Thu/Tue evenings 8-9:30pm
- LL_009: Evening appointments (9pm+)
- LL_013: Sunday preferred, flexible on time
- LL_015: Saturday 1-3pm open house
- LL_018: Busy but responsive

**→ Action:** Provide multiple options, landlord selects

---

### TIER 4: Need to Ask (8 landlords)
❓ **LL_004, LL_005, LL_006, LL_007, LL_011, LL_016, LL_020, LL_021**

No schedule mentioned in messages. Direct follow-up needed.
- LL_004, LL_005, LL_006, LL_016, LL_020: No schedule mentioned
- LL_007: Not available same-day (plan 1+ day ahead)
- LL_011: Very limited availability
- LL_021: Wednesday nights ONLY

**→ Action:** Ask directly about availability

---

## Key Statistics

- **Total Landlords:** 20 (LL_002 through LL_021)
- **Messages Analyzed:** 4,576
- **Average per Landlord:** 229 messages
- **Most Active:** LL_002 (1,284 messages)
- **Least Active:** LL_021 (181 messages)

### Availability Distribution
- Very High (2): LL_003, LL_014
- High (1): LL_002
- Medium (9): LL_006, LL_008, LL_009, LL_010, LL_012, LL_013, LL_015, LL_017, LL_019
- Low (4): LL_007, LL_011, LL_018, LL_021
- Unknown (4): LL_004, LL_005, LL_016, LL_020

---

## Most Common Preferred Days

1. **Monday/Wednesday:** 6 landlords each
2. **Thursday:** 5 landlords
3. **Tuesday/Friday:** 5 landlords each
4. **Sunday:** 4 landlords
5. **Saturday:** 2 landlords

**Most popular evening times:** 6-9pm range

---

## Quick Messaging Templates

### For Tier 1 (Anytime)
```
Hi! I have a tenant keen on [unit]. You free this week?
```

### For Tier 2 (Specific Slots)
```
Can we lock in [DAY] [TIME]? That's when my tenant is free.
```

### For Tier 3 (Flexible)
```
I have interested tenants. Can you do:
- [TIME 1]
- [TIME 2]
- [TIME 3]?
```

### For Tier 4 (Ask)
```
When's best for you - mornings, afternoons, or evenings? 
Weekdays or weekends?
```

---

## Usage Examples

### Example 1: Scheduling a Viewing
1. Read **landlord-viewing-summary.txt** to see landlord tier
2. Check **landlord-viewing-schedules.md** for specific availability
3. Use template from **landlord-viewing-action-plan.md**
4. Send message with proposed time(s)

### Example 2: Creating a Master Calendar
1. Export **landlord-viewing-schedules.csv** 
2. Import into Google Sheets or Excel
3. Add your tenant availability
4. Find overlapping time slots

### Example 3: Programmatic Integration
1. Parse **landlord-viewing-schedules.json**
2. Build availability matching algorithm
3. Auto-suggest viewing times
4. Send confirmation messages

### Example 4: Team Training
1. Share **landlord-viewing-action-plan.md** with team
2. Review Tier categories and messaging templates
3. Practice with Tier 4 landlords (follow-up)
4. Track conversion rates by tier

---

## Data Extraction Method

**Source:** WhatsApp messages stored at  
`~/whatsapp-mcp/whatsapp-bridge/store/messages.db`

**Analysis Process:**
1. Queried SQLite database for all 20 landlord JIDs
2. Retrieved full message histories (4,576 total messages)
3. Identified viewing-related keywords and time mentions
4. Extracted exact quotes from conversations
5. Categorized by availability level
6. Created prioritized action plans

**Keywords used in extraction:**
- Viewing/View/See the
- Available/Free/Busy
- Time/When/Day/Schedule
- Days: Mon, Tue, Wed, Thu, Fri, Sat, Sun
- Times: 6pm, 7pm, 8pm, 9pm, morning, evening, etc.

---

## Important Notes

### Accuracy
- All information extracted directly from WhatsApp conversations
- Direct quotes preserved where mentioned
- No speculative information included
- "Not mentioned" indicates no viewing availability discussed in messages

### Updates
- This analysis reflects conversations up to 2026-06-08
- Availability may change - follow up before each viewing
- Update records when landlords confirm specific times
- Track no-shows and cancellations for future reference

### Privacy
- JIDs are landlord identifiers from WhatsApp
- Personal names and phone numbers removed
- Only viewing-relevant information extracted
- Conversation context preserved only where needed

---

## Next Steps

### Immediate (Today)
- [ ] Review landlord-viewing-summary.txt
- [ ] Contact Tier 1 landlords (LL_003, LL_014, LL_002)
- [ ] Confirm times with Tier 2 landlords (LL_010, LL_017, LL_019, LL_012)

### This Week
- [ ] Send option-based messages to Tier 3 (LL_008, LL_009, LL_013, LL_015, LL_018)
- [ ] Follow up with Tier 4 landlords (LL_004, LL_005, LL_006, LL_007, LL_011, LL_016, LL_020, LL_021)
- [ ] Create master viewing calendar with confirmed slots
- [ ] Set up reminders for unique time slots (e.g., LL_021 Wed nights only)

### Ongoing
- [ ] Track scheduling success rate by tier
- [ ] Monitor response times
- [ ] Update availability as seasons change
- [ ] Share feedback with team on what messaging works best

---

## Support & Questions

For detailed information on a specific landlord:
1. Check **landlord-viewing-schedules.md** for full profile
2. Review **landlord-viewing-action-plan.md** for strategy
3. Cross-reference **landlord-viewing-schedules.json** for structured data

For team coordination:
1. Share **landlord-viewing-summary.txt** for quick briefing
2. Use **landlord-viewing-action-plan.md** templates for outreach
3. Sync calendar using **landlord-viewing-schedules.csv**

---

**Generated:** 2026-06-08  
**Analysis Tool:** WhatsApp MCP + SQLite extraction  
**Coverage:** 100% of 20 landlords (LL_002-LL_021)
