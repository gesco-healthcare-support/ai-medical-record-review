"""What the transcript page-number read actually SENDS, and what it does when the reply is cut off.

`test_deposition_pages.py` pins the reply PARSING - `_offset_from`'s agreement rule, the marker
relabelling, the audit guard - across twenty tests. Every one of them calls `_offset_from` or
`_deposition_pages_block` directly. So the REQUEST this function builds had never been pinned by
anything: not the page count reaching the model, not the cap, not the schema, not the thinking
budget, not which model answers.

READ THE DIFF ON THIS FILE, not only its current state. The first commit pinned the request as
google-genai received it, before any routing existed; this version asserts the same guarantees
through the provider seam, and adds the truncation contract that routing made possible. Where an
assertion changed, the request changed - and only `max_output_tokens` was meant to.

A REAL synthetic PDF is written to tmp_path rather than stubbing `PdfReader`, because the page cap
is one of the things being pinned and a fake reader cannot demonstrate it: the bytes are read back
and the pages counted. Synthetic blank pages only - never a real record.
"""

import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.errors import TranscriptPagesUnreadableError
from app.services import deposition_pages as dp
from app.services.llm.gemini import to_gemini_schema
from app.services.llm.parts import DocumentPart, ImagePart, TextPart

# The schema as google-genai received it BEFORE routing, frozen here rather than imported.
#
# Asserting against `dp._SCHEMA` would compare the module to itself and pass however that constant
# changes. This copy is the independent record: `_SCHEMA` is lowercase now for the seam, so the test
# runs it back through `to_gemini_schema` and requires the result to equal exactly what Gemini used
# to be sent. That is what makes "the Gemini request is unchanged" a checkable claim.
_FROZEN_GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "pages": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "i": {
                        "type": "INTEGER",
                        "description": "1-based position among attached pages",
                    },
                    "printed": {"type": "INTEGER", "description": "Printed page number, or 0"},
                },
                "required": ["i", "printed"],
            },
        }
    },
    "required": ["pages"],
}


def _blank_pdf(path, pages):
    """A SYNTHETIC PDF of `pages` blank pages. Never a real record."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _reply(count):
    """A well-formed answer for `count` attached pages, offset +100 and unanimous."""
    return json.dumps({"pages": [{"i": i, "printed": 100 + i} for i in range(1, count + 1)]})


def _stub_provider(monkeypatch, captured, reply=None, truncated=False):
    """Route `deposition_pages.provider_for_stage` to a stub recording what the service asked for.

    Returns a dict carrying the stage the SERVICE handed the resolver. A resolver stub that accepts
    anything swallows a wrong stage silently - and a wrong stage here means the thinking budget
    resolves to 0, which a thinking model rejects with a 400 that this fail-safe function cannot
    report.
    """
    asked = {}

    class _Provider:
        def generate_structured(self, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return SimpleNamespace(
                text=reply if reply is not None else _reply(6), truncated=truncated
            )

    def _resolver(stage, *_a, **_k):
        asked["stage"] = stage
        return _Provider()

    monkeypatch.setattr(dp, "provider_for_stage", _resolver)
    return asked


def _sent_pdf_bytes(captured):
    """The PDF bytes of the single document part, read back off the request."""
    return captured["parts"][0].data


def test_the_call_sends_one_pdf_part_then_the_prompt(tmp_path, monkeypatch):
    """WHEN the stage resolves to gemini, THE SYSTEM SHALL send one PDF document part carrying the
    sub-document's pages, followed by `_PROMPT`.

    Order is pinned because the prompt says "The attached pages are consecutive pages from ONE
    deposition" - it refers backwards to the part before it.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply=_reply(4))
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 12)

    assert dp.transcript_page_offset(pdf, 3, 6) == 98

    parts = captured["parts"]
    assert len(parts) == 2
    assert isinstance(parts[0], DocumentPart)
    assert parts[0].mime_type == "application/pdf"
    # Pages 3..6 inclusive is four, and the bytes really are a PDF carrying exactly those.
    assert len(PdfReader(io.BytesIO(_sent_pdf_bytes(captured))).pages) == 4
    assert isinstance(parts[1], TextPart)
    assert parts[1].text == dp._PROMPT


def test_the_page_cap_bounds_a_long_transcript(tmp_path, monkeypatch):
    """WHEN the span exceeds `_MAX_PAGES`, THE SYSTEM SHALL send only the first `_MAX_PAGES` pages.

    The cap keeps the payload small AND stays inside `summary_image_max_pages` (15), so every page
    asked about is one the model can actually see rather than one it would guess at from OCR.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 40)

    dp.transcript_page_offset(pdf, 1, 30)

    sent = PdfReader(io.BytesIO(_sent_pdf_bytes(captured)))
    assert len(sent.pages) == dp._MAX_PAGES == 6


def test_the_schema_still_reaches_gemini_exactly_as_it_did_before_routing(tmp_path, monkeypatch):
    """WHEN the stage resolves to gemini, THE SYSTEM SHALL send the schema it always sent.

    `_SCHEMA` is lowercase now because the seam takes neutral JSON Schema. This runs it back through
    the translator Gemini's provider uses and requires the result to equal the frozen literal - so
    the lowercasing is proven to be a spelling change and not a request change.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2)

    assert to_gemini_schema(captured["schema"]) == _FROZEN_GEMINI_SCHEMA
    # Control: the neutral form really is different, or the translation proves nothing.
    assert captured["schema"] != _FROZEN_GEMINI_SCHEMA


def test_the_call_carries_its_cap_temperature_model_and_resolves_through_the_deposition_stage(
    tmp_path, monkeypatch
):
    """WHEN transcript_page_offset runs, THE SYSTEM SHALL send the configured cap, temperature 0.0
    and the deposition-stage model, and SHALL resolve BOTH transport and model through `deposition`.

    `asked["stage"]` is the half no config-level test can see, and here it carries more than usual:
    the stage selects `thinking_for("deposition")`, so a wrong string silently drops the budget to 0
    - the exact failure that would make every transcript lose its citations without a word in any
    log.

    The cap is asserted against the SETTING rather than a literal, because it is deliberately
    env-overridable; asserting 2048 here would pin the default instead of the wiring.
    """
    captured = {}
    asked = _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2)

    settings = get_settings()
    assert asked["stage"] == "deposition"
    assert captured["stage"] == "deposition"
    assert captured["model"] == settings.model_for_stage("deposition")
    assert captured["temperature"] == 0.0
    assert captured["max_output_tokens"] == settings.deposition_max_output_tokens
    assert captured["system"] is None
    # The budget the stage resolves to must be the one this call used to pass explicitly.
    assert settings.thinking_for("deposition") == settings.summary_thinking_budget


def test_the_model_is_the_general_gemini_model_not_the_summary_one(tmp_path, monkeypatch):
    """WHEN no model is passed, THE SYSTEM SHALL answer with the general model, not summary_model.

    `genai_model` and `summary_model` are DIFFERENT models, and this read has always used the
    general one. Pinned because the model now resolves through a STAGE, and a stage resolving to the
    wrong family would change which model reads the page numbers without changing anything a reader
    would notice.

    THIS TEST DOES NOT HOLD THE GUARANTEE ITS NAME CLAIMS, and a reader should know which one does.
    On Gemini `model_for_stage("deposition")` and `genai_model` resolve to the SAME STRING, so
    replacing the stage call with the raw setting leaves this test green. Found by mutation probe:
    the only test that dies is `test_a_vllm_backend_sends_page_images_at_the_deposition_render_target`,
    via its `captured["model"] == "served-by-the-pod/model"` assertion, because the two strings part
    company only once a backend overrides the model. Do not delete that assertion believing it is
    incidental to a rasterisation test - it is the one pinning that the model resolves through the
    stage at all.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2)

    settings = get_settings()
    assert captured["model"] == settings.genai_model
    assert captured["model"] != settings.summary_model


def test_an_explicit_model_overrides_the_default(tmp_path, monkeypatch):
    """WHEN a caller passes `model`, THE SYSTEM SHALL use it verbatim."""
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2, model="an-explicitly-chosen-model")

    assert captured["model"] == "an-explicitly-chosen-model"


def test_a_truncated_reply_raises_rather_than_reporting_no_page_numbers(tmp_path, monkeypatch):
    """IF the reply is truncated, THEN the system SHALL raise TranscriptPagesUnreadableError.

    THE POINT OF THE ROUTING. `None` means "this transcript has no establishable pagination", and a
    truncated read used to produce exactly that value - so the summary cited nothing and no one
    could tell whether there had been page numbers to read. The seam reports `truncated`; the direct
    google-genai call could not see it.

    Adrian's decision on 2026-09-16, over the recommendation to keep the unconditional "never
    raises". The typed error is what keeps that from being a regression - see
    `test_one_unreadable_transcript_does_not_discard_the_rest_of_the_bundle`.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply="", truncated=True)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    with pytest.raises(TranscriptPagesUnreadableError, match="truncated"):
        dp.transcript_page_offset(pdf, 1, 2)


def test_the_truncation_raise_survives_the_fail_safe_catch(tmp_path, monkeypatch):
    """The raise above happens INSIDE the try, so the broad `except Exception` would swallow it.

    Not a duplicate of the test above: that one would pass if the raise were moved outside the try
    and the seam call left unguarded. This asserts the specific thing that makes the guard real -
    that `TranscriptPagesUnreadableError` is named in the re-raise clause beside `JobCancelled`,
    exactly as that flag needed when the same broad catch swallowed IT.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply="", truncated=True)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    with pytest.raises(TranscriptPagesUnreadableError):
        dp.transcript_page_offset(pdf, 1, 2)

    # CONTROL: an ordinary read failure is still absorbed, so the clause above is narrow rather than
    # having quietly turned this fail-safe function into a raising one.
    class _Provider:
        def generate_structured(self, **_kwargs):
            raise RuntimeError("vertex is unhappy")

    monkeypatch.setattr(dp, "provider_for_stage", lambda *_a, **_k: _Provider())
    assert dp.transcript_page_offset(pdf, 1, 2) is None


def test_an_untruncated_unparseable_reply_is_still_the_fail_safe_none(tmp_path, monkeypatch):
    """WHEN the reply is not JSON and was NOT truncated, THE SYSTEM SHALL return None.

    The control for the truncation tests: `None` must keep meaning "no offset could be established"
    whenever that is genuinely what happened, or the guard would have turned every unreadable
    transcript into a raised error and the bundle export into a minefield.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply="not json at all", truncated=False)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    assert dp.transcript_page_offset(pdf, 1, 2) is None


def test_a_vllm_backend_sends_page_images_at_the_deposition_render_target(tmp_path, monkeypatch):
    """WHEN deposition resolves to vllm, THE SYSTEM SHALL send image parts rendered to
    `deposition_image_long_edge_px`, not the summarize target.

    vLLM cannot carry an inline PDF at all, so the image path is a precondition rather than a
    preference. The render target is asserted because a printed page number sits in a corner in
    small type, and the shared rasteriser would otherwise impose summarize's 1024.
    """
    captured = {}
    seen = {}
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_backend", "vllm")
    monkeypatch.setattr(settings, "vllm_model", "served-by-the-pod/model")

    def _rasterise(_path, _start, _end, cap, long_edge_px=None):
        seen["cap"] = cap
        seen["long_edge_px"] = long_edge_px
        return [ImagePart(data=b"img")] * 3

    monkeypatch.setattr(dp, "page_image_parts", _rasterise)
    _stub_provider(monkeypatch, captured, reply=_reply(3))
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 3)

    parts = captured["parts"]
    assert not any(isinstance(part, DocumentPart) for part in parts)
    assert all(isinstance(part, ImagePart) for part in parts[:-1])
    assert isinstance(parts[-1], TextPart), "the prompt is still last"
    assert seen["cap"] == dp._MAX_PAGES
    assert seen["long_edge_px"] == settings.deposition_image_long_edge_px
    # Control: it genuinely differs from the summarize target, or this passes for the wrong reason.
    assert settings.deposition_image_long_edge_px != settings.summary_image_long_edge_px
    assert captured["model"] == "served-by-the-pod/model"
