#!/usr/bin/env bash
# daily-lead-count.sh — 08:00 SGT active lead count posted to Telegram.
# Business rule: buyer leads inactive >5 days are dead (excluded).
#                Tenant prospects inactive >5 days are also excluded.
#                Landlord records are EXEMPT from the 5-day cutoff.
# Sources:
#   clients.db          — buyer pipeline (last_contact_at field)
#   wa-pipeline/tenant-records.json — rental prospects (received field)
#   wa-agent/landlord-db.json       — active landlords (no cutoff)
set -euo pipefail

LOG=/tmp/daily-lead-count.log
exec >> "$LOG" 2>&1

log() { printf '[%s] %s\n' "$(TZ=Asia/Singapore date '+%Y-%m-%d %H:%M:%S SGT')" "$*"; }

log "=== daily-lead-count start ==="

. "$HOME/.claude/bin/_telegram-helper.sh"

STATE="$HOME/.claude/state"
DB="$STATE/clients.db"

# ── Buyer pipeline: active = last_contact_at within 5 days (or null = new, keep) ─
read -r active_buyers qualified_buyers engaged_buyers nurture_buyers hot_buyers new_today <<< "$(python3 - "$DB" <<'PY'
import sqlite3, sys

db = sqlite3.connect(sys.argv[1])
db.row_factory = sqlite3.Row
cur = db.cursor()

# Active: last_contact_at within 5 days, OR last_contact_at is NULL (never contacted = new lead, keep)
# Exclude closed/lost/not_fit/inactive stages regardless
ACTIVE_STAGES_SQL = "stage NOT IN ('closed','lost','not_fit','inactive','dead')"
CUTOFF_SQL = "(last_contact_at IS NULL OR datetime(last_contact_at) >= datetime('now','-5 days'))"
WHERE = f"{ACTIVE_STAGES_SQL} AND {CUTOFF_SQL}"

cur.execute(f"SELECT COUNT(*) FROM clients WHERE {WHERE}")
active = cur.fetchone()[0]

cur.execute(f"SELECT COUNT(*) FROM clients WHERE stage='qualified' AND {CUTOFF_SQL}")
qualified = cur.fetchone()[0]

cur.execute(f"SELECT COUNT(*) FROM clients WHERE stage='engaged' AND {CUTOFF_SQL}")
engaged = cur.fetchone()[0]

cur.execute(f"SELECT COUNT(*) FROM clients WHERE stage='nurture' AND {CUTOFF_SQL}")
nurture = cur.fetchone()[0]

cur.execute(f"SELECT COUNT(*) FROM clients WHERE lead_temperature='Hot' AND {WHERE}")
hot = cur.fetchone()[0]

from datetime import date
today = date.today().isoformat()
cur.execute("SELECT COUNT(*) FROM clients WHERE DATE(first_contact_at)=?", (today,))
new = cur.fetchone()[0]

print(active, qualified, engaged, nurture, hot, new)
PY
)"

# ── Rental prospects: active = received within 5 days ─────────────────────────
active_tenants=$(python3 - "$STATE/wa-pipeline/tenant-records.json" <<'PY'
import json, sys, datetime, os
try:
    path = sys.argv[1]
    if not os.path.exists(path):
        print(0)
        sys.exit(0)
    data = json.load(open(path))
    records = data if isinstance(data, list) else list(data.values())
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)).strftime('%Y-%m-%d')
    # received field is ISO-ish: "2026-06-12 06:50:51+08:00" — compare first 10 chars
    active = sum(1 for r in records if str(r.get("received", ""))[:10] >= cutoff)
    print(active)
except Exception:
    print(0)
PY
)

# ── Landlords: no 5-day cutoff — exempt by business rule ──────────────────────
landlord_active=$(python3 - \
    "$STATE/wa-agent/landlord-db.json" \
    "$STATE/client-db-export/landlords_active.json" <<'PY'
import json, sys, os

# Prefer the export file (landlords_active.json) — it tracks status explicitly.
# Fall back to wa-agent/landlord-db.json filtered by status=available/active.
export_path = sys.argv[2]
main_path   = sys.argv[1]

try:
    if os.path.exists(export_path):
        data = json.load(open(export_path))
        records = data if isinstance(data, list) else list(data.values())
        print(len(records))
        sys.exit(0)
except Exception:
    pass

try:
    data = json.load(open(main_path))
    records = data if isinstance(data, list) else list(data.values())
    active = [r for r in records if str(r.get("status","")).lower() in ("available","active","")]
    print(len(active))
except Exception:
    print(0)
PY
)

# ── Build date label (SGT) ────────────────────────────────────────────────────
DATE_LABEL=$(TZ=Asia/Singapore date '+%Y-%m-%d')

# ── Compose message ───────────────────────────────────────────────────────────
MSG="$(printf '%s' "Lead count  ${DATE_LABEL}

Active buyers: ${active_buyers} (qualified: ${qualified_buyers}, engaged: ${engaged_buyers}, nurture: ${nurture_buyers}, hot: ${hot_buyers})
New today: ${new_today}

Active rental prospects: ${active_tenants}
Active landlords: ${landlord_active}

Note: buyers and tenants inactive >5d excluded. Landlords exempt.")"

log "sending to Telegram"
send_tg "$MSG"
log "done"
