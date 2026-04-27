# K451-K518 launchd plist plan

Generated 2026-04-27. **Not yet loaded into launchctl** — pending Winfred review.

All plists live under `~/Library/LaunchAgents/com.crestbrick.<script>.plist`.

All use `TZ=Asia/Singapore`, `RunAtLoad=false`, and stdout/stderr to `~/.claude/bin/<script>.launchd.{out,err}.log`.


## Schedule table

| K-num | Script | Schedule | Cron equivalent | Disabled? |
|---|---|---|---|---|
| K451 | absd-cohort-tracker | Daily 08:00 SGT | `0 8 * * *` | no |
| K452 | cooling-measure-impact-digest | Weekly Sun 21:00 SGT | `0 21 * * 0` | no |
| K453 | absd-clawback-risk-monitor | Daily 08:01 SGT | `1 8 * * *` | no |
| K454 | ssd-window-watcher | Daily 08:02 SGT | `2 8 * * *` | no |
| K455 | hdb-mop-graduation-alert | Daily 08:03 SGT | `3 8 * * *` | no |
| K456 | hdb-lease-decay-monitor | Daily 08:04 SGT | `4 8 * * *` | no |
| K457 | hdb-upgrader-cohort-builder | Monthly day 1 08:00 SGT | `0 8 1 * *` | no |
| K458 | hdb-bto-ballot-reminder | Daily 08:05 SGT | `5 8 * * *` | no |
| K459 | ec-mop-private-conversion-watch | Monthly day 1 08:01 SGT | `1 8 1 * *` | no |
| K460 | post-key-collection-checkin | Daily 08:06 SGT | `6 8 * * *` | no |
| K461 | client-birthday-warmth | Daily 08:07 SGT | `7 8 * * *` | no |
| K462 | stale-lead-resurrection | Daily 08:08 SGT | `8 8 * * *` | no |
| K463 | referral-ask-trigger | On-demand | `(disabled)` | yes |
| K464 | post-viewing-followup-nudge | Daily 08:09 SGT | `9 8 * * *` | no |
| K465 | client-tier-rebalancer | Weekly Sun 21:05 SGT | `5 21 * * 0` | no |
| K466 | ghosted-prospect-detector | Daily 08:10 SGT | `10 8 * * *` | no |
| K467 | weekly-pipeline-velocity | Weekly Sun 21:10 SGT | `10 21 * * 0` | no |
| K468 | lead-source-conversion-rates | Monthly day 1 08:02 SGT | `2 8 1 * *` | no |
| K469 | commission-forecast-rolling-90d | Weekly Mon 08:00 SGT | `0 8 * * 1` | no |
| K470 | deal-stage-stuck-alert | Daily 08:11 SGT | `11 8 * * *` | no |
| K471 | win-rate-by-property-type | Quarterly Jan/Apr/Jul/Oct day 1 08:00 SGT | `0 8 1 1,4,7,10 *` | no |
| K472 | first-meeting-to-deal-time | Monthly day 1 08:03 SGT | `3 8 1 * *` | no |
| K473 | ura-quarterly-data-watcher | Daily 08:12 SGT | `12 8 * * *` | no |
| K474 | enbloc-activity-radar | Daily 08:13 SGT | `13 8 * * *` | no |
| K475 | new-launch-balloting-tracker | Daily 08:14 SGT | `14 8 * * *` | no |
| K476 | preview-weekend-prep | Weekly Fri 17:00 SGT | `0 17 * * 5` | no |
| K477 | psf-trend-alerts-by-district | Weekly Sun 21:15 SGT | `15 21 * * 0` | no |
| K478 | vacancy-rate-watcher-by-region | Monthly day 1 08:04 SGT | `4 8 1 * *` | no |
| K479 | cea-disclosure-reminder | Annual Jan 15 09:00 SGT | `0 9 15 1 *` | no |
| K480 | pdpa-consent-audit | Quarterly Jan/Apr/Jul/Oct day 1 08:01 SGT | `1 8 1 1,4,7,10 *` | no |
| K481 | cea-cpd-hours-tracker | Monthly day 1 08:05 SGT | `5 8 1 * *` | no |
| K482 | pdpa-data-retention-purge | Monthly day 1 08:06 SGT | `6 8 1 * *` | no |
| K483 | estate-agent-licence-renewal | Daily 08:15 SGT | `15 8 * * *` | no |
| K484 | subscription-cost-audit | Monthly day 1 08:07 SGT | `7 8 1 * *` | no |
| K485 | log-rotation | Weekly Sun 03:00 SGT | `0 3 * * 0` | no |
| K487 | state-file-cleanup | Weekly Sun 03:30 SGT | `30 3 * * 0` | no |
| K488 | launchd-job-health-report | Weekly Sun 21:20 SGT | `20 21 * * 0` | no |
| K489 | db-integrity-check | Weekly Sun 04:00 SGT | `0 4 * * 0` | no |
| K490 | telegram-delivery-monitor | Every 4h | `every 4h` | no |
| K491 | disk-usage-alert | Every 4h | `every 4h` | no |
| K492 | secrets-leakage-scanner | Daily 08:16 SGT | `16 8 * * *` | no |
| K493 | post-due-reminder | Daily 20:00 SGT | `0 20 * * *` | no |
| K494 | content-repurpose-prompt | Weekly Sun 21:25 SGT | `25 21 * * 0` | no |
| K495 | instagram-story-cadence-monitor | Every 4h | `every 4h` | no |
| K496 | youtube-shorts-backlog | Weekly Mon 09:00 SGT | `0 9 * * 1` | no |
| K497 | blog-publishing-streak-watch | Weekly Sun 21:30 SGT | `30 21 * * 0` | no |
| K498 | lease-expiry-alert | Daily 08:17 SGT | `17 8 * * *` | no |
| K499 | rental-market-rent-comps | Monthly day 1 08:08 SGT | `8 8 1 * *` | no |
| K500 | tenancy-renewal-window | Daily 08:18 SGT | `18 8 * * *` | no |
| K501 | rental-yield-vs-target | Quarterly Jan/Apr/Jul/Oct day 1 08:02 SGT | `2 8 1 1,4,7,10 *` | no |
| K502 | deposit-refund-due-tracker | Daily 08:19 SGT | `19 8 * * *` | no |
| K503 | mortgage-rate-alert | Daily 08:20 SGT | `20 8 * * *` | no |
| K504 | loan-refinance-window-watch | Daily 08:21 SGT | `21 8 * * *` | no |
| K505 | cpf-accrued-interest-projector | Quarterly Jan/Apr/Jul/Oct day 1 08:03 SGT | `3 8 1 1,4,7,10 *` | no |
| K506 | tdsr-headroom-monitor | Weekly Mon 08:05 SGT | `5 8 * * 1` | no |
| K507 | loan-tenure-shorten-suggestion | On-demand | `(disabled)` | yes |
| K508 | fixed-vs-floating-comparator | Weekly Sun 21:35 SGT | `35 21 * * 0` | no |
| K509 | show-flat-opening-radar | Daily 08:22 SGT | `22 8 * * *` | no |
| K510 | developer-stock-clearing-watch | Daily 08:23 SGT | `23 8 * * *` | no |
| K511 | investor-portfolio-review-prompt | Annual Feb 1 09:00 SGT | `0 9 1 2 *` | no |
| K512 | decoupling-eligibility-screener | On-demand | `(disabled)` | yes |
| K513 | trust-structure-flag | On-demand | `(disabled)` | yes |
| K514 | estate-planning-conversation-prompt | Monthly day 1 08:09 SGT | `9 8 1 * *` | no |
| K515 | foreign-buyer-stamp-duty-update | Daily 08:24 SGT | `24 8 * * *` | no |
| K516 | viewing-cluster-optimizer | Daily 07:00 SGT | `0 7 * * *` | no |
| K517 | open-house-rsvp-aggregator | On-demand | `(disabled)` | yes |
| K518 | weekly-winfred-summary | Weekly Sun 21:40 SGT | `40 21 * * 0` | no |

## Bucket summary

- **Total plists:** 67
- **Disabled (on-demand):** 5
- **Daily:** 27
- **Weekly:** 16
- **Monthly:** 10
- **Quarterly:** 4
- **Annual:** 2
- **Interval:** 3
- **On-demand:** 5

## Stagger windows

- **Daily 08:xx SGT batch:** 08:00 through 08:24 (one job per minute) to avoid load spike.
- **Monthly day-1 08:xx SGT batch:** 08:00 through 08:09 (one job per minute).
- **Quarterly day-1 08:xx SGT batch:** 08:00 through 08:03 (one job per minute).
- **Sunday 21:xx SGT digest batch:** 21:00 through 21:40 (5-minute spacing).
- **Weekly maintenance (off-hours):** log-rotation Sun 03:00, state-file-cleanup Sun 03:30, db-integrity Sun 04:00.

## On-demand (Disabled=true) jobs — require manual trigger

These plists exist on disk but won't auto-fire. Trigger via `launchctl kickstart gui/$UID/<label>`:
- `K463` `referral-ask-trigger` — takes CLI args / context-specific
- `K507` `loan-tenure-shorten-suggestion` — takes CLI args / context-specific
- `K512` `decoupling-eligibility-screener` — takes CLI args / context-specific
- `K513` `trust-structure-flag` — takes CLI args / context-specific
- `K517` `open-house-rsvp-aggregator` — takes CLI args / context-specific

## To load (after review)

```bash
for f in ~/Library/LaunchAgents/com.crestbrick.k4{51..85}-*.plist \
        ~/Library/LaunchAgents/com.crestbrick.k4{87..99}-*.plist \
        ~/Library/LaunchAgents/com.crestbrick.k5{00..18}-*.plist; do
  launchctl bootstrap gui/$UID "$f"
done
```
