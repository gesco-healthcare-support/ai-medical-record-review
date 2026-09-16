"""What the isolated DOI read actually SENDS, and what it does when the reply is cut off.

`test_summary_doi.py` pins the reply PARSING - `_clean`, the fail-safe, the prefix rewrite - and
stubs the model call with a fake PDF writer that emits `b"%PDF-1.4 fake"`. So the REQUEST this
function builds had never been pinned by anything: not the page count reaching the model, not the
cap, not the part order.

READ THE DIFF ON THIS FILE, not only its current state. The first commit pinned the request as
google-genai received it directly; this version asserts the same guarantees through the provider
seam, and adds the truncation contract that routing made possible. Where an assertion changed, the
request changed.

A REAL synthetic PDF is written to tmp_path rather than stubbing `PdfReader`, because the page cap
is one of the things being pinned and a fake writer cannot demonstrate it: the bytes are read back
and counted. Synthetic blank pages only - never a real record.
"""

import io
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import summary_doi as sd
from app.services.llm.parts import DocumentPart, ImagePart, TextPart


def _blank_pdf(path, pages):
    """A SYNTHETIC PDF of `pages` blank pages. Never a real record."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _stub_provider(monkeypatch, captured, reply="09/25/2023", truncated=False):
    """Route `summary_doi.provider_for_stage` to a stub recording what the service asked for.

    Returns a dict carrying the stage the SERVICE handed the resolver. A resolver stub that accepts
    anything swallows a wrong stage silently - and a wrong stage here means the thinking budget
    resolves to 0, which a thinking model rejects with a 400 that this fail-safe function cannot
    report.
    """
    asked = {}

    class _Provider:
        def generate_text(self, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return SimpleNamespace(text=reply, truncated=truncated)

    def _resolver(stage, *_a, **_k):
        asked["stage"] = stage
        return _Provider()

    monkeypatch.setattr(sd, "provider_for_stage", _resolver)
    return asked


def test_the_doi_call_sends_one_pdf_part_then_the_prompt(tmp_path, monkeypatch):
    """WHEN the stage resolves to gemini, THE SYSTEM SHALL send exactly one PDF document part
    carrying the sub-document's pages, followed by `_ISOLATION_PROMPT`.

    Order is pinned because the prompt says "The attached pages are ONE medical document" - it
    refers backwards to the part before it.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 12)

    assert sd.extract_injury_date(pdf, 3, 6) == "09/25/23"

    parts = captured["parts"]
    assert len(parts) == 2
    assert isinstance(parts[0], DocumentPart)
    assert parts[0].mime_type == "application/pdf"
    # Pages 3..6 inclusive is four, and the bytes really are a PDF carrying exactly those.
    assert len(PdfReader(io.BytesIO(parts[0].data)).pages) == 4
    assert isinstance(parts[1], TextPart)
    assert parts[1].text == sd._ISOLATION_PROMPT


def test_the_page_cap_bounds_a_long_sub_document(tmp_path, monkeypatch):
    """WHEN the span exceeds `_MAX_PAGES`, THE SYSTEM SHALL send only the first `_MAX_PAGES` pages.

    The cap is load-bearing and measured: raising it 5 -> 10 on 2026-07-31 was the single largest
    fix to missed DOIs, because past page 5 the labelled field simply was not in the payload.
    """
    captured = {}
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 40)

    sd.extract_injury_date(pdf, 1, 30)

    sent = PdfReader(io.BytesIO(captured["parts"][0].data))
    assert len(sent.pages) == sd._MAX_PAGES == 10


def test_the_doi_call_carries_its_cap_temperature_model_and_resolves_through_the_doi_stage(
    tmp_path, monkeypatch
):
    """WHEN `extract_injury_date` runs, THE SYSTEM SHALL send the configured cap, temperature 0.0
    and the doi-stage model, and SHALL resolve BOTH transport and model through `doi`.

    `asked["stage"]` is the half no config-level test can see, and here it carries more than usual:
    the stage selects `thinking_for("doi")`, so a wrong string silently drops the budget to 0 - the
    exact failure that once made every document report no injury date.

    The cap is asserted against the SETTING rather than a literal, because it is deliberately
    env-overridable; asserting 2048 here would pin the default instead of the wiring.
    """
    captured = {}
    asked = _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    sd.extract_injury_date(pdf, 1, 2)

    settings = get_settings()
    assert asked["stage"] == "doi"
    assert captured["stage"] == "doi"
    assert captured["model"] == settings.model_for_stage("doi")
    assert captured["temperature"] == 0.0
    assert captured["max_output_tokens"] == settings.doi_max_output_tokens
    # Free text, not a structured call: the prompt alone constrains the reply and `_clean` parses
    # whatever comes back. Pinned because it decides which seam method this must use.
    assert "schema" not in captured
    assert captured["system"] is None


def test_a_truncated_reply_raises_for_a_strict_caller(tmp_path, monkeypatch):
    """IF the reply is truncated AND the caller passed `strict`, THEN the system SHALL raise.

    THIS IS THE DATA-LOSS PATH THIS PR CLOSES. A truncated reply has no date in it, so `_clean`
    returned "-" - and "-" is a CLAIM ("this document states no injury date"), not a failure. In
    `scripts/backfill_doi.py` that value reaches `apply_doi_prefix`, which STRIPS the DOI prefix
    from a stored medical-legal summary. `strict` exists precisely so a read failure is never
    mistaken for "states none", and before this it did not catch truncation because truncation is
    not an exception.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply="", truncated=True)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    with pytest.raises(Exception, match="truncated"):
        sd.extract_injury_date(pdf, 1, 2, strict=True)


def test_a_truncated_reply_still_degrades_to_dash_for_the_pooled_caller(tmp_path, monkeypatch):
    """IF the reply is truncated AND the caller did not pass `strict`, THEN the system SHALL return
    "-", exactly as before.

    Segmentation calls this once per row on a pool and has always treated any failure as "no date".
    That path must not change: the fix is about the BACKFILL, where "-" destroys data, not about
    making segmentation noisier.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply="", truncated=True)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    assert sd.extract_injury_date(pdf, 1, 2) == "-"


def test_an_untruncated_empty_reply_is_still_a_genuine_no(tmp_path, monkeypatch):
    """WHEN the model answers with no date and was NOT truncated, THE SYSTEM SHALL return "-".

    The control for the two tests above: "-" must keep meaning "states none" when that is what the
    model actually said, or the truncation guard would have turned every real negative into an
    error.
    """
    captured = {}
    _stub_provider(monkeypatch, captured, reply="-", truncated=False)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    assert sd.extract_injury_date(pdf, 1, 2, strict=True) == "-"


def test_a_vllm_backend_sends_page_images_at_the_doi_render_target(tmp_path, monkeypatch):
    """WHEN `doi` resolves to vllm, THE SYSTEM SHALL send image parts rendered to
    `doi_image_long_edge_px`, not the summarize target.

    vLLM cannot carry an inline PDF at all, so the image path is a precondition rather than a
    preference. The render target is asserted because reading a small labelled date field needs
    more pixels than judging page layout, and the shared rasteriser would otherwise impose
    summarize's 1024.
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

    monkeypatch.setattr(sd, "page_image_parts", _rasterise)
    _stub_provider(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    sd.extract_injury_date(pdf, 1, 3)

    parts = captured["parts"]
    assert not any(isinstance(part, DocumentPart) for part in parts)
    assert all(isinstance(part, ImagePart) for part in parts[:-1])
    assert isinstance(parts[-1], TextPart), "the prompt is still last"
    assert seen["cap"] == sd._MAX_PAGES
    assert seen["long_edge_px"] == settings.doi_image_long_edge_px
    # Control: it genuinely differs from the summarize target, or this passes for the wrong reason.
    assert settings.doi_image_long_edge_px != settings.summary_image_long_edge_px
    assert captured["model"] == "served-by-the-pod/model"
