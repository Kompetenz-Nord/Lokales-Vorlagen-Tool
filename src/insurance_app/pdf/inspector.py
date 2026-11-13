"""AcroForm-Inspektion: Felder, Zustände, Formate und PDF-Berechtigungen.

Liest eine PDF nur (keine Änderungen). Ergebnis ist ein ``FormInspection``-Modell,
das Vorlagen-Registry, Werteauflösung, Befüllung und Qualitätskontrolle gemeinsam
nutzen.

Koordinaten: Seitenkoordinaten mit Ursprung oben links in PDF-Punkten
(wie ``core.models.BoundingBox``). Seitennummern sind 1-basiert.
"""

from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FieldType",
    "FormInspection",
    "PdfFieldInfo",
    "PdfOpenError",
    "PdfPermissions",
    "WidgetInfo",
    "field_dict_xref",
    "format_af_number",
    "inspect_document",
    "inspect_pdf_form",
    "open_pdf",
    "option_label_near",
    "parse_number_format",
    "pdf_name",
    "read_permissions",
    "widget_states",
]

log = logging.getLogger(__name__)

FieldType = Literal["text", "checkbox", "radio", "signature", "button", "combobox", "listbox"]

_WIDGET_TYPES: dict[int, FieldType] = {
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "listbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
}

# Feld-Flags (PDF 32000-1, Tabelle 221/226/228)
FF_READ_ONLY = 1
FF_REQUIRED = 1 << 1
FF_MULTILINE = 1 << 12
FF_COMB = 1 << 24


class PdfOpenError(Exception):
    """Die PDF kann nicht geöffnet werden (beschädigt, kein PDF, Passwort falsch)."""

    def __init__(self, message: str, *, needs_password: bool = False) -> None:
        super().__init__(message)
        self.user_message = message
        self.needs_password = needs_password


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfPermissions(_Model):
    """Verschlüsselung und Rechte laut PDF-Sicherheitseinstellungen."""

    encrypted: bool
    needs_password: bool
    can_fill_forms: bool
    can_modify: bool
    can_annotate: bool
    can_assemble: bool
    can_print: bool
    can_copy: bool
    encryption_method: str | None = None  # z. B. "Standard V4 R4 128-bit AES"
    raw: int = -1  # Rechte-Bitfeld (P-Eintrag), -1 = alle Rechte

    @property
    def can_flatten(self) -> bool:
        """Darf die Seite verändert werden (Formular fixieren, Overlay)?"""
        return (not self.encrypted) or self.can_modify


class WidgetInfo(_Model):
    page: int  # 1-basiert
    rect: list[float]  # [x0, y0, x1, y1] Seitenkoordinaten oben links, PDF-Punkte
    on_state: str | None = None  # Ein-Zustand bei Checkbox/Radio (z. B. "Ja", "0")
    xref: int
    states: list[str] = Field(default_factory=list)  # alle Darstellungszustände (/AP /N, ohne "Off")
    current_state: str | None = None  # /AS (Checkbox/Radio)

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


class PdfFieldInfo(_Model):
    name: str  # vollqualifizierter Feldname
    field_type: FieldType
    widgets: list[WidgetInfo] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    max_len: int | None = None
    font: str | None = None  # Schrift laut DA, z. B. "Arial"
    font_size: float | None = None  # Schriftgröße laut DA (0 = automatisch)
    align: Literal["left", "center", "right"] = "left"
    flags: int = 0
    read_only: bool = False
    multiline: bool = False
    comb: bool = False
    number_format: dict[str, Any] | None = None  # AFNumber_Format: decimals, sep_style, …
    choice_values: list[str] = Field(default_factory=list)  # Ein-Zustände bzw. Auswahlwerte
    value: str | None = None
    has_value: bool = False
    signed: bool = False  # nur Signaturfelder: enthält bereits eine Signatur
    xref: int | None = None  # Objekt des Felds (bei Optionsgruppen: Elternobjekt)

    @property
    def on_states(self) -> list[str]:
        """Alle Ein-Zustände der Widgets (Checkbox/Radio)."""
        return [w.on_state for w in self.widgets if w.on_state]


class FormInspection(_Model):
    page_count: int
    permissions: PdfPermissions
    is_xfa: bool = False
    signed_signature_fields: list[str] = Field(default_factory=list)
    fields: list[PdfFieldInfo] = Field(default_factory=list)
    page_sizes: list[tuple[float, float]] = Field(default_factory=list)
    has_acroform: bool = False

    def field(self, name: str) -> PdfFieldInfo | None:
        return next((f for f in self.fields if f.name == name), None)

    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}

    def fields_of_type(self, *types: FieldType) -> list[PdfFieldInfo]:
        return [f for f in self.fields if f.field_type in types]


# ---------------------------------------------------------------------------
# Öffnen und Berechtigungen
# ---------------------------------------------------------------------------


def open_pdf(path: Path, password: str | None = None) -> pymupdf.Document:
    """PDF öffnen (nur PDF). Wirft ``PdfOpenError`` mit verständlicher Meldung."""
    path = Path(path)
    if not path.is_file():
        raise PdfOpenError("Die PDF-Datei wurde nicht gefunden.")
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:  # pymupdf wirft verschiedene Fehlertypen
        log.info("PDF konnte nicht geöffnet werden (%s)", type(exc).__name__)
        raise PdfOpenError("Die Datei ist keine lesbare PDF oder beschädigt.") from exc
    if not doc.is_pdf:
        doc.close()
        raise PdfOpenError("Die Datei ist keine PDF.")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PdfOpenError(
                "Die PDF ist mit einem Passwort geschützt. Bitte das Passwort eingeben.",
                needs_password=True,
            )
    return doc


def _is_encrypted(doc: pymupdf.Document) -> bool:
    try:
        kind, _ = doc.xref_get_key(-1, "Encrypt")
    except Exception:  # pragma: no cover - defensive
        kind = "null"
    return kind != "null" or bool(doc.metadata and doc.metadata.get("encryption"))


def read_permissions(doc: pymupdf.Document, needs_password: bool = False) -> PdfPermissions:
    """Rechte eines geöffneten Dokuments auslesen."""
    encrypted = _is_encrypted(doc)
    perm = int(doc.permissions) if encrypted else -1
    method = (doc.metadata or {}).get("encryption") if encrypted else None

    def has(bit: int) -> bool:
        return bool(perm & bit)

    can_annotate = has(pymupdf.PDF_PERM_ANNOTATE)
    return PdfPermissions(
        encrypted=encrypted,
        needs_password=needs_password,
        # Formularfelder ausfüllen: Bit 9 (FORM) oder Bit 6 (ANNOTATE) erlaubt das Ausfüllen
        can_fill_forms=has(pymupdf.PDF_PERM_FORM) or can_annotate,
        can_modify=has(pymupdf.PDF_PERM_MODIFY),
        can_annotate=can_annotate,
        can_assemble=has(pymupdf.PDF_PERM_ASSEMBLE),
        can_print=has(pymupdf.PDF_PERM_PRINT),
        can_copy=has(pymupdf.PDF_PERM_COPY),
        encryption_method=method or None,
        raw=perm,
    )


# ---------------------------------------------------------------------------
# PDF-Hilfsfunktionen (auch von Filler und QA genutzt)
# ---------------------------------------------------------------------------


def _ref(value: str) -> int | None:
    m = re.match(r"\s*(\d+)\s+\d+\s+R", value)
    return int(m.group(1)) if m else None


def field_dict_xref(doc: pymupdf.Document, widget_xref: int) -> int:
    """Objekt des (terminalen) Felds zu einem Widget.

    Widgets ohne eigenen ``/T``-Eintrag (z. B. Kinder einer Optionsgruppe) gehören
    zum Elternfeld; dort stehen ``/V`` und ``/FT``.
    """
    xref = widget_xref
    for _ in range(32):  # Schutz gegen zyklische Strukturen
        kind, _ = doc.xref_get_key(xref, "T")
        if kind != "null":
            return xref
        pkind, pval = doc.xref_get_key(xref, "Parent")
        parent = _ref(pval) if pkind == "xref" else None
        if parent is None:
            return xref
        xref = parent
    return xref


def _inherited(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    current: int | None = xref
    for _ in range(32):
        if current is None:
            break
        kind, val = doc.xref_get_key(current, key)
        if kind != "null":
            return kind, val
        pkind, pval = doc.xref_get_key(current, "Parent")
        current = _ref(pval) if pkind == "xref" else None
    return "null", "null"


def _acroform_xref(doc: pymupdf.Document) -> int | None:
    cat = doc.pdf_catalog()
    kind, val = doc.xref_get_key(cat, "AcroForm")
    if kind == "xref":
        return _ref(val)
    if kind == "dict":
        return cat  # eingebettetes Dictionary: Schlüssel über "AcroForm/..." lesen
    return None


def _acroform_key(doc: pymupdf.Document, key: str) -> tuple[str, str]:
    cat = doc.pdf_catalog()
    kind, val = doc.xref_get_key(cat, "AcroForm")
    if kind == "xref":
        ref = _ref(val)
        return doc.xref_get_key(ref, key) if ref else ("null", "null")
    if kind == "dict":
        return doc.xref_get_key(cat, f"AcroForm/{key}")
    return "null", "null"


def _decode_name(raw: str) -> str:
    name = raw[1:] if raw.startswith("/") else raw
    if "#" not in name:
        return name
    out = bytearray()
    i = 0
    data = name.encode("latin-1", errors="replace")
    while i < len(data):
        if data[i : i + 1] == b"#" and i + 2 < len(data) + 1:
            try:
                out.append(int(data[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(data[i])
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1")


_NAME_REGULAR = re.compile(r"[A-Za-z0-9_.+\-*!'\"&;:,?@^`|~]")


def pdf_name(state: str) -> str:
    """PDF-Namensobjekt für einen Zustand, z. B. ``"Ja"`` -> ``"/Ja"`` (mit #-Escaping)."""
    if not state:
        raise ValueError("Leerer Zustandsname")
    out: list[str] = []
    for ch in state:
        if _NAME_REGULAR.fullmatch(ch):
            out.append(ch)
        else:
            out.extend(f"#{b:02X}" for b in ch.encode("utf-8"))
    return "/" + "".join(out)


def widget_states(doc: pymupdf.Document, widget_xref: int) -> list[str]:
    """Darstellungszustände eines Widgets laut ``/AP /N`` (ohne ``Off``)."""
    kind, val = doc.xref_get_key(widget_xref, "AP/N")
    keys: list[str] = []
    if kind == "dict":
        keys = _ap_dict_keys(val)
    elif kind == "xref":
        ref = _ref(val)
        if ref is not None and not doc.xref_is_stream(ref):
            keys = _ap_dict_keys(doc.xref_object(ref, compressed=True))
    return [k for k in keys if k != "Off"]


def _ap_dict_keys(raw: str) -> list[str]:
    # Paare "/Name <wert>" – Werte sind Referenzen "n 0 R" oder Inline-Streams nicht möglich.
    text = raw.strip()
    if text.startswith("<<"):
        text = text[2:]
    if text.endswith(">>"):
        text = text[:-2]
    keys: list[str] = []
    for m in re.finditer(r"/([^\s/<>\[\]()]+)\s*(\d+\s+\d+\s+R|<<.*?>>|/[^\s/<>]+)?", text):
        keys.append(_decode_name("/" + m.group(1)))
    return keys


_AF_NUMBER = re.compile(
    r"AFNumber_Format\s*\(\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*(true|false)\s*\)"
)


def parse_number_format(js: str | None) -> dict[str, Any] | None:
    """``AFNumber_Format(nDec, sepStyle, negStyle, currStyle, strCurrency, bCurrencyPrepend)`` lesen."""
    if not js:
        return None
    m = _AF_NUMBER.search(js)
    if not m:
        return None
    return {
        "decimals": max(0, int(m.group(1))),
        "sep_style": int(m.group(2)),
        "neg_style": int(m.group(3)),
        "curr_style": int(m.group(4)),
        "currency": m.group(5),
        "currency_prepend": m.group(6) == "true",
    }


_SEPARATORS = {
    0: (",", "."),  # 1,234.56
    1: ("", "."),  # 1234.56
    2: (".", ","),  # 1.234,56
    3: ("", ","),  # 1234,56
    4: ("'", "."),  # 1'234.56
}


def format_af_number(value: str | Decimal, decimals: int = 2, sep_style: int = 2) -> str:
    """Zahl wie Acrobat ``AFNumber_Format`` darstellen (ohne Währung).

    ``sep_style`` 2 ergibt das deutsche Format ``1.234,56``.
    """
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Kein gültiger Zahlenwert") from exc
    if not number.is_finite():
        raise ValueError("Kein gültiger Zahlenwert")
    decimals = max(0, int(decimals))
    quant = Decimal(1).scaleb(-decimals)
    number = number.quantize(quant, rounding=ROUND_HALF_UP)
    thousands, point = _SEPARATORS.get(sep_style, _SEPARATORS[2])
    sign = "-" if number < 0 else ""
    digits = f"{abs(number):.{decimals}f}"
    int_part, _, frac = digits.partition(".")
    if thousands:
        groups: list[str] = []
        while len(int_part) > 3:
            groups.insert(0, int_part[-3:])
            int_part = int_part[:-3]
        groups.insert(0, int_part)
        int_part = thousands.join(groups)
    return f"{sign}{int_part}{point}{frac}" if decimals else f"{sign}{int_part}"


# ---------------------------------------------------------------------------
# Inspektion
# ---------------------------------------------------------------------------

_ALIGN: dict[int, Literal["left", "center", "right"]] = {0: "left", 1: "center", 2: "right"}


def _read_value(doc: pymupdf.Document, field_xref: int) -> str | None:
    kind, val = doc.xref_get_key(field_xref, "V")
    if kind == "null":
        return None
    if kind == "name":
        return _decode_name(val)
    if kind == "string":
        return val
    if kind == "array":
        return val  # Mehrfachauswahl (Listenfeld) – roh
    if kind == "xref":
        return f"<ref {val}>"  # z. B. Signaturwert
    return val


def _choice_options(widget: pymupdf.Widget) -> list[str]:
    values: list[str] = []
    for item in widget.choice_values or []:
        if isinstance(item, (list, tuple)) and item:
            values.append(str(item[0]))
        else:
            values.append(str(item))
    return values


def inspect_document(doc: pymupdf.Document) -> FormInspection:
    """Inspektion eines bereits geöffneten (entsperrten) Dokuments."""
    permissions = read_permissions(doc)
    page_sizes = [(round(p.rect.width, 3), round(p.rect.height, 3)) for p in doc]
    is_xfa = _acroform_key(doc, "XFA")[0] != "null"
    has_acroform = _acroform_xref(doc) is not None
    q_default_kind, q_default = _acroform_key(doc, "Q")
    default_q = int(q_default) if q_default_kind == "int" else 0

    fields: dict[str, PdfFieldInfo] = {}
    for page in doc:
        for widget in page.widgets() or []:
            name = widget.field_name or ""
            if not name:
                continue
            ftype = _WIDGET_TYPES.get(widget.field_type, "button")
            fx = field_dict_xref(doc, widget.xref)
            states = widget_states(doc, widget.xref) if ftype in ("checkbox", "radio") else []
            on_state: str | None = None
            current: str | None = None
            if ftype in ("checkbox", "radio"):
                on_state = states[0] if states else None
                as_kind, as_val = doc.xref_get_key(widget.xref, "AS")
                current = _decode_name(as_val) if as_kind == "name" else None
            r = widget.rect
            winfo = WidgetInfo(
                page=page.number + 1,
                rect=[round(r.x0, 3), round(r.y0, 3), round(r.x1, 3), round(r.y1, 3)],
                on_state=on_state,
                xref=widget.xref,
                states=states,
                current_state=current,
            )
            info = fields.get(name)
            if info is None:
                flags = int(widget.field_flags or 0)
                q_kind, q_val = _inherited(doc, widget.xref, "Q")
                q = int(q_val) if q_kind == "int" else default_q
                value = _read_value(doc, fx)
                if ftype == "text" and value is None:
                    value = widget.field_value or None
                max_len = int(widget.text_maxlen) if widget.text_maxlen else None
                font_size = float(widget.text_fontsize) if widget.text_fontsize is not None else None
                choice_values: list[str] = []
                if ftype in ("combobox", "listbox"):
                    choice_values = _choice_options(widget)
                signed = ftype == "signature" and value is not None
                info = PdfFieldInfo(
                    name=name,
                    field_type=ftype,
                    max_len=max_len if ftype == "text" else None,
                    font=widget.text_font or None,
                    font_size=font_size,
                    align=_ALIGN.get(q, "left"),
                    flags=flags,
                    read_only=bool(flags & FF_READ_ONLY),
                    multiline=ftype == "text" and bool(flags & FF_MULTILINE),
                    comb=ftype == "text" and bool(flags & FF_COMB),
                    number_format=parse_number_format(widget.script_format) if ftype == "text" else None,
                    choice_values=choice_values,
                    value=value,
                    has_value=value not in (None, "", "Off"),
                    signed=signed,
                    xref=fx,
                )
                fields[name] = info
            info.widgets.append(winfo)
            if winfo.page not in info.pages:
                info.pages.append(winfo.page)
            if ftype in ("checkbox", "radio") and on_state and on_state not in info.choice_values:
                info.choice_values.append(on_state)

    signed_fields = [f.name for f in fields.values() if f.signed]
    return FormInspection(
        page_count=doc.page_count,
        permissions=permissions,
        is_xfa=is_xfa,
        signed_signature_fields=signed_fields,
        fields=list(fields.values()),
        page_sizes=page_sizes,
        has_acroform=has_acroform,
    )


def inspect_pdf_form(path: Path, password: str | None = None) -> FormInspection:
    """PDF-Formular inspizieren.

    Ist die PDF passwortgeschützt und kein (richtiges) Passwort angegeben, wird
    eine Inspektion ohne Felder mit ``permissions.needs_password=True``
    zurückgegeben. Nicht lesbare Dateien führen zu ``PdfOpenError``.
    """
    try:
        doc = open_pdf(path, password)
    except PdfOpenError as exc:
        if not exc.needs_password:
            raise
        return FormInspection(
            page_count=0,
            permissions=PdfPermissions(
                encrypted=True,
                needs_password=True,
                can_fill_forms=False,
                can_modify=False,
                can_annotate=False,
                can_assemble=False,
                can_print=False,
                can_copy=False,
                raw=0,
            ),
        )
    try:
        return inspect_document(doc)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Beschriftung neben einem Widget (Vorlagen-Studio, Tests)
# ---------------------------------------------------------------------------


def _text_lines(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    lines: list[tuple[pymupdf.Rect, str]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if text:
                lines.append((pymupdf.Rect(line["bbox"]), text))
    return lines


def _join_lines(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if not out:
            out = part
        elif out.endswith("-") and part[:1].islower():
            out = out[:-1] + part
        else:
            out = f"{out} {part}"
    return " ".join(out.split())


def option_label_near(
    path: Path,
    field_name: str,
    state: str | None = None,
    lines: int = 1,
    password: str | None = None,
) -> str:
    """Text rechts neben dem Widget (gleiche Zeile, optional Folgezeilen).

    ``state`` wählt bei Optionsgruppen das Widget mit diesem Ein-Zustand; bei
    ``None`` wird das erste Widget des Felds verwendet. Leerer Text, wenn das
    Feld oder der Zustand nicht existiert oder keine Beschriftung gefunden wird.
    """
    doc = open_pdf(path, password)
    try:
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_name != field_name:
                    continue
                if state is not None:
                    states = widget_states(doc, widget.xref)
                    if state not in states:
                        continue
                return _label_right_of(page, widget.rect, lines)
        return ""
    finally:
        doc.close()


def _label_right_of(page: pymupdf.Page, rect: pymupdf.Rect, lines: int) -> str:
    all_lines = _text_lines(page)
    cy0, cy1 = rect.y0 - 2, rect.y1 + 2
    same = [
        (r, t)
        for r, t in all_lines
        if r.x0 >= rect.x1 - 2 and cy0 <= (r.y0 + r.y1) / 2 <= cy1 and r.x0 - rect.x1 < 300
    ]
    if not same:
        return ""
    same.sort(key=lambda item: item[0].x0)
    first_rect, first_text = same[0]
    parts = [first_text]
    last = first_rect
    if lines > 1:
        below = sorted(
            (
                (r, t)
                for r, t in all_lines
                if abs(r.x0 - first_rect.x0) <= 3 and r.y0 > first_rect.y0 + 1 and r is not first_rect
            ),
            key=lambda item: item[0].y0,
        )
        for r, t in below:
            if len(parts) >= lines:
                break
            if r.y0 - last.y1 > max(6.0, last.height):
                break
            parts.append(t)
            last = r
    return _join_lines(parts)
