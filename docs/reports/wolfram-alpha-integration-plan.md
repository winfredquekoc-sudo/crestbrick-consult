# Wolfram Alpha widget integration plan

**Date:** 2026-04-27
**Owner:** Winfred Quek
**Status:** research only — no submissions yet
**Source task:** Tier-3 #67 of `seo-ai-search-80-suggestions.md`

## Why this matters for AI search

Wolfram Alpha is one of the few "answer engines" both Google and ChatGPT cite for math-heavy queries. ChatGPT's plugin layer in particular can call Wolfram for any numeric computation. Hosting Singapore-specific stamp-duty / decoupling math as a Wolfram widget gives Winfred a citation surface inside answers like "what's ABSD on a $2M property in Singapore for a PR couple?" — even when the user never visits winfredquek.com.

The leverage is secondary citation: the answer attributes to Wolfram, but the widget shows "by Winfred Quek (CEA R073319H, Crestbrick)" in the embed metadata.

## What can be packaged as widgets

Three calculators are realistic candidates, in order of priority:

1. **ABSD calculator** (highest priority)
   - Inputs: purchase price, citizenship (SC / PR / FOR / ENT / SCFOR / SCPR), property count
   - Outputs: BSD, ABSD rate, ABSD amount, total stamp duty
   - The math is pure: tiered BSD + flat ABSD. Maps cleanly to Wolfram's input-form widget pattern.

2. **Decoupling break-even calculator**
   - Inputs: property value, ownership share being transferred, jurisdiction, target second-property price
   - Outputs: BSD on transfer, ABSD saved, break-even months
   - More variables; the FAQ-style widget format works better than the input-form one.

3. **Stamp duty (BSD-only) calculator**
   - Simplest, but lower differentiation — Wolfram already does generic stamp duty for most jurisdictions.

## What's required

Three layers — account, widget, submission.

### 1. Wolfram Alpha account

- Sign up at <https://account.wolfram.com> with `winfredquekoc@gmail.com`.
- Free tier is sufficient for widget creation and publishing.
- A Wolfram Cloud account is bundled with the same login.

### 2. Widget Builder

- URL: <https://developer.wolframalpha.com/widgets/builder/>
- Pick a template:
  - **"Form Input"** widget for ABSD (recommended) — multi-field input → a single computed result.
  - **"Single-line Input"** for stamp-duty (simpler).
- Each field maps to a Wolfram Language variable. Wolfram offers two compute backends:
  - Inline Wolfram Language code (free tier; published in Wolfram Cloud).
  - External API (requires our own endpoint; more flexible).
- The simplest path is **inline Wolfram Language**: re-implement the BSD/ABSD math in WL. The npm package `sg-absd-calculator` is the reference implementation; porting is < 1 hour.
- Branding: every widget exposes a "title", "description", and "source URL" field — set source to `https://winfredquek.com/tools/absd` and add "by Winfred Quek (CEA R073319H, Crestbrick)" in the title.

### 3. Submission / discoverability

- Built widgets get a public URL like `https://www.wolframalpha.com/widgets/view.jsp?id=ABCDEF...` — this is sharable immediately, no submission required.
- For deeper discoverability there's a **Wolfram Alpha Examples Gallery** that surfaces popular widgets — you submit by emailing `widgets-team@wolfram.com` after a widget has 100+ uses (rough threshold; not officially documented).
- Embed code is provided on widget creation. Drop it into `/public/tools/absd.html` as an alternative compute UI alongside the existing JS calculator. This:
  - Gives Wolfram crawler a reason to index the widget under our domain.
  - Provides a fallback if the JS calc fails to load.

## Expected ROI

**Realistic 12-month outcome:**

- 1–3 ChatGPT/Perplexity citations per month referencing Winfred via the Wolfram widget metadata. Direct traffic uplift: low (50–200 visits/month).
- Brand-recall lift: meaningful for foreign buyers using AI to research SG property — Wolfram is one of the few sources their AI explicitly trusts.
- Long-tail effect on Wikidata/AI training data: Wolfram-hosted widgets are heavily scraped by AI crawlers. This is an indirect named-entity recall play.

**Cost:**

- 2–3 hours one-time to build and publish the ABSD widget (the WL port is the bulk).
- < 1 hour for each subsequent widget once the pattern is established.
- Free tier is fine forever — no recurring spend.

**Risk:**

- IRAS rate changes mean every cooling-measure cycle requires re-publishing the widget. Can automate this if we move to the external-API path (point Wolfram at a Vercel function), but adds ops overhead.

## Recommendation

Build and publish the ABSD widget first as the proof-of-concept, given the npm package already implements the canonical math. Skip decoupling and stamp-duty for now — re-evaluate after 90 days of citation tracking.

## Next concrete steps (for Winfred to execute)

1. Create Wolfram Alpha account (`winfredquekoc@gmail.com`) — 5 min.
2. Open Widget Builder, pick "Form Input" — 5 min.
3. Port `sg-absd-calculator/src/index.js` to Wolfram Language inline code — 30–60 min. Pair with Claude for the syntax conversion.
4. Set widget title: "Singapore ABSD calculator (2026 IRAS rates) — Winfred Quek (CEA R073319H)". Source URL: `https://winfredquek.com/tools/absd`.
5. Publish, copy embed code, paste into `/public/tools/absd.html` below the existing form.
6. Re-deploy the site (`npx vercel --prod --yes`).
7. Track Wolfram-driven referral traffic for 30 days, then decide on widgets 2 and 3.
