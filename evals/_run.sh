#!/usr/bin/env bash
# Wrapper for launchd: weekly Crestbrick agent eval run.
# Schedule: Sunday 17:00 SGT (= Sunday 09:00 UTC) via
# ~/Library/LaunchAgents/com.crestbrick.agent-evals.plist.
set -u
set -o pipefail

EVALS_DIR="$HOME/crestbrick-consult/evals"
LOG_DIR="$EVALS_DIR/_logs"
mkdir -p "$LOG_DIR"

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
LOG="$LOG_DIR/run-$STAMP.log"

# Ensure PATH includes the user's npm-global bin so `claude` is found.
export PATH="$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

cd "$EVALS_DIR" || exit 99
{
  echo "=== eval run $STAMP ==="
  /usr/bin/env python3 "$EVALS_DIR/run.py"
  echo "=== exit $? ==="
} >>"$LOG" 2>&1
