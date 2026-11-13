"""AppService: importierte Vorlagenversion entfernen – sichere Folgen für Vorgänge. Synthetische Daten."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from insurance_app.core.paths import AppPaths
from insurance_app.core.service import REMOVED_VERSION_SUFFIX, AppService, ServiceError
from insurance_app.export.exporter import ExportBlockedError
from insurance_app.templates.schema import TemplateStatus

TODAY = date(2026, 11, 13)
SL13065 = "swisslife_sl13065_freigabe"
ORIGINAL = Path(__file__).resolve().parents[2] / "assets" / "templates" / "originals"

pytestmark = pytest.mark.skipif(not any(ORIGINAL.glob("*.pdf")), reason="Originalvorlagen nicht vorhanden")


@pytest.fixture()
def svc(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: TODAY
    )
    yield service
    service.close()


def _own_copy(svc: AppService) -> str:
    """Eigene (importierte) Kopie der mitgelieferten SL13065-Version anlegen und bestätigen."""
    shipped = svc.registry.get_definition(SL13065)
    svc.registry.save_mapping(SL13065, shipped, confirmed=True)
    assert svc.registry.verify(SL13065).source == "importiert"
    return shipped.version


def test_remove_version_marks_cases_and_keeps_exports(svc):
    version = _own_copy(svc)
    case = svc.create_sample_case()
    svc.select_template(case, SL13065)
    other = svc.create_case("MUSTER ohne Vorlage")
    export_file = svc.export_dir / "MUSTER_bereits_erzeugt.pdf"
    export_file.parent.mkdir(parents=True, exist_ok=True)
    export_file.write_bytes(b"%PDF-1.7 MUSTER")
    export_hash = hashlib.sha256(export_file.read_bytes()).hexdigest()
    log_before = svc.export_log()

    assert svc.cases_using_template_version(SL13065, version) == [case.id]
    affected = svc.remove_imported_template_version(SL13065, version)
    assert affected == [case.id]

    # mitgelieferte Originalvorlage ist wieder die verwendete Version – gleicher Versionsname
    report = svc.registry.verify(SL13065)
    assert report.status == TemplateStatus.ACTIVE and report.source == "mitgeliefert"
    reloaded = svc.load_case(case.id)
    assert reloaded.template_id == SL13065
    assert reloaded.template_version == version + REMOVED_VERSION_SUFFIX
    assert reloaded.history[-1].action == "template_version_removed"
    assert reloaded.contract_fields.keys() == case.contract_fields.keys()  # Werte bleiben erhalten
    status = svc.review_status(reloaded)
    assert not status.ready
    assert status.blocking_items[0].code == "TEMPLATE_VERSION_CHANGED"
    with pytest.raises(ExportBlockedError):
        svc.generate_pdf(reloaded)

    # erneut auswählen hebt die Sperre auf
    svc.select_template(reloaded, SL13065)
    assert reloaded.template_version == version
    assert all(i.code != "TEMPLATE_VERSION_CHANGED" for i in svc.review_status(reloaded).checklist)

    assert svc.load_case(other.id).template_version is None
    assert hashlib.sha256(export_file.read_bytes()).hexdigest() == export_hash  # Exporte unberührt
    assert svc.export_log() == log_before


def test_remove_version_refuses_shipped_original(svc):
    shipped = svc.registry.get_definition(SL13065)
    case = svc.create_sample_case()
    svc.select_template(case, SL13065)
    assert svc.cases_using_template_version(SL13065, shipped.version) == []  # nicht importiert
    with pytest.raises(ServiceError, match="Mitgelieferte"):
        svc.remove_imported_template_version(SL13065, shipped.version)
    assert svc.load_case(case.id).template_version == shipped.version
    assert svc.registry.verify(SL13065).status == TemplateStatus.ACTIVE
