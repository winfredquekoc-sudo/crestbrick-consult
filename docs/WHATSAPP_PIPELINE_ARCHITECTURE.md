# WhatsApp Enquiry Pipeline Architecture

**Last Updated:** 2026-06-08  
**Status:** Production  
**Version:** 2.0 (Consolidated - Claude only)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Pipeline Flow](#pipeline-flow)
4. [Components](#components)
5. [Data Structures](#data-structures)
6. [Running & Monitoring](#running--monitoring)
7. [Troubleshooting](#troubleshooting)

---

## Overview

The WhatsApp Enquiry Pipeline is an automated system that manages incoming property rental, sale, and landlord enquiries via WhatsApp. It classifies messages, extracts tenant/landlord profiles, makes intelligent matching decisions using Claude AI, and orchestrates the entire conversation lifecycle.

### Key Facts
- **Active Conversations:** 51+
- **Message Processing:** Every 4 minutes (240s launchd interval)
- **Decision Engine:** Claude 3.5 Sonnet
- **Deduplication:** 4-layer guard system
- **State Management:** Single source of truth (conversation-state.json)

---

## Architecture

### High-Level Flow

```
WhatsApp Message
       ↓
[whatsapp-autoreply.sh] ← Orchestrator (launchd trigger every 240s)
       ↓
[wa_pre_processor.py] ← Classification + Profile Extraction (Python, ~5ms)
       ↓
Claude 3.5 Sonnet ← Decision Engine (reads soul.md + landlord DB)
       ↓
[wa_post_processor.py] ← Execution + State Management (Python, ~10ms)
       ↓
WhatsApp API + Telegram Notification
```

### System Components

| Component | Type | Size | Purpose |
|-----------|------|------|---------|
| `whatsapp-autoreply.sh` | Bash | 20K | Orchestrator: triggers pre → Claude → post |
| `wa_pre_processor.py` | Python | 28K + 300 inline | Reads DB, classifies messages, builds tasks |
| Claude 3.5 Sonnet | LLM | External | Makes decisions, writes personalized messages |
| `wa_post_processor.py` | Python | 12K | Executes decisions, manages state, sends messages |
| `wa_send_guard.py` | Python | 11K | Atomic locks & deduplication logic |
| `soul.md` | Config | 2K | Winfred's voice & tone guidelines |

### Launchd Job

```
Label: com.crestbrick.whatsapp-autoreply
Schedule: Every 240 seconds (4 minutes)
Handler: /Users/winfredquek/crestbrick-consult/scripts/whatsapp-autoreply.sh
Quiet Hours: 10pm–8am SGT (exits silently)
```

---

## Pipeline Flow

### Step 1: Pre-Processor (Python)

**Input:** SQLite WhatsApp messages database  
**Output:** JSON tasks array

#### What it does:

1. **Scans Messages DB** — Finds new/unprocessed messages in the last 20 minutes
2. **Classifies Each Message** — Uses keyword matching:
   - **TENANT** — "interested in room", "want to rent"
   - **LANDLORD_SELL** — "selling my property", "en bloc"
   - **BUYER_BUY** — "looking to buy", "want to purchase"
   - **AGENT/COBROKE** — "co-broke", "commission", "my client"
   - **LANDLORD** — "I have a room", landlord-specific keywords

3. **Extracts Profile Fields** — Uses regex + keyword matching to pull:
   - name, nationality, gender, age, pass_type, pax, move_in_date, budget, lease_term, occupation, pets, smoking

4. **Reads Conversation State** — Checks if we've already replied to this JID and what stage they're at:
   - `template_sent` (sent listing form, waiting for profile)
   - `profile_received` (got profile, doing match check)
   - `viewing_offered` (sent viewing slot, waiting for confirmation)
   - `viewing_confirmed` (booked, done)
   - `dead` (no match, went silent, or rejected)

5. **Builds Task JSON** — One task per JID that needs action:
   ```json
   {
     "jid": "64313008099389@lid",
     "phase": "A" | "B" | "A_SELL" | "A_BUY",
     "listing_id": "edgefield-104b",
     "is_landlord": false,
     "trigger_message": "Hi, interested in 2 rooms...",
     "recent_messages": [...],
     "profile": {"pax": 2, "move_in": "July"},
     "conversation_stage": "template_sent"
   }
   ```

6. **Passes to Claude** — JSON tasks array sent as context

#### Dedup Guard 1: Form Already Sent?

Before sending another form to the same JID, check:
```python
if jid in replied_jids.json:
    skip_this_jid()  # Already sent, don't send again
```

---

### Step 2: Claude Decision Engine

**Input:** Task JSON + soul.md + landlord DB + time  
**Output:** JSON actions array

#### What it does:

Claude reads **ALL context** and decides:
- What type of message to send
- Whether the prospect matches landlord preferences
- Whether to offer a viewing slot
- What to say (using Winfred's voice)

#### Phase A (New Tenant Enquiry)

When a new prospect enquires about a rental:

1. **Match listing** — Find property in landlord DB (e.g., "Edgefield Plains" → `edgefield-104b`)
2. **Build listing description** — From landlord DB fields:
   - room_type, asking_rent, utilities_included, house_rules, viewing_schedule
3. **Attach profile form** — Standard fields (name, nationality, gender, pass type, pax, move-in date, etc.)
4. **Sign off** — "Winfred Quek | CEA R073319H"
5. **Action:** `send_tenant_template`, stage: `template_sent`

#### Phase B (Tenant Response with Profile)

When prospect fills in profile or provides info:

1. **Extract profile** — Call `extract_profile(recent_messages)` to pull fields from casual replies
2. **Profile match check** — Compare against landlord preferences:
   - `preferred_gender` — must match (if set)
   - `preferred_nationality` — must match (if set)
   - `max_pax` — prospect pax ≤ max_pax (if set)
3. **If no match** → Reject with: "sorry, landlord is looking for [specific]..." → stage: `dead`
4. **If match** → Push viewing slot from landlord DB
5. **Offer slot** — "Landlord is free on [day] [time] — does that work?"
6. **Action:** `send_reply`, stage: `viewing_offered`

#### Phase B (Prospect Confirms Viewing)

When prospect confirms a slot:

1. **Confirm** — "Confirmed, see you on [day] at [time]. Address is [property_address]."
2. **Action:** `send_reply`, stage: `viewing_confirmed`

#### Phase A_SELL (New Seller)

When someone wants to sell:
- Send seller intake form (full template)
- Action: `send_seller_intake`, stage: `seller_form_sent`

#### Phase A_BUY (New Buyer)

When someone wants to buy:
- Send buyer intake form (full template)
- Action: `send_buyer_form`, stage: `buyer_form_sent`

#### Landlord Handling

**Carousell lead** (reply to Winfred's outreach):
- Ask: "What's the asking rent? When can my tenant view?"

**New inbound landlord** (cold message with landlord keywords):
- Send full landlord intake form

**Known landlord** (in DB already):
- Brief: "Let me check and get back to you shortly :)"

---

### Step 3: Post-Processor (Python)

**Input:** JSON actions from Claude  
**Output:** Message sent, state updated, Telegram notification

#### What it does:

For each action in Claude's JSON:

1. **Dedup Guard 2: Sent This Run?**
   ```python
   if action_jid in sent_this_run:
       skip()  # Don't send the same message twice in one cycle
   ```

2. **Dedup Guard 3: Phase A Rate-Limit?**
   ```python
   if phase == "A" and (now - last_bot_reply) < 150 seconds:
       skip()  # Wait 150s between bot replies to same JID
   ```

3. **Dedup Guard 4: Atomic Lock?**
   ```python
   with lock(state_file):  # Prevents concurrent writes
       send_message()
       update_state()
   ```

4. **Send via WhatsApp API**
   ```
   POST http://127.0.0.1:8080/api/send
   {recipient: jid, message: message}
   ```

5. **Update conversation-state.json** — Atomically write:
   ```json
   {
     "jid": "...",
     "stage": "template_sent" | "viewing_offered" | "viewing_confirmed",
     "listing": "edgefield-104b",
     "last_bot_reply": "2026-06-08T14:48:00+08:00",
     "profile": {extracted fields},
     "offered_slot": {day, time, raw},
     "requested_slot": {day, time, raw},
     "hot_lead": false,
     "forms_sent": ["tenant_template"],
     "enthusiasm": 3-5
   }
   ```

6. **Update replied-jids.json** — Add JID if form was sent (prevents re-sending)

7. **Log** — Write to daily log file: `autoreply-YYYY-MM-DD.log`

8. **Notify Winfred** — Send Telegram message:
   ```
   📲 NEW TENANT (edgefield-104b): 2 pax, July move-in
   ```

---

## Data Structures

### conversation-state.json

**Location:** `~/.claude/state/listing-templates/conversation-state.json`

```json
{
  "conversations": {
    "64313008099389@lid": {
      "jid": "64313008099389@lid",
      "stage": "viewing_confirmed",
      "listing": "edgefield-104b",
      "last_message_from_prospect": "2026-06-08T16:00:00+08:00",
      "last_bot_reply": "2026-06-08T16:04:00+08:00",
      "profile": {
        "name": "Brandon Chan",
        "nationality": "Chinese",
        "gender": "Male",
        "age": "28",
        "pass_type": "EP",
        "pax": "2",
        "move_in_date": "1 July",
        "lease_term": "2 years",
        "budget": "$1,200"
      },
      "offered_slot": {
        "day": "Sat",
        "time": "9pm",
        "raw": "Sat 9pm"
      },
      "requested_slot": {
        "date": "Sat",
        "time": "9pm",
        "raw": "Sat 9pm"
      },
      "hot_lead": false,
      "forms_sent": ["tenant_template"],
      "enthusiasm": 4,
      "notes": "EP holder, confirmed Sat 9pm"
    }
  }
}
```

### landlord-db.json

**Location:** `~/.claude/state/wa-agent/landlord-db.json`

```json
{
  "64313008099389@lid": {
    "jid": "64313008099389@lid",
    "name": "Frank (Ong Chuan Heng)",
    "listing_id": "edgefield-104b",
    "property_address": "104B Edgefield Plains #14-29, Punggol S(822104)",
    "room_type": "2 common rooms available",
    "asking_rent": "$900/pax, $1,100 for 2 pax",
    "available_from": "1 room now, 1 room end of month",
    "min_lease": "long term preferred",
    "utilities_included": "Yes, utilities and aircon included",
    "house_rules": "No drunks. Owner stays in master room.",
    "viewing_schedule": "Weekdays or weekends after 7pm",
    "cooking": "Light cooking only (Maggie mee etc)",
    "owner_stays": "Yes — master room",
    "status": "available"
  }
}
```

### soul.md

**Location:** `~/crestbrick-consult/scripts/soul.md`

Contains Winfred's voice guidelines:
- Investor-minded, not salesy
- Helpful, practical, no BS
- Short messages (max 6 lines usually)
- No hyphens, no exclamation marks
- Professional but casual
- Always sign with "Winfred Quek | CEA R073319H" on first touch

---

## Running & Monitoring

### How to Start

1. **Launchd job is already installed:**
   ```bash
   launchctl list | grep whatsapp-autoreply
   ```

2. **If not running:**
   ```bash
   launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-autoreply.plist
   ```

3. **Manual trigger (for testing):**
   ```bash
   bash ~/crestbrick-consult/scripts/whatsapp-autoreply.sh
   ```

### View Logs

```bash
# Today's log
tail -100 ~/.claude/state/whatsapp-autoreply/autoreply-$(date +%Y-%m-%d).log

# Follow live
tail -f ~/.claude/state/whatsapp-autoreply/autoreply-*.log
```

### Check Pipeline State

```bash
# View all active conversations
python3 << 'EOF'
import json
f = json.load(open(os.path.expanduser("~/.claude/state/listing-templates/conversation-state.json")))
for jid, conv in f["conversations"].items():
    print(f"{conv['listing']} ({conv['stage']}): {conv.get('profile',{}).get('name','?')}")
EOF

# Count by stage
python3 << 'EOF'
import json
from collections import Counter
f = json.load(open(os.path.expanduser("~/.claude/state/listing-templates/conversation-state.json")))
stages = Counter(c['stage'] for c in f['conversations'].values())
for stage, count in sorted(stages.items()):
    print(f"{stage}: {count}")
EOF
```

---

## Monitoring & Optimization (Option 4)

### What to Track

#### 1. **Conversion Metrics** (Most Important)

```bash
# Weekly conversion report
Template Sent (last 7 days): 12
  ├─ Profile Received: 8 (67%)
  ├─ Viewing Offered: 6 (50%)
  ├─ Viewing Confirmed: 4 (33%)
  └─ Dead/Rejected: 4 (33%)

Goal: Increase template_sent → viewing_confirmed ratio
Target: 50%+ (vs. historical 40%)
Impact: +1% = ~20-30 extra viewings/month = $2,000+ commission
```

**Tracking Query:**
```python
import json
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
conv_state = json.load(open(os.path.expanduser("~/.claude/state/listing-templates/conversation-state.json")))

now = datetime.now(SGT)
last_7d = now - timedelta(days=7)

stages = {}
for jid, conv in conv_state["conversations"].items():
    last_reply = conv.get("last_bot_reply", "")
    if last_reply:
        try:
            ts = datetime.fromisoformat(last_reply)
            if ts > last_7d:
                stage = conv.get("stage")
                stages[stage] = stages.get(stage, 0) + 1
        except:
            pass

for stage in ["template_sent", "profile_received", "viewing_offered", "viewing_confirmed", "dead"]:
    count = stages.get(stage, 0)
    print(f"{stage}: {count}")
```

#### 2. **API Cost Tracking**

Claude charges ~$0.003 per request (average).

```bash
# Monthly estimate
Requests/day: ~160-200 (2,280/month)
Cost/month: ~$6-9
Historical: was $3/month with Groq

Justification:
- Better quality → higher conversion
- 1-2% conversion improvement = thousands in commission
- ROI: Break-even at first extra deal
```

**How to track:**
- Check Claude API dashboard monthly: https://console.anthropic.com/account/usage
- Log requests in daily logs
- Compare to viewing confirmations

#### 3. **Performance Metrics**

```bash
# Response time per cycle
Time from message arrival → Claude decision:
  - Message in DB: T0
  - Pre-processor finishes: T0 + 5ms
  - Claude decision: T0 + 2-5 seconds
  - Post-processor finishes: T0 + 5-6 seconds
  - Telegram notification: T0 + 6-7 seconds

Goal: Keep under 10 seconds (mostly Claude latency, can't optimize)
Alert: If >15 seconds, check Claude API status
```

**How to measure:**
```python
# In autoreply.sh, add timestamps
START=$(date +%s%3N)
python3 wa_pre_processor.py
PRE=$(date +%s%3N)
# ... Claude ...
CLAUDE=$(date +%s%3N)
python3 wa_post_processor.py
END=$(date +%s%3N)

echo "Pre: $((PRE-START))ms, Claude: $((CLAUDE-PRE))ms, Post: $((END-CLAUDE))ms" >> log
```

#### 4. **Duplicate Prevention Check**

```bash
# Weekly check: have we had any duplicate messages sent?
grep "DUPLICATE\|DEDUP" ~/.claude/state/whatsapp-autoreply/autoreply-*.log

Goal: ZERO duplicates
Alert: If any duplicates, investigate which guard failed
```

#### 5. **Landlord Match Quality**

```bash
# What % of prospects are being rejected for not matching landlord prefs?
Viewing Offered: 5
Dead (rejected): 2  → 28% rejection rate

Goal: <15% rejection (too high means filters are too strict)
Action: Review landlord preferences if >20% rejected
```

---

### Recommended Monitoring Cadence

| Frequency | What to Check | How |
|-----------|---------------|-----|
| **Daily** | Pipeline running? | Check launchd status, last log entry |
| **Daily** | Duplicate messages? | Grep logs for "DUPLICATE" |
| **Weekly** | Conversion metrics | Run weekly stats query |
| **Weekly** | Response times | Check log timestamps |
| **Monthly** | API costs | Check Claude dashboard |
| **Monthly** | Match quality | Review rejection rate |
| **Quarterly** | ROI analysis | Compare commission vs. API spend |

---

### Optimization Ideas

1. **Faster Phase A Response**
   - Currently: 2-5s (Claude latency)
   - Optimization: Pre-generate common templates (Python-only, 100ms)
   - Trade-off: Less personalized, but faster
   - Recommendation: Do this if >30% of Phase A messages are similar

2. **Better Profile Extraction**
   - Add more regex patterns for edge cases
   - Track which fields are most often missing
   - Prompt Claude to ask for critical missing fields

3. **Viewing Confirmation Nudge**
   - If prospect silent 24h after viewing_offered
   - Send one auto-nudge: "Still keen for [slot]?"
   - Track if nudges increase confirmation rate

4. **Landlord Preference Fine-Tuning**
   - Currently: hard match (must match or reject)
   - Idea: Soft match (preference, but still offer)
   - Example: "EP preferred, but WP welcome"

---

## Troubleshooting

### Issue: No Messages Being Processed

**Check:**
1. Is launchd job running? `launchctl list | grep whatsapp-autoreply`
2. Is WhatsApp bridge running? `ps aux | grep whatsapp-bridge`
3. Are there new messages in the DB? Query the messages.db

**Fix:**
```bash
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-autoreply.plist
```

### Issue: Duplicate Messages Sent

**Should not happen** (4-layer guard). If it does:

1. Check which guard failed:
   - Layer 1: form_already_sent() in database
   - Layer 2: sent_this_run set
   - Layer 3: Phase A 150s rate-limit
   - Layer 4: Atomic lock

2. Check logs: `grep "DUPLICATE\|SKIP" autoreply-*.log`

3. Investigate the specific JID and stage

### Issue: Message Not Sent to Prospect

**Likely causes:**
1. WhatsApp API is down: Check http://127.0.0.1:8080/api/send
2. Prospect is blocked or doesn't exist
3. Claude skipped the JID (check logs for "SKIP")
4. Rate-limit (Phase A 150s wait)

**Debug:**
```bash
# Check the last reply timestamp for that JID
python3 << 'EOF'
import json
jid = "xxx@lid"
data = json.load(open(os.path.expanduser("~/.claude/state/listing-templates/conversation-state.json")))
conv = data["conversations"].get(jid)
print(f"Last reply: {conv.get('last_bot_reply')}")
print(f"Stage: {conv.get('stage')}")
EOF
```

### Issue: Claude Replies Are Generic/Bad

**Possible causes:**
1. soul.md not being read — check whatsapp-autoreply.sh line for soul.md path
2. Claude model downgrade — verify we're using Claude 3.5 Sonnet
3. Context not being passed — check that landlord DB is being loaded

**Fix:**
1. Verify soul.md exists and is readable
2. Check Claude CLI command in whatsapp-autoreply.sh
3. Re-run with fresh context

---

## Version History

| Date | Version | Change |
|------|---------|--------|
| 2026-06-08 | 2.0 | Consolidated to Claude only, inlined extract_profile, removed Groq system |
| 2026-06-01 | 1.9 | Added duplicate guard improvements |
| 2026-05-20 | 1.5 | Initial Claude system (parallel with Groq) |

---

## References

- **WhatsApp MCP Bridge:** ~/whatsapp-mcp/whatsapp-bridge/
- **Conversation State:** ~/.claude/state/listing-templates/
- **Landlord Database:** ~/.claude/state/wa-agent/landlord-db.json
- **Logs:** ~/.claude/state/whatsapp-autoreply/
- **Backup (archived):** /tmp/wa-old-backup/

---

**Maintained by:** Winfred Quek  
**Questions?** Check logs first, then check soul.md and landlord DB
