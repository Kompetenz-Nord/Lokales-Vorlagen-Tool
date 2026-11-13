from __future__ import annotations

import pytest

from insurance_app.core.case import Case, PipelineStage
from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory, is_order_field
from insurance_app.core.models import FieldStatus, FieldValue
from insurance_app.core.settings import Settings


def test_order_fields_are_never_extractable():
    for f in FIELD_REGISTRY.values():
        if f.category == FieldCategory.ORDER:
            assert f.extractable is False, f.key
            assert f.key.startswith("order."), f.key


def test_case_rejects_automatic_order_values():
    case = Case()
    with pytest.raises(ValueError):
        case.set_field(
            FieldValue(key="order.severance_consent", normalized_value="true", status=FieldStatus.EXTRACTED)
        )
    case.set_field(
        FieldValue(key="order.severance_consent", normalized_value="true", status=FieldStatus.MANUAL)
    )
    assert case.get("order.severance_consent").as_bool() is True
    assert is_order_field("order.severance_consent")


def test_case_field_defaults_to_missing():
    case = Case()
    fv = case.field("contract.number")
    assert fv.status == FieldStatus.MISSING
    assert case.stage == PipelineStage.ABLAGE
    assert case.id.startswith("V")


def test_released_statuses():
    assert FieldStatus.CONFIRMED.is_released and FieldStatus.MANUAL.is_released
    for st in (FieldStatus.EXTRACTED, FieldStatus.CONFLICT, FieldStatus.UNCLEAR, FieldStatus.MISSING):
        assert not st.is_released


def test_settings_local_only_enforced(tmp_path):
    s = Settings()
    assert s.local_only and not s.ai_enabled
    with pytest.raises(ValueError):
        Settings(local_only=False)
    s.save(tmp_path / "s.json")
    assert Settings.load(tmp_path / "s.json") == s
