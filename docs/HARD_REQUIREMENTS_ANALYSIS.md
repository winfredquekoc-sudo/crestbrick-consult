# Hard Requirements Analysis & Recommendations

Based on 21 landlords collected in landlord-db.json

## Current Implementation vs Recommended

### ✓ KEEP AS HARD (Already Effective)

**1. Pets Policy**
- 4 landlords explicitly forbid pets (19%)
- 0 allow pets explicitly
- 17 unspecified (default: assume flexible)
- **Current:** Checking if `pets=yes AND landlord.pets_allowed=false` → REJECT
- **Recommendation:** KEEP — clear hard boundary when enforced

**2. Pax (Occupancy) Limit**
- 100% of landlords specify max_pax
- Ranges: 1-2 pax per room
- **Current:** Checking `tenant_pax > landlord.max_pax` → REJECT
- **Recommendation:** KEEP — universal enforcement

**3. Lease Term Range**
- 100% specify min/max lease months
- Min range: 1-12 months
- Max range: 2-749 months (mix of 2-24mo preferred, some ultra-flexible)
- **Current:** Checking `lease_term < min OR lease_term > max` → REJECT
- **Recommendation:** KEEP — but flag suspicious max values (749mo = essentially no max)

---

### ⚠️ ADD AS NEW HARD REQUIREMENTS

**4. Cooking Policy** [NEW]
- 3 landlords explicitly forbid cooking (14%)
  - LL_008: `cooking_allowed: false`
  - LL_018: `cooking_allowed: false`
  - LL_019: `cooking_allowed: false`
- 4 allow cooking
- 14 unspecified
- **Recommendation:** ADD HARD CHECK
  ```javascript
  if (tenant.occupation.includes("chef") || tenant.note.includes("catering")) {
    if (landlord.cooking_allowed === false) REJECT;
  }
  ```
  Only apply if landlord explicitly forbids AND tenant is hospitality-adjacent.

**5. Overnight Visitors Policy** [NEW]
- 3 landlords explicitly forbid overnight guests (14%)
  - LL_001 (Mrs. Chong): `overnight_visitors_allowed: false`
  - LL_018: `overnight_visitors_allowed: false`
  - LL_019: `overnight_visitors_allowed: false`
- 0 allow explicitly
- 18 unspecified
- **Recommendation:** ADD HARD CHECK
  ```javascript
  if (tenant.living_situation === "couple") {
    if (landlord.overnight_visitors_allowed === false) REJECT;
  }
  ```
  Only hard-reject if couple AND landlord forbids.

**6. Pass Type Requirement** [NEW]
- 7 landlords specify required pass type (33%)
  - LL_008: `preferred_pass_type: "Student Pass"`
  - LL_012: `preferred_pass_type: "Student Pass"`
  - LL_014: `preferred_pass_type: "Student Pass"`
  - LL_015: `preferred_pass_type: "Student Pass"`
  - LL_018: `preferred_pass_type: "Student Pass"`
  - LL_019: `preferred_pass_type: "Student Pass"`
  - LL_011: `preferred_pass_type: "Work Permit"`
- **Recommendation:** ADD HARD CHECK
  ```javascript
  if (landlord.preferred_pass_type && landlord.preferred_pass_type !== "Any") {
    if (tenant.pass_type !== landlord.preferred_pass_type) REJECT;
  }
  ```
  This is a major filter — Student Pass landlords only want students.

---

### ✓ KEEP AS HARD (Singapore Market)

**Gender Preferences**
- 3 landlords strictly Female: LL_002, LL_018
- 1 landlord strictly Male: LL_010
- Rest "Any"
- **Current:** Not checking
- **Recommendation:** ADD AS HARD CHECK
  - Singapore market: explicit gender preferences are standard
  - Only reject if landlord specifies AND tenant doesn't match
  - Reason: Practical business requirement, not discrimination

**Ethnicity/Race Restrictions** [MISSING DATA]
- Database shows all "not_specified" (data extraction incomplete at 28% avg)
- **YOU'VE INDICATED:** Some landlords DO have ethnicity restrictions stated verbally
- **Recommendation:** ADD AS HARD CHECK (highest priority)
  - Update landlord intake form to capture ethnicity preferences
  - Ask: "Do you have any ethnicity preferences? If yes, specify."
  - Apply hard filter: reject if landlord specifies ethnicity AND tenant doesn't match
  - Reason: Critical matching criterion that's currently missing from system

---

## Revised Hard Requirements Checklist (Singapore Market)

Replace current Tier 1 logic with:

```javascript
function evaluateTier1Revised(tenant, landlord) {
  // 1. PETS
  if (tenant.pets === "yes" && landlord.pets_allowed === false) {
    return { passed: false, reason: "Pets not allowed" };
  }

  // 2. PAX (occupancy)
  const tenantPax = parseInt(tenant.pax) || 1;
  if (landlord.max_pax && tenantPax > landlord.max_pax) {
    return { passed: false, reason: `Exceeds max pax (${landlord.max_pax})` };
  }

  // 3. LEASE TERM RANGE
  const leaseTerm = parseInt(tenant.lease_term) || 12;
  if (landlord.min_lease_months && leaseTerm < landlord.min_lease_months) {
    return { passed: false, reason: `Below min lease (${landlord.min_lease_months}mo)` };
  }
  if (landlord.max_lease_months && landlord.max_lease_months < 500 && leaseTerm > landlord.max_lease_months) {
    return { passed: false, reason: `Exceeds max lease (${landlord.max_lease_months}mo)` };
  }

  // 4. GENDER [SG MARKET]
  if (landlord.preferred_gender && landlord.preferred_gender !== "Any") {
    if (tenant.gender !== landlord.preferred_gender) {
      return { 
        passed: false, 
        reason: `Gender mismatch (landlord wants ${landlord.preferred_gender})` 
      };
    }
  }

  // 5. ETHNICITY [CRITICAL - DATA GAP]
  if (landlord.preferred_ethnicity && landlord.preferred_ethnicity !== "Any") {
    if (tenant.ethnicity !== landlord.preferred_ethnicity) {
      return { 
        passed: false, 
        reason: `Ethnicity mismatch (landlord wants ${landlord.preferred_ethnicity})` 
      };
    }
  }

  // 6. PASS TYPE
  if (landlord.preferred_pass_type && landlord.preferred_pass_type !== "Any") {
    if (tenant.pass_type !== landlord.preferred_pass_type) {
      return { 
        passed: false, 
        reason: `Pass type mismatch (need ${landlord.preferred_pass_type}, have ${tenant.pass_type})` 
      };
    }
  }

  // 7. COOKING
  if (tenant.occupation?.includes("Chef") && landlord.cooking_allowed === false) {
    return { passed: false, reason: "Cooking forbidden, occupation is Chef" };
  }

  // 8. OVERNIGHT VISITORS
  if (tenant.household_type === "couple" && landlord.overnight_visitors_allowed === false) {
    return { passed: false, reason: "Overnight guests forbidden, tenant is couple" };
  }

  return { passed: true };
}
```

---

## Impact on Matching

**Before (current):** Only 1 match found for Dhruv Singh (Mrs. Chong)  

**After (revised):** Filters more precisely:
- Pass Type requirement eliminates non-matching landlords upfront (33% filter)
- Gender check eliminates 3 Female-only, 1 Male-only landlords (SG market)
- Ethnicity check eliminates mismatches (data currently missing — CRITICAL GAP)
- Cooking/overnight visitor checks prevent edge-case mismatches

**Data Collection Gap:** 
- Ethnicity preferences are stated by landlords but NOT captured in landlord-db.json
- Must update landlord intake form to ask: "Do you have ethnicity preferences?"
- This is a critical missing data point for accurate matching in Singapore market

---

## Implementation Priority (Revised for SG Market)

1. **CRITICAL:** Ethnicity data collection — Missing from current system
   - Update landlord intake form: "Do you have ethnicity preferences? (Optional)"
   - Re-interview incomplete landlords (19/21 at 28-57% completeness)
   - Add to tenant intake: "Ethnicity: Chinese / Malay / Indian / Other"

2. **HIGH:** Pass Type filtering (33% of landlords require specific pass)

3. **HIGH:** Gender filtering (14% strict; standard in SG market)

4. **MEDIUM:** Cooking & Overnight Visitors (14% each; only apply if relevant)

---

## Tenant Profile Fields to Collect

To support new hard requirements, update tenant intake form to ask:

```
• Name: [existing]
• Email: [existing]
• Nationality: [existing]
• Ethnicity: Chinese / Malay / Indian / Eurasian / Other [NEW - CRITICAL]
• Gender: Male / Female / Other [ADD TO FORM]
• Pass type: SC / PR / Student / Work Permit / EP / STP [ADD TO FORM]
• No. of pax: [existing]
• Household type: Single / Couple / Group [NEW]
• Occupation: [existing - note "Chef" for cooking check]
• Budget: [existing]
• Lease term: [existing]
• Pets: None / Dog / Cat / Other [NEW]
• Overnight guests expected: Yes / No [NEW]
```

---

## Summary of Changes

| Requirement | Current | Recommended | Reason |
|-------------|---------|-------------|--------|
| Pets | Hard ✓ | Keep Hard | Clear enforcement |
| Pax | Hard ✓ | Keep Hard | Universal |
| Lease Term | Hard ✓ | Keep Hard | Universal |
| Cooking | Not checked | Add Hard* | 14% forbid |
| Overnight Visitors | Not checked | Add Hard* | 14% forbid |
| Pass Type | Not checked | Add Hard** | 33% require specific |
| Gender | Not checked | Add Hard | 3/21 strict (SG market) |
| Ethnicity | Not captured | Add Hard*** | Data gap — capture & filter |

*Apply only if relevant (chef → cooking, couple → visitors)  
**Major filter — recommended priority

---

**Implementation Priority:**
1. **HIGH:** Add Pass Type hard check (33% landlords)
2. **MEDIUM:** Add Cooking & Overnight Visitors checks (14% each)
3. **LOW:** Move Gender to soft scoring

---
*Last Updated: 2026-06-11*
*Analysis based on 21 landlords in landlord-db.json*
