# Premium Newsletter Platform Spec — Substack vs Buttondown for Crestbrick Premium

**Decision date:** 2026-04-28
**Decision-maker:** Winfred Quek
**Tier:** Crestbrick Premium ($29/mo, 250-sub cap)
**Status:** Recommended platform — Buttondown

---

## The two-sentence summary

Substack is a publishing-as-marketing platform optimised for audience growth via discovery. Buttondown is a publishing-as-tool platform optimised for owners who already have an audience and want clean economics, control, and deliverability. Crestbrick Premium fits Buttondown.

---

## Side-by-side comparison

| Dimension | Substack | Buttondown |
|---|---|---|
| **Pricing model** | Free to start. Takes 10% of paid revenue + Stripe ~3% = ~13% all-in. | Flat monthly: $9 (Hobby, ≤100 subs), $29 (Standard, ≤1,000 subs), $79 (Pro). $0 take rate. |
| **At 250 paid subs × $29/mo = $7,250 MRR** | Substack take = $725/mo, Stripe ~$235/mo, net ~$6,290/mo | Buttondown $29/mo (Standard tier, well under cap), Stripe ~$235/mo, net ~$6,986/mo |
| **Annual delta in Crestbrick's favour** | — | **+$8,352/yr saved with Buttondown** |
| **Custom domain** | Available, but newsletter URL stays substack-flavoured (your-pub.substack.com or custom). Email send-from is via Substack infra. | Full custom domain (premium.crestbrick.sg). Custom send-from email (premium@crestbrick.sg). DNS records required (SPF, DKIM, DMARC, optional MX). |
| **Branding control** | Limited. Substack header, Substack footer, Substack reader app. Looks like Substack. | Full. Looks like Crestbrick. No vendor branding in email or web. |
| **Discovery / network effects** | Strong. Substack Recommends, Notes, Discover. Real source of free signups for some publications. | None. You bring your own audience. Zero network growth from the platform. |
| **Analytics** | Basic open/click, growth, paid conversion funnel. No deep segmentation. | Full open/click, segmentation tags, automation triggers, A/B subject lines, paid analytics, RSS/API exports. |
| **Deliverability** | Solid. Shared sending infrastructure. Some clients flag Substack as marketing. | Excellent when DNS is configured properly (SPF/DKIM/DMARC). Treats deliverability as a product, not an afterthought. |
| **Paid subscription handling** | Built-in. Stripe integration, automatic gating of posts. | Built-in. Stripe integration, automatic gating, founders' codes, comp subs. |
| **Subscriber export / portability** | Allowed but discouraged. You can leave with your list. | Trivial. CSV one click. Owner-first product philosophy. |
| **API access** | Limited. | Full REST API for automations (matches Crestbrick's launchd/Bash automation stack). |
| **Best for** | Writers building audience from zero | Operators with an audience monetising it |

---

## The pricing math, exactly

**Scenario A — Full 250 cap, Substack:**
- Gross MRR: 250 × $29 = $7,250
- Substack 10%: −$725
- Stripe ~3.4% + $0.50 per txn ≈ −$372
- Net to Winfred: ~$6,153/mo = **$73,836/yr**

**Scenario B — Full 250 cap, Buttondown:**
- Gross MRR: 250 × $29 = $7,250
- Buttondown flat (Standard, up to 1,000 subs): −$29
- Stripe ~3.4% + $0.50 per txn ≈ −$372
- Net to Winfred: ~$6,849/mo = **$82,188/yr**

**Annual difference: $8,352/yr in Crestbrick's favour with Buttondown.**

That delta funds an entire research subscription stack (REALIS, EdgeProp Pro, SRX, ACRA pulls) with budget left over.

---

## Why Buttondown wins for this specific case

1. **The audience is not coming from the platform.** Crestbrick Premium is sold to a curated funnel — existing free list, podcast guests, referrals. Substack's discovery network is irrelevant.
2. **The 250-sub cap is small enough that the take-rate model is punitive.** A 10% revenue share makes sense at scale (1m+ free readers, 5%+ paid conversion). At 250 subs, it is just margin loss with no marketing return.
3. **Brand control matters.** "Crestbrick Premium" must read as a Winfred Quek property research product, not a Substack writer's hobby. Email send-from premium@crestbrick.sg, web-from premium.crestbrick.sg.
4. **The automation stack (107 jobs, launchd, DB) needs API access.** Buttondown's API allows Crestbrick's existing infrastructure to push subscriber events, trigger automations, and reconcile against the internal CRM. Substack does not.
5. **Deliverability is non-negotiable for paid.** A subscriber paying $29/mo who finds the issue in promotions tab churns instantly. Custom DKIM/SPF on Buttondown via crestbrick.sg gives the cleanest deliverability path.

The only argument for Substack is the discovery network. For a 250-cap, paid, curated tier — that argument loses.

**Recommendation: Buttondown, Standard plan, $29/mo.**

---

## Signup steps — operational checklist

1. Create Buttondown account at buttondown.email using winfredquekoc@gmail.com.
2. Choose Standard plan ($29/mo). Skip Hobby — automation features needed are gated.
3. Connect Stripe (use existing Crestbrick Stripe account).
4. Configure custom domain: premium.crestbrick.sg (web) and crestbrick.sg (email send-from).
5. Configure DNS — see records below.
6. Set up paid tier: $29/mo, $290/yr (save $58 = 2 months free).
7. Create founders' coupon: FOUNDER30 — 30% off first 12 months, capped at first 30 redemptions.
8. Create refer-a-friend automation: 1 referral = 1 free month, 5 referrals = 6 free months.
9. Import free list via CSV. Send announcement email gating premium content.
10. Schedule launch sequence — see Migration Plan.

---

## DNS records required (Cloudflare or current Crestbrick DNS host)

Replace `crestbrick.sg` with the actual domain if different. Buttondown will issue exact record values during setup; the structure is:

| Type | Host | Value | Purpose |
|---|---|---|---|
| CNAME | premium | (Buttondown-provided target) | Custom domain web routing |
| TXT | crestbrick.sg | `v=spf1 include:_spf.buttondown.email ~all` (merge with existing SPF if present — only one SPF record allowed) | Sender authentication |
| CNAME | buttondown1._domainkey | (Buttondown-provided DKIM target) | DKIM signing |
| CNAME | buttondown2._domainkey | (Buttondown-provided DKIM target) | DKIM signing |
| TXT | _dmarc | `v=DMARC1; p=quarantine; rua=mailto:dmarc@crestbrick.sg; pct=100` | Anti-spoofing policy |
| TXT | (Buttondown verification host) | (Buttondown verification token) | Domain ownership |

**Critical:** Crestbrick already has email infrastructure. The existing SPF record must be merged, not replaced. Multiple SPF records will fail validation. Verify with `dig TXT crestbrick.sg` before and after.

After DNS propagation (usually <2 hours, allow 24), send a test issue to a Gmail, Outlook, and corporate inbox. Check headers for SPF=pass, DKIM=pass, DMARC=pass on all three.

---

## Migration plan

### Launch gating — when to flip the switch

Per the existing Premium Tier Charter, do not launch paid until **both** gates clear:

1. **Free list ≥ 5,000 active subscribers** (active = opened or clicked at least one of last 6 issues).
2. **Sustained ≥40% open rate over the trailing 8 issues** on the free list. Premium is a quality signal — if the free product is not earning attention, the paid product will not earn dollars.

Track weekly. Do not launch on a single hot week. Two consecutive months above both gates = green light.

### First-30-day playbook

**Pre-launch (Day −14 to Day 0):**

- Pre-write 8 archetype issues. These are the issues that map to the 8 most common subscriber archetypes Winfred sees in consult intake. Examples:
  1. The dual-income couple with one HDB and ABSD pressure (Issue 01 in samples folder)
  2. The 50+ owner-investor staring at lease decay (Issue 03 in samples folder)
  3. The cycle reader watching CCR/RCR/OCR spreads (Issue 02 in samples folder)
  4. The HDB upgrader with $400k cash and indecision
  5. The single-property owner thinking about en-bloc dynamics
  6. The expat PR weighing private vs leaving
  7. The retiree allocating between property and dividend stocks
  8. The new parent re-running the school-district math

Pre-writing 8 issues = 8 weeks of buffer. Premium subscribers churn fastest in weeks 2–6 if cadence slips. Buffer prevents that.

- Set up the welcome sequence (3 emails over 7 days): (1) welcome + how to read, (2) the 4-pillar framework primer, (3) ask: what's the one question you want answered first?

**Launch week (Day 0 to Day 7):**

- Open with **Founders' Member discount**: first 30 paid signups get the FOUNDER30 code (30% off first 12 months) + locked-in renewal at the same rate for life. Cap explicit. Communicate scarcity honestly.
- Send announcement to free list: 1 main email Day 0, 1 reminder Day 3, 1 last-call Day 6.
- Publish Issue 01 (one of the three samples) gated 50/50 — first half free, paywall after the table.

**Days 8–30:**

- Publish Issue 02 (full paywall — premium subs only). This sets the expectation: free list sees teasers, paid sees full.
- Launch **refer-a-friend** mechanic: every paid sub gets a unique link. 1 successful referral = 1 free month credit. 5 referrals = 6 months free. Communicates "this is a list worth being on" without spammy growth-hacking.
- Day 21 — first cohort review. Pull churn, open rate, click rate, refund requests. If churn >10% or NPS-equivalent feedback signals dissatisfaction, pause new growth and fix product before pushing harder.
- Day 30 — close founders' tier. Even if not all 30 slots filled, close on time. Scarcity maintained = trust maintained.

### Cap strategy at 250

Do not raise the cap. The cap is the product. When the 250th seat fills, switch to a **waitlist** with quarterly intake batches. Run a brief Q&A with Winfred at each batch open. The waitlist is itself a marketing artefact — it signals demand and qualifies the next cohort.

If demand consistently outruns supply over four consecutive quarters, do not raise the cap. Raise the price. Move from $29 to $39. Existing members stay grandfathered. This protects intimacy and forces the product to compound on quality, not headcount.

---

## Companion files

- `/Users/winfredquek/crestbrick-consult/products/newsletter/premium-samples/issue-01-decoupling-math.md`
- `/Users/winfredquek/crestbrick-consult/products/newsletter/premium-samples/issue-02-ccr-rcr-ocr-spread.md`
- `/Users/winfredquek/crestbrick-consult/products/newsletter/premium-samples/issue-03-lease-decay-trap.md`
- `/Users/winfredquek/crestbrick-consult/products/newsletter/premium-tier-charter.md` (updated, references this spec and the three samples)
