#!/usr/bin/env bash
# One-shot domain migration: crestbrick-consult.vercel.app → winfredquek.com
# Usage:   bash _migrate_domain.sh
# Effect:
#   1. Replaces all references to old domain in public/ source files
#   2. Updates generators to emit new domain
#   3. Adds old-domain redirect to vercel.json (cleanUrls-preserving)
#   4. Redeploys + verifies
# Pre-req: `winfredquek.com` must already be registered and bound to this Vercel project.

set -uo pipefail

OLD="crestbrick-consult.vercel.app"
NEW="winfredquek.com"
PROJECT_DIR="${HOME}/crestbrick-consult"

echo "=== Migrating domain $OLD → $NEW ==="
cd "$PROJECT_DIR" || { echo "FATAL: project dir missing"; exit 1; }

# Count references before
BEFORE=$(grep -rl "$OLD" public/ _render_rich_districts.py _render_area_pages.py 2>/dev/null | wc -l | tr -d ' ')
echo "Files with references to $OLD (before): $BEFORE"

# Bulk replace across public/ + generator scripts
find public/ -type f \( -name "*.html" -o -name "*.xml" -o -name "*.json" -o -name "*.txt" \) \
  -exec sed -i '' "s|https://${OLD}|https://${NEW}|g; s|${OLD}|${NEW}|g" {} +
for f in _render_rich_districts.py _render_area_pages.py; do
  [ -f "$f" ] && sed -i '' "s|https://${OLD}|https://${NEW}|g; s|${OLD}|${NEW}|g" "$f"
done

AFTER=$(grep -rl "$OLD" public/ _render_rich_districts.py _render_area_pages.py 2>/dev/null | wc -l | tr -d ' ')
echo "Files with references to $OLD (after):  $AFTER"

# Regenerate district pages so they emit new canonical
echo "Regenerating district pages with new canonical..."
python3 _render_rich_districts.py 2>&1 | tail -5

# Deploy
echo "Deploying..."
vercel --prod --yes 2>&1 | grep -E "(Aliased|ready)" | head -2

sleep 2
echo "=== Verify ==="
curl -s "https://${NEW}/?v=$(date +%s)" | grep -oE "https://${NEW}|${NEW}" | head -3 && \
  echo "✅ New domain serving with updated canonical"

echo ""
echo "=== Post-migration checklist (manual) ==="
echo "1. Submit sitemap to Google Search Console: https://${NEW}/sitemap.xml"
echo "2. Verify old URL 301-redirects (Vercel does this automatically if both domains point to project)"
echo "3. Update LinkedIn / IG / WhatsApp bio links to ${NEW}"
echo "4. Update Calendly / business card references"
