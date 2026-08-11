"""Unit tests for the PDF sizing/counting/segmentation helpers."""

from mrr_ai.services import pdf as pdf_service
from mrr_ai.services.pdf import (
    get_pdf_page_count,
    get_pdf_size,
    segment_pdf,
    segment_pdf_locally,
)


def test_get_pdf_size_is_positive(make_pdf, tmp_path):
    path = make_pdf(tmp_path / "doc.pdf", pages=2)
    assert get_pdf_size(path) > 0


def test_get_pdf_page_count(make_pdf, tmp_path):
    path = make_pdf(tmp_path / "doc.pdf", pages=3)
    assert get_pdf_page_count(path) == 3


def test_get_pdf_page_count_invalid_returns_none(tmp_path):
    bad = tmp_path / "not.pdf"
    bad.write_text("this is not a pdf", encoding="utf-8")
    assert get_pdf_page_count(str(bad)) is None


def test_segment_pdf_locally_splits_into_chunks(make_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "out"))
    path = make_pdf(tmp_path / "big.pdf", pages=5)

    created = segment_pdf_locally(path, pages_per_segment=2)

    assert len(created) == 3  # 2 + 2 + 1
    assert created == sorted(created)
    for chunk in created:
        assert (tmp_path / "out" / "big_segmented").as_posix() in chunk.replace("\\", "/")


def test_segment_pdf_writes_under_home_mrrs(make_pdf, tmp_path, home_tmp):
    path = make_pdf(tmp_path / "rec.pdf", pages=3)

    segment_pdf(path, pages_per_segment=2)

    out_dir = home_tmp / "MRRs" / "rec_segmented"
    assert out_dir.is_dir()
    assert len(list(out_dir.glob("*.pdf"))) == 2
