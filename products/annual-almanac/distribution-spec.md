# SG Property Almanac — Distribution Spec

**Volume 1 release: 15 January 2027** (covers calendar 2026)
**Cadence: annual**, with quarterly addenda for paid tier (decision deferred to post-V1)

---

## 1. Funnel

### Free tier (default for Volume 1)
- Email-gated PDF download
- Hosted on `winfredquek.com/almanac`
- Opt-in form requires: email, first name, "what brought you here" (single-select: own HDB / own private / multiple properties / not yet bought / agent or industry)
- Auto-tag in newsletter platform: `lead_magnet:almanac_2026`
- Double opt-in confirmation, then immediate PDF delivery email
- Kept free until Volume 1 list growth data is in (target: >2,000 net new subscribers in first 60 days). Decision on paid tier for Volume 2 made by 31 March 2027.

### Paid tier (deferred — A/B for V2)
- $49 PDF + Excel data tables (district PSFs, launch sell-through, BTO history)
- Quarterly addenda PDFs (4–6 pages each) bundled
- Stripe checkout from same `/almanac` page; free tier preserved as 8-page executive summary lead magnet

---

## 2. `winfredquek.com/almanac` page spec

- Above-the-fold: cover image of the PDF, one-line value prop, single-field email capture (first name optional, address required)
- Below-the-fold:
  - Table of contents preview (Chapters 1–8)
  - Pull quotes from 3–4 most opinionated takes
  - About Winfred (50 words, CEA line, Crestbrick)
  - "What you'll get" — 5 bullets
  - "Past editions" placeholder for Volume 2 onward
  - Disclaimer footer (CEA R073319H · Crestbrick · PropNex)
- No social-proof testimonials in V1 — let the document speak
- OG card + Twitter card with cover image
- Tracking: UTM-aware so we know which channel pulled which signups

---

## 3. Newsletter platform integration

Existing platform (whichever is current — ConvertKit / Beehiiv / Mailerlite):
- New form: `almanac-2026-optin`
- New automation: `almanac-2026-delivery`
  - Trigger: form submit + double opt-in confirmed
  - Step 1: deliver PDF (signed S3 link, 30-day expiry, re-issuable from `/almanac/access`)
  - Step 2 (Day 2): "Three takes you might have missed" — three pull-quotes from the Almanac with chapter pointers
  - Step 3 (Day 5): invitation to Q1 2027 masterclass
  - Step 4 (Day 10): 4-Pillar Course soft pitch
  - Step 5 (Day 21): 1:1 audit booking link

---

## 4. Launch sequence (15 Jan 2027)

| Day | Channel | Asset |
|---|---|---|
| **T-7** (Jan 8) | Email list | "Almanac drops next week" — single takeaway pre-tease |
| **T-3** (Jan 12) | LinkedIn | Cover reveal + one chart from Chapter 1 |
| **T-1** (Jan 14) | Telegram / WhatsApp broadcast | Direct link, soft launch to existing community |
| **T-0** (Jan 15) | Full launch | Email blast, LinkedIn long-form post, IG carousel of 5 strongest charts, TG broadcast |
| **T+3** | LinkedIn | "What surprised me writing this" — narrative essay |
| **T+7** | Email | Follow-up — "Did you actually read it? Here are the three pages that matter" |
| **T+14** | Podcast / video | 30-min walkthrough of 2027 predictions (Chapter 6) |
| **T+30** | Re-broadcast | One-month anniversary — share the running prediction tracker |

Quarterly: in April, July, October — re-pin Almanac at top of every email and as `/almanac` site-wide banner.

---

## 5. Annual production calendar

| Month | Milestone |
|---|---|
| **Oct (year N)** | Outline locked, data pull begins |
| **Nov** | Chapters 1–4 drafted (historical sections) |
| **Dec** | Chapter 6 (predictions) + final Q4 data refresh |
| **Early Jan (N+1)** | Design pass, proof, build |
| **Mid Jan (N+1)** | Publish + launch sequence |
| **Apr / Jul / Oct (N+1)** | Quarterly addenda (paid tier when activated) |

---

## 6. Success metrics

| Metric | Target (V1) | Floor |
|---|---|---|
| Net new email subs in 60 days | 2,000 | 800 |
| PDF downloads (delivered) | 5,000 | 2,000 |
| LinkedIn impressions on launch post | 50,000 | 15,000 |
| Masterclass signups attributable | 200 | 60 |
| 1:1 audit bookings attributable in 90 days | 20 | 6 |
| Press / podcast pickups | 3 | 1 |

If floor metrics miss: keep Almanac free for V2, double down on distribution. If targets hit: A/B paid tier for V2.

---

## 7. Compliance + legal

- CEA R073319H displayed in footer of every page of PDF and on the opt-in landing page
- "General research; not individualised advice" disclaimer on inside cover and back matter
- All cited data points sourced and the source list reproduced in Chapter 8
- PropNex agency line in back matter
- No personalised property recommendations in the document
