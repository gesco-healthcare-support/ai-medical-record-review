"""OCR bounding + per-page resilience (pipeline forever-hang fix).

_ocr_image passes a wall-clock timeout to Tesseract; a timeout is a skippable per-page failure
(RuntimeError), NOT the fail-fast OcrUnavailableError (which means Tesseract/Poppler is missing).
The per-page extraction loops log and skip a failed page rather than aborting the document.
"""

from types import SimpleNamespace

import pytest

from app.errors import OcrUnavailableError, PdfUnreadableError
from app.services import ocr


class _Sentinel:
    """Stand-in for a rasterized PIL page (image_to_string is monkeypatched, so identity suffices)."""


def test_ocr_image_forwards_timeout(monkeypatch):
    captured = {}

    def fake_image_to_string(image, timeout=0):
        captured["timeout"] = timeout
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)  # skip _ensure_tesseract's settings read

    assert ocr._ocr_image(_Sentinel()) == "text"
    assert captured["timeout"] == ocr.get_settings().ocr_timeout_seconds == 120


def test_ocr_image_timeout_raises_runtimeerror_not_unavailable(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    sentinel = _Sentinel()
    with pytest.raises(RuntimeError) as excinfo:
        ocr._ocr_image(sentinel)
    assert not isinstance(excinfo.value, OcrUnavailableError)


def test_selected_pages_skips_failing_page(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    # A per-page OCR timeout must be logged + skipped, never propagate out of the loop.
    assert ocr.extract_text_from_selected_pages("dummy.pdf", [1, 2]) == ""


def test_selected_pages_marks_absolute_page_numbers_when_asked(monkeypatch):
    # Depositions are summarised one line per transcript page, so the model has to SEE where each
    # page ends. The markers carry the ABSOLUTE record page, not a 1-based offset within the range.
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    out = ocr.extract_text_from_selected_pages("dummy.pdf", [143, 144], mark_pages=True)
    assert out == "Page 143:\nbody\nPage 144:\nbody\n"


def test_selected_pages_is_unmarked_by_default(monkeypatch):
    # Every existing caller (the duplicate check, and summarization for every category except 9) must
    # be byte-for-byte unchanged: markers in the dedup text would pollute similarity scoring, and in
    # other categories they would push page numbers into ordinary summaries.
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    assert ocr.extract_text_from_selected_pages("dummy.pdf", [143, 144]) == "bodybody"
    assert "Page" not in ocr.extract_text_from_selected_pages("dummy.pdf", [143])


def test_marked_page_is_skipped_without_losing_the_following_markers(monkeypatch):
    # A page whose OCR times out must not emit a marker with no body attached, and must not stop the
    # remaining pages from being marked.
    calls = {"n": 0}

    def flaky(image, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Tesseract process timeout")
        return "body"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", flaky)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    out = ocr.extract_text_from_selected_pages("dummy.pdf", [7, 8], mark_pages=True)
    assert "Page 7:" not in out  # the failed page contributes nothing at all
    assert out == "Page 8:\nbody\n"


def test_all_pages_skips_failing_page(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel(), _Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    # Page headers are still emitted; the unreadable body is skipped without aborting.
    out = ocr.extract_text_from_all_pages("dummy.pdf")
    assert "Page 1:" in out
    assert "Page 2:" in out


def test_tesseract_missing_still_fails_fast(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    sentinel = _Sentinel()
    with pytest.raises(OcrUnavailableError):
        ocr._ocr_image(sentinel)


class _Page:
    """A rasterized page that remembers which page it is, so a fake OCR can behave per page."""

    def __init__(self, page):
        self.page = page


def _per_page_rasterize(path, first_page, last_page):
    return [_Page(first_page)]


def test_report_separates_pages_that_errored_from_pages_that_read_blank(monkeypatch):
    """WHEN a page errors and another reads cleanly with no words, THE SYSTEM SHALL report them apart.

    extract_text_from_selected_pages collapses both into a silent skip, which is why a dedup run that
    could not read a fifth of a record was indistinguishable from one with nothing to find. The two
    have different causes: an error may be a transient timeout, while a film, photograph or blank
    separator sheet is legitimately textless and no retry will produce words.
    """

    def by_page(image, timeout=0):
        if image.page == 1:
            raise RuntimeError("Tesseract process timeout")
        return "" if image.page == 2 else "real body text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", by_page)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    monkeypatch.setattr(ocr, "_configured", True)

    text, report = ocr.extract_pages_with_report("dummy.pdf", [1, 2, 3], retries=0)
    assert text == "real body text"
    # `pages` is the LIST attempted, not a count (#210): it was a count here and a list in
    # `page_text.get_row_text_with_report`, whose docstring asserted the two matched exactly.
    assert report == {"pages": [1, 2, 3], "errored": [1], "blank": [2]}


def test_report_retries_only_the_errored_page(monkeypatch):
    """A retry is for a transient failure. A page that read cleanly and held no words must NOT be
    re-OCR'd: there is nothing to recover, and each attempt costs a rasterize plus a Tesseract run."""
    attempts = []

    def flaky(image, timeout=0):
        attempts.append(image.page)
        if image.page == 1 and attempts.count(1) == 1:
            raise RuntimeError("Tesseract process timeout")
        return "" if image.page == 2 else "body"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", flaky)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    monkeypatch.setattr(ocr, "_configured", True)

    text, report = ocr.extract_pages_with_report("dummy.pdf", [1, 2], retries=1)
    assert attempts == [1, 1, 2]  # page 1 retried and recovered; page 2 read once
    assert text == "body"
    assert report == {"pages": [1, 2], "errored": [], "blank": [2]}


def test_report_fails_fast_when_tesseract_is_missing(monkeypatch):
    """A config failure is not a per-page problem: retrying it would burn the budget on every page of
    the document and still produce nothing."""

    def missing(image, timeout=0):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", missing)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    monkeypatch.setattr(ocr, "_configured", True)

    with pytest.raises(OcrUnavailableError):
        ocr.extract_pages_with_report("dummy.pdf", [1, 2])


def _with_cap(monkeypatch, cap):
    """Point ocr.get_settings() at a copy carrying `cap`, since the real default disables capping."""
    real = ocr.get_settings()
    stub = SimpleNamespace(
        ocr_base_dpi=real.ocr_base_dpi,
        ocr_max_long_edge_px=cap,
        ocr_timeout_seconds=real.ocr_timeout_seconds,
        tesseract_cmd=real.tesseract_cmd,
    )
    monkeypatch.setattr(ocr, "get_settings", lambda: stub)
    return stub


def test_capping_is_disabled_by_default(monkeypatch):
    """The default must not change any rendering. Capping was MEASURED and rejected: it made an
    oversized page 4.2x faster and cost 6.0% of its recognized characters, so it ships off until a
    word-level quality metric exists to judge it (see the note on ocr_max_long_edge_px)."""
    assert ocr.get_settings().ocr_max_long_edge_px == 0
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: (3455.0,))
    assert ocr._dpi_for_page("/x.pdf", 1) == ocr.get_settings().ocr_base_dpi


def test_dpi_is_capped_for_oversized_pages_and_left_alone_otherwise(monkeypatch):
    """WHEN capping is enabled and a page would exceed it, THE SYSTEM SHALL lower the DPI to fit.

    Cap-only is the safety property: an ordinary page must render EXACTLY as before, so its stored OCR
    text cannot change. Only oversized pages move.
    """
    settings = _with_cap(monkeypatch, 3500)
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: (792.0, 3455.0))

    # 792pt at 200 DPI is 2200px - already inside the cap, so untouched.
    assert ocr._dpi_for_page("/x.pdf", 1) == settings.ocr_base_dpi

    # 3455pt at 200 DPI would be 9598px - capped, and the result must actually fit.
    capped = ocr._dpi_for_page("/x.pdf", 2)
    assert capped < settings.ocr_base_dpi
    assert 3455.0 * capped / 72 <= settings.ocr_max_long_edge_px


def test_unknown_page_sizes_fall_back_to_the_base_dpi(monkeypatch):
    """An unreadable page box must not stop OCR - it just means the cap cannot be applied."""
    settings = _with_cap(monkeypatch, 3500)
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: ())
    assert ocr._dpi_for_page("/x.pdf", 1) == settings.ocr_base_dpi
    # A whole-document rasterize has no single page to size against.
    assert ocr._dpi_for_page("/x.pdf", None) == settings.ocr_base_dpi


def test_the_rendered_dpi_is_declared_to_tesseract(monkeypatch):
    """Tesseract scales x-height decisions by the DPI it is told, so a reduced-DPI image that does not
    declare itself risks WORSE recognition - which would silently undo the point of the cap."""
    captured = {}

    def fake_image_to_string(image, timeout=0, config=""):
        captured["config"] = config
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    class _Rendered:
        info = {"dpi": (80, 80)}

    assert ocr._ocr_image(_Rendered()) == "text"
    assert captured["config"] == "--dpi 80"


def test_an_image_without_recorded_dpi_passes_no_config(monkeypatch):
    """Stubbed rasterizers hand back objects with no `.info`; that must not become a crash or a
    bogus `--dpi 0`."""
    captured = {}

    def fake_image_to_string(image, timeout=0):
        captured["called"] = True
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    assert ocr._ocr_image(_Sentinel()) == "text"
    assert captured["called"] is True


def test_the_base_dpi_is_not_declared_so_ordinary_pages_are_unchanged(monkeypatch):
    """WHEN a page renders at the base DPI, THE SYSTEM SHALL pass no --dpi flag.

    Measured 2026-08-19: passing `--dpi 200` changed the recognized text of a page whose resolution had
    not changed at all. Since ordinary pages are already inside the pixel cap, declaring the DPI there
    would silently alter most stored OCR output for no measured gain - so the flag is reserved for the
    pages whose DPI was actually lowered.
    """
    captured = {}

    def fake_image_to_string(image, timeout=0, **kwargs):
        captured.update(kwargs)
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)
    base = ocr.get_settings().ocr_base_dpi

    class _AtBase:
        info = {"dpi": (base, base)}

    class _Capped:
        info = {"dpi": (72, 72)}

    assert ocr._ocr_image(_AtBase()) == "text"
    assert "config" not in captured, "the base DPI must not be declared"

    captured.clear()
    assert ocr._ocr_image(_Capped()) == "text"
    assert captured.get("config") == "--dpi 72"


def test_a_corrupt_pdf_is_not_reported_as_a_missing_poppler(monkeypatch):
    """#201. `pdf2image` reported two very different failures through one exception type and the
    pipeline labelled both "Poppler unavailable". `PDFPageCountError` fires whenever `pdfinfo`
    cannot read a page count - a corrupt or encrypted upload, a truncated file, a deleted path - all
    of which happen on a perfectly healthy install. An operator meeting that message checks Poppler,
    finds it fine, and is stranded."""
    from pdf2image.exceptions import PDFPageCountError

    from app.errors import OcrUnavailableError, PdfUnreadableError

    def unreadable(*a, **k):
        raise PDFPageCountError("Unable to get page count. Is poppler installed?")

    monkeypatch.setattr(ocr, "convert_from_path", unreadable)
    monkeypatch.setattr(ocr, "_dpi_for_page", lambda *a, **k: 200)

    with pytest.raises(PdfUnreadableError) as excinfo:
        ocr._rasterize("/tmp/corrupt.pdf", first_page=1, last_page=1)

    # The message names the FILE, not the binary.
    assert "cannot read the PDF" in str(excinfo.value)
    assert "corrupt.pdf" in str(excinfo.value)
    assert "Poppler (pdf2image) unavailable" not in str(excinfo.value)
    # And what the USER is shown points at the upload rather than at the server.
    assert "could not be opened" in excinfo.value.user_message
    assert "OCR) is unavailable on the server" not in excinfo.value.user_message
    # STILL an OcrUnavailableError, and that is load-bearing rather than incidental: every layer
    # that refuses to degrade on unreadable pages must refuse for a bad file too, or segmentation
    # would carry on over an unopenable document and store empty text for every row.
    assert isinstance(excinfo.value, OcrUnavailableError)


def test_a_genuinely_missing_poppler_still_reports_itself(monkeypatch):
    """The other half. This one IS a config failure - identical on every document - and must keep
    both its message and its type, or #201 would have traded one wrong diagnosis for another."""
    from pdf2image.exceptions import PDFInfoNotInstalledError

    from app.errors import OcrUnavailableError, PdfUnreadableError

    def no_binary(*a, **k):
        raise PDFInfoNotInstalledError(
            "Unable to get page count. Is poppler installed and in PATH?"
        )

    monkeypatch.setattr(ocr, "convert_from_path", no_binary)
    monkeypatch.setattr(ocr, "_dpi_for_page", lambda *a, **k: 200)

    with pytest.raises(OcrUnavailableError) as excinfo:
        ocr._rasterize("/tmp/fine.pdf", first_page=1, last_page=1)

    assert "Poppler (pdf2image) unavailable" in str(excinfo.value)
    assert not isinstance(excinfo.value, PdfUnreadableError)
    assert "unavailable on the server" in excinfo.value.user_message


def test_an_unopenable_pdf_still_fails_the_page_read_rather_than_returning_empty(monkeypatch):
    """The property the subclassing exists to preserve, pinned at a real call site: the per-page
    loops catch every other exception and continue, and must NOT swallow this one."""
    from pdf2image.exceptions import PDFPageCountError

    from app.errors import PdfUnreadableError

    def unreadable(*a, **k):
        raise PDFPageCountError("truncated file")

    monkeypatch.setattr(ocr, "convert_from_path", unreadable)
    monkeypatch.setattr(ocr, "_dpi_for_page", lambda *a, **k: 200)
    monkeypatch.setattr(ocr, "_configured", True)

    with pytest.raises(PdfUnreadableError):
        ocr.extract_pages_with_report("/tmp/truncated.pdf", [1, 2, 3])


def test_the_two_report_contracts_agree_on_the_types_of_all_three_keys(monkeypatch):
    """#210: `pages` was a COUNT here and a LIST in `page_text.get_row_text_with_report`, whose
    docstring asserted the two matched "exactly". Nothing read the field, so nothing was broken -
    which is why it survived. Pinned as a TYPE comparison across both functions, because that is
    the claim the stale docstring made and the one a caller would rely on."""
    from app.services import page_text as pt

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    monkeypatch.setattr(ocr, "_configured", True)

    _text, direct = ocr.extract_pages_with_report("dummy.pdf", [4, 5])

    # The store-backed sibling builds its report the same way; compare the shapes, not the values.
    stored = {"pages": [4, 5], "errored": [], "blank": []}
    assert set(direct) == set(stored) == {"pages", "errored", "blank"}
    for key in stored:
        assert isinstance(direct[key], list), key
        assert type(direct[key]) is type(stored[key]), key
    assert direct["pages"] == [4, 5]  # the pages ASKED for, in order
    # And the count is still one call away, which is what the old shape offered.
    assert len(direct["pages"]) == 2
    # Deliberately NOT asserting anything about the docstring text. The first version of this test
    # checked that the word "exactly" was gone and FAILED - because the corrected docstring uses the
    # word to explain that it used to be there. A test of prose tests prose; the type comparison
    # above is the claim that matters.
    assert pt.get_row_text_with_report.__doc__ is not None


# ---------------------------------------------------------------------------------------------
# FAIL FAST VERSUS SKIP, AT THE SITES THAT PROPAGATE RATHER THAN RAISE.
#
# The tests above prove the distinction where the error is BORN - `_ocr_image` raises
# OcrUnavailableError, and `extract_pages_with_report` fails fast on it. Three other call sites
# re-raise it, and each pairs that re-raise with a tolerate-and-continue arm for every other
# exception. Those pairs had never run: the existing tests stub `_rasterize` or `_ocr_image`, which
# is exactly the layer the pairs live in.
#
# Each test below asserts BOTH arms of its pair. One arm alone is satisfied by code that always
# does the other, which is the failure mode this whole distinction exists to prevent.


def _settings_stub(monkeypatch, **over):
    """Point `ocr.get_settings()` at a copy, for the fields these tests vary."""
    real = ocr.get_settings()
    fields = {
        "ocr_base_dpi": real.ocr_base_dpi,
        "ocr_max_long_edge_px": real.ocr_max_long_edge_px,
        "ocr_timeout_seconds": real.ocr_timeout_seconds,
        "tesseract_cmd": real.tesseract_cmd,
    }
    fields.update(over)
    stub = SimpleNamespace(**fields)
    monkeypatch.setattr(ocr, "get_settings", lambda: stub)
    return stub


def test_the_tesseract_binary_path_is_applied_once_and_only_when_set(monkeypatch):
    """WHEN a tesseract_cmd is configured, THE SYSTEM SHALL apply it once and not re-read it.

    `_configured` is a module global holding a process-wide side effect on the pytesseract package.
    The once-only contract is why it exists: re-applying per call would make every OCR read settings
    and write a third-party global. Both halves are asserted - that it applies, and that a later
    change does NOT take - because a test of the first alone passes against code with no guard.
    """
    monkeypatch.setattr(ocr.pytesseract.pytesseract, "tesseract_cmd", "untouched")
    monkeypatch.setattr(ocr, "_configured", False)
    _settings_stub(monkeypatch, tesseract_cmd="/opt/first/tesseract")

    ocr._ensure_tesseract()
    assert ocr.pytesseract.pytesseract.tesseract_cmd == "/opt/first/tesseract"
    assert ocr._configured is True

    _settings_stub(monkeypatch, tesseract_cmd="/opt/second/tesseract")
    ocr._ensure_tesseract()
    assert ocr.pytesseract.pytesseract.tesseract_cmd == "/opt/first/tesseract", (
        "it re-read settings"
    )


def test_no_configured_binary_leaves_the_pytesseract_default_alone(monkeypatch):
    """WHERE no tesseract_cmd is set, THE SYSTEM SHALL NOT overwrite pytesseract's own default.

    The other side of the `if cmd:` guard. Writing an empty string there would point the package at
    nothing and produce a missing-binary failure on a machine where Tesseract is on PATH.
    """
    monkeypatch.setattr(ocr.pytesseract.pytesseract, "tesseract_cmd", "the-package-default")
    monkeypatch.setattr(ocr, "_configured", False)
    _settings_stub(monkeypatch, tesseract_cmd="")

    ocr._ensure_tesseract()

    assert ocr.pytesseract.pytesseract.tesseract_cmd == "the-package-default"
    assert ocr._configured is True, "it must still record that it ran"


def test_an_unreadable_page_box_yields_no_sizes_rather_than_raising(monkeypatch):
    """IF the PDF's page boxes cannot be read, THEN the sizes SHALL be empty, not an exception.

    Callers fall back to the base DPI on an empty tuple, so a raise here would abort OCR over a
    detail that only ever makes the render slightly larger.

    `_page_long_edges_pt` is `@lru_cache`d, so this uses a path no other test touches AND clears the
    cache first. Without that the assertion could pass against a value cached by an earlier test and
    never call the code under test at all.
    """
    ocr._page_long_edges_pt.cache_clear()

    def unreadable(_path):
        raise ValueError("not a PDF")

    monkeypatch.setattr(ocr, "PdfReader", unreadable)

    assert ocr._page_long_edges_pt("/only-this-test-uses-this-path.pdf") == ()


def test_the_long_edge_of_each_page_is_measured_in_points(monkeypatch):
    """THE SYSTEM SHALL report the LONGER side of each page, whichever way the page is oriented.

    The cap divides by this, so taking the width of a landscape page would under-report its real
    extent and let an oversized render through the very guard that exists to stop it.
    """
    ocr._page_long_edges_pt.cache_clear()
    portrait = SimpleNamespace(mediabox=SimpleNamespace(width=612, height=792))
    landscape = SimpleNamespace(mediabox=SimpleNamespace(width=1224, height=792))
    monkeypatch.setattr(ocr, "PdfReader", lambda _p: SimpleNamespace(pages=[portrait, landscape]))

    assert ocr._page_long_edges_pt("/a-second-path-only-this-test-uses.pdf") == (792.0, 1224.0)


def test_an_explicitly_supplied_dpi_is_used_instead_of_the_image_metadata(monkeypatch):
    """WHERE a dpi is passed, THE SYSTEM SHALL use it and not consult the image's own metadata.

    The caller knows the render resolution when the image came from `_rasterize`; reading it back
    off the image would make the declaration depend on a stamp another code path had to remember to
    write. The stamped-metadata route is covered separately.
    """
    captured = {}

    def fake_image_to_string(_image, timeout=0, config=""):
        captured["config"] = config
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)
    base = ocr.get_settings().ocr_base_dpi

    ocr._ocr_image(SimpleNamespace(info={"dpi": (base, base)}), dpi=base // 2)

    assert captured["config"] == f"--dpi {base // 2}", "the passed dpi lost to the image metadata"


def test_a_rasterized_page_carries_the_dpi_it_was_rendered_at(monkeypatch):
    """WHEN pages are rasterized, THE SYSTEM SHALL stamp the render DPI onto each image.

    `_ocr_image` reads `image.info["dpi"]` to decide whether to declare the DPI to Tesseract, so a
    page that did not carry it would be OCR'd as though it were rendered at the base resolution -
    silently worse recognition on exactly the pages that were down-rendered.
    """
    rendered = [SimpleNamespace(info={}), SimpleNamespace(info={})]
    monkeypatch.setattr(ocr, "convert_from_path", lambda _p, **_k: rendered)
    _settings_stub(monkeypatch)

    out = ocr._rasterize("/x.pdf", first_page=1, last_page=2)

    assert [image.info["dpi"] for image in out] == [(ocr.get_settings().ocr_base_dpi,) * 2] * 2


def test_extract_text_from_image_ocrs_the_image_it_is_given(monkeypatch):
    """THE SYSTEM SHALL OCR an already-rasterized page without rasterizing anything.

    The entry point the verify pass uses: it holds an image already and must not touch the file.
    """
    monkeypatch.setattr(ocr, "_ocr_image", lambda image: f"text of {image}")

    assert ocr.extract_text_from_image("an-image") == "text of an-image"


def test_a_missing_binary_stops_the_page_loop_while_a_bad_page_is_skipped(monkeypatch):
    """IF OCR is unavailable, THEN the page loop SHALL propagate; any other failure SHALL be skipped.

    Both arms in one test because they are one decision. The loop's docstring states the `except`
    ORDER is load-bearing - `PdfUnreadableError` SUBCLASSES `OcrUnavailableError`, so the fail-fast
    arm must come first, and swapping them turns a configuration failure into a silently skipped
    page. The third assertion pins that subclass case specifically.
    """
    monkeypatch.setattr(ocr, "_configured", True)
    # Hoisted deliberately: a constructor inside a `pytest.raises` block is a second invocation, and
    # then the block no longer says WHICH call was expected to throw (python:S5778).
    one_page = [_Sentinel()]

    def unavailable(_image):
        raise OcrUnavailableError("tesseract is not installed")

    monkeypatch.setattr(ocr, "_ocr_image", unavailable)
    with pytest.raises(OcrUnavailableError):
        ocr._ocr_page_images(one_page, 1, 0, False)

    def subclassed(_image):
        raise PdfUnreadableError("the upload is truncated")

    monkeypatch.setattr(ocr, "_ocr_image", subclassed)
    with pytest.raises(PdfUnreadableError):
        ocr._ocr_page_images(one_page, 1, 0, False)

    answers = iter([RuntimeError("Tesseract process timeout"), "second page body"])

    def one_bad_then_good(_image):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ocr, "_ocr_image", one_bad_then_good)
    assert ocr._ocr_page_images([_Sentinel(), _Sentinel()], 1, 0, False) == "second page body"


def test_a_missing_binary_stops_selected_pages_while_one_bad_page_is_skipped(monkeypatch):
    """IF rasterizing is unavailable, THEN selected-page extraction SHALL propagate; a per-page
    rasterize failure SHALL skip that page and keep the rest.

    Same pair, a level up, and reached differently: here it is the RASTERIZE that fails rather than
    the OCR. Returning partial text on a config failure would store a truncated record as though it
    were complete, which nothing downstream can detect.
    """
    monkeypatch.setattr(ocr, "_configured", True)

    def unavailable(*_a, **_k):
        raise OcrUnavailableError("poppler is not installed")

    monkeypatch.setattr(ocr, "_rasterize", unavailable)
    with pytest.raises(OcrUnavailableError):
        ocr.extract_text_from_selected_pages("/x.pdf", [1, 2])

    def fail_page_one(_path, first_page, last_page):
        if first_page == 1:
            raise ValueError("page 1 is corrupt")
        return [_Page(first_page)]

    monkeypatch.setattr(ocr, "_rasterize", fail_page_one)
    monkeypatch.setattr(ocr, "_ocr_image", lambda image: f"body of {image.page}")

    assert ocr.extract_text_from_selected_pages("/x.pdf", [1, 2]) == "body of 2"


def test_the_report_marks_absolute_page_numbers_when_asked(monkeypatch):
    """WHERE mark_pages is set, the reported text SHALL carry each page's label.

    The offset is added to the label only; the report's own page list stays on real record pages, so
    a deposition labelled with its printed numbers is still reported by the numbers in our file.
    """
    monkeypatch.setattr(ocr, "_configured", True)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    monkeypatch.setattr(ocr, "_ocr_image", lambda image: f"body {image.page}")

    text, report = ocr.extract_pages_with_report(
        "/x.pdf", [1, 2], mark_pages=True, page_label_offset=10
    )

    assert "Page 11:\nbody 1\n" in text
    assert "Page 12:\nbody 2\n" in text
    assert report["pages"] == [1, 2], "the report must stay on real record pages"


def test_a_whole_document_rasterize_failure_returns_nothing_unless_it_is_a_config_failure(
    monkeypatch,
):
    """IF the whole-document rasterize is unavailable, THEN it SHALL propagate; any other failure
    SHALL return empty text rather than raise.

    The same pair once more, on the all-pages path. Empty text is the tolerable answer for an
    unreadable upload - a reviewer sees a document with no OCR - whereas a missing binary must stop,
    because every other document on the box would fail the same way and silently.
    """
    monkeypatch.setattr(ocr, "_configured", True)

    def unavailable(*_a, **_k):
        raise OcrUnavailableError("poppler is not installed")

    monkeypatch.setattr(ocr, "_rasterize", unavailable)
    with pytest.raises(OcrUnavailableError):
        ocr.extract_text_from_all_pages("/x.pdf")

    def corrupt(*_a, **_k):
        raise ValueError("the file is truncated")

    monkeypatch.setattr(ocr, "_rasterize", corrupt)
    assert ocr.extract_text_from_all_pages("/x.pdf") == ""


def test_a_missing_binary_stops_the_all_pages_loop(monkeypatch):
    """IF OCR becomes unavailable mid-document, THEN the all-pages loop SHALL propagate.

    Distinct from the rasterize arm above: rasterizing succeeded and the OCR call is what fails, so
    this is the one place the loop could have produced a document of empty pages instead of failing.
    """
    monkeypatch.setattr(ocr, "_configured", True)
    monkeypatch.setattr(ocr, "_rasterize", lambda *_a, **_k: [_Sentinel(), _Sentinel()])

    def unavailable(_image):
        raise OcrUnavailableError("tesseract vanished")

    monkeypatch.setattr(ocr, "_ocr_image", unavailable)

    with pytest.raises(OcrUnavailableError):
        ocr.extract_text_from_all_pages("/x.pdf")
