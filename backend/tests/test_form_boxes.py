"""Check boxes found and judged from page pixels (`app.services.form_boxes`).

Synthetic pages only: boxes, ticks and specks drawn with Pillow at the module's 200 dpi, and OCR
words given by hand, so no test needs Tesseract or Poppler.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services import form_boxes as fb
from app.services.form_boxes import Box, Word

SIDE = 26  # a 0.13 in box at 200 dpi


def _page(marks, size=(1700, 400), side=SIDE, width=3):
    """A white page with one box per (x, y, mark); returns the greyscale array."""
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    for x, y, mark in marks:
        draw.rectangle([x, y, x + side - 1, y + side - 1], outline=0, width=width)
        if mark == "tick":
            draw.line(
                [(x + 6, y + side // 2), (x + side // 2 - 1, y + side - 7), (x + side - 6, y + 6)],
                fill=0,
                width=3,
            )
        elif mark == "cross":
            draw.line([(x + 6, y + 6), (x + side - 7, y + side - 7)], fill=0, width=3)
            draw.line([(x + 6, y + side - 7), (x + side - 7, y + 6)], fill=0, width=3)
        elif mark == "filled":
            draw.rectangle([x, y, x + side - 1, y + side - 1], fill=0)
        elif mark == "speck":
            draw.point([(x + side // 2, y + side // 2), (x + side // 2 + 1, y + side // 2)], fill=0)
    return np.asarray(img)


def _by_x(boxes):
    return sorted(boxes, key=lambda b: (b.y0, b.x0))


# --- finding boxes -------------------------------------------------------------------------------


def test_demonstrates_every_box_is_found_and_a_mark_inside_it_is_measured():
    page = _page([(100, 100, None), (300, 100, "tick"), (500, 100, "cross"), (900, 100, "speck")])
    boxes = _by_x(fb.find_boxes(page))
    assert [b.x0 for b in boxes] == [100, 300, 500, 900]
    assert [b.marked for b in boxes] == [False, True, True, False]
    assert boxes[0].ink == 0.0


def test_guard_a_solid_square_is_not_a_box():
    """A solid square is as likely a bullet as a filled-in box, so it is not judged here: a choice
    printed with one is left out of the list, and the images decide it as they do without it."""
    assert fb.find_boxes(_page([(700, 100, "filled")])) == []


def test_guard_the_closed_loop_of_a_bold_letter_is_not_a_box():
    """Measured on a synthetic scan: the bowl of a bold "P" in a heading closed into a 17 x 14 px
    square with a small hole, and was read as a ticked box. A box's outline is thin for its size."""
    img = Image.new("L", (300, 200), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 50, 120, 70], outline=0, width=6)  # a loop of stroke 6 round a 9 px hole
    draw.rectangle([100, 70, 105, 95], fill=0)  # the stem below it
    assert fb.find_boxes(np.asarray(img)) == []


def test_demonstrates_a_thick_outline_is_one_box_not_one_per_row_of_its_edge():
    boxes = fb.find_boxes(_page([(100, 100, None)], width=5))
    assert len(boxes) == 1


@pytest.mark.parametrize("side", [10, 80])
def test_guard_squares_too_small_or_too_large_for_a_check_box_are_not_boxes(side):
    """A 0.05 in square is a letter's size; a 0.4 in one is a table cell."""
    assert fb.find_boxes(_page([(100, 100, None)], side=side)) == []


def test_guard_a_bracket_or_an_underline_is_not_a_box():
    img = Image.new("L", (800, 300), 255)
    draw = ImageDraw.Draw(img)
    draw.line([(100, 100), (126, 100)], fill=0, width=3)  # an underline of a box's width
    draw.line([(300, 100), (300, 126)], fill=0, width=3)  # "[" - three sides
    draw.line([(300, 100), (326, 100)], fill=0, width=3)
    draw.line([(300, 126), (326, 126)], fill=0, width=3)
    assert fb.find_boxes(np.asarray(img)) == []


def test_guard_a_rectangle_far_from_square_is_not_a_box():
    img = Image.new("L", (800, 300), 255)
    ImageDraw.Draw(img).rectangle([100, 100, 125, 140], outline=0, width=3)
    assert fb.find_boxes(np.asarray(img)) == []


def test_demonstrates_a_box_scanned_with_small_breaks_in_its_outline_is_still_found():
    page = _page([(100, 100, None)]).copy()
    page[100:103, 110:112] = 255  # two columns of the top edge lost
    assert len(fb.find_boxes(page)) == 1


def test_demonstrates_grey_paper_is_not_ink():
    page = _page([(100, 100, None)]).copy()
    page[page == 255] = 200  # a grey scan background
    (box,) = fb.find_boxes(page)
    assert not box.marked


def test_guard_the_marked_threshold_separates_a_few_specks_from_a_tick():
    assert fb.MARKED_INK > 0.01
    assert fb.MARKED_INK < 0.15


# --- labels --------------------------------------------------------------------------------------


def _box(x, y, ink=0.0, side=SIDE):
    return Box(x, y, x + side, y + side, ink)


def _word(text, left, top, width=None, height=22):
    return Word(text, left, top, width or 14 * len(text), height)


def test_demonstrates_a_box_takes_the_words_to_its_right_on_its_line():
    boxes = [_box(100, 100, ink=0.3), _box(600, 100)]
    words = [
        _word("97110", 140, 102),
        _word("Therapeutic", 220, 102),
        _word("97530", 640, 102),
        _word("Activities", 720, 102),
    ]
    assert fb.label_boxes(boxes, words) == [
        {"label": "97110 Therapeutic", "marked": True},
        {"label": "97530 Activities", "marked": False},
    ]


def test_demonstrates_a_label_wrapped_onto_the_next_line_is_kept_whole():
    boxes = [_box(100, 100, ink=0.3)]
    words = [_word("97110", 140, 102), _word("Therapeutic", 220, 102), _word("Exercises", 140, 128)]
    assert fb.label_boxes(boxes, words) == [
        {"label": "97110 Therapeutic Exercises", "marked": True}
    ]


def test_demonstrates_a_wrapped_line_that_starts_under_the_box_is_part_of_the_label():
    """On the reported form a long label wraps back under its box ("97140 Manual Therapy /" then
    "Myofascial Release / Joint" then "Mobilization", each starting left of the label's first
    letter), and must stay one choice."""
    boxes = [_box(100, 100, ink=0.0)]
    words = [
        _word("97140", 140, 102),
        _word("Manual", 230, 102),
        _word("Myofascial", 93, 130),
        _word("Release", 250, 130),
        _word("Mobilization", 93, 158),
    ]
    assert fb.label_boxes(boxes, words) == [
        {"label": "97140 Manual Myofascial Release Mobilization", "marked": False}
    ]


def test_guard_a_field_line_under_a_label_is_not_part_of_it():
    """A form prints a field under each choice ("Time spent: minutes") and section headings below
    the last one ("Objective:"); a line holding a field name is not more of the label."""
    boxes = [_box(100, 100, ink=0.3)]
    words = [
        _word("97110", 140, 102),
        _word("Therapeutic", 220, 102),
        _word("Exercises", 140, 128),
        _word("Time", 93, 154),
        _word("Spent:", 160, 154),
        _word("minutes", 260, 154),
    ]
    assert fb.label_boxes(boxes, words) == [
        {"label": "97110 Therapeutic Exercises", "marked": True}
    ]


def test_guard_the_next_line_is_not_taken_when_it_starts_with_its_own_box():
    boxes = [_box(100, 100, ink=0.3), _box(100, 128)]
    words = [_word("Headache", 140, 102), _word("Abdominal", 140, 130), _word("Pain", 290, 130)]
    assert fb.label_boxes(boxes, words) == [
        {"label": "Headache", "marked": True},
        {"label": "Abdominal Pain", "marked": False},
    ]


def test_guard_a_label_stops_at_the_next_box_on_its_line():
    """Tightly packed choices: the next box comes sooner than a gap wide enough to end a label."""
    boxes = [_box(100, 100), _box(180, 100, ink=0.5)]
    words = [_word("Hip", 132, 98, height=30), _word("Knee", 212, 98, height=30)]
    assert fb.label_boxes(boxes, words) == [
        {"label": "Hip", "marked": False},
        {"label": "Knee", "marked": True},
    ]


def test_guard_ocr_junk_read_from_the_box_itself_is_not_part_of_a_label():
    """OCR reads a box as glyphs ("[J", or "]" for its right edge) where a label may start."""
    boxes = [_box(100, 100, ink=0.3)]
    words = [
        _word("[J", 98, 100, width=20),
        _word("]", 122, 100, width=12),
        _word("Lumbar", 140, 102),
        _word("Pain", 240, 102),
    ]
    assert fb.label_boxes(boxes, words) == [{"label": "Lumbar Pain", "marked": True}]


def test_guard_a_box_with_no_words_beside_it_is_left_out():
    boxes = [_box(100, 100, ink=0.3), _box(100, 300)]
    words = [_word("Lumbar", 140, 102)]
    assert fb.label_boxes(boxes, words) == [{"label": "Lumbar", "marked": True}]


def test_guard_words_far_to_the_right_are_not_a_box_label():
    boxes = [_box(100, 100)]
    words = [_word("Elsewhere", 100 + SIDE + 5 * SIDE, 102)]
    assert fb.label_boxes(boxes, words) == []


def test_guard_a_wide_gap_ends_a_label():
    boxes = [_box(100, 100)]
    words = [_word("Other", 140, 102, width=60), _word("Comments", 140 + 60 + 200, 102)]
    assert fb.label_boxes(boxes, words) == [{"label": "Other", "marked": False}]


def test_demonstrates_a_form_that_prints_each_label_before_its_box_is_read_from_the_left():
    """Measured on the live box: a few forms print "Gait [ ]  Massage [x]". Read from the right,
    every box got its neighbour's label and the marks landed on the wrong treatments."""
    boxes = [_box(200, 100), _box(500, 100, ink=0.4), _box(200, 140, ink=0.4)]
    words = [
        _word("Gait", 120, 102),
        _word("Massage", 380, 102),
        _word("Hot", 110, 142),
        _word("pack", 160, 142, width=30),
    ]
    assert fb.labels_on_the_left(boxes, words)
    assert fb.label_boxes(boxes, words) == [
        {"label": "Gait", "marked": False},
        {"label": "Massage", "marked": True},
        {"label": "Hot pack", "marked": True},
    ]


def test_guard_the_previous_column_on_the_left_does_not_make_a_form_left_labelled():
    """On an ordinary form the words left of a box are the previous column's label, a column gap
    away; its own label is a space away on the right."""
    boxes = [_box(100, 100), _box(400, 100)]
    words = [_word("Headache", 140, 102), _word("Shoulder", 440, 102)]
    assert not fb.labels_on_the_left(boxes, words)


# --- the page read -------------------------------------------------------------------------------


def test_demonstrates_a_page_without_boxes_makes_no_ocr_call(monkeypatch):
    called = []
    monkeypatch.setattr(fb, "ocr_words", lambda *a, **k: called.append(1) or [])
    blank = Image.new("RGB", (800, 600), "white")
    assert fb.read_page(blank, timeout=5) == []
    assert called == []


def test_demonstrates_a_page_read_pairs_its_boxes_with_the_ocr_words(monkeypatch):
    img = Image.fromarray(_page([(100, 100, "tick"), (600, 100, None)])).convert("RGB")
    monkeypatch.setattr(
        fb,
        "ocr_words",
        lambda image, timeout: [_word("Massage", 140, 102), _word("Gait", 640, 102)],
    )
    assert fb.read_page(img, timeout=5) == [
        {"label": "Massage", "marked": True},
        {"label": "Gait", "marked": False},
    ]


def test_demonstrates_every_page_of_the_row_is_read_in_order(monkeypatch):
    rendered = []

    def render(pdf_path, **kwargs):
        rendered.append((kwargs["first_page"], kwargs["dpi"]))
        return [f"page{kwargs['first_page']}"]

    monkeypatch.setattr(fb.ocr, "_rasterize", render)
    monkeypatch.setattr(fb, "read_page", lambda image, timeout: [{"label": image, "marked": False}])
    assert fb.read_check_boxes("x.pdf", 15, 16) == [
        {"label": "page15", "marked": False},
        {"label": "page16", "marked": False},
    ]
    assert rendered == [(15, fb.FORM_BOX_DPI), (16, fb.FORM_BOX_DPI)]


def test_demonstrates_ocr_words_keeps_real_words_with_their_boxes(monkeypatch):
    data = {
        "text": ["", "Lumbar", " ", "Pain"],
        "conf": ["-1", "91", "-1", "88.5"],
        "left": [0, 140, 0, 240],
        "top": [0, 102, 0, 102],
        "width": [0, 90, 0, 50],
        "height": [0, 22, 0, 22],
    }
    seen = {}

    def image_to_data(image, config, output_type, timeout):
        seen.update(config=config, timeout=timeout)
        return data

    monkeypatch.setattr(fb.pytesseract, "image_to_data", image_to_data)
    assert fb.ocr_words("img", timeout=7) == [
        Word("Lumbar", 140, 102, 90, 22),
        Word("Pain", 240, 102, 50, 22),
    ]
    assert "--psm 11" in seen["config"]
    assert seen["timeout"] == 7
