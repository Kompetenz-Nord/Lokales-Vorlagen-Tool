"""Programmstart (Desktop-Oberfläche oder Kommandozeilen-Hilfen)."""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="lokales-vorlagen-tool", description=f"{APP_NAME} {__version__}")
    p.add_argument("--version", action="store_true", help="Version anzeigen und beenden")
    p.add_argument(
        "--selftest",
        action="store_true",
        help="Lokale Selbstprüfung (Vorlagen, OCR, Speicher) ohne Oberfläche ausführen",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.selftest:
        from .core.selftest import run_selftest

        return run_selftest()
    from .ui.app import run_gui

    return run_gui()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
