#!/usr/bin/env bash
# Build the comprehensive study guide PDF (report class = real chapters).
# Run from guides/:  ./build-study-guide.sh
set -euo pipefail
cd "$(dirname "$0")"

python3 assemble_study_guide.py
mkdir -p pdf

pandoc study-guide/_assembled.md \
  --pdf-engine=xelatex \
  -V documentclass=report \
  -V geometry:margin=1in -V fontsize=10pt \
  -V colorlinks=true -V linkcolor=RoyalBlue -V urlcolor=RoyalBlue -V toccolor=black \
  --toc --toc-depth=2 --number-sections \
  -H header.tex \
  --lua-filter=breakcode.lua \
  --syntax-highlighting=tango \
  -o pdf/zoox-study-guide.pdf 2> >(grep -i "missing character" | sort -u >&2 || true)

echo "built pdf/zoox-study-guide.pdf  ($(pdfinfo pdf/zoox-study-guide.pdf 2>/dev/null | awk '/Pages/{print $2}') pages)"
