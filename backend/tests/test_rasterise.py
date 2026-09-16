"""The shared PDF rasteriser: the render resolution, and the page cap that must be a parameter.

The DPI tests MOVED HERE from test_summarize_engine.py unchanged when `_page_dpi` was extracted;
they are unit tests of a pure function and belong beside it. They matter more than their size
suggests: a DPI is a resolution only relative to a page's declared box, and this corpus declares
three different things, so the same "120 dpi" produced three different resolutions and one of them
cost 25,380 vision tokens for a single page.

The cap tests are new, and they are why this module exists at all rather than being a copied loop.
"""

import io
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import rasterise

# --- page_dpi: the render resolution -----------------------------------------------------------


def _reader_with_box(width_pt, height_pt):
    """Stand-in for PdfReader exposing only the crop box that page_dpi reads."""
    box = SimpleNamespace(width=width_pt, height=height_pt)
    return SimpleNamespace(pages=[SimpleNamespace(cropbox=box)])


def _long_edge_px(width_pt, height_pt):
    dpi = rasterise.page_dpi(_reader_with_box(width_pt, height_pt), 1, get_settings())
    return dpi, max(width_pt, height_pt) / 72 * dpi


@pytest.mark.parametrize(
    "width_pt,height_pt",
    [
        (2700, 3455),  # de-identified records A/B/C: box == pixel count, 120 dpi renders 4500x5758
        (1258, 1631),  # records 01-04 and 07-09: also box == pixel count
        (605, 790),  # records 05, 06, 10-14: an honest box over a 150 dpi source
        (612, 792),  # US Letter, which no record in the corpus actually has
    ],
)
def test_page_dpi_lands_every_corpus_geometry_on_the_configured_long_edge(width_pt, height_pt):
    settings = get_settings()
    _, long_edge = _long_edge_px(width_pt, height_pt)
    target = settings.summary_image_long_edge_px
    # At or under the target, and ON it rather than far under - a DPI is an integer, so the
    # rounding loses at most a few pixels. The whole point is that all four land in one place.
    assert long_edge <= target
    assert long_edge > target * 0.95


def test_page_dpi_never_exceeds_the_configured_dpi():
    # A page small enough that the long-edge fit would ALLOW a higher DPI must not get one:
    # summary_image_dpi is the ceiling, and the long edge is a second, lower one.
    settings = get_settings()
    dpi, long_edge = _long_edge_px(72, 144)  # a 1x2 inch page
    assert dpi == settings.summary_image_dpi
    assert long_edge < settings.summary_image_long_edge_px


def test_page_dpi_caps_a_landscape_page_on_its_long_edge():
    # Orientation comes from the box, not from /Rotate, so the WIDER side is what gets capped.
    settings = get_settings()
    dpi = rasterise.page_dpi(_reader_with_box(3455, 2700), 1, get_settings())
    assert 3455 / 72 * dpi <= settings.summary_image_long_edge_px


def test_page_dpi_falls_back_rather_than_dividing_by_zero():
    # A degenerate box is a corrupt PDF, not a signal to render at 0 dpi. Fall back to the
    # configured DPI and let the rasterizer fail loudly if the page is genuinely unreadable.
    settings = get_settings()
    assert rasterise.page_dpi(_reader_with_box(0, 0), 1, settings) == settings.summary_image_dpi


def test_page_dpi_is_never_zero_on_an_absurdly_large_box():
    # A box big enough that the fitted DPI rounds to 0 must still render something.
    assert rasterise.page_dpi(_reader_with_box(10_000_000, 10_000_000), 1, get_settings()) >= 1


# --- page_image_parts: the cap that must be a parameter ----------------------------------------


class _FakeImage:
    """A PIL stand-in. Poppler is not invoked; only the bytes path is exercised."""

    def convert(self, _mode):
        return self

    def save(self, buffer, format=None, quality=None):  # noqa: A002 - PIL's own keyword
        buffer.write(f"JPEG:{format}:{quality}".encode())


def _stub_the_rasteriser(monkeypatch, pages=200):
    """Replace Poppler and pypdf so these tests need neither a PDF nor a poppler install."""
    rendered = []
    box = SimpleNamespace(width=612, height=792)
    reader = SimpleNamespace(pages=[SimpleNamespace(cropbox=box) for _ in range(pages)])
    monkeypatch.setattr(rasterise, "PdfReader", lambda _path: reader)

    def _convert(_path, first_page, last_page, dpi):
        rendered.append((first_page, last_page, dpi))
        return [_FakeImage()]

    monkeypatch.setattr(rasterise, "convert_from_path", _convert)
    return rendered


def test_the_cap_bounds_a_range_longer_than_it(monkeypatch):
    """WHEN max_pages is smaller than the range, THE SYSTEM SHALL return exactly max_pages parts."""
    rendered = _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 50, 4)

    assert len(parts) == 4
    assert [first for first, _last, _dpi in rendered] == [1, 2, 3, 4]


def test_a_hundred_page_window_is_not_truncated_to_the_summarize_cap(monkeypatch):
    """WHEN called with max_pages of 100, THE SYSTEM SHALL return 100 parts.

    THIS IS THE REASON THE CAP IS A PARAMETER. The rasteriser this was extracted from read
    `summary_image_max_pages` (15) internally. Segmentation windows run to `window_max_pages` (100),
    so a helper that kept the internal read would have dropped 85 pages of a full window and
    reported nothing - and a short window is indistinguishable from a short document downstream.
    """
    rendered = _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 100, 100)

    assert len(parts) == 100
    # Control: the summarize cap genuinely differs, or this passes for the wrong reason.
    assert get_settings().summary_image_max_pages == 15
    assert len(rendered) == 100


def test_a_range_shorter_than_the_cap_is_not_padded(monkeypatch):
    """WHEN the range is shorter than max_pages, THE SYSTEM SHALL return one part per real page."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 5, 7, 100)

    assert len(parts) == 3


def test_every_part_is_a_jpeg_image_part(monkeypatch):
    """WHEN pages are rasterised, THE SYSTEM SHALL emit JPEG ImageParts at quality 70.

    The MIME type is asserted because the seam dispatches on it: `llm/openai.py` builds a data URL
    from it and Gemini passes it to `Part.from_bytes`, so a wrong type is a wrong request on both
    backends rather than a cosmetic detail.
    """
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 2, 10)

    assert all(part.mime_type == "image/jpeg" for part in parts)
    assert all(part.data == b"JPEG:JPEG:70" for part in parts)


def test_the_dpi_is_derived_per_page_rather_than_taken_from_the_setting(monkeypatch):
    """WHEN a page declares a box equal to its pixel count, THE SYSTEM SHALL lower the DPI for it.

    Pins that the rasteriser routes through page_dpi rather than passing summary_image_dpi straight
    through - the 1024px long edge is a decided value, and sending the raw setting would silently
    upscale two thirds of the corpus.
    """
    rendered = []
    settings = get_settings()
    big = SimpleNamespace(width=2700, height=3455)  # box == pixel count
    reader = SimpleNamespace(pages=[SimpleNamespace(cropbox=big)])
    monkeypatch.setattr(rasterise, "PdfReader", lambda _path: reader)

    def _convert(_path, first_page, last_page, dpi):
        rendered.append(dpi)
        return [_FakeImage()]

    monkeypatch.setattr(rasterise, "convert_from_path", _convert)

    rasterise.page_image_parts("/synthetic.pdf", 1, 1, 10)

    assert rendered[0] < settings.summary_image_dpi
    assert 3455 / 72 * rendered[0] <= settings.summary_image_long_edge_px


def test_the_buffer_is_written_as_jpeg_bytes(monkeypatch):
    """Guards the io path: an ImagePart must carry bytes, not a file handle or a PIL object."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 1, 10)

    assert isinstance(parts[0].data, bytes)
    assert isinstance(io.BytesIO(parts[0].data).read(), bytes)
