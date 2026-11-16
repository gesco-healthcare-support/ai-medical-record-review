"""The largest page a render may make, and one huge page read at a time.

On 2026-11-13 a record whose pages declare a size of 154 x 200 inches ran the live box out of memory
twice: at 200 dpi such a page renders to 1,232 megapixels, held again by every program it passes
through, and the kernel killed the worker reading it. Each render now stays within
`ocr.MAX_RENDER_MEGAPIXELS` and a side Tesseract can read, and pages above
`ocr.LARGE_RENDER_MEGAPIXELS` are read one at a time in a process.

Every PDF here is synthetic: blank pages of a declared size, made with pypdf. Rendering and OCR are
replaced, so what is checked is the dpi each render is asked for.
"""

import threading
import time

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.services import form_boxes, ocr, verify_pass

BASE_DPI = 200


def _pdf(tmp_path, *sizes_in) -> str:
    """A PDF of blank pages, each (width, height) in inches."""
    writer = PdfWriter()
    for width, height in sizes_in:
        writer.add_blank_page(width=width * 72, height=height * 72)
    path = tmp_path / "pages.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return str(path)


def _megapixels(size_in, dpi) -> float:
    width, height = size_in
    return width * dpi * height * dpi / 1_000_000


@pytest.fixture
def renders(monkeypatch):
    """Every render `ocr._rasterize` asks Poppler for, as its keyword arguments."""
    asked = []

    def render(_pdf_path, **kwargs):
        asked.append(kwargs)
        return [Image.new("L", (8, 8), 255)]

    monkeypatch.setattr(ocr, "convert_from_path", render)
    return asked


def test_demonstrates_a_page_declared_154_by_200_inches_renders_within_the_ceiling(
    tmp_path, renders
):
    """The reported page: 1,232 Mpx at the 200 dpi base. Its render is held to the ceiling, and to
    a side Tesseract can read, and the image records the dpi it really has."""
    path = _pdf(tmp_path, (8.5, 11), (154, 200))

    images = ocr._rasterize(path, first_page=2, last_page=2)

    dpi = renders[-1]["dpi"]
    assert dpi < BASE_DPI
    assert _megapixels((154, 200), dpi) <= ocr.MAX_RENDER_MEGAPIXELS
    assert 200 * dpi <= ocr.MAX_RENDER_SIDE_PX
    assert images[0].info["dpi"] == (dpi, dpi)


def test_demonstrates_the_lowered_dpi_is_declared_to_tesseract(tmp_path, renders, monkeypatch):
    """The page read through the OCR pass's own reader tells Tesseract its real dpi, as any render
    below the base does."""
    told = {}

    def read(image, timeout=0, config=""):
        told["config"] = config
        return "some words"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", read)
    monkeypatch.setattr(ocr, "_configured", True)
    path = _pdf(tmp_path, (154, 200))

    assert ocr._read_page(path, 1) == "some words"
    assert told["config"] == f"--dpi {renders[-1]['dpi']}"


def test_demonstrates_a_page_too_long_for_tesseract_is_held_to_a_side_it_can_read(
    tmp_path, renders
):
    """A strip 4 x 400 inches is only 64 Mpx at 200 dpi, but 80,000 px long: Tesseract cannot read
    an image over 32,767 px on a side, so it read nothing before."""
    path = _pdf(tmp_path, (4, 400))

    ocr._rasterize(path, first_page=1, last_page=1)

    assert 400 * renders[-1]["dpi"] <= ocr.MAX_RENDER_SIDE_PX


def test_demonstrates_a_whole_document_render_takes_the_lowest_dpi_any_page_needs(
    tmp_path, renders
):
    path = _pdf(tmp_path, (8.5, 11), (154, 200))

    ocr._rasterize(path)

    assert _megapixels((154, 200), renders[-1]["dpi"]) <= ocr.MAX_RENDER_MEGAPIXELS


def test_demonstrates_the_check_box_read_is_held_and_measures_boxes_at_the_real_dpi(
    tmp_path, renders, monkeypatch
):
    """The check-box read asks for 200 dpi outright; that is held to the ceiling too, and the boxes
    are then measured at the dpi the page was really rendered at - their sizes are in inches."""
    measured = []

    def find_boxes(gray, dpi):
        measured.append(dpi)
        return []

    monkeypatch.setattr(form_boxes, "find_boxes", find_boxes)
    path = _pdf(tmp_path, (154, 200))

    assert form_boxes.read_check_boxes(path, 1, 1) == []
    assert renders[-1]["dpi"] < form_boxes.FORM_BOX_DPI
    assert measured == [renders[-1]["dpi"]]


def test_demonstrates_the_verify_pass_page_image_is_held(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(
        verify_pass,
        "convert_from_path",
        lambda _p, **kwargs: asked.append(kwargs["dpi"]) or [Image.new("L", (8, 8))],
    )
    path = _pdf(tmp_path, (154, 200))

    verify_pass._page_image(path, 1)

    assert _megapixels((154, 200), asked[-1]) <= verify_pass._MAX_IMAGE_MEGAPIXELS


@pytest.mark.parametrize(
    "size_in",
    [(8.5, 11), (11, 17), (36, 48), (48, 48)],
    ids=["letter", "ledger", "48x36 drawing", "48x48 drawing"],
)
def test_guard_an_ordinary_or_large_real_page_renders_exactly_as_asked(tmp_path, renders, size_in):
    """GUARD: capping real large pages was measured to cost words (`ocr_max_long_edge_px` in
    config). Every page up to 48 inches on its long side keeps its dpi in all three renders."""
    path = _pdf(tmp_path, size_in)

    ocr._rasterize(path, first_page=1, last_page=1)
    assert renders[-1]["dpi"] == BASE_DPI
    assert ocr.render_dpi(path, form_boxes.FORM_BOX_DPI, 1, 1) == form_boxes.FORM_BOX_DPI
    assert ocr.render_dpi(path, 120, 1, 1, max_megapixels=verify_pass._MAX_IMAGE_MEGAPIXELS) == 120


def test_guard_the_verify_ceiling_is_the_ocr_ceiling_page_size_at_its_own_dpi():
    """GUARD: the verify pass holds the same pages as the OCR does - the OCR ceiling's page size at
    120 dpi instead of 200. Change one and this asks for the other."""
    assert verify_pass._MAX_IMAGE_MEGAPIXELS == round(
        ocr.MAX_RENDER_MEGAPIXELS * (120 / BASE_DPI) ** 2
    )


def test_guard_a_page_whose_size_cannot_be_read_keeps_its_dpi(tmp_path):
    """GUARD: an unreadable page box never stops a read; it renders as it always did."""
    assert ocr.render_dpi(str(tmp_path / "missing.pdf"), BASE_DPI, 1, 1) == BASE_DPI
    path = _pdf(tmp_path, (8.5, 11))
    assert ocr.render_dpi(path, BASE_DPI, 5, 5) == BASE_DPI  # past the last page


def _concurrent_reads(monkeypatch, path, pages) -> int:
    """Read ``pages`` of ``path`` in one thread each through the OCR pass's reader; the most
    renders that were ever under way at once."""
    state = {"now": 0, "most": 0}
    guard = threading.Lock()

    def render(_pdf_path, **_kwargs):
        with guard:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
        time.sleep(0.2)
        with guard:
            state["now"] -= 1
        return [Image.new("L", (8, 8), 255)]

    monkeypatch.setattr(ocr, "convert_from_path", render)
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, **_k: "words")
    monkeypatch.setattr(ocr, "_configured", True)
    start = threading.Barrier(len(pages))

    def read(page):
        start.wait(timeout=5)
        ocr._read_page(path, page)

    threads = [threading.Thread(target=read, args=(page,)) for page in pages]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return state["most"]


def test_demonstrates_large_pages_are_read_one_at_a_time(tmp_path, monkeypatch):
    """Two pages of 144 Mpx (60 x 60 inches) each, under the ceiling but above the one-at-a-time
    line: the OCR pass's threads take turns rather than hold both at once."""
    path = _pdf(tmp_path, (60, 60), (60, 60))
    assert _megapixels((60, 60), BASE_DPI) > ocr.LARGE_RENDER_MEGAPIXELS

    assert _concurrent_reads(monkeypatch, path, [1, 2]) == 1


def test_guard_ordinary_pages_are_still_read_side_by_side(tmp_path, monkeypatch):
    """GUARD: below the line nothing waits - a 48 x 36 inch drawing (69 Mpx) included."""
    path = _pdf(tmp_path, (8.5, 11), (36, 48))
    assert _megapixels((36, 48), BASE_DPI) <= ocr.LARGE_RENDER_MEGAPIXELS

    assert _concurrent_reads(monkeypatch, path, [1, 2]) == 2
