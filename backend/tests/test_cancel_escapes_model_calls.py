"""A reviewer's Stop is a signal, not a model failure.

JobCancelled is raised out of a cancellable sleep as a cooperative control-flow signal, meant to
unwind through the worker pool to `_run`'s handler. It subclasses Exception, so any call site that
wraps the model call in a broad `except Exception` swallows it: the job keeps running until the
next cancel check, the log blames the model for a deliberate user action, and that unit of work
silently takes its fallback.

WHICH LAYER RAISES IT DEPENDS ON THE CALL SITE, which is why the stubs below are not uniform. A
site still calling google-genai directly gets the signal from `generate_with_retry`; a site routed
through the provider seam gets it from the provider - on Gemini via the `generate_with_retry` it
wraps, on vLLM from its own sleep. Both unwind identically, so moving a service across the seam
changes what these tests STUB and not what they assert. The duplicate confirmation, the boundary
verification, the summary audit and the injury-date read are on the seam; only the deposition page
offset is not, yet.

`llm_classify` was fixed for this and pinned in test_classification.py. These are the other four
call sites with the same shape, found by walking the AST for model calls inside a broad catch
rather than by reading - which is why all four were missed the first time.

Each test DEMONSTRATES the bug: it fails on origin/main, where the signal is swallowed and the
function returns its fallback instead of raising.
"""

import pytest

from app.services import dedup, deposition_pages, summary_doi, summary_verify, verify_pass
from app.worker.failures import JobCancelled


def _cancels(*_args, **_kwargs):
    raise JobCancelled(3, 170)


def test_a_stop_escapes_the_duplicate_confirmation(monkeypatch):
    """Swallowed, this returned `list(members)` - every candidate confirmed as a duplicate on the
    strength of a call that never happened."""

    # Routed through the provider seam now, like the summary audit below - so the signal comes from
    # the provider's own cancellable sleep rather than from `generate_with_retry` directly.
    class _Provider:
        def generate_structured(self, *_a, **_k):
            raise JobCancelled(3, 170)

    monkeypatch.setattr(dedup, "provider_for_stage", lambda *_a, **_k: _Provider())
    members = [
        {"idx": 1, "title": "A", "date": "01/01/2020", "source_text": "x" * 50},
        {"idx": 2, "title": "A", "date": "01/01/2020", "source_text": "x" * 50},
    ]
    with pytest.raises(JobCancelled):
        dedup.confirm_cluster(members)


def test_a_stop_escapes_the_transcript_page_read(monkeypatch):
    """Swallowed, this returned None - the deposition shipped with no page citations at all, and
    the log said the page-number read had failed."""
    monkeypatch.setattr(deposition_pages, "generate_with_retry", _cancels)
    monkeypatch.setattr(deposition_pages, "get_genai_client", object)
    monkeypatch.setattr(deposition_pages, "PdfReader", lambda *_a, **_k: _FakeReader())
    monkeypatch.setattr(deposition_pages, "PdfWriter", _FakeWriter)
    with pytest.raises(JobCancelled):
        deposition_pages.transcript_page_offset("x.pdf", 1, 3)


def test_a_stop_escapes_the_injury_date_read(monkeypatch):
    """Swallowed, this returned "-" - the row shipped stating no injury date, which is
    indistinguishable from a document that genuinely carries none."""

    # Routed through the provider seam now, so the signal arrives from the provider's own
    # cancellable sleep rather than from `generate_with_retry` directly.
    class _Provider:
        def generate_text(self, *_a, **_k):
            raise JobCancelled(3, 170)

    monkeypatch.setattr(summary_doi, "provider_for_stage", lambda *_a, **_k: _Provider())
    monkeypatch.setattr(summary_doi, "PdfReader", lambda *_a, **_k: _FakeReader())
    monkeypatch.setattr(summary_doi, "PdfWriter", _FakeWriter)
    with pytest.raises(JobCancelled):
        summary_doi.extract_injury_date("x.pdf", 1, 3)


def test_a_stop_escapes_the_boundary_verification(monkeypatch):
    """Swallowed, this returned False - "these are different documents", so a real merge
    suggestion was dropped on the strength of a call that never happened."""

    # Routed through the provider seam now, so the signal arrives from the provider's own
    # cancellable sleep rather than from `generate_with_retry` directly.
    class _Provider:
        def generate_choice(self, *_a, **_k):
            raise JobCancelled(3, 170)

    monkeypatch.setattr(verify_pass, "provider_for_stage", lambda *_a, **_k: _Provider())
    monkeypatch.setattr(verify_pass, "_page_image", lambda *_a, **_k: object())
    monkeypatch.setattr(verify_pass, "_png_bytes", lambda *_a, **_k: b"png")
    monkeypatch.setattr(verify_pass, "_boundary_text", lambda *_a, **_k: "")
    with pytest.raises(JobCancelled):
        verify_pass._same_document(
            "x.pdf",
            {"start": 1, "end": 2, "category": "1", "date": "01/01/2020", "title": "A"},
            {"start": 3, "end": 4, "category": "1", "date": "01/01/2020", "title": "B"},
        )


def test_a_stop_escapes_the_summary_audit(monkeypatch):
    """The site the first audit MISSED. It calls the provider rather than
    `generate_with_retry`, and the provider raises the signal from its own cancellable sleep -
    so following one route into JobCancelled found four of five.

    Swallowed, this returned `_unverified(...)`: the audit recorded as failed and the
    unverified summary kept, on a call the reviewer had already stopped."""

    class _Provider:
        def generate_structured(self, *_a, **_k):
            raise JobCancelled(3, 170)

    monkeypatch.setattr(summary_verify, "get_provider", lambda *_a, **_k: _Provider())
    with pytest.raises(JobCancelled):
        summary_verify.verify_summary("m", "source text", "summary text")


class _FakePage:
    pass


class _FakeReader:
    """pypdf stands in so the test never touches a real PDF - the model call is the subject."""

    pages = [_FakePage() for _ in range(10)]


class _FakeWriter:
    def add_page(self, _page):
        pass

    def write(self, buffer):
        buffer.write(b"%PDF-1.4\n")
