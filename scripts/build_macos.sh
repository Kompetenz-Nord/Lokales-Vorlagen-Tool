#!/usr/bin/env bash
# Baut die macOS-App (LokalesVorlagenTool.app) mit PyInstaller und optional ein DMG.
#   ./scripts/build_macos.sh          # .app bauen
#   ./scripts/build_macos.sh --dmg    # zusaetzlich DMG erzeugen (hdiutil)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Bitte zuerst ./scripts/setup_macos.sh ausfuehren." >&2; exit 1; }
PY=./.venv/bin/python

"$PY" -m pytest -q -m "not slow"
"$PY" -m PyInstaller --noconfirm --clean packaging/lokales_vorlagen_tool.spec

APP="dist/LokalesVorlagenTool.app"
"$APP/Contents/MacOS/LokalesVorlagenTool" --version
# Ad-hoc-Signatur (ohne Apple-Entwicklerkonto). Fuer die Weitergabe: echte Signatur + Notarisierung.
codesign --force --deep --sign - "$APP" || echo "Hinweis: codesign nicht verfuegbar/fehlgeschlagen."

if [ "${1:-}" = "--dmg" ]; then
  rm -f dist/LokalesVorlagenTool-macos.dmg
  hdiutil create -volname "Lokales Vorlagen-Tool" -srcfolder "$APP" -ov -format UDZO dist/LokalesVorlagenTool-macos.dmg
  echo "DMG: dist/LokalesVorlagenTool-macos.dmg"
fi
echo "Build fertig: $APP"
echo "Hinweis: Tesseract OCR ist nicht enthalten (brew install tesseract tesseract-lang)."
