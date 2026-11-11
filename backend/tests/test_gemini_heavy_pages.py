"""A cut of the record too heavy for one Gemini request is sent as page images, not refused.

Gemini's segmentation windows, injury-date read and deposition read are sent a cut of the record's
own PDF, and Vertex caps a request near 20 MB after base64. On 2026-11-11 a 223-page record failed
Identify three times because one scanned page weighed 14.2 MB on its own: segmentation raised before
any model call, and the reviewer saw only the generic error. The injury-date read failed the same
way and quietly left the row without a date.

`rasterise.pdf_or_page_images` sends such a cut as the images vLLM receives for the same call.

Synthetic only: blank pages written to tmp_path, Poppler replaced, the provider stubbed. The
weight of a cut is simulated by lowering the limit, never by building a real 14 MB file.
"""

from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import deposition_pages, pdf, rasterise, summary_doi
from app.services import segment_engine as se
from app.services.gemini import SEGMENTATION_PROMPT
from app.services.llm.parts import DocumentPart, ImagePart, TextPart


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both caches are module state; no test may see another test's files."""
    pdf._readers.clear()
    rasterise._edges_cache.clear()
    yield
    pdf._readers.clear()
    rasterise._edges_cache.clear()


class _FakeImage:
    """A PIL stand-in. Poppler is not invoked; only the bytes path is exercised."""

    def convert(self, _mode):
        return self

    def save(self, buffer, format=None, quality=None):  # noqa: A002 - PIL's own keyword
        buffer.write(b"jpeg")


@pytest.fixture
def renders(monkeypatch):
    """Replace Poppler; record the (page, dpi) of every render."""
    seen = []

    def _convert(_path, first_page, last_page, dpi):
        assert first_page == last_page, "rendered one page at a time"
        seen.append((first_page, dpi))
        return [_FakeImage()]

    monkeypatch.setattr(rasterise, "convert_from_path", _convert)
    return seen


def _record(tmp_path, pages=12):
    """A SYNTHETIC PDF of blank letter pages. Never a real record."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    path = tmp_path / "synthetic.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _over_the_limit(monkeypatch):
    """Every cut is now heavier than one request carries."""
    monkeypatch.setattr(rasterise, "GEMINI_INLINE_PDF_LIMIT", 10)


def _texts(parts):
    return [part.text for part in parts if isinstance(part, TextPart)]


# --- the helper -----------------------------------------------------------------------------------


def test_a_cut_that_fits_is_sent_as_the_same_pdf_bytes(tmp_path, renders):
    """GUARD. Below the limit Gemini is sent exactly the bytes it has always been sent."""
    path = _record(tmp_path)

    parts = rasterise.pdf_or_page_images(path, 3, 6, label_pages=True)

    assert len(parts) == 1
    assert isinstance(parts[0], DocumentPart)
    assert parts[0].mime_type == "application/pdf"
    assert parts[0].data == pdf.slice_pdf(path, 3, 6)
    assert renders == [], "nothing is rendered"


def test_a_cut_over_the_limit_is_sent_as_every_page_as_an_image(tmp_path, monkeypatch, renders):
    """WHEN a cut is heavier than one request carries, THE SYSTEM SHALL send its pages as images.

    Every page of the cut (no cap: a short image list reads as a short document), labelled as the
    vLLM window is, and no PDF part beside them (pages would be numbered two ways)."""
    path = _record(tmp_path)
    _over_the_limit(monkeypatch)

    parts = rasterise.pdf_or_page_images(path, 3, 6, label_pages=True)

    assert not any(isinstance(part, DocumentPart) for part in parts)
    assert sum(isinstance(part, ImagePart) for part in parts) == 4
    assert [page for page, _dpi in renders] == [3, 4, 5, 6]
    assert _texts(parts)[1:] == ["Page 1", "Page 2", "Page 3", "Page 4"]


def test_the_limit_is_inclusive(tmp_path, monkeypatch, renders):
    """A cut of exactly the limit still travels as its PDF; one byte more does not."""
    path = _record(tmp_path)
    size = len(pdf.slice_pdf(path, 2, 2))

    monkeypatch.setattr(rasterise, "GEMINI_INLINE_PDF_LIMIT", size)
    assert isinstance(rasterise.pdf_or_page_images(path, 2, 2)[0], DocumentPart)

    monkeypatch.setattr(rasterise, "GEMINI_INLINE_PDF_LIMIT", size - 1)
    assert isinstance(rasterise.pdf_or_page_images(path, 2, 2)[0], ImagePart)


def test_the_limit_stays_under_what_vertex_accepts():
    """GUARD. Vertex caps a request near 20 MB after base64, which inflates by 4/3."""
    assert rasterise.GEMINI_INLINE_PDF_LIMIT * 4 / 3 < 20 * 1024 * 1024
    # And above the packing budget, so an ordinary window always travels as its PDF.
    assert rasterise.GEMINI_INLINE_PDF_LIMIT > get_settings().window_budget_mb * 1024 * 1024


# --- segmentation ---------------------------------------------------------------------------------


def _stub_segment_provider(monkeypatch, captured, reply):
    class _Provider:
        def generate_structured(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text=reply, truncated=False, output_tokens=None)

    monkeypatch.setattr(se, "provider_for_stage", lambda *_a, **_k: _Provider())


def test_a_heavy_segmentation_window_reaches_gemini_as_labelled_images(
    tmp_path, monkeypatch, renders
):
    """WHEN a Gemini window is too heavy for one request, THE SYSTEM SHALL send it as the labelled
    images vLLM gets, and read the answer's page numbers within the window as always.

    This window used to be refused before any call, failing the whole record."""
    settings = get_settings()
    assert settings.backend_for("segment") == "gemini"
    path = _record(tmp_path)
    _over_the_limit(monkeypatch)
    captured = {}
    _stub_segment_provider(
        monkeypatch, captured, '[{"s": 1, "e": 1, "t": "Report", "d": "-", "m": "-"}]'
    )

    rows = se._window_rows(path, 7, 7)

    parts = captured["parts"]
    assert not any(isinstance(part, DocumentPart) for part in parts)
    assert sum(isinstance(part, ImagePart) for part in parts) == 1
    assert _texts(parts)[1:] == ["Page 1", SEGMENTATION_PROMPT]
    assert [(row["start"], row["end"]) for row in rows] == [(7, 7)]
    # Rendered as the vLLM window renders it (no long-edge override).
    assert renders == [(7, rasterise.page_dpi(PdfReader(path), 7, settings))]


def test_an_ordinary_gemini_window_is_unchanged(tmp_path, monkeypatch, renders):
    """GUARD. One PDF, then the prompt, nothing rendered."""
    path = _record(tmp_path)
    captured = {}
    _stub_segment_provider(monkeypatch, captured, "[]")

    se._window_rows(path, 1, 5)

    parts = captured["parts"]
    assert len(parts) == 2
    assert isinstance(parts[0], DocumentPart)
    assert renders == []


def test_the_gemini_window_budget_is_the_setting_and_vllm_has_none(monkeypatch):
    settings = get_settings()
    assert se._window_byte_budget(settings) == int(settings.window_budget_mb * 1024 * 1024)
    monkeypatch.setattr(settings, "llm_backend", "vllm")
    assert se._window_byte_budget(settings) == se._NO_BYTE_BUDGET


# --- the injury date and the deposition read --------------------------------------------------------


@pytest.fixture
def images_asked(monkeypatch):
    """Record what the Gemini path asks the rasteriser for."""
    asked = []

    def _images(path, start, end, max_pages, long_edge_px=None, label_pages=False, max_dpi=None):
        asked.append((int(start), int(end), max_pages, long_edge_px, label_pages))
        return [ImagePart(data=b"jpeg")]

    monkeypatch.setattr(rasterise, "page_image_parts", _images)
    return asked


def test_a_heavy_injury_date_read_is_sent_as_the_vllm_images(tmp_path, monkeypatch, images_asked):
    """WHEN the first pages of a document are too heavy for one request, THE SYSTEM SHALL send them
    as images at the injury-date render size, rather than the call failing and the row losing its
    date. The same pages as before: the first ten."""
    settings = get_settings()
    assert settings.backend_for("doi") == "gemini"
    path = _record(tmp_path, pages=14)
    _over_the_limit(monkeypatch)

    parts = summary_doi._isolated_parts(path, 2, 14, settings)

    assert parts == [ImagePart(data=b"jpeg")]
    assert images_asked == [(2, 11, 10, settings.doi_image_long_edge_px, False)]


def test_a_heavy_deposition_read_is_sent_as_the_vllm_images(tmp_path, monkeypatch, images_asked):
    """WHEN a transcript's first pages are too heavy for one request, THE SYSTEM SHALL send them as
    images at the deposition render size, rather than the transcript losing its page citations."""
    settings = get_settings()
    assert settings.backend_for("deposition") == "gemini"
    path = _record(tmp_path)
    _over_the_limit(monkeypatch)

    parts = deposition_pages._isolated_parts(path, 4, 9, settings)

    assert parts == [ImagePart(data=b"jpeg")]
    assert images_asked == [(4, 9, 6, settings.deposition_image_long_edge_px, False)]


def test_light_injury_date_and_deposition_reads_are_unchanged(tmp_path, images_asked):
    """GUARD. Below the limit both are sent the PDF cut they have always been sent."""
    settings = get_settings()
    path = _record(tmp_path, pages=14)

    doi = summary_doi._isolated_parts(path, 2, 14, settings)
    depo = deposition_pages._isolated_parts(path, 4, 9, settings)

    assert doi == [DocumentPart(data=pdf.slice_pdf(path, 2, 11), mime_type="application/pdf")]
    assert depo == [DocumentPart(data=pdf.slice_pdf(path, 4, 9), mime_type="application/pdf")]
    assert images_asked == []
