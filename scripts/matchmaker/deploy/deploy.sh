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
# exactly the check that would have caught the 11 Aug incident. Presence
# alone is not enough — a truncated or gutted middleware.js (syntactically
# present, functionally inert) would pass a bare -f check, so also assert it
# still references MM_USER/MM_PASS and still answers with a 401. This is a
# cheap grep, not a real invocation, so it cannot catch every way the file
# could be broken — but it catches the specific failure mode (file present,
# logic gone) that a presence-only check misses entirely. ---
if [ ! -f "$HERE/middleware.js" ]; then
  echo "deploy.sh: ABORTED — $HERE/middleware.js is missing. Refusing to deploy the PII app without auth middleware present." >&2
  exit 1
fi
if ! grep -q "MM_USER" "$HERE/middleware.js" || ! grep -q "MM_PASS" "$HERE/middleware.js"; then
  echo "deploy.sh: ABORTED — $HERE/middleware.js no longer references MM_USER/MM_PASS. It is present but does not look like it enforces auth — refusing to deploy the PII app." >&2
  exit 1
fi
if ! grep -q "401" "$HERE/middleware.js"; then
  echo "deploy.sh: ABORTED — $HERE/middleware.js does not appear to return a 401 response anywhere. It is present but does not look like it enforces auth — refusing to deploy the PII app." >&2
  exit 1
fi

if [ ! -f "$SRC" ]; then
  echo "deploy.sh: $SRC not found — run python3 scripts/matchmaker/build.py first" >&2
  exit 1
fi

# --- capture the build_id of the artifact about to ship. build.py stamps
# this into the DATA payload it inlines (see scripts/matchmaker/build.py's
# final print line and export_data.py's build_id). Reading it back out of
# $SRC — the exact bytes about to be copied and deployed below — rather than
# trusting anything printed earlier in this shell session means this value
# can never drift from what's actually shipped. It is the one string that
# lets us tell "the production alias answered" apart from "the production
# alias answered with THIS deploy", which a bare 401/200 status code cannot. ---
BUILD_ID="$(grep -o '"build_id": *"[^"]*"' "$SRC" | head -1 | sed -E 's/.*"([^"]+)"$/\1/')"
if [ -z "$BUILD_ID" ]; then
  echo "deploy.sh: ABORTED — could not find a build_id in $SRC's payload. Re-run python3 scripts/matchmaker/build.py." >&2
  exit 1
fi
echo "deploy.sh: shipping build_id $BUILD_ID"

cp "$SRC" "$HERE/index.html"
echo "deploying PII artifact to crestbrick-matchmaker-private — deploy/middleware.js confirmed present and MM_USER/MM_PASS-referencing, MM_USER/MM_PASS must already be set on the Vercel project"

cd "$HERE"
OUT="$(vercel --prod --yes 2>&1 | tee /dev/stderr)"
URL="$(printf '%s\n' "$OUT" | grep -Eo 'https://[A-Za-z0-9.-]+\.vercel\.app' | tail -1)"

if [ -z "$URL" ]; then
  echo "deploy.sh: could not parse a deployed URL from vercel output above — verify auth manually before sharing any link" >&2
  exit 1
fi

# --- promote, and PROVE the alias moved. -------------------------------------
# `vercel --prod` builds a production deployment but does NOT necessarily move the
# production alias onto it — observed live on 13 Aug 2026: the deploy reported
# success while crestbrick-matchmaker-private.vercel.app was still serving a
# deployment from ten hours earlier, and an explicit `vercel promote` was needed.
#
# The auth probes below cannot catch this on their own. Both the old and the new
# deployment answer 401 on "/" AND on "/api/crm" — the middleware runs before
# routing, so even a deployment with no /api directory at all returns 401 there.
# That is a shared marker, and shared markers are exactly what produced a false
# "deployed" confirmation once before. Deployment identity is the only honest
# discriminator available without credentials, so compare it explicitly.
DEPLOY_ID="$(printf '%s\n' "$OUT" | grep -Eo 'dpl_[A-Za-z0-9]+' | head -1)"
alias_dpl() { (cd "$HERE" && vercel inspect "$PROD_ALIAS" 2>&1) | grep -Eo 'dpl_[A-Za-z0-9]+' | head -1; }
if [ -n "$DEPLOY_ID" ]; then
  if [ "$(alias_dpl)" != "$DEPLOY_ID" ]; then
    echo "deploy.sh: production alias is NOT on the deployment just built — promoting $DEPLOY_ID..." >&2
    (cd "$HERE" && vercel promote "$DEPLOY_ID" --yes) || {
      echo "deploy.sh: promote FAILED — $PROD_ALIAS is still serving an older build. Promote from the Vercel dashboard before treating this deploy as done." >&2
      exit 1
    }
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      [ "$(alias_dpl)" = "$DEPLOY_ID" ] && break
      sleep 3
    done
  fi
  if [ "$(alias_dpl)" = "$DEPLOY_ID" ]; then
    echo "deploy.sh: confirmed — $PROD_ALIAS resolves to $DEPLOY_ID (this build)."
  else
    echo "deploy.sh: FAILED — $PROD_ALIAS still does not resolve to $DEPLOY_ID after promoting. Do not treat this build as live." >&2
    exit 1
  fi
else
  echo "deploy.sh: could not parse a deployment id from the vercel output — cannot prove the alias moved onto this build. Check the dashboard before treating it as live." >&2
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

# --- production alias build identity: alias propagation can lag behind
# `vercel --prod --yes` returning, so a probe against $PROD_ALIAS taken
# immediately after may still be answered by the PREVIOUS deployment, not
# the one just shipped. A 401 from the old deployment and a 401 from the new
# one are byte-identical, so the checks below cannot tell them apart on
# their own — this is exactly how a shared marker gave a false "deployed"
# confirmation on an earlier PR. The only thing that discriminates old vs.
# new is content unique to the new build, and build_id (captured above from
# $SRC) is that content. It is baked into the DATA payload, which is inside
# the Basic Auth wall, so reading it requires MM_USER/MM_PASS. When they are
# exported, poll (bounded) until the alias serves THIS build_id before
# trusting anything else read from it. When they are absent, say so plainly
# — the checks below still prove an auth wall is present, but NOT that it is
# this deployment's wall. ---
BUILD_CONFIRMED=0
if [ -n "${MM_USER:-}" ] && [ -n "${MM_PASS:-}" ]; then
  echo "deploy.sh: confirming $PROD_ALIAS is serving build_id $BUILD_ID before trusting its auth checks..."
  ATTEMPTS=0
  MAX_ATTEMPTS=20
  SLEEP_SECS=3
  while [ "$ATTEMPTS" -lt "$MAX_ATTEMPTS" ]; do
    ATTEMPTS=$((ATTEMPTS + 1))
    BODY="$($CURL -u "$MM_USER:$MM_PASS" "$PROD_ALIAS" || true)"
    if printf '%s' "$BODY" | grep -q "\"build_id\": *\"$BUILD_ID\""; then
      BUILD_CONFIRMED=1
      echo "deploy.sh: confirmed — $PROD_ALIAS is serving build_id $BUILD_ID (attempt $ATTEMPTS/$MAX_ATTEMPTS, ~$(( (ATTEMPTS - 1) * SLEEP_SECS ))s elapsed)."
      break
    fi
    sleep "$SLEEP_SECS"
  done
  if [ "$BUILD_CONFIRMED" -ne 1 ]; then
    echo "deploy.sh: FAILED — $PROD_ALIAS never served build_id $BUILD_ID after $MAX_ATTEMPTS attempts (~$((MAX_ATTEMPTS * SLEEP_SECS))s). It is still serving a stale deployment (or something else is wrong). Do NOT treat this deploy as live — check the Vercel dashboard, and re-run this script once the alias has propagated." >&2
    exit 1
  fi
else
  echo "deploy.sh: MM_USER/MM_PASS not set — cannot read the Basic Auth walled body to confirm $PROD_ALIAS is serving build_id $BUILD_ID (this build) rather than a stale deployment. The checks below can only prove an auth wall is present on whatever answers — NOT that it is this build's auth wall. Export MM_USER and MM_PASS for a real confirmation." >&2
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

if [ "$BUILD_CONFIRMED" -eq 1 ]; then
  echo "verified: $PROD_ALIAS returns 401 (auth wall active) AND is confirmed serving build_id $BUILD_ID (this build) — safe to share only with Winfred's own MM_USER/MM_PASS"
else
  echo "verified: $PROD_ALIAS returns 401 (auth wall active) — build identity NOT confirmed (MM_USER/MM_PASS were not set), so this does NOT prove build_id $BUILD_ID (this build) is what's being served, only that whatever is being served is behind the wall"
fi

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
if [ "$BUILD_CONFIRMED" -eq 1 ]; then
  echo "verified: $PROD_ALIAS/api/crm returns 401 (CRM API is behind the same wall, confirmed on build_id $BUILD_ID)"
else
  echo "verified: $PROD_ALIAS/api/crm returns 401 (CRM API is behind the same wall) — build identity NOT confirmed (MM_USER/MM_PASS were not set)"
fi

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
