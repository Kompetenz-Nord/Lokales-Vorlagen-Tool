"""Registry: selbst importierte Vorlagenversion entfernen (nur synthetische Daten)."""

from __future__ import annotations

import pytest
from tpl_test_helpers import make_import_pdf, requires_originals, sha256

from conftest import SL13065_PDF
from insurance_app.templates.registry import (
    TemplateNotAvailableError,
    TemplateRegistry,
    TemplateRegistryError,
)
from insurance_app.templates.schema import TemplateStatus

TECIS = "tecis_bestandsuebertrag"
SL13065_ID = "swisslife_sl13065_freigabe"


def test_remove_imported_version_deletes_only_that_version(app_paths, tmp_path):
    source = make_import_pdf(tmp_path / "MUSTER_tecis.pdf")
    before = sha256(source)
    reg = TemplateRegistry(app_paths)
    reg.import_original(TECIS, source, version="2026-10")
    draft = reg.import_original(TECIS, source, version="2026-11")
    reg.save_mapping(TECIS, draft.definition, confirmed=True)
    assert reg.verify(TECIS).status == TemplateStatus.ACTIVE

    removed = reg.remove_imported_version(TECIS, "2026-11")
    assert removed.version == "2026-11" and removed.source == "importiert"
    assert not (app_paths.user_templates_dir / TECIS / "2026-11").exists()  # auch schreibgeschützte Kopie
    assert [v.version for v in reg.list_versions(TECIS)] == ["2026-10"]
    assert reg.verify(TECIS).status == TemplateStatus.NEEDS_REVIEW  # Status-Cache wurde verworfen
    assert sha256(source) == before, "Quelle darf nicht verändert werden"

    reg.remove_imported_version(TECIS, "2026-10")
    assert reg.list_versions(TECIS) == []
    assert reg.verify(TECIS).status == TemplateStatus.MISSING_ORIGINAL
    assert not (app_paths.user_templates_dir / TECIS).exists()  # leerer Ordner aufgeräumt


def test_remove_imported_version_rejects_unknown_and_unsafe_names(app_paths, tmp_path):
    reg = TemplateRegistry(app_paths)
    reg.import_original(TECIS, make_import_pdf(tmp_path / "MUSTER_tecis.pdf"), version="2026-11")
    with pytest.raises(TemplateNotAvailableError):
        reg.remove_imported_version(TECIS, "2099-01")
    for bad in ("..", "../2026-11", "", "a/b"):
        with pytest.raises(TemplateRegistryError):
            reg.remove_imported_version(TECIS, bad)
    with pytest.raises(TemplateRegistryError):
        reg.remove_imported_version("gibt_es_nicht", "2026-11")
    assert [v.version for v in reg.list_versions(TECIS)] == ["2026-11"]


@requires_originals
def test_shipped_original_can_never_be_removed(app_paths):
    reg = TemplateRegistry(app_paths)
    shipped = reg.get_definition(SL13065_ID)
    original_hash = sha256(SL13065_PDF)
    with pytest.raises(TemplateRegistryError, match="Mitgelieferte"):
        reg.remove_imported_version(SL13065_ID, shipped.version)
    assert sha256(SL13065_PDF) == original_hash

    # Eigene Kopie der mitgelieferten Version (gleicher Versionsname): nur die Kopie wird gelöscht
    reg.save_mapping(SL13065_ID, shipped, confirmed=True)
    assert reg.verify(SL13065_ID).source == "importiert"
    reg.remove_imported_version(SL13065_ID, shipped.version)
    report = reg.verify(SL13065_ID)
    assert report.status == TemplateStatus.ACTIVE and report.source == "mitgeliefert"
    assert SL13065_PDF.is_file() and sha256(SL13065_PDF) == original_hash
    with pytest.raises(TemplateRegistryError, match="Mitgelieferte"):
        reg.remove_imported_version(SL13065_ID, shipped.version)
