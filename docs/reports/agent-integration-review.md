# Agent Integration Review

**Date:** 2026-04-27
**Scope:** 94 subagent definitions vs ~480 automation scripts in `~/.claude/bin/`
**Goal:** Wire existing agents into existing triggers — more leverage, zero new work for Winfred.

---

## Headline finding

The automation stack is **trigger-rich, agent-poor**. Roughly 90% of scripts fire a Telegram alert and stop. They detect events perfectly, but they don't invoke the specialist agent who could turn the event into action (a draft message, a calculator run, a client-specific impact list). The wiring gap is the single biggest unlock.

---

## Section 1 — Top 10 Integration Gaps

| # | Script | Event detected | Should invoke | Current behavior | Patch |
|---|---|---|---|---|---|
| 1 | `wa-workflow.sh` | New WA inbound, qualified as lead | `lead-qualifier` then `nurture-drip-agent` (cold) / `relationship-manager` (warm) | Inline JSONL prompt does qualification but never hands off to specialist agents for follow-on assets (drip plan, viewing offer) | After Telegram approval at `wa-workflow.sh:600`, also enqueue `claude -p "@lead-qualifier deep-score @<slug>"` for any HOT/WARM lead. Output goes into client folder, no Telegram noise. |
| 2 | `anniversary-touch.sh` | Client purchase anniversary today | `relationship-manager` + `portfolio-tracker` | Only sends generic Telegram draft ("How's it going?") | Replace inline DRAFT block (lines 71-75) with `claude -p "@relationship-manager anniversary-message client=<slug> years=<n>"`. Have `portfolio-tracker` attach current valuation delta vs purchase price. |
| 3 | `mortgage-rate-monitor.sh` | 3M SORA drops >0.2% | `refinance-trigger` (already exists!) on per-client basis | Line 112 calls the script but `refinance-trigger.sh` is the bash file, not the agent. Agent never invoked. | Change line 112 from `bash "$REFINANCE_TRIGGER"` to `claude -p "@refinance-trigger scan-clients sora=$SORA_3M delta=$DELTA"` so the agent produces personalised refi math per affected mortgage holder. |
| 4 | `cooling-measure-blast.sh` | MAS/HDB/URA policy keyword detected | `cooling-measure-scenario` agent | Drafts a single generic broadcast | After signal at line 67, invoke `cooling-measure-scenario` with the specific measure → it should produce a per-client impact tier (foreigner ABSD-exposed, decoupling-vulnerable, etc.) before broadcast. |
| 5 | `bto-alert.sh` | New BTO exercise announced | `hdb-upgrader-hunter` + `district-affordability-matrix` | Sends one Telegram alert | After alert, invoke `hdb-upgrader-hunter` to produce a list of MOP-met HDB clients in the affected estate, plus `district-affordability-matrix` to compare BTO vs resale upgrade math. |
| 6 | `after-hours-lead-flag.sh` | Lead came in after 22:00 SGT | `lead-qualifier` + `whatsapp-writer` | Just lists names + previews | Pipe each overnight lead through `whatsapp-writer` to pre-draft a "good morning, saw your message overnight" reply that's attached to the morning Telegram digest as ready-to-send. |
| 7 | `overnight-competitor-tracker.sh` | Top SG agent posts new positioning claim | `competitor-scanner` + `content-director` | Sends Telegram digest only | After digest, invoke `content-director` with the 3 noteworthy items → ask for "counter-positioning angle for Winfred's next post." Bridges intel into editorial calendar. |
| 8 | `hot-lead-daily-nudge.sh` | Hot lead untouched >2 days | `negotiation-coach` or `nurture-drip-agent` | Lists names + days, no draft | For each ⚠️ flagged lead at line 43, invoke `whatsapp-writer` with last touchpoint context → attach a draft re-engage message to the alert. Winfred approves with one tap. |
| 9 | `warm-lead-nurture-trigger.sh` | Warm lead 7+ days no touch | `nurture-drip-agent` | Sends Telegram nudge per-client | The nudge already has client context (ICP, citizenship, days). Invoke `nurture-drip-agent` to draft the actual message bucket-appropriate for that ICP rather than asking Winfred to write it. |
| 10 | `api-health-checker.sh` | External API failure | `developer` + `chief-operating-officer` | Telegram alert only | On 2+ failures, auto-invoke `developer` with the failed endpoints list to suggest fallback URLs / retry windows; `coo` to flag downstream scripts that will break today. |

---

## Section 2 — Under-utilised Agents

These agents exist but have no scheduled trigger. Each one is a high-value asset sitting idle.

| Agent | Why it's idle | Proposed trigger |
|---|---|---|
| `cashflow-stress-tester` | No script invokes it | Cron: monthly 1st @ 09:00 SGT — run for every active client with mortgage, alert if SORA+1% scenario breaks TDSR |
| `cpf-retirement-impact-projector` | Never invoked | Cron: quarterly — for each client age 45+, project CPF OA depletion at retirement vs current property usage |
| `decoupling-strategist` | Defined but no trigger | Hook into `cooling-measure-blast.sh` when ABSD changes detected; also cron weekly for joint-name HDB clients with private property ambitions |
| `enbloc-probability-scorer` | No script | Cron: monthly — score every client's current property if condo age >25 years; alert any moving from "low" to "medium" |
| `school-catchment-agent` | No trigger | Hook into `client-lifecycle` onboarding — auto-run for any client with kids age 5-11 in profile |
| `district-affordability-matrix` | Manual only | Hook into `mortgage-rate-monitor.sh` — when rates move materially, regenerate the matrix and Telegram-attach |
| `private-seller-net-proceeds` | Manual only | Hook into `seller-hunter` outputs — every new seller-intent lead gets auto-net-proceeds calc attached |
| `referral-engine` | No trigger | Cron: monthly — scan clients with deal-stage=completed, NPS>=8, no referral generated, send Winfred a "ask for referral now" digest |
| `testimonial-collector` | No trigger | Hook into `post-deal-30d-checkin.sh` — auto-invoke for completed deals at day 30 |
| `case-study-generator.sh` exists but `case-study-generator` agent equivalent isn't wired into closed deals | Cron: monthly review of `deals.stage=completed` past 30 days, draft case study with PII removed |
| `engagement-analyst` | No trigger | Hook into weekly digest — analyse last 7 days IG/LinkedIn metrics, flag underperformers |
| `brand-auditor` | Manual only | Cron: weekly Sunday — sweep last 7 days of WhatsApp drafts + posts + newsletters for off-brand drift |
| `family-office-relationship` | No trigger | Cron: monthly — if any FO contact in DB, generate touch suggestion |
| `repurposing-coordinator` | No trigger | Hook into `daily-newsletter.sh` post-publish — auto-spec 3 derivative formats (carousel, reel script, LinkedIn long-form) |
| `headline-generator` | No trigger | Hook into `overnight-content-writer.sh` — generate 5 headline variants per draft |
| `viewing-feedback-logger.sh` runs but no `negotiation-coach` follows up on poor-feedback viewings | Hook: any viewing with rating <3, auto-invoke `negotiation-coach` for a price-strategy adjustment memo |

---

## Section 3 — Workflow Simplifications (chain agents behind one launcher)

### S1. New-lead intake chain (currently 3 disconnected steps)

Today: `wa-workflow.sh` qualifies → Winfred approves draft → nothing else fires automatically.

Proposed launcher `new-lead-pipeline.sh`:
```
1. lead-qualifier → deep-score
2. school-catchment-agent → if kids
3. affordability-modeler → if budget signal present
4. nurture-drip-agent → schedule 7/14/30 day touches
```
One bash file, one approval, four agents do work. File path: `~/.claude/bin/new-lead-pipeline.sh` (new).

### S2. Closed-deal celebration chain

Today: `post-deal-30d-checkin.sh` runs but standalone.

Proposed wrapper invokes in sequence:
```
1. testimonial-collector → ask for review
2. case-study-generator → draft anonymised case study
3. referral-engine → identify 3 referral asks
4. google-review-request.sh → existing script
5. relationship-manager → schedule 90-day, 180-day, anniversary touches
```

### S3. Policy-change response chain

Today: `cooling-measure-blast.sh` drafts one broadcast.

Proposed `policy-change-response.sh`:
```
1. cooling-measure-scenario → per-client impact tier
2. decoupling-strategist → flag joint-name clients now exposed
3. cpf-optimizer → recompute CPF math under new rules
4. content-director → spec a same-day explainer post
5. wa-broadcast-scheduler → tier-segmented broadcasts (not one-size-fits-all)
```

### S4. Newsletter → repurposing chain

Today: `daily-newsletter.sh` publishes, ends.

Proposed: chain `repurposing-coordinator` → `instagram-writer` → `linkedin-writer` → `carousel-designer` so one brief becomes 4 outputs across channels by 9am.

### S5. Mortgage-rate move chain

Today: `mortgage-rate-monitor.sh` fires `refinance-trigger.sh` (bash) once.

Proposed:
```
1. refinance-trigger (agent) → per-client refi math
2. loan-comparison-agent → 3 best banks today
3. mortgage-break-even-calculator.sh → existing
4. whatsapp-writer → personalised draft per affected client
```

---

## Section 4 — Recommended new cron hooks (agents on schedule)

| Agent | Suggested schedule | Why |
|---|---|---|
| `portfolio-auditor` | Weekly Sunday 21:00 SGT | Sweep all active client folders, flag stale data (no contact >30d, missing CPF, missing mortgage) |
| `cea-compliance-checker` | Weekly Friday 17:00 SGT | Audit week's outbound WA + posts for CEA breaches before weekend |
| `legal-compliance` | Monthly 1st | PDPA + OTP audit across active client folders |
| `cashflow-stress-tester` | Monthly 1st | All mortgaged clients — SORA+1% scenario |
| `enbloc-probability-scorer` | Monthly 15th | All condo clients — refresh probability score |
| `family-office-hunter` | Weekly Tuesday 09:00 | Cold pipeline thinning auto-detect |
| `business-owner-hunter` | Weekly Wednesday 09:00 | Same |
| `expat-hunter` | Weekly Thursday 09:00 | Same |
| `market-cycle-indicator.sh` exists but `market-scout` agent never reads its output | Daily 08:30 — `market-scout` reads the indicator and produces a 1-line "where we are in the cycle" for the morning brief |
| `brand-auditor` | Weekly Sunday 18:00 | Quality gate before next week's posts |
| `engagement-analyst` | Weekly Monday 06:00 | Last-week metrics → input to `content-director` Monday planning |

---

## Section 5 — Top 5 Quick Wins (this week)

Ranked by ROI per hour of wiring effort.

### QW1. Wire `mortgage-rate-monitor.sh` → `refinance-trigger` agent (NOT bash)
**Effort:** 5 min. **Value:** Every rate drop produces personalised refi math for every affected client automatically.
**Patch:** `~/.claude/bin/mortgage-rate-monitor.sh` line 112. Replace `bash "$REFINANCE_TRIGGER"` with `claude -p --permission-mode bypassPermissions "@refinance-trigger scan-all-clients sora=${SORA_3M} delta=${DELTA}" >> "$LOG" 2>&1 &`.

### QW2. Wire `anniversary-touch.sh` → `relationship-manager` for personalised drafts
**Effort:** 15 min. **Value:** Generic anniversary message becomes specific (mentions their actual property gain, market context, next decision point).
**Patch:** Replace lines 71-75 in `anniversary-touch.sh` with a `claude -p` call that passes `client_slug`, `years_ago`, `address` and asks the agent for a 3-line draft.

### QW3. Auto-attach drafts to `hot-lead-daily-nudge.sh`
**Effort:** 20 min. **Value:** Winfred goes from "I see Tan is overdue" to "approve & send" in one tap.
**Patch:** In the loop at line 40-45, for each ⚠️ lead invoke `whatsapp-writer` with last-message context, append draft to the LIST variable.

### QW4. Build `new-lead-pipeline.sh` chain (S1 above)
**Effort:** 45 min. **Value:** Every qualifying WA lead produces a full intake package (deep score, drip plan, affordability if applicable) inside their client folder by the time Winfred clicks Approve. He approves once, four agents work in parallel.
**File to create:** `~/.claude/bin/new-lead-pipeline.sh` triggered from `wa-workflow.sh` after pending entry is created.

### QW5. Wire `cooling-measure-blast.sh` → `cooling-measure-scenario` for tiered broadcast
**Effort:** 30 min. **Value:** Foreigner clients get the ABSD-specific impact, HDB clients get the LTV-specific message, instead of one generic broadcast that mostly misses.
**Patch:** Insert before line 72 (`log "cooling measure signal detected — drafting broadcast"`): a call to `cooling-measure-scenario` that returns segmented client lists, then loop the broadcast drafting per segment.

---

## Notes & caveats

- All wiring should respect the org-chart routing: scripts shouldn't call worker agents directly. Route through the relevant manager (`financial-advisor`, `client-lifecycle`, `property-intelligence`, `deals-manager`, `lead-generation`, `social-media-manager`).
- Every agent invocation should answer WHY-HOW-WHAT inside its prompt; otherwise they generate noise, not value.
- `claude -p` calls inside scripts should run with `--permission-mode bypassPermissions` and a hard timeout (existing `wa-workflow.sh` uses 480s — copy the pattern).
- Several agents (e.g. `family-office-relationship`, `enbloc-probability-scorer`) only become valuable once the underlying client DB is rich enough — verify data quality before scheduling.
- `~/.claude/bin/` has 1,340 entries (incl. logs); the 480 figure is roughly the `.sh`/`.py` count. No new agents needed — focus is utilisation, not creation.

**Bottom line:** wiring just QW1-QW5 this week converts ~5 idle agents into automated specialists. Estimated time investment: ~2 hours for ~20 hours/week of recurring leverage.
