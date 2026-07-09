#!/usr/bin/env bash
# Render a .drawio → matching .png using draw.io Desktop.
# The .drawio is the source of truth; this script regenerates the PNG.
#
# Usage:
#   ./render.sh                 # renders architecture.drawio → architecture.png
#   ./render.sh some.drawio     # renders <name>.drawio → <name>.png

set -euo pipefail

DRAWIO="/Applications/draw.io.app/Contents/MacOS/draw.io"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/architecture.drawio}"
OUT="${SRC%.drawio}.png"

if [[ ! -x "$DRAWIO" ]]; then
  echo "draw.io Desktop not found at $DRAWIO" >&2
  echo "Install from https://github.com/jgraph/drawio-desktop/releases/latest" >&2
  exit 1
fi

"$DRAWIO" --export --format png --width 2400 --output "$OUT" "$SRC"
echo "Wrote $OUT"
