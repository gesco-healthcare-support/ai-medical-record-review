"""A deposition printed CONDENSED, four transcript pages to each scanned sheet.

Reviewer report 2026-10-08: such a deposition was never summarized. The printed-number read expects
one printed number per scanned page and found four, its reply was cut off on every attempt, and the
row failed. Had it run, the OCR would have handed the model each sheet as ONE page, its four pages in
the order 1, 3, 2, 4 with nothing between them - so groups of ten "pages" would have been forty.

Synthetic only: the sheets are drawn here, and every OCR call is a stub that returns made-up text.
Tesseract never runs, so these pin the logic around it - where a sheet is cut, how its quarters are
numbered, what each page is labelled - not the recognizer itself.
"""

import pytest
from PIL import Image, ImageDraw

from app.errors import OcrUnavailableError, TranscriptPagesUnreadableError
from app.services import ocr
from app.services import summarize_engine as se

_NO_ISSUES = {"fixed_text": "", "issues": [], "ok": True}

# A sheet at 100 dpi: the grid's frame, middle row line and middle column line where a condensed
# transcript draws them. Thin lines, as on the measured record.
_W, _H = 850, 1100
_TOP, _BOTTOM, _X, _Y = 86, 1021, 418, 553


def _sheet(*, middle_row=True, middle_column=True, frame=True, half_line=False):
    image = Image.new("L", (_W, _H), 255)
    draw = ImageDraw.Draw(image)
    if frame:
        draw.rectangle([20, _TOP, _W - 20, _TOP + 1], fill=0)
        draw.rectangle([20, _BOTTOM, _W - 20, _BOTTOM + 1], fill=0)
    if middle_row:
        draw.rectangle([20, _Y, _W - 20, _Y + 1], fill=0)
    if middle_column:
        draw.rectangle([_X, _TOP, _X + 1, _BOTTOM], fill=0)
    if half_line:
        # A line across ONE quarter, like a reporter's signature line - not a grid line.
        draw.rectangle([450, 560, 820, 561], fill=0)
    return image


# --- finding the grid ------------------------------------------------------------------------------


def test_a_condensed_sheet_is_cut_on_its_grid_lines():
    x, y, top, bottom = ocr._grid_cuts(_sheet())
    assert x in (_X, _X + 1)
    assert y in (_Y, _Y + 1)
    assert top in (_TOP, _TOP + 1), "the running header above the frame stays out of the quarters"
    assert bottom in (_BOTTOM, _BOTTOM + 1), "and so does the reporter's footer below it"


def test_without_a_frame_the_quarters_run_to_the_sheet_edge():
    assert ocr._grid_cuts(_sheet(frame=False))[2:] == (0, _H)


@pytest.mark.parametrize(
    "sheet",
    [
        pytest.param(_sheet(middle_row=False, middle_column=False), id="an ordinary page"),
        # The record's cover sheet: one rule across the middle and no column line.
        pytest.param(_sheet(middle_column=False), id="a single middle rule"),
        pytest.param(_sheet(middle_row=False), id="a column line alone"),
        # A line across one quarter only reaches half the sheet's width.
        pytest.param(_sheet(middle_row=False, half_line=True), id="a signature line"),
    ],
)
def test_a_sheet_without_a_full_grid_is_read_as_one_page(sheet):
    assert ocr._grid_cuts(sheet) is None


def test_a_line_across_one_quarter_is_not_taken_for_the_middle_line():
    # The signature line sits nearer the middle than nothing at all; it must not move the cut.
    assert ocr._grid_cuts(_sheet(half_line=True))[1] in (_Y, _Y + 1)


def test_anything_that_is_not_an_image_is_no_grid():
    assert ocr._grid_cuts("not an image") is None


# --- numbering a sheet's quarters ------------------------------------------------------------------


def test_a_quarter_label_is_a_line_holding_only_the_page_number():
    assert ocr._quarter_label("Q. and then\nPage 12\nA. yes") == 12
    assert ocr._quarter_label("A. I read it on page 12 of the report") is None
    assert ocr._quarter_label("Page 7\nPage 9") is None, "two different labels prove nothing"
    assert ocr._quarter_label(None) is None


@pytest.mark.parametrize(
    ("labels", "numbers"),
    [
        # Quarters in the fixed order top-left, bottom-left, top-right, bottom-right.
        pytest.param([None, 2, 3, 4], [1, 2, 3, 4], id="first sheet: the cover has no label"),
        pytest.param([21, 22, 23, 24], [21, 22, 23, 24], id="down then across"),
        pytest.param([5, 7, 6, 8], [5, 7, 6, 8], id="across then down"),
        pytest.param([37, 38, 99, None], [37, 38, 39, 40], id="one misread is outvoted"),
    ],
)
def test_a_sheet_is_numbered_from_the_labels_that_agree(labels, numbers):
    assert ocr._sheet_numbering(labels) == numbers


@pytest.mark.parametrize(
    "labels",
    [
        pytest.param([None, None, 3, None], id="one label"),
        pytest.param([5, 9, 13, 2], id="no two agree"),
        pytest.param([5, 6, 9, 10], id="a tie"),
        # Top-left and bottom-right are N and N+3 in either order, so they cannot say which.
        pytest.param([5, None, None, 8], id="only the corners"),
        pytest.param([None, None, None, None], id="no labels"),
    ],
)
def test_a_sheet_whose_labels_do_not_establish_a_numbering_is_refused(labels):
    assert ocr._sheet_numbering(labels) is None


# --- reading a sheet's quarters ----------------------------------------------------------------------


def _quarters(monkeypatch, answers):
    """`_ocr_image` answering each read in turn: top-left, bottom-left, top-right, bottom-right.

    An Exception class in ``answers`` is raised for that read instead. A quarter is read once more
    after a failure (retries=1), so a quarter that never reads is listed twice."""
    sizes, queue = [], list(answers)

    def fake(image, dpi=None):
        sizes.append(image.size)
        answer = queue.pop(0)
        if isinstance(answer, type) and issubclass(answer, Exception):
            raise answer("tesseract timed out")
        return answer

    monkeypatch.setattr(ocr, "_ocr_image", fake)
    return sizes


def test_a_condensed_sheet_comes_back_as_its_transcript_pages_in_order(monkeypatch):
    sizes = _quarters(
        monkeypatch,
        ["CAPTION OF THE CASE", "Page 2\nQ. two", "Page 3\nQ. three", "Page 4\nA. four"],
    )
    pages = ocr._condensed_quarters(_sheet())

    assert [number for number, _ in pages] == [1, 2, 3, 4]
    assert pages[0][1] == "CAPTION OF THE CASE"
    assert "Q. two" in pages[1][1]
    assert all("Page" not in text for _, text in pages), "the marker carries the number now"
    # Each quarter is a quarter: the crops are the grid's four cells, not the whole sheet.
    assert all(width < _W and height < _H for width, height in sizes)


def test_an_across_then_down_sheet_is_returned_in_page_order(monkeypatch):
    _quarters(monkeypatch, ["Page 5\nTL", "Page 7\nBL", "Page 6\nTR", "Page 8\nBR"])
    pages = ocr._condensed_quarters(_sheet())
    assert [(number, text.strip()) for number, text in pages] == [
        (5, "TL"),
        (6, "TR"),
        (7, "BL"),
        (8, "BR"),
    ]


def test_the_empty_quarters_after_the_transcript_ends_are_left_out(monkeypatch):
    _quarters(monkeypatch, ["Page 41\nQ. last", "Page 42\nA. end", "", "   "])
    assert [number for number, _ in ocr._condensed_quarters(_sheet())] == [41, 42]


def test_a_quarter_whose_ocr_failed_is_kept_as_unreadable(monkeypatch):
    _quarters(monkeypatch, ["Page 9\nA", "Page 10\nB", "Page 11\nC", RuntimeError, RuntimeError])
    pages = ocr._condensed_quarters(_sheet(), retries=1)
    assert pages[-1] == (12, None), "numbered from its neighbours, with no text"


def test_a_missing_tesseract_still_fails_fast_from_a_quarter(monkeypatch):
    def missing(image, dpi=None):
        raise OcrUnavailableError("Tesseract not found")

    monkeypatch.setattr(ocr, "_ocr_image", missing)
    with pytest.raises(OcrUnavailableError):
        ocr._condensed_quarters(_sheet())


def test_a_grid_whose_labels_do_not_agree_is_read_as_one_page(monkeypatch):
    # A table drawn with a middle cross is a grid with no page labels.
    _quarters(monkeypatch, ["cell", "cell", "cell", "cell"])
    assert ocr._condensed_quarters(_sheet()) is None


def test_a_sheet_with_no_grid_reads_no_quarter(monkeypatch):
    monkeypatch.setattr(ocr, "_ocr_image", lambda *a, **k: pytest.fail("no quarter to read"))
    assert ocr._condensed_quarters(_sheet(middle_row=False, middle_column=False)) is None


# --- the deposition extractor ------------------------------------------------------------------------


def _row_of_sheets(monkeypatch, sheets, broken=()):
    """Record pages -> what `_condensed_quarters` returns for them (None reads the page whole).

    A page in ``broken`` cannot be rasterized at all, on every attempt."""
    monkeypatch.setattr(ocr, "_configured", True)

    def rasterize(path, first_page, last_page, **_kw):
        if first_page in broken:
            raise RuntimeError("poppler could not render this page")
        return [first_page]

    monkeypatch.setattr(ocr, "_rasterize", rasterize)
    monkeypatch.setattr(ocr, "_condensed_quarters", lambda image, retries=1: sheets.get(image))
    monkeypatch.setattr(ocr, "_ocr_image", lambda image, dpi=None: f"WHOLE SHEET {image}")


_FOUR = [(1, "p1"), (2, "p2"), (3, "p3"), (4, "p4")]
_NEXT_FOUR = [(5, "p5"), (6, "p6"), (7, "p7"), (8, "p8")]


def test_every_transcript_page_gets_its_own_printed_number(monkeypatch):
    _row_of_sheets(monkeypatch, {11: _FOUR, 12: _NEXT_FOUR})
    text, report, numbers = ocr.extract_condensed_transcript("/x.pdf", [10, 11, 12, 13])

    assert [line for line in text.splitlines() if line.startswith("Page ")] == [
        f"Page {n}:" for n in range(1, 9)
    ], "eight transcript pages from two sheets, in order"
    assert text.startswith(ocr.FRONT_MATTER_MARKER + "\nWHOLE SHEET 10\n"), "the cover sheet"
    assert text.endswith(ocr.UNNUMBERED_MARKER + "\nWHOLE SHEET 13\n"), "an exhibit after it"
    assert "Page 10:" not in text and "Page 13:" not in text, "no record page reads as a citation"
    assert report == {"pages": [10, 11, 12, 13], "errored": [], "blank": []}
    assert numbers == ocr.TranscriptNumbers(condensed=True, unreadable=[])


def test_a_lost_quarter_is_reported_in_both_numberings(monkeypatch):
    lost = [(5, "p5"), (6, None), (7, "p7"), (8, "p8")]
    _row_of_sheets(monkeypatch, {11: _FOUR, 12: lost})
    text, report, numbers = ocr.extract_condensed_transcript("/x.pdf", [11, 12])

    assert "Page 6:" not in text, (
        "an unread page gets no marker, as an unread record page never did"
    )
    assert report["errored"] == [12], "the record page, for the reviewer's row tooling"
    assert numbers.unreadable == [6], "the transcript page, for the notice"


def test_a_sheet_lost_whole_cannot_be_stated_in_transcript_numbers(monkeypatch):
    _row_of_sheets(monkeypatch, {11: _FOUR, 12: _NEXT_FOUR}, broken={12})
    _, report, numbers = ocr.extract_condensed_transcript("/x.pdf", [11, 12])

    assert report["errored"] == [12]
    assert numbers == ocr.TranscriptNumbers(condensed=True, unreadable=None)


def test_with_no_condensed_sheet_the_text_is_exactly_the_ordinary_marked_read(monkeypatch):
    _row_of_sheets(monkeypatch, {})
    text, report, numbers = ocr.extract_condensed_transcript("/x.pdf", [10, 11])
    plain_text, plain_report = ocr.extract_pages_with_report("/x.pdf", [10, 11], mark_pages=True)

    assert (text, report) == (plain_text, plain_report)
    assert numbers == ocr.TranscriptNumbers(condensed=False, unreadable=None)


# --- the check in front of the printed-number read ---------------------------------------------------


def test_the_check_finds_a_grid_on_any_of_the_first_sheets(monkeypatch):
    sheets = {3: _sheet()}
    blank = _sheet(middle_row=False, middle_column=False)
    monkeypatch.setattr(
        ocr, "_rasterize", lambda p, first_page, last_page: [sheets.get(first_page, blank)]
    )
    assert ocr.has_condensed_sheets("/x.pdf", 1, 40) is True


def test_the_check_looks_no_further_than_the_printed_number_read_would(monkeypatch):
    seen = []
    blank = _sheet(middle_row=False, middle_column=False)

    def rasterize(p, first_page, last_page):
        seen.append(first_page)
        return [_sheet() if first_page == 7 else blank]

    monkeypatch.setattr(ocr, "_rasterize", rasterize)
    assert ocr.has_condensed_sheets("/x.pdf", 1, 40) is False
    assert seen == [1, 2, 3, 4, 5, 6]


def test_the_check_is_fail_safe(monkeypatch):
    def unreadable(*_a, **_kw):
        raise ocr.PdfUnreadableError("cannot read the PDF")

    monkeypatch.setattr(ocr, "_rasterize", unreadable)
    assert ocr.has_condensed_sheets("/x.pdf", 1, 4) is False


# --- what the summary is told and cites --------------------------------------------------------------


def _condensed_row(monkeypatch, *, numbers, errored=(), text=None):
    """A deposition row the check finds condensed, with every model seam stubbed."""
    calls = []
    marked = text if text is not None else "".join(f"Page {n}:\nQ. A.\n" for n in range(1, 9))

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        calls.append(system_msg)
        if system_msg == se.TITLE_PROMPT:
            return "DEPOSITION OF THE APPLICANT", False
        return "On pages 1 to 8, asked about the injury; stated it happened at work.", False

    def no_offset_read(*_a, **_kw):
        raise TranscriptPagesUnreadableError("the printed-number read must not run")

    report = {"pages": [10, 11, 12], "errored": list(errored), "blank": []}
    monkeypatch.setattr(se, "has_condensed_sheets", lambda *a, **k: True)
    monkeypatch.setattr(
        se, "extract_condensed_transcript", lambda path, pages: (marked, report, numbers)
    )
    monkeypatch.setattr(se, "extract_pages_with_report", lambda *a, **k: pytest.fail("not this"))
    monkeypatch.setattr(se, "transcript_page_offset", no_offset_read)
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    return calls


def _deposition():
    return {
        "start": 10,
        "end": 12,
        "category": "9",
        "date": "01/31/2012",
        "injury_date": "-",
        "flag": "",
    }


def test_a_condensed_deposition_is_summarized_without_the_printed_number_read(monkeypatch):
    # The reported failure: that read raised on every attempt, so the row was never summarized.
    calls = _condensed_row(monkeypatch, numbers=ocr.TranscriptNumbers(True, []))
    out = se.summarize_row("/x.pdf", _deposition(), prompt="P")

    body_system = next(system for system in calls if system != se.TITLE_PROMPT)
    assert "ARE this transcript's own printed page numbers" in body_system
    assert "printed condensed, four transcript pages to each scanned sheet" in body_system
    assert out["summaryText"].startswith("On pages 1 to 8")
    assert out["sourceText"].count("Page ") == 8


def test_a_condensed_deposition_notice_cites_the_transcript_page(monkeypatch):
    _condensed_row(monkeypatch, numbers=ocr.TranscriptNumbers(True, [6]), errored=[11])
    out = se.summarize_row("/x.pdf", _deposition(), prompt="P")

    assert out["summaryText"].endswith(se.partial_unreadable_notice([6]))
    assert out["unreadablePages"] == [11], "record pages stay what the row tooling matches"


def test_a_notice_falls_back_to_record_pages_when_a_whole_sheet_was_lost(monkeypatch):
    _condensed_row(monkeypatch, numbers=ocr.TranscriptNumbers(True, None), errored=[11])
    out = se.summarize_row("/x.pdf", _deposition(), prompt="P")
    assert out["summaryText"].endswith(se.partial_unreadable_notice([11]))


def test_a_grid_with_no_readable_numbering_cites_nothing(monkeypatch):
    # The check saw a grid but no sheet could be numbered: the markers are record pages, and the
    # printed-number read was skipped, so nothing may be cited.
    calls = _condensed_row(
        monkeypatch,
        numbers=ocr.TranscriptNumbers(False, None),
        text="Page 10:\nQ.\nPage 11:\nA.\n",
    )
    se.summarize_row("/x.pdf", _deposition(), prompt="P")
    body_system = next(system for system in calls if system != se.TITLE_PROMPT)
    assert "Do NOT write any page number" in body_system


def test_an_ordinary_deposition_still_reads_its_printed_numbers(monkeypatch):
    seen = {}

    def extract(path, pages, mark_pages=False, **kw):
        seen["offset"] = kw.get("page_label_offset")
        return "Page 1:\nQ.\n", {"pages": pages, "errored": [], "blank": []}

    monkeypatch.setattr(se, "has_condensed_sheets", lambda *a, **k: False)
    monkeypatch.setattr(se, "extract_condensed_transcript", lambda *a, **k: pytest.fail("not this"))
    monkeypatch.setattr(se, "transcript_page_offset", lambda *a, **k: -9)
    monkeypatch.setattr(se, "extract_pages_with_report", extract)
    monkeypatch.setattr(se, "_generate", lambda *a, **k: ("On pages 1 to 3, asked.", False))
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    se.summarize_row("/x.pdf", _deposition(), prompt="P")
    assert seen["offset"] == -9


def test_the_condensed_sentence_is_only_for_a_condensed_transcript():
    assert "printed condensed" not in se._deposition_pages_block(-417)
    assert "printed condensed" in se._deposition_pages_block(None, condensed=True)
    assert "Do NOT write any page number" in se._deposition_pages_block(None)
