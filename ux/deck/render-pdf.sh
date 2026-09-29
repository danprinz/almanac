#!/bin/zsh
# Render the deck to a single landscape PDF, one slide per page.
#
# The page size and the forced background printing live in the deck's own
# @media print block, not here -- without them Chrome slices each 16:9 slide
# across portrait pages and drops the paper fill and the graph-paper grid,
# which are content in this design rather than decoration.
set -e
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE="${0:A:h}"
OUT="$HERE/../five-situations.pdf"
"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --virtual-time-budget=20000 \
  --print-to-pdf="$OUT" \
  "file://$HERE/index.html" >/dev/null 2>&1
echo "$OUT"
