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

from app.config import _SMALLEST_PAGE_LONG_EDGE_PT, _STAGE_RENDER_TARGETS, get_settings
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


def test_images_carry_no_page_label_unless_asked(monkeypatch):
    """GUARD. Summarization, the DOI read and the deposition read all take this default, and
    none of them reports a page position back - so a label there is tokens for nothing and a
    change to what those three send."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 3, 10)

    assert [type(part).__name__ for part in parts] == ["ImagePart"] * 3


def _labels(parts):
    """The ``Page N`` labels only.

    Extracted by PREDICATE rather than by slicing `parts[::2]`, which the one-time preamble
    broke: a caller that adds any part before the first label silently shifts every index."""
    return [p.text for p in parts if getattr(p, "text", "").startswith("Page ")]


def test_the_labels_are_introduced_as_positions_rather_than_pagination(monkeypatch):
    """WHEN pages are labelled, THE SYSTEM SHALL say once that a label is a position only.

    "Page 1", "Page 2", "Page 3" is also exactly what ONE document's pagination looks like, and
    the model read it that way: on the six reviewer-corrected records (2026-09-23) boundary
    recall on ONE-PAGE documents was 77.6% against 95.3% on every other length, and 78% of all
    remaining missed boundaries sat on documents of one or two pages, arriving in consecutive
    runs. The label cannot say what it MEANS; this does."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 3, 10, label_pages=True)

    assert parts[0].text == rasterise._LABEL_PREAMBLE
    assert "SEPARATE documents" in parts[0].text
    # ONCE for the whole call, not once per page - it is a statement about the labels.
    assert sum(1 for p in parts if getattr(p, "text", "") == rasterise._LABEL_PREAMBLE) == 1


def test_an_unlabelled_call_gets_no_preamble(monkeypatch):
    """The preamble explains the labels, so with no labels it is noise - and it would reach
    summarization and the DOI read, which pass label_pages=False and never ask for a position."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 3, 10)

    assert all(type(p).__name__ == "ImagePart" for p in parts)


def test_a_labelled_page_says_which_page_it_is_before_showing_it(monkeypatch):
    """A bare list of images carries NO page position, so a model asked for one has to count the
    images. Gemini never had to - a PDF part has discrete pages - which is why this only arises
    on the rasterised backend. The label goes BEFORE its image so it reads as naming what
    follows."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 3, 10, label_pages=True)

    # one preamble, then a label before each of the three images
    assert len(parts) == 7
    assert _labels(parts) == ["Page 1", "Page 2", "Page 3"]
    assert [type(p).__name__ for p in parts[2::2]] == ["ImagePart"] * 3


def test_the_label_numbers_the_call_and_not_the_document(monkeypatch):
    """THE COORDINATE SYSTEM, and the one thing here most likely to be 'corrected' later.

    A window starting at page 21 labels its images `Page 1`..`Page 5`, NOT `Page 21`. That is
    what SEGMENTATION_PROMPT already means by "the N-th page of THIS file" - the file being the
    excerpt - and what `_window_rows` already assumes when it adds `window_start - 1` to every
    row it parses. Labelling absolutely would double-count that offset and would need the prompt
    reworded, which moves its fingerprint and costs the quality baseline its comparability.
    """
    rendered = _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 21, 25, 10, label_pages=True)

    assert _labels(parts) == ["Page 1", "Page 2", "Page 3", "Page 4", "Page 5"]
    # Control: the ABSOLUTE pages really are 21-25, so the labels are relative by choice rather
    # than because the call happened to start at 1.
    assert [first for first, _last, _dpi in rendered] == [21, 22, 23, 24, 25]


def test_the_cap_counts_pages_rather_than_parts(monkeypatch):
    """A labelled call emits two parts per page, so a cap applied to the PARTS list would halve
    the window - 15 pages of a 30-page window, silently, which is what `max_pages` exists to stop."""
    _stub_the_rasteriser(monkeypatch)

    parts = rasterise.page_image_parts("/synthetic.pdf", 1, 50, 4, label_pages=True)

    assert _labels(parts) == ["Page 1", "Page 2", "Page 3", "Page 4"]
    assert sum(1 for p in parts if type(p).__name__ == "ImagePart") == 4


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


# --- the render target as a parameter ----------------------------------------------------------
#
# Same lesson as the page cap above, one level down: the resolution was baked in, so a second caller
# wanting a different value had no way to ask. The DOI read supplies its own, because reading a
# labelled date field is a different task from judging page layout.


def test_omitting_the_render_target_uses_the_summarize_setting():
    """WHEN long_edge_px is omitted, THE SYSTEM SHALL render exactly as it did before it existed."""
    settings = get_settings()
    reader = _reader_with_box(612, 792)
    assert rasterise.page_dpi(reader, 1, settings) == rasterise.page_dpi(
        reader, 1, settings, settings.summary_image_long_edge_px
    )


def test_a_supplied_render_target_raises_the_dpi_for_the_same_page():
    """WHEN a larger long_edge_px is supplied, THE SYSTEM SHALL render that page at a higher DPI.

    Asserted as a comparison rather than an absolute, so the test says what the parameter is FOR
    without pinning a number that the ceiling below may clamp.
    """
    settings = get_settings()
    reader = _reader_with_box(612, 792)
    default = rasterise.page_dpi(reader, 1, settings)
    bigger = rasterise.page_dpi(reader, 1, settings, settings.summary_image_long_edge_px + 260)
    assert bigger > default


def test_the_dpi_ceiling_still_binds_so_a_large_request_is_not_granted():
    """WHEN the requested target exceeds what summary_image_dpi allows, THE SYSTEM SHALL clamp.

    THIS IS THE TRAP THE PARAMETER INTRODUCES. `summary_image_dpi` (120) caps the returned DPI, so a
    letter page tops out around 1320 px on its long edge however large the request. A caller asking
    for 2200 silently gets ~1320 and would read the number back from its own setting believing it
    had been honoured - which is exactly the class of silent no-op this repo has shipped before.
    """
    settings = get_settings()
    reader = _reader_with_box(612, 792)

    dpi = rasterise.page_dpi(reader, 1, settings, 2200)

    assert dpi == settings.summary_image_dpi, "the ceiling binds, not the request"
    achieved = 792 / 72 * dpi
    assert achieved < 2200, "the request was NOT granted"


def test_the_render_target_reaches_the_rasteriser(monkeypatch):
    """WHEN page_image_parts is given a target, THE SYSTEM SHALL pass it through to page_dpi."""
    rendered = []
    box = SimpleNamespace(width=612, height=792)
    reader = SimpleNamespace(pages=[SimpleNamespace(cropbox=box) for _ in range(4)])
    monkeypatch.setattr(rasterise, "PdfReader", lambda _path: reader)

    def _convert(_path, first_page, last_page, dpi):
        rendered.append(dpi)
        return [_FakeImage()]

    monkeypatch.setattr(rasterise, "convert_from_path", _convert)

    rasterise.page_image_parts("/synthetic.pdf", 1, 1, 10)
    default_dpi = rendered[-1]
    rasterise.page_image_parts(
        "/synthetic.pdf", 1, 1, 10, get_settings().summary_image_long_edge_px + 260
    )

    assert rendered[-1] > default_dpi


# --- the SHIPPED target, and the boot guard that protects it -------------------------------------
#
# Everything above guards the MECHANISM - omitting the target, a larger target raising the dpi, the
# ceiling clamping, the target reaching the rasteriser. All four still pass if the shipped value
# stops being achievable, because the ceiling that would stop it (`summary_image_dpi`) belongs to
# the SUMMARIZE stage and no test here reads the two together.


def test_the_doi_render_target_actually_lands_on_the_tightest_corpus_page():
    """WHEN page_dpi is given doi_image_long_edge_px, THE SYSTEM SHALL land within one dpi step.

    The assertion the mechanism tests cannot make: that 1300 IS ACHIEVED, not merely configured.
    Asserted on the 790pt geometry rather than US Letter because a SMALLER box needs a HIGHER dpi,
    so 790pt is the worst case and no record in the corpus is tighter. The tolerance is one dpi
    step (790/72 = 10.97px) because a dpi is an integer and the fit loses up to that much however
    the ceiling is set - anything larger is the ceiling binding rather than rounding.
    """
    settings = get_settings()
    page_pt = _SMALLEST_PAGE_LONG_EDGE_PT
    dpi = rasterise.page_dpi(
        _reader_with_box(605, page_pt), 1, settings, settings.doi_image_long_edge_px
    )
    achieved = page_pt / 72 * dpi
    assert achieved >= settings.doi_image_long_edge_px - page_pt / 72, (
        f"the DOI read renders at {achieved:.0f}px against its "
        f"{settings.doi_image_long_edge_px}px target - summary_image_dpi "
        f"({settings.summary_image_dpi}) is capping it"
    )


@pytest.mark.parametrize("ceiling", [120, 118, 117, 110, 60])
@pytest.mark.parametrize("stage,setting", sorted(_STAGE_RENDER_TARGETS.items()))
def test_the_boot_guard_agrees_with_what_page_dpi_actually_renders(
    stage, setting, ceiling, monkeypatch
):
    """The boot guard RE-DERIVES this function's arithmetic; this is what stops the two drifting.

    `Settings._assert_target_is_reachable` cannot call page_dpi - `rasterise` imports `config`, so
    the dependency can only run one way - so it recomputes the fit on the same geometry. A guard
    that describes arithmetic it does not execute can drift silently and still read like a check,
    which is the failure mode this repo has shipped before. So the guard's verdict is compared
    against what the REAL function returns, across the boundary in both directions.

    Parametrised over the ceiling rather than asserted once because a guard that never fires and a
    guard with the threshold one step off both look correct at the shipped value alone. 120 and 118
    must pass, 117 and below must refuse.

    Parametrised over `_STAGE_RENDER_TARGETS` rather than over doi alone so a stage added to that
    mapping is tied to page_dpi automatically. A new stage whose target nobody checked against the
    real renderer is exactly what generalising the guard could otherwise have made easy to add.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "summary_image_dpi", ceiling)
    page_pt = _SMALLEST_PAGE_LONG_EDGE_PT
    target = getattr(settings, setting)

    dpi = rasterise.page_dpi(_reader_with_box(605, page_pt), 1, settings, target)
    lands = page_pt / 72 * dpi >= target - page_pt / 72

    try:
        settings._assert_target_is_reachable(stage, setting)
        guard_allows_boot = True
    except RuntimeError:
        guard_allows_boot = False

    assert guard_allows_boot == lands, (
        f"at summary_image_dpi={ceiling} page_dpi returns {dpi} "
        f"({'lands' if lands else 'falls short'}) but the boot guard "
        f"{'allows' if guard_allows_boot else 'refuses'} it"
    )
