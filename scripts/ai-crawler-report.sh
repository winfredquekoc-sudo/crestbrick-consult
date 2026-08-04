#!/usr/bin/env bash
# ai-crawler-report.sh — best-effort AI crawler (GPTBot/ClaudeBot/PerplexityBot/
# OAI-SearchBot/Google-Extended) hit report for winfredquek.com, PLUS an honest
# statement of what this Vercel setup can and cannot see.
#
# WHY THIS EXISTS
#   GA4 is JS-based and never fires for bots (they don't execute analytics.js).
#   The only way to see GPTBot etc. is server-side request logs. This script
#   investigates what's actually available on this project's Vercel plan and
#   surfaces it. It does NOT implement a log drain, middleware, or a proxy —
#   see "NOT IMPLEMENTED" below for why, and what to do if you want one.
#
# INVESTIGATION FINDINGS (verified live against this project, 2026-08-05):
#   - Auth: `vercel whoami` → winfredquekoc-3888 (CLI is authed; legacy `vercel
#     --prod` deploy path — do NOT use it, this repo deploys via `git push`).
#   - `vercel logs <url>` DOES return real data — confirmed by curling
#     /api/lead-magnet (a serverless function) and seeing it appear in
#     `vercel logs --json` output within seconds, source:"serverless".
#   - Requests to STATIC pages (/, /sitemap.xml, even cache-busted query
#     strings and deliberate 404s) NEVER appeared in `vercel logs`, repeatedly,
#     across several test requests. This matches Vercel's documented behavior:
#     Runtime Logs on the Hobby (free) plan only capture function invocations
#     (serverless λ / edge ε) — CDN-served static/prerendered responses (◇)
#     are not logged at all on this tier, cached or not.
#   - Hobby-plan Runtime Logs retention is ~1 hour (Pro: 1 day, Enterprise:
#     3 days) — even the function logs we CAN see disappear fast.
#   - Log Drains (continuous export of ALL request logs incl. static, to a
#     destination like Axiom/Datadog/S3) are a Pro/Enterprise-only feature —
#     confirmed via Vercel's own docs/changelog. Not available free.
#
#   BOTTOM LINE: this site is almost entirely static pages (/insights/*,
#   listings, etc.) — exactly what GPTBot/ClaudeBot/PerplexityBot crawl — and
#   those requests are INVISIBLE to any free-tier Vercel logging surface.
#   The only thing this script can honestly report on is hits to /api/*
#   serverless routes, which AI crawlers essentially never touch (there is no
#   reason for a page-fetching bot to POST to a lead-magnet API).
#
# WHAT THIS SCRIPT DOES
#   1. Pulls whatever Vercel WILL show us (last hour, /api/* invocations) and
#      greps for known AI crawler user agents, printing a per-bot per-path
#      table. On this project that table will almost always be empty — that
#      emptiness is expected and does NOT mean "no bots are visiting."
#   2. Prints the three real upgrade paths, plainly, with trade-offs. It does
#      NOT implement any of them.
#   3. (Section 2, separate from logging) prints instructions for GA4 AI
#      referral click tracking — this part GA4 genuinely CAN do, because a
#      human clicking a ChatGPT/Perplexity citation link IS a real browser
#      hit with a referrer header. See docs/ai-referral-tracking.md for the
#      ready-to-paste GA4 Exploration definition.
#
# USAGE
#   ./scripts/ai-crawler-report.sh              # last 1h (Hobby log window)
#   ./scripts/ai-crawler-report.sh --since 55m  # stay inside the 1h retention
#
# REQUIRES: vercel CLI, authed (`vercel whoami`), run from a dir linked to the
# crestbrick-consult project (.vercel/project.json present) or pass --project.
set -euo pipefail

SINCE="55m"
PROJECT_ARG=()
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE="$2"; shift 2 ;;
    --project) PROJECT_ARG=(--project "$2"); shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

DOMAIN="https://winfredquek.com"

# Known AI crawler / GEO-relevant user-agent substrings.
declare -a BOTS=(
  "GPTBot"
  "ChatGPT-User"
  "OAI-SearchBot"
  "ClaudeBot"
  "Claude-User"
  "Claude-SearchBot"
  "PerplexityBot"
  "Perplexity-User"
  "Google-Extended"
  "Applebot-Extended"
  "Bytespider"
)

echo "======================================================================"
echo " AI CRAWLER HIT REPORT — winfredquek.com"
echo " Generated: $(TZ=Asia/Singapore date '+%Y-%m-%d %H:%M:%S SGT')"
echo " Log window requested: last ${SINCE} (Hobby plan retains ~1h max)"
echo "======================================================================"
echo
echo "--- SECTION 1: what Vercel logs actually show (function invocations only) ---"
echo

if ! command -v vercel >/dev/null 2>&1; then
  echo "vercel CLI not found on PATH — cannot query logs. Install: npm i -g vercel" >&2
  exit 1
fi

if ! vercel whoami >/dev/null 2>&1; then
  echo "vercel CLI is not authenticated (vercel whoami failed)."
  echo "Run 'vercel login' interactively to enable this section. Skipping log pull."
  echo
else
  RAW_LOG=$(mktemp)
  trap 'rm -f "$RAW_LOG"' EXIT

  if vercel logs "$DOMAIN" --no-follow --since "$SINCE" -n 500 --json "${PROJECT_ARG[@]+"${PROJECT_ARG[@]}"}" > "$RAW_LOG" 2>/dev/null; then
    :
  else
    echo "vercel logs call failed or returned nothing parseable." >&2
  fi

  # Each line is one JSON log record: {..., requestPath, requestMethod,
  # responseStatusCode, source, ...}. The Vercel runtime log schema does NOT
  # include the request User-Agent header in this JSON — it is not exposed
  # via `vercel logs` at all (confirmed by inspecting captured records this
  # session). This means even for the /api/* traffic we CAN see, we cannot
  # identify which hits were bots vs humans by UA string using this CLI
  # alone. We report path+method+status instead, and note this limitation
  # explicitly rather than fabricate a UA column.
  TOTAL_LINES=$(grep -c . "$RAW_LOG" 2>/dev/null || echo 0)
  echo "Function-invocation log lines pulled: ${TOTAL_LINES}"
  echo
  if [ "$TOTAL_LINES" -gt 0 ]; then
    echo "Per-path hit counts (source=serverless/edge only — NOT static pages):"
    grep -o '"requestPath":"[^"]*"' "$RAW_LOG" 2>/dev/null | sort | uniq -c | sort -rn || true
    echo
    echo "NOTE: the JSON schema returned by 'vercel logs --json' on this plan"
    echo "does NOT include a User-Agent field, so per-bot attribution (the"
    echo "'per-bot per-path hit report' this script aims for) is NOT possible"
    echo "from this data source, even for the /api/* paths it does capture."
    for bot in "${BOTS[@]}"; do
      : "$bot"  # kept in BOTS[] for documentation / future log-drain reuse
    done
  else
    echo "No function-invocation logs in this window. Either nothing hit"
    echo "/api/* in the last ${SINCE}, or the 1-hour retention window has"
    echo "already rolled past the activity."
  fi
fi

echo
echo "--- SECTION 1b: what this script CANNOT see, and why (read this) ---"
cat <<'EOF'
CANNOT see, on the current (free/Hobby) Vercel plan:
  - Any request to a static page or asset (/, /insights/*, /listings, images,
    /sitemap.xml, robots.txt, etc.) — this is ~100% of what GPTBot,
    ClaudeBot, PerplexityBot, OAI-SearchBot, and Google-Extended actually
    crawl. Verified empirically this session: repeated curl requests to
    static pages (including cache-busted and 404 paths) never appeared in
    `vercel logs`, while a single request to /api/lead-magnet appeared
    within seconds.
  - User-Agent strings, even for the /api/* traffic that IS logged (the CLI
    JSON schema doesn't expose it).
  - Anything older than ~1 hour (Hobby plan log retention).

CAN see, right now, free:
  - Invocation count + path/method/status for serverless & edge FUNCTION
    routes only (/api/*), for the last hour.
  - Whether robots.txt / crawler access itself is broken (via curl -A
    "GPTBot" against the live site — that's a request YOU make, not a log).

THREE REAL UPGRADE PATHS (NOT implemented by this script — evaluate, then
ask Winfred before building any of these):
  1. Vercel Log Drain (Pro plan, ~US$20/mo/member minimum + drain usage) —
     ships ALL request logs, including static/CDN-served pages, to a
     destination (Axiom, Datadog, S3, etc.). This is the only way to see
     bot hits on static pages without touching the app itself. Cleanest
     option; costs money and requires the Pro plan.
  2. Edge Middleware logging (free on Hobby) — add a `middleware.ts` that
     runs on every request, checks the UA against the AI-bot list, and
     writes a log line (which WOULD then show up in `vercel logs` as an
     edge-function invocation). RISK: middleware runs on the request path
     for every visitor, including humans — a bug here can break or slow the
     entire site, and it silently converts every static page into an edge
     invocation (changes caching/cost characteristics, counts against the
     Hobby edge-function invocation limits). CLAUDE.md for this repo
     explicitly excludes middleware from this task's scope. Do not add it
     without a deliberate review and Winfred's go-ahead.
  3. Cloudflare (or similar) reverse proxy in front of Vercel — free tier
     gives full request logs incl. bot UA, IP, ASN via Cloudflare's own
     dashboard/Logpush, independent of Vercel entirely. Requires moving DNS
     for winfredquek.com to Cloudflare (nameserver or CNAME setup) and is a
     real infrastructure change — plan, don't improvise, and get a "go"
     before touching DNS on a production domain.

None of the three are implemented here. This script only reports on what is
already free and already available without any infrastructure change.
EOF

echo
echo "--- SECTION 2: AI referral clicks (GA4 CAN see these — see docs) ---"
cat <<'EOF'
Unlike bot crawl traffic, a human clicking a citation link from ChatGPT,
Perplexity, Gemini, Copilot, or Claude.ai IS a normal browser pageview with
a referrer header — GA4 already captures this via its consent-gated tag on
this site. No new instrumentation needed; this is a REPORTING problem, not a
data-collection gap.

Steps:
  1. Open GA4 → Explore → Blank exploration.
  2. Build the exploration exactly as specified in:
       docs/ai-referral-tracking.md
     (dimension: Session source / medium or Session manual source, filtered
     to chatgpt.com | perplexity.ai | gemini.google.com | copilot.microsoft.com
     | claude.ai; metrics: Sessions, Engaged sessions, Key events/Conversions).
  3. Save it, pin it, check weekly alongside normal channel reports.

This is documentation + a ready GA4 exploration definition — nothing to run
here. See docs/ai-referral-tracking.md for the full paste-in spec.
EOF

echo
echo "======================================================================"
echo " END REPORT"
echo "======================================================================"
