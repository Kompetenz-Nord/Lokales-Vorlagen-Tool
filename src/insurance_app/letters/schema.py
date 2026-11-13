"""Schema für generierte Briefvorlagen (DIN 5008).

Briefvorlagen sind **Kundenschreiben an den Versicherer** – keine Formulare des
Versicherers. Sie enthalten keine Logos und geben sich nicht als Original aus.

Platzhalter in Texten
---------------------
* ``{feld.schluessel}``          Pflichtwert (muss bestätigt/manuell erfasst sein)
* ``{feld.schluessel?}``         optionaler Wert (fehlt er, bleibt die Stelle leer)
* ``{feld.schluessel|filter}``   Formatierung: ``date`` (TT.MM.JJJJ), ``amount`` (1.234,56 €),
                                 ``interval`` (monatlich …), ``choice`` (Beschriftung), ``iban`` (4er-Gruppen)
* ``{@name}``                    abgeleiteter Textbaustein (siehe ``derived``)
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.models import Severity
from ..templates.schema import Acknowledgment, Condition

PLACEHOLDER = re.compile(r"\{(?:(@[a-z_]+)|([a-z_]+\.[a-z_]+)(\?)?(?:\|([a-z_]+))?)\}")
FILTERS = {"date", "amount", "interval", "choice", "iban", "text"}


class LetterBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    conditions: list[Condition] = Field(
        default_factory=list
    )  # UND-verknüpft, Auswertung von links nach rechts
    style: Literal["paragraph", "line", "list"] = "paragraph"  # line = ohne Leerzeile danach


class DerivedText(BaseModel):
    """Textbaustein, der von einer Auswahl abhängt (z. B. Beendigungszeitpunkt)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    cases: dict[str, str]  # normalisierter Wert -> Text (darf Platzhalter enthalten)


class LetterHint(BaseModel):
    """Prüfbedürftiger Hinweis für den Anwender (wird NICHT gedruckt)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    source: str | None = None  # z. B. "§ 8 Abs. 1 VVG"
    severity: Severity = Severity.INFO
    conditions: list[Condition] = Field(default_factory=list)


class InfoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    text: str
    optional: bool = True  # leere optionale Zeilen entfallen


class AllowedValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    values: list[str]
    message: str
    conditions: list[Condition] = Field(default_factory=list)


class LetterTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    catalog_id: str
    version: str
    kind: Literal["letter"] = "letter"
    title: str  # Anzeige im Katalog
    short_title: str  # für Dateinamen
    form_code: str = "DIN5008"
    subject_variants: list[LetterBlock]  # erste zutreffende Variante = fett gedruckter Betreff
    subject_lines: list[LetterBlock] = Field(default_factory=list)  # weitere Betreffzeilen (normal)
    info_block: list[InfoItem] = Field(default_factory=list)
    salutation: str = "Sehr geehrte Damen und Herren,"
    blocks: list[LetterBlock]
    closing: str = "Mit freundlichen Grüßen"
    derived: dict[str, DerivedText] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)  # nur nicht-personenbezogene feste Werte
    allowed_values: list[AllowedValues] = Field(default_factory=list)
    acknowledgments: list[Acknowledgment] = Field(default_factory=list)
    plausibility_rules: list[str] = Field(default_factory=list)
    hints: list[LetterHint] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> LetterTemplate:
        for text in self.all_texts():
            for m in PLACEHOLDER.finditer(text):
                derived, _key, _opt, flt = m.groups()
                if derived and derived[1:] not in self.derived:
                    raise ValueError(f"Unbekannter abgeleiteter Baustein {derived} in {self.template_id}")
                if flt and flt not in FILTERS:
                    raise ValueError(f"Unbekannter Filter |{flt} in {self.template_id}")
        if not self.subject_variants:
            raise ValueError("Mindestens eine Betreff-Variante erforderlich")
        return self

    def all_texts(self) -> list[str]:
        texts = [b.text for b in self.subject_variants + self.subject_lines + self.blocks]
        texts += [i.text for i in self.info_block]
        for d in self.derived.values():
            texts += list(d.cases.values())
        return texts

    def referenced_keys(self) -> set[str]:
        keys: set[str] = set()
        for text in self.all_texts():
            for m in PLACEHOLDER.finditer(text):
                if m.group(2):
                    keys.add(m.group(2))
        for b in self.subject_variants + self.subject_lines + self.blocks:
            keys.update(c.field for c in b.conditions)
        keys.update(d.field for d in self.derived.values())
        keys.update(a.field for a in self.acknowledgments)
        keys.update(a.field for a in self.allowed_values)
        return keys
