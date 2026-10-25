#!/usr/bin/env bash
# Build the GitHub Pages site into OUT (default _site). Used by .github/workflows/pages.yml and locally.
#
#   /            landing page
#   /design/     the design document
#   /app/        the console
#   /help/       the DITA help center (DITA-OT HTML5, strict) and csh.json for the console's help panel
#   /migrated/   what MANIFEST produced: the TPU sample docs and MANIFEST's own help, each with its report
#   /corpus/     the DITA source the console shows beside the migrated MyST
#
# Needs DITA_HOME and LYCHEE (see the README) and the [sphinx] extra.
set -euo pipefail
OUT="${1:-_site}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
cd "$ROOT"
rm -rf "$OUT" && mkdir -p "$OUT"/{app,help,migrated,design}
OUT="$(cd "$OUT" && pwd)"

cp site/index.html "$OUT/index.html"
cp design/index.html "$OUT/design/index.html"
cp -r console/* "$OUT/app/"
mkdir -p "$OUT/app/assets/fonts" "$OUT/assets/fonts"
cp manifest/assets/fonts/*.woff2 "$OUT/app/assets/fonts/"
cp manifest/assets/fonts/*.woff2 "$OUT/assets/fonts/"

echo "Help: DITA-OT HTML5, strict"
python help/tools/openapi2dita.py --check
"$DITA_HOME/bin/dita" -i help/manifest-help.ditamap -f html5 -o "$OUT/help" --processing-mode=strict
python help/tools/build_csh.py "$OUT/help"

echo "Source for the console's side-by-side view, and sample uploads"
mkdir -p "$OUT/source/corpus" "$OUT/samples"
cp -r corpus/tpu corpus/media "$OUT/source/corpus/"
cp -r help "$OUT/source/help"
(cd corpus && zip -qr "$OUT/samples/tpu-dita.zip" tpu media)
(cd help && zip -qr "$OUT/samples/manifest-help-dita.zip" manifest-help.ditamap keys reuse topics)

for pair in "tpu:corpus/tpu/tpu.ditamap" "help:help/manifest-help.ditamap"; do
  name="${pair%%:*}" map="${pair#*:}"
  echo "MANIFEST migrates $map"
  manifest migrate "$map" --out "$WORK/$name" --rules rules || test $? -le 1
  cp -r "$WORK/$name/check/sphinx-html/unfiltered" "$OUT/migrated/$name"
  cp -r "$WORK/$name/report" "$OUT/migrated/$name-report"
done
python site/fill_kpis.py "$OUT/index.html" "$OUT/migrated/tpu-report/report.json"
touch "$OUT/.nojekyll"
echo "Built $OUT"
