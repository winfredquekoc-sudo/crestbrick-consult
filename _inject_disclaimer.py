#!/usr/bin/env python3
"""
Inject disclaimer block into every HTML page under /public/
Rules:
  - Skip: 404.html, privacy.html, unsubscribe.html, thank-you.html
  - Idempotent: skip if disclaimer-block already present
  - Insert just before </footer> if present, else just before </body>
"""

import os
import re

PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")

SKIP_FILES = {"404.html", "privacy.html", "unsubscribe.html", "thank-you.html"}

DISCLAIMER_HTML = """
  <!-- Disclaimer -->
  <div class="disclaimer-block" style="border-top:1px solid rgba(255,255,255,0.08); margin-top:2rem; padding:1.25rem 1.5rem;">
    <p style="font-size:0.72rem; line-height:1.65; color:#706c64; max-width:900px; margin:0 auto; text-align:center;">
      The information and insights provided on this page are for informational purposes only and are based on Winfred's independent research and views. While we strive to ensure accuracy and reliability, we do not guarantee the completeness, correctness, or timeliness of the data presented. Real estate investments are subject to various risks, including but not limited to market fluctuations, changes in economic conditions, interest rate volatility, regulatory shifts, liquidity constraints, and unforeseen property-specific risks. Past performance is not indicative of future results, and investment outcomes may vary. This page does not constitute investment, financial, or professional advice and should not be relied upon as such. Investors should conduct their own due diligence and seek advice from qualified professionals before making any investment decisions.
    </p>
  </div>
"""

def inject_file(filepath):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    if "disclaimer-block" in content:
        return "skip"

    # Try to insert before </footer>
    footer_match = re.search(r"</footer>", content, re.IGNORECASE)
    body_match = re.search(r"</body>", content, re.IGNORECASE)

    if footer_match:
        insert_pos = footer_match.start()
    elif body_match:
        insert_pos = body_match.start()
    else:
        # No footer or body closing tag — append at end
        content = content + DISCLAIMER_HTML
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return "injected-append"

    new_content = content[:insert_pos] + DISCLAIMER_HTML + content[insert_pos:]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)
    return "injected"


def main():
    injected = 0
    skipped_rule = 0
    already_has = 0
    errors = []

    for root, dirs, files in os.walk(PUBLIC_DIR):
        # Skip non-content dirs
        dirs[:] = [d for d in dirs if d not in {".vercel", "img", "data", "ebooks"}]
        for fname in files:
            if not fname.endswith(".html"):
                continue
            if fname in SKIP_FILES:
                skipped_rule += 1
                continue
            filepath = os.path.join(root, fname)
            try:
                result = inject_file(filepath)
                if result == "skip":
                    already_has += 1
                else:
                    injected += 1
            except Exception as e:
                errors.append(f"{filepath}: {e}")

    print(f"Injected: {injected}")
    print(f"Already had disclaimer: {already_has}")
    print(f"Skipped (rules): {skipped_rule}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    else:
        print("No errors.")

if __name__ == "__main__":
    main()
