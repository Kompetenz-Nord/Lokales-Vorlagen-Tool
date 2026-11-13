"""Lokale Selbstprüfung ohne Oberfläche: ``python app.py --selftest``.

Prüft Vorlagen, OCR, lokale Ablage und (optional) die lokale KI. Gibt nur
technische Statusangaben aus – keine Dokumentinhalte.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def run_selftest(out=None) -> int:  # noqa: ANN001
    out = out or sys.stdout
    from .. import APP_NAME, __version__
    from .paths import AppPaths
    from .service import AppService

    ok = True

    def line(status: str, text: str) -> None:
        print(f"[{status:^6}] {text}", file=out)

    print(f"{APP_NAME} {__version__} – Selbstprüfung", file=out)
    paths = AppPaths.default()
    try:
        svc = AppService(paths, configure_logs=False)
    except Exception as exc:  # noqa: BLE001
        line("FEHLER", f"Datenablage nicht nutzbar: {getattr(exc, 'user_message', type(exc).__name__)}")
        return 2
    try:
        line("OK", f"Datenordner: {svc.paths.data}")
        line("OK", f"Exportordner: {svc.export_dir}")
        for choice in svc.template_choices(refresh=True):
            status = "OK" if choice.available else "INFO"
            kind = {"form": "Originalvorlage", "letter": "Brief DIN 5008", None: "—"}[choice.kind]
            line(status, f"Vorlage {choice.number}: {choice.title} – {choice.status.value} ({kind})")
        for name, (available, reason) in svc.ocr_status().items():
            line("OK" if available else "INFO", f"OCR {name}: {reason}")
        tess_ok = svc.ocr_status().get("tesseract", (False, ""))[0]
        if not tess_ok:
            line("WARN", "Ohne Tesseract können nur PDFs mit Textebene ausgewertet werden.")
        ai = svc.local_ai_status()
        line("INFO", f"Lokale KI: {ai.state.value} – {ai.message}")
        # Funktionsprobe: Musterfall in einem temporären Datenordner befüllen (synthetische Daten)
        with tempfile.TemporaryDirectory(prefix="lvt-selftest-") as tmp:
            probe = AppService(
                AppPaths(resources=paths.resources, data=Path(tmp) / "d", exports=Path(tmp) / "e"),
                configure_logs=False,
                guard_network=False,
            )
            try:
                case = probe.create_sample_case()
                number = case.contract_fields.get("contract.number")
                if number is not None and number.normalized_value:
                    line("OK", "Musterfall: Texterkennung und Extraktion funktionieren.")
                else:
                    line("FEHLER", "Musterfall: Vertragsnummer nicht erkannt.")
                    ok = False
            finally:
                probe.close()
    finally:
        svc.close()
    line("OK" if ok else "FEHLER", "Selbstprüfung abgeschlossen.")
    return 0 if ok else 1
