"""Page rendering + blank-page (ink-density) detection via PyMuPDF.

Shared by the free-cue survey (0a) and the Gemini oracles (0b). Documents are opened
once and cached so per-page rendering does not re-parse the PDF.
"""

import fitz
import numpy as np

_DOCS = {}


def get_doc(pdf_path):
    if pdf_path not in _DOCS:
        _DOCS[pdf_path] = fitz.open(pdf_path)
    return _DOCS[pdf_path]


def page_count(pdf_path):
    return get_doc(pdf_path).page_count


def render_png(pdf_path, page_number, dpi=150):
    """Return PNG bytes for a 1-indexed page at the given DPI (for inline Gemini input)."""
    page = get_doc(pdf_path)[page_number - 1]
    return page.get_pixmap(dpi=dpi).tobytes("png")


def ink_density(pdf_path, page_number, dpi=72, dark_below=250):
    """Fraction of non-white pixels on a 1-indexed page (low DPI grayscale).

    A near-zero value means a blank/near-blank page (a likely document separator).
    """
    page = get_doc(pdf_path)[page_number - 1]
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    return float((arr < dark_below).mean()) if arr.size else 0.0
