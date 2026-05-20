# Fact-Check Review Sheet — 15 New Articles

**Status:** committed to git, NOT deployed. Do not deploy until reviewed.
**How to use:** work article-by-article. `[ ]` → `[x]` as you clear each item. Edit the HTML in `public/insights/` directly, or tell Claude the correct figure and it will edit.

Bank mortgage rate (~1.5%), the downpayment instalment table, and the market-outlook fabricated price-index table were already corrected in an earlier pass. Everything below is still open.

---

## 0. Recurring figures — RESOLVED (use these exact values)

All confirmed 2026-05-20 — Winfred-confirmed + verified against CPF/IRAS/HDB. Apply wherever an article states otherwise.

| Item | Correct value | Articles currently say |
|---|---|---|
| BRS 2026 | **$110,200** | $106,500 — wrong |
| FRS 2026 | **$220,400** | $213,000 — wrong |
| ERS 2026 | **$440,800** (4× BRS) | $318,000 — wrong |
| EHG max | $120,000 (income ≤$9,000) | correct — no change |
| Family Grant (resale) | **$80,000** for 4-room and smaller; **$50,000** for 5-room+ | inverted/wrong in articles 5 & 7 |
| PHG | $30,000 / $20,000 | amounts correct (check the live-with vs live-near wording) |
| SSD (bought on/after 4 Jul 2025) | 4-year holding: **16% / 12% / 8% / 4% / 0%** for ≤1 / 1–2 / 2–3 / 3–4 / >4 years | 3-year 12/8/4 — wrong |
| HFE letter validity | **9 months** | article 5 says 6 — wrong |
| HDB occupancy cap | **6** persons for 3-room; **8** for 4-room and above | blanket 6 — wrong |
| Property tax — owner-occupied (2025) | **0/4/6/10/14/20/26/32%**; bands $12k / $40k / $50k / $75k / $85k / $100k / $140k | article 15 table outdated — wrong |
| Property tax — non-owner-occupied | **12/20/28/36%**; bands $30k / $45k / $60k | article 15 table wrong |
| HDB concessionary loan rate | 2.6% | believed correct — confirm |
| EC grant | "**CPF Housing Grant**" for ECs, up to **$30,000** | called "AHG" — wrong name |
| HDB resale option fee | option fee + deposit capped at **$5,000** total | "1% + 4%" — wrong |

---

## 1. downpayment-condo-singapore-2026.html
**Errors to fix:**
- [ ] L250: BSD on $1.5M shown as $24,600 — should be **$44,600** ($24,600 is the BSD for a $1M property).
- [ ] L257: BSD on $2M shown as $64,600 — should be **$69,600**.
- [ ] L232 vs L236: 3rd-property table shows min cash 32.5% ($487,500); footnote says 25%. Reconcile (min cash for 3rd property is 25% = $375,000).
- [ ] L144 vs L250: total upfront framed as "exceeds $200,000" vs "$180,000–$220,000" — pick one.
**Verify:** L246 HDB loan 2.6%; L154/L208/L236 MAS LTV claims (75%, 45%/35%, Notice 632); L201 accrued-interest math (~$82k on $300k/10yr — recompute, ≈$84k).

## 2. bto-application-guide-singapore-2026.html
**Errors to fix:**
- [ ] L143: "30-year MOP waiver for resale buyer" — garbled, rewrite (Prime/Plus flats have a 10-year MOP).
- [ ] L189: unsuccessful-ballot mechanic muddled ("one extra chance" vs "double after two") — clarify.
**Verify:** L139/L165-167 BTO income ceilings ($7k / $14k / $21k); L181 first-timer priority "95% of 4-room+"; L196 booking fee $500–$2,000; L227 accrued-interest example.

## 3. singapore-property-market-outlook-2026.html
**Errors to fix:**
- [ ] L190: "SCR (Rest of Central)" — URA's term is **RCR**.
- [ ] L139 vs L179: MOP-wave timeline inconsistent ("2018–2019 completions" vs "launched 2018–2019, MOP 2023–2024").
**Verify:** the forward-looking opinions are now written in your voice ("In my view…") — confirm you actually agree with the base/bull/bear takes before this publishes under your name. L130/L185 foreigner ABSD 60% / April 2023 / doubled from 30%.

## 4. condo-maintenance-fees-singapore.html
**Errors to fix:**
- [ ] L258: worked example uses a 24% marginal tax rate (that's the top bracket, income >$1M) — use a typical landlord rate.
- [ ] L146 vs L190: quick answer floor $300/mth vs mega-condo table floor $280/mth.
**Verify:** L153/L198 "BMSMA requires minimum 10% sinking fund contribution" (check against the Act); L232 "According to BCA…strata roll inspection" (likely BMSMA/MCST, not BCA); L204 lift/facade cost estimates — label as estimates.

## 5. first-time-home-buyer-singapore-guide.html
**Errors to fix:**
- [ ] L192: EHG taper sentence self-contradictory — max "at $9,000 or below" AND tapering "up to $9,000".
- [ ] L177: combined grants "$80,000–$190,000" — $190,000 doesn't reconcile with the article's own component figures.
- [ ] L195: Family Grant tiers contradict article 7 (hdb-resale).
- [ ] L211: HFE validity 6 months — contradicts article 7 (says 9).
- [ ] L223/L225: HDB resale OTP described as "$1 option + 4% exercise = 5%" — wrong structure for HDB.
- [ ] L198: PHG "within 4km $30k / within 2km $20k" — garbled (criteria is live-with vs live-near).
**Verify:** L150/L192 EHG max $120k + ceiling; L195 Family Grant amounts; L201 Step-Up Grant $15,000. (BSD examples $9,600/$12,600 — checked, correct.)

## 6. landlord-guide-renting-out-singapore-2026.html
**Errors to fix:**
- [ ] L309: "Singapore expanded taxable foreign-sourced income from 2024" — looks hallucinated and out of place; recommend deleting.
- [ ] L263 vs L267: tenancy stamp duty wording calls a 0.4% rate "lower" than another 0.4% rate.
**Verify:** L156/L255/L257 property tax NOO 12–36% / owner-occ 0–23% / HDB NOO 10–23%; L170 occupancy caps "2-room 4 / 3-room+ 6"; L216 income tax "0–24%"; L235-243 rental tax worked example uses 24% rate.

## 7. hdb-resale-buying-process-singapore.html
**Errors to fix:**
- [ ] L165: HFE validity 9 months — contradicts article 5 (says 6).
- [ ] L202/L204: HDB resale OTP as "1% option + 4% exercise" — wrong structure for HDB resale.
- [ ] L218-219: CHG tiers ($80k for 2-3 room, $40k 4-room, $20k 5-room) — contradicts article 5; figures suspect.
**Verify:** L215-216 EHG $120k + income ceiling $9,000 (singles vs families); L221-222 PHG amounts; L246 HDB loan 2.6%.

## 8. cpf-at-55-property-impact-singapore.html
**Errors to fix:**
- [ ] L162: ERS ~$318,000 — likely outdated (ERS moved to ~4× BRS in 2025).
- [ ] L135/L152: "sweeps SA first, then OA" at 55 — outdated; the Special Account is being closed for members 55+ from 2025.
**Verify:** L154-162 BRS $106,500 / FRS $213,000 / ERS — 2026 figures; CPF Life payout ranges (label as illustrative); L186 Valuation Limit / 50-year-lease rule.

## 9. executive-condominium-buyer-guide-2026.html
**Errors to fix:**
- [ ] L163/L265: EC grant called "AHG (Additional Housing Grant)" — AHG was folded into EHG in 2019; wrong name.
- [ ] L191: references "Family Grant for EC" — reconcile with the AHG naming.
- [ ] L219: "sell HDB within 6 months of EC TOP" attributed to IRAS — that's an HDB rule, not IRAS.
**Verify:** L135/L141 EC income ceiling $16,000; L144/L160 EC ABSD (0% first-timer / 20% if owns property); L227 "15–25% discount" (estimate — label).

## 10. how-to-sell-hdb-resale-singapore.html
**Errors to fix:**
- [ ] L147/L260: SSD 12%/8%/4% over 3 years — confirm current schedule (revised 2025?).
**Verify:** L228 FRS $213,000 for 2026; L219 HDB approval letter 4–6 weeks. (Net-proceeds example math — checked, internally consistent.)

## 11. singapore-property-investment-beginners.html
**Errors to fix:**
- [ ] L263: "top rate 22% in 2026 for income above $320,000" — top marginal income tax rate is **24%**.
- [ ] L266: SSD 12%/8%/4% over 3 years — same SSD-schedule issue as article 10.
**Verify:** L147/L187-189 rental yields by region (estimates — label); L182 "3–5% p.a. appreciation" (URA); L217 ABSD 20% "unchanged since April 2023". (BSD $44,600 on $1.5M — checked, correct.)

## 12. property-valuation-singapore-guide.html
**Errors to fix:**
- [ ] L233: "SLA's lease decay framework" — the conventional reference is **Bala's Table**; reword.
**Verify:** L268 HDB concessionary loan 2.6%; L151/L203 MAS valuer-panel claims; L272 caveat lodged "within 2 weeks"; L313 CPF "lease <30yr at age 95" rule. (LTV worked example — checked, internally consistent.)

## 13. how-to-negotiate-property-price-singapore.html
**Errors to fix:**
- [ ] L326: "Singapore property transactions by law require licensed estate agents to represent the parties" — **false**; private sales without an agent are legal.
- [ ] L272/L281: "1% option + 4% deposit" presented as universal — note it's the private structure, not HDB.
**Verify:** L256/L267 CEA disclosure / dual-representation rules.

## 14. hdb-subletting-rules-singapore.html
**Errors to fix:**
- [ ] L147: "maximum occupancy for any flat is 6 persons regardless of flat type" — wrong; smaller flats have lower caps. Contradicts the article's own L246.
**Verify:** L148/L269-271 non-Malaysian foreigner caps (4-room+ max 4, 3-room max 2); L360 tenancy stamp duty "0.4% of total rent" (IRAS uses Average Annual Rent bands); L222-226/L291/L300 minimum 6-month rental period.

## 15. singapore-property-tax-investor-guide.html
**Errors to fix:**
- [ ] L176-206: the entire 2026 property tax rate table — the owner-occupied schedule (0/4/6/10/14/23%) looks like outdated pre-2025 tiers; owner-occupied top rate is now higher. Replace both tables with current IRAS figures.
- [ ] L252/L256: worked examples ($4,500 and $16,200 owner-occ; $8,640 and $25,200 NOO) don't match the article's own rate table — recompute after the table is fixed.
- [ ] L252: "12% on first $75k, 20% on remainder" applied to an AV of $72,000 — nonsensical (AV is below $75k).
**Verify:** L308-314 Section 14A depreciation ("25% declining balance or 3-year write-off"); L442 commercial property tax flat 10%.

---

## Still outstanding (separate from these 15)

The ~160 articles already **live** on winfredquek.com, plus the 4 gap articles, `/faq` (79 Q&As), and `/data`, were generated the same way and are public now. They likely carry similar errors. Decide separately when to audit those.
