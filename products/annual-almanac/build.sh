#!/usr/bin/env bash
# SG Property Almanac — PDF build script
# Author: Winfred Quek / Crestbrick
#
# Primary path: Pandoc + XeLaTeX with brand template (recommended)
# Fallback path: Python markdown -> HTML -> Chrome headless -> PDF
#
# Usage:
#   ./build.sh                        # builds DRAFT pdf
#   ./build.sh release                # builds release pdf with year stamp

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/almanac-2026.md"
TEMPLATE_TEX="$ROOT/template.tex"
TEMPLATE_CSS="$ROOT/template.css"
MODE="${1:-draft}"

if [[ "$MODE" == "release" ]]; then
  OUT="$ROOT/sg-property-almanac-2026.pdf"
else
  OUT="$ROOT/sg-property-almanac-2026-DRAFT.pdf"
fi

# ---- Path A: Pandoc + XeLaTeX (preferred) -------------------------------
if command -v pandoc >/dev/null 2>&1 && command -v xelatex >/dev/null 2>&1; then
  echo "[build] Using Pandoc + XeLaTeX"
  pandoc "$SRC" \
    --from=markdown \
    --pdf-engine=xelatex \
    --template="$TEMPLATE_TEX" \
    --toc --toc-depth=2 \
    --variable=mainfont:"Fraunces" \
    --variable=sansfont:"Inter" \
    --variable=monofont:"JetBrains Mono" \
    --variable=fontsize:11pt \
    --variable=geometry:"a4paper,margin=2.2cm" \
    --variable=linkcolor:"NavyBrand" \
    --variable=documentclass:article \
    -o "$OUT"
  echo "[build] Wrote $OUT"
  exit 0
fi

# ---- Path B: Python markdown -> Chrome headless -> PDF (fallback) -------
echo "[build] Pandoc/XeLaTeX not found. Using Chrome headless fallback."

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [[ ! -x "$CHROME" ]]; then
  echo "[build] ERROR: neither pandoc+xelatex nor Chrome are available." >&2
  echo "[build] Install pandoc + MacTeX:" >&2
  echo "         brew install pandoc && brew install --cask mactex-no-gui" >&2
  exit 1
fi

if ! python3 -c "import markdown" >/dev/null 2>&1; then
  echo "[build] Installing python markdown..."
  pip3 install --break-system-packages --quiet markdown
fi

HTML="$ROOT/.build-almanac.html"
BODY="$ROOT/.build-body.html"

python3 -c "
import markdown, sys
with open('$SRC') as f:
    md = f.read()
html = markdown.markdown(md, extensions=['tables','fenced_code','toc','attr_list','sane_lists'])
open('$BODY','w').write(html)
"

cat > "$HTML" <<HTMLDOC
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SG Property Almanac 2026</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --navy: #0b1f3a;
    --navy-2: #14305a;
    --gold: #c9a14a;
    --ink: #1a1a1a;
    --muted: #5a6470;
    --rule: #d8d8d8;
    --bg-quote: #f6f1e3;
  }
  @page { size: A4; margin: 22mm 20mm 24mm 20mm;
          @bottom-center { content: counter(page); color: #5a6470; font-family: Inter, sans-serif; font-size: 9pt; }
          @top-right { content: "SG Property Almanac 2026 - Crestbrick"; color: #5a6470; font-family: Inter, sans-serif; font-size: 8.5pt; letter-spacing: .04em; }
        }
  html, body { color: var(--ink); }
  body { font-family: 'Fraunces', Georgia, serif; font-size: 11pt; line-height: 1.55; }
  h1, h2, h3, h4 { font-family: 'Fraunces', Georgia, serif; color: var(--navy); }
  h1 { font-size: 26pt; line-height: 1.15; margin-top: 28pt; padding-top: 18pt; border-top: 3px solid var(--gold); page-break-before: always; }
  h1:first-of-type { page-break-before: avoid; border-top: none; padding-top: 0; }
  h2 { font-size: 17pt; margin-top: 22pt; color: var(--navy-2); }
  h3 { font-size: 13pt; margin-top: 16pt; color: var(--navy-2); }
  p, li { font-family: 'Fraunces', Georgia, serif; }
  strong { color: var(--navy); }
  hr { border: none; border-top: 1px solid var(--rule); margin: 18pt 0; }
  blockquote {
    background: var(--bg-quote);
    border-left: 4px solid var(--gold);
    padding: 10pt 14pt;
    margin: 14pt 0;
    font-family: 'Fraunces', Georgia, serif;
    color: var(--navy);
  }
  table { width: 100%; border-collapse: collapse; margin: 12pt 0; font-family: 'Inter', sans-serif; font-size: 9.5pt; }
  th, td { padding: 6pt 8pt; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: top; }
  th { background: var(--navy); color: #fff; font-weight: 600; letter-spacing: .02em; }
  tr:nth-child(even) td { background: #fafafa; }
  code { font-family: 'JetBrains Mono', Menlo, monospace; font-size: 9.5pt; }
  em { color: var(--muted); }
  ul, ol { padding-left: 1.2em; }
  /* Cover */
  .cover { page-break-after: always; padding-top: 20mm; }
  .cover .kicker { font-family: 'Inter', sans-serif; font-size: 10pt; letter-spacing: .25em; text-transform: uppercase; color: var(--gold); }
  .cover .title { font-family: 'Fraunces', Georgia, serif; font-size: 48pt; line-height: 1.05; color: var(--navy); margin: 8pt 0 12pt; font-weight: 600; }
  .cover .vol { font-family: 'Inter', sans-serif; font-size: 12pt; color: var(--navy-2); letter-spacing: .12em; text-transform: uppercase; }
  .cover .by { font-family: 'Inter', sans-serif; font-size: 11pt; color: var(--muted); margin-top: 80pt; }
</style>
</head>
<body>
HTMLDOC

cat "$BODY" >> "$HTML"
echo "</body></html>" >> "$HTML"

# Render with Chrome headless
TMPOUT="$ROOT/.build-out.pdf"
"$CHROME" \
  --headless=new \
  --disable-gpu \
  --no-pdf-header-footer \
  --print-to-pdf-no-header \
  --print-to-pdf="$TMPOUT" \
  --virtual-time-budget=10000 \
  "file://$HTML" >/dev/null 2>&1

mv "$TMPOUT" "$OUT"
rm -f "$HTML" "$BODY"
echo "[build] Wrote $OUT"
