#!/usr/bin/env bash
# serp-snapshot.sh — free rank tracking for winfredquek.com's 20 target queries
# (scripts/serp-queries.txt). Queries the local SearXNG instance if reachable,
# records whether winfredquek.com appears in the top 20 and at what position,
# and appends one row per query to ~/.claude/state/serp-history.csv.
#
# WHY: closes the measure loop for SEO/GEO work — free rank tracking without a
# paid rank-tracker subscription, using the SearXNG instance already configured
# for this machine's MCP setup (~/.claude.json -> mcpServers.searxng, default
# SEARXNG_URL=http://localhost:8888).
#
# Fails soft: if the backend is down, this script does NOT fabricate ranking
# data — it records one "unreachable" row so the gap itself is visible in the
# history, prints manual-fallback instructions, and exits 0 (safe for cron).
# Never hammers: 1-2s sleep between queries.
#
# Usage: scripts/serp-snapshot.sh [--searxng-url URL] [--queries FILE] [--out FILE]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
QUERIES_FILE="$SCRIPT_DIR/serp-queries.txt"
STATE_DIR="$HOME/.claude/state"
OUT_CSV="$STATE_DIR/serp-history.csv"
DOMAIN="winfredquek.com"
TOP_N=20

while [ $# -gt 0 ]; do
  case "$1" in
    --searxng-url) SEARXNG_URL="$2"; shift 2 ;;
    --queries) QUERIES_FILE="$2"; shift 2 ;;
    --out) OUT_CSV="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

csv_escape() {
  local s="${1//\"/\"\"}"
  printf '"%s"' "$s"
}

csv_row() {
  # date,query,found,position,note
  printf '%s,%s,%s,%s,%s\n' \
    "$(csv_escape "$1")" "$(csv_escape "$2")" "$(csv_escape "$3")" "$(csv_escape "$4")" "$(csv_escape "$5")"
}

mkdir -p "$STATE_DIR"
if [ ! -f "$OUT_CSV" ]; then
  echo "date,query,found,position,note" > "$OUT_CSV"
fi

if [ ! -f "$QUERIES_FILE" ]; then
  log "Query list not found at $QUERIES_FILE — nothing to do."
  exit 1
fi

for dep in curl jq; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    log "Missing dependency: $dep. Cannot run automated snapshot."
    log "Manual fallback: run each query in scripts/serp-queries.txt at google.com/search?q=... and note winfredquek.com's position by hand into $OUT_CSV."
    exit 0
  fi
done

TODAY="$(date '+%Y-%m-%d')"

# Reachability check first — fail soft, never hammer a dead backend.
# NOTE: curl's -w already prints "000" on connection failure (exit 7), so a
# naive `|| echo "000"` fallback double-prints ("000000"). Capture the exit
# status separately instead of relying on output-substitution fallback.
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$SEARXNG_URL/" 2>/dev/null)" || true
HTTP_CODE="${HTTP_CODE:-000}"
if [ "$HTTP_CODE" = "000" ]; then
  log "SearXNG unreachable at $SEARXNG_URL (connection failed)."
  csv_row "$TODAY" "__backend_check__" "unreachable" "" \
    "SearXNG not reachable at $SEARXNG_URL - start the instance and rerun scripts/serp-snapshot.sh" >> "$OUT_CSV"
  log "Recorded the gap honestly (one 'unreachable' row), not fabricated positions."
  log "Manual fallback: run each query from $QUERIES_FILE at google.com/search?q=<query> or bing.com/search?q=<query>, note whether winfredquek.com appears in the top 20 and at what position."
  exit 0
fi
log "SearXNG reachable at $SEARXNG_URL (HTTP $HTTP_CODE)."

QUERY_COUNT=0
FOUND_COUNT=0

while IFS= read -r QUERY || [ -n "$QUERY" ]; do
  QUERY="$(printf '%s' "$QUERY" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [ -z "$QUERY" ] && continue
  case "$QUERY" in \#*) continue ;; esac
  QUERY_COUNT=$((QUERY_COUNT + 1))

  RESPONSE="$(curl -s --max-time 10 -G "$SEARXNG_URL/search" \
    --data-urlencode "q=$QUERY" \
    --data-urlencode "format=json" \
    --data-urlencode "language=en" 2>/dev/null || true)"

  if [ -z "$RESPONSE" ]; then
    log "query failed (empty response): $QUERY"
    csv_row "$TODAY" "$QUERY" "error" "" "empty response from SearXNG" >> "$OUT_CSV"
  else
    POSITION="$(printf '%s' "$RESPONSE" | jq -r --arg domain "$DOMAIN" --argjson topn "$TOP_N" '
        (.results // [])[0:$topn]
        | map(.url // "")
        | to_entries[]
        | select(.value | test($domain))
        | (.key + 1)
      ' 2>/dev/null | head -1)"

    if [ -n "${POSITION:-}" ]; then
      log "FOUND  position $POSITION  -  $QUERY"
      csv_row "$TODAY" "$QUERY" "yes" "$POSITION" "" >> "$OUT_CSV"
      FOUND_COUNT=$((FOUND_COUNT + 1))
    else
      log "absent (not in top $TOP_N)  -  $QUERY"
      csv_row "$TODAY" "$QUERY" "no" "" "" >> "$OUT_CSV"
    fi
  fi

  # Never hammer — 1-2s between queries.
  sleep "$(( (RANDOM % 2) + 1 ))"
done < "$QUERIES_FILE"

log "Done. $FOUND_COUNT/$QUERY_COUNT queries found winfredquek.com in the top $TOP_N. Results appended to $OUT_CSV"
