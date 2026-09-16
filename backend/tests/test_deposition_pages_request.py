"""What the transcript page-number read actually SENDS, and what it does when the reply is cut off.

`test_deposition_pages.py` pins the reply PARSING - `_offset_from`'s agreement rule, the marker
relabelling, the audit guard - across twenty tests. Every one of them calls `_offset_from` or
`_deposition_pages_block` directly. So the REQUEST this function builds had never been pinned by
anything: not the page count reaching the model, not the cap, not the schema, not the thinking
budget, not which model answers.

READ THE DIFF ON THIS FILE, not only its current state. The first commit pinned the request as
google-genai received it, before any routing existed; later commits assert the same guarantees
through the provider seam. Where an assertion changed, the request changed - and only
`max_output_tokens` was meant to.

A REAL synthetic PDF is written to tmp_path rather than stubbing `PdfReader`, because the page cap
is one of the things being pinned and a fake reader cannot demonstrate it: the bytes are read back
and the pages counted. Synthetic blank pages only - never a real record.
"""

import io
import json
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import deposition_pages as dp

# The schema as google-genai receives it today, FROZEN here rather than imported.
#
# Asserting `config.response_schema is dp._SCHEMA` would pass however that constant changes - it
# would compare the module to itself. This copy is the independent record, so lowercasing _SCHEMA
# for the provider seam has to keep translating back to exactly this or the test fails.
_FROZEN_SCHEMA = {
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


def _stub_model(monkeypatch, captured, reply=None):
    """Route `deposition_pages.generate_with_retry` to a stub recording what the service asked for."""

    def _call(client, **kwargs):
        captured.clear()
        captured.update(kwargs)
        captured["client"] = client
        return SimpleNamespace(text=reply if reply is not None else _reply(6))

    monkeypatch.setattr(dp, "generate_with_retry", _call)
    monkeypatch.setattr(dp, "get_genai_client", lambda: "the-genai-client")


def _sent_pdf_bytes(captured):
    """The PDF bytes of the single document part, read back off the request."""
    part = captured["contents"][0]
    return part.inline_data.data


def test_the_call_sends_one_pdf_part_then_the_prompt(tmp_path, monkeypatch):
    """WHEN transcript_page_offset runs, THE SYSTEM SHALL send one PDF part carrying the
    sub-document's pages, followed by `_PROMPT`.

    Order is pinned because the prompt says "The attached pages are consecutive pages from ONE
    deposition" - it refers backwards to the part before it.
    """
    captured = {}
    _stub_model(monkeypatch, captured, reply=_reply(4))
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 12)

    assert dp.transcript_page_offset(pdf, 3, 6) == 98

    contents = captured["contents"]
    assert len(contents) == 2
    assert contents[0].inline_data.mime_type == "application/pdf"
    # Pages 3..6 inclusive is four, and the bytes really are a PDF carrying exactly those.
    assert len(PdfReader(io.BytesIO(_sent_pdf_bytes(captured))).pages) == 4
    assert contents[1] == dp._PROMPT


def test_the_page_cap_bounds_a_long_transcript(tmp_path, monkeypatch):
    """WHEN the span exceeds `_MAX_PAGES`, THE SYSTEM SHALL send only the first `_MAX_PAGES` pages.

    The cap keeps the payload small AND stays inside `summary_image_max_pages` (15), so every page
    asked about is one the model can actually see rather than one it would guess at from OCR.
    """
    captured = {}
    _stub_model(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 40)

    dp.transcript_page_offset(pdf, 1, 30)

    sent = PdfReader(io.BytesIO(_sent_pdf_bytes(captured)))
    assert len(sent.pages) == dp._MAX_PAGES == 6


def test_the_call_carries_its_cap_temperature_schema_and_thinking_budget(tmp_path, monkeypatch):
    """WHEN transcript_page_offset runs, THE SYSTEM SHALL send temperature 0.0, the configured
    output cap, JSON mode with the frozen schema, and the summary thinking budget.

    The thinking budget is the one that bites: the retry seam defaults `thinking_budget` to 0, which
    a thinking model rejects with a 400 - and because this function is fail-safe that rejection is
    SILENT and every transcript loses its citations. It is asserted against the SETTING rather than
    -1 so the test pins the wiring, not the current default.
    """
    captured = {}
    _stub_model(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2)

    settings = get_settings()
    config = captured["config"]
    assert config.temperature == 0.0
    assert config.max_output_tokens == 400
    assert config.response_mime_type == "application/json"
    assert config.response_schema == _FROZEN_SCHEMA
    assert config.thinking_config.thinking_budget == settings.summary_thinking_budget


def test_the_model_is_the_general_gemini_model_not_the_summary_one(tmp_path, monkeypatch):
    """WHEN no model is passed, THE SYSTEM SHALL answer with the general model, not summary_model.

    `genai_model` and `summary_model` are DIFFERENT models (2.5-flash against 3.5-flash), and this
    read has always used the general one. Pinned because routing resolves the model through a stage,
    and a stage that resolves to the wrong family would change which model reads the page numbers
    without changing anything a reader would notice.
    """
    captured = {}
    _stub_model(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2)

    settings = get_settings()
    assert captured["model"] == settings.genai_model
    assert captured["model"] != settings.summary_model


def test_an_explicit_model_overrides_the_default(tmp_path, monkeypatch):
    """WHEN a caller passes `model`, THE SYSTEM SHALL use it verbatim."""
    captured = {}
    _stub_model(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    dp.transcript_page_offset(pdf, 1, 2, model="an-explicitly-chosen-model")

    assert captured["model"] == "an-explicitly-chosen-model"
