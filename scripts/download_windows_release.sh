#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist-windows"
mkdir -p "$DIST_DIR"

TAG="${1:-latest}"
ARCH="${2:-auto}"
FORMAT="${3:-exe}"
ARCH="$(printf '%s' "$ARCH" | tr '[:upper:]' '[:lower:]')"
FORMAT="$(printf '%s' "$FORMAT" | tr '[:upper:]' '[:lower:]')"

if [[ "$ARCH" == "auto" ]]; then
  case "$(uname -m)" in
    aarch64|arm64) ARCH="arm64" ;;
    x86_64|amd64) ARCH="x64" ;;
    *) ARCH="x64" ;;
  esac
fi

if [[ "$TAG" == "latest" ]]; then
  BASE_URL="https://github.com/michaldziwisz/programista/releases/latest/download"
else
  BASE_URL="https://github.com/michaldziwisz/programista/releases/download/${TAG}"
fi

case "$FORMAT" in
  exe|msi)
    ;;
  *)
    echo "Nieznany format: '$FORMAT' (użyj: exe|msi)" >&2
    exit 2
    ;;
esac

case "$ARCH" in
  arm64)
    if [[ "$FORMAT" == "msi" ]]; then
      CANDIDATES=("programista-win-arm64.msi" "programista-win-x64.msi")
    else
      # Prefer native ARM64, but fall back to x64 if release doesn't ship ARM64 yet.
      CANDIDATES=("programista-win-arm64.exe" "programista-arm64.exe" "programista-win-x64.exe" "programista.exe")
    fi
    ;;
  x64)
    if [[ "$FORMAT" == "msi" ]]; then
      CANDIDATES=("programista-win-x64.msi")
    else
      CANDIDATES=("programista-win-x64.exe" "programista.exe")
    fi
    ;;
  *)
    echo "Nieznana architektura: '$ARCH' (użyj: auto|x64|arm64)" >&2
    exit 2
    ;;
esac

OUT_TMP="$DIST_DIR/programista.${FORMAT}.tmp"
OUT="$DIST_DIR/programista.${FORMAT}"

for FILE in "${CANDIDATES[@]}"; do
  URL="${BASE_URL}/${FILE}"
  echo "Pobieranie ($ARCH): $URL"

  rm -f "$OUT_TMP"
  if curl -fL --retry 3 --retry-delay 2 -o "$OUT_TMP" "$URL"; then
    mv -f "$OUT_TMP" "$OUT" || {
      echo "Nie mogę nadpisać: $OUT (zamknij uruchomioną aplikację / zwolnij blokadę pliku i spróbuj ponownie)" >&2
      exit 1
    }
    if [[ "$FORMAT" == "exe" && "$ARCH" == "arm64" && "$FILE" != *arm64* ]]; then
      echo "Uwaga: brak natywnej binarki ARM64 w tym release — pobrano wariant x64 (emulacja)." >&2
    fi
    echo "Gotowe: $OUT (źródło: $FILE)"
    exit 0
  fi
done

echo "Nie znaleziono binarki dla arch=$ARCH, tag=$TAG." >&2
exit 1
