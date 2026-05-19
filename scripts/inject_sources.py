#!/usr/bin/env python3
"""Inject Sources & References section into Singapore property articles."""

import os
import re

INSIGHTS_DIR = "/Users/winfredquek/crestbrick-consult/.claude/worktrees/zealous-jang-86cef2/public/insights"

# Verified official source URLs
SOURCES = {
    "IRAS ABSD": ("https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/additional-buyer-s-stamp-duty-(absd)", "IRAS: Additional Buyer's Stamp Duty (ABSD)"),
    "IRAS BSD": ("https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/buyer-s-stamp-duty-(bsd)", "IRAS: Buyer's Stamp Duty (BSD)"),
    "IRAS SSD": ("https://www.iras.gov.sg/taxes/stamp-duty/for-property/selling-or-disposing-property/seller-s-stamp-duty-(ssd)-for-residential-property", "IRAS: Seller's Stamp Duty (SSD) for Residential Property"),
    "IRAS Property Tax": ("https://www.iras.gov.sg/taxes/property-tax/property-owners/property-tax-rates", "IRAS: Property Tax Rates"),
    "IRAS Rental Income": ("https://www.iras.gov.sg/taxes/individual-income-tax/basics-of-individual-income-tax/what-is-taxable-what-is-not/rental-income", "IRAS: Rental Income"),
    "IRAS Stamp Duty (general)": ("https://www.iras.gov.sg/taxes/stamp-duty/for-property", "IRAS: Stamp Duty for Property"),
    "MAS TDSR Framework": ("https://www.mas.gov.sg/regulation/regulations/total-debt-servicing-ratio-framework", "MAS: Total Debt Servicing Ratio Framework"),
    "MAS Property Market Measures": ("https://www.mas.gov.sg/monetary-policy/monetary-policy-decisions", "MAS: Monetary Policy Decisions"),
    "MAS LTV Limits": ("https://www.mas.gov.sg/regulation/regulations/loan-to-value-limit", "MAS: Loan-to-Value Limits"),
    "CPF Housing Usage": ("https://www.cpf.gov.sg/member/tools-and-services/calculators/cpf-housing-usage", "CPF Board: Housing Usage Calculator"),
    "CPF OA for Housing": ("https://www.cpf.gov.sg/member/account-services/using-your-cpf-savings/using-cpf-for-housing", "CPF Board: Using CPF Savings for Housing"),
    "CEA Public Register": ("https://www.cea.gov.sg/public-register", "CEA: Public Register"),
    "URA Property Prices": ("https://www.ura.gov.sg/Corporate/Property/Property-Data/Download-Property-Data", "URA: Download Property Data"),
    "URA Residential Property": ("https://www.ura.gov.sg/Corporate/Property/Residential", "URA: Residential Property"),
    "HDB Resale Portal": ("https://www.hdb.gov.sg/residential/buying-a-flat/buying-procedure-for-resale-flats", "HDB: Buying Procedure for Resale Flats"),
    "HDB Grants": ("https://www.hdb.gov.sg/residential/buying-a-flat/understanding-your-options/flat-and-grant-eligibility", "HDB: Flat and Grant Eligibility"),
    "HDB MOP/Eligibility": ("https://www.hdb.gov.sg/residential/selling-a-flat/eligibility", "HDB: Eligibility to Sell"),
    "HDB BTO": ("https://www.hdb.gov.sg/residential/buying-a-flat/new-flats/bto-flats", "HDB: BTO Flats"),
    "SLA Residential Property Act": ("https://www.sla.gov.sg/land-administration/managing-land-ownership-and-registration/residential-property", "SLA: Residential Property"),
    "MOF Budget": ("https://www.mof.gov.sg/singaporebudget", "MOF: Singapore Budget"),
    "Data.gov.sg": ("https://data.gov.sg", "Data.gov.sg: Singapore Government Data"),
}

# File → list of source keys
FILE_SOURCES = {
    "absd-singapore-2026.html": ["IRAS ABSD", "IRAS BSD", "MAS Property Market Measures", "MOF Budget"],
    "absd-singapore.html": ["IRAS ABSD", "IRAS BSD", "MAS Property Market Measures"],
    "absd-explained.html": ["IRAS ABSD", "IRAS Stamp Duty (general)", "MOF Budget"],
    "absd-for-singles-singapore-2026.html": ["IRAS ABSD", "MAS Property Market Measures"],
    "absd-remission-married-couples-2026.html": ["IRAS ABSD", "IRAS Stamp Duty (general)"],
    "absd-remission-claim-process-iras.html": ["IRAS ABSD", "IRAS Stamp Duty (general)"],
    "absd-refund-how-to-claim-timeline.html": ["IRAS ABSD", "IRAS Stamp Duty (general)"],
    "buyer-stamp-duty-complete-guide-2026.html": ["IRAS BSD", "IRAS Stamp Duty (general)"],
    "seller-stamp-duty-singapore.html": ["IRAS SSD", "IRAS Stamp Duty (general)"],
    "stamp-duty-exemptions-singapore.html": ["IRAS Stamp Duty (general)", "IRAS ABSD", "IRAS BSD"],
    "restructuring-breakeven.html": ["IRAS ABSD", "IRAS BSD"],
    "property-restructuring-after-99-1.html": ["IRAS ABSD", "IRAS Stamp Duty (general)"],
    "decoupling-singapore.html": ["IRAS ABSD", "IRAS BSD", "IRAS Stamp Duty (general)"],
    "transferring-property-between-spouses-sg.html": ["IRAS BSD", "IRAS Stamp Duty (general)", "SLA Residential Property Act"],
    "adding-child-to-property-title-sg.html": ["IRAS BSD", "IRAS Stamp Duty (general)", "SLA Residential Property Act"],
    "cpf-accrued-interest-trap.html": ["CPF Housing Usage", "CPF OA for Housing"],
    "cpf-accrued-interest-upgrade.html": ["CPF Housing Usage", "CPF OA for Housing"],
    "cpf-second-property-rules-singapore.html": ["CPF Housing Usage", "CPF OA for Housing", "IRAS ABSD"],
    "cpf-oa-vs-cash-downpayment-singapore.html": ["CPF Housing Usage", "CPF OA for Housing", "MAS LTV Limits"],
    "sora-vs-fixed-mortgage-2026.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "fixed-vs-floating-mortgage.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "fixed-floating-lock-in-expires-2026.html": ["MAS TDSR Framework"],
    "refinancing-singapore-mortgage-2026.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "bridging-loan-singapore-playbook.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "interest-offset-mortgage-singapore.html": ["MAS TDSR Framework"],
    "loan-tenure-impact-singapore.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "mortgage-after-55-singapore.html": ["MAS TDSR Framework", "CPF OA for Housing"],
    "tdsr-stress-test-explained.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "tdsr-one-income-singapore-2026.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "non-singapore-income-tdsr-haircut.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "joint-borrower-sole-proprietor-singapore.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "self-employed-mortgage-singapore-2026.html": ["MAS TDSR Framework", "MAS LTV Limits"],
    "foreigner-home-loan-singapore-2026.html": ["MAS TDSR Framework", "MAS LTV Limits", "SLA Residential Property Act"],
    "property-tax-2026-singapore.html": ["IRAS Property Tax"],
    "rental-income-tax-singapore-guide.html": ["IRAS Rental Income", "IRAS Property Tax"],
    "iras-property-audit-singapore.html": ["IRAS Rental Income", "IRAS Property Tax", "IRAS Stamp Duty (general)"],
    "negative-gearing-singapore-property.html": ["IRAS Rental Income"],
    "sc-pr-couple-second-property-absd.html": ["IRAS ABSD", "MAS TDSR Framework", "MAS Property Market Measures"],
    "property-one-name-vs-joint-name-2026.html": ["IRAS ABSD", "IRAS Stamp Duty (general)"],
    "joint-tenancy-vs-tenancy-in-common-sg.html": ["SLA Residential Property Act", "IRAS Stamp Duty (general)"],
}


def build_sources_block(source_keys):
    li_items = []
    for key in source_keys:
        url, label = SOURCES[key]
        li_items.append(
            f'    <li style="font-size:.85rem;"><a href="{url}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);">{label}</a></li>'
        )
    lis = "\n".join(li_items)
    return f"""<section style="max-width:3xl;margin:3rem auto 0;padding:2rem 1.5rem;border-top:1px solid var(--rule);">
  <h2 style="font-size:1rem;font-weight:600;color:var(--ink);margin-bottom:1rem;">Sources &amp; References</h2>
  <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:.5rem;">
{lis}
  </ul>
</section>
"""


def process_file(filename, source_keys):
    filepath = os.path.join(INSIGHTS_DIR, filename)
    if not os.path.exists(filepath):
        print(f"  MISSING: {filename}")
        return "missing"

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Skip if already has a Sources section
    if "Sources &amp; References" in content or "Sources & References" in content:
        print(f"  SKIP (already has Sources): {filename}")
        return "skipped"

    sources_block = build_sources_block(source_keys)

    # Find injection point: before <aside or before <footer
    aside_match = re.search(r'<aside[\s>]', content)
    footer_match = re.search(r'<footer[\s>]', content)

    if aside_match:
        inject_pos = aside_match.start()
        anchor = "<aside"
    elif footer_match:
        inject_pos = footer_match.start()
        anchor = "<footer"
    else:
        print(f"  NO ANCHOR (aside/footer not found): {filename}")
        return "no_anchor"

    new_content = content[:inject_pos] + sources_block + "\n" + content[inject_pos:]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"  UPDATED (before {anchor}): {filename}")
    return "updated"


def main():
    results = {"updated": [], "skipped": [], "missing": [], "no_anchor": []}

    for filename, source_keys in FILE_SOURCES.items():
        status = process_file(filename, source_keys)
        results[status].append(filename)

    print("\n--- SUMMARY ---")
    print(f"Updated:    {len(results['updated'])}")
    print(f"Skipped:    {len(results['skipped'])}")
    print(f"Missing:    {len(results['missing'])}")
    print(f"No anchor:  {len(results['no_anchor'])}")

    if results["missing"]:
        print("\nMissing files:")
        for f in results["missing"]:
            print(f"  {f}")
    if results["no_anchor"]:
        print("\nNo anchor found:")
        for f in results["no_anchor"]:
            print(f"  {f}")


if __name__ == "__main__":
    main()
