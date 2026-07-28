#!/usr/bin/env bash
# lead-count-report.sh — daily 8am SGT lead count snapshot posted to Telegram.
# Sources: clients.db (buyer pipeline), wa-pipeline state (rental prospects),
#          wa-lead-queue.jsonl (unprocessed inbound), pg-conv-state.json (PG enquiries).
set -euo pipefail

LOG=/tmp/lead-count-report.log
exec >> "$LOG" 2>&1

log() { printf '[%s] %s\n' "$(TZ=Asia/Singapore date '+%Y-%m-%d %H:%M:%S SGT')" "$*"; }

log "=== lead-count-report start ==="

. "$HOME/.claude/bin/_telegram-helper.sh"

STATE="$HOME/.claude/state"
DB="$STATE/clients.db"

# ── Buyer pipeline from clients.db ────────────────────────────────────────────
read -r total_clients warm_clients hot_clients qualified_clients engaged_clients nurture_clients <<< "$(python3 - "$DB" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.row_factory = sqlite3.Row
cur = db.cursor()
cur.execute("SELECT COUNT(*) FROM clients")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM clients WHERE lead_temperature = 'Warm' OR lead_temperature = 'Hot'")
warm = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM clients WHERE lead_temperature = 'Hot'")
hot = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM clients WHERE stage = 'qualified'")
qualified = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM clients WHERE stage = 'engaged'")
engaged = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM clients WHERE stage = 'nurture'")
nurture = cur.fetchone()[0]
print(total, warm, hot, qualified, engaged, nurture)
PY
)"

# ── Rental prospects from wa-pipeline ─────────────────────────────────────────
tenant_count=$(python3 - "$STATE/wa-pipeline/tenant-records.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print(len(data))
except Exception:
    print(0)
PY
)

# ── Awaiting Winfred reply (wa-pipeline pending_reply) ────────────────────────
pending_reply=$(python3 - "$STATE/wa-pipeline/state.json" <<'PY'
import json, sys
try:
    state = json.load(open(sys.argv[1]))
    print(len(state.get("pending_reply", {})))
except Exception:
    print(0)
PY
)

# ── Unprocessed inbound (wa-lead-queue) ───────────────────────────────────────
lead_queue=$(python3 - "$STATE/wa-lead-queue.jsonl" <<'PY'
import sys, os
try:
    lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    print(len(lines))
except Exception:
    print(0)
PY
)

# ── PropertyGuru conversations ────────────────────────────────────────────────
read -r pg_total pg_complete pg_pending <<< "$(python3 - "$STATE/pg-conv-state.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    convs = data.get("conversations", {})
    total = len(convs)
    complete = sum(1 for c in convs.values() if c.get("stage") in ("complete", "escalated"))
    pending = total - complete
    print(total, complete, pending)
except Exception:
    print(0, 0, 0)
PY
)"

# ── Build date label ──────────────────────────────────────────────────────────
DATE_LABEL=$(TZ=Asia/Singapore date '+%-d %b')

# ── Compose message ───────────────────────────────────────────────────────────
MSG="$(printf '%s' "Daily Lead Count  ${DATE_LABEL}

Buyer pipeline (${total_clients} total)
  Qualified: ${qualified_clients}
  Engaged: ${engaged_clients}
  Nurture: ${nurture_clients}
  Warm/Hot: ${warm_clients} (${hot_clients} hot)

Rental prospects: ${tenant_count}
Pending reply from Winfred: ${pending_reply}
Inbound queue (unprocessed): ${lead_queue}
PG conversations: ${pg_total} (${pg_pending} pending, ${pg_complete} complete)")"

log "sending to Telegram"
send_tg "$MSG"
log "done"
