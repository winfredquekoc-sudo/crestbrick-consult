#!/bin/sh
# indexnow-ping.sh — notifies IndexNow (Bing/Seznam/Naver; Bing also feeds ChatGPT
# search + Copilot) plus a Google sitemap ping, right after a publisher pushes new
# pages to main. Fail soft: network/API errors are logged, never propagated, so a
# ping failure can never break the calling publisher's exit code.
#
# Usage: indexnow-ping.sh [--dry-run] <url> [url ...]
#        echo "<url>" | indexnow-ping.sh [--dry-run]
HOST="winfredquek.com"
KEY="43b2bc618f45f79d20c657c5fc4b0746"
KEY_LOCATION="https://${HOST}/${KEY}.txt"
LOG_DIR="$HOME/Library/Logs/crestbrick"
LOG="$LOG_DIR/indexnow.log"

mkdir -p "$LOG_DIR" 2>/dev/null

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG" 2>/dev/null
}

DRY_RUN=0
if [ "$1" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi

if [ "$#" -gt 0 ]; then
  URLS="$*"
else
  URLS="$(cat)"
fi

if [ -z "$URLS" ]; then
  log "no URLs supplied; nothing to ping."
  exit 0
fi

URL_LIST_JSON=""
for u in $URLS; do
  [ -z "$u" ] && continue
  if [ -z "$URL_LIST_JSON" ]; then
    URL_LIST_JSON="\"$u\""
  else
    URL_LIST_JSON="${URL_LIST_JSON},\"$u\""
  fi
done

if [ -z "$URL_LIST_JSON" ]; then
  log "no valid URLs after parsing; nothing to ping."
  exit 0
fi

BODY="{\"host\":\"${HOST}\",\"key\":\"${KEY}\",\"keyLocation\":\"${KEY_LOCATION}\",\"urlList\":[${URL_LIST_JSON}]}"

log "pinging for: $URLS"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "$BODY"
  log "dry run, body above; skipping network calls."
  exit 0
fi

IN_STATUS=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' \
  -X POST "https://api.indexnow.org/indexnow" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$BODY" 2>>"$LOG")
log "indexnow.org response: ${IN_STATUS:-no response (network error)}"

GP_STATUS=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' \
  "https://www.google.com/ping?sitemap=https://${HOST}/sitemap.xml" 2>>"$LOG")
log "google sitemap ping response: ${GP_STATUS:-no response (network error)}"

exit 0
