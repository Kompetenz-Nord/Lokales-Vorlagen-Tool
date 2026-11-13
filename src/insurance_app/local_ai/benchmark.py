"""Vergleich von Extraktionsergebnissen mit einer Musterlösung (Ground Truth).

Gedacht für den Vergleich „nur OCR“ gegen „OCR + lokale KI“ auf synthetischen
Musterdokumenten (``*.truth.json``). Zählweise je Feld der Musterlösung:

* ``correct`` – Wert stimmt (nach Vergleichsnormalisierung) überein
* ``wrong``   – ein anderer Wert wurde geliefert (gefährlichster Fehler)
* ``missing`` – kein Wert geliefert

Zusätzlich ``unexpected``: gelieferte Werte für Felder ohne Musterlösung
(nicht Teil von ``total``; Hinweis auf mögliche Halluzinationen).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.fields import FIELD_REGISTRY, FieldType
from insurance_app.core.textutil import fold

__all__ = ["FieldScore", "Outcome", "compare_methods", "score_extraction", "values_equal"]

Outcome = Literal["correct", "wrong", "missing"]
Comparator = Callable[[str, str, str], bool]

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_TEXTUAL = (FieldType.TEXT, FieldType.NAME, FieldType.COUNTRY, FieldType.CONTRACT_NUMBER)


class FieldScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correct: int = 0
    wrong: int = 0
    missing: int = 0
    total: int = 0
    unexpected: int = 0
    details: dict[str, Outcome] = Field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return self.wrong / self.total if self.total else 0.0

    @property
    def missing_rate(self) -> float:
        return self.missing / self.total if self.total else 0.0


def values_equal(key: str, expected: str, actual: str) -> bool:
    """Vergleich nach kanonischer Form; Text/Namen/Nummern ohne Groß-/Kleinschreibung und Trennzeichen."""
    exp = expected.strip()
    act = actual.strip()
    if exp == act:
        return True
    fdef = FIELD_REGISTRY.get(key)
    if fdef is not None and fdef.dtype in _TEXTUAL:
        return _NON_ALNUM.sub("", fold(exp)) == _NON_ALNUM.sub("", fold(act))
    if fdef is not None and fdef.dtype == FieldType.IBAN:
        return exp.replace(" ", "").upper() == act.replace(" ", "").upper()
    return False


def score_extraction(
    ground_truth: dict[str, str],
    extracted: dict[str, str | None],
    comparator: Comparator | None = None,
) -> FieldScore:
    """Extraktion gegen die Musterlösung bewerten (Details je Feld in ``FieldScore.details``)."""
    equal = comparator or values_equal
    score = FieldScore(total=len(ground_truth))
    for key, expected in ground_truth.items():
        actual = extracted.get(key)
        if actual is None or not str(actual).strip():
            score.missing += 1
            score.details[key] = "missing"
        elif equal(key, str(expected), str(actual)):
            score.correct += 1
            score.details[key] = "correct"
        else:
            score.wrong += 1
            score.details[key] = "wrong"
    score.unexpected = sum(
        1
        for key, value in extracted.items()
        if key not in ground_truth and value is not None and str(value).strip()
    )
    return score


def compare_methods(
    ground_truth: dict[str, str],
    results_by_method: dict[str, dict[str, str | None]],
    comparator: Comparator | None = None,
) -> dict[str, dict[str, Any]]:
    """Mehrere Verfahren vergleichen.

    Rückgabe je Verfahren: ``correct``, ``wrong``, ``missing``, ``total``, ``unexpected``,
    ``accuracy`` (= correct/total), ``error_rate`` (= wrong/total), ``missing_rate``
    (= missing/total) und ``details`` (Feld -> Ergebnis).
    """
    out: dict[str, dict[str, Any]] = {}
    for method, extracted in results_by_method.items():
        score = score_extraction(ground_truth, extracted, comparator)
        out[method] = {
            "correct": score.correct,
            "wrong": score.wrong,
            "missing": score.missing,
            "total": score.total,
            "unexpected": score.unexpected,
            "accuracy": round(score.accuracy, 4),
            "error_rate": round(score.error_rate, 4),
            "missing_rate": round(score.missing_rate, 4),
            "details": dict(score.details),
        }
    return out
