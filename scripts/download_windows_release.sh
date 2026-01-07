#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist-windows"
mkdir -p "$DIST_DIR"

TAG="${1:-latest}"
if [[ "$TAG" == "latest" ]]; then
  URL="https://github.com/michaldziwisz/programista/releases/latest/download/programista.exe"
else
  URL="https://github.com/michaldziwisz/programista/releases/download/${TAG}/programista.exe"
fi

OUT_TMP="$DIST_DIR/programista.exe.tmp"
OUT="$DIST_DIR/programista.exe"

echo "Pobieranie: $URL"
curl -fL --retry 3 --retry-delay 2 -o "$OUT_TMP" "$URL"
mv -f "$OUT_TMP" "$OUT"
echo "Gotowe: $OUT"

