"""Check boxes on a form page, found and judged from the page's PIXELS - no model call.

Why this exists: on a scanned form the OCR text prints every choice whether or not its box is
ticked (a ticked and an empty box both come out as the same junk glyphs), and the model, shown the
page image, cannot reliably tell a ticked box from an empty one either - measured on the live box
on 2026-11-09 against the senior reviewer's report, a 2048px read named unticked procedures as
ticked in 12 of the 15 mistakes left, the same way on every read. A box, though, is a printed
square, and a tick is ink inside it: both can be measured exactly. So this module finds every small
square on the page, measures how much of its inside is inked, and pairs it with the printed words
to its right.
`summarize_engine._form_marks_parts` hands the result to the body call as a MARKED / NOT MARKED
list (`SUMMARY_FORM_MARKS`).

What it does NOT see, on purpose: a choice marked by circling it, by a hand-written mark beside it,
or a box whose outline did not survive the scan. Those choices are simply not in the list, and the
body call judges them from the images as it always has - the list never says anything about them.

Logs nothing: a label is the document's text. Callers log counts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import chain

import numpy as np
import pytesseract

from app.config import get_settings
from app.services import ocr

logger = logging.getLogger(__name__)

# The render the boxes are measured on. 200 dpi is the OCR base, so a box of the commonest printed
# size (0.10-0.15 in) is 20-30 px - large enough that a tick is many pixels and a speck is few.
FORM_BOX_DPI = 200

# A pixel is ink when darker than this (0 black, 255 white). Light enough to keep a faint pen
# stroke, dark enough that a grey scan background is paper.
_INK_LEVEL = 160

# The side of a check box, in inches. Smaller squares are letters ("o", "D") and dots; larger ones
# are table cells and photos.
_MIN_SIDE_IN = 0.08
_MAX_SIDE_IN = 0.30

# How much of each side of a candidate square must be inked for it to count as a box outline. A
# printed box is drawn whole; a scan can break a few pixels of it.
_EDGE_COVERAGE = 0.85

# How far, as a share of the box's side, a box may be from square.
_SQUARENESS = 0.15

# The thickest a box's outline may be, as a share of its side. A printed box is drawn with a thin
# line; the closed loop of a bold letter ("P", "D", "O") is a thick stroke round a small hole.
_MAX_STROKE = 0.22

# The inside of a box is measured inset from its outline by this share of its side, so the outline
# itself (and its blur in a scan) is never counted as a mark.
_INSET = 0.18

# A box is MARKED when at least this share of its inside is ink. On the reported form, rendered
# from a screenshot at about 80 dpi, the three ticked boxes measured 0.36-0.39 and all 52 empty
# ones 0.000. A tick drawn corner to corner covers about a sixth of the inside; specks of scan noise
# a few hundredths.
MARKED_INK = 0.08

# A label is the printed words that start within this many box sides to the right of the box.
_LABEL_REACH = 3.0

# A label stops at a gap wider than this many word heights, at the next box, or after these many
# lines and words.
_WORD_GAP = 1.6
_MAX_LABEL_LINES = 3
_MAX_LABEL_WORDS = 14


@dataclass(frozen=True)
class Box:
    """One printed check box: its outline in pixels (right and bottom exclusive) and its ink."""

    x0: int
    y0: int
    x1: int
    y1: int
    ink: float

    @property
    def side(self) -> int:
        return self.x1 - self.x0

    @property
    def marked(self) -> bool:
        return self.ink >= MARKED_INK


@dataclass(frozen=True)
class Word:
    """One OCR word and its box in pixels."""

    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def middle(self) -> float:
        return self.top + self.height / 2


def _horizontal_runs(ink: np.ndarray):
    """Every horizontal stretch of ink: (rows, first columns, end columns exclusive), row-major."""
    padded = np.pad(ink.astype(np.int8), ((0, 0), (1, 1)))
    step = np.diff(padded, axis=1)
    rows, starts = np.nonzero(step == 1)
    _, ends = np.nonzero(step == -1)
    return rows, starts, ends


def _column_coverage(ink: np.ndarray, top: int, bottom: int, x: int, reach: int) -> float:
    """Share of rows top..bottom with ink within ``reach`` columns of ``x``."""
    if bottom <= top:
        return 0.0
    band = ink[top:bottom, max(0, x - reach) : min(ink.shape[1], x + reach + 1)]
    return float(band.any(axis=1).mean())


def _row_coverage(ink: np.ndarray, y: int, left: int, right: int) -> float:
    """Share of columns left..right with ink on row ``y``."""
    if right <= left or not 0 <= y < ink.shape[0]:
        return 0.0
    return float(ink[y, left:right].mean())


def _bottom_edge(ink: np.ndarray, top: int, left: int, right: int) -> int | None:
    """The row that closes a square whose top edge runs left..right on ``top``, or None."""
    side = right - left
    best, best_cover = None, 0.0
    for y in range(top + int((1 - _SQUARENESS) * side), top + int((1 + _SQUARENESS) * side) + 1):
        cover = _row_coverage(ink, y, left, right)
        if cover >= _EDGE_COVERAGE and cover > best_cover:
            best, best_cover = y, cover
    return best


def _stroke(ink: np.ndarray, top: int, bottom: int, left: int, right: int) -> float:
    """The outline's thickness: the middle, over the box's middle rows, of the ink run each row
    starts with from its left edge and ends with at its right edge. A tick across the box adds to
    a few rows only, so the middle is the outline's own."""
    rows = ink[top + (bottom - top) // 4 : bottom - (bottom - top) // 4 + 1, left:right]
    if rows.size == 0:
        return 0.0
    blank = ~rows
    from_left = np.where(blank.any(axis=1), blank.argmax(axis=1), rows.shape[1])
    from_right = np.where(blank.any(axis=1), blank[:, ::-1].argmax(axis=1), rows.shape[1])
    return float(np.median(np.maximum(from_left, from_right)))


def _outline(ink: np.ndarray, top: int, left: int, right: int, reach: int):
    """The (x0, y0, x1, y1) of a box whose top edge is this run, or None when no thin-lined square
    closes it."""
    bottom = _bottom_edge(ink, top, left, right)
    if bottom is None:
        return None
    for x in (left, right - 1):
        if _column_coverage(ink, top, bottom, x, reach) < _EDGE_COVERAGE:
            return None
    if _stroke(ink, top, bottom, left, right) > _MAX_STROKE * (right - left):
        return None
    return left, top, right, bottom + 1


def _ink_inside(ink: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    """Share of a box's inside, inset from its outline, that is ink."""
    inset = max(2, int(round(_INSET * (x1 - x0))))
    inner = ink[y0 + inset : y1 - inset, x0 + inset : x1 - inset]
    return float(inner.mean()) if inner.size else 0.0


def _outlines_from_top_edges(ink: np.ndarray, shortest: int, longest: int, reach: int):
    """Every box outline whose TOP edge is one unbroken horizontal run of ink."""
    rows, starts, ends = _horizontal_runs(ink)
    lengths = ends - starts
    keep = (lengths >= shortest) & (lengths <= longest)
    for top, left, right in zip(rows[keep], starts[keep], ends[keep], strict=True):
        found = _outline(ink, int(top), int(left), int(right), reach)
        if found is not None:
            yield found


def find_boxes(gray: np.ndarray, dpi: int = FORM_BOX_DPI) -> list[Box]:
    """Every check box on a greyscale page image, top to bottom, with the ink inside it.

    A box is a square of side `_MIN_SIDE_IN`..`_MAX_SIDE_IN` whose four edges are inked. It is
    found from its top edge, and again from its bottom edge (the page searched upside down), so a
    scan that broke one horizontal edge still finds it. A thick outline is found once."""
    ink = np.asarray(gray) < _INK_LEVEL
    shortest = max(5, int(_MIN_SIDE_IN * dpi))
    longest = max(shortest, int(_MAX_SIDE_IN * dpi))
    reach = max(1, int(round(0.012 * dpi)))
    height = ink.shape[0]
    upside_down = (
        (x0, height - y1, x1, height - y0)
        for x0, y0, x1, y1 in _outlines_from_top_edges(ink[::-1], shortest, longest, reach)
    )
    near = 3 * reach + 2
    outlines: list[tuple[int, int, int, int]] = []
    for found in chain(_outlines_from_top_edges(ink, shortest, longest, reach), upside_down):
        if any(abs(found[0] - o[0]) <= near and abs(found[1] - o[1]) <= near for o in outlines):
            continue
        outlines.append(found)
    outlines.sort(key=lambda o: (o[1], o[0]))
    return [
        Box(x0, y0, x1, y1, round(_ink_inside(ink, x0, y0, x1, y1), 3))
        for x0, y0, x1, y1 in outlines
    ]


def _overlaps_a_box(word: Word, boxes: list[Box]) -> bool:
    """Whether an OCR word sits on a box - the glyph junk OCR makes of the box itself."""
    return any(
        word.left < b.x1 and word.right > b.x0 and word.top < b.y1 and word.bottom > b.y0
        for b in boxes
    )


def _same_line(a: Word, b: Word) -> bool:
    """Whether two words share a printed line (their vertical extents mostly overlap)."""
    overlap = min(a.bottom, b.bottom) - max(a.top, b.top)
    return overlap >= 0.5 * min(a.height, b.height)


def _box_ahead(boxes: list[Box], line: Word, start: int, stop: int) -> bool:
    """Whether another box sits on this word's line between columns start..stop."""
    return any(b.y0 < line.bottom and b.y1 > line.top and start <= b.x0 < stop for b in boxes)


def _line_from(first: Word, words: list[Word], boxes: list[Box]) -> list[Word]:
    """``first`` and the words after it on its line, up to a wide gap or the next box."""
    line = [first]
    for word in sorted(
        (w for w in words if w.left > first.left and _same_line(w, first)), key=lambda w: w.left
    ):
        gap = word.left - line[-1].right
        if gap > _WORD_GAP * _line_height(line):
            break
        if _box_ahead(boxes, word, line[-1].right, word.right):
            break
        line.append(word)
    return line


def _line_height(line: list[Word]) -> float:
    """A line's text height: the middle of its words' heights (a tall "/" or "(" does not count)."""
    heights = sorted(w.height for w in line)
    return float(heights[len(heights) // 2])


def _next_line(previous: list[Word], left: float, right: float, words, boxes) -> list[Word]:
    """The wrapped continuation of a label: the line just below, starting between columns
    left..right - unless a box starts that line (then it is the next choice).

    ``left`` reaches back under the box: forms print a wrapped label from the box's edge as often as
    from the label's first letter."""
    height = _line_height(previous)
    bottom = max(w.bottom for w in previous)
    below = [
        w
        for w in words
        if bottom - 0.2 * height <= w.top <= bottom + 0.9 * height and left <= w.left <= right
    ]
    if not below:
        return []
    first = min(below, key=lambda w: w.left)
    if any(
        b.y0 < first.bottom and b.y1 > first.top and left - 2 * height <= b.x0 <= first.left
        for b in boxes
    ):
        return []
    line = _line_from(first, words, boxes)
    if any(w.text.endswith(":") for w in line):
        return []  # a field of the form ("Time spent:", "Objective:"), not more of the label
    return line


def _label_for(box: Box, words: list[Word], boxes: list[Box]) -> str:
    """The printed words that belong to a box: from just right of it, wrapped lines included."""
    reach = box.x1 + _LABEL_REACH * box.side
    starts = [
        w
        for w in words
        if box.x1 - 0.2 * box.side <= w.left <= reach
        and box.y0 - 0.6 * box.side <= w.middle <= box.y1 + 0.6 * box.side
    ]
    if not starts:
        return ""
    # The line level with the box (a label is printed beside its box, not above or below it), then
    # that line's first word.
    centre = (box.y0 + box.y1) / 2
    level = min(starts, key=lambda w: abs(w.middle - centre))
    first = min((w for w in starts if _same_line(w, level)), key=lambda w: w.left)
    if _box_ahead(boxes, first, box.x1, first.left):
        return ""
    lines = [_line_from(first, words, boxes)]
    while len(lines) < _MAX_LABEL_LINES:
        height = _line_height(lines[-1])
        more = _next_line(
            lines[-1], min(box.x0, first.left) - height, first.left + height, words, boxes
        )
        if not more:
            break
        lines.append(more)
    text = " ".join(w.text for line in lines for w in line).split()
    return " ".join(text[:_MAX_LABEL_WORDS])


def label_boxes(boxes: list[Box], words: list[Word]) -> list[dict]:
    """``[{label, marked}]`` for every box that has printed words beside it, in page order."""
    readable = [w for w in words if w.text.strip() and not _overlaps_a_box(w, boxes)]
    choices = []
    for box in sorted(boxes, key=lambda b: (b.y0, b.x0)):
        label = _label_for(box, readable, boxes)
        if label:
            choices.append({"label": label, "marked": box.marked})
    return choices


def ocr_words(image, timeout: int) -> list[Word]:
    """Every word Tesseract reads on a page image, with its box. Sparse-text mode: a form is
    scattered labels, not paragraphs."""
    data = pytesseract.image_to_data(
        image,
        config=f"--psm 11 --dpi {FORM_BOX_DPI}",
        output_type=pytesseract.Output.DICT,
        timeout=timeout,
    )
    words = []
    for i, text in enumerate(data.get("text", [])):
        text = (text or "").strip()
        if not text or float(data["conf"][i]) < 0:
            continue
        words.append(
            Word(
                text,
                int(data["left"][i]),
                int(data["top"][i]),
                int(data["width"][i]),
                int(data["height"][i]),
            )
        )
    return words


def read_page(image, timeout: int) -> list[dict]:
    """The labelled check boxes of one rendered page; [] when it has none (no OCR call then)."""
    gray = np.asarray(image.convert("L"))
    boxes = find_boxes(gray, FORM_BOX_DPI)
    if not boxes:
        return []
    return label_boxes(boxes, ocr_words(image, timeout))


def read_check_boxes(pdf_path, start: int, end: int) -> list[dict]:
    """``[{label, marked}]`` for every labelled check box on pages start..end, in page order.

    Rendered through `ocr._rasterize`, which keeps its Poppler error mapping. Raises what rendering
    or OCR raises; the caller decides what a failed read means."""
    timeout = get_settings().ocr_timeout_seconds
    choices: list[dict] = []
    for page in range(int(start), int(end) + 1):
        for image in ocr._rasterize(pdf_path, dpi=FORM_BOX_DPI, first_page=page, last_page=page):
            choices.extend(read_page(image, timeout))
    return choices
