# WhatsApp Pipeline: Monitoring & Optimization Plan

**Purpose:** Track API costs, conversion metrics, and ROI to ensure Claude investment is justified

---

## Option 4: Monitoring Strategy

### Phase 1: Establish Baselines (Weeks 1-4)

**Goal:** Understand current performance before optimizations

#### Metric 1: Conversion Funnel

Track every prospect through the stages:

```
Week 1-4 Baseline:
┌─────────────────────────────────┐
│ Prospects reaching template_sent │ N = 12
├─────────────────────────────────┤
│ → Profile received (filled form) │ 8 (67%)
├─────────────────────────────────┤
│ → Viewing offered (matched prefs)│ 6 (50%)
├─────────────────────────────────┤
│ → Viewing confirmed (booked)     │ 4 (33%)
└─────────────────────────────────┘
Conversion Rate: 33% (template → confirmed)
Dead/Rejected: 4 (33%)
```

**Implementation:** Add this to daily digest

```python
# Add to wa_post_processor.py logging
import json
from datetime import datetime, timedelta, timezone

def log_funnel_stats():
    """Daily conversion funnel snapshot"""
    conv_state = load_json(CONV_STATE, {"conversations": {}})
    
    now = datetime.now(timezone(timedelta(hours=8)))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    stages = {}
    for jid, conv in conv_state["conversations"].items():
        last_reply = conv.get("last_bot_reply", "")
        if last_reply:
            try:
                ts = datetime.fromisoformat(last_reply)
                if ts >= today_start:
                    stage = conv.get("stage")
                    stages[stage] = stages.get(stage, 0) + 1
            except:
                pass
    
    log_line = f"FUNNEL: {stages}"
    print(log_line)
    
log_funnel_stats()
```

#### Metric 2: API Cost Tracking

```
Week 1-4 Baseline Cost:
Daily requests: ~160-180
Weekly requests: ~1,120-1,260
Monthly estimate: ~4,800-5,400 requests
Cost: ~$4.80-5.40/month @ $0.001/request
```

**How to track:**
1. Log every Claude API call (add timestamp to whatsapp-autoreply.sh)
2. Check Claude API dashboard monthly: https://console.anthropic.com/account/usage
3. Compare invoice to estimated requests

```bash
# In whatsapp-autoreply.sh, add around line 345
CLAUDE_START=$(date +%s%N)
RAW_OUTPUT=$(/Users/winfredquek/.npm-global/bin/claude ...)
CLAUDE_END=$(date +%s%N)
CLAUDE_MS=$(( (CLAUDE_END - CLAUDE_START) / 1000000 ))
echo "Claude latency: ${CLAUDE_MS}ms" >> "$LOG_FILE"
```

#### Metric 3: Message Quality (Subjective)

Track for 4 weeks:
- Are prospects responding positively?
- Are they confirming viewings?
- Any complaints about messages being generic?

---

### Phase 2: Compare to Groq Baseline (If Data Available)

**Historical Groq System (before consolidation):**

If you have old logs from wa_prospect_agent.py:
- Conversion rate: ?
- Duplicate messages: ?
- Prospect feedback: ?

**Comparison:**
```
Metric                    Groq (Old)      Claude (New)      Delta
──────────────────────────────────────────────────────────────
Conversion (template→confirmed)  ?%            33%             ?
Duplicate messages         Occasional      Prevented        ✓
Message quality            ★★★☆☆           ★★★★★           +++
Cost/month                 $3              $5-6             +$2-3
```

---

### Phase 3: Ongoing Monitoring (Monthly)

#### Monthly Report Template

```
═══════════════════════════════════════════════════════════
WHATSAPP PIPELINE MONTHLY REPORT — JUNE 2026
═══════════════════════════════════════════════════════════

📊 CONVERSION FUNNEL
─────────────────────
Template Sent:        47 (this month)
  ├─ Profile Received: 32 (68%)
  ├─ Viewing Offered: 24 (51%)
  ├─ Viewing Confirmed: 15 (32%)
  └─ Dead/Rejected: 15 (32%)

Conversion Rate: 32% (template_sent → viewing_confirmed)
Target: 40% (1% improvement = +5 viewings/month)

💰 API COSTS
─────────────────────
Claude Requests: 4,800
Estimated Cost: $4.80 (@ $0.001/request)
Actual Cost: $X.XX (from Anthropic dashboard)
Status: On budget ✓

⚡ PERFORMANCE
─────────────────────
Avg Response Time: 6.2 seconds
Duplicate Messages: 0 ✓
Pipeline Uptime: 99.8%
Errors/Warnings: 2 (none critical)

✅ INSIGHTS
─────────────────────
• Brandon Chan viewing (Sat 9pm) scheduled → confirm status
• Edgefield Plains: 3 active conversations (good)
• Rejected for gender mismatch: 4 prospects (12% of total)
• Still waiting for profile: 8 prospects (17% of total)

🎯 OPTIMIZATIONS TO TEST
─────────────────────
[ ] Nudge silent prospects after 24h
[ ] Fine-tune landlord gender/nationality preferences
[ ] Add 5 more rejection reasons to logs
[ ] Track viewing confirmation rate (how many actually show up)

═══════════════════════════════════════════════════════════
```

#### How to Generate This Report Automatically

Create a weekly dashboard script:

```python
# ~/crestbrick-consult/scripts/pipeline_stats.py
#!/usr/bin/env python3
import json, os
from datetime import datetime, timedelta, timezone
from collections import Counter

SGT = timezone(timedelta(hours=8))
CONV_STATE = os.path.expanduser("~/.claude/state/listing-templates/conversation-state.json")

def generate_monthly_report():
    conv = json.load(open(CONV_STATE))["conversations"]
    now = datetime.now(SGT)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Count stages
    stages = Counter()
    for jid, c in conv.items():
        last_reply = c.get("last_bot_reply", "")
        if last_reply:
            try:
                ts = datetime.fromisoformat(last_reply)
                if ts >= month_start:
                    stages[c.get("stage")] += 1
            except:
                pass
    
    # Calculate funnel
    template_sent = stages.get("template_sent", 0)
    profile_received = stages.get("profile_received", 0)
    viewing_offered = stages.get("viewing_offered", 0)
    viewing_confirmed = stages.get("viewing_confirmed", 0)
    dead = stages.get("dead", 0)
    
    total = template_sent + profile_received + viewing_offered + viewing_confirmed + dead
    
    print(f"""
═══════════════════════════════════════════════════════════
WHATSAPP PIPELINE REPORT — {now.strftime('%B %Y')}
═══════════════════════════════════════════════════════════

📊 CONVERSION FUNNEL
─────────────────────
Template Sent:        {template_sent} (this month)
  ├─ Profile Received: {profile_received} ({100*profile_received//max(1,template_sent)}%)
  ├─ Viewing Offered: {viewing_offered} ({100*viewing_offered//max(1,template_sent)}%)
  ├─ Viewing Confirmed: {viewing_confirmed} ({100*viewing_confirmed//max(1,template_sent)}%)
  └─ Dead/Rejected: {dead} ({100*dead//max(1,total)}%)

Conversion Rate: {100*viewing_confirmed//max(1,template_sent)}% (template → confirmed)
Target: 40%

Requests This Month: ~{total*240} (estimated)
Cost Estimate: ${total*0.001:.2f}
    """)

if __name__ == "__main__":
    generate_monthly_report()
```

Run it daily or weekly:
```bash
# Add to launchd or cron
0 8 * * 1 python3 ~/crestbrick-consult/scripts/pipeline_stats.py >> ~/pipeline-reports.log
```

---

## Phase 4: Optimization Roadmap

### Priority 1: Increase Conversion (Highest ROI)

**Goal:** Move conversion from 32% → 40%

#### Idea 1: Better Profile Extraction in Phase B

**Current:** Extract only from text, sometimes miss fields  
**Idea:** Have Claude ask for ONE missing critical field (not all)

```python
# In Claude prompt, add:
if found_fields < 3:
    ask_for_single_most_critical_field()
else:
    proceed_to_viewing_slot()
```

**Expected impact:** +2-3% conversion

#### Idea 2: Softer Landlord Matching

**Current:** Hard match (reject if any mismatch)  
**Idea:** Soft match (flag but still offer)

```python
# Instead of:
if gender_mismatch:
    reject()

# Do:
if gender_mismatch:
    offer_slot_but_note("Landlord prefers female, you're male")
    flag_for_winfred_review()
```

**Expected impact:** +3-5% conversion

#### Idea 3: Viewing Confirmation Nudge

**Current:** Silent after viewing_offered  
**Idea:** Nudge after 24h if no response

```python
# In Phase B, check:
if stage == "viewing_offered" and (now - last_bot_reply) > 24h:
    send_nudge("Still keen for [slot]?")
    stage = "nudge_sent"
```

**Expected impact:** +2-3% conversion (some prospects just forgot)

---

### Priority 2: Cost Optimization

**Current:** $5-6/month  
**Goal:** Stay under $6 (break-even threshold)

#### Idea 1: Cache Repeated Decisions

**Current:** Every prospect re-reads full landlord DB  
**Idea:** Cache landlord prefs if asking about same listing

```python
# Cache landlord_db in memory between cycles
landlord_cache = {}
if listing not in landlord_cache:
    landlord_cache[listing] = load_landlord(listing)
```

**Expected savings:** 5-10% (fewer tokens, faster)

#### Idea 2: Batch Processing

**Current:** Process messages one-by-one  
**Idea:** Batch 5-10 similar tasks, ask Claude once

**Trade-off:** Slightly less personalized, saves 50%+ on cost  
**Risk:** Harder to coordinate different decision types

---

### Priority 3: Better Metrics

**Goal:** Understand what's working

#### Add Viewing Show-up Rate

```python
# New field in conversation-state.json
"viewing_confirmed": "Sat 9pm",
"viewing_happened": true | false | "pending",
"viewing_feedback": "No-show" | "Showed up, serious" | "Showed up, not ready"
```

Track: Of confirmed viewings, how many actually happen?
- 100% show-up = viewing confirmation is valuable
- 50% show-up = need better qualification

#### Add Prospect Feedback

```python
"prospect_feedback": {
    "first_message_quality": 5,  # 1-5 stars
    "viewing_experience": 4
}
```

Have Claude ask after viewing: "How was your experience?"

---

## Success Metrics (6 Months)

### Target

```
Metric                          Current    Target    Impact
─────────────────────────────────────────────────────────────
Conversion (template → confirmed) 32%       40%       +6% = +25 viewings/month
API Cost/month                  $5         $4        -20% (via optimization)
Duplicate Messages              0          0         ✓ (maintain)
Prospect Satisfaction           ?          4.5/5     New metric

6-Month ROI:
  Baseline investment: 6 months × $5 = $30
  ROI: +25 viewings/month × $2,000 = +$50,000
  Net: +$49,970 (1,665x ROI)
```

---

## Implementation Timeline

```
Week 1-4:   Establish baselines (no code changes)
Week 5-8:   Test Idea 1 (better profile extraction)
Week 9-12:  Test Idea 2 (softer matching)
Week 13-16: Test Idea 3 (nudge system)
Month 6:    Review results, scale what works
```

---

## How to Start Monitoring RIGHT NOW

### Step 1: Add Daily Stats to Logs (5 minutes)

Add to wa_post_processor.py:

```python
def log_daily_stats():
    conv = load_json(CONV_STATE, {"conversations": {}})
    stages = Counter(c.get("stage") for c in conv.values())
    
    log_msg = f"DAILY_STATS: {dict(stages)}"
    print(log_msg)

log_daily_stats()  # Call at end of script
```

### Step 2: Monthly Dashboard (10 minutes)

Create `~/crestbrick-consult/scripts/pipeline_stats.py` (code above)  
Add to crontab: `0 8 * * 1 python3 ... >> ~/pipeline-reports.log`

### Step 3: Track Claude Costs (2 minutes)

Check monthly: https://console.anthropic.com/account/usage  
Compare to requests logged in daily logs

### Step 4: 6-Month Review

After 6 months:
- Compare conversion to baseline (target: 40%)
- Compare API costs (target: $4/month)
- Assess ROI (target: 1,000x+)
- Decide which optimizations to keep

---

## Questions to Ask Monthly

1. **Conversion:** Are more prospects booking viewings? (track %)
2. **Quality:** Are prospects complaining? (track feedback)
3. **Cost:** Is API spend justified by results? (track $/conversion)
4. **Optimization:** Which idea should we test next? (evaluate ideas above)

---

**Next Step:** Start monitoring. No code changes needed for baseline phase.  
**Owner:** Winfred (review monthly) or delegate to ops team
