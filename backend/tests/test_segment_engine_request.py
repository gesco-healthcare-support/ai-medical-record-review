"""What the segmentation window call actually SENDS.

Every other test of this module replaces `_window_rows` wholesale - see `test_segment_engine.py`,
`test_pool_wiring.py` and `test_jobs.py`, all of which stub it out - so the request that function
BUILDS had never been pinned by anything in this repo before these.

READ THE DIFF ON THIS FILE, not just its current state. The first commit pinned the request as
google-genai received it directly; this version asserts the same guarantees through the provider
seam. Where an assertion changed, the request changed, and that is the whole point of having written
it before the routing rather than after.

Pure and synthetic: the provider is stubbed and the PDF is blank pages written to tmp_path. Nothing
reaches Vertex or a pod, and no real record is opened.
"""

import io
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import segment_engine as se
from app.services.gemini import SEGMENTATION_PROMPT, SEGMENTATION_SYSTEM
from app.services.llm.gemini import to_gemini_schema
from app.services.llm.parts import DocumentPart, ImagePart, TextPart

# A FROZEN COPY of the schema exactly as google-genai received it BEFORE the seam, deliberately
# duplicated rather than imported from app.services.gemini.
#
# The duplication is the entire point. That module constant is lowercase now so the seam can
# translate it per backend, and an assertion written against the constant would compare the new
# value to ITSELF - passing no matter how wrong the translation was. Frozen here, this is the only
# thing in the suite that can catch a bad dialect conversion.
_SCHEMA_AS_GEMINI_RECEIVES_IT = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING", "description": "Sequential id: Doc1, Doc2, ..."},
            "s": {
                "type": "INTEGER",
                "description": "First page of the sub-document: 1-based position in THIS file",
            },
            "e": {"type": "INTEGER", "description": "Last page of the sub-document, inclusive"},
            "t": {"type": "STRING", "description": "Title or document type; no commas"},
            "d": {"type": "STRING", "description": "Visit/encounter date MM/DD/YYYY, or '-'"},
            "m": {
                "type": "STRING",
                "enum": ["x", "-"],
                "description": "'x' when the document needs human review",
            },
        },
        "required": ["s", "e", "t", "d", "m"],
        "propertyOrdering": ["id", "s", "e", "t", "d", "m"],
    },
}


def _blank_pdf(path, pages):
    """A SYNTHETIC PDF of `pages` blank pages. Never a real record."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _stub_provider(monkeypatch, captured, reply="[]"):
    """Route `segment_engine.provider_for_stage` to a stub recording what the service asked for.

    Returns a dict carrying the stage the SERVICE handed the resolver. A resolver stub that accepts
    anything swallows a wrong stage silently, and resolving the transport through the wrong stage is
    the defect that reached main in #318.
    """
    asked = {}

    class _Provider:
        def generate_structured(self, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return SimpleNamespace(text=reply)

    def _resolver(stage, *_a, **_k):
        asked["stage"] = stage
        return _Provider()

    monkeypatch.setattr(se, "provider_for_stage", _resolver)
    return asked


def test_the_window_call_sends_one_pdf_part_then_the_prompt(tmp_path, monkeypatch):
    """WHEN `_window_rows` runs on Gemini, THE SYSTEM SHALL send exactly one PDF document part
    carrying the window's pages, followed by SEGMENTATION_PROMPT.

    The page COUNT is asserted by reading the sent bytes back rather than by recomputing the writer
    dance, so this pins the payload's observable content instead of restating the implementation.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 10)

    assert se._window_rows(pdf, 3, 6) == []

    parts = captured["parts"]
    assert len(parts) == 2
    assert isinstance(parts[0], DocumentPart)
    assert parts[0].mime_type == "application/pdf"
    # Pages 3..6 inclusive is four, and the bytes really are a PDF carrying exactly those.
    assert len(PdfReader(io.BytesIO(parts[0].data)).pages) == 4
    assert isinstance(parts[1], TextPart)
    assert parts[1].text == SEGMENTATION_PROMPT


def test_the_window_call_carries_the_segment_config_and_resolves_through_the_segment_stage(
    tmp_path, monkeypatch
):
    """WHEN `_window_rows` runs, THE SYSTEM SHALL send the segmentation system prompt, temperature,
    sampling parameters and model, and SHALL resolve BOTH transport and model through `segment`.

    `asked["stage"]` is the half no config-level test can see. `stage=` only selects a thinking
    budget - gemini.py reads it for `thinking_for()`, vllm.py and openai.py both `del stage` - so
    what says the TRANSPORT resolved through segment is which stage the SERVICE handed the resolver.
    That is exactly the half that was wrong in #318.

    top_p and top_k are asserted because the seam had no way to carry them until this work added
    one; routing without that would have dropped both and changed the request on the stage this
    pipeline is most sensitive to, with nothing reporting it.
    """
    captured = {}
    asked = _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 2)

    settings = get_settings()
    assert asked["stage"] == "segment"
    assert captured["stage"] == "segment"
    assert captured["model"] == settings.model_for_stage("segment")
    assert captured["system"] == SEGMENTATION_SYSTEM
    assert captured["temperature"] == 0.0
    assert captured["top_p"] == 0.95
    assert captured["top_k"] == 40
    # This call has never carried a cap, and the seam sends the field only when it is set.
    assert "max_output_tokens" not in captured


def test_the_schema_still_reaches_gemini_in_exactly_the_dialect_it_used_to(tmp_path, monkeypatch):
    """WHEN the lowercased schema is translated, THE SYSTEM SHALL produce the uppercase literal that
    google-genai received before the seam - byte for byte.

    This is the assertion that makes the dialect change safe. The service now sends ordinary JSON
    Schema and the Gemini provider translates it; comparing the TRANSLATED value against a frozen
    copy of the old literal is the only way to prove the translation is lossless. `propertyOrdering`
    is part of that comparison: it is a google-genai key with no equivalent elsewhere, and it
    survives only because the translator passes unrecognised keys through untouched.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 2)

    assert to_gemini_schema(captured["schema"]) == _SCHEMA_AS_GEMINI_RECEIVES_IT
    # Control: the schema really is lowercase now, or the translation above proves nothing.
    assert captured["schema"]["type"] == "array"


def test_a_vllm_backend_sends_page_images_instead_of_the_pdf(tmp_path, monkeypatch):
    """WHEN `segment` resolves to vllm, THE SYSTEM SHALL send one image part per page and no PDF.

    vLLM cannot carry an inline PDF at all - `llm/openai.py::_to_messages`, which the vLLM provider
    converts through, raises TypeError on a DocumentPart. So this is not a preference: without the
    image path the stage cannot run on that backend.
    """
    captured = {}
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_backend", "vllm")
    monkeypatch.setattr(settings, "vllm_model", "served-by-the-pod/model")
    monkeypatch.setattr(
        se,
        "page_image_parts",
        lambda _p, start, end, cap, label_pages=False: [ImagePart(data=b"img")] * 3,
    )
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 3)

    parts = captured["parts"]
    assert not any(isinstance(part, DocumentPart) for part in parts)
    assert all(isinstance(part, ImagePart) for part in parts[:-1])
    assert isinstance(parts[-1], TextPart), "the prompt is still last"
    assert captured["model"] == "served-by-the-pod/model"


def test_the_vllm_page_cap_is_handed_to_the_rasteriser(tmp_path, monkeypatch):
    """WHEN rasterising for vllm, THE SYSTEM SHALL pass `vllm_segment_max_pages` as the cap.

    Pinned because the cap is the whole reason the rasteriser takes one: the summarize caller passes
    15, and a segmentation window that silently inherited that number would lose every page past the
    fifteenth with nothing raised - and a short window is indistinguishable from a short document
    downstream.
    """
    seen = {}
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_backend", "vllm")

    def _rasterise(_path, start, end, cap, label_pages=False):
        seen["cap"] = cap
        return [ImagePart(data=b"img")]

    monkeypatch.setattr(se, "page_image_parts", _rasterise)
    _stub_provider(monkeypatch, {})
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 3)

    assert seen["cap"] == settings.vllm_segment_max_pages
    # Control: it is genuinely a different number from the summarize cap, or this passes trivially.
    assert settings.vllm_segment_max_pages != settings.summary_image_max_pages


def test_the_vllm_window_labels_every_page_with_its_position(tmp_path, monkeypatch):
    """WHEN rasterising for vllm, THE SYSTEM SHALL ask for page labels.

    A PDF part carries page positions; a list of JPEGs does not. So on this backend the model
    had to COUNT its way to a page number, while SEGMENTATION_PROMPT forbids the only other
    positional signal ("Ignore page numbers printed on the pages"). Measured against the
    boundaries reviewers kept, that cost 6.14 misplaced boundaries per 100 on vllm against 0.62
    on gemini, 82% of them by exactly one page - while the rate of boundaries deleted outright
    was unchanged, which is what says the model found the right documents and numbered them
    wrong. `_window_parts` carries the full measurement.
    """
    seen = {}
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_backend", "vllm")

    def _rasterise(_path, start, end, cap, label_pages=False):
        seen["label_pages"] = label_pages
        return [ImagePart(data=b"img")]

    monkeypatch.setattr(se, "page_image_parts", _rasterise)
    _stub_provider(monkeypatch, {})
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 3)

    assert seen["label_pages"] is True


def test_the_gemini_window_is_not_labelled(tmp_path, monkeypatch):
    """GUARD, and the reason the labelling is not unconditional: the PDF path needs nothing.

    Gemini receives one DocumentPart whose container already carries page positions, so a label
    there would add tokens, change a payload that has been stable since this stage was written,
    and move nothing it measures.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 3)

    parts = captured["parts"]
    assert len(parts) == 2, "one PDF then the prompt, and nothing between them"
    assert isinstance(parts[0], DocumentPart)
    assert parts[1].text == SEGMENTATION_PROMPT


def test_a_fenced_reply_is_still_parsed(tmp_path, monkeypatch):
    """WHEN the model wraps its JSON in a ```json fence, THE SYSTEM SHALL still parse the rows.

    Pinned because the fence-stripping sits between the response and the parser, so a routing change
    that altered how the reply text is read would break it silently - the window would simply yield
    no rows, which reads as a short document rather than as an error.
    """
    captured = {}
    _stub_provider(
        monkeypatch,
        captured,
        reply='```json\n[{"s": 1, "e": 2, "t": "A", "d": "-", "m": "-"}]\n```',
    )
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    rows = se._window_rows(pdf, 1, 2)

    assert [(r["start"], r["end"], r["title"]) for r in rows] == [(1, 2, "A")]
