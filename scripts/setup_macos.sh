#!/usr/bin/env bash
# Entwicklungs-/Startumgebung unter macOS einrichten (lokal, ohne Cloud).
#   ./scripts/setup_macos.sh                 # venv + Abhaengigkeiten, Tesseract nur pruefen
#   ./scripts/setup_macos.sh --install-tesseract   # zusaetzlich: brew install tesseract tesseract-lang
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
echo "Python: $($PYTHON --version)"
if [ ! -d .venv ]; then
  echo "Lege virtuelle Umgebung .venv an ..."
  "$PYTHON" -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"

if command -v tesseract >/dev/null 2>&1; then
  echo "Tesseract gefunden: $(command -v tesseract)"
  tesseract --list-langs || true
elif [ "${1:-}" = "--install-tesseract" ]; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew fehlt. Siehe https://brew.sh – danach Skript erneut starten." >&2
    exit 1
  fi
  brew install tesseract tesseract-lang
else
  echo "Tesseract OCR nicht gefunden. Die App startet trotzdem (nur PDFs mit Textebene)."
  echo "Installation: ./scripts/setup_macos.sh --install-tesseract"
fi

echo
echo "Fertig. Start der App:  ./.venv/bin/python app.py"
echo "Selbsttest:            ./.venv/bin/python app.py --selftest"
