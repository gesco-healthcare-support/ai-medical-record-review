"""Free (no-Gemini) document-boundary cues for the Phase 0 survey (0a).

Each detector returns the set of pages it predicts as a document START. We then score that
set against the gold starts to learn how many true boundaries each cue catches for free, and
how many false starts it invents. Text cues read the existing Tesseract OCR cache (so quality
is coupled to OCR -- a known caveat); the blank cue reads pixels directly.
"""

import re

import images

# --- blank / separator pages -----------------------------------------------------------------

BLANK_INK_MAX = 0.005  # < 0.5% dark pixels => effectively blank (tuneable)


def blank_pages(pdf_path, n, dpi=72):
    """Pages that are blank/near-blank (likely separators)."""
    return {p for p in range(1, n + 1) if images.ink_density(pdf_path, p, dpi=dpi) < BLANK_INK_MAX}


def starts_after_blank(pdf_path, n):
    """Predict a new document starts on the page AFTER each blank separator."""
    blanks = blank_pages(pdf_path, n)
    return {p + 1 for p in blanks if p + 1 <= n}


# --- page-number resets -----------------------------------------------------------------------
#
# A per-document page counter that resets ("...Page 1 of 8" after "...Page 8 of 8") is the
# strongest free boundary cue we have. Two detector grammars, chosen by an OCR-cache survey +
# a band/grammar sweep (2026-06-16):
#   STRICT = "Page X of Y" only (the word "page" anchors it) -> high precision (0.60-0.85 on the
#            clean cases). This is the tier Solution 4 trusts as a fixed pre-cut.
#   BROAD  = STRICT + bare "X of Y" -> higher recall (lifts the case where "X of Y" is the common
#            footer form: Case 2 0.62->0.73 recall) at a little precision; used for the
#            recall-oriented candidate union, not as a pre-cut.
# Rejected by evidence: a FOOTER-only band (whole-text scans recall MORE -- page numbers sit
# outside the last 3 lines often enough that restricting hurts), and "Page X" with no total
# (it was global Bates pagination 1..N across a whole record, never per-document, so it marked
# no boundaries and only cost precision). x <= y guards against "5 of 3 copies"-type noise.

_PAGE_OF = re.compile(r"page\s+(\d{1,4})\s+of\s+(\d{1,4})", re.IGNORECASE)  # "Page X of Y"
_X_OF_Y = re.compile(r"(?<!\d)(\d{1,4})\s+of\s+(\d{1,4})(?!\d)", re.IGNORECASE)  # bare "X of Y"


def _page_number(text, broad=False):
    """Return (x, y) for the last valid page counter on the page (x <= y required), or None.
    broad=False matches only "Page X of Y"; broad=True also accepts bare "X of Y"."""
    grammars = (_PAGE_OF, _X_OF_Y) if broad else (_PAGE_OF,)
    for rx in grammars:
        for m in reversed(list(rx.finditer(text or ""))):
            x, y = int(m.group(1)), int(m.group(2))
            if x <= y:
                return x, y
    return None


def starts_from_page_numbers(pages_text, broad=False):
    """Predict a start when the per-document page counter resets or its total changes.

    Reset = the counter restarts at 1 or fails to advance (x <= previous x); total change = a
    different "of Y" than the previous page. broad=False is the high-precision pre-cut detector;
    broad=True trades precision for recall in the candidate union.
    pages_text: list indexed 0..n-1 (page p = pages_text[p-1]).
    """
    starts = {1}
    prev = None  # (x, y) of the previous page that carried a counter
    for idx, text in enumerate(pages_text):
        page = idx + 1
        cur = _page_number(text, broad=broad)
        if cur is None:
            continue
        x, y = cur
        if prev is not None:
            px, py = prev
            if x == 1 or x <= px or y != py:  # reset or different document length
                starts.add(page)
        elif x == 1:
            starts.add(page)
        prev = cur
    return starts


# --- header / footer (letterhead) similarity -------------------------------------------------

HEADER_LINES = 3
FOOTER_LINES = 3
# Similarity below this between consecutive header/footer bands => candidate boundary. Swept on
# the 3 clean cases (2026-06-16): clean F1 is flat from 0.35-0.50 but recall climbs monotonically
# (0.47 -> 0.58); since this cue is a recall-oriented candidate generator that Gemini confirms,
# 0.45 buys ~+0.07 clean / +0.12 ROR recall at the same precision (~0.24).
SIM_BOUNDARY_MAX = 0.45


def _band(text):
    """Normalized header+footer text band of a page (letterhead signature)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    band = " ".join(lines[:HEADER_LINES] + lines[-FOOTER_LINES:])
    band = re.sub(r"[^a-z\s]", " ", band.lower())  # drop digits/punct (page numbers, dates)
    return re.sub(r"\s+", " ", band).strip()


def _similarity(a, b):
    from difflib import SequenceMatcher

    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def starts_from_header_change(pages_text, threshold=SIM_BOUNDARY_MAX):
    """Predict a start where the header/footer band differs sharply from the previous page.

    `threshold` is the similarity below which consecutive bands count as a boundary; exposed so
    tune_cues.py can sweep it. This cue is a candidate generator (tuned for recall), not a
    decision -- Gemini confirms its proposals.
    """
    starts = {1}
    prev_band = None
    for idx, text in enumerate(pages_text):
        page = idx + 1
        band = _band(text)
        if prev_band is not None and band and prev_band:
            if _similarity(prev_band, band) < threshold:
                starts.add(page)
        prev_band = band
    return starts
