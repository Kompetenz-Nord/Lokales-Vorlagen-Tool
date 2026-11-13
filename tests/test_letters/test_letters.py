"""Tests der DIN-5008-Briefvorlagen (ausschließlich synthetische Daten)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pymupdf
import pytest

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.core.models import FieldStatus, FieldValue
from insurance_app.core.paths import resource_root
from insurance_app.letters import load_letter_template
from insurance_app.letters.compose import resolve_letter
from insurance_app.letters.din5008 import render_letter
from insurance_app.letters.plausibility import check_letter_plausibility
from insurance_app.templates.schema import TemplateCatalog

ROOT = resource_root()
LETTERS = ROOT / "config" / "letter_templates"
FONTS = ROOT / "assets" / "fonts"
TODAY = date(2026, 11, 13)
MUSTER_NAME = "Musterfrau"


def m(key: str, value: str, norm: str | None = None, status: FieldStatus = FieldStatus.MANUAL) -> FieldValue:
    return FieldValue(
        key=key, value=value, normalized_value=norm if norm is not None else value, status=status
    )


def base_fields() -> tuple[dict[str, FieldValue], dict[str, FieldValue]]:
    contract = [
        m("policyholder.first_name", "Erika"),
        m("policyholder.name", MUSTER_NAME),
        m("policyholder.street", "Beispielweg"),
        m("policyholder.house_number", "7"),
        m("policyholder.postal_code", "12345"),
        m("policyholder.city", "Musterstadt"),
        m("contract.number", "8815201-7"),
        m("insured.first_name", "Erika"),
        m("insured.last_name", MUSTER_NAME),
        m("premium.amount", "100,00 EUR", "100.00"),
    ]
    order = [
        m("order.letter_date", "13.11.2026", "2026-11-13"),
        m("order.recipient_company", "Beispiel Lebensversicherung AG (MUSTER)"),
        m("order.recipient_street", "Musterallee"),
        m("order.recipient_house_number", "1"),
        m("order.recipient_postal_code", "54321"),
        m("order.recipient_city", "Beispielstadt"),
        m("order.deadline_checked", "ja", "true"),
    ]
    return {f.key: f for f in contract}, {f.key: f for f in order}


def plan_for(
    name: str, extra_order: list[FieldValue], contract_override: list[FieldValue] | None = None, mode="final"
):
    t = load_letter_template(LETTERS / f"{name}.json")
    contract, order = base_fields()
    for fv in contract_override or []:
        contract[fv.key] = fv
    for fv in extra_order:
        order[fv.key] = fv
    msgs = check_letter_plausibility(t.plausibility_rules, contract, order, TODAY)
    return t, resolve_letter(t, contract, order, mode=mode, extra_messages=msgs)


def codes(plan) -> set[str]:
    return {i.code for i in plan.checklist if i.blocking}


# ---------------------------------------------------------------------------


def test_all_letter_templates_valid_and_in_catalog():
    cat = TemplateCatalog.model_validate_json((ROOT / "config" / "template_catalog.json").read_text("utf-8"))
    letter_entries = {e.id: e for e in cat.entries if e.letter_file}
    assert len(letter_entries) == 6
    for entry in letter_entries.values():
        t = load_letter_template(LETTERS / entry.letter_file)
        assert t.catalog_id == entry.id
        assert all(k in FIELD_REGISTRY for k in t.referenced_keys())
    # Haftungsfreistellung bleibt ohne generierten Text
    assert next(e for e in cat.entries if e.id == "tecis_haftungsfreistellung_ak").letter_file is None


def test_no_175_sgb_and_no_ruecktritt_in_widerruf():
    raw = (LETTERS / "widerruf.json").read_text("utf-8")
    assert "trete" not in raw.lower()
    t = load_letter_template(LETTERS / "widerruf.json")
    printed = " ".join(t.all_texts())
    assert "175" not in printed


def test_ordinary_termination_full_and_rendered(tmp_path: Path):
    _, plan = plan_for(
        "kuendigung_ordentlich",
        [
            m("order.termination_timing", "date", "date"),
            m("order.desired_date", "01.01.2027", "2027-01-01"),
            m("order.revoke_sepa", "nein", "false"),
            m("order.payout_to_account", "nein", "false"),
        ],
    )
    assert not plan.blocking, plan.checklist
    text = plan.printed_text()
    assert "kündige ich den oben genannten Versicherungsvertrag fristgerecht zum 01.01.2027" in text
    assert "hilfsweise zum nächstmöglichen Zeitpunkt" in text
    assert "SEPA" not in text and "[Fehlt" not in text
    assert plan.subject == "Kündigung des Versicherungsvertrags"
    out = tmp_path / "k.pdf"
    res = render_letter(plan, out, FONTS, preview=False)
    assert res.page_count == 1 and not res.warnings
    doc = pymupdf.open(out)
    page = doc[0]
    # Empfänger liegt vollständig in der Anschriftzone (Fensterumschlag DIN lang)
    hit = page.search_for("Beispiel Lebensversicherung AG")[0]
    to_mm = 25.4 / 72
    assert hit.x0 * to_mm >= 20 and hit.x1 * to_mm <= 105
    assert hit.y0 * to_mm >= 62.7 and hit.y1 * to_mm <= 90
    # alle tatsächlich verwendeten Schriften eingebettet (Liberation Sans)
    span_fonts = {
        s["font"] for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
    }
    assert span_fonts and all("LiberationSans" in f for f in span_fonts)
    assert doc.metadata["author"] == "" and MUSTER_NAME not in doc.metadata["title"]
    assert "Mit freundlichen Grüßen" in page.get_text() and "Grüßen," not in page.get_text()
    assert "VORSCHAU" not in page.get_text()


def test_missing_decision_blocks_and_preview_marks(tmp_path: Path):
    _, plan = plan_for("kuendigung_ordentlich", [], mode="preview")
    assert "DECISION_REQUIRED" in codes(plan)
    res = render_letter(plan, tmp_path / "p.pdf", FONTS, preview=True)
    assert res.page_count == 1
    assert "VORSCHAU" in pymupdf.open(tmp_path / "p.pdf")[0].get_text()
    with pytest.raises(ValueError):
        render_letter(plan, tmp_path / "f.pdf", FONTS, preview=False)


def test_unconfirmed_contract_number_blocks():
    _, plan = plan_for(
        "kuendigung_ordentlich",
        [
            m("order.termination_timing", "next_possible"),
            m("order.revoke_sepa", "nein", "false"),
            m("order.payout_to_account", "nein", "false"),
        ],
        contract_override=[m("contract.number", "8815201-7", status=FieldStatus.EXTRACTED)],
    )
    assert "UNCONFIRMED" in codes(plan)
    assert all(MUSTER_NAME not in i.message and "8815201" not in i.message for i in plan.checklist)


def test_ordinary_termination_rejects_increase_timing():
    _, plan = plan_for(
        "kuendigung_ordentlich",
        [
            m("order.termination_timing", "increase_effective"),
            m("order.revoke_sepa", "nein", "false"),
            m("order.payout_to_account", "nein", "false"),
        ],
    )
    assert "NOT_ALLOWED" in codes(plan)


def test_missing_acknowledgment_blocks():
    t = load_letter_template(LETTERS / "kuendigung_ordentlich.json")
    contract, order = base_fields()
    del order["order.deadline_checked"]
    for fv in [
        m("order.termination_timing", "next_possible"),
        m("order.revoke_sepa", "nein", "false"),
        m("order.payout_to_account", "nein", "false"),
    ]:
        order[fv.key] = fv
    plan = resolve_letter(t, contract, order, mode="final")
    assert "ACK_REQUIRED" in codes(plan)


def test_premium_change_detects_end_before_start():
    """Fehler aus der bisherigen Vorlage: Ende der Befristung vor dem Beginn."""
    _, plan = plan_for(
        "hdi_beitragsfreistellung",
        [
            m("order.premium_change_type", "reduction"),
            m("order.change_start_date", "01.11.2026", "2026-11-01"),
            m("order.change_end_date", "01.02.2026", "2026-02-01"),
            m("order.new_premium_amount", "50", "50.00"),
            m("order.premium_interval", "monthly"),
            m("order.resume_premium_amount", "100", "100.00"),
            m("order.sepa_mandate_continues", "ja", "true"),
        ],
    )
    assert "CHANGE_PERIOD_ORDER" in codes(plan)


def test_premium_reduction_text_and_subject():
    _, plan = plan_for(
        "hdi_beitragsfreistellung",
        [
            m("order.premium_change_type", "reduction"),
            m("order.change_start_date", "01.12.2026", "2026-12-01"),
            m("order.change_end_date", "01.03.2027", "2027-03-01"),
            m("order.new_premium_amount", "50", "50.00"),
            m("order.premium_interval", "monthly"),
            m("order.resume_premium_amount", "100", "100.00"),
            m("order.sepa_mandate_continues", "ja", "true"),
        ],
    )
    assert not plan.blocking, plan.checklist
    assert plan.subject == "Antrag auf befristete Beitragsreduzierung"
    text = plan.printed_text()
    assert "vom 01.12.2026 bis zum 01.03.2027 auf 50,00 € monatlich zu reduzieren" in text
    assert "in Höhe von 100,00 € monatlich wieder aufzunehmen" in text


def test_premium_exemption_requires_decision_on_end_date_then_unbefristet():
    order = [
        m("order.premium_change_type", "exemption"),
        m("order.change_start_date", "01.12.2026", "2026-12-01"),
        m("order.sepa_mandate_continues", "nein", "false"),
    ]
    _, plan = plan_for("hdi_beitragsfreistellung", order)
    assert "DECISION_REQUIRED" in codes(plan)
    na = FieldValue(key="order.change_end_date", status=FieldStatus.NOT_APPLICABLE)
    _, plan = plan_for("hdi_beitragsfreistellung", [*order, na])
    assert not plan.blocking, plan.checklist
    assert plan.subject == "Antrag auf Beitragsfreistellung"
    assert "Beitragsfreistellung ab dem 01.12.2026" in plan.printed_text()


def test_increase_total_mismatch_warns_only():
    _, plan = plan_for(
        "continentale_beitragserhoehung",
        [
            m("order.change_start_date", "01.01.2027", "2027-01-01"),
            m("order.increase_amount", "130", "130.00"),
            m("order.new_premium_amount", "380", "380.00"),
            m("order.premium_interval", "monthly"),
            m("order.sepa_mandate_continues", "ja", "true"),
        ],
    )
    assert not plan.blocking
    assert any(i.code == "INCREASE_TOTAL_MISMATCH" for i in plan.checklist)  # 100 + 130 != 380
    assert "um 130,00 € monatlich zu erhöhen" in plan.printed_text()


def test_advisor_change_wording_and_explicit_consents():
    order = [
        m("order.new_advisor_company", "tecis Finanzdienstleistungen AG"),
        m("order.new_advisor_name", "Beraterin Beispiel"),
        m("order.new_advisor_street", "Beratungsweg"),
        m("order.new_advisor_house_number", "5"),
        m("order.new_advisor_postal_code", "10115"),
        m("order.new_advisor_city", "Berlin"),
    ]
    _, plan = plan_for("tecis_bestandsuebertrag", order)
    assert {"DECISION_REQUIRED"} <= codes(plan)  # Vollmacht + Datenweitergabe offen
    _, plan = plan_for(
        "tecis_bestandsuebertrag",
        [
            *order,
            m("order.grant_change_authority", "nein", "false"),
            m("order.data_sharing_consent", "ja", "true"),
        ],
    )
    assert not plan.blocking, plan.checklist
    text = plan.printed_text()
    assert (
        "die Betreuung des oben genannten Vertrags auf die tecis Finanzdienstleistungen AG zu übertragen"
        in text
    )
    assert "Vertragsänderungen können" not in text
    assert "übertrage ich meinen Vertrag" not in text


def test_widerruf_variants_and_hints():
    _, plan = plan_for(
        "widerruf",
        [
            m("order.revocation_subject", "termination"),
            m("order.termination_letter_date", "01.11.2026", "2026-11-01"),
        ],
    )
    assert not plan.blocking, plan.checklist
    assert plan.subject == "Rücknahme meiner Kündigung"
    assert any("§ 130" in (h.source or "") for h in plan.hints)
    _, plan = plan_for(
        "widerruf",
        [
            m("order.revocation_subject", "contract"),
            m("order.refund_to_account", "ja", "true"),
            m("order.payment_account_holder", "Erika Musterfrau"),
            m("order.payment_iban", "DE89370400440532013000"),
        ],
    )
    assert not plan.blocking, plan.checklist
    assert "IBAN: DE89 3704 0044 0532 0130 00" in plan.printed_text()
    assert any("§ 8" in (h.source or "") for h in plan.hints)


def test_special_termination_requires_basis_confirmation_and_checks_timing():
    order = [
        m("order.special_reason", "claim"),
        m("order.insurer_notice_date", "01.11.2026", "2026-11-01"),
        m("order.termination_timing", "increase_effective"),
        m("order.revoke_sepa", "nein", "false"),
        m("order.payout_to_account", "nein", "false"),
        FieldValue(key="order.legal_basis_text", status=FieldStatus.NOT_APPLICABLE),
    ]
    _, plan = plan_for("kuendigung_ausserordentlich", order)
    assert {"ACK_REQUIRED", "NOT_ALLOWED"} <= codes(plan)
    order[2] = m("order.termination_timing", "next_possible")
    _, plan = plan_for(
        "kuendigung_ausserordentlich", [*order, m("order.legal_basis_confirmed", "ja", "true")]
    )
    assert not plan.blocking, plan.checklist
    assert "im Zusammenhang mit dem Schadenfall vom 01.11.2026" in plan.printed_text()


def test_old_notice_date_warns():
    _, plan = plan_for(
        "kuendigung_ausserordentlich",
        [
            m("order.special_reason", "premium_increase"),
            m("order.insurer_notice_date", "01.08.2026", "2026-08-01"),
            m("order.termination_timing", "increase_effective"),
            m("order.revoke_sepa", "nein", "false"),
            m("order.payout_to_account", "nein", "false"),
            m("order.legal_basis_confirmed", "ja", "true"),
            FieldValue(key="order.legal_basis_text", status=FieldStatus.NOT_APPLICABLE),
        ],
    )
    assert not plan.blocking
    assert any(i.code == "NOTICE_DATE_OLD" for i in plan.checklist)


def test_multipage_letter_has_page_numbers_and_form_a(tmp_path: Path):
    attachments = "; ".join(f"Anlage Nummer {i}" for i in range(60))
    _, plan = plan_for(
        "kuendigung_ordentlich",
        [
            m("order.termination_timing", "next_possible"),
            m("order.revoke_sepa", "nein", "false"),
            m("order.payout_to_account", "nein", "false"),
            m("order.attachments", attachments),
        ],
    )
    res = render_letter(plan, tmp_path / "long.pdf", FONTS, preview=False, form="A")
    assert res.page_count >= 2 and res.form == "A"
    doc = pymupdf.open(tmp_path / "long.pdf")
    assert "Seite 1 von" in doc[0].get_text()
