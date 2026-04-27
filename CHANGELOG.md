# Changelog

All notable changes to crestbrick-consult (site + agents + automation stack) live here. Update on every meaningful change. Newest first.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Versions track the site repo (`package.json`); agent/automation changes are dated entries under the latest version.

## [Unreleased] — 2026-04-25

### Added — bulk improvements rollout (J99)
- **Site:** `audit-sample.html` lead magnet, `case-studies.html` (3 sanitised case studies), `testimonials-carousel.html` snippet, `tools-promo.html` snippet, `site-enhancements.js` (sticky filter + lazy-load + WA tracking)
- **Site:** `/api/track-click` and `/api/audit-sample-request` serverless endpoints
- **Site:** Vercel Insights (`_vercel/insights/script.js`) wired into `index.html` + `listings.html`
- **Site:** `vercel.ts` typed config alongside `vercel.json` (cutover staged)
- **Site:** `.nvmrc` pinning Node 24, `engines.node >=24.0.0` in `package.json`
- **Scripts:** `scripts/_inject_schema_and_og.py` for schema.org RealEstateListing + OG index
- **Scripts:** `scripts/_generate_og_images.py` (Higgsfield brief + PIL fallback)
- **Agents:** `family-office-relationship.md` (white-glove FO track replaces drip cadence)
- **Agents:** `_co-broke-scope.md` doc resolving co-broke-coordinator vs cobroke-manager overlap
- **Automation:** `~/.claude/bin/overnight-lib.sh` (heartbeat / ol_retry / dead-letter / cost log)
- **Automation:** `~/.claude/bin/overnight-summary.sh` + plist (06:00 SGT digest)
- **Automation:** `~/.claude/bin/overnight-link-checker.sh` + plist (04:45 SGT)
- **Automation:** `~/.claude/bin/competitor-dedup.sh` (content-hash dedup)
- **Automation:** `~/.claude/bin/agent-lib.sh` (lock / circuit-breaker / invocation log)
- **Automation:** `~/.claude/bin/agent-heatmap.sh` (weekly cold-agent report)
- **Automation:** `~/.claude/bin/clients-db-init.sh` (SQLite schema for clients/deals/touchpoints/life_events)
- **Automation:** `~/.claude/bin/client-health-score.sh` (0-100 score + churn flag)
- **Automation:** `~/.claude/bin/life-event-engine.sh` (consolidated birthday/MOP/anniversary)
- **Automation:** `~/.claude/bin/client-90d-checkin.sh` (post-deal nudge)
- **Automation:** `~/.claude/bin/post-otp-checklist.sh` (per-deal milestone checklist)
- **Automation:** `~/.claude/bin/client-milestone-message.sh` (client-facing draft)
- **Automation:** `~/.claude/bin/calendar-deal-sync.sh` (Google Calendar event spec)
- **Automation:** `~/.claude/bin/supply-cliff-alert.sh` (>500-unit TOP within 6mo alert)
- **Automation:** `~/.claude/bin/portfolio-valuation-refresh.sh` (monthly URA-based refresh)
- **Automation:** `~/.claude/bin/listing-watcher-midweek.sh` + plist (Tue/Thu 12:00)
- **Automation:** `~/.claude/bin/security-audit.sh` (J91 monthly hygiene)
- **Automation:** `~/.claude/bin/state-restore-test.sh` (J92 quarterly restore dry-run)
- **Automation:** `~/.claude/bin/mcp-health-check.sh` + plist (every 4h)
- **Automation:** `~/.claude/bin/subscription-audit.sh` (J98 monthly cost review)
- **Automation:** `~/.claude/bin/content-perf-feedback.sh` (closes the loop on engagement data)
- **Automation:** `~/.claude/bin/content-dedup.sh` (Jaccard-based draft dedup)
- **State:** `~/.claude/state/clients.db` (SQLite, supersedes scattered JSON state)
- **State:** `~/.claude/state/priorities.md` (daily top-3 + manager check-ins)
- **State:** `~/.claude/state/client-context-schema.md` (shared context, no re-collection)
- **State:** `~/.claude/state/agent-handoff-protocol.md` (deterministic chaining JSON)
- **State:** `~/.claude/state/content-machine/queue.json` (priority + deps + 4-Pillar)
- **State:** `~/.claude/state/content-machine/forbidden-topics.md` (hard veto list)
- **State:** `~/.claude/state/content-machine/reel-hooks-library.md` (proven hooks)
- **State:** `~/.claude/state/content-machine/format-specs.json` (data-driven slide counts etc.)
- **State:** `~/.claude/state/property-intel/{district-schema,listing-watchdog-config}.json`
- **State:** `~/.claude/state/deals/H_schema_extension.sql` (offer_history + deal_postmortems + commission view)
- **State:** `~/.claude/state/security-rotations.txt` (token rotation tracker)
- **SOPs:** `~/.claude/state/sops/otp-template-legal-review.md` (annual)
- **Versioning:** git init in `~/.claude/agents/` (B16) — agent definitions now version-controlled

### Changed
- Listing-watchdog noise threshold raised to 5% (config in `listing-watchdog-config.json`)
- Carousel default slide count = 7 (was 10) per `format-specs.json`

### Deferred (need agent-file edits with care)
- A6 (overnight-beautify human gate), C27 (testimonial-collector enhancement),
  G64/G65/G67/G69 (PSF leak capture, market-scout cap, rental multi-portal, en-bloc sentiment),
  H71/H77 (decoupling-strategist split output, net-proceeds CTA)

---

## [1.0.0] — Pre-2026-04-25
Site live at https://winfredquek.com (Vercel). Agent stack at ~92 agents.
History before this date is git log + tribal memory; CHANGELOG starts here.
