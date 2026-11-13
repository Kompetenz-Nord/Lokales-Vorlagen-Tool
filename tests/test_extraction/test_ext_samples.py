import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf
import pytest
from extraction_helpers import FIXTURES
from PIL import Image

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory, FieldType
from insurance_app.samples import MUSTERFALL_FILES, generate_sample_set
from insurance_app.samples.generator import (
    FILE_POLICY,
    FILE_POLICY_SCAN,
    FILE_POLICY_SCAN_LOWRES,
    FILE_PORTAL,
    FILE_STATUS_REPORT,
    MUSTER_MARK,
    PORTAL_SIZE,
    portal_words,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return generate_sample_set(tmp_path_factory.mktemp("muster"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_files_exist_small_and_marked(sample):
    assert set(sample.files) == set(sample.ground_truth) == set(sample.metadata)
    for name, path in sample.files.items():
        assert path.exists(), name
        assert path.stat().st_size < 1_000_000, name
        assert "MUSTER" in name


def test_truth_json_written_and_consistent(sample):
    for name, truth in sample.ground_truth.items():
        on_disk = json.loads((sample.files[name].parent / f"{name}.truth.json").read_text(encoding="utf-8"))
        assert on_disk == truth
        for key, value in truth.items():
            fdef = FIELD_REGISTRY[key]
            assert fdef.category == FieldCategory.CONTRACT
            if fdef.dtype == FieldType.DATE:
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            if fdef.dtype == FieldType.AMOUNT:
                assert re.fullmatch(r"\d+\.\d{2}", value)


def test_generation_is_byte_deterministic(sample, tmp_path):
    again = generate_sample_set(tmp_path / "again")
    for name, path in sample.files.items():
        assert _sha(path) == _sha(again.files[name]), name


def _semantic_equal(a: Path, b: Path) -> bool:
    """Inhaltlich gleich (unabhängig von Bibliotheksversionen, die Bytes leicht ändern können)."""
    if _sha(a) == _sha(b):
        return True
    if a.suffix == ".pdf":
        with pymupdf.open(str(a)) as da, pymupdf.open(str(b)) as db:
            return da.page_count == db.page_count and all(
                pa.get_text() == pb.get_text() for pa, pb in zip(da, db, strict=True)
            )
    with Image.open(a) as ia, Image.open(b) as ib:
        if ia.size != ib.size:
            return False
        pa, pb = ia.convert("L").tobytes(), ib.convert("L").tobytes()
        return sum(abs(x - y) for x, y in zip(pa[::97], pb[::97], strict=True)) / len(pa[::97]) < 3


def test_fixtures_are_current(sample):
    """Die eingecheckten Fixtures entsprechen dem aktuellen Generator (sonst: scripts/generate_samples.py)."""
    for name, path in sample.files.items():
        fixture = FIXTURES / name
        assert fixture.exists(), f"{name} fehlt – scripts/generate_samples.py ausführen"
        assert _semantic_equal(fixture, path), f"{name} veraltet – scripts/generate_samples.py ausführen"
        assert (FIXTURES / f"{name}.truth.json").read_text(encoding="utf-8") == (
            path.parent / f"{name}.truth.json"
        ).read_text(encoding="utf-8")


def test_every_pdf_page_carries_muster_mark(sample):
    for name in (FILE_POLICY, FILE_STATUS_REPORT, "MUSTER_Nachtrag_Beitrag.pdf"):
        with pymupdf.open(str(sample.files[name])) as doc:
            assert MUSTER_MARK in doc.metadata["subject"]
            for page in doc:
                assert MUSTER_MARK in page.get_text(), (name, page.number)


def test_scan_is_image_only(sample):
    with pymupdf.open(str(sample.files[FILE_POLICY_SCAN])) as doc:
        assert doc.page_count == 1
        page = doc[0]
        assert page.get_text().strip() == ""
        assert len(page.get_images()) == 1
    with Image.open(sample.files[FILE_POLICY_SCAN_LOWRES]) as img:
        assert img.mode == "L"
        assert 800 <= img.width <= 850


def test_status_report_has_invisible_text_layer_labels_first(sample):
    with pymupdf.open(str(sample.files[FILE_STATUS_REPORT])) as doc:
        assert doc.page_count == 2
        for page in doc:
            assert {span["type"] for span in page.get_texttrace()} == {3}  # unsichtbar
            assert len(page.get_images()) == 1
        words = [w[4] for w in doc[1].get_text("words")]
        assert words.index("Spätester") < words.index("Bäckerei")  # erst Labels, dann Werte
        text = doc[0].get_text()
        assert "lnformation" in text and "Lebensversicherunq" in text  # Fremd-OCR-Fehler


def test_portal_png_and_word_boxes(sample):
    with Image.open(sample.files[FILE_PORTAL]) as img:
        assert img.size == PORTAL_SIZE
    words = portal_words()
    texts = [w.text for w in words]
    assert "5550123-4" in texts and "Ruhend" in texts
    assert all(0 <= w.bbox.x0 < w.bbox.x1 <= PORTAL_SIZE[0] * 0.75 for w in words)


def test_musterfall_files_belong_to_one_contract(sample):
    numbers = {sample.metadata[n].contract_number for n in MUSTERFALL_FILES}
    assert numbers == {"8815201-7"}
    assert sample.metadata[FILE_PORTAL].contract_number != "8815201-7"


def test_script_writes_to_given_directory(tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import generate_samples
    finally:
        sys.path.pop(0)
    assert generate_samples.main([str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / FILE_POLICY).exists()
    assert "MUSTER_Police_Direktversicherung.pdf" in capsys.readouterr().out
