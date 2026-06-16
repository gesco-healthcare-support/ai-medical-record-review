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


# --- page-number resets ("Page X of Y") ------------------------------------------------------

_PAGE_OF = re.compile(r"page\s+(\d{1,3})\s+of\s+(\d{1,4})", re.IGNORECASE)


def _page_number(text):
    """Return (x, y) from the last 'Page X of Y' on the page, or None."""
    matches = _PAGE_OF.findall(text or "")
    if not matches:
        return None
    x, y = matches[-1]
    return int(x), int(y)


def starts_from_page_numbers(pages_text):
    """Predict a start when the per-document page counter resets or its total changes.

    pages_text: list indexed 0..n-1 (page p = pages_text[p-1]).
    """
    starts = {1}
    prev = None
    for idx, text in enumerate(pages_text):
        page = idx + 1
        cur = _page_number(text)
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
SIM_BOUNDARY_MAX = 0.35  # similarity below this between consecutive bands => boundary


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


def starts_from_header_change(pages_text):
    """Predict a start where the header/footer band differs sharply from the previous page."""
    starts = {1}
    prev_band = None
    for idx, text in enumerate(pages_text):
        page = idx + 1
        band = _band(text)
        if prev_band is not None and band and prev_band:
            if _similarity(prev_band, band) < SIM_BOUNDARY_MAX:
                starts.add(page)
        prev_band = band
    return starts
