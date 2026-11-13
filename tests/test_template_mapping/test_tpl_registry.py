"""Vorlagen-Registry: Status, Import, Versionen, Pakete (nur synthetische Daten)."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest
from tpl_test_helpers import make_import_pdf, make_password_pdf, requires_originals, sha256

from conftest import ROOT, SL13065_PDF
from insurance_app.core.paths import AppPaths
from insurance_app.templates import registry as registry_module
from insurance_app.templates.registry import (
    MISSING_ORIGINAL_REASON,
    TemplateNotAvailableError,
    TemplateRegistry,
    TemplateRegistryError,
)
from insurance_app.templates.schema import FieldMapping, MappingKind, TemplateDefinition, TemplateStatus

TECIS = "tecis_bestandsuebertrag"
SL13065_ID = "swisslife_sl13065_freigabe"
SL18477_ID = "swisslife_sl18477_uebernahme"


def _resources_copy(tmp_path: Path) -> Path:
    res = tmp_path / "res"
    shutil.copytree(ROOT / "config", res / "config")
    shutil.copytree(ROOT / "assets" / "templates" / "originals", res / "assets" / "templates" / "originals")
    shutil.copytree(ROOT / "assets" / "fonts", res / "assets" / "fonts")
    return res


def _paths(tmp_path: Path, resources: Path) -> AppPaths:
    return AppPaths(resources=resources, data=tmp_path / "data", exports=tmp_path / "exports").ensure()


@requires_originals
def test_shipped_catalog_statuses(app_paths):
    reg = TemplateRegistry(app_paths)
    items = reg.list_items()
    assert [i.entry.number for i in items] == list(range(1, 10))
    statuses = [i.status for i in items]
    assert statuses.count(TemplateStatus.ACTIVE) == 2
    assert statuses.count(TemplateStatus.MISSING_ORIGINAL) == 7
    active = {i.entry.id for i in items if i.status == TemplateStatus.ACTIVE}
    assert active == {SL13065_ID, SL18477_ID}
    for item in items:
        if item.status == TemplateStatus.MISSING_ORIGINAL:
            assert item.status_reason == MISSING_ORIGINAL_REASON
            assert item.version is None and item.source is None
        else:
            assert item.version == "2024-09" and item.source == "mitgeliefert" and item.updated_at is not None
    assert reg.is_active(SL13065_ID) and not reg.is_active(TECIS)
    for item in items:
        notes = reg.verify(item.entry.id).notes
        if item.status == TemplateStatus.MISSING_ORIGINAL and getattr(item.entry, "letter_file", None):
            assert any("DIN 5008" in n for n in notes)
    report = reg.verify(SL18477_ID)
    assert report.status == TemplateStatus.ACTIVE and report.reasons == []
    assert report.permissions is not None and not report.permissions.can_modify
    assert reg.get_pdf_path(SL13065_ID) == SL13065_PDF
    assert reg.get_definition(SL18477_ID).form_number == "SL18477"
    with pytest.raises(TemplateNotAvailableError):
        reg.get_definition(TECIS)
    with pytest.raises(TemplateRegistryError):
        reg.entry("gibt_es_nicht")


@requires_originals
def test_tampered_original_is_error(tmp_path):
    res = _resources_copy(tmp_path)
    original = res / "assets" / "templates" / "originals" / SL13065_PDF.name
    original.write_bytes(original.read_bytes() + b"\n%MUSTER-Manipulation\n")
    reg = TemplateRegistry(_paths(tmp_path, res))
    report = reg.verify(SL13065_ID)
    assert report.status == TemplateStatus.ERROR
    assert any("Prüfsumme" in r for r in report.reasons)
    statuses = {i.entry.id: i.status for i in reg.list_items(refresh=True)}
    assert statuses[SL13065_ID] == TemplateStatus.ERROR and statuses[SL18477_ID] == TemplateStatus.ACTIVE


@requires_originals
def test_missing_original_file_and_broken_mapping(tmp_path):
    res = _resources_copy(tmp_path)
    (res / "assets" / "templates" / "originals" / SL13065_PDF.name).unlink()
    mapping = res / "config" / "field_schemas" / "swisslife_sl18477.json"
    mapping.write_text("{kaputt", encoding="utf-8")
    reg = TemplateRegistry(_paths(tmp_path, res))
    assert reg.verify(SL13065_ID).status == TemplateStatus.MISSING_ORIGINAL
    broken = reg.verify(SL18477_ID)
    assert broken.status == TemplateStatus.ERROR
    assert "fehlerhaft" in broken.reasons[0]


@requires_originals
def test_mapping_field_or_state_missing_is_error(tmp_path):
    res = _resources_copy(tmp_path)
    path = res / "config" / "field_schemas" / "swisslife_sl18477.json"
    data = json.loads(path.read_text("utf-8"))
    for f in data["fields"]:
        if f["pdf_field"] == "Beitragszahlung":
            f["choice_map"]["sepa"] = "7"
        if f["pdf_field"] == "Firma":
            f["pdf_field"] = "Firma (gibt es nicht)"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    reg = TemplateRegistry(_paths(tmp_path, res))
    report = reg.verify(SL18477_ID)
    assert report.status == TemplateStatus.ERROR
    text = " ".join(report.reasons)
    assert "Firma (gibt es nicht)" in text and "Auswahlzustand 7" in text


@requires_originals
def test_unconfirmed_shipped_mapping_needs_review(tmp_path):
    res = _resources_copy(tmp_path)
    path = res / "config" / "field_schemas" / "swisslife_sl13065.json"
    data = json.loads(path.read_text("utf-8"))
    data["mapping_confirmed"] = False
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    report = TemplateRegistry(_paths(tmp_path, res)).verify(SL13065_ID)
    assert report.status == TemplateStatus.NEEDS_REVIEW


def test_import_original_creates_draft_then_confirm(app_paths, tmp_path):
    source = make_import_pdf(tmp_path / "MUSTER_tecis.pdf")
    before = sha256(source)
    reg = TemplateRegistry(app_paths)
    draft = reg.import_original(TECIS, source, version="2026-11")
    assert sha256(source) == before, "Quelle darf nicht verändert werden"
    definition = draft.definition
    assert definition is not None and not definition.mapping_confirmed
    assert definition.catalog_id == TECIS and definition.version == "2026-11"
    assert definition.template_id == "tecis_bestandsuebertrag_2026-11"
    assert definition.insurer_code == "TECIS" and definition.short_title == "Bestandsuebertrag"
    assert definition.sha256 == before
    stored = app_paths.user_templates_dir / TECIS / "2026-11" / "original.pdf"
    assert stored.is_file() and sha256(stored) == before
    assert not stored.stat().st_mode & stat.S_IWUSR, "Kopie muss schreibgeschützt sein"

    report = reg.verify(TECIS)
    assert report.status == TemplateStatus.NEEDS_REVIEW and report.source == "importiert"
    item = next(i for i in reg.list_items(refresh=True) if i.entry.id == TECIS)
    assert (
        item.status == TemplateStatus.NEEDS_REVIEW
        and item.version == "2026-11"
        and item.source == "importiert"
    )
    assert item.updated_at is not None

    versions = reg.list_versions(TECIS)
    assert [v.version for v in versions] == ["2026-11"] and not versions[0].confirmed

    reg.save_mapping(TECIS, definition, confirmed=True)
    assert reg.verify(TECIS).status == TemplateStatus.ACTIVE
    assert reg.is_active(TECIS)
    assert reg.get_definition(TECIS).mapping_confirmed
    assert reg.get_pdf_path(TECIS) == stored
    assert reg.list_versions(TECIS)[0].confirmed
    found = reg.find_by_template_id(definition.template_id)
    assert found is not None and found[0] == TECIS

    # Bestätigung zurücknehmen -> wieder "Zu prüfen"
    reg.save_mapping(TECIS, definition, confirmed=False)
    assert reg.verify(TECIS).status == TemplateStatus.NEEDS_REVIEW


def test_import_original_default_version_is_unique(app_paths, tmp_path):
    source = make_import_pdf(tmp_path / "MUSTER.pdf")
    reg = TemplateRegistry(app_paths)
    a = reg.import_original(TECIS, source).definition.version
    b = reg.import_original(TECIS, source).definition.version
    assert a != b and b.startswith(a) and b.endswith("-2")
    with pytest.raises(TemplateRegistryError):
        reg.import_original(TECIS, source, version=a)


def test_import_original_rejects_bad_input(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    text = tmp_path / "kein.pdf"
    text.write_text("kein PDF", encoding="utf-8")
    with pytest.raises(TemplateRegistryError) as exc:
        reg.import_original(TECIS, text)
    assert "keine PDF" in exc.value.user_message
    with pytest.raises(TemplateRegistryError):
        reg.import_original(TECIS, make_password_pdf(tmp_path / "pw.pdf"))
    with pytest.raises(TemplateRegistryError):
        reg.import_original(TECIS, tmp_path / "fehlt.pdf")
    with pytest.raises(TemplateRegistryError):
        reg.import_original(TECIS, make_import_pdf(tmp_path / "ok.pdf"), version="../x")
    with pytest.raises(TemplateRegistryError):
        reg.import_original("unbekannt", make_import_pdf(tmp_path / "ok2.pdf"))
    assert not (app_paths.user_templates_dir / TECIS).exists() or not any(
        (app_paths.user_templates_dir / TECIS).iterdir()
    )


def test_save_mapping_validations(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    draft = reg.import_original(TECIS, make_import_pdf(tmp_path / "MUSTER.pdf"), version="1")
    definition = draft.definition
    with pytest.raises(TemplateRegistryError):
        reg.save_mapping("tecis_haftungsfreistellung_ak", definition, confirmed=True)
    with pytest.raises(TemplateRegistryError):
        reg.save_mapping(TECIS, definition.model_copy(update={"sha256": "1" * 64}), confirmed=True)
    with pytest.raises(TemplateRegistryError):
        reg.save_mapping(TECIS, definition.model_copy(update={"version": "99"}), confirmed=True)


def test_confirmed_mapping_with_missing_field_is_error(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    definition = reg.import_original(TECIS, make_import_pdf(tmp_path / "MUSTER.pdf"), version="1").definition
    bad = definition.model_copy(
        update={
            "fields": [
                *definition.fields,
                FieldMapping(
                    pdf_field="Fehlt", label="Fehlt", kind=MappingKind.TEXT, source="contract.number"
                ),
            ]
        }
    )
    reg.save_mapping(TECIS, bad, confirmed=True)
    report = reg.verify(TECIS)
    assert report.status == TemplateStatus.ERROR and any("Fehlt" in r for r in report.reasons)


@requires_originals
def test_save_mapping_for_shipped_version_creates_user_copy(app_paths):
    reg = TemplateRegistry(app_paths)
    shipped = reg.get_definition(SL13065_ID)
    reg.save_mapping(SL13065_ID, shipped, confirmed=True)
    report = reg.verify(SL13065_ID)
    assert report.status == TemplateStatus.ACTIVE and report.source == "importiert"
    assert reg.get_pdf_path(SL13065_ID).parent == app_paths.user_templates_dir / SL13065_ID / "2024-09"
    assert sha256(SL13065_PDF) == shipped.sha256  # mitgelieferte Datei unverändert


@requires_originals
def test_package_roundtrip_shipped(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    zip_path = reg.export_package(SL13065_ID, tmp_path / "pakete" / "sl13065.zip")
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == ["manifest.json", "mapping.json", "original.pdf"]
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["catalog_id"] == SL13065_ID and manifest["version"] == "2024-09"
        assert manifest["sha256"] == sha256(SL13065_PDF) and "created" in manifest
        assert zf.read("original.pdf") == SL13065_PDF.read_bytes()
        mapping = TemplateDefinition.model_validate_json(zf.read("mapping.json"))
        assert mapping.pdf_file == "original.pdf"

    other = TemplateRegistry(AppPaths.for_testing(tmp_path / "zweiter"))
    info = other.import_package(zip_path)
    assert info.catalog_id == SL13065_ID and info.version == "2024-09" and info.source == "importiert"
    assert not info.confirmed and info.pdf_path.is_file() and info.sha256 == manifest["sha256"]
    # mitgelieferte, bestätigte Version bleibt aktiv
    assert other.verify(SL13065_ID).status == TemplateStatus.ACTIVE
    # erneuter Import desselben Pakets ist idempotent
    assert other.import_package(zip_path).pdf_path == info.pdf_path


@requires_originals
def test_package_roundtrip_sl18477_keeps_conditions(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    zip_path = reg.export_package(SL18477_ID, tmp_path / "sl18477.zip")
    other = TemplateRegistry(AppPaths.for_testing(tmp_path / "zweiter"))
    info = other.import_package(zip_path)
    imported = TemplateDefinition.model_validate_json(info.mapping_path.read_text("utf-8"))
    shipped = reg.get_definition(SL18477_ID)
    assert imported.model_dump(exclude={"pdf_file", "mapping_confirmed"}) == shipped.model_dump(
        exclude={"pdf_file", "mapping_confirmed"}
    )
    iban = imported.mapping("IBAN")
    assert iban.conditions[0].in_ == ["sepa", "transfer"]
    # Bestätigen der importierten Version macht sie aktiv
    other.save_mapping(SL18477_ID, imported, confirmed=True)
    report = other.verify(SL18477_ID)
    assert report.status == TemplateStatus.ACTIVE and report.source == "importiert"


def test_package_roundtrip_imported_requires_review(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    definition = reg.import_original(TECIS, make_import_pdf(tmp_path / "MUSTER.pdf"), version="3").definition
    reg.save_mapping(TECIS, definition, confirmed=True)
    zip_path = reg.export_package(TECIS, tmp_path / "tecis.zip")
    other = TemplateRegistry(AppPaths.for_testing(tmp_path / "zweiter"))
    info = other.import_package(zip_path)
    assert info.version == "3" and not info.confirmed
    assert other.verify(TECIS).status == TemplateStatus.NEEDS_REVIEW
    imported = other.get_definition(TECIS)
    other.save_mapping(TECIS, imported, confirmed=True)
    assert other.verify(TECIS).status == TemplateStatus.ACTIVE


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.fixture()
def package_members(app_paths, tmp_path) -> dict[str, bytes]:
    reg = TemplateRegistry(app_paths)
    reg.import_original(TECIS, make_import_pdf(tmp_path / "MUSTER.pdf"), version="5")
    zip_path = reg.export_package(TECIS, tmp_path / "ok.zip", version="5")
    with zipfile.ZipFile(zip_path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


@pytest.mark.parametrize(
    "evil", ["../evil.json", "/absolut.pdf", "a/../../b.pdf", "C:/win.pdf", "ordner\\datei.pdf"]
)
def test_import_package_rejects_unsafe_paths(tmp_path, package_members, evil):
    zip_path = _write_zip(tmp_path / "evil.zip", {**package_members, evil: b"x"})
    reg = TemplateRegistry(AppPaths.for_testing(tmp_path / "ziel"))
    with pytest.raises(TemplateRegistryError) as exc:
        reg.import_package(zip_path)
    assert "unzulässige" in exc.value.user_message or "unerwartet" in exc.value.user_message
    assert not (tmp_path / "evil.json").exists()
    assert not (tmp_path / "ziel" / "user_data" / "templates" / TECIS).exists()


def test_import_package_rejects_oversized_member(tmp_path, package_members, monkeypatch):
    monkeypatch.setattr(registry_module, "MAX_MEMBER_BYTES", 1000)
    zip_path = _write_zip(tmp_path / "gross.zip", package_members)
    with pytest.raises(TemplateRegistryError) as exc:
        TemplateRegistry(AppPaths.for_testing(tmp_path / "ziel")).import_package(zip_path)
    assert "zu groß" in exc.value.user_message


def test_import_package_rejects_inconsistent_content(tmp_path, package_members):
    reg = TemplateRegistry(AppPaths.for_testing(tmp_path / "ziel"))
    manifest = json.loads(package_members["manifest.json"])
    cases = {
        "sha": {**package_members, "manifest.json": json.dumps({**manifest, "sha256": "a" * 64}).encode()},
        "catalog": {
            **package_members,
            "manifest.json": json.dumps({**manifest, "catalog_id": "unbekannt"}).encode(),
        },
        "version": {**package_members, "manifest.json": json.dumps({**manifest, "version": "../x"}).encode()},
        "pdf": {**package_members, "original.pdf": package_members["original.pdf"] + b"%x"},
        "missing": {k: v for k, v in package_members.items() if k != "mapping.json"},
        "mapping": {**package_members, "mapping.json": b"{}"},
    }
    for name, members in cases.items():
        with pytest.raises(TemplateRegistryError):
            reg.import_package(_write_zip(tmp_path / f"{name}.zip", members))
    not_zip = tmp_path / "kein.zip"
    not_zip.write_text("kein zip", encoding="utf-8")
    with pytest.raises(TemplateRegistryError):
        reg.import_package(not_zip)


def test_import_package_conflicting_version(tmp_path, package_members, app_paths):
    reg = TemplateRegistry(AppPaths.for_testing(tmp_path / "ziel"))
    reg.import_package(_write_zip(tmp_path / "a.zip", package_members))
    other_pdf = make_import_pdf(tmp_path / "anders.pdf")
    data = other_pdf.read_bytes() + b"%anders\n"
    mapping = json.loads(package_members["mapping.json"])
    manifest = json.loads(package_members["manifest.json"])
    digest = hashlib.sha256(data).hexdigest()
    mapping["sha256"] = manifest["sha256"] = digest
    members = {
        "manifest.json": json.dumps(manifest).encode(),
        "mapping.json": json.dumps(mapping).encode(),
        "original.pdf": data,
    }
    with pytest.raises(TemplateRegistryError) as exc:
        reg.import_package(_write_zip(tmp_path / "b.zip", members))
    assert "anderem Inhalt" in exc.value.user_message


def test_export_package_without_original(app_paths, tmp_path):
    with pytest.raises(TemplateNotAvailableError):
        TemplateRegistry(app_paths).export_package(TECIS, tmp_path / "x.zip")


# ---------------------------------------------------------------------------
# SEC-07: beschädigter Deflate-Datenstrom -> verständliche Meldung statt Absturz
# ---------------------------------------------------------------------------


def test_sec_07_corrupt_deflate_stream_gives_registry_error(tmp_path, package_members):
    import struct
    import zlib

    zip_path = tmp_path / "kaputt.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in package_members.items():
            zf.writestr(name, data)
    raw = bytearray(zip_path.read_bytes())
    with zipfile.ZipFile(zip_path) as zf:
        offset = zf.getinfo("original.pdf").header_offset
    name_len, extra_len = struct.unpack("<HH", raw[offset + 26 : offset + 30])
    start = offset + 30 + name_len + extra_len
    for i in range(start + 10, start + 200):
        raw[i] ^= 0xFF
    zip_path.write_bytes(bytes(raw))
    with zipfile.ZipFile(zip_path) as zf, pytest.raises(zlib.error):
        zf.read("original.pdf")  # Voraussetzung: genau dieser Fehlerfall
    reg = TemplateRegistry(AppPaths.for_testing(tmp_path / "ziel"))
    with pytest.raises(TemplateRegistryError) as exc:
        reg.import_package(zip_path)
    assert exc.value.user_message == "Das Vorlagenpaket ist beschädigt."
    assert not (tmp_path / "ziel" / "user_data" / "templates" / TECIS).exists()


@pytest.mark.parametrize("error", [EOFError("abgeschnitten"), ValueError("kaputt")])
def test_sec_07_other_decompression_errors_are_mapped(tmp_path, package_members, monkeypatch, error):
    zip_path = _write_zip(tmp_path / "p.zip", package_members)

    def broken_open(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(zipfile.ZipFile, "open", broken_open)
    with pytest.raises(TemplateRegistryError) as exc:
        TemplateRegistry(AppPaths.for_testing(tmp_path / "ziel")).import_package(zip_path)
    assert exc.value.user_message == "Das Vorlagenpaket ist beschädigt."
