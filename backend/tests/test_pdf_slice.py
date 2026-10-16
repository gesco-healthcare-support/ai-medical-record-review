"""Gemini is sent a slice of the record's PDF; the record must be opened once, not once per slice.

Measured 2026-10-16 on a 5,077-page, 480 MB record: a fresh PdfReader per slice cost 2.2-2.4s (pypdf
reads the whole file into memory, then flattens the page tree) against 0.01-0.04s per slice from a
reader opened once, with byte-identical output. Gemini's segmentation windows, injury-date read and
deposition read each cut one slice per window or row. See `app.services.pdf._readers`.
"""

import hashlib
import io
import threading
import time

import pypdf
import pytest
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import deposition_pages, pdf, segment_engine, summary_doi


@pytest.fixture(autouse=True)
def _no_open_readers():
    """The reader cache is module state; no test may see another test's files."""
    pdf._readers.clear()
    yield
    pdf._readers.clear()


def _record(path, pages=12, width=612):
    """A real PDF whose pages differ in size, so a cut of the wrong pages cannot pass."""
    writer = PdfWriter()
    for n in range(pages):
        writer.add_blank_page(width=width + n, height=792)
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _fresh_cut(path, first, last):
    """What the three callers did before: a new reader for every cut."""
    reader = PdfReader(path)
    writer = PdfWriter()
    for page in range(first - 1, last):
        writer.add_page(reader.pages[page])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _count_opens(monkeypatch, delay=0.0):
    """Count every PdfReader construction, whichever module's name for the class was used."""
    opened = []
    real_init = pypdf.PdfReader.__init__

    def _init(self, *args, **kwargs):
        opened.append(args[0] if args else None)
        time.sleep(delay)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(pypdf.PdfReader, "__init__", _init)
    return opened


def test_a_cut_is_byte_identical_to_a_fresh_reader(tmp_path):
    """GUARD. What Gemini is sent must not change - only what it costs to produce."""
    path = _record(tmp_path / "record.pdf")

    for first, last in ((1, 1), (3, 7), (10, 12)):
        ours = pdf.slice_pdf(path, first, last)
        assert (
            hashlib.sha256(ours).digest() == hashlib.sha256(_fresh_cut(path, first, last)).digest()
        )
        assert len(PdfReader(io.BytesIO(ours)).pages) == last - first + 1


def test_gemini_slices_open_the_record_once(monkeypatch, tmp_path):
    """WHEN Gemini's three readers cut many slices of one record, THE SYSTEM SHALL open it once."""
    path = _record(tmp_path / "record.pdf")
    settings = get_settings()
    assert settings.backend_for("segment") == "gemini"
    opened = _count_opens(monkeypatch)

    segment_engine._window_parts(path, 1, 6, settings)
    segment_engine._window_parts(path, 5, 12, settings)
    for start in (2, 4, 6, 8):
        summary_doi._isolated_parts(path, start, start + 1, settings)
    deposition_pages._isolated_parts(path, 9, 11, settings)

    assert len(opened) == 1


def test_slices_cut_at_the_same_time_open_the_record_once(monkeypatch, tmp_path):
    """WHEN a pool starts on one record, THE SYSTEM SHALL open it once and cut correct slices.

    pypdf is not thread-safe; the cuts share one reader under its lock, and each must still match a
    cut made alone."""
    path = _record(tmp_path / "record.pdf")
    expected = {start: _fresh_cut(path, start, start + 1) for start in range(1, 9)}
    opened = _count_opens(monkeypatch, delay=0.05)
    gate = threading.Barrier(8)
    got = {}

    def _cut(start):
        gate.wait()
        got[start] = pdf.slice_pdf(path, start, start + 1)

    threads = [threading.Thread(target=_cut, args=(start,)) for start in range(1, 9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(opened) == 1
    assert got == expected


def test_a_file_replaced_in_place_is_opened_again(monkeypatch, tmp_path):
    """GUARD. Keyed on size and mtime as well as the path, so a new file under an old name is never
    cut from the old file's reader."""
    path = _record(tmp_path / "record.pdf", pages=4)
    pdf.slice_pdf(path, 1, 1)
    _record(tmp_path / "record.pdf", pages=6, width=700)
    opened = _count_opens(monkeypatch)

    cut = pdf.slice_pdf(path, 6, 6)

    assert len(opened) == 1
    assert len(PdfReader(io.BytesIO(cut)).pages) == 1


def test_a_path_that_cannot_be_stated_is_never_cached(monkeypatch):
    """GUARD. Nothing to key it on, so it is opened every time - as every cut was before."""
    opened = _count_opens(monkeypatch)

    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            pdf.slice_pdf("/no/such/record.pdf", 1, 1)

    assert len(opened) == 2
    assert not pdf._readers


def test_at_most_two_records_stay_open(tmp_path):
    """GUARD. Each open reader holds its whole file in memory, so the oldest is let go."""
    paths = [_record(tmp_path / f"r{n}.pdf", pages=2) for n in range(pdf._READERS_CACHE_SIZE + 2)]
    for path in paths:
        pdf.slice_pdf(path, 1, 1)

    assert len(pdf._readers) == pdf._READERS_CACHE_SIZE
    assert {key[0] for key in pdf._readers} == set(paths[-pdf._READERS_CACHE_SIZE :])
