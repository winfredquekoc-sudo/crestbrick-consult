#!/usr/bin/env bash
# check_pii_paths.sh — CI mirror of ~/.claude/bin/pii_path_guard.sh.
#
# The local pre-commit hook is the SOURCE OF TRUTH for what counts as a PII-shaped
# path; this script and scripts/ci/pii_paths.txt must be kept in sync with it BY HAND
# whenever the hook's PII_PATTERNS array changes (see comment at the top of that file
# and of pii_paths.txt). Detection is on the path only, not file content — fast,
# deterministic, matches the local hook's own tradeoff.
#
# Usage:
#   scripts/ci/check_pii_paths.sh <file> [<file> ...]   # explicit paths (used by tests)
#   git diff --name-only ... | scripts/ci/check_pii_paths.sh -   # paths from stdin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATTERNS_FILE="$SCRIPT_DIR/pii_paths.txt"

if [ ! -f "$PATTERNS_FILE" ]; then
  echo "check_pii_paths: missing pattern file $PATTERNS_FILE" >&2
  exit 1
fi

files=()
if [ "${1:-}" = "-" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && files+=("$line")
  done
else
  files=("$@")
fi

if [ ${#files[@]} -eq 0 ]; then
  echo "check_pii_paths: no changed files to check"
  exit 0
fi

violations=""
for f in "${files[@]}"; do
  [ -z "$f" ] && continue
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    case "$pat" in
      \#*) continue ;;
    esac
    if printf '%s' "$f" | grep -Eq "$pat"; then
      violations="${violations}  $f   (matched: $pat)"$'\n'
      break
    fi
  done < "$PATTERNS_FILE"
done

if [ -n "$violations" ]; then
  {
    echo ""
    echo "PII PATH GATE FAILED: changed path(s) match a known client-PII shape:"
    printf '%s' "$violations"
    echo ""
    echo "These paths carry real landlord/tenant names, phones, addresses or photos and"
    echo "must never reach a public/shared remote. Rename the file, or confirm with"
    echo "Winfred it is a genuine false positive before proceeding."
  } >&2
  exit 1
fi

echo "check_pii_paths: ${#files[@]} file(s) checked, no PII-shaped paths found"
exit 0
