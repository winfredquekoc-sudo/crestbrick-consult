# SEO Tier-3 execution report

**Date:** 2026-04-27
**Source:** automatable items from `seo-ai-search-80-suggestions.md` (Tier 3 + #38 from Tier 2)
**Status:** complete and deployed

## Items executed

| # | Title | Status | Output |
|---|-------|--------|--------|
| 38 | Programmatic MRT × school catchment | DONE | 64 pages at `/public/area/*.html`, all in sitemap |
| 62 | VideoObject schema scaffolding | DONE | Commented placeholder injected into top 5 articles |
| 67 | Wolfram Alpha widgets research | DONE | `wolfram-alpha-integration-plan.md` |
| 69 | Open-source one calculator | DONE | `packages/sg-absd-calculator/` (5/5 tests passing) |
| 72 | AI crawler log auditor | DONE | `~/.claude/bin/ai-crawler-audit.sh` + plist (linted OK) |
| 73 | Google Discover eligibility | DONE | `discover-eligibility-checklist.md` |
| 74 | News sitemap | DONE | `public/news-sitemap.xml` (10 articles) |
| 77 | noai/noimageai opt-out research | DONE | `ai-training-policy.md` (recommend: keep all open) |
| 78 | LLMs full-context page | DONE | `public/llms-full-context.html` + `public/llms.md` |

## Items skipped (per scope)

#61, #63–66, #68, #70, #71, #75–76, #79, #80 — require external accounts, manual outreach, or quarterly cadence.

---

## #38 — MRT × school catchment matrix

**Generator:** `_render_mrt_school_matrix.py`. Curated list of 64 (MRT, primary-school) pairs covering 30 MRT stations across NSL/EWL/NEL/CCL/DTL/TEL. Distance bands marked A (within 1 km, Phase 2A/2B priority) and B (1–2 km, Phase 2C priority).

**Page anatomy:**
- H1 in the form "Living near {MRT} with kids at {School}"
- Distance + walk-time strip with phase summary
- Distance-band primer paragraph (Phase 2A/2B/2C explained)
- Hand-written area paragraph per MRT (28 unique paragraphs)
- Hand-written housing paragraph (HDB BTO + condo) per MRT
- 5-Q FAQ block
- 3 CTA links: `/tools`, `/contact`, WhatsApp
- JSON-LD: Article + Place + EducationalOrganization + RealEstateAgent + BreadcrumbList

**Sitemap impact:** 64 new entries appended to `/public/sitemap.xml`. Sitemap now contains 209 URLs (was 145).

**Sample URLs to inspect:**
- https://winfredquek.com/area/punggol-punggol-primary
- https://winfredquek.com/area/bishan-catholic-high
- https://winfredquek.com/area/marine-parade-tao-nan

**Visual style:** matches existing `/hdb-towns/*` and `/districts/*` style — same dark theme, Fraunces+Inter, accent gold. No new design tokens introduced.

## #62 — VideoObject schema scaffolding

Injected a commented-out reference VideoObject schema into the head of the 5 most-content-heavy insights articles:
- `absd-singapore-2026.html`
- `en-bloc-singapore-guide.html`
- `new-launch-vs-resale-by-district-2026.html`
- `singapore-property-yield-by-district.html`
- `seller-stamp-duty-singapore.html`

The block sits adjacent to existing schema, marked `<!-- VideoObject schema: fill in once YouTube channel launches per #61 -->`. No fabricated URLs. When Winfred ships a YouTube channel he can uncomment, fill in `{{YT_ID}}` / dates / duration, and the schema activates.

## #67 — Wolfram Alpha widgets

Research-only deliverable at `/Users/winfredquek/crestbrick-consult/wolfram-alpha-integration-plan.md`.

Recommendation: build the ABSD widget first using the npm package as the reference math, then re-evaluate after 90 days of citation tracking. Free tier sufficient. 2–3 hours one-time effort.

## #69 — `sg-absd-calculator` npm package

Path: `/Users/winfredquek/crestbrick-consult/packages/sg-absd-calculator/`.

- `package.json` — name, MIT license, author Winfred Quek, no deps
- `src/index.js` — pure functions: `calculateAbsd({price, citizenship, propertyCount}) → {amount, bsd, absd, rate, ratePercent, breakdown}`, `computeBsd(price)`, `absdRate(citizenship, count)`. Full 2026 IRAS rate table.
- `tests/run.js` — 5 test cases: SC first / SC second / PR first / Foreigner / Entity. **5/5 passing.**
- `README.md` — usage, API, rate tables, CEA disclosure
- `LICENSE` — MIT

Not pushed to GitHub — left for Winfred to push from his own account. Suggested repo: `winfredquek/sg-absd-calculator`.

## #72 — AI crawler log auditor

**Script:** `/Users/winfredquek/.claude/bin/ai-crawler-audit.sh`
- Pulls last 24h of Vercel logs via `vercel logs winfredquek.com --since 24h`
- Counts hits for: GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, ClaudeUserBot/Claude-Web/anthropic-ai, PerplexityBot, Google-Extended, Applebot, Amazonbot, BraveBot, CCBot, Bytespider
- Telegram digest with totals + top 5 paths per top bot
- Logs to `~/.claude/bin/ai-crawler-audit.log`

**Plist:** `~/Library/LaunchAgents/com.crestbrick.ai-crawler-audit.plist`
- Schedule: 01:00 UTC = 09:00 SGT daily
- `RunAtLoad: false`
- `plutil -lint` → OK
- **NOT loaded** per instructions — Winfred to run `launchctl load ~/Library/LaunchAgents/com.crestbrick.ai-crawler-audit.plist` when ready

**Prereqs Winfred needs to satisfy before first run:**
1. `cd ~/crestbrick-consult && vercel link` (one-time project link)
2. `vercel login` (one-time auth)
3. `~/.telegram-bot.env` with TELEGRAM_BOT_TOKEN and TELEGRAM_WINFRED_CHAT_ID (already exists for other jobs)

## #73 — Google Discover eligibility checklist

Path: `discover-eligibility-checklist.md`.

Audited all 32 insights articles. Summary:
- **All 32 articles** have Article schema, canonical, mobile viewport, OG image, datePublished + dateModified
- Site OG image is 1200×630 px — meets Discover minimum width
- robots.txt allows GoogleBot + Google-Extended
- **Technically Discover-eligible: 32/32**

Remaining gaps are content-level (per-article hero images, image arrays in schema), not technical. Real blocker is upstream: site is not yet indexed in Google (Tier-0 #1).

## #74 — News sitemap

Path: `public/news-sitemap.xml`. 10 time-sensitive articles included:
1. /insights/cooling-measures
2. /insights/absd-singapore-2026
3. /insights/foreign-buyer-60-absd-strategy
4. /insights/hdb-mop-upgrade-timeline
5. /insights/cpf-accrued-interest-trap
6. /insights/seller-stamp-duty-singapore
7. /insights/tdsr-stress-test-explained
8. /insights/fixed-vs-floating-mortgage
9. /insights/ownership-restructuring-math
10. /insights/new-launch-vs-resale-by-district-2026

Referenced from `robots.txt` (added `Sitemap: https://winfredquek.com/news-sitemap.xml`).

## #77 — AI training opt-out

Decision recorded: **keep all pages trainable. Do not deploy `noai` / `noimageai` meta tags.**

The site is at zero AI visibility baseline; exclusion is the opposite of what's needed. `noai` is also poorly respected by major foundation-model labs in 2026 — `robots.txt` is the working channel and is already set up to *allow* AI bots.

Document at `ai-training-policy.md`.

## #78 — LLMs full-context page

Two formats delivered:
- `public/llms-full-context.html` (≈2,400 words, structured Q&A, FAQPage + Person + BreadcrumbList schema)
- `public/llms.md` (markdown mirror of the same content for crawlers preferring plain text)

Sections: Identity, Services (4-Pillar framework), Sample analyses (3), Stamp duty reference (2026), Tools and content, Contact, Citation guidance.

**Linked from:**
- `/llms.txt` (appended new section)
- Homepage footer Quick links (`<a href="/llms-full-context">AI-readable context</a>`)
- `sitemap.xml`

---

## Deployment

`npx vercel --prod --yes` → succeeded. Status: 200 confirmed on:
- https://winfredquek.com/area/punggol-punggol-primary
- https://winfredquek.com/llms-full-context
- https://winfredquek.com/news-sitemap.xml

## Files created or modified

**New files (16 outside of /public/area/):**
- `_render_mrt_school_matrix.py`
- `public/news-sitemap.xml`
- `public/llms-full-context.html`
- `public/llms.md`
- `wolfram-alpha-integration-plan.md`
- `ai-training-policy.md`
- `discover-eligibility-checklist.md`
- `seo-tier3-execution-report.md` (this file)
- `packages/sg-absd-calculator/package.json`
- `packages/sg-absd-calculator/LICENSE`
- `packages/sg-absd-calculator/README.md`
- `packages/sg-absd-calculator/src/index.js`
- `packages/sg-absd-calculator/tests/run.js`
- `~/.claude/bin/ai-crawler-audit.sh`
- `~/Library/LaunchAgents/com.crestbrick.ai-crawler-audit.plist`

**Plus 64 generated pages in `public/area/*.html`.**

**Modified files:**
- `public/sitemap.xml` (+65 entries: 64 area + 1 llms-full-context)
- `public/robots.txt` (+1 line: news-sitemap reference)
- `public/llms.txt` (+2 sections: AI-context links, area-pages link)
- `public/index.html` (+1 footer link to /llms-full-context)
- `public/insights/absd-singapore-2026.html` (VideoObject scaffold)
- `public/insights/en-bloc-singapore-guide.html` (VideoObject scaffold)
- `public/insights/new-launch-vs-resale-by-district-2026.html` (VideoObject scaffold)
- `public/insights/singapore-property-yield-by-district.html` (VideoObject scaffold)
- `public/insights/seller-stamp-duty-singapore.html` (VideoObject scaffold)

## Manual follow-ups for Winfred

1. **Push the npm package** — `cd packages/sg-absd-calculator && git init && git add . && git commit -m "init" && gh repo create winfredquek/sg-absd-calculator --public --source=.`. Then `npm publish`.
2. **Load the AI crawler audit plist** — `launchctl load ~/Library/LaunchAgents/com.crestbrick.ai-crawler-audit.plist` after running `vercel link` and `vercel login` in `~/crestbrick-consult/`.
3. **Verify Google Search Console + Bing Webmaster** (Tier-0 #1, #2) so the new pages get indexed quickly. Submit `sitemap.xml` and `news-sitemap.xml`.
4. **IndexNow ping** for the 64 new area URLs.
5. **Wolfram widget build** — execute the steps in `wolfram-alpha-integration-plan.md` when bandwidth permits.

## What did NOT execute

Nothing failed — all in-scope items completed.

The skipped items in the brief are correctly skipped: they require Winfred's accounts (YouTube, Wolfram), manual outreach (awards, quarterly competitor audits), or paid tools (Profound).
