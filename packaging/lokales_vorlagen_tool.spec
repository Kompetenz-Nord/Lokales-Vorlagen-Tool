# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spezifikation fuer Windows und macOS (auch unter Linux baubar).
# Aufruf aus dem Projektordner:  pyinstaller --noconfirm packaging/lokales_vorlagen_tool.spec
#
# Mitgeliefert werden: assets/ (Originalvorlagen, Schriften), config/ (Katalog, Feldzuordnungen).
# NICHT mitgeliefert: Tesseract (extern installieren), Ollama + Modellgewichte (optional, separat),
# Docling/RapidOCR (optional). Keine Kundendaten, keine user_data/, keine exports/.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH wird von PyInstaller gesetzt)
APP_NAME = "LokalesVorlagenTool"

datas = [
    (str(ROOT / "assets" / "templates" / "originals"), "assets/templates/originals"),
    (str(ROOT / "assets" / "fonts"), "assets/fonts"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "docs"), "docs"),  # Anleitungen (z. B. LOKALE_KI.md, BEDIENUNG.md) für die Hilfe-Schaltflächen
]
icons_dir = ROOT / "assets" / "icons"
if icons_dir.exists():
    datas.append((str(icons_dir), "assets/icons"))

excludes = [
    # optionale/schwere Pakete nicht automatisch einsammeln
    "docling", "torch", "torchvision", "transformers", "rapidocr_onnxruntime", "onnxruntime",
    "paddle", "paddleocr", "tkinter", "matplotlib", "IPython", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick3D",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=["pymupdf", "pypdfium2", "pypdf", "pytesseract", "insurance_app.ui.app"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

icon_file = None
if sys.platform == "darwin" and (icons_dir / "app.icns").exists():
    icon_file = str(icons_dir / "app.icns")
elif sys.platform.startswith("win") and (icons_dir / "app.ico").exists():
    icon_file = str(icons_dir / "app.ico")

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_file,
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME,
)
if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=icon_file,
        bundle_identifier="de.lokal.vorlagentool",
        info_plist={
            "CFBundleDisplayName": "Lokales Vorlagen-Tool",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # PySide6 6.12 liefert macOS-Pakete nur ab macOS 14 (Wheel-Tag macosx_14_0, PyPI geprüft am 13.11.2026)
            "LSMinimumSystemVersion": "14.0",
        },
    )
