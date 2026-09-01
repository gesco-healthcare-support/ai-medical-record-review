"""Deposition page grouping: the transcript-page offset, the marker labels, and the audit guard.

The thing under test is a two-numbering problem. A transcript prints its OWN page numbers; our OCR
markers count pages of the whole scanned record. Citing the second as though it were the first sends a
reviewer to the wrong page, so the offset between them is discovered once and the markers relabelled -
and when it cannot be discovered, nothing is cited at all.

Pure: no Vertex, no database. ``_offset_from`` is exercised directly with model-shaped payloads, and
the OCR label is exercised with the rasterize/OCR steps stubbed.
"""

import pytest

from app.services import deposition_pages as dp
from app.services import ocr
from app.services import summarize_engine as se

# --- offset agreement ----------------------------------------------------------------------------


def _payload(*pairs):
    """A model reply: (position among the attached pages, printed number)."""
    return {"pages": [{"i": i, "printed": printed} for i, printed in pairs]}


def test_two_agreeing_pages_establish_the_offset():
    # WHEN the printed numbers agree on one offset, THE SYSTEM SHALL return it. A deposition at record
    # pages 418-460 whose first pages print 1, 2, 3 has offset -417.
    data = _payload((1, 1), (2, 2), (3, 3))
    assert dp._offset_from(data, start=418, last=423) == -417


def test_a_single_readable_page_is_not_enough():
    # WHEN only one page yields a number, THE SYSTEM SHALL refuse. One page cannot show that the
    # transcript is sequentially paginated from where we think it starts - a cover page, an index, or
    # inserted exhibits all break a constant offset, and one reading cannot detect that.
    data = _payload((1, 1), (2, 0), (3, 0))
    assert dp._offset_from(data, start=418, last=423) is None


def test_disagreeing_offsets_are_refused_rather_than_guessed():
    # WHEN the readings imply different offsets, THE SYSTEM SHALL return None. Producing no citation is
    # correct here; a wrong one is trusted and cannot be found.
    data = _payload((1, 1), (2, 50))
    assert dp._offset_from(data, start=418, last=423) is None


def test_two_offsets_supported_equally_are_refused_rather_than_guessed():
    """A 2-2 tie is not a majority, and picking one was ORDER-DEPENDENT.

    `max` returns the first maximal element, and the order is whatever sequence the model listed the
    pages in - so identical readings produced different citations on different calls. Reachable on a
    real transcript: an appearance/cover block carrying its own numbering, two of its pages and two
    body pages inside the six-page sample.

    The existing `test_disagreeing_offsets_are_refused_rather_than_guessed` covers 1-vs-1, where
    neither candidate reaches `_MIN_AGREEING`. This is the case where BOTH do.
    """
    cover_first = {
        "pages": [
            {"i": 1, "printed": 1},  # cover block  -> offset -99
            {"i": 2, "printed": 2},
            {"i": 3, "printed": 1},  # body         -> offset -101
            {"i": 4, "printed": 2},
        ]
    }
    body_first = {
        "pages": [
            {"i": 3, "printed": 1},
            {"i": 4, "printed": 2},
            {"i": 1, "printed": 1},
            {"i": 2, "printed": 2},
        ]
    }
    assert dp._offset_from(cover_first, start=100, last=105) is None
    assert dp._offset_from(body_first, start=100, last=105) is None


def test_a_clear_majority_still_wins_over_a_tied_pair():
    """The tie guard must not swallow a genuine majority: 3 agreeing beats 2 that agree with each
    other, and that offset is still returned."""
    data = {
        "pages": [
            {"i": 1, "printed": 1},  # -99, seen twice
            {"i": 2, "printed": 2},
            {"i": 3, "printed": 3},  # -99 again -> three
            {"i": 4, "printed": 1},  # -102, seen twice
            {"i": 5, "printed": 2},
        ]
    }
    assert dp._offset_from(data, start=100, last=105) == -99


def test_the_majority_offset_wins_when_a_reading_is_stray():
    # WHEN most pages agree and one is misread, THE SYSTEM SHALL take the agreeing offset.
    data = _payload((1, 1), (2, 2), (3, 900))
    assert dp._offset_from(data, start=418, last=423) == -417


def test_pages_showing_no_number_are_skipped_not_treated_as_zero():
    # A cover or appearance page reports 0. THE SYSTEM SHALL ignore it rather than deriving an offset
    # from it, which would put every citation hundreds of pages out.
    # Positions 3 and 4 are record pages 102 and 103, printing 4 and 5, so the offset is -98.
    data = _payload((1, 0), (2, 0), (3, 4), (4, 5))
    assert dp._offset_from(data, start=100, last=105) == -98
    assert 102 + -98 == 4  # spelling the arithmetic out, because I got it wrong once


def test_a_reading_outside_the_attached_range_is_ignored():
    # A position the model invented cannot be mapped to a record page, so it must not vote.
    data = _payload((1, 1), (99, 1))
    assert dp._offset_from(data, start=418, last=423) is None


def test_a_malformed_entry_does_not_break_the_read():
    data = {"pages": [{"i": "x", "printed": None}, {"i": 1, "printed": 1}, {"i": 2, "printed": 2}]}
    assert dp._offset_from(data, start=10, last=15) == -9


def test_an_empty_or_missing_payload_returns_none():
    assert dp._offset_from({}, start=1, last=3) is None
    assert dp._offset_from({"pages": []}, start=1, last=3) is None
    assert dp._offset_from(None, start=1, last=3) is None


# --- marker labels -------------------------------------------------------------------------------


@pytest.fixture
def _stub_ocr(monkeypatch):
    """Stub rasterize + OCR so the label logic is tested without Poppler or Tesseract."""
    monkeypatch.setattr(ocr, "_rasterize", lambda path, first_page, last_page: ["image"])
    monkeypatch.setattr(ocr, "_ocr_image", lambda image: "TESTIMONY TEXT")


def test_markers_default_to_the_record_page(_stub_ocr):
    # WHEN no offset is given, THE SYSTEM SHALL label markers with the record page, byte-identically to
    # before - every existing caller depends on this.
    text = ocr.extract_text_from_selected_pages("/x.pdf", [418, 419], mark_pages=True)
    assert "Page 418:" in text
    assert "Page 419:" in text


def test_an_offset_relabels_markers_with_transcript_pages(_stub_ocr):
    # WHEN an offset is given, THE SYSTEM SHALL label markers with the transcript's own numbers, so the
    # prompt can cite the numbers it is handed and do no arithmetic.
    text = ocr.extract_text_from_selected_pages(
        "/x.pdf", [418, 419], mark_pages=True, page_label_offset=-417
    )
    assert "Page 1:" in text
    assert "Page 2:" in text
    assert "Page 418:" not in text


def test_an_offset_is_ignored_when_markers_are_off(_stub_ocr):
    # The offset labels markers; with markers off there is nothing to label.
    text = ocr.extract_text_from_selected_pages("/x.pdf", [418], page_label_offset=-417)
    assert "Page" not in text


# --- what the prompt is told the markers mean ----------------------------------------------------


def test_a_known_offset_tells_the_model_to_cite_the_markers():
    block = se._deposition_pages_block(-417)
    assert "ARE this transcript's own printed page numbers" in block
    assert "Cite them" in block


def test_an_unknown_offset_forbids_citing_any_page():
    # This is the fail-safe that matters: with no offset the markers are OUR page numbers, and citing
    # them would look like a transcript page and be wrong.
    block = se._deposition_pages_block(None)
    assert "NOT" in block
    assert "Do NOT write any page number" in block


# --- the audit guard -----------------------------------------------------------------------------


_RAW = (
    "On pages 4 to 6, asked to state her name; affirmed.\n"
    "On pages 7 to 9, asked about her duties; stated lifting up to forty pounds.\n"
    "On pages 10 to 12, asked about treatment; stated physical therapy twice weekly."
)


def test_the_guard_rejects_a_rewrite_that_flattens_the_grouping():
    # WHEN the audit returns fewer paragraphs than the raw body, THE SYSTEM SHALL reject it: the page
    # grouping is how a reviewer finds the testimony, and a flattened body loses that permanently.
    flattened = "The deponent was asked her name, her duties and her treatment, and answered."
    assert se._drops_deposition_structure(_RAW, flattened) is True


def test_the_guard_rejects_a_rewrite_that_drops_page_citations():
    # Same paragraph count, citations gone. No faithfulness finding justifies deleting a page
    # reference - it is a pointer to the source, not a claim about the medicine.
    stripped = (
        "Asked to state her name; affirmed.\n"
        "Asked about her duties; stated lifting up to forty pounds.\n"
        "Asked about treatment; stated physical therapy twice weekly."
    )
    assert se._drops_deposition_structure(_RAW, stripped) is True


def test_the_guard_allows_a_genuine_correction():
    # WHEN the audit preserves the paragraphs and their ranges, THE SYSTEM SHALL accept the rewrite -
    # rewording inside a paragraph is exactly what the audit exists to do.
    corrected = (
        "On pages 4 to 6, asked to state her name; affirmed.\n"
        "On pages 7 to 9, asked about her duties; stated lifting up to twenty pounds.\n"
        "On pages 10 to 12, asked about treatment; stated physical therapy twice weekly."
    )
    assert se._drops_deposition_structure(_RAW, corrected) is False


def test_the_guard_allows_a_two_page_final_group():
    # The last group may cover two pages, written "On pages 34 and 35," - the guard must accept that
    # joiner or it would reject every transcript whose length is not a multiple of three.
    raw = "On pages 4 to 6, asked her name.\nOn pages 7 and 8, asked her address."
    fixed = "On pages 4 to 6, asked her full name.\nOn pages 7 and 8, asked her home address."
    assert se._drops_deposition_structure(raw, fixed) is False
