# 100 Improvements — Crestbrick Stack
Generated 2026-04-25. Ranked loosely by impact × ease.

## A. Overnight Build Pipeline (11 current jobs)
1. Add a single overnight-summary job at 06:00 that reads all *.state files and sends a consolidated Telegram digest (what ran, what failed, what was produced).
2. Stagger 00:00 collision — overnight-content-writer and overnight-new-launch-pov both fire at midnight. Move POV to 00:30 so they don't compete for LLM rate limits.
3. Add exponential backoff + retry to every overnight script (currently one failure = silent drop).
4. Emit a heartbeat file per job; overnight-system-fix flags any job whose heartbeat is >24h stale.
5. Add per-job cost tracking (tokens in/out) to state files — feed into finance agent monthly.
6. overnight-beautify should diff its changes and require a human +1 for copy edits touching landing pages (>50 char changes).
7. Cap overnight-content-writer at 3 drafts/night — currently unbounded, fills the backlog with noise.
8. Add a "dead-letter" folder for jobs that fail 3 nights in a row — stop retrying, ping Winfred.
9. overnight-competitor-tracker needs de-dup logic — it's flagging the same PropNex post every night if URL changes slightly.
10. Add overnight-link-checker — crawl crestbrick-consult.vercel.app and flag 404s/broken assets.

## B. Agent Orchestration
11. Chief-of-staff should maintain a daily "priorities.md" file so managers can coordinate without routing every call through it.
12. Add a circuit breaker — if any agent calls another >5 times in 60s, halt and alert (prevents infinite delegation loops).
13. Standardize every agent's output to include a `next_action` field so chief-of-staff can chain without re-parsing.
14. Agents that touch state files should hold a lockfile; concurrent writes on otp-tracker.json / referral-tracker.json are a race waiting to happen.
15. Add a "dry-run" flag to every agent so Winfred can preview before execution.
16. Version the agent definitions — current setup has no rollback if an edit breaks behavior.
17. Build an agent-usage heatmap — which agents actually get called? Kill or merge the bottom 20%.
18. Add cost-per-invocation logging per agent — weekly report on which agents are burning tokens.
19. Cross-agent memory: shared client-context file so affordability-modeler, stamp-duty-calc, and portfolio-auditor don't re-collect the same inputs.
20. Add an "agent handoff" protocol — structured JSON between agents instead of natural-language handoffs.

## C. Client Lifecycle & CRM
21. Move from scattered state files to a single SQLite client DB (clients.db) — current design will not scale past 50 active clients.
22. Add a client-status state machine (lead → qualified → engaged → transacting → closed → nurture) with automatic transition rules.
23. Every client should have a last_contact_at timestamp; auto-flag clients silent >30 days.
24. Birthday + MOP + anniversary triggers exist separately — consolidate into one life-event engine.
25. Add a referral-source attribution field to every client record — currently lead-attribution job exists but isn't joined to client data.
26. Build a "client health score" (0-100) combining recency, engagement, payment progress, NPS proxy.
27. Testimonial-collector should auto-suggest the best past-client case studies to reference in new pitches.
28. Add a churn-prediction signal — clients who haven't opened the last 3 WA broadcasts get flagged.
29. Post-deal 90-day check-in is missing from the pipeline — add it between onboarding-agent and relationship-manager.
30. Family Office clients need a separate white-glove track with quarterly in-person review prompts, not WA drips.

## D. Content Engine
31. Content calendar is a flat JSON — move to a queue with priority + dependencies (e.g., carousel requires 2 ballot-briefs first).
32. Every published piece should be tagged with 4-Pillar category — currently no way to audit pillar balance.
33. Add an "evergreen vs timely" flag so evergreen content can be recycled after 90 days.
34. A/B test subject lines in newsletter — pick 3, send to 10% each, winner gets the 70%.
35. Content performance data isn't fed back to content-writer — engagement-analyst reports, but no closed loop.
36. Kill duplicate topics — content-writer and seo-content-agent both cover ABSD explainers with different angles. Consolidate.
37. Add a "forbidden topics" list (active client situations, competitor bashing) so no agent accidentally writes on them.
38. Carousel slide count should be data-driven — top-performing carousels are 7 slides, not 10. Update design spec.
39. Reel hooks library — save the 10 best-performing reel hooks as templates for headline-generator.
40. Cross-post decay logic — when LinkedIn post underperforms, don't remix to IG; kill it.

## E. Lead Generation
41. HDB-upgrader-hunter needs MOP data from HDB API, not just forum scraping — currently miss ~40% of upgraders.
42. Expat-hunter should filter out <6-month stays — EP holders with short contracts aren't buyers.
43. Family-office-hunter output has no de-dup against current clients — Winfred gets pitched his own network.
44. Add a lead-scoring model (0-100) before any hunter output hits Winfred's WA — reduce noise.
45. Business-owner-hunter should cross-check ACRA revenue — current "SME founder" bucket includes too many <$1M shops.
46. Investor-network-scanner is a lurker — add a contribute-first policy before any outreach.
47. Seller-hunter for en-bloc candidates needs to check URA plot ratio upside, not just age — fix enbloc-probability-scorer integration.
48. Build a "no-contact" list — explicit opt-outs go here and every hunter checks it first.
49. Lead-source ROI dashboard — which hunter produces the best conversion? Reallocate weekly.
50. Cold DM templates need rotation — same opening line to 3 prospects in one WA group = instant burn.

## F. Financial Advisor Tools
51. Stamp-duty-calculator hardcodes 2026 rates — move to a rates.json file that's version-controlled.
52. CPF-optimizer doesn't model Voluntary Housing Refund (VHR) — add it for advanced clients.
53. Loan-comparison-agent scrapes bank sites live — cache rates for 6h, currently hammers DBS/OCBC on every call.
54. Add a mortgage insurance comparison (MRTA vs term) — currently not covered anywhere.
55. Cashflow-stress-tester needs a 2008-style scenario template — current worst case is too mild.
56. Tax-optimizer misses non-resident owner tax treatment — add for foreign investor clients.
57. New-launch-modeler should flag PPS vs DPS break-even months — currently just shows cash flow, not the decision.
58. Upgrade-cashflow-modeler output PDF is text-heavy — add a single visual timeline client can screenshot.
59. Affordability-modeler should output 3 budget bands (safe / stretch / ceiling), not a single number.
60. Add a refinance-timing agent that watches client lock-in expiries and nudges 6 months out (refinance-trigger exists, ensure it's firing).

## G. Property Intelligence
61. URA data fetch is fragile — no retry, no checksum validation on downloads.
62. Listing-watchdog fires on price changes >3% — noise threshold. Move to >5% or paired with DOM change.
63. District-analyst output inconsistent — sometimes MRT names, sometimes postal sectors. Standardize schema.
64. New-launch-tracker should capture indicative PSF leaks from early-access events, not just official launches.
65. Market-scout summaries are too long — cap at 200 words, link to full source.
66. Add a "supply cliff" alert — when a district has >500 units TOP'ing in 6mo, flag for any active buyer there.
67. Rental-comps-agent uses 99.co only — add PropertyGuru rental listings for better coverage.
68. Portfolio-tracker valuations use outdated PSF — refresh monthly against URA quarterly data.
69. En-bloc-probability-scorer ignores sentiment — add a news-scan signal ("residents organizing EOGM" = +20 points).
70. Competitive-listing-scout fires weekly — if a comparable drops price mid-week, Winfred finds out 6 days late.

## H. Deals & Transactions
71. Decoupling-strategist output needs a 1-page summary (client-facing) + detailed memo (advisor-facing). Currently one muddled doc.
72. Negotiation-coach has no record of prior offers on the same property — add offer history tracking.
73. Co-broke-coordinator and cobroke-manager are two agents doing similar things — merge or clearly separate scopes.
74. Document-drafter OTP template hasn't been legally reviewed since setup — annual legal review SOP needed.
75. Open-house-planner doesn't sync with Google Calendar — manual double entry right now.
76. Milestone-tracker reminders go to Winfred only — add client-facing versions (e.g., "completion in 14 days — here's what to prep").
77. Private-seller-net-proceeds PDF doesn't include a "what to do with proceeds" CTA — missed upsell.
78. Add a post-OTP checklist generator (valuation, loan, lawyer, CPF instruction) per deal.
79. Deal failure post-mortems don't exist — when a deal dies, capture why in a lessons.md log.
80. Commission tracking lives in cobroke-manager but not joined to sales-revenue — dashboard is incomplete.

## I. Website (crestbrick-consult.vercel.app)
81. Listings page: add a sticky filter bar — currently users scroll and lose filter state.
82. Add schema.org RealEstateListing markup to every listing page for Google Rich Results.
83. Lazy-load listing images — first contentful paint is hurt on mobile.
84. Add a "download 4-Pillar audit sample PDF" gated lead magnet.
85. WhatsApp click-to-chat tracking — currently no idea which page converts.
86. Case studies page is thin — publish 3 sanitized case studies (HDB upgrader, decoupler, investor).
87. Add testimonials carousel to homepage — current page has them buried.
88. Build a public "tools" page: free ABSD calculator, free rental yield estimator. SEO gold + lead magnet.
89. Set up Vercel Analytics + Web Vitals — no visibility on Core Web Vitals right now.
90. Add Open Graph images per article — social shares currently show default.

## J. Ops, Security, Tooling
91. Run `security-audit` slash command monthly — credential hygiene check on .env files and API keys.
92. State backups exist (state-backup.plist) — verify restore path actually works by doing a quarterly dry-run restore.
93. WhatsApp bridge has no auto-restart on crash — com.crestbrick.whatsapp-bridge should have KeepAlive true.
94. Telegram bot token and WA session need quarterly rotation reminder — add to ScheduleWakeup.
95. MCP server health check daemon — right now a dead MCP is discovered only when an agent calls it and fails.
96. vercel.json should move to vercel.ts for typed config (per 2026 platform guidance).
97. Node 18 → 24 LTS upgrade on Vercel project (18 is deprecated).
98. Subscription audit — finance agent should run a monthly "are we paying for tools no agent uses" review.
99. Add a CHANGELOG.md at repo root — tracking agent + script changes across the stack is currently tribal memory.
100. Single "Crestbrick OS" dashboard — one URL showing: pipeline, overnight build status, active clients, weekly revenue forecast, open tasks. Right now every view is a separate agent call.
