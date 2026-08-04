#!/usr/bin/env bash
# publish-listings-nightly.sh — daily publisher for public/listings.json + public/listings.html,
# modeled on the insights-drip publisher (~/.claude/bin/insights-drip.sh): regenerates the
# listings feed AND the server-rendered listings.html cards from Winfred's own landlord DB
# (scripts/gen-website-listings.py) into a clean origin/main worktree, commits ONLY
# public/listings.json + public/listings.html when either changed, pushes (Vercel deploys
# on push to main). Idempotent — safe to run more than once a day.
#
# NOT wired to launchd by this script. Installing the plist / schedule is a separate,
# deliberate step for Winfred to take (or ask for) once he's reviewed the output.
#
# Manual run: bash scripts/publish-listings-nightly.sh
set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO="/Users/winfredquek/crestbrick-consult"
STATE="$HOME/.claude/state/listings-nightly"
WT="$STATE/wt"
BRANCH="main"
DB="$HOME/crestbrick-consult/_templates/landlord-db.json"

mkdir -p "$STATE"
echo "===== publish-listings-nightly $(date '+%F %T') ====="

if [ ! -f "$DB" ]; then
  echo "landlord DB not found at $DB; nothing to publish."
  exit 0
fi

if git -C "$REPO" worktree list | grep -q "$WT"; then
  git -C "$WT" fetch origin "$BRANCH" --quiet || { echo "fetch failed"; exit 1; }
  git -C "$WT" reset --hard "origin/$BRANCH" --quiet || { echo "reset failed"; exit 1; }
else
  git -C "$REPO" fetch origin "$BRANCH" --quiet || { echo "fetch failed"; exit 1; }
  git -C "$REPO" worktree add --detach "$WT" "origin/$BRANCH" --quiet || { echo "worktree add failed"; exit 1; }
fi

# gen-website-listings.py always reads the landlord DB from its fixed absolute path
# (it's untracked PII, never present inside a git worktree) and writes into $WT.
# It writes both public/listings.json AND server-renders the same listings into
# public/listings.html (sentinel-bounded cards + JSON-LD), so both must be staged
# and both must be included in the diff gate below.
python3 "$REPO/scripts/gen-website-listings.py" --root "$WT" || { echo "generator failed"; exit 1; }

git -C "$WT" add public/listings.json public/listings.html
python3 "$WT/scripts/gen-image-sitemap.py" && git -C "$WT" add public/sitemap-images.xml || true

if git -C "$WT" diff --cached --quiet -- public/listings.json public/listings.html; then
  echo "no change in public/listings.json or public/listings.html; nothing to publish."
else
  if ! python3 -c "import json; json.load(open('$WT/public/listings.json'))"; then
    echo "listings.json failed JSON validation; aborting and resetting."
    git -C "$WT" reset --hard "origin/$BRANCH" --quiet
    exit 1
  fi
  if ! grep -q '<!-- LISTINGS:START -->' "$WT/public/listings.html"; then
    echo "listings.html is missing its LISTINGS sentinel markers; aborting and resetting."
    git -C "$WT" reset --hard "origin/$BRANCH" --quiet
    exit 1
  fi
  git -C "$WT" commit -q -m "listings: nightly sync from landlord DB

Co-Authored-By: claude-flow <ruv@ruv.net>" || { echo "commit failed"; exit 1; }
  git -C "$WT" push origin "HEAD:$BRANCH" || { echo "push failed"; exit 1; }
  echo "published public/listings.json and public/listings.html, pushed to ${BRANCH}."
  bash "$WT/scripts/indexnow-ping.sh" "https://winfredquek.com/listings" || true
fi
