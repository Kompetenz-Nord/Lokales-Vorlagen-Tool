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
3. Seitenlage: Ist das Ergebnis schwach (keine Wörter, sehr wenig Text oder
   mittlere Konfidenz < 0,5) **oder** stehen die Wörter hochkant (Tesseract
   liest quer liegenden Text oft mit hoher Konfidenz), prüft die Lageerkennung
   (OSD, ``--psm 0``), ob die Seite um 90/180/270° gedreht ist. Die
   aufgerichtete Seite wird erneut erkannt und das bessere Ergebnis behalten.
   Fehlt das Sprachpaket ``osd``, werden die Drehungen ausprobiert; eine
   Drehung gilt nur, wenn sie klar am besten ist.
   Die Wortboxen werden danach in die **übergebene (angezeigte) Seite**
   zurückgerechnet: Breite, Höhe und ``PageText.rotation`` bleiben unverändert,
   damit die Fundstelle in der Ansicht an der richtigen Stelle liegt. Die
   Wörter stehen in Lesereihenfolge; die Extraktion erkennt die Leserichtung
   daran (``extraction.layout.detect_text_rotation``).
4. Rauschen: Bleibt das Ergebnis schwach, folgt genau ein weiterer Versuch mit
   einem Medianfilter (``preprocess.denoise_median``). Das bessere Ergebnis
   bleibt, ein Hinweis nennt den Filter.
5. Qualität über ``ocr.page_quality``.

Rechenkerne: Tesseract läuft mit ``OMP_THREAD_LIMIT=1`` (ein Rechenkern), wenn
die Umgebung nichts anderes vorgibt. Ohne Begrenzung konkurrieren die Threads
unter Last so stark, dass eine Seite in die Zeitgrenze laufen kann.
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
from .preprocess import (
    denoise_median,
    deskew,
    rotate_box_back,
    rotate_point_back,
    to_grayscale,
    upscale_if_small,
)

log = logging.getLogger(__name__)

#: Unter dieser mittleren Konfidenz wird die Seitenlage geprüft (OSD).
OSD_RETRY_CONFIDENCE = 0.5
#: Unter so vielen Buchstaben/Ziffern gilt eine Seite als „sehr wenig Text“.
WEAK_TEXT_CHARS = 20
#: Ohne OSD: Eine ausprobierte Drehung muss um diesen Wert besser sein als die nächstbeste.
ROTATION_CLEAR_MARGIN = 0.15
#: Hochkant-Prüfung: Mindestzahl längerer Wörter und Mindestanteil hochkant stehender Boxen.
SIDEWAYS_MIN_WORDS = 5
SIDEWAYS_MIN_SHARE = 0.6
#: Zeitlimit je Tesseract-Aufruf (Sekunden).
DEFAULT_TIMEOUT_S = 240.0
#: Rechenkerne für Tesseract, wenn die Umgebung ``OMP_THREAD_LIMIT`` nicht setzt.
DEFAULT_OMP_THREAD_LIMIT = "1"
_PROBE_TIMEOUT_S = 20.0

_LANG_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*$")
_VERSION_PATTERN = re.compile(r"tesseract\s+v?(\d[\w.\-]*)", re.IGNORECASE)
_ROTATE_PATTERN = re.compile(r"^Rotate:\s*(\d+)", re.MULTILINE)

MSG_NOT_INSTALLED = (
    "Tesseract ist nicht installiert oder wurde nicht gefunden. "
    "Bitte Tesseract installieren oder den Pfad in den Einstellungen angeben."
)
MSG_NOT_STARTABLE = "Tesseract konnte nicht gestartet werden. Bitte Installation und Pfad prüfen."
ROTATED_WARNING = (
    "Die Seite war gedreht ({rotation}°) und wurde für die Texterkennung automatisch ausgerichtet."
)
DENOISED_WARNING = (
    "Die Seite war stark verrauscht. Für die Texterkennung wurde ein Rauschfilter verwendet – "
    "bitte Werte sorgfältig prüfen."
)


def subprocess_env() -> dict[str, str]:
    """Umgebung für den Tesseract-Aufruf: wie der eigene Prozess, aber mit Thread-Begrenzung.

    ``OMP_THREAD_LIMIT`` wird nur ergänzt, wenn es nicht schon gesetzt ist.
    ``os.environ`` selbst bleibt unverändert.
    """
    env = dict(os.environ)
    if not env.get("OMP_THREAD_LIMIT", "").strip():
        env["OMP_THREAD_LIMIT"] = DEFAULT_OMP_THREAD_LIMIT
    return env


def looks_sideways(words: list[Word]) -> bool:
    """Stehen die Wörter überwiegend hochkant (Seite um 90° oder 270° gedreht)?

    Betrachtet werden nur Wörter ab 4 Zeichen: Sie sind bei normaler Lage
    deutlich breiter als hoch.
    """
    long_words = [w for w in words if len(w.text.strip()) >= 4 and w.bbox.width > 0 and w.bbox.height > 0]
    if len(long_words) < SIDEWAYS_MIN_WORDS:
        return False
    tall = sum(1 for w in long_words if w.bbox.height > 1.5 * w.bbox.width)
    return tall / len(long_words) >= SIDEWAYS_MIN_SHARE


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
    denoised: bool = False

    @property
    def score(self) -> float:
        if self.mean_confidence is None:
            return 0.0
        chars = text_statistics(self.text).meaningful_chars
        return self.mean_confidence * min(1.0, chars / float(WEAK_TEXT_CHARS))

    @property
    def weak(self) -> bool:
        """Keine Wörter, sehr wenig Text oder niedrige mittlere Konfidenz."""
        if self.mean_confidence is None or self.mean_confidence < OSD_RETRY_CONFIDENCE:
            return True
        return text_statistics(self.text).meaningful_chars < WEAK_TEXT_CHARS


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
                env=subprocess_env(),
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
        self,
        command: str,
        gray: Image.Image,
        dpi: float,
        rotation: int,
        page_width: float,
        page_height: float,
    ) -> _Attempt:
        """Ein Erkennungslauf; ``rotation`` = Aufrichtung im Uhrzeigersinn vor dem Lauf.

        Die Wortboxen liegen danach wieder im Koordinatensystem der übergebenen
        (nicht aufgerichteten) Seite.
        """
        upright = gray.rotate(-rotation, expand=True) if rotation else gray
        upscaled, factor = upscale_if_small(upright, dpi)
        straight, angle = deskew(upscaled)
        center_x, center_y = straight.width / 2.0, straight.height / 2.0
        scale_x = page_width / gray.width
        scale_y = page_height / gray.height

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
            x0, y0, x1, y1 = rotate_box_back(
                min(xs), min(ys), max(xs), max(ys), rotation, gray.width, gray.height
            )
            key = (pw.block, pw.paragraph, pw.line)
            if key not in line_index:
                line_index[key] = lines_per_block.get(pw.block, 0)
                lines_per_block[pw.block] = line_index[key] + 1
            words.append(
                Word(
                    text=pw.text,
                    bbox=scale_bbox(x0, y0, x1, y1, scale_x, scale_y, page_width, page_height),
                    confidence=round(pw.confidence, 4),
                    block_no=max(pw.block - 1, 0),
                    line_no=line_index[key],
                )
            )
        return _Attempt(
            rotation=rotation,
            width=page_width,
            height=page_height,
            words=words,
            text=words_to_text(words),
            mean_confidence=weighted_mean_confidence(words),
        )

    def _oriented(
        self, command: str, gray: Image.Image, dpi: float, page_width: float, page_height: float
    ) -> _Attempt:
        """Erkennung mit Prüfung der Seitenlage (siehe Moduldokumentation, Schritt 3)."""
        first = self._attempt(command, gray, dpi, 0, page_width, page_height)
        sideways = looks_sideways(first.words)
        if not (first.weak or sideways):
            return first

        def run(rotation: int) -> _Attempt:
            return self._attempt(command, gray, dpi, rotation, page_width, page_height)

        if "osd" in self._probe().languages:
            upscaled, _ = upscale_if_small(gray, dpi)
            rotation = self._osd_rotation(command, upscaled)
            if not rotation:
                return first
            retry = run(rotation)
            # Hochkant gelesener Text hat oft selbst eine hohe Konfidenz: Bestätigt OSD die
            # Querlage, genügt ein etwa gleich gutes Ergebnis der aufgerichteten Seite.
            confirmed = sideways and rotation in (90, 270)
            if retry.score > first.score or (confirmed and retry.score >= 0.9 * first.score):
                return retry
            return first

        # Ohne OSD: Drehungen ausprobieren. Nur eine klar beste Drehung gilt.
        candidates = [90, 270] if sideways else [180, 90, 270]
        tries = sorted((run(r) for r in candidates), key=lambda a: a.score, reverse=True)
        top, second = tries[0], tries[1]
        if top.score - second.score < ROTATION_CLEAR_MARGIN:
            return first
        if sideways:
            return top if top.score >= 0.9 * first.score else first
        return top if top.score - first.score >= ROTATION_CLEAR_MARGIN else first

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
        best = self._oriented(probe.command, gray, dpi, page_width, page_height)

        if best.weak:
            # Genau ein weiterer Versuch mit Rauschfilter; das bessere Ergebnis bleibt.
            retry = self._oriented(probe.command, denoise_median(gray), dpi, page_width, page_height)
            if retry.score > best.score:
                retry.denoised = True
                best = retry

        quality, warnings = assess_text_quality(
            best.text, best.words, TextLayerOrigin.OCR, best.mean_confidence, self.settings
        )
        if best.denoised:
            warnings = [DENOISED_WARNING, *warnings]
        if best.rotation:
            warnings = [ROTATED_WARNING.format(rotation=best.rotation), *warnings]
        log.info(
            "OCR Seite %d: woerter=%d konfidenz=%s drehung=%d rauschfilter=%s dauer=%.2fs",
            page_number,
            len(best.words),
            f"{best.mean_confidence:.2f}" if best.mean_confidence is not None else "-",
            best.rotation,
            "ja" if best.denoised else "nein",
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
            # Koordinaten liegen im System der übergebenen Seite -> keine zusätzliche Drehung
            rotation=0,
        )
