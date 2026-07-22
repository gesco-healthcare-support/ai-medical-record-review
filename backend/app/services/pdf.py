"""PDF utilities: file size and page count (pypdf).

The classic on-disk chunking helpers (segment_pdf / segment_pdf_locally) are dropped - they
wrote ~/MRRs artifacts for the single-user flow, which the re-platform replaces.
"""

import logging
import os

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def get_pdf_size(filepath: str) -> float:
    """Size of the PDF file in megabytes."""
    return os.path.getsize(filepath) / (1024 * 1024)


def get_pdf_page_count(pdf_file: str) -> int | None:
    """Total page count, or None if the file is not a readable PDF."""
    try:
        return len(PdfReader(pdf_file).pages)
    except Exception as exc:  # any parse failure means "not a usable PDF"
        logger.warning("could not read PDF page count: %s", exc)
        return None
