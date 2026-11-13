"""Einfacher Brief: ein Formular, das wie der fertige Brief aussieht.

Idee: Der Anwender sieht genau die Angaben, die im Brief stehen – Absender, Empfänger,
Bezugsangaben (z. B. Versicherungsnummer, Kennzeichen), Betreff, Datum, Text, Name.
Die App füllt alles vor, was sie in den Dokumenten gefunden hat. Der Anwender liest
einmal gegen, ändert bei Bedarf und klickt auf „PDF jetzt erzeugen“.

Schutzregeln bleiben erhalten
* Nichts wird erfunden: Felder ohne Fund bleiben leer; Unsicheres wird als Hinweis genannt.
* Keine Fristberechnung: Der Text nennt keinen Termin, den die App ausgerechnet hat.
* Keine Unterschrift: Unter der Grußformel bleibt Platz für die handschriftliche Unterschrift.
* Platzhalter in eckigen Klammern (z. B. „[Grund bitte eintragen]“) blockieren die PDF-Erzeugung.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ..core.case import Case
from ..core.models import FieldStatus, FieldValue, PageText
from ..core.textutil import normalize_ws, parse_german_date
from ..extraction.layout import estimate_skew, group_lines
from .compose import InfoLine, LetterPlan, RenderedBlock

__all__ = [
    "DEFAULT_LETTER_TYPE",
    "SIMPLE_LETTER_TYPES",
    "SIMPLE_LETTER_VERSION",
    "Prefill",
    "Reference",
    "SimpleLetter",
    "SimpleLetterType",
    "AddressWindow",
    "find_insurer_address",
    "letter_type",
    "prefill_letter",
    "read_address_window",
    "to_plan",
    "validate_letter",
]

SIMPLE_LETTER_VERSION = "2026-11"
SALUTATION = "Sehr geehrte Damen und Herren,"
CLOSING = "Mit freundlichen Grüßen"
LABEL_CONTRACT = "Versicherungsnummer"
LABEL_PLATE = "Kennzeichen"
MAX_REFERENCES = 5


class Reference(BaseModel):
    """Bezugsangabe rechts oben im Brief, z. B. „Versicherungsnummer“ / „12345“."""

    model_config = ConfigDict(extra="forbid")

    label: str = ""
    value: str = ""


class SimpleLetter(BaseModel):
    """Alle Angaben eines einfachen Briefs – genau das, was gedruckt wird."""

    model_config = ConfigDict(extra="forbid")

    letter_type: str = "kuendigung_kfz"
    sender_name: str = ""
    sender_street: str = ""
    sender_postal_city: str = ""
    recipient: str = ""  # mehrzeilig: Name, ggf. Straße/Postfach, PLZ Ort
    references: list[Reference] = Field(default_factory=list)
    subject: str = ""
    date: str = ""  # TT.MM.JJJJ
    body: str = ""  # Anrede, Text, Grußformel – durch Leerzeilen getrennt
    signer: str = ""

    def recipient_lines(self) -> list[str]:
        return [normalize_ws(x) for x in self.recipient.splitlines() if normalize_ws(x)]

    def filled_references(self) -> list[Reference]:
        return [
            Reference(label=normalize_ws(r.label).rstrip(":"), value=normalize_ws(r.value))
            for r in self.references
            if normalize_ws(r.label) and normalize_ws(r.value)
        ]


class SimpleLetterType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str  # Auswahltext in der Oberfläche
    short_title: str  # für den Dateinamen (ASCII)
    subject: str
    text: str  # Brieftext ohne Anrede und Grußformel; Absätze durch Leerzeile getrennt
    reference_labels: list[str] = Field(default_factory=lambda: [LABEL_CONTRACT])
    hint: str = ""

    def body(self) -> str:
        return f"{SALUTATION}\n\n{self.text}\n\n{CLOSING}"


_CONFIRM = (
    "Bitte senden Sie mir eine schriftliche Kündigungsbestätigung unter Angabe des Beendigungszeitpunktes zu."
)
_DEADLINE_HINT = (
    "Die App prüft keine Fristen. Ob und zu wann die Kündigung möglich ist, ergibt sich aus Ihrem Vertrag."
)

SIMPLE_LETTER_TYPES: dict[str, SimpleLetterType] = {
    t.key: t
    for t in (
        SimpleLetterType(
            key="kuendigung_kfz",
            title="Kündigung Kfz-Versicherung",
            short_title="Kuendigung-Kfz",
            subject="Kündigung",
            text=(
                "hiermit kündige ich meine Versicherung fristgerecht, ersatzweise zum "
                f"nächstmöglichen Zeitpunkt.\n\n{_CONFIRM}"
            ),
            reference_labels=[LABEL_PLATE, LABEL_CONTRACT],
            hint=_DEADLINE_HINT,
        ),
        SimpleLetterType(
            key="kuendigung",
            title="Kündigung (andere Versicherung)",
            short_title="Kuendigung",
            subject="Kündigung",
            text=(
                "hiermit kündige ich meine Versicherung fristgerecht, ersatzweise zum "
                f"nächstmöglichen Zeitpunkt.\n\n{_CONFIRM}"
            ),
            hint=_DEADLINE_HINT,
        ),
        SimpleLetterType(
            key="sonderkuendigung",
            title="Sonderkündigung (z. B. nach Beitragserhöhung oder Schaden)",
            short_title="Sonderkuendigung",
            subject="Außerordentliche Kündigung",
            text=(
                "hiermit kündige ich meine Versicherung außerordentlich. "
                "Grund: [Grund bitte eintragen, z. B. Beitragserhöhung laut Ihrem Schreiben vom …]\n\n"
                f"{_CONFIRM}"
            ),
            hint=(
                "Bitte den Grund in den eckigen Klammern ersetzen. Ob ein Sonderkündigungsrecht besteht "
                "und welche Frist gilt, prüft die App nicht."
            ),
        ),
        SimpleLetterType(
            key="widerruf",
            title="Widerruf",
            short_title="Widerruf",
            subject="Widerruf",
            text=(
                "hiermit widerrufe ich meine Vertragserklärung zum oben genannten Vertrag.\n\n"
                "Bitte bestätigen Sie mir den Eingang des Widerrufs schriftlich."
            ),
            hint="Die App prüft nicht, ob die Widerrufsfrist noch läuft.",
        ),
        SimpleLetterType(
            key="freier_brief",
            title="Freier Brief (eigener Text)",
            short_title="Brief",
            subject="",
            text="[Text bitte eintragen]",
        ),
    )
}
DEFAULT_LETTER_TYPE = "kuendigung_kfz"


def letter_type(key: str) -> SimpleLetterType:
    try:
        return SIMPLE_LETTER_TYPES[key]
    except KeyError:
        raise ValueError(f"Unbekannte Briefart: {key}") from None


class Prefill(BaseModel):
    """Vorbefüllter Brief plus Angaben, was aus den Dokumenten stammt."""

    letter: SimpleLetter
    auto_fields: list[str] = Field(default_factory=list)  # z. B. "sender_name", "reference:Kennzeichen"
    notes: list[str] = Field(default_factory=list)  # verständliche Hinweise (unsicher, mehrere Werte …)


# ---------------------------------------------------------------------------
# Vorbefüllung aus dem Vorgang
# ---------------------------------------------------------------------------

_USABLE = (FieldStatus.EXTRACTED, FieldStatus.CONFIRMED, FieldStatus.MANUAL)


def _value(case: Case, key: str, notes: list[str], label: str) -> str:
    """Wert eines Feldes, wie er im Dokument steht – nur wenn die Erkennung eindeutig war."""
    fv: FieldValue | None = case.get(key)
    if fv is None:
        return ""
    if fv.status in _USABLE and (fv.value or fv.normalized_value):
        return normalize_ws(fv.value or fv.normalized_value or "")
    if fv.status == FieldStatus.CONFLICT:
        notes.append(f"{label}: In den Dokumenten stehen unterschiedliche Werte – bitte selbst eintragen.")
    elif fv.status == FieldStatus.UNCLEAR:
        notes.append(f"{label}: nicht sicher lesbar – bitte selbst eintragen.")
    return ""


_SEPARATORS = re.compile(r"\s*[·•|∙‧]\s*|\s+[-–]\s+|\s*,\s*")
_PLZ_CITY = re.compile(r"^(?:D[- ])?\d{5}\s+\S.*$")
_STOP_WORDS = {"versicherung", "versicherungen", "ag", "se", "vvag", "a.g.", "allgemeine", "deutsche", "die"}


def _name_tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß]{3,}", name.lower()) if w not in _STOP_WORDS}


def find_insurer_address(page_texts: list[str], insurer_name: str) -> list[str]:
    """Anschrift des Versicherers aus seiner Absenderzeile lesen (z. B. über dem Adressfenster).

    Gesucht wird eine einzelne Zeile der Form „Name · Straße 1 · 12345 Ort“. Sie muss mit
    „PLZ Ort“ enden und ein Namenswort des Versicherers enthalten. Sonst: leere Liste.
    """
    tokens = _name_tokens(insurer_name)
    if not tokens:
        return []
    for text in page_texts:
        for raw in text.splitlines():
            line = normalize_ws(raw)
            parts = [p for p in _SEPARATORS.split(line) if p]
            if not 2 <= len(parts) <= 4 or not _PLZ_CITY.match(parts[-1]):
                continue
            if not _name_tokens(parts[0]) & tokens:
                continue
            return parts
    return []


_SALUTATIONS = ("herrn", "herr", "frau", "familie", "firma", "eheleute")
_STREET = re.compile(r"^[A-ZÄÖÜ][\w .\-äöüßÄÖÜ]{2,40}\s\d{1,4}\s?[a-zA-Z]?$")
_PERSON = re.compile(r"^[A-ZÄÖÜ][\w.\-äöüßÄÖÜ]+(?: [A-ZÄÖÜa-zäöü][\w.\-äöüßÄÖÜ]+){1,4}$")
_POSTBOX = re.compile(r"^Postfach\s+[\d ]{2,12}$", re.IGNORECASE)


class AddressWindow(BaseModel):
    """Adressfeld eines Schreibens: an wen es ging und (darüber) wer es geschickt hat."""

    name: str = ""
    street: str = ""
    postal_city: str = ""
    sender_parts: list[str] = Field(default_factory=list)
    sender_reconstructed: bool = False  # Leerzeichen der Absenderzeile wurden ergänzt (eng gedruckt)


_GLUED_PLZ = re.compile(r"^(\d{5})([A-ZÄÖÜ].*)$")


def _unglue(part: str) -> str:
    """Fehlende Leerzeichen einer eng gedruckten Zeile ergänzen („Musterallee1“ -> „Musterallee 1“).

    Manche Texterkennung liest sehr kleine Schrift ohne Wortabstände. Ergänzt werden nur
    Leerzeichen an eindeutigen Übergängen (klein→GROSS, Buchstabe→Ziffer, PLZ→Ort); kein
    Zeichen wird geändert. Der Aufrufer weist darauf hin, dass die Schreibweise zu prüfen ist.
    """
    if " " in part:
        return part
    m = _GLUED_PLZ.match(part)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    text = re.sub(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])", " ", part)
    return re.sub(r"(?<=[a-zäöüß])(?=\d)", " ", text)


def _left_segments(page: PageText) -> list[tuple[float, str]]:
    """Zeilen der linken Seitenhälfte als (y, Text); weit entfernte Spalten werden abgetrennt."""
    out: list[tuple[float, str]] = []
    gap = max(24.0, page.width * 0.05)
    for line in group_lines(list(page.words), skew=estimate_skew(page.words)):
        segment: list[str] = []
        last_x1: float | None = None
        for word in line.words:
            if last_x1 is not None and word.bbox.x0 - last_x1 > gap:
                break  # rechte Spalte (z. B. Informationsblock) gehört nicht zur Anschrift
            segment.append(word.text)
            last_x1 = word.bbox.x1
        if segment and line.words[0].bbox.x0 < page.width * 0.45:
            out.append((line.bbox.y0, normalize_ws(" ".join(segment))))
    return out


def read_address_window(page: PageText) -> AddressWindow | None:
    """Adressfeld auf der ersten Seite lesen: „Name / Straße Nr. / PLZ Ort“ direkt untereinander.

    Fast alle Schreiben von Versicherern folgen diesem Aufbau (DIN 5008). Gelesen wird nur ein
    vollständiger Block in der oberen Seitenhälfte; alles andere bleibt unberücksichtigt.
    """
    lines = _left_segments(page)
    for i in range(2, len(lines)):
        y, town = lines[i]
        if y > page.height * 0.5:
            break
        street, name = lines[i - 1][1], lines[i - 2][1]
        if not _PLZ_CITY.match(town) or len(town) > 40 or ":" in town:
            continue
        if not (_STREET.match(street) or _POSTBOX.match(street)):
            continue
        words = name.split()
        if words and words[0].lower() in _SALUTATIONS:
            name = " ".join(words[1:])
        if not _PERSON.match(name) or any(ch.isdigit() for ch in name):
            continue
        window = AddressWindow(name=name, street=street, postal_city=town)
        # Absenderzeile des Schreibens: kleine Zeile knapp über dem Adressfeld
        for j in range(i - 3, max(-1, i - 6), -1):
            raw_parts = [x for x in _SEPARATORS.split(lines[j][1]) if x]
            parts = [_unglue(x) for x in raw_parts]
            if 2 <= len(parts) <= 4 and _PLZ_CITY.match(parts[-1]):
                window.sender_parts = parts
                window.sender_reconstructed = parts != raw_parts
                break
        return window
    return None


def prefill_letter(
    case: Case | None,
    type_key: str,
    today: date,
    *,
    known_recipient: list[str] | None = None,
) -> Prefill:
    """Brief der gewählten Art mit allem vorbefüllen, was im Vorgang eindeutig erkannt wurde."""
    ltype = letter_type(type_key)
    letter = SimpleLetter(
        letter_type=ltype.key,
        subject=ltype.subject,
        date=today.strftime("%d.%m.%Y"),
        body=ltype.body(),
        references=[Reference(label=lbl) for lbl in ltype.reference_labels],
    )
    auto: list[str] = []
    notes: list[str] = []
    if case is None:
        return Prefill(letter=letter)

    def take(attr: str, value: str) -> None:
        if value:
            setattr(letter, attr, value)
            auto.append(attr)

    first = _value(case, "policyholder.first_name", notes, "Vorname")
    last = _value(case, "policyholder.name", notes, "Name")
    name = normalize_ws(f"{first} {last}")
    take("sender_name", name)
    take("signer", name)
    street = _value(case, "policyholder.street", notes, "Straße")
    house = _value(case, "policyholder.house_number", notes, "Hausnummer")
    take("sender_street", normalize_ws(f"{street} {house}") if street else "")
    plz = _value(case, "policyholder.postal_code", notes, "Postleitzahl")
    city = _value(case, "policyholder.city", notes, "Ort")
    take("sender_postal_city", f"{plz} {city}" if plz and city else "")

    # Adressfeld des Schreibens: ergänzt, was nicht über Beschriftungen gefunden wurde
    first_pages = [d.text.pages[0] for d in case.active_documents() if d.text and d.text.pages]
    window = next((w for w in (read_address_window(pg) for pg in first_pages) if w), None)
    if window is not None:
        used = False
        if not letter.sender_name and not notes:
            take("sender_name", window.name)
            take("signer", window.name)
            used = True
        same_person = bool(_name_tokens(window.name) & _name_tokens(letter.sender_name)) or used
        if same_person and not letter.sender_street and not _POSTBOX.match(window.street):
            take("sender_street", window.street)
            used = True
        if same_person and not letter.sender_postal_city:
            take("sender_postal_city", window.postal_city)
            used = True
        if used:
            notes.append("Ihre Anschrift stammt aus dem Adressfeld des Dokuments – bitte kurz prüfen.")

    insurer = _value(case, "insurer.name", notes, "Versicherer")
    texts = [pg.text for d in case.active_documents() if d.text for pg in d.text.pages[:2]]
    lines = find_insurer_address(texts, insurer) if insurer else []
    if not lines and window is not None and window.sender_parts:
        lines = window.sender_parts  # Absenderzeile direkt über dem Adressfeld
        if window.sender_reconstructed:
            notes.append("Empfänger aus sehr klein gedruckter Zeile gelesen – Schreibweise bitte prüfen.")
    if lines:
        take("recipient", "\n".join(lines))
    elif known_recipient:
        take("recipient", "\n".join(known_recipient))
        notes.append("Empfängeranschrift aus Ihrem Adressbuch übernommen – bitte prüfen.")
    elif insurer:
        take("recipient", insurer)
        notes.append(
            "Anschrift des Versicherers nicht gefunden – bitte Straße/Postfach und PLZ Ort ergänzen."
        )

    by_label = {
        LABEL_CONTRACT: _value(case, "contract.number", notes, LABEL_CONTRACT),
        LABEL_PLATE: _value(case, "vehicle.license_plate", notes, LABEL_PLATE),
    }
    for ref in letter.references:
        value = by_label.get(ref.label, "")
        if value:
            ref.value = value
            auto.append(f"reference:{ref.label}")
    return Prefill(letter=letter, auto_fields=auto, notes=list(dict.fromkeys(notes)))


# ---------------------------------------------------------------------------
# Prüfung und Umwandlung in einen Briefplan
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\[[^\]\n]{0,120}\]")
_CLOSINGS = (
    "mit freundlichen grüßen",
    "mit freundlichem gruß",
    "freundliche grüße",
    "viele grüße",
    "hochachtungsvoll",
)


def validate_letter(letter: SimpleLetter) -> list[str]:
    """Offene Punkte in einfacher Sprache; leere Liste = der Brief kann erzeugt werden."""
    problems: list[str] = []
    if not normalize_ws(letter.sender_name):
        problems.append("Absender: Bitte Ihren Namen eintragen.")
    if not normalize_ws(letter.sender_street):
        problems.append("Absender: Bitte Straße und Hausnummer eintragen.")
    if not _PLZ_CITY.match(normalize_ws(letter.sender_postal_city)):
        problems.append("Absender: Bitte Postleitzahl und Ort eintragen (z. B. 12345 Musterstadt).")
    lines = letter.recipient_lines()
    if len(lines) < 2 or not any(_PLZ_CITY.match(x) for x in lines):
        problems.append("Empfänger: Bitte Name und Anschrift eintragen (mindestens Name sowie PLZ und Ort).")
    elif len(lines) > 6:
        problems.append("Empfänger: höchstens 6 Zeilen – sonst passt die Anschrift nicht ins Fenster.")
    if not normalize_ws(letter.subject):
        problems.append("Bitte einen Betreff eintragen.")
    if parse_german_date(letter.date) is None:
        problems.append("Datum: Bitte im Format TT.MM.JJJJ eintragen.")
    if not _text_paragraphs(letter)[1]:
        problems.append("Bitte den Brieftext eintragen.")
    if _PLACEHOLDER.search(letter.body) or _PLACEHOLDER.search(letter.subject):
        problems.append("Im Text steht noch ein Platzhalter in eckigen Klammern – bitte ersetzen.")
    if letter.letter_type != "freier_brief" and not letter.filled_references():
        problems.append(
            "Bitte mindestens eine Bezugsangabe eintragen (z. B. Versicherungsnummer), "
            "damit der Empfänger den Vertrag findet."
        )
    if len(letter.filled_references()) > MAX_REFERENCES:
        problems.append(f"Höchstens {MAX_REFERENCES} Bezugsangaben.")
    if not normalize_ws(letter.signer):
        problems.append("Bitte den Namen unter dem Brief eintragen.")
    return problems


def _text_paragraphs(letter: SimpleLetter) -> tuple[str, list[str], str]:
    """Brieftext in Anrede, Absätze und Grußformel zerlegen."""
    paragraphs = [
        normalize_ws(p) for p in re.split(r"\n\s*\n", letter.body.replace("\r\n", "\n")) if normalize_ws(p)
    ]
    salutation = closing = ""
    if paragraphs and paragraphs[0].lower().startswith(("sehr geehrte", "guten tag", "hallo", "liebe")):
        salutation = paragraphs.pop(0)
    if paragraphs and paragraphs[-1].lower().rstrip(",.") in _CLOSINGS:
        closing = paragraphs.pop()
    return salutation, paragraphs, closing


def to_plan(letter: SimpleLetter, *, mode: str = "preview") -> LetterPlan:
    """Briefplan für den DIN-5008-Renderer. ``mode="final"`` setzt einen geprüften Brief voraus."""
    ltype = letter_type(letter.letter_type)
    salutation, paragraphs, closing = _text_paragraphs(letter)
    name = normalize_ws(letter.sender_name)
    street = normalize_ws(letter.sender_street)
    town = normalize_ws(letter.sender_postal_city)
    info = [InfoLine(label=f"{r.label}:", value=r.value) for r in letter.filled_references()]
    parsed = parse_german_date(letter.date)
    info.append(
        InfoLine(label="Datum:", value=parsed.strftime("%d.%m.%Y") if parsed else normalize_ws(letter.date))
    )
    return LetterPlan(
        template_id=f"einfacher_brief_{ltype.key}",
        template_version=SIMPLE_LETTER_VERSION,
        mode="final" if mode == "final" else "preview",
        sender_lines=[x for x in (name, street, town) if x],
        return_line=" · ".join(x for x in (name, street, town) if x),
        recipient_lines=letter.recipient_lines(),
        info=info,
        subject=normalize_ws(letter.subject),
        salutation=salutation,
        blocks=[RenderedBlock(text=p) for p in paragraphs],
        closing=closing or CLOSING,
        signer=normalize_ws(letter.signer),
        title=ltype.title,
    )
