# Content Audit — 2026-05-18

Performed by: overnight build agent  
Scope: all top-level `/public/*.html` pages  
Method: three-persona review (Agent A: Compliance, Agent B: Business Value, Agent C: User Trust)

---

## Calculators

### absd-calculator.html
- **Rates verified correct:** SC 0%/20%/30%, PR 5%/30%/35%, Foreigner 60%, Entity 65%
- **Action:** Added "Last updated: May 2026" + IRAS link to disclaimer
- **Verdict:** KEEP (all three)

### stamp-duty-calculator.html
- **BSD bands verified correct:** 1% on first $180k, 2% next $180k, 3% next $640k, 4% next $500k, 5% next $1.5M, 6% above $3M
- **ABSD rates verified correct** (same as above)
- **Action:** Added "Last updated: May 2026" + IRAS link to disclaimer
- **Verdict:** KEEP (all three)

### tdsr-calculator.html
- **Rates verified correct:** TDSR 55%, MSR 30%, stress rate 4.0% p.a.
- **Action:** Added "Last updated: May 2026" + MAS link to disclaimer
- **Verdict:** KEEP (all three)

---

## Top-Level Pages

### index.html
- No inflated claim counts. "9 years, 5 properties before 30" is personal credential.
- CEA R073319H appears correctly.
- No "2-3% bank rate" claim found.
- **Verdict:** KEEP (all three). Disclaimer injected.

### about.html
- CEA number correct. PropNex and Crestbrick affiliations correct.
- Personal narrative accurate.
- **Verdict:** KEEP (all three). Disclaimer injected.

### track-record.html
- "20+ clients, S$50M+ advised" — matches approved figures per project memory.
- Transactions anonymized with clear non-guarantee language.
- **Verdict:** KEEP (all three). Disclaimer injected.

### case-studies.html
- "interest at 3.6%" appears within a named historical case scenario — acceptable.
- "2025" in article IDs is a case identifier, not a stale market date claim.
- **Verdict:** KEEP (all three). Disclaimer injected.

### faq.html
- Rate examples (3.5% mortgage, 2.5–3.5% yield) are illustrative scenarios, not current rate advice.
- "current rate" on line 945 was unqualified — **REWRITE** agreed by A+C.
- **Action:** Rewritten to "prevailing rate at the time of analysis".
- CPF 2.5% OA rate is correct CPF Board figure, not a bank rate.
- 4% stress-test figure is accurate MAS guidance.
- **Verdict:** Partial rewrite applied.

### services.html, pricing.html, buyers-guide.html, sellers-guide.html
- No stale dates, no incorrect rates, no inflated claims found.
- CEA disclosure present.
- **Verdict:** KEEP (all three). Disclaimer injected.

### wealth-stack.html, enbloc-reinvestment.html, family-office-brief.html
- Specialist pages. No regulatory violations. No incorrect rate claims.
- **Verdict:** KEEP (all three). Disclaimer injected.

### glossary.html
- CPF OA 2.5% is correct statutory rate.
- **Verdict:** KEEP (all three). Disclaimer injected.

### new-launches.html, listings.html, districts.html, insights.html
- No stale market year claims found.
- **Verdict:** KEEP (all three). Disclaimer injected.

---

## Global Actions Taken

| Action | Count / Detail |
|---|---|
| Disclaimer block injected | 382 pages |
| Pages correctly excluded | 4 (404, privacy, unsubscribe, thank-you) |
| Calculator disclaimers updated | 3 (ABSD, BSD, TDSR) |
| FAQ rewrite applied | 1 instance (line 945, "current rate" → qualified) |
| Template files updated | `_render_area_pages.py`, `_render_rich_districts.py` |
| Inflated claims found | 0 |
| "$120M" claim found | 0 |
| "2-3% bank rate" claim found | 0 |
| Stale 2024/2025 market date claims found | 0 in top-level pages |

---

## Things That Are Fine (Not Errors)

- CPF accrued interest at 2.5% — correct CPF Board statutory rate
- 4% MAS stress test — correct and current MAS guideline
- "9 years, 5 properties before 30" — personal credential, not a regulated claim
- "20+ clients, S$50M+ advised" — correct, properly qualified with "Representative outcomes, not guarantees"
- Case study rates (3.5–3.6%) — explicitly framed as historical scenarios
- Yield ranges (2.5–3.5%) — presented as market context ranges, not guaranteed returns
