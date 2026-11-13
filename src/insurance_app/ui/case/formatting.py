"""Anzeigeformate für die Vorgangsansicht (ohne Qt, gut testbar).

Alle Texte sind deutsch und in einfacher Sprache. Werte werden nur für die
Anzeige formatiert – gespeichert bleibt immer der kanonische Wert.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from ...core.case import Case, PipelineStage
from ...core.fields import FIELD_REGISTRY, FieldDef, FieldType
from ...core.models import (
    Candidate,
    DocumentType,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    PageQuality,
    Severity,
    TextLayerOrigin,
)
from ..theme import SEVERITY_STYLE

CONFIDENCE_TOOLTIP = (
    "Sicherheit: heuristischer Wert, nicht kalibriert. "
    "Er zeigt nur grob, wie eindeutig die Fundstelle war – bitte den Wert immer selbst prüfen."
)

TOTAL_STAGES = len(PipelineStage)

METHOD_LABELS: dict[ExtractionMethod, str] = {
    ExtractionMethod.PDF_TEXT: "Textebene",
    ExtractionMethod.TESSERACT: "OCR (Tesseract)",
    ExtractionMethod.DOCLING_OCR: "OCR (Docling)",
    ExtractionMethod.RAPID_OCR: "OCR (RapidOCR)",
    ExtractionMethod.PADDLE_OCR: "OCR (PaddleOCR)",
    ExtractionMethod.LOCAL_VLM: "Lokale KI (Bilderkennung)",
    ExtractionMethod.MANUAL: "Manuell eingetragen",
}

METHOD_HELP = (
    "Textebene = Text, der bereits im PDF gespeichert ist.\n"
    "OCR = automatische Texterkennung aus dem Bild einer Seite (Optical Character Recognition).\n"
    "Fremde OCR-Textebene = Scan, in dem ein anderes Programm bereits Text hinterlegt hat; "
    "dieser Text kann Lesefehler enthalten."
)

QUALITY_LABELS: dict[PageQuality, str] = {
    PageQuality.GOOD: "gut lesbar",
    PageQuality.LOW: "unsicher gelesen – bitte genau prüfen",
    PageQuality.EMPTY: "kein Text gefunden",
}

QUALITY_SYMBOLS: dict[PageQuality, str] = {
    PageQuality.GOOD: "✔",
    PageQuality.LOW: "⚠",
    PageQuality.EMPTY: "✖",
}

# Eingabeformat je Datentyp: (Platzhalter im Eingabefeld, Erklärung)
FORMAT_HINTS: dict[FieldType, tuple[str, str]] = {
    FieldType.DATE: ("TT.MM.JJJJ", "Datum im Format TT.MM.JJJJ, z. B. 01.10.2026"),
    FieldType.MONTH: ("MM.JJJJ", "Monat und Jahr im Format MM.JJJJ, z. B. 10.2026"),
    FieldType.AMOUNT: ("1.234,56", "Betrag in Euro mit Komma, z. B. 1.234,56 (ohne €-Zeichen)"),
    FieldType.IBAN: (
        "DE.. .... .... .... .... ..",
        "IBAN (internationale Kontonummer), z. B. DE89 3704 0044 0532 0130 00 – Leerzeichen sind erlaubt",
    ),
    FieldType.POSTAL_CODE: ("12345", "Postleitzahl (in Deutschland 5 Ziffern)"),
    FieldType.NAME: ("Name", "Name wie im Dokument, z. B. Musterfrau"),
    FieldType.TEXT: ("", "Freier Text"),
    FieldType.CONTRACT_NUMBER: ("Vertragsnummer", "Vertrags- oder Versicherungsnummer wie im Dokument"),
    FieldType.INTEGER: ("z. B. 67", "Ganze Zahl ohne Nachkommastellen"),
    FieldType.CURRENCY: ("EUR", "Währungskürzel, z. B. EUR"),
    FieldType.COUNTRY: ("Deutschland", "Land, z. B. Deutschland"),
    FieldType.BOOL: ("", "Ja oder Nein"),
    FieldType.CHOICE: ("", "Bitte eine der angebotenen Möglichkeiten wählen"),
}

ACTION_LABELS: dict[str, str] = {
    "case_created": "Vorgang angelegt",
    "document_added": "Dokument hinzugefügt",
    "document_removed": "Dokument entfernt",
    "document_excluded": "Dokument von der Auswertung ausgeschlossen",
    "document_included": "Dokument wieder einbezogen",
    "document_type_set": "Dokumenttyp korrigiert",
    "text_recognized": "Text erkannt",
    "extracted": "Wert erkannt",
    "conflict": "Widersprüchliche Werte gefunden",
    "cleared": "Wert entfernt (keine Quelle mehr)",
    "sources_added": "Weitere passende Quelle gefunden",
    "confirmed": "Wert bestätigt",
    "edited": "Wert manuell eingetragen",
    "decided": "Auswahl getroffen",
    "candidate_chosen": "Wert aus dem Vergleich übernommen",
    "marked_unclear": "Als unklar markiert",
    "not_applicable": "Als nicht relevant markiert",
    "reset": "Zurückgesetzt",
    "template_selected": "Vorlage gewählt",
    "export": "PDF erzeugt",
    "archived": "Vorgang archiviert",
    "unarchived": "Vorgang wiederhergestellt",
    "ai_extracted": "Wert durch lokale KI gelesen",
    "ai_suggestion_matches": "Lokale KI liest denselben Wert",
    "ai_suggestion_differs": "Lokale KI liest einen anderen Wert (Hinweis)",
    "ai_conflict": "Konflikt durch Zweitprüfung der lokalen KI",
    "ai_suggestion_added": "Vorschlag der lokalen KI ergänzt",
}


# ---------------------------------------------------------------------------
# Zeit, Stufen, Akteure
# ---------------------------------------------------------------------------


def format_local_time(dt: datetime | None) -> str:
    """Zeitpunkt in Ortszeit als TT.MM.JJJJ HH:MM."""
    if dt is None:
        return ""
    try:
        local = dt.astimezone()
    except (ValueError, OSError):
        local = dt
    return local.strftime("%d.%m.%Y %H:%M")


def stage_text(stage: PipelineStage | int) -> str:
    st = PipelineStage(int(stage))
    return f"{int(st)}/{TOTAL_STAGES} {st.label_de}"


def action_text(action: str) -> str:
    return ACTION_LABELS.get(action, action.replace("_", " ").capitalize())


def actor_text(actor: str) -> str:
    return "Nutzer" if actor == "user" else "System"


def severity_symbol(severity: Severity) -> str:
    return SEVERITY_STYLE[severity][0]


def severity_color(severity: Severity) -> str:
    return SEVERITY_STYLE[severity][1]


# ---------------------------------------------------------------------------
# Werte
# ---------------------------------------------------------------------------


def format_amount(norm: str | None) -> str | None:
    """``1234.56`` -> ``1.234,56``."""
    if norm is None:
        return None
    try:
        d = Decimal(norm)
    except (InvalidOperation, ValueError):
        return None
    s = f"{d:,.2f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def format_iban(norm: str) -> str:
    compact = norm.replace(" ", "")
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


def format_normalized(fdef: FieldDef | None, norm: str | None, *, currency: str | None = "EUR") -> str:
    """Kanonischen Wert lesbar darstellen (Datum TT.MM.JJJJ, Betrag 1.234,56 € …)."""
    if norm is None:
        return ""
    if fdef is None:
        return norm
    dtype = fdef.dtype
    if dtype == FieldType.DATE and len(norm) == 10 and norm[4] == "-":
        return f"{norm[8:10]}.{norm[5:7]}.{norm[0:4]}"
    if dtype == FieldType.MONTH and len(norm) == 7 and norm[4] == "-":
        return f"{norm[5:7]}.{norm[0:4]}"
    if dtype == FieldType.AMOUNT:
        amount = format_amount(norm)
        if amount is None:
            return norm
        symbol = "€" if (currency or "EUR") == "EUR" else currency
        return f"{amount} {symbol}"
    if dtype == FieldType.IBAN:
        return format_iban(norm)
    if dtype == FieldType.CHOICE and fdef.choices:
        return fdef.choices.get(norm, norm)
    if dtype == FieldType.BOOL:
        return {"true": "Ja", "false": "Nein"}.get(norm, norm)
    return norm


def currency_of(case: Case | None) -> str:
    if case is None:
        return "EUR"
    fv = case.get("premium.currency")
    if fv is not None and fv.normalized_value:
        return fv.normalized_value
    return "EUR"


def candidate_display(key: str, cand: Candidate, currency: str = "EUR") -> str:
    fdef = FIELD_REGISTRY.get(key)
    if cand.normalized is not None:
        return format_normalized(fdef, cand.normalized, currency=currency)
    return cand.value


def distinct_candidate_values(key: str, fv: FieldValue, currency: str = "EUR") -> list[str]:
    seen: list[str] = []
    for c in fv.candidates:
        text = candidate_display(key, c, currency)
        if text and text not in seen:
            seen.append(text)
    return seen


def display_value(key: str, fv: FieldValue | None, currency: str = "EUR") -> str:
    """Hauptzeile der Wertspalte."""
    if fv is None:
        return "—"
    if fv.status == FieldStatus.NOT_APPLICABLE:
        return "– (bewusst leer)"
    if fv.status == FieldStatus.CONFLICT and fv.normalized_value is None:
        values = distinct_candidate_values(key, fv, currency)
        if values:
            return f"{len(values)} Werte: " + " | ".join(values)
        return "Widersprüchliche Werte"
    fdef = FIELD_REGISTRY.get(key)
    if fv.normalized_value is not None:
        return format_normalized(fdef, fv.normalized_value, currency=currency)
    if fv.value:
        return f"{fv.value} (nicht eindeutig lesbar)"
    return "— (nicht gefunden)"


def edit_text(key: str, fv: FieldValue | None) -> str:
    """Vorbelegung für ein Eingabefeld (Format wie erwartet, ohne €-Zeichen)."""
    if fv is None or fv.status in (FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE):
        return ""
    fdef = FIELD_REGISTRY.get(key)
    norm = fv.normalized_value
    if fdef is not None and norm is not None:
        if fdef.dtype in (FieldType.DATE, FieldType.MONTH, FieldType.IBAN):
            return format_normalized(fdef, norm)
        if fdef.dtype == FieldType.AMOUNT:
            return format_amount(norm) or norm
        if fdef.dtype == FieldType.CHOICE and fdef.choices:
            return fdef.choices.get(norm, norm)
        return norm
    return fv.value or ""


def confidence_text(confidence: float | None) -> str:
    if confidence is None:
        return ""
    if confidence >= 0.8:
        level = "hoch"
    elif confidence >= 0.5:
        level = "mittel"
    else:
        level = "niedrig"
    return f"Sicherheit: {level}"


def method_text(method: ExtractionMethod | None, origin: TextLayerOrigin | None = None) -> str:
    if method is None:
        return ""
    text = METHOD_LABELS.get(method, str(method))
    if method == ExtractionMethod.PDF_TEXT and origin == TextLayerOrigin.EMBEDDED_OCR:
        text += " (fremde OCR-Textebene)"
    return text


def page_origin(case: Case | None, document_id: str | None, page: int | None) -> TextLayerOrigin | None:
    if case is None or not document_id or not page:
        return None
    doc = case.document(document_id)
    if doc is None or doc.text is None:
        return None
    for p in doc.text.pages:
        if p.page_number == page:
            return p.origin
    return None


def document_name(case: Case | None, document_id: str | None) -> str:
    if case is None or not document_id:
        return ""
    doc = case.document(document_id)
    return doc.original_name if doc else document_id


SHORT_DOC_TYPES: dict[DocumentType, str] = {
    DocumentType.POLICY: "Police",
    DocumentType.AMENDMENT: "Nachtrag",
    DocumentType.PREMIUM_NOTICE: "Beitragsmitteilung",
    DocumentType.STATUS_REPORT: "Standmitteilung",
    DocumentType.CANCELLATION_CONFIRMATION: "Kündigungsbestätigung",
    DocumentType.PORTAL_OVERVIEW: "Portal-Übersicht",
}


def short_document_label(case: Case | None, document_id: str | None, fallback: str = "") -> str:
    """Kurzname einer Quelle: Dokumenttyp (z. B. „Police“), wenn eindeutig, sonst Dateiname."""
    if case is None or not document_id:
        return fallback
    doc = case.document(document_id)
    if doc is None:
        return fallback or document_id
    cls = doc.classification
    if cls is not None and cls.doc_type in SHORT_DOC_TYPES:
        same = [
            d
            for d in case.documents
            if d.classification is not None and d.classification.doc_type == cls.doc_type
        ]
        if len(same) == 1:
            return SHORT_DOC_TYPES[cls.doc_type]
    return doc.original_name


def source_text(case: Case | None, fv: FieldValue | None) -> tuple[str, str]:
    """(Kurztext, Tooltip) für die Spalte „Quelle / Seite“."""
    if fv is None:
        return "", ""
    if fv.status == FieldStatus.CONFLICT:
        lines = []
        labels: list[str] = []
        for c in fv.candidates:
            ev = c.evidence
            page = f", Seite {ev.page}" if ev.page else ""
            lines.append(f"• {ev.file_name}{page}")
            label = short_document_label(case, ev.document_id, ev.file_name)
            if label not in labels:
                labels.append(label)
        return f"{len(fv.candidates)} Quellen:\n" + ", ".join(labels), "\n".join(lines)
    if fv.status == FieldStatus.MANUAL:
        return "manuell eingetragen", "Von Ihnen eingetragen oder ausgewählt."
    if fv.source_document_id:
        name = document_name(case, fv.source_document_id)
        label = short_document_label(case, fv.source_document_id, name)
        page = f", S. {fv.source_page}" if fv.source_page else ""
        origin = page_origin(case, fv.source_document_id, fv.source_page)
        tip = f"{name}{page}\nMethode: {method_text(fv.extraction_method, origin)}"
        extra = len({(c.evidence.document_id, c.evidence.page) for c in fv.candidates}) - 1
        short = f"{label}{page}"
        if extra > 0:
            short += f"\n(+{extra} weitere)"
            tip += f"\nAußerdem in {extra} weiteren Fundstelle(n) mit gleichem Wert."
        return short, tip
    if fv.candidates:
        ev = fv.candidates[0].evidence
        page = f", S. {ev.page}" if ev.page else ""
        return f"{short_document_label(case, ev.document_id, ev.file_name)}{page}", ev.file_name
    return "—", "Keine Fundstelle in den Dokumenten."
