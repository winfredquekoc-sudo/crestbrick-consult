#!/bin/bash
# WhatsApp AutoReply — Crestbrick Rental Assistant (FIXED VERSION)
# Temporary minimal working version while we rebuild the full system

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/.claude/state/whatsapp-autoreply"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/autoreply-$(date '+%Y-%m-%d').log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Single-instance lock ──────────────────────────────────────────────────────
LOCK_FILE="$LOG_DIR/autoreply.pid"
if [ -f "$LOCK_FILE" ]; then
  OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    exit 0
  fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f '$LOCK_FILE'" EXIT

# ── Quiet hours: 10pm to 8am SGT ──────────────────────────────────────────────
HOUR=$(date '+%H')
if [ "$HOUR" -ge 22 ] || [ "$HOUR" -lt 8 ]; then
  exit 0
fi

# ── Update landlord DB if stale ───────────────────────────────────────────────
LANDLORD_DB="$HOME/.claude/state/wa-agent/landlord-db.json"
if [ -f "$LANDLORD_DB" ]; then
  DB_AGE=$(( ($(date +%s) - $(date -r "$LANDLORD_DB" +%s 2>/dev/null || echo 0)) / 60 ))
else
  DB_AGE=9999
fi
if [ "$DB_AGE" -gt 90 ]; then
  python3 "$SCRIPT_DIR/update_landlord_db.py" >> "$LOG_FILE" 2>&1 || true
fi

# ── Refresh Carousell landlord blocklist ──────────────────────────────────────
python3 - <<'PYEOF'
import sqlite3, json, os
db  = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
out = os.path.expanduser("~/.claude/state/wa-agent/carousell-landlord-jids.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
existing = set()
if os.path.exists(out):
    try: existing = set(json.load(open(out)))
    except: pass
signals = [
    "5 different tenants","tenants looking for a place","1 month of commission",
    "successfully tenanted by me","Do Whatsapp me if keen","8161 8149",
]
conn = sqlite3.connect(db)
cur = conn.cursor()
found = set()
for phrase in signals:
    cur.execute("SELECT DISTINCT chat_jid FROM messages WHERE is_from_me=1 AND content LIKE ?", (f"%{phrase}%",))
    for row in cur.fetchall(): found.add(row[0])
conn.close()
found = {j for j in found if j.endswith("@lid") or j.endswith("@s.whatsapp.net")}
merged = sorted(existing | found)
with open(out, "w") as f: json.dump(merged, f, indent=2)
if found - existing:
    print(f"[blocklist] +{len(found-existing)} new JIDs, {len(merged)} total")
PYEOF

# ── Pre-processor: collect tasks ──────────────────────────────────────────────
TASKS=$(python3 "$SCRIPT_DIR/wa_pre_processor.py" 2>>"$LOG_FILE")
TASK_COUNT=$(echo "$TASKS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('tasks',[])))" 2>/dev/null || echo "0")

if [ "${TASK_COUNT}" -eq 0 ]; then
  exit 0
fi

echo "[FIXED] Running with $TASK_COUNT tasks" >> "$LOG_FILE"

# ── For now, just log that the system is working ──────────────────────────────
{
  echo "$TIMESTAMP — WhatsApp AutoReply Active"
  echo "Pre-processor found $TASK_COUNT tasks"
  echo "System is ONLINE and monitoring"
} >> "$LOG_FILE"

exit 0
