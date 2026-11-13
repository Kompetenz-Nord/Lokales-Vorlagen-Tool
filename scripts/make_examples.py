"""Beispielexporte mit ausschließlich synthetischen Daten erzeugen („MUSTER – KEINE ECHTDATEN“).

Aufruf:  python scripts/make_examples.py [Zielordner]   (Standard: docs/beispiel)

Erzeugt über den regulären App-Ablauf (AppService, inkl. Qualitätskontrolle):
* SL13065 Freigabeerklärung (Swiss-Life-Originalvorlage, befüllt)
* SL18477 Übernahmeerklärung, Variante private Fortführung (Originalvorlage, befüllt)
* Ordentliche Kündigung als DIN-5008-Brief
Alle Daten stammen aus dem Musterfall bzw. sind offensichtlich fiktiv.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from insurance_app.core.paths import AppPaths  # noqa: E402
from insurance_app.core.service import AppService  # noqa: E402

TODAY = date(2026, 11, 13)


def _release_person(svc: AppService, case) -> None:
    for key in (
        "contract.number",
        "insured.first_name",
        "insured.last_name",
        "policyholder.name",
        "insured.birth_date",
    ):
        svc.confirm_field(case, key)
    for key in ("insured.street", "insured.house_number", "insured.postal_code", "insured.city"):
        fv = case.field(key)
        if fv.status.value == "CONFLICT":
            svc.choose_candidate(case, key, fv.suggestion_index or 0)
    svc.edit_field(case, "employer.employment_start", "01.01.2019")
    svc.edit_field(case, "employer.employment_end", "30.09.2026")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else ROOT / "docs" / "beispiel"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lvt-beispiel-") as tmp:
        svc = AppService(
            AppPaths.for_testing(Path(tmp)), guard_network=True, configure_logs=False, today=lambda: TODAY
        )
        try:
            produced: list[Path] = []
            # 1) SL13065 Freigabe
            case = svc.create_sample_case()
            svc.select_template(case, "swisslife_sl13065_freigabe")
            _release_person(svc, case)
            svc.set_choice(case, "order.severance_consent", False)
            svc.set_choice(case, "order.waive_refund", False)
            produced.append(svc.generate_pdf(case).path)
            # 2) SL18477 Übernahme, private Fortführung
            case = svc.create_sample_case()
            svc.select_template(case, "swisslife_sl18477_uebernahme")
            _release_person(svc, case)
            svc.confirm_field(case, "insured.nationality")
            svc.edit_field(case, "insured.birth_place", "Musterstadt")
            svc.set_choice(case, "order.continuation_mode", "private")
            svc.set_choice(case, "order.private_contribution_option", "reduced")
            svc.edit_field(case, "order.reduced_premium_amount", "75,50")
            svc.set_choice(case, "order.pep_status", "no")
            svc.set_choice(case, "order.payment_method", "sepa")
            svc.edit_field(case, "order.payment_iban", "DE89 3704 0044 0532 0130 00")
            svc.edit_field(case, "order.payment_account_holder", "Erika Musterfrau")
            svc.edit_field(case, "order.payment_institution", "Beispielbank (MUSTER)")
            svc.set_choice(case, "order.advice_waiver_acknowledged", True)
            produced.append(svc.generate_pdf(case).path)
            # 3) Ordentliche Kündigung (DIN 5008)
            case = svc.create_sample_case()
            svc.select_template(case, "kuendigung_ordentlich")
            for key in (
                "contract.number",
                "policyholder.name",
                "policyholder.street",
                "policyholder.house_number",
                "policyholder.postal_code",
                "policyholder.city",
                "insurer.name",
                "insured.first_name",
                "insured.last_name",
            ):
                svc.confirm_field(case, key)
            svc.copy_insurer_to_recipient(case)
            for key, value in (
                ("order.recipient_street", "Musterallee"),
                ("order.recipient_house_number", "1"),
                ("order.recipient_postal_code", "12345"),
                ("order.recipient_city", "Musterstadt"),
                ("order.letter_date", "13.11.2026"),
            ):
                svc.edit_field(case, key, value)
            svc.set_choice(case, "order.termination_timing", "next_possible")
            svc.set_choice(case, "order.revoke_sepa", True)
            svc.set_choice(case, "order.payout_to_account", False)
            svc.set_choice(case, "order.deadline_checked", True)
            produced.append(svc.generate_pdf(case).path)
            for p in produced:
                name = p.name.split("_V")[0] + "_MUSTER.pdf"  # Vorgangs-ID weglassen (zufällig)
                shutil.copyfile(p, target / name)
                print(f"erzeugt: {target / name}")
        finally:
            svc.close()
            from insurance_app.security.network_guard import uninstall_network_guard

            uninstall_network_guard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
