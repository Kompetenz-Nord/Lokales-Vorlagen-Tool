#!/usr/bin/env python3
"""Extraktions-Benchmark auf synthetischen Testdokumenten („MUSTER – KEINE ECHTDATEN“).

Misst je Datei, wie viele Felder die App richtig, falsch oder gar nicht erkennt –
verglichen mit der Musterlösung des Generators ``insurance_app.samples.variants``.

Ablauf je Datei (wie in der App):
    Dokumentcheck -> Texterkennung (Textebene + Tesseract) -> Dokumenttyp -> Extraktion -> Zusammenführung

Verfahren:
    ocr_only  PDF-Textebene + Tesseract, regelbasierte Extraktion (immer)
    ocr_vlm   zusätzlich lokale Vision-KI über Ollama (nur mit --with-vlm und nur, wenn
              ein Ollama-Dienst mit dem Modell auf diesem Rechner läuft)

Aufruf:
    python3 scripts/benchmark_extraction.py                      # Satz im Temp-Ordner erzeugen und messen
    python3 scripts/benchmark_extraction.py --set-dir tests/fixtures/benchmark
    python3 scripts/benchmark_extraction.py --json ergebnis.json --markdown ergebnis.md
    python3 scripts/benchmark_extraction.py --only digital_nachtrag,rauschen_hoch --details
    python3 scripts/benchmark_extraction.py --with-vlm           # Vergleich mit lokaler KI

Kein Netzwerk außer Loopback (127.0.0.1/::1): Der Netzwerk-Wächter der App ist aktiv.
Die Ergebnisse gelten für synthetische Daten, nicht für echte Dokumente.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory  # noqa: E402
from insurance_app.core.models import (  # noqa: E402
    ClassificationResult,
    DocumentCheckResult,
    DocumentText,
    DocumentType,
    FieldStatus,
    FieldValue,
    TextLayerOrigin,
)
from insurance_app.core.settings import Settings  # noqa: E402
from insurance_app.documents.classify import classify_document  # noqa: E402
from insurance_app.documents.intake import check_document  # noqa: E402
from insurance_app.extraction.extractor import ExtractionResult, SourceDocInfo, extract_candidates  # noqa: E402
from insurance_app.extraction.merge import MergeOutcome, merge_candidates  # noqa: E402
from insurance_app.local_ai.benchmark import compare_methods, values_equal  # noqa: E402
from insurance_app.ocr.service import TextRecognitionService  # noqa: E402
from insurance_app.samples.variants import (  # noqa: E402
    CATEGORY_LABELS,
    MANIFEST_NAME,
    BenchmarkItem,
    generate_benchmark_set,
    load_benchmark_set,
)

__all__ = [
    "METHOD_OCR",
    "METHOD_VLM",
    "VLM_SKIPPED",
    "Aggregate",
    "DocResult",
    "FieldMetrics",
    "PeakMemorySampler",
    "PipelineRun",
    "VlmRunner",
    "aggregate",
    "delivered_values",
    "evaluate_item",
    "main",
    "render_markdown",
    "run_benchmark",
    "run_pipeline",
    "score_fields",
]

METHOD_OCR = "ocr_only"
METHOD_VLM = "ocr_vlm"
VLM_SKIPPED = "übersprungen: lokale KI nicht verfügbar"

#: Status, bei denen die App dem Menschen EINEN Wert vorschlägt
_VALUE_STATUSES = (FieldStatus.EXTRACTED, FieldStatus.UNCLEAR, FieldStatus.CONFIRMED, FieldStatus.MANUAL)

METHOD_LABELS = {
    METHOD_OCR: "nur OCR (Textebene + Tesseract)",
    METHOD_VLM: "OCR + lokale KI (Ollama)",
}


# ---------------------------------------------------------------------------
# Pipeline (wie AppService: Dokumentcheck -> Text -> Typ -> Extraktion -> Zusammenführung)
# ---------------------------------------------------------------------------


@dataclass
class PipelineRun:
    check: DocumentCheckResult
    text: DocumentText
    classification: ClassificationResult
    info: SourceDocInfo
    extraction: ExtractionResult
    outcome: MergeOutcome

    @property
    def ocr_pages(self) -> int:
        return sum(1 for p in self.text.pages if p.origin == TextLayerOrigin.OCR)

    @property
    def engines(self) -> list[str]:
        return sorted({p.engine_version for p in self.text.pages if p.engine_version})


class PipelineError(RuntimeError):
    """Die Datei konnte nicht verarbeitet werden (Meldung ohne Inhalte)."""


def run_pipeline(path: Path, ocr: TextRecognitionService, document_id: str = "BENCH-1") -> PipelineRun:
    """Eine Datei wie in der App verarbeiten (ohne Vorgang/Datenbank)."""
    check = check_document(Path(path))
    if not check.ok:
        codes = ", ".join(sorted({i.code for i in check.issues}))
        raise PipelineError(f"Dokumentcheck nicht bestanden ({codes})")
    text = ocr.recognize_document(Path(path), document_id, check)
    cls = classify_document(text)
    info = SourceDocInfo(
        document_id=document_id,
        file_name=Path(path).name,
        doc_type=cls.doc_type,
        document_date=cls.document_date,
        effective_date=cls.effective_date,
    )
    extraction = extract_candidates(text, info)
    outcome = merge_candidates([(info, extraction)])
    return PipelineRun(check, text, cls, info, extraction, outcome)


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------


def delivered_values(fields: dict[str, FieldValue]) -> dict[str, str | None]:
    """Wert, den die App je Feld vorschlägt (kanonisch, sonst Rohwert).

    ``CONFLICT`` und ``MISSING`` liefern keinen einzelnen Wert (-> ``None``).
    """
    out: dict[str, str | None] = {}
    for key, fv in fields.items():
        if fv.status in _VALUE_STATUSES:
            out[key] = fv.normalized_value if fv.normalized_value is not None else fv.value
        else:
            out[key] = None
    return out


@dataclass
class FieldMetrics:
    """Zählwerte eines Dokuments für ein Verfahren (nur Felder der Musterlösung, außer ``unexpected``)."""

    total: int = 0
    correct: int = 0
    wrong: int = 0
    missing: int = 0  # kein einzelner Wert (MISSING oder CONFLICT)
    unexpected: int = 0  # Werte für Felder ohne Sollwert (Hinweis, nicht Teil von total)
    high_conf_wrong: int = 0  # Status EXTRACTED, aber falscher Wert (gefährlichster Fehler)
    flagged_wrong: int = 0  # falscher Wert, aber als UNCLEAR markiert
    unclear: int = 0
    conflict: int = 0
    conflict_with_truth: int = 0  # Konflikt, bei dem der richtige Wert zur Auswahl steht
    details: dict[str, dict[str, Any]] = field(default_factory=dict)


def _conflict_has_truth(key: str, fv: FieldValue, expected: str) -> bool:
    for cand in fv.candidates:
        value = cand.normalized if cand.normalized is not None else cand.value
        if value and values_equal(key, expected, value):
            return True
    return False


def score_fields(
    ground_truth: dict[str, str],
    fields: dict[str, FieldValue],
    method: str = METHOD_OCR,
    *,
    include_values: bool = False,
) -> FieldMetrics:
    """Feldwerte gegen die Musterlösung bewerten (über ``local_ai.benchmark.compare_methods``)."""
    delivered = delivered_values(fields)
    scores = compare_methods(ground_truth, {method: delivered})[method]
    m = FieldMetrics(
        total=scores["total"],
        correct=scores["correct"],
        wrong=scores["wrong"],
        missing=scores["missing"],
        unexpected=scores["unexpected"],
    )
    for key, outcome in scores["details"].items():
        fv = fields.get(key) or FieldValue(key=key, status=FieldStatus.MISSING)
        if fv.status == FieldStatus.UNCLEAR:
            m.unclear += 1
        if fv.status == FieldStatus.CONFLICT:
            m.conflict += 1
            if _conflict_has_truth(key, fv, ground_truth[key]):
                m.conflict_with_truth += 1
        if outcome == "wrong":
            if fv.status == FieldStatus.EXTRACTED:
                m.high_conf_wrong += 1
            elif fv.status == FieldStatus.UNCLEAR:
                m.flagged_wrong += 1
        entry: dict[str, Any] = {"outcome": outcome, "status": fv.status.value}
        if include_values:  # nur für synthetische Daten gedacht
            entry["expected"] = ground_truth[key]
            entry["actual"] = delivered.get(key)
            entry["confidence"] = fv.confidence
        m.details[key] = entry
    return m


@dataclass
class DocResult:
    item_id: str
    category: str
    categories: list[str]
    layout: str
    file_name: str
    method: str
    pages: int = 0
    ocr_pages: int = 0
    doc_type_expected: str = ""
    doc_type_detected: str = ""
    total: int = 0
    correct: int = 0
    wrong: int = 0
    missing: int = 0
    unexpected: int = 0
    high_conf_wrong: int = 0
    flagged_wrong: int = 0
    unclear: int = 0
    conflict: int = 0
    conflict_with_truth: int = 0
    runtime_s: float = 0.0
    peak_rss_mb: float | None = None
    engines: list[str] = field(default_factory=list)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def doc_type_ok(self) -> bool:
        return bool(self.doc_type_expected) and self.doc_type_expected == self.doc_type_detected

    def apply(self, m: FieldMetrics) -> None:
        for name in (
            "total",
            "correct",
            "wrong",
            "missing",
            "unexpected",
            "high_conf_wrong",
            "flagged_wrong",
            "unclear",
            "conflict",
            "conflict_with_truth",
        ):
            setattr(self, name, getattr(m, name))
        self.fields = m.details

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["doc_type_ok"] = self.doc_type_ok
        return data


# ---------------------------------------------------------------------------
# Speicher (Spitzenwert inkl. Tesseract-Unterprozesse)
# ---------------------------------------------------------------------------


class PeakMemorySampler:
    """Misst den höchsten Arbeitsspeicher (RSS) während eines Blocks.

    Mit ``psutil``: Hauptprozess + Unterprozesse (Tesseract), Abtastung alle ``interval`` s.
    Kurze Spitzen zwischen zwei Abtastungen können fehlen (Näherung).
    Ohne ``psutil``: Höchstwert seit Prozessstart über ``resource`` (keine Einzelmessung je Datei).
    """

    def __init__(self, interval: float = 0.02, enabled: bool = True) -> None:
        self.interval = interval
        self.enabled = enabled
        self.peak_bytes: int | None = None
        self.mode = "aus"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: Any = None
        if not enabled:
            return
        try:
            import psutil

            self._proc = psutil.Process(os.getpid())
            self.mode = "psutil"
        except Exception:
            self._proc = None
            self.mode = "resource" if _resource_peak() is not None else "aus"

    def _sample(self) -> int:
        rss = self._proc.memory_info().rss
        with contextlib.suppress(Exception):
            for child in self._proc.children(recursive=True):
                with contextlib.suppress(Exception):
                    rss += child.memory_info().rss
        return int(rss)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                value = self._sample()
                self.peak_bytes = max(self.peak_bytes or 0, value)
            self._stop.wait(self.interval)

    def __enter__(self) -> PeakMemorySampler:
        if self.mode == "psutil":
            with contextlib.suppress(Exception):
                self.peak_bytes = self._sample()
            self._thread = threading.Thread(target=self._loop, name="bench-mem", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=2.0)
        elif self.mode == "resource":
            self.peak_bytes = _resource_peak()

    @property
    def peak_mb(self) -> float | None:
        return None if self.peak_bytes is None else round(self.peak_bytes / (1024 * 1024), 1)


def _resource_peak() -> int | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    scale = 1 if sys.platform == "darwin" else 1024  # macOS: Bytes, Linux: KiB
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int((own + children) * scale)


# ---------------------------------------------------------------------------
# Lokale KI (optional, nur Loopback)
# ---------------------------------------------------------------------------


class VlmRunner:
    """Zweitprüfung mit dem lokalen Vision-Modell – wie ``AppService.run_local_ai``."""

    def __init__(self, url: str, model: str, timeout: float = 120.0, max_tokens: int = 1024) -> None:
        self.url = url
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._extractor: Any = None

    def availability(self) -> tuple[bool, str]:
        """(bereit?, Grund) – wirft nie."""
        from insurance_app.local_ai.ollama_client import OllamaClient, OllamaError
        from insurance_app.security.network_guard import NetworkBlockedError

        try:
            client = OllamaClient(self.url, timeout=3.0)
        except (ValueError, NetworkBlockedError):
            return False, "Die Ollama-Adresse zeigt nicht auf diesen Rechner (nur 127.0.0.1/localhost)."
        if not client.is_running():
            return False, "Kein Ollama-Dienst auf diesem Rechner erreichbar."
        try:
            present = client.has_model(self.model)
        except OllamaError:
            return False, "Die Modellliste von Ollama ist nicht lesbar."
        if not present:
            return False, f"Das Modell „{self.model}“ ist lokal nicht installiert."
        return True, ""

    def _get_extractor(self) -> Any:
        if self._extractor is None:
            from insurance_app.extraction.normalize import normalize_value
            from insurance_app.local_ai.ollama_client import OllamaClient
            from insurance_app.local_ai.vlm_extractor import VlmExtractor

            self._extractor = VlmExtractor(
                OllamaClient(self.url, timeout=10.0),
                self.model,
                timeout=self.timeout,
                max_tokens=self.max_tokens,
                normalizer=lambda key, raw: normalize_value(FIELD_REGISTRY[key], raw)[0],
            )
        return self._extractor

    def run(self, path: Path, run: PipelineRun) -> tuple[dict[str, FieldValue], list[str]]:
        """Offene Felder (fehlend/unklar/Konflikt) mit der KI prüfen und abgleichen."""
        from insurance_app.documents.pages import load_page_image
        from insurance_app.local_ai.reconcile import reconcile
        from insurance_app.local_ai.vlm_extractor import check_field_keys

        fields = run.outcome.fields
        targets = [
            k
            for k, fdef in FIELD_REGISTRY.items()
            if fdef.category == FieldCategory.CONTRACT
            and fdef.extractable
            and not fdef.legal_relevant
            and (
                fields.get(k) is None
                or fields[k].status in (FieldStatus.MISSING, FieldStatus.UNCLEAR, FieldStatus.CONFLICT)
            )
        ]
        targets = check_field_keys(targets) if targets else []
        if not targets:
            return dict(fields), []
        weak = [p for p in run.text.pages if p.quality.value != "GOOD"] or run.text.pages[:2]
        pages = [(p.page_number, load_page_image(path, p.page_number, dpi=150)) for p in weak[:3]]
        texts = {p.page_number: p.text for p in weak[:3]}
        res = self._get_extractor().extract(pages, run.info.document_id, path.name, targets, page_texts=texts)
        new_fields, _history = reconcile(fields, res.candidates)
        return new_fields, list(res.errors)


# ---------------------------------------------------------------------------
# Durchlauf
# ---------------------------------------------------------------------------


def _base_result(item: BenchmarkItem, method: str) -> DocResult:
    return DocResult(
        item_id=item.id,
        category=item.category,
        categories=list(item.categories),
        layout=str(item.meta.get("layout", "")),
        file_name=item.file.name,
        method=method,
        pages=int(item.meta.get("page_count", 0) or 0),
        doc_type_expected=str(item.meta.get("doc_type", "")),
        total=len(item.ground_truth),
        missing=len(item.ground_truth),
    )


def evaluate_item(
    item: BenchmarkItem,
    ocr: TextRecognitionService,
    *,
    vlm: VlmRunner | None = None,
    measure_memory: bool = True,
    include_values: bool = False,
) -> list[DocResult]:
    """Eine Benchmark-Datei messen -> Ergebnis je Verfahren (``ocr_only`` und ggf. ``ocr_vlm``)."""
    ocr_res = _base_result(item, METHOD_OCR)
    sampler = PeakMemorySampler(enabled=measure_memory)
    started = time.perf_counter()
    run: PipelineRun | None = None
    try:
        with sampler:
            run = run_pipeline(item.file, ocr, document_id=f"BENCH-{item.id}")
    except Exception as exc:  # eine defekte Datei darf den Benchmark nicht abbrechen
        ocr_res.error = f"{type(exc).__name__}: {exc}"[:300]
    ocr_res.runtime_s = round(time.perf_counter() - started, 3)
    ocr_res.peak_rss_mb = sampler.peak_mb
    if run is None:
        return [ocr_res]
    ocr_res.ocr_pages = run.ocr_pages
    ocr_res.pages = len(run.text.pages)
    ocr_res.engines = run.engines
    ocr_res.doc_type_detected = run.classification.doc_type.value
    ocr_res.apply(score_fields(item.ground_truth, run.outcome.fields, METHOD_OCR, include_values=include_values))
    results = [ocr_res]

    if vlm is not None:
        vlm_res = _base_result(item, METHOD_VLM)
        vlm_res.pages, vlm_res.ocr_pages, vlm_res.engines = ocr_res.pages, ocr_res.ocr_pages, ocr_res.engines
        vlm_res.doc_type_detected = ocr_res.doc_type_detected
        vlm_sampler = PeakMemorySampler(enabled=measure_memory)
        vlm_started = time.perf_counter()
        try:
            with vlm_sampler:
                fields, errors = vlm.run(item.file, run)
            vlm_res.notes.extend(errors)
            vlm_res.apply(score_fields(item.ground_truth, fields, METHOD_VLM, include_values=include_values))
        except Exception as exc:
            vlm_res.error = f"{type(exc).__name__}: {exc}"[:300]
        vlm_res.runtime_s = round(ocr_res.runtime_s + (time.perf_counter() - vlm_started), 3)
        peaks = [p for p in (ocr_res.peak_rss_mb, vlm_sampler.peak_mb) if p is not None]
        vlm_res.peak_rss_mb = max(peaks) if peaks else None
        results.append(vlm_res)
    return results


@dataclass
class Aggregate:
    files: int = 0
    pages: int = 0
    ocr_pages: int = 0
    total: int = 0
    correct: int = 0
    wrong: int = 0
    missing: int = 0
    unexpected: int = 0
    high_conf_wrong: int = 0
    flagged_wrong: int = 0
    unclear: int = 0
    conflict: int = 0
    conflict_with_truth: int = 0
    doc_type_ok: int = 0
    errors: int = 0
    runtime_total_s: float = 0.0
    runtime_max_s: float = 0.0
    peak_rss_mb: float | None = None

    def add(self, r: DocResult) -> None:
        self.files += 1
        self.pages += r.pages
        self.ocr_pages += r.ocr_pages
        for name in (
            "total",
            "correct",
            "wrong",
            "missing",
            "unexpected",
            "high_conf_wrong",
            "flagged_wrong",
            "unclear",
            "conflict",
            "conflict_with_truth",
        ):
            setattr(self, name, getattr(self, name) + getattr(r, name))
        self.doc_type_ok += int(r.doc_type_ok)
        self.errors += int(r.error is not None)
        self.runtime_total_s = round(self.runtime_total_s + r.runtime_s, 3)
        self.runtime_max_s = max(self.runtime_max_s, r.runtime_s)
        if r.peak_rss_mb is not None:
            self.peak_rss_mb = max(self.peak_rss_mb or 0.0, r.peak_rss_mb)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return self.wrong / self.total if self.total else 0.0

    @property
    def missing_rate(self) -> float:
        return self.missing / self.total if self.total else 0.0

    @property
    def runtime_mean_s(self) -> float:
        return self.runtime_total_s / self.files if self.files else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            accuracy=round(self.accuracy, 4),
            error_rate=round(self.error_rate, 4),
            missing_rate=round(self.missing_rate, 4),
            runtime_mean_s=round(self.runtime_mean_s, 3),
        )
        return data


def aggregate(results: Sequence[DocResult], method: str) -> tuple[dict[str, Aggregate], Aggregate]:
    """Summen je Kategorie (Dateien mit ``also_in`` zählen in mehreren) und gesamt (jede Datei einmal)."""
    per_cat: dict[str, Aggregate] = {}
    overall = Aggregate()
    for r in results:
        if r.method != method:
            continue
        overall.add(r)
        for cat in dict.fromkeys(r.categories):
            per_cat.setdefault(cat, Aggregate()).add(r)
    order = {c: i for i, c in enumerate(CATEGORY_LABELS)}
    ordered = dict(sorted(per_cat.items(), key=lambda kv: (order.get(kv[0], 99), kv[0])))
    return ordered, overall


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %".replace(".", ",")


def _num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "–"
    return f"{value:.{digits}f}".replace(".", ",")


_AGG_HEADER = (
    "| {first} | Dateien | Seiten (OCR) | Felder | Richtig | Falsch | Fehlend | Sicher falsch | "
    "Unklar | Konflikt | Ø Zeit/Datei | Max. RAM |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
)


def _agg_row(name: str, a: Aggregate) -> str:
    return (
        f"| {name} | {a.files} | {a.pages} ({a.ocr_pages}) | {a.total} | {_pct(a.accuracy)} | "
        f"{_pct(a.error_rate)} | {_pct(a.missing_rate)} | {a.high_conf_wrong} | {a.unclear} | "
        f"{a.conflict} | {_num(a.runtime_mean_s, 2)} s | {_num(a.peak_rss_mb, 0)} MB |"
    )


def render_markdown(report: dict[str, Any], *, include_values: bool = False) -> str:
    """Bericht als Markdown (Tabellen je Kategorie und je Datei)."""
    docs = [DocResult(**{k: v for k, v in d.items() if k != "doc_type_ok"}) for d in report["documents"]]
    lines: list[str] = ["# Extraktions-Benchmark (synthetische Daten, MUSTER)", ""]
    env = report.get("environment", {})
    lines.append(
        f"Testsatz: {report['set']['files']} Dateien, {report['set']['pages']} Seiten, "
        f"{report['set']['fields']} Sollwerte · Umgebung: {env.get('cpu_count', '?')} CPU, "
        f"{env.get('platform', '?')}, OCR: {', '.join(env.get('ocr_engines') or ['–'])}"
    )
    lines.append("")
    for method in report["methods"]:
        per_cat, overall = aggregate(docs, method)
        lines.append(f"## Verfahren: {METHOD_LABELS.get(method, method)}")
        lines.append("")
        lines.append(_AGG_HEADER.format(first="Kategorie"))
        for cat, agg in per_cat.items():
            lines.append(_agg_row(cat, agg))
        lines.append(_agg_row("**Gesamt**", overall))
        lines.append("")
        lines.append(
            "| Datei-ID | Kategorie | Typ erkannt | Felder | Richtig | Falsch | Fehlend | Sicher falsch | "
            "Unklar | Konflikt | Zeit | RAM |"
        )
        lines.append("|---|---|:-:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in docs:
            if r.method != method:
                continue
            if r.error:
                lines.append(f"| {r.item_id} | {r.category} | – | {r.total} | Fehler: {r.error} ||||||||")
                continue
            lines.append(
                f"| {r.item_id} | {r.category} | {'ja' if r.doc_type_ok else 'nein'} | {r.total} | "
                f"{r.correct} | {r.wrong} | {r.missing} | {r.high_conf_wrong} | {r.unclear} | {r.conflict} | "
                f"{_num(r.runtime_s, 2)} s | {_num(r.peak_rss_mb, 0)} MB |"
            )
        lines.append("")
        if include_values:
            lines.append("### Abweichungen je Feld")
            lines.append("")
            lines.append("| Datei-ID | Feld | Ergebnis | Status | Soll | Ist |")
            lines.append("|---|---|---|---|---|---|")
            for r in docs:
                if r.method != method:
                    continue
                for key, d in r.fields.items():
                    if d.get("outcome") != "correct":
                        lines.append(
                            f"| {r.item_id} | {key} | {d.get('outcome')} | {d.get('status')} | "
                            f"{d.get('expected', '')} | {d.get('actual') if d.get('actual') is not None else '–'} |"
                        )
            lines.append("")
    vlm = report.get("vlm", {})
    lines.append("## Lokale KI")
    lines.append("")
    if vlm.get("status") == "measured":
        lines.append(f"Gemessen mit Modell „{vlm.get('model')}“ über {vlm.get('url')}.")
    elif vlm.get("status") == "skipped":
        lines.append(f"{VLM_SKIPPED} – {vlm.get('reason', '')}")
    else:
        lines.append("Nicht angefordert (Aufruf mit --with-vlm).")
    lines.append("")
    lines.append(
        "Legende: *Fehlend* = kein einzelner Wert vorgeschlagen (Status MISSING oder CONFLICT). "
        "*Sicher falsch* = falscher Wert mit Status „Erkannt“ (EXTRACTED), also ohne Unsicherheits-Hinweis. "
        "Zeit und RAM je Datei inkl. Tesseract. Ergebnisse gelten nur für diesen synthetischen Testsatz."
    )
    return "\n".join(lines) + "\n"


def _environment(settings: Settings, engines: Sequence[str], sampler_mode: str) -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "cpu_count": os.cpu_count(),
        "ocr_backend": settings.ocr_backend,
        "ocr_languages": settings.ocr_languages,
        "ocr_dpi": settings.ocr_dpi,
        "ocr_engines": sorted(set(engines)),
        "memory_measurement": sampler_mode,
    }
    with contextlib.suppress(Exception):
        import psutil

        env["ram_total_gb"] = round(psutil.virtual_memory().total / 1024**3, 1)
    return env


def run_benchmark(
    items: Sequence[BenchmarkItem],
    *,
    settings: Settings | None = None,
    with_vlm: bool = False,
    ollama_url: str | None = None,
    model: str | None = None,
    measure_memory: bool = True,
    include_values: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Alle Dateien messen und einen JSON-fähigen Bericht liefern."""
    settings = settings or Settings()
    say = progress or (lambda _msg: None)
    ocr = TextRecognitionService(settings)
    vlm_info: dict[str, Any] = {"status": "not_requested"}
    vlm: VlmRunner | None = None
    if with_vlm:
        url = ollama_url or settings.ollama_url
        name = model or settings.ai_model
        runner = VlmRunner(url, name, timeout=float(settings.ai_timeout_s), max_tokens=settings.ai_max_tokens)
        ok, reason = runner.availability()
        vlm_info = {"status": "measured" if ok else "skipped", "url": url, "model": name}
        if ok:
            vlm = runner
        else:
            vlm_info["reason"] = reason
            say(f"{VLM_SKIPPED} ({reason})")

    documents: list[DocResult] = []
    started = time.perf_counter()
    for idx, item in enumerate(items, start=1):
        results = evaluate_item(
            item, ocr, vlm=vlm, measure_memory=measure_memory, include_values=include_values
        )
        documents.extend(results)
        r = results[0]
        status = f"Fehler ({r.error})" if r.error else f"{r.correct}/{r.total} richtig, {r.high_conf_wrong} sicher falsch"
        say(f"[{idx}/{len(items)}] {item.id}: {status}, {r.runtime_s:.2f} s")
    methods = [METHOD_OCR] + ([METHOD_VLM] if vlm is not None else [])
    engines = [e for d in documents for e in d.engines]
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "synthetic": True,
        "environment": _environment(settings, engines, PeakMemorySampler(enabled=measure_memory).mode),
        "set": {
            "files": len(items),
            "pages": sum(int(i.meta.get("page_count", 0) or 0) for i in items),
            "fields": sum(len(i.ground_truth) for i in items),
            "size_bytes": sum(i.file.stat().st_size for i in items if i.file.exists()),
            "seed": items[0].meta.get("base_seed") if items else None,
        },
        "methods": methods,
        "vlm": vlm_info,
        "wall_time_s": round(time.perf_counter() - started, 2),
        "documents": [d.to_dict() for d in documents],
        "categories": {},
        "overall": {},
    }
    for method in methods:
        per_cat, overall = aggregate(documents, method)
        report["categories"][method] = {c: a.to_dict() for c, a in per_cat.items()}
        report["overall"][method] = overall.to_dict()
    return report


# ---------------------------------------------------------------------------
# Kommandozeile
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _local_only() -> Iterator[None]:
    """Netzwerk-Wächter der App aktivieren (nur Loopback) und danach wiederherstellen."""
    from insurance_app.security.network_guard import (
        install_network_guard,
        is_installed,
        uninstall_network_guard,
    )

    installed_here = not is_installed()
    if installed_here:
        install_network_guard()
    try:
        yield
    finally:
        if installed_here:
            uninstall_network_guard()


def _load_items(args: argparse.Namespace, tmp_root: Path) -> list[BenchmarkItem]:
    if args.set_dir is not None:
        set_dir = Path(args.set_dir)
        if args.regenerate or not (set_dir / MANIFEST_NAME).is_file():
            print(f"Erzeuge Testsatz in {set_dir} …", file=sys.stderr)
            items = generate_benchmark_set(set_dir, seed=args.seed)
        else:
            items = load_benchmark_set(set_dir)
    else:
        print("Erzeuge Testsatz im temporären Ordner …", file=sys.stderr)
        items = generate_benchmark_set(tmp_root, seed=args.seed, only=args.only or None)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {i.id for i in items}
        if unknown:
            raise ValueError(f"Unbekannte Varianten: {', '.join(sorted(unknown))}")
        items = [i for i in items if i.id in wanted]
    if args.category:
        items = [i for i in items if args.category in i.categories]
    return items


def _split_ids(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Misst die Feld-Erkennung auf synthetischen Testdokumenten (MUSTER). "
            "Kein Netzwerk außer Loopback."
        )
    )
    p.add_argument("--set-dir", type=Path, help="Ordner mit Testsatz (wird erzeugt, falls leer).")
    p.add_argument("--regenerate", action="store_true", help="Testsatz in --set-dir neu erzeugen.")
    p.add_argument("--generate-only", action="store_true", help="Nur Testsatz erzeugen, nicht messen.")
    p.add_argument("--seed", type=int, default=0, help="Zufalls-Seed für die Erzeugung (Standard 0).")
    p.add_argument("--only", type=_split_ids, default=None, help="Nur diese Varianten-IDs (Komma-Liste).")
    p.add_argument("--category", help="Nur Dateien dieser Kategorie (z. B. rauschen).")
    p.add_argument("--json", type=Path, dest="json_path", help="Bericht zusätzlich als JSON speichern.")
    p.add_argument("--markdown", type=Path, help="Bericht zusätzlich als Markdown-Datei speichern.")
    p.add_argument("--details", action="store_true", help="Soll-/Ist-Werte je Feld ausgeben (synthetisch).")
    p.add_argument("--no-memory", action="store_true", help="Arbeitsspeicher nicht messen.")
    p.add_argument("--with-vlm", action="store_true", help="Zusätzlich lokale KI (Ollama, nur Loopback).")
    p.add_argument("--ollama-url", help="Adresse des lokalen Ollama-Dienstes (Standard aus Einstellungen).")
    p.add_argument("--model", help="Modellname für die lokale KI (Standard aus Einstellungen).")
    p.add_argument("--quiet", action="store_true", help="Keine Fortschrittsmeldungen.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    say: Callable[[str], None] = (lambda _m: None) if args.quiet else (lambda m: print(m, file=sys.stderr))
    with _local_only(), tempfile.TemporaryDirectory(prefix="bench_set_") as tmp:
        try:
            items = _load_items(args, Path(tmp))
        except (OSError, ValueError) as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return 1
        if args.generate_only:
            print(f"{len(items)} Dateien erzeugt.")
            return 0
        if not items:
            print("Fehler: keine Dateien ausgewählt.", file=sys.stderr)
            return 1
        report = run_benchmark(
            items,
            with_vlm=args.with_vlm,
            ollama_url=args.ollama_url,
            model=args.model,
            measure_memory=not args.no_memory,
            include_values=args.details,
            progress=say,
        )
    if args.with_vlm and report["vlm"]["status"] == "skipped":
        print(f"{VLM_SKIPPED} ({report['vlm'].get('reason', '')})")
    markdown = render_markdown(report, include_values=args.details)
    print(markdown)
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
