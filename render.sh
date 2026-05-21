#!/usr/bin/env bash
# Render architecture.drawio → architecture.png using draw.io Desktop.
# architecture.drawio is the source of truth; this script regenerates the PNG.

set -euo pipefail

DRAWIO="/Applications/draw.io.app/Contents/MacOS/draw.io"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/architecture.drawio"
OUT="$HERE/architecture.png"

if [[ ! -x "$DRAWIO" ]]; then
  echo "draw.io Desktop not found at $DRAWIO" >&2
  echo "Install from https://github.com/jgraph/drawio-desktop/releases/latest" >&2
  exit 1
fi

"$DRAWIO" --export --format png --width 2400 --output "$OUT" "$SRC"
echo "Wrote $OUT"
