#!/bin/bash
# WhatsApp → Google Calendar sync
# Runs via launchd: com.crestbrick.whatsapp-calendar-sync
# NOTE 2026-06-12: the old Job 1 (PropertyGuru auto-reply) was REMOVED.
# All prospect messaging is owned by the wa-pipeline daemon
# (com.crestbrick.wa-pipeline). This script must NEVER send WhatsApp
# messages — calendar reconciliation only.

LOG_DIR="$HOME/.claude/state/whatsapp-calendar-sync"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/sync-$(date +%Y-%m-%d).log"

# Quiet hours: no syncs 23:00-07:59 SGT
HOUR=$((10#$(date +%H)))
if [ "$HOUR" -ge 23 ] || [ "$HOUR" -lt 8 ]; then
  exit 0
fi

# Runs at 08/11/14/17/20; 6h lookback overlaps the 3h gap. The 08:00 run
# must cover the overnight gap since the 20:00 sync, so 13h there.
LOOKBACK="6 hours"
if [ "$HOUR" -eq 8 ]; then
  LOOKBACK="13 hours"
fi

echo "" >> "$LOG_FILE"
echo "=== Sync run: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"

PROMPT='You are Winfred Quek'\''s personal assistant. Your ONLY job is to
sync confirmed WhatsApp appointments into Google Calendar. You must NOT
send any WhatsApp message to anyone under any circumstances.

Today'\''s date is '"$(date '+%Y-%m-%d')"'. Current time is '"$(date '+%H:%M')"' SGT.

### Step 1: Scan WhatsApp for appointments
Use list_messages with these queries, all with after= last '"$LOOKBACK"', limit=50:
- query="viewing"
- query="meet"
- query="appointment"
- query="confirm"

Also call list_chats (limit=40, sort_by=last_active).

Look for:
- New appointments/viewings confirmed by both parties
- Reschedules, cancellations, time changes

Read the full thread context before acting.

### Step 2: Resolve every person to a real name and phone number
NEVER put a JID (numbers ending in @lid or @s.whatsapp.net) in any
calendar event. For every person involved:
1. Phone number: if the chat JID ends in @s.whatsapp.net the number is
   the digits before the @. If it ends in @lid, resolve it with:
   sqlite3 ~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db \
     "SELECT pn FROM whatsmeow_lid_map WHERE lid='\''<digits>'\''"
2. Name, in priority order:
   a. Address book: sqlite3 ~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db \
      "SELECT full_name FROM whatsmeow_contacts WHERE their_jid='\''<phone>@s.whatsapp.net'\''"
   b. Tenant records: the "name" field in ~/.claude/state/wa-pipeline/tenant-records.json
   c. A name they gave in the chat itself
   d. Only if all fail, use the phone number as the display name
Format phone numbers like +65 9123 4567.

### Step 3: Check Google Calendar
- account: winfred
- calendarId: winfredquekoc@gmail.com
- List events for next 14 days

### Step 4: Reconcile
- New confirmed event not in calendar → create it
- Existing event rescheduled → update it
- Cancelled event → delete it

Event format:
- Title: emoji + type + person'\''s name (e.g. "🏠 Viewing – Sarah Tan")
- Location: full address if known
- Description: name and phone number (e.g. "Sarah Tan, +65 9123 4567"),
  the property, 2 or 3 sentences on what was last discussed, flag if
  anything is still pending. No JIDs anywhere.

### Output
Print a brief summary of what calendar changes were made.
Do not ask for confirmation — just act. Do not send WhatsApp messages.'

/Users/winfredquek/.npm-global/bin/claude \
  --dangerously-skip-permissions \
  --model sonnet \
  --strict-mcp-config --mcp-config "$HOME/.claude/mcp-configs/wa-gw.json" \
  --setting-sources "" \
  -p "$PROMPT" \
  >> "$LOG_FILE" 2>&1

echo "=== Done: $(date '+%H:%M:%S') ===" >> "$LOG_FILE"

# Keep only last 14 days of logs
find "$LOG_DIR" -name "sync-*.log" -mtime +14 -delete
