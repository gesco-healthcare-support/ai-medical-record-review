"""PDF utilities: file size, page count, and a page range cut out as its own PDF (pypdf).

The classic on-disk chunking helpers (segment_pdf / segment_pdf_locally) are dropped - they
wrote ~/MRRs artifacts for the single-user flow, which the re-platform replaces.
"""

import io
import logging
import os
import threading
from collections import OrderedDict

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)

# One open reader per recent file, so cutting many pieces out of one record parses it ONCE.
# MEASURED 2026-10-16 on a 5,077-page, 480 MB record: a fresh PdfReader per cut cost 2.2-2.4s (pypdf
# reads the WHOLE file into memory on open, then flattens the page tree), against 0.01-0.04s per cut
# from a reader opened once - and the bytes are identical either way, so what a model is sent does
# not change. Gemini's segmentation windows, injury-date read and deposition read each cut once per
# window or row, so a long record paid it hundreds of times, holding the GIL, and briefly held one
# whole-file copy in memory per concurrent call.
#
# Keyed on (path, size, mtime) so a file replaced in place is opened again; a path that cannot be
# stat'ed is never cached. Two files, because each entry holds the whole file in memory and a
# worker works one record at a time. pypdf is not thread-safe, so every cut from a shared reader
# runs under that reader's own lock; the cache lock is held while a file is opened, so threads
# starting together on one record open it once rather than once each.
_READERS_CACHE_SIZE = 2
_readers: "OrderedDict[tuple, tuple[PdfReader, threading.Lock]]" = OrderedDict()
_readers_lock = threading.Lock()


def _reader_for(pdf_path) -> tuple[PdfReader, threading.Lock]:
    try:
        stat = os.stat(pdf_path)
        key = (os.fspath(pdf_path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return PdfReader(pdf_path), threading.Lock()
    with _readers_lock:
        entry = _readers.get(key)
        if entry is None:
            entry = (PdfReader(pdf_path), threading.Lock())
            _readers[key] = entry
            while len(_readers) > _READERS_CACHE_SIZE:
                _readers.popitem(last=False)
        else:
            _readers.move_to_end(key)
        return entry


def slice_pdf(pdf_path, first_page, last_page) -> bytes:
    """Pages ``first_page``..``last_page`` (1-based, inclusive) as a standalone PDF's bytes.

    Byte-identical to cutting the same pages from a freshly opened reader; only the cost differs.
    See `_readers`.
    """
    reader, lock = _reader_for(pdf_path)
    buffer = io.BytesIO()
    with lock:
        writer = PdfWriter()
        for page in range(int(first_page) - 1, int(last_page)):
            writer.add_page(reader.pages[page])
        writer.write(buffer)
    return buffer.getvalue()


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
