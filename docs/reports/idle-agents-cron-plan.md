# Idle-Agent Cron Plan

**Date:** 2026-04-28
**Source:** `agent-integration-review.md` Section 4

Wires 9 previously-idle Claude subagents to launchd cron triggers. All wrappers + plists are written and validated. **None loaded** — Winfred reviews schedules and loads when ready.

## Schedule table

| # | Agent | Wrapper | Plist (label) | SGT schedule | UTC cron equivalent |
|---|---|---|---|---|---|
| 1 | portfolio-auditor | `~/.claude/bin/agent-portfolio-auditor.sh` | com.crestbrick.agent-portfolio-auditor | Sunday 21:00 | Sunday (Wkday=0) 13:00 |
| 2 | cea-compliance-checker | `~/.claude/bin/agent-cea-compliance-checker.sh` | com.crestbrick.agent-cea-compliance-checker | Friday 17:00 | Friday (Wkday=5) 09:00 |
| 3 | cashflow-stress-tester | `~/.claude/bin/agent-cashflow-stress-tester.sh` | com.crestbrick.agent-cashflow-stress-tester | Day-1 of month, 09:00 | Day=1, 01:00 |
| 4 | enbloc-probability-scorer | `~/.claude/bin/agent-enbloc-probability-scorer.sh` | com.crestbrick.agent-enbloc-probability-scorer | Day-15 of month, 09:00 | Day=15, 01:00 |
| 5 | brand-auditor | `~/.claude/bin/agent-brand-auditor.sh` | com.crestbrick.agent-brand-auditor | Sunday 18:00 | Sunday (Wkday=0) 10:00 |
| 6 | engagement-analyst | `~/.claude/bin/agent-engagement-analyst.sh` | com.crestbrick.agent-engagement-analyst | Monday 06:00 | Sunday (Wkday=0) 22:00 |
| 7 | market-scout-cycle | `~/.claude/bin/agent-market-scout-cycle.sh` | com.crestbrick.agent-market-scout-cycle | Daily 08:30 | Daily 00:30 |
| 8 | repurposing-coordinator | `~/.claude/bin/agent-repurposing-coordinator.sh` | (no plist — trigger-only) | manual / called by `newsletter-repurpose.sh` | n/a |
| 9 | referral-engine | `~/.claude/bin/agent-referral-engine.sh` | com.crestbrick.agent-referral-engine | Day-1 of month, 10:00 | Day=1, 02:00 |

## Stagger / quiet hours

- All schedules respect quiet hours 22:00–08:00 SGT for chatty agents (Telegram digest output).
- engagement-analyst at Monday 06:00 SGT is the earliest — it runs unattended to seed content-director's Monday planning, output ready when Winfred wakes.
- market-scout-cycle at 08:30 SGT lands just after the daily newsletter so its cycle read can be read in context.
- Day-1 monthlies stagger 1 hour: cashflow-stress-tester at 09:00, referral-engine at 10:00.

## Agent-name fallback note

The integration review referenced `market-scout-cycle` — that exact agent name does NOT exist. The wrapper invokes `@market-scout` (the canonical agent) with a cycle-specific prompt. Logged for transparency.

## Pattern (every wrapper)

```bash
#!/usr/bin/env bash
# ...
set -uo pipefail
ENV_FILE="$HOME/.telegram-bot.env"
LOG=...; STATE=...; CLAUDE_BIN=...; MUTE_FLAG=...

# Mute flag → exit 0
# Idempotency state file (writes today's date when complete)
# claude -p with --output-format text --permission-mode bypassPermissions, 240-600s timeout
# Telegram digest with head-of-output preview + path to full output
# Output saved at ~/.claude/state/agent-{name}/YYYY-MM-DD.md
```

## Load command (run when ready)

```bash
for plist in ~/Library/LaunchAgents/com.crestbrick.agent-{portfolio-auditor,cea-compliance-checker,cashflow-stress-tester,enbloc-probability-scorer,brand-auditor,engagement-analyst,market-scout-cycle,referral-engine}.plist; do
  [ -f "$plist" ] && launchctl load -w "$plist" && echo "loaded $plist"
done
```

To unload (if any agent goes rogue):
```bash
launchctl unload -w ~/Library/LaunchAgents/com.crestbrick.agent-NAME.plist
```

## To verify a single agent before loading

```bash
bash ~/.claude/bin/agent-NAME.sh
# Check ~/.claude/bin/agent-NAME.log
# Check Telegram for the digest
# Check ~/.claude/state/agent-NAME/$(date +%Y-%m-%d).md for the full output
```

## Files validated

- 9 wrapper scripts: all `bash -n` clean, all chmod +x
- 8 plists: all `plutil -lint` OK
- repurposing-coordinator deliberately has no plist (trigger-based)

## Outputs land at

`~/.claude/state/agent-{name}/YYYY-MM-DD.md` per agent run.
