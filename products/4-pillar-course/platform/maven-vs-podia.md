# Platform Spec — Maven vs Podia for the 4-Pillar Investor Course

**Decision needed:** Pick the platform to host the 4-Pillar Investor Course (6 modules, ~25 lessons, ~$499 evergreen + $1,499 cohort variants).
**Decision date:** 2026-04-28
**Owner:** Winfred Quek
**Recommendation:** **Podia for the evergreen $499 base course. Run cohort variants ($1,499) directly off Podia + a Discord/Circle community layer. Skip Maven for v1.**

---

## At-a-glance comparison

| Dimension | **Maven** | **Podia** |
|---|---|---|
| **Best fit for** | Live cohort-based courses with high facilitator involvement | Evergreen self-paced courses, digital downloads, communities |
| **Pricing model** | 10% revenue share + payment processing | Flat $39/mo (Mover) or $89/mo (Shaker); $0 transaction fee on paid plans |
| **Format strength** | Cohorts, live Zoom, weekly homework, peer learning | Self-paced video, drip schedules, file downloads, email |
| **Self-paced support** | Limited — built around cohort cycles | Native — this is the core product |
| **Cohort support** | Native — this is the core product | Workable but you build it yourself with drip + community |
| **Pricing flexibility** | Tied to cohort dates and seat caps | Full flexibility — tiers, bundles, payment plans, coupons |
| **Community / Q&A** | Built-in cohort community per run | Native community feature on Shaker; or pair with Discord/Circle |
| **Email marketing** | Basic | Native email + automation included |
| **Sales page builder** | Templated, cohort-style | Full storefront, customisable, integrates with own domain |
| **Singapore-specific** | USD-denominated; SGD pricing requires workarounds | Supports SGD pricing; Stripe SG works natively |
| **Discoverability** | Maven marketplace (some inbound) | None — you drive all traffic |
| **Switching cost later** | High — cohorts fragment if you migrate | Low — content lives in your account, exportable |

---

## Why Podia for v1

The 4-Pillar Course shape:

- **Format:** 6 modules, ~25 video lessons, workbook, 4 Excel templates, 3 case studies, quizzes, email sequences. **This is an evergreen self-paced product with a cohort upsell** — not a cohort-first product.
- **Pricing:** $499 base / $799 + 1:1 / $1,499 cohort. The $499 is the volume tier; cohort is the premium tier. Maven optimises for the wrong tier.
- **Audience:** Singaporean upgraders + investor-minded HDB owners. They pay in SGD. They expect a polished branded storefront under the Crestbrick / Winfred Quek umbrella, not a Maven-marketplace listing.
- **Operations:** Solo operator + small Crestbrick team. Maven's revenue share + cohort cadence demands more operational intensity per dollar than Podia's flat fee.

**Math check at conservative volume.** Assume 100 sales of the $499 base in year one (= $49,900 gross).

- **On Maven:** 10% revenue share = $4,990. Plus payment processing ~3% = $1,500. Total platform cost ≈ **$6,490**.
- **On Podia (Shaker, $89/mo × 12):** $1,068/year. Plus Stripe SG ~3.4% = $1,700. Total platform cost ≈ **$2,770**.

Podia saves ~$3,700 at this volume. The savings grow linearly with revenue. At 300 sales/year ($150K gross), Podia saves ~$11K vs Maven.

**Where Maven *would* win** is if the primary product were a $2K+ cohort with 30+ students per run, where the marketplace inbound and the cohort tooling reduce CAC and ops more than 10% / processing costs. That's not where the 4-Pillar Course lives. The cohort variant is the *upsell*, not the centre of gravity.

---

## Cohort variant — how to run it on Podia

The $1,499 cohort SKU (10 students, weekly live Q&A, 6 weeks) does **not** require Maven. Stack:

1. **Podia** — hosts the course content, gates access to cohort SKU buyers, handles payment.
2. **Zoom** (existing Crestbrick account) — weekly live Q&A.
3. **Discord** or **Circle** — cohort-only community channel. Discord free tier works; Circle ($89/mo Basic) is more polished.
4. **Calendly** — schedule the optional 1:1 Blueprint review for the $799 tier.

This stack runs ~$90–$180/month and replicates Maven's cohort experience for our cohort size.

---

## Recommendation

**Sign up for Podia Shaker ($89/mo).** Defer Maven entirely — revisit only if cohort sales eclipse evergreen sales by 2:1 in any quarter, which would be a different business shape than the current plan.

### Signup steps (when ready to commit)

1. Go to **podia.com**, click "Get started for free."
2. Sign up with `winfredquekoc@gmail.com`. Use a strong password; store in 1Password.
3. During onboarding, choose store name: **"Winfred Quek"** (per brand voice memory — primary brand is the personal name, not "Crestbrick").
4. Connect custom domain — proposed: `learn.winfredquek.com` or `course.winfredquek.com` (subdomain off the main site).
5. Plan selection: start on the free trial; upgrade to **Shaker ($89/mo)** before publishing — Shaker unlocks unlimited courses, native community, email automation, and the affiliate program (useful later for partner referrals).
6. Connect **Stripe Singapore** (existing Crestbrick Stripe account). Confirm SGD pricing displays correctly on a test product.
7. Create a private test product, run a $1 transaction end-to-end, confirm payout to the SG bank account.
8. Build out Module 1 first, gate Modules 2–6 as drip-released content (Podia drip feature) for the cohort variant; immediate full-access for the evergreen $499 buyer.

### Credentials & access (to set up on signup day)

- Login email: `winfredquekoc@gmail.com`
- Backup admin: TBD — recommend adding a second team admin for continuity
- Password: store in 1Password under "Podia — Winfred Quek storefront"
- Stripe: link to the existing Crestbrick Stripe SG account; do not create a new one
- Domain: route subdomain via existing DNS provider (Cloudflare? confirm)

### Open items before signup

- Confirm Stripe SG account is the right entity for course sales (vs personal sole prop) — quick check with accountant.
- Confirm CEA compliance review on course content before public sale (already flagged in original outline).
- Decide subdomain final form: `learn.` vs `course.` vs `school.`.

---

*Winfred Quek · CEA R073319H · Crestbrick Consulting*
*4-Pillar Investor Course · Platform Decision Memo*
