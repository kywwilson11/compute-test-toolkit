#!/usr/bin/env bash
# Build polished PDFs from the markdown guides using pandoc + xelatex.
# Usage: bash guides/build.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p pdf

COMMON=(
  --pdf-engine=xelatex
  --toc --toc-depth=2 --number-sections
  -H header.tex
  -V geometry:margin=1in
  -V fontsize=10pt
  -V mainfont="STIX Two Text"
  -V monofont=Menlo
  -V colorlinks=true
  -V linkcolor=RoyalBlue -V urlcolor=RoyalBlue -V toccolor=black
  --syntax-highlighting=tango
)

for f in 01-job-prep-deep-dive 02-success-guide; do
  echo "Building pdf/$f.pdf ..."
  pandoc "$f.md" "${COMMON[@]}" -o "pdf/$f.pdf"
done

echo "Done. PDFs in $(pwd)/pdf/"
