"""Tesseract-Backend (lokal, ``deu+eng``) mit Wortkonfidenzen.

Datenschutz: Das Seitenbild wird **über stdin** an das Tesseract-Programm
übergeben und das Ergebnis (TSV) **über stdout** gelesen. Es entstehen keine
temporären Dateien (``pytesseract`` würde Bilder in den Temp-Ordner
schreiben und wird deshalb hier nicht verwendet). Kein Netzwerkzugriff.

Ablauf je Seite
---------------
1. Graustufen, Vergrößern auf ca. 300 dpi, Begradigen (``ocr.preprocess``).
2. Tesseract TSV (``--psm`` wie eingestellt) -> Wörter mit Konfidenz
   (``conf / 100``; Einträge mit ``conf < 0`` oder leerem Text entfallen).
   Wortboxen werden in das übergebene Bild zurückgerechnet (Begradigung und
   Vergrößerung umgekehrt) und dann in Seitenkoordinaten skaliert.
3. Ist die mittlere Konfidenz < 0,5 (oder gibt es keine Wörter), prüft die
   Lageerkennung (OSD), ob die Seite um 90/180/270° gedreht ist. Dann wird die
   aufgerichtete Seite erneut erkannt und das bessere Ergebnis behalten
   (``PageText.rotation`` = Drehung im Uhrzeigersinn, Koordinaten beziehen sich
   dann auf die aufgerichtete Seite).
4. Qualität über ``ocr.page_quality``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image

from ..core.models import ExtractionMethod, PageText, TextLayerOrigin, Word
from ..core.settings import Settings
from .base import OcrError, OcrUnavailableError, scale_bbox, weighted_mean_confidence, words_to_text
from .page_quality import assess_text_quality, text_statistics
from .preprocess import deskew, rotate_point_back, to_grayscale, upscale_if_small

log = logging.getLogger(__name__)

#: Unter dieser mittleren Konfidenz wird die Seitenlage geprüft (OSD).
OSD_RETRY_CONFIDENCE = 0.5
#: Zeitlimit je Tesseract-Aufruf (Sekunden).
DEFAULT_TIMEOUT_S = 240.0
_PROBE_TIMEOUT_S = 20.0

_LANG_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*$")
_VERSION_PATTERN = re.compile(r"tesseract\s+v?(\d[\w.\-]*)", re.IGNORECASE)
_ROTATE_PATTERN = re.compile(r"^Rotate:\s*(\d+)", re.MULTILINE)

MSG_NOT_INSTALLED = (
    "Tesseract ist nicht installiert oder wurde nicht gefunden. "
    "Bitte Tesseract installieren oder den Pfad in den Einstellungen angeben."
)
MSG_NOT_STARTABLE = "Tesseract konnte nicht gestartet werden. Bitte Installation und Pfad prüfen."


def _fallback_paths() -> list[Path]:
    """Übliche Installationsorte (GUI-Programme haben oft einen kurzen PATH)."""
    if sys.platform.startswith("win"):
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Tesseract-OCR"
            / "tesseract.exe",
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
        return candidates
    if sys.platform == "darwin":
        return [
            Path("/opt/homebrew/bin/tesseract"),
            Path("/usr/local/bin/tesseract"),
            Path("/opt/local/bin/tesseract"),
        ]
    return [Path("/usr/bin/tesseract"), Path("/usr/local/bin/tesseract")]


@dataclass(frozen=True)
class _Probe:
    command: str | None
    version: str | None
    languages: tuple[str, ...]
    ok: bool
    reason: str


@dataclass
class _Attempt:
    rotation: int
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    text: str = ""
    mean_confidence: float | None = None

    @property
    def score(self) -> float:
        if self.mean_confidence is None:
            return 0.0
        chars = text_statistics(self.text).meaningful_chars
        return self.mean_confidence * min(1.0, chars / 20.0)


@dataclass(frozen=True)
class _PixelWord:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float
    block: int
    paragraph: int
    line: int


class TesseractBackend:
    """Lokale Texterkennung mit dem Tesseract-Programm (siehe Moduldokumentation)."""

    name = "tesseract"
    method = ExtractionMethod.TESSERACT

    def __init__(
        self,
        languages: str = "deu+eng",
        tesseract_cmd: str | None = None,
        psm: int = 3,
        *,
        settings: Settings | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not _LANG_PATTERN.match(languages):
            raise ValueError("Ungültige Sprachangabe für Tesseract (Beispiel: 'deu+eng').")
        if not 0 <= int(psm) <= 13:
            raise ValueError("Ungültiger Seitensegmentierungsmodus (psm 0–13).")
        self.languages = languages
        self.tesseract_cmd = tesseract_cmd or None
        self.psm = int(psm)
        self.settings = settings or Settings()
        self.timeout_s = float(timeout_s)
        self._probe_cache: _Probe | None = None
        self._lock = threading.Lock()

    # -- Verfügbarkeit ----------------------------------------------------
    def _resolve_command(self) -> str | None:
        if self.tesseract_cmd:
            candidate = Path(self.tesseract_cmd).expanduser()
            if candidate.is_file():
                return str(candidate)
            return shutil.which(self.tesseract_cmd)
        found = shutil.which("tesseract")
        if found:
            return found
        for candidate in _fallback_paths():
            if candidate.is_file():
                return str(candidate)
        return None

    def _run(
        self, command: str, args: list[str], data: bytes | None, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        kwargs: dict[str, object] = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run(  # noqa: S603 - fester Programmpfad, keine Shell
                [command, *args],
                input=data,
                capture_output=True,
                timeout=timeout,
                check=False,
                **kwargs,  # type: ignore[arg-type]
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Die Texterkennung hat zu lange gedauert und wurde abgebrochen.") from exc
        except OSError as exc:
            raise OcrUnavailableError(MSG_NOT_STARTABLE) from exc

    def _probe(self) -> _Probe:
        with self._lock:
            if self._probe_cache is not None:
                return self._probe_cache
            self._probe_cache = self._do_probe()
            log.info("Tesseract verfügbar=%s version=%s", self._probe_cache.ok, self._probe_cache.version)
            return self._probe_cache

    def _do_probe(self) -> _Probe:
        command = self._resolve_command()
        if command is None:
            return _Probe(None, None, (), False, MSG_NOT_INSTALLED)
        try:
            proc = self._run(command, ["--version"], None, _PROBE_TIMEOUT_S)
        except (OcrError, OcrUnavailableError):
            return _Probe(command, None, (), False, MSG_NOT_STARTABLE)
        output = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace")
        match = _VERSION_PATTERN.search(output)
        if proc.returncode != 0 or match is None:
            return _Probe(command, None, (), False, MSG_NOT_STARTABLE)
        version = match.group(1)
        try:
            proc = self._run(command, ["--list-langs"], None, _PROBE_TIMEOUT_S)
        except (OcrError, OcrUnavailableError):
            return _Probe(command, version, (), False, MSG_NOT_STARTABLE)
        listing = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace").splitlines()
        languages = tuple(
            line.strip() for line in listing if line.strip() and " " not in line.strip() and ":" not in line
        )
        missing = [lang for lang in self.languages.split("+") if lang not in languages]
        if missing:
            if len(missing) == 1:
                reason = (
                    f"Sprachpaket '{missing[0]}' fehlt. Bitte das Tesseract-Sprachpaket "
                    f"'{missing[0]}' installieren."
                )
            else:
                names = ", ".join(f"'{m}'" for m in missing)
                reason = f"Sprachpakete {names} fehlen. Bitte diese Tesseract-Sprachpakete installieren."
            return _Probe(command, version, languages, False, reason)
        return _Probe(
            command, version, languages, True, f"Tesseract {version} bereit (Sprachen: {self.languages})."
        )

    def is_available(self) -> tuple[bool, str]:
        probe = self._probe()
        return probe.ok, probe.reason

    def version(self) -> str | None:
        return self._probe().version

    def available_languages(self) -> list[str]:
        """Installierte Tesseract-Sprachen (leer, wenn Tesseract fehlt)."""
        return list(self._probe().languages)

    def reset_cache(self) -> None:
        """Verfügbarkeit beim nächsten Aufruf neu prüfen (z. B. nach Pfadänderung)."""
        with self._lock:
            self._probe_cache = None

    @property
    def engine_version(self) -> str:
        return f"tesseract {self.version() or '?'} ({self.languages})"

    # -- Erkennung --------------------------------------------------------
    @staticmethod
    def _encode(image: Image.Image) -> bytes:
        buffer = BytesIO()
        image.save(buffer, format="PPM")  # unkomprimiert (PGM bei Graustufen): schnell, nur im Speicher
        return buffer.getvalue()

    def _tsv(self, command: str, image: Image.Image, dpi: float) -> list[_PixelWord]:
        args = [
            "stdin",
            "stdout",
            "-l",
            self.languages,
            "--psm",
            str(self.psm),
            "--dpi",
            str(int(min(max(dpi, 70), 2400))),
            "tsv",
        ]
        proc = self._run(command, args, self._encode(image), self.timeout_s)
        if proc.returncode != 0:
            log.warning("Tesseract beendet mit Code %d", proc.returncode)
            raise OcrError("Die Texterkennung ist fehlgeschlagen.")
        words: list[_PixelWord] = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines()[1:]:
            parts = line.split("\t", 11)
            if len(parts) < 12 or parts[0] != "5":
                continue
            token = parts[11].strip()
            try:
                conf = float(parts[10])
                left, top, width, height = (int(parts[i]) for i in (6, 7, 8, 9))
                block, paragraph, line_no = int(parts[2]), int(parts[3]), int(parts[4])
            except ValueError:
                continue
            if conf < 0 or not token:
                continue
            words.append(
                _PixelWord(
                    text=token,
                    x0=float(left),
                    y0=float(top),
                    x1=float(left + width),
                    y1=float(top + height),
                    confidence=max(0.0, min(1.0, conf / 100.0)),
                    block=block,
                    paragraph=paragraph,
                    line=line_no,
                )
            )
        return words

    def _osd_rotation(self, command: str, image: Image.Image) -> int | None:
        if "osd" not in self._probe().languages:
            return None
        try:
            proc = self._run(
                command, ["stdin", "stdout", "--psm", "0", "-l", "osd"], self._encode(image), self.timeout_s
            )
        except OcrError:
            return None
        match = _ROTATE_PATTERN.search(proc.stdout.decode("utf-8", "replace"))
        if proc.returncode != 0 or match is None:
            return None
        rotation = int(match.group(1)) % 360
        return rotation if rotation in (90, 180, 270) else None

    def _attempt(
        self, command: str, gray: Image.Image, dpi: float, rotation: int, frame_w: float, frame_h: float
    ) -> _Attempt:
        upscaled, factor = upscale_if_small(gray, dpi)
        straight, angle = deskew(upscaled)
        center_x, center_y = straight.width / 2.0, straight.height / 2.0
        scale_x = frame_w / gray.width
        scale_y = frame_h / gray.height

        words: list[Word] = []
        line_index: dict[tuple[int, int, int], int] = {}
        lines_per_block: dict[int, int] = {}
        for pw in self._tsv(command, straight, dpi * factor):
            corners = [
                rotate_point_back(x, y, angle, center_x, center_y)
                for x, y in ((pw.x0, pw.y0), (pw.x1, pw.y0), (pw.x0, pw.y1), (pw.x1, pw.y1))
            ]
            xs = [c[0] / factor for c in corners]
            ys = [c[1] / factor for c in corners]
            key = (pw.block, pw.paragraph, pw.line)
            if key not in line_index:
                line_index[key] = lines_per_block.get(pw.block, 0)
                lines_per_block[pw.block] = line_index[key] + 1
            words.append(
                Word(
                    text=pw.text,
                    bbox=scale_bbox(min(xs), min(ys), max(xs), max(ys), scale_x, scale_y, frame_w, frame_h),
                    confidence=round(pw.confidence, 4),
                    block_no=max(pw.block - 1, 0),
                    line_no=line_index[key],
                )
            )
        return _Attempt(
            rotation=rotation,
            width=frame_w,
            height=frame_h,
            words=words,
            text=words_to_text(words),
            mean_confidence=weighted_mean_confidence(words),
        )

    def recognize(
        self,
        image: Image.Image,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: float,
    ) -> PageText:
        """Seitenbild erkennen (siehe Moduldokumentation)."""
        probe = self._probe()
        if not probe.ok or probe.command is None:
            raise OcrUnavailableError(probe.reason)
        if image.width < 1 or image.height < 1 or page_width <= 0 or page_height <= 0:
            raise OcrError("Das Seitenbild ist leer.")
        started = time.perf_counter()
        gray = to_grayscale(image)
        best = self._attempt(probe.command, gray, dpi, 0, page_width, page_height)

        if best.mean_confidence is None or best.mean_confidence < OSD_RETRY_CONFIDENCE:
            upscaled, _ = upscale_if_small(gray, dpi)
            rotation = self._osd_rotation(probe.command, upscaled)
            if rotation:
                upright = gray.rotate(-rotation, expand=True)
                frame_w, frame_h = (
                    (page_height, page_width) if rotation in (90, 270) else (page_width, page_height)
                )
                retry = self._attempt(probe.command, upright, dpi, rotation, frame_w, frame_h)
                if retry.score > best.score:
                    best = retry

        quality, warnings = assess_text_quality(
            best.text, best.words, TextLayerOrigin.OCR, best.mean_confidence, self.settings
        )
        if best.rotation:
            warnings = [
                f"Die Seite war gedreht ({best.rotation}°) und wurde für die Texterkennung "
                "automatisch ausgerichtet.",
                *warnings,
            ]
        log.info(
            "OCR Seite %d: woerter=%d konfidenz=%s drehung=%d dauer=%.2fs",
            page_number,
            len(best.words),
            f"{best.mean_confidence:.2f}" if best.mean_confidence is not None else "-",
            best.rotation,
            time.perf_counter() - started,
        )
        return PageText(
            page_number=page_number,
            width=best.width,
            height=best.height,
            method=ExtractionMethod.TESSERACT,
            origin=TextLayerOrigin.OCR,
            words=best.words,
            text=best.text,
            mean_confidence=best.mean_confidence,
            quality=quality,
            warnings=warnings,
            engine_version=self.engine_version,
            source_dpi=float(dpi),
            rotation=best.rotation,
        )
