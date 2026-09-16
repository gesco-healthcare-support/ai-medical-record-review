"""What the isolated DOI read actually SENDS.

`test_summary_doi.py` pins the reply PARSING - `_clean`, the fail-safe, the prefix rewrite - and
stubs the model call with a fake PDF writer that emits `b"%PDF-1.4 fake"`. So the REQUEST this
function builds has never been pinned: not the page count that reaches the model, not the cap, not
the part order.

READ THE DIFF ON THIS FILE, not only its current state. This version pins the request as
google-genai receives it directly; the routing commit that follows asserts the same guarantees
through the provider seam. Where an assertion changes, the request changed - which is the whole
reason for writing it before the change rather than after.

A REAL synthetic PDF is written to tmp_path rather than stubbing `PdfReader`, because the page cap
is one of the things being pinned and a fake writer cannot demonstrate it: the bytes are read back
and counted. Synthetic blank pages only - never a real record.
"""

import io
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import summary_doi as sd


def _blank_pdf(path, pages):
    """A SYNTHETIC PDF of `pages` blank pages. Never a real record."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _stub_the_genai_call(monkeypatch, captured, reply="09/25/2023"):
    """Record the google-genai request `extract_injury_date` builds, and answer with ``reply``."""

    def _capture(_client, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return SimpleNamespace(text=reply)

    monkeypatch.setattr(sd, "generate_with_retry", _capture)
    monkeypatch.setattr(sd, "get_genai_client", lambda: None)
    return captured


def test_the_doi_call_sends_one_pdf_part_then_the_prompt(tmp_path, monkeypatch):
    """WHEN `extract_injury_date` runs, THE SYSTEM SHALL send exactly one inline PDF part carrying
    the sub-document's pages, followed by `_ISOLATION_PROMPT`.

    Order is pinned because the prompt says "The attached pages are ONE medical document" - it
    refers backwards to the part before it.
    """
    captured = {}
    _stub_the_genai_call(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 12)

    assert sd.extract_injury_date(pdf, 3, 6) == "09/25/23"

    contents = captured["contents"]
    assert len(contents) == 2
    blob = contents[0].inline_data
    assert blob.mime_type == "application/pdf"
    # Pages 3..6 inclusive is four, and the bytes really are a PDF carrying exactly those.
    assert len(PdfReader(io.BytesIO(blob.data)).pages) == 4
    assert contents[1] == sd._ISOLATION_PROMPT


def test_the_page_cap_bounds_a_long_sub_document(tmp_path, monkeypatch):
    """WHEN the span exceeds `_MAX_PAGES`, THE SYSTEM SHALL send only the first `_MAX_PAGES` pages.

    The cap is load-bearing and measured: raising it 5 -> 10 on 2026-07-31 was the single largest
    fix to missed DOIs, because past page 5 the labelled field simply was not in the payload. A
    change that silently sent fewer pages would regress that with nothing failing.
    """
    captured = {}
    _stub_the_genai_call(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 40)

    sd.extract_injury_date(pdf, 1, 30)

    sent = PdfReader(io.BytesIO(captured["contents"][0].inline_data.data))
    assert len(sent.pages) == sd._MAX_PAGES == 10


def test_the_doi_call_carries_its_cap_temperature_model_and_thinking_budget(tmp_path, monkeypatch):
    """WHEN `extract_injury_date` runs, THE SYSTEM SHALL send the 200-token cap, temperature 0.0,
    the genai model, and the SUMMARY thinking budget rather than the seam's default of 0.

    The thinking budget is the one with a scar: this function is fail-safe, so a model that rejects
    a zero budget with a 400 produced a SILENT "-" on every document. Pinning it here means the
    routing change has to carry it through `thinking_for("doi")` rather than rediscover it.
    """
    captured = {}
    _stub_the_genai_call(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    sd.extract_injury_date(pdf, 1, 2)

    settings = get_settings()
    assert captured["model"] == settings.genai_model
    config = captured["config"]
    assert config.temperature == 0.0
    assert config.max_output_tokens == 200
    assert config.thinking_config.thinking_budget == settings.summary_thinking_budget


def test_the_doi_call_asks_for_free_text_not_a_schema(tmp_path, monkeypatch):
    """WHEN `extract_injury_date` runs, THE SYSTEM SHALL constrain the reply with the PROMPT alone.

    Unlike every other structured call in this pipeline, this one sends no `response_schema` and no
    `response_mime_type` - the prompt says "Answer with ONLY the date(s) or '-'" and `_clean` parses
    whatever comes back. Pinned because it decides which seam method the routing change must use:
    a free-text completion, not a structured one.
    """
    captured = {}
    _stub_the_genai_call(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    sd.extract_injury_date(pdf, 1, 2)

    config = captured["config"]
    assert getattr(config, "response_schema", None) is None
    assert getattr(config, "response_mime_type", None) is None
