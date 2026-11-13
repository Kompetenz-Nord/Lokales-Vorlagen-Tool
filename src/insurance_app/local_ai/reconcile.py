"""Abgleich von KI-Vorschlägen (``LOCAL_VLM``) mit den bisherigen Feldwerten.

Die KI entscheidet nie selbst. Regeln je Ausgangsstatus:

* ``CONFIRMED`` / ``MANUAL`` / ``NOT_APPLICABLE``: Status und Wert bleiben. Der Vorschlag
  wird angehängt; weicht er ab, erscheint der sichtbare Hinweis
  „Lokale KI schlägt einen abweichenden Wert vor – bitte prüfen.“
* ``EXTRACTED``: gleicher Wert → bleibt, Vorschlag wird angehängt;
  abweichender Wert → ``CONFLICT`` ohne Wert (alle Kandidaten zur Auswahl).
* ``MISSING``: → ``EXTRACTED`` mit Hinweis „Nur durch lokale KI gelesen – besonders
  sorgfältig prüfen.“ (Ist der KI-Wert nicht normalisierbar → ``UNCLEAR``.)
* ``UNCLEAR``: Waren alle bisherigen Kandidaten unlesbar (ohne Normalwert) und ist der
  KI-Wert lesbar → ``EXTRACTED`` mit demselben Hinweis. Weicht ein lesbarer KI-Wert von den
  (unsicheren) bisherigen Werten ab → ``CONFLICT`` (beide Werte zur Auswahl); sonst bleibt ``UNCLEAR``.
* ``CONFLICT``: bleibt ``CONFLICT``, Vorschlag wird angehängt.

Auftragsdaten (``order.*``) werden mit ``ValueError`` abgelehnt. Jede Änderung
erzeugt einen Verlaufseintrag. Die Eingabe wird nicht verändert.
"""

from __future__ import annotations

import re

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory, FieldType
from insurance_app.core.models import (
    Candidate,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    HistoryEntry,
)
from insurance_app.core.textutil import fold

__all__ = [
    "NOTE_AI_CONFLICT",
    "NOTE_AI_DIFFERS",
    "NOTE_AI_ONLY",
    "NOTE_AI_ONLY_UNREADABLE",
    "reconcile",
    "same_value",
]

NOTE_AI_DIFFERS = "Lokale KI schlägt einen abweichenden Wert vor – bitte prüfen."
NOTE_AI_ONLY = "Nur durch lokale KI gelesen – besonders sorgfältig prüfen."
NOTE_AI_ONLY_UNREADABLE = "Nur durch lokale KI gelesen – Wert nicht eindeutig lesbar, bitte prüfen."
NOTE_AI_CONFLICT = (
    "Lokale KI und Texterkennung nennen unterschiedliche Werte – bitte den richtigen Wert auswählen."
)

_KEEP = (FieldStatus.CONFIRMED, FieldStatus.MANUAL, FieldStatus.NOT_APPLICABLE)
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_TEXTUAL = (FieldType.TEXT, FieldType.NAME, FieldType.COUNTRY, FieldType.CONTRACT_NUMBER)


def _compact(text: str) -> str:
    return _NON_ALNUM.sub("", fold(text))


def _cmp_key(key: str, normalized: str | None, raw: str | None) -> str | None:
    fdef = FIELD_REGISTRY.get(key)
    if normalized is not None:
        if fdef is not None and fdef.dtype in _TEXTUAL:
            return _compact(normalized) or normalized
        return normalized
    if raw is not None:
        return "raw:" + (_compact(raw) or raw)
    return None


def same_value(
    key: str, a_norm: str | None, a_raw: str | None, b_norm: str | None, b_raw: str | None
) -> bool:
    """Gleicher Wert? Bevorzugt Normalwerte; Text/Namen/Nummern ohne Groß-/Kleinschreibung und Trennzeichen."""
    if a_norm is not None and b_norm is not None:
        return _cmp_key(key, a_norm, None) == _cmp_key(key, b_norm, None)
    a_raw_eff = a_raw if a_raw is not None else a_norm
    b_raw_eff = b_raw if b_raw is not None else b_norm
    if a_raw_eff is None or b_raw_eff is None:
        return False
    return _cmp_key(key, None, a_raw_eff) == _cmp_key(key, None, b_raw_eff)


def _cand_identity(c: Candidate) -> tuple[object, ...]:
    ev = c.evidence
    return (ev.document_id, ev.page, ev.method, ev.quote, c.value, c.normalized)


def _source(c: Candidate) -> str:
    ev = c.evidence
    page = f", S. {ev.page}" if ev.page is not None else ""
    model = f" ({ev.model_version})" if ev.model_version else ""
    return f"{ev.file_name}{page}, {ev.method.value}{model}"


def _add_note(existing: str | None, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} | {note}"


def _candidate_from_field(fv: FieldValue) -> Candidate | None:
    """Bisherigen Feldwert als Kandidat sichern (falls er nicht schon in ``candidates`` steht)."""
    if fv.value is None and fv.normalized_value is None:
        return None
    for c in fv.candidates:
        if same_value(fv.key, c.normalized, c.value, fv.normalized_value, fv.value):
            return None
    doc = fv.source_document_id or "unbekannt"
    return Candidate(
        field_key=fv.key,
        value=fv.value if fv.value is not None else (fv.normalized_value or ""),
        normalized=fv.normalized_value,
        confidence=fv.confidence,
        evidence=Evidence(
            document_id=doc,
            file_name=doc,
            page=fv.source_page,
            quote=fv.source_quote,
            method=fv.extraction_method or ExtractionMethod.PDF_TEXT,
            model_version=fv.model_version,
            rule_id="bisheriger_wert",
        ),
        note="bisher erkannter Wert",
    )


def _take_candidate(fv: FieldValue, cand: Candidate, status: FieldStatus, note: str) -> FieldValue:
    ev = cand.evidence
    return fv.model_copy(
        update={
            "value": cand.value,
            "normalized_value": cand.normalized,
            "status": status,
            "confidence": cand.confidence,
            "source_document_id": ev.document_id,
            "source_page": ev.page,
            "source_quote": ev.quote,
            "extraction_method": ev.method,
            "model_version": ev.model_version,
            "user_confirmed_at": None,
            "candidates": [*fv.candidates, cand],
            "note": note,
            "suggestion_index": None,
        }
    )


def _history(
    action: str, key: str, old: FieldValue, new: FieldValue, cand: Candidate, detail: str
) -> HistoryEntry:
    return HistoryEntry(
        actor="system",
        action=action,
        field_key=key,
        old_status=old.status,
        new_status=new.status,
        old_value=old.effective_value,
        new_value=new.effective_value
        if new.status != FieldStatus.CONFLICT
        else cand.normalized or cand.value,
        source=_source(cand),
        detail=detail,
    )


def _apply(fv: FieldValue, cand: Candidate) -> tuple[FieldValue, HistoryEntry | None]:
    key = fv.key
    if any(_cand_identity(c) == _cand_identity(cand) for c in fv.candidates):
        return fv, None  # bereits bekannt (wiederholter Abgleich)
    matches = same_value(key, cand.normalized, cand.value, fv.normalized_value, fv.value)
    status = fv.status

    if status in _KEEP:
        if matches:
            new = fv.model_copy(update={"candidates": [*fv.candidates, cand]})
            return new, _history(
                "ai_suggestion_matches", key, fv, new, cand, "Lokale KI liest denselben Wert (nur Hinweis)"
            )
        new = fv.model_copy(
            update={"candidates": [*fv.candidates, cand], "note": _add_note(fv.note, NOTE_AI_DIFFERS)}
        )
        return new, _history(
            "ai_suggestion_differs",
            key,
            fv,
            new,
            cand,
            f"Lokale KI schlägt einen abweichenden Wert vor: {cand.normalized or cand.value}",
        )

    if status == FieldStatus.EXTRACTED:
        if matches:
            new = fv.model_copy(update={"candidates": [*fv.candidates, cand]})
            return new, _history(
                "ai_suggestion_matches", key, fv, new, cand, "Lokale KI liest denselben Wert"
            )
        previous = _candidate_from_field(fv)
        cands = [*fv.candidates, *([previous] if previous else []), cand]
        new = fv.model_copy(
            update={
                "value": None,
                "normalized_value": None,
                "status": FieldStatus.CONFLICT,
                "confidence": None,
                "source_document_id": None,
                "source_page": None,
                "source_quote": None,
                "extraction_method": None,
                "model_version": None,
                "user_confirmed_at": None,
                "candidates": cands,
                "note": NOTE_AI_CONFLICT,
                "suggestion_index": None,
            }
        )
        return new, _history(
            "ai_conflict", key, fv, new, cand, "Lokale KI liest einen anderen Wert – Konflikt"
        )

    if status == FieldStatus.MISSING:
        if cand.normalized is not None:
            new = _take_candidate(fv, cand, FieldStatus.EXTRACTED, NOTE_AI_ONLY)
            return new, _history("ai_extracted", key, fv, new, cand, "Wert nur durch lokale KI gelesen")
        new = _take_candidate(fv, cand, FieldStatus.UNCLEAR, NOTE_AI_ONLY_UNREADABLE)
        return new, _history("ai_extracted", key, fv, new, cand, "Wert nur durch lokale KI gelesen (unklar)")

    if status == FieldStatus.UNCLEAR:
        prior_unreadable = bool(fv.candidates) and all(c.normalized is None for c in fv.candidates)
        if prior_unreadable and cand.normalized is not None:
            new = _take_candidate(fv, cand, FieldStatus.EXTRACTED, NOTE_AI_ONLY)
            return new, _history(
                "ai_extracted", key, fv, new, cand, "Texterkennung unlesbar – Wert durch lokale KI gelesen"
            )
        differs = cand.normalized is not None and not any(
            c.normalized is not None and same_value(key, c.normalized, c.value, cand.normalized, cand.value)
            for c in fv.candidates
        )
        if differs and fv.candidates:
            # Unsichere Texterkennung und abweichender, lesbarer KI-Wert: beide Werte zur Auswahl stellen
            new = fv.model_copy(
                update={
                    "status": FieldStatus.CONFLICT,
                    "value": None,
                    "normalized_value": None,
                    "candidates": [*fv.candidates, cand],
                    "note": NOTE_AI_CONFLICT,
                    "suggestion_index": None,
                }
            )
            return new, _history(
                "ai_conflict", key, fv, new, cand, "Unklarer Wert – lokale KI liest einen anderen Wert"
            )
        new = fv.model_copy(update={"candidates": [*fv.candidates, cand]})
        return new, _history(
            "ai_suggestion_added", key, fv, new, cand, "Vorschlag der lokalen KI ergänzt (bleibt unklar)"
        )

    # CONFLICT (und künftige Status): nie auflösen, nur ergänzen
    new = fv.model_copy(update={"candidates": [*fv.candidates, cand]})
    return new, _history(
        "ai_suggestion_added", key, fv, new, cand, "Vorschlag der lokalen KI zum Konflikt ergänzt"
    )


def reconcile(
    fields: dict[str, FieldValue], candidates: list[Candidate]
) -> tuple[dict[str, FieldValue], list[HistoryEntry]]:
    """KI-Kandidaten in Feldwerte einarbeiten -> (neue Feldwerte, Verlaufseinträge).

    Gibt ein neues Dictionary zurück (Eingabe bleibt unverändert). Felder ohne
    Eintrag werden als ``MISSING`` angenommen.
    """
    for cand in candidates:
        fdef = FIELD_REGISTRY.get(cand.field_key)
        if fdef is None:
            raise ValueError(f"Unbekannter Feldschlüssel: {cand.field_key}")
        if (
            fdef.category == FieldCategory.ORDER
            or cand.field_key.startswith("order.")
            or not fdef.extractable
        ):
            raise ValueError(f"Auftragsdaten ({cand.field_key}) dürfen nie durch die KI befüllt werden.")

    out: dict[str, FieldValue] = {k: v.model_copy(deep=True) for k, v in fields.items()}
    history: list[HistoryEntry] = []
    for cand in candidates:
        key = cand.field_key
        current = out.get(key) or FieldValue(key=key, status=FieldStatus.MISSING)
        updated, entry = _apply(current, cand)
        if entry is not None:
            out[key] = updated
            history.append(entry)
    return out, history
