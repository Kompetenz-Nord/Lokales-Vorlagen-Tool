"""Zusammenführung der Kandidaten mehrerer Dokumente zu Feldwerten.

Grundsätze
----------
* Keine automatische Auswahl bei Widersprüchen: unterschiedliche Werte ergeben
  ``CONFLICT`` ohne Wert. Ein Nachtrag mit ausdrücklichem Gültigkeitsdatum wird
  höchstens als *Hinweis* markiert (``suggestion_index``) – entscheiden muss der Mensch.
* Nie nach Dateidatum entscheiden.
* Bestätigte/manuelle Werte bleiben erhalten; weicht eine NEUE Quelle ab, entsteht
  ein Konflikt mit dem bisher bestätigten Wert als Auswahlmöglichkeit.
* Auftragsdaten (``order.*``) werden nie angefasst.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory, FieldType, contract_fields
from insurance_app.core.models import (
    Candidate,
    DocumentType,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    HistoryEntry,
    Severity,
    ValidationMessage,
)
from insurance_app.core.textutil import fold, format_date_de

from .extractor import ExtractionResult, SourceDocInfo
from .normalize import NOTE_UNREADABLE, compact_key, is_placeholder, is_reference

__all__ = [
    "MIN_EXTRACTED_CONFIDENCE",
    "MergeOutcome",
    "contract_number_key",
    "contract_numbers_by_document",
    "group_documents",
    "merge_candidates",
]

log = logging.getLogger(__name__)

MIN_EXTRACTED_CONFIDENCE = 0.45
CONFIRMED_LABEL = "bisher bestätigter Wert"
NOTE_CONFIRMED_DIFFERS = "Neue Quelle weicht vom bestätigten Wert ab."
NOTE_CONFLICT = "Die Quellen nennen unterschiedliche Werte – bitte den richtigen Wert auswählen."
NOTE_UNCLEAR_LOW = "Erkennung unsicher – bitte mit der Quelle vergleichen."
NOTE_ONLY_UNREADABLE = f"{NOTE_UNREADABLE} – bitte prüfen."
NOTE_EMPTY_IN_SOURCE = "In der Quelle ist für dieses Feld kein Wert eingetragen."
NOTE_REFERENCE = (
    "Die Quelle nennt keinen eigenen Wert, sondern verweist auf eine andere Angabe "
    "(z. B. „wie Versicherungsnehmer“). Bitte den Wert selbst eintragen."
)
NOTE_INSURER_IBAN = "Eine IBAN entspricht der Bankverbindung des Versicherers und wurde nicht übernommen."

_KEEP_STATUSES = (FieldStatus.CONFIRMED, FieldStatus.MANUAL)


class MergeOutcome(BaseModel):
    """Ergebnis der Zusammenführung: neue Feldwerte (nur Vertragsdaten), Verlauf, Hinweise."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, FieldValue] = Field(default_factory=dict)
    history: list[HistoryEntry] = Field(default_factory=list)
    warnings: list[ValidationMessage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Vertragsnummern / Dokumentgruppen
# ---------------------------------------------------------------------------


_CN_ALPHA_PREFIX = re.compile(r"^[A-Z]{1,4}(?=\d+$)")


def contract_number_key(number: str) -> str:
    """Vergleichsschlüssel einer Vertragsnummer (nur Buchstaben/Ziffern, groß).

    Ein kurzes Buchstaben-Präfix vor einer reinen Ziffernfolge (``LV 1234567``)
    zählt nicht: „LV 1234567“ und „1234567“ sind dieselbe Nummer.
    """
    key = re.sub(r"[^A-Z0-9]", "", number.upper())
    return _CN_ALPHA_PREFIX.sub("", key)


def contract_numbers_by_document(
    results: Sequence[tuple[SourceDocInfo, ExtractionResult]],
) -> dict[str, set[str]]:
    """Dokument-ID -> Menge der (normalisierten) Vertragsnummern laut Kandidaten."""
    out: dict[str, set[str]] = {}
    for info, res in results:
        nums = {
            contract_number_key(c.normalized)
            for c in res.candidates.get("contract.number", [])
            if c.normalized
        }
        out.setdefault(info.document_id, set()).update(n for n in nums if n)
    return out


def group_documents(results: Sequence[tuple[SourceDocInfo, ExtractionResult]]) -> list[list[str]]:
    """Dokumente mit gemeinsamer Vertragsnummer gruppieren (z. B. Police + mehrere Nachträge).

    Dokumente ohne erkannte Vertragsnummer bilden je eine eigene Gruppe.
    """
    numbers = contract_numbers_by_document(results)
    order = list(dict.fromkeys(info.document_id for info, _ in results))
    parent = {d: d for d in order}

    def find(d: str) -> str:
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    owner: dict[str, str] = {}
    for doc in order:
        for num in sorted(numbers.get(doc, ())):
            if num in owner:
                ra, rb = find(owner[num]), find(doc)
                if ra != rb:
                    first, second = (ra, rb) if order.index(ra) < order.index(rb) else (rb, ra)
                    parent[second] = first
            else:
                owner[num] = doc
    groups: dict[str, list[str]] = {}
    for doc in order:
        groups.setdefault(find(doc), []).append(doc)
    return list(groups.values())


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


_STREET_ABBR = re.compile(r"str(?:asse\b|\.|\b)")
_INSURER_FORM_TOKENS = {
    "ag",
    "se",
    "ag.",
    "a.g.",
    "vvag",
    "kgaa",
    "gmbh",
    "versicherung",
    "versicherungen",
    "versicherungs-ag",
    "lebensversicherung",
    "lebensversicherungs-ag",
}


def _street_key(normalized: str) -> str:
    """„Musterstr. 12“ und „Musterstraße 12“ vergleichen gleich."""
    return compact_key(_STREET_ABBR.sub("strasse", fold(normalized)))


def _insurer_key(normalized: str) -> str:
    """„Swiss Life AG“ und „Swiss Life“ vergleichen gleich (Rechtsform/Versicherungswort zählt nicht)."""
    tokens = [t for t in fold(normalized).split(" ") if t.strip(",;()") not in _INSURER_FORM_TOKENS]
    return compact_key(" ".join(tokens))


def _cmp_key(key: str, normalized: str) -> str:
    fdef = FIELD_REGISTRY.get(key)
    if fdef is None:
        return normalized
    if key.endswith(".street"):
        return _street_key(normalized) or compact_key(normalized) or normalized
    if key == "insurer.name":
        return _insurer_key(normalized) or compact_key(normalized) or normalized
    if fdef.dtype in (FieldType.TEXT, FieldType.NAME, FieldType.COUNTRY):
        return compact_key(normalized) or normalized
    if fdef.dtype == FieldType.CONTRACT_NUMBER:
        return contract_number_key(normalized) or normalized
    return normalized


def _cand_id(c: Candidate) -> tuple[str, int | None, str | None, str | None, str]:
    return (c.evidence.document_id, c.evidence.page, c.evidence.rule_id, c.normalized, c.value)


def _conf(c: Candidate) -> float:
    return c.confidence if c.confidence is not None else 0.0


def _placeholder_note(evidence: list[Evidence]) -> str | None:
    if not evidence:
        return None
    for ev in evidence:
        words = (ev.quote or "").split()
        for n in (3, 2, 1):
            if len(words) > n:
                tail = " ".join(words[-n:])
                if tail.strip() and is_placeholder(tail):
                    return f"Quelle nennt den Wert als '{tail}'."
        for n in (3, 2, 1):
            if len(words) > n and is_reference(" ".join(words[-n:])):
                return NOTE_REFERENCE
    return NOTE_EMPTY_IN_SOURCE


def _pseudo_candidate(old: FieldValue) -> Candidate | None:
    """Der „bisher bestätigte Wert“ eines offenen Konflikts (oder ``None``)."""
    return next(
        (
            c
            for c in old.candidates
            if c.evidence.file_name == CONFIRMED_LABEL and c.evidence.method == ExtractionMethod.MANUAL
        ),
        None,
    )


def _confirmed_conflict(
    key: str, pseudo: Candidate, cands: list[Candidate], doc_numbers: dict[str, set[str]]
) -> FieldValue:
    """Konflikt mit dem bisher bestätigten Wert als erster Auswahlmöglichkeit."""
    idx, hint = _suggestion(key, cands, doc_numbers)
    note = NOTE_CONFIRMED_DIFFERS if hint is None else f"{NOTE_CONFIRMED_DIFFERS} {hint}"
    return FieldValue(
        key=key,
        status=FieldStatus.CONFLICT,
        candidates=[pseudo, *cands],
        note=note,
        suggestion_index=None if idx is None else idx + 1,
    )


def _from_best(key: str, best: Candidate, cands: list[Candidate], status: FieldStatus, note: str | None):
    ev = best.evidence
    return FieldValue(
        key=key,
        value=best.value,
        normalized_value=best.normalized,
        status=status,
        confidence=best.confidence,
        source_document_id=ev.document_id,
        source_page=ev.page,
        source_quote=ev.quote,
        extraction_method=ev.method,
        model_version=ev.model_version,
        candidates=cands,
        note=note,
    )


def _suggestion(
    key: str, cands: list[Candidate], doc_numbers: dict[str, set[str]]
) -> tuple[int | None, str | None]:
    """Hinweis auf den Nachtrag mit dem spätesten ausdrücklichen Gültigkeitsdatum.

    Nur wenn alle beteiligten Dokumente dieselbe (einzige) Vertragsnummer tragen.
    """
    docs = {c.evidence.document_id for c in cands}
    numbers: set[str] = set()
    for doc in docs:
        nums = doc_numbers.get(doc)
        if not nums or len(nums) != 1:
            return None, None
        numbers |= nums
    if len(numbers) != 1:
        return None, None
    amendments = [
        (i, c)
        for i, c in enumerate(cands)
        if c.document_type == DocumentType.AMENDMENT
        and c.effective_date is not None
        and c.normalized is not None
    ]
    if not amendments:
        return None, None
    latest = max(c.effective_date for _, c in amendments if c.effective_date is not None)
    top = [(i, c) for i, c in amendments if c.effective_date == latest]
    if len({_cmp_key(key, c.normalized or "") for _, c in top}) != 1:
        return None, None  # mehrere Nachträge gleichen Datums widersprechen sich
    idx = max(top, key=lambda ic: _conf(ic[1]))[0]
    note = (
        f"Hinweis: Der Nachtrag (gültig ab {format_date_de(latest)}) ist fachlich vermutlich maßgeblich "
        "– bitte selbst entscheiden."
    )
    return idx, note


def _merge_field(
    key: str,
    old: FieldValue | None,
    cands: list[Candidate],
    placeholders: list[Evidence],
    doc_numbers: dict[str, set[str]],
) -> FieldValue:
    if old is not None and old.status == FieldStatus.NOT_APPLICABLE:
        return old

    if old is not None and old.status == FieldStatus.CONFLICT:
        # Offener Konflikt gegen einen bestätigten/manuellen Wert: Dieser Wert darf bei
        # keiner erneuten Zusammenführung verloren gehen, bis der Nutzer entscheidet.
        pseudo = _pseudo_candidate(old)
        if pseudo is not None:
            return _confirmed_conflict(key, pseudo, cands, doc_numbers)

    if old is not None and old.status in _KEEP_STATUSES:
        if not cands:
            return old
        known = {_cand_id(c) for c in old.candidates}
        new = [c for c in cands if _cand_id(c) not in known]
        confirmed = old.normalized_value if old.normalized_value is not None else old.value
        confirmed_cmp = _cmp_key(key, confirmed) if confirmed is not None else None
        differing = [
            c for c in new if c.normalized is not None and _cmp_key(key, c.normalized) != confirmed_cmp
        ]
        if not differing:
            if not new:
                return old
            return old.model_copy(update={"candidates": [*old.candidates, *new]})
        pseudo = Candidate(
            field_key=key,
            value=old.value if old.value is not None else (confirmed or ""),
            normalized=old.normalized_value,
            confidence=None,
            evidence=Evidence(
                document_id=old.source_document_id or "manuell",
                file_name=CONFIRMED_LABEL,
                page=old.source_page,
                quote=None,
                method=ExtractionMethod.MANUAL,
            ),
            note="Bisher bestätigter Wert",
        )
        return _confirmed_conflict(key, pseudo, cands, doc_numbers)

    if not cands:
        return FieldValue(key=key, status=FieldStatus.MISSING, note=_placeholder_note(placeholders))

    parsable = [c for c in cands if c.normalized is not None]
    if not parsable:
        best = max(cands, key=_conf)
        fv = _from_best(key, best, cands, FieldStatus.UNCLEAR, NOTE_ONLY_UNREADABLE)
        return fv.model_copy(update={"normalized_value": None})

    distinct = list(dict.fromkeys(_cmp_key(key, c.normalized or "") for c in parsable))
    if len(distinct) == 1:
        best = max(parsable, key=_conf)
        if _conf(best) >= MIN_EXTRACTED_CONFIDENCE:
            return _from_best(key, best, cands, FieldStatus.EXTRACTED, best.note)
        return _from_best(key, best, cands, FieldStatus.UNCLEAR, best.note or NOTE_UNCLEAR_LOW)

    idx, hint = _suggestion(key, cands, doc_numbers)
    return FieldValue(
        key=key,
        status=FieldStatus.CONFLICT,
        candidates=cands,
        note=hint or NOTE_CONFLICT,
        suggestion_index=idx,
    )


def _source_text(fv: FieldValue) -> str | None:
    if fv.source_document_id is None:
        return None
    best = next(
        (
            c
            for c in fv.candidates
            if c.evidence.document_id == fv.source_document_id and c.evidence.page == fv.source_page
        ),
        None,
    )
    if best is None:
        return None
    ev = best.evidence
    return f"{ev.file_name}, S. {ev.page}, {ev.method.value}"


def _history(key: str, old: FieldValue | None, new: FieldValue) -> HistoryEntry | None:
    if old is new:
        return None
    if old is not None and old.status in _KEEP_STATUSES and new.status == old.status:
        added = len(new.candidates) - len(old.candidates)
        if added <= 0:
            return None
        return HistoryEntry(
            actor="system",
            action="sources_added",
            field_key=key,
            old_status=old.status,
            new_status=new.status,
            source=None,
            detail=f"{added} weitere übereinstimmende Quelle(n)",
        )
    changed = (
        old is None
        or old.status != new.status
        or old.normalized_value != new.normalized_value
        or len(old.candidates) != len(new.candidates)
    )
    if not changed:
        return None
    if new.status in (FieldStatus.EXTRACTED, FieldStatus.UNCLEAR):
        action, detail = "extracted", f"{len(new.candidates)} Quelle(n)"
    elif new.status == FieldStatus.CONFLICT:
        distinct = {c.normalized for c in new.candidates if c.normalized is not None}
        action, detail = "conflict", f"{len(distinct)} unterschiedliche Werte"
    elif new.status == FieldStatus.MISSING and old is not None and old.status != FieldStatus.MISSING:
        action, detail = "cleared", "keine Quelle mehr vorhanden"
    else:
        return None
    return HistoryEntry(
        actor="system",
        action=action,
        field_key=key,
        old_status=old.status if old else None,
        new_status=new.status,
        old_value=(old.effective_value if old else None),
        new_value=new.effective_value,
        source=_source_text(new),
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------


def merge_candidates(
    results: list[tuple[SourceDocInfo, ExtractionResult]],
    existing: dict[str, FieldValue] | None = None,
) -> MergeOutcome:
    """Kandidaten aller Dokumente zu Feldwerten zusammenführen (nur Vertragsdaten).

    ``existing``: bisherige Vertragsfelder des Vorgangs (bestätigte/manuelle Werte
    und „nicht relevant“ bleiben erhalten). Das Ergebnis enthält alle
    Vertragsfelder; Auftragsdaten sind nie enthalten.
    """
    existing = existing or {}
    by_field: dict[str, list[Candidate]] = {}
    placeholders: dict[str, list[Evidence]] = {}
    ignored_order = 0
    for _info, res in results:
        for key, cands in res.candidates.items():
            fdef = FIELD_REGISTRY.get(key)
            if fdef is None or fdef.category != FieldCategory.CONTRACT or not fdef.extractable:
                ignored_order += len(cands)
                continue
            by_field.setdefault(key, []).extend(sorted(cands, key=lambda c: -_conf(c)))
        for key, evs in res.placeholders.items():
            placeholders.setdefault(key, []).extend(evs)

    warnings: list[ValidationMessage] = []
    # Bankverbindung des Versicherers aus irgendeinem Dokument ist nie die des Kunden
    insurer_ibans = {iban for _info, res in results for iban in res.insurer_ibans}
    if insurer_ibans and "bank.iban" in by_field:
        kept = [c for c in by_field["bank.iban"] if c.normalized not in insurer_ibans]
        if len(kept) != len(by_field["bank.iban"]):
            warnings.append(
                ValidationMessage(
                    severity=Severity.INFO,
                    code="INSURER_IBAN_IGNORED",
                    message=NOTE_INSURER_IBAN,
                    field_key="bank.iban",
                )
            )
        by_field["bank.iban"] = kept
        if not kept:
            del by_field["bank.iban"]
    doc_numbers = contract_numbers_by_document(results)
    distinct_numbers = set().union(*doc_numbers.values()) if doc_numbers else set()
    if len(distinct_numbers) > 1:
        warnings.append(
            ValidationMessage(
                severity=Severity.WARNING,
                code="DOCS_DIFFERENT_CONTRACTS",
                message=(
                    f"Die Dokumente nennen {len(distinct_numbers)} verschiedene Vertragsnummern. "
                    "Bitte prüfen, ob alle Dokumente zu diesem Vorgang gehören."
                ),
                field_key="contract.number",
            )
        )
    if ignored_order:
        log.warning("Merge: %d Kandidaten für nicht extrahierbare Felder ignoriert", ignored_order)

    fields: dict[str, FieldValue] = {}
    history: list[HistoryEntry] = []
    for fdef in contract_fields():
        key = fdef.key
        old = existing.get(key)
        if not fdef.extractable:
            if old is not None:
                fields[key] = old
            continue
        new = _merge_field(key, old, by_field.get(key, []), placeholders.get(key, []), doc_numbers)
        fields[key] = new
        entry = _history(key, old, new)
        if entry is not None:
            history.append(entry)
    log.debug(
        "Merge: %d Dokumente, %d Felder mit Kandidaten, %d Konflikte",
        len(results),
        len(by_field),
        sum(1 for fv in fields.values() if fv.status == FieldStatus.CONFLICT),
    )
    return MergeOutcome(fields=fields, history=history, warnings=warnings)
