"""markitdown page -> markdown conversion for Solution 3 (markitdown is mandatory here).

These records are SCANNED (no text layer), so markitdown's default pdfminer path returns
blank -- it must use the markitdown-ocr plugin (LLM-vision OCR). The OCR backend (an
OpenAI-compatible vision LLM) must be wired at run time; with the current free-tier Gemini /
credit-less OpenAI keys it cannot run, so Solution 3 is exercised only once a paid LLM path
exists. Imports are lazy so the other three solutions never depend on markitdown.
"""

import os
import tempfile

from pypdf import PdfReader, PdfWriter

_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        from markitdown import MarkItDown

        # enable_plugins picks up markitdown-ocr. TODO(run-time): pass a working vision LLM
        # client/model (paid Gemini via its OpenAI-compatible endpoint, or OpenAI) so scanned
        # pages actually OCR; without it scanned pages convert to empty markdown.
        _converter = MarkItDown(enable_plugins=True)
    return _converter


def page_markdown(pdf_path, page_number):
    """Convert one 1-indexed page to markdown via markitdown (+ OCR plugin for scans)."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.add_page(reader.pages[page_number - 1])
    tmp = os.path.join(tempfile.gettempdir(), f"pss_md_{page_number}.pdf")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    return _get_converter().convert(tmp).text_content
