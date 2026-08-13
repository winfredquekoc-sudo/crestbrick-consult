#!/usr/bin/env bash
# Manual only — nothing in build.py or any automated job calls this. Run by hand:
#   python3 scripts/matchmaker/build.py && scripts/matchmaker/deploy/deploy.sh
# Roll back only, no new deploy:
#   scripts/matchmaker/deploy/deploy.sh --rollback
#
# INCIDENT (11 Aug 2026): this folder was once deployed to production without
# deploy/middleware.js present, which shipped the PII app unauthenticated on
# the production alias for ~5 minutes until manual rollback. Two structural
# fixes below make that harder to repeat: (a) deploy/middleware.js presence is
# now a hard precondition checked BEFORE every deploy, and (b) if the
# production alias is ever caught answering 200 after a deploy, this script
# rolls back and re-verifies automatically instead of just printing a warning.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Absolute, not relative: build.py always writes to ~/crestbrick-consult/_local
# regardless of which worktree ran it, so deploy.sh must match that, not walk
# up from wherever this copy of deploy.sh happens to sit (e.g. a worktree).
SRC="$HOME/crestbrick-consult/_local/matchmaker.html"
# The stable alias is what matters — Vercel platform SSO does NOT cover it,
# only hash suffixed per deployment URLs get a 302 to vercel.com/sso-api.
PROD_ALIAS="https://crestbrick-matchmaker-private.vercel.app"
CURL="curl -s --max-time 20"

rollback_and_verify() {
  echo "deploy.sh: rolling back to the previous production deployment..." >&2
  # Bare `vercel rollback --yes` does NOT roll back — verified live during the
  # 11 Aug incident, it only reports "No deployment rollback in progress".
  # The previous production deployment must be targeted explicitly.
  local prev_url
  prev_url="$( (cd "$HERE" && vercel ls --prod 2>&1) | grep -Eo 'https://[a-z0-9-]+\.vercel\.app' | sed -n '2p')"
  if [ -z "$prev_url" ]; then
    echo "deploy.sh: could not identify the previous production deployment — go to the Vercel dashboard now and roll back manually. Do not treat this app as private until the alias returns 401 again." >&2
    exit 1
  fi
  (cd "$HERE" && vercel rollback "$prev_url" --yes) || {
    echo "deploy.sh: vercel rollback FAILED — go to the Vercel dashboard now and roll back manually. Do not treat this app as private until the alias returns 401 again." >&2
    exit 1
  }
  sleep 3
  local status_after
  status_after="$($CURL -o /dev/null -w '%{http_code}' "$PROD_ALIAS")"
  if [ "$status_after" != "401" ]; then
    echo "deploy.sh: ROLLBACK DID NOT RESTORE AUTH — $PROD_ALIAS still returns $status_after. Go to the Vercel dashboard NOW." >&2
    exit 1
  fi
  echo "deploy.sh: rollback verified — $PROD_ALIAS returns 401 again."
}

if [ "${1:-}" = "--rollback" ]; then
  rollback_and_verify
  exit 0
fi

# --- hard precondition: refuse to deploy this folder without its own auth
# middleware present, regardless of what's already live on Vercel. This is
# exactly the check that would have caught the 11 Aug incident. ---
if [ ! -f "$HERE/middleware.js" ]; then
  echo "deploy.sh: ABORTED — $HERE/middleware.js is missing. Refusing to deploy the PII app without auth middleware present." >&2
  exit 1
fi

if [ ! -f "$SRC" ]; then
  echo "deploy.sh: $SRC not found — run python3 scripts/matchmaker/build.py first" >&2
  exit 1
fi

cp "$SRC" "$HERE/index.html"
echo "deploying PII artifact to crestbrick-matchmaker-private — deploy/middleware.js confirmed present, MM_USER/MM_PASS must already be set on the Vercel project"

cd "$HERE"
OUT="$(vercel --prod --yes 2>&1 | tee /dev/stderr)"
URL="$(printf '%s\n' "$OUT" | grep -Eo 'https://[A-Za-z0-9.-]+\.vercel\.app' | tail -1)"

if [ -z "$URL" ]; then
  echo "deploy.sh: could not parse a deployed URL from vercel output above — verify auth manually before sharing any link" >&2
  exit 1
fi

# --- hash suffixed deployment URL: either the app's own middleware answers
# 401 directly, or platform SSO intercepts first with a 302 to its sso-api.
# Both mean auth is intact; anything else is a real failure. ---
HASH_INFO="$($CURL -o /dev/null -w '%{http_code} %{redirect_url}' "$URL")"
HASH_STATUS="${HASH_INFO%% *}"
HASH_LOCATION="${HASH_INFO#* }"
if [ "$HASH_STATUS" = "401" ]; then
  echo "deploy.sh: deployment URL $URL returns 401 (app auth active)."
elif [ "$HASH_STATUS" = "302" ] && printf '%s' "$HASH_LOCATION" | grep -q 'vercel.com/sso-api'; then
  echo "deploy.sh: deployment URL $URL returns 302 to Vercel SSO ($HASH_LOCATION) — platform protection active."
else
  echo "deploy.sh: FAILED — deployment URL $URL returned HTTP $HASH_STATUS (location: ${HASH_LOCATION:-none}), expected 401 or a 302 to vercel.com/sso-api." >&2
  echo "deploy.sh: AUTH MAY BE OFF on this deployment — check the Vercel dashboard immediately, this artifact has tenant/landlord PII." >&2
  exit 1
fi

# --- production alias: this is the URL that actually matters, and the one
# platform SSO does NOT cover. It must be 401, full stop. ---
STATUS="$($CURL -o /dev/null -w '%{http_code}' "$PROD_ALIAS")"
if [ "$STATUS" = "200" ]; then
  echo "############################################################" >&2
  echo "# DEPLOY.SH: PRODUCTION ALIAS IS UNAUTHENTICATED (HTTP 200) #" >&2
  echo "# $PROD_ALIAS is serving the PII app with NO AUTH WALL.     #" >&2
  echo "# Rolling back immediately.                                 #" >&2
  echo "############################################################" >&2
  rollback_and_verify
  exit 1
fi
if [ "$STATUS" != "401" ]; then
  echo "deploy.sh: FAILED — $PROD_ALIAS returned HTTP $STATUS, expected 401 (auth wall). Not the plain 200 that triggers auto rollback, but not the expected 401 either — check the Vercel dashboard before sharing any link." >&2
  if [ "$STATUS" = "503" ]; then
    echo "deploy.sh: a 503 here is middleware.js reporting that MM_USER/MM_PASS are not set on the Vercel project. Set both, then redeploy — do NOT treat this app as private until the alias returns 401." >&2
  fi
  exit 1
fi

echo "verified: $PROD_ALIAS returns 401 (auth wall active) — safe to share only with Winfred's own MM_USER/MM_PASS"

# --- the CRM API must sit behind the SAME wall as the app. /api/crm can read and write
# every note, stage and phone number in the CRM, so an /api route that answered without
# auth would hand all of it out even while "/" still looked locked. This check is the
# reason middleware.js's matcher must never be narrowed to exclude /api. ---
API_STATUS="$($CURL -o /dev/null -w '%{http_code}' "$PROD_ALIAS/api/crm")"
if [ "$API_STATUS" != "401" ]; then
  echo "############################################################" >&2
  echo "# DEPLOY.SH: THE CRM API IS NOT BEHIND THE AUTH WALL        #" >&2
  echo "# $PROD_ALIAS/api/crm returned $API_STATUS, expected 401.   #" >&2
  echo "# It can read and write every CRM note and phone number.    #" >&2
  echo "# Rolling back immediately.                                 #" >&2
  echo "############################################################" >&2
  rollback_and_verify
  exit 1
fi
echo "verified: $PROD_ALIAS/api/crm returns 401 (CRM API is behind the same wall)"

# --- backend reachability. Needs credentials, so it only runs when MM_USER/MM_PASS are
# exported locally; without them the deploy is still complete and the wall is still
# proven, we just cannot see past it from here. Never fails the deploy: a healthy app
# with no database is the supported local-only mode, not a broken deploy. ---
if [ -n "${MM_USER:-}" ] && [ -n "${MM_PASS:-}" ]; then
  HEALTH="$($CURL -u "$MM_USER:$MM_PASS" "$PROD_ALIAS/api/health")"
  case "$HEALTH" in
    *'"mode":"cloud"'*)             echo "verified: CRM backend is live — $HEALTH" ;;
    *'"mode":"local-only"'*)        echo "note: DATABASE_URL is not set on the Vercel project, so the app runs local-only — CRM writes stay on each device and do not sync. Set DATABASE_URL to switch it on." ;;
    *'"mode":"cloud-unreachable"'*) echo "WARNING: DATABASE_URL is set but the database did not answer. Every CRM write will queue on the device until it does. Response: $HEALTH" >&2 ;;
    *)                              echo "WARNING: unexpected /api/health response: $HEALTH" >&2 ;;
  esac
else
  echo "note: export MM_USER and MM_PASS before running this to also check the CRM backend's database connection."
fi
