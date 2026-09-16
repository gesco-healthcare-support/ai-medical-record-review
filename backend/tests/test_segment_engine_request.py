"""What the segmentation window call actually SENDS.

Every other test of this module replaces `_window_rows` wholesale - see `test_segment_engine.py`,
`test_pool_wiring.py` and `test_jobs.py`, all of which stub it out - so the request that function
BUILDS has never been pinned by anything in this repo.

That matters now. The routing change that follows must send the identical PDF bytes on the Gemini
path, and "identical" is not a checkable claim without a _before_ to compare against. These tests
are that before, and they are committed on their own so the comparison is a diff rather than an
assertion about a diff.

Pure and synthetic: the model call is stubbed and the PDF is blank pages written to tmp_path.
Nothing reaches Vertex and no real record is opened.
"""

import io
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services import segment_engine as se
from app.services.gemini import SEGMENTATION_PROMPT, SEGMENTATION_SYSTEM

# A FROZEN COPY of the schema exactly as google-genai receives it today, deliberately duplicated
# rather than imported from app.services.gemini.
#
# The duplication is the entire point. The routing work lowercases that module constant so the seam
# can translate it per backend, and an assertion written against the constant would then compare the
# new value to ITSELF - passing no matter how wrong the translation was. Frozen here, this is the
# only thing in the suite that can catch a bad dialect conversion.
_SCHEMA_AS_GEMINI_RECEIVES_IT_TODAY = {
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


def _stub_the_genai_call(monkeypatch, captured, reply="[]"):
    """Record the google-genai request `_window_rows` builds, and answer with ``reply``."""

    def _capture(client, **kwargs):
        captured.clear()
        captured.update(kwargs)
        captured["client"] = client
        return SimpleNamespace(text=reply)

    monkeypatch.setattr(se, "generate_with_retry", _capture)
    return captured


def test_the_window_call_sends_one_pdf_part_then_the_prompt(tmp_path, monkeypatch):
    """WHEN `_window_rows` runs, THE SYSTEM SHALL send exactly one inline PDF part carrying the
    window's pages, followed by SEGMENTATION_PROMPT.

    The page COUNT is asserted by reading the sent bytes back rather than by recomputing the writer
    dance, so this pins the payload's observable content instead of restating the implementation.
    """
    captured = {}
    _stub_the_genai_call(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 10)

    assert se._window_rows(pdf, 3, 6, object()) == []

    contents = captured["contents"]
    assert len(contents) == 2
    blob = contents[0].inline_data
    assert blob.mime_type == "application/pdf"
    # Pages 3..6 inclusive is four, and the bytes really are a PDF carrying exactly those.
    assert len(PdfReader(io.BytesIO(blob.data)).pages) == 4
    assert contents[1] == SEGMENTATION_PROMPT


def test_the_window_call_carries_the_segment_config_and_thinking_budget(tmp_path, monkeypatch):
    """WHEN `_window_rows` runs, THE SYSTEM SHALL send the segmentation schema and system prompt,
    and the SEGMENT thinking budget rather than the seam's default of 0.

    The budget is pinned because segmentation is the one stage where thinking is load-bearing: an
    A/B on labelled cases showed thinking-off regresses strict doc-F1 by over-segmenting. It is also
    the value the routing change has to carry through `thinking_for("segment")`, and a wrong stage
    string there would swap it for `gemini_thinking_budget` = 0 with nothing else noticing.
    """
    captured = {}
    _stub_the_genai_call(monkeypatch, captured)
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    se._window_rows(pdf, 1, 2, object())

    settings = get_settings()
    assert captured["model"] == settings.genai_model
    config = captured["config"]
    assert config.temperature == 0.0
    assert config.top_p == 0.95
    assert config.top_k == 40
    assert config.response_mime_type == "application/json"
    assert config.system_instruction == SEGMENTATION_SYSTEM
    assert config.thinking_config.thinking_budget == settings.segment_thinking_budget
    # Equality against the FROZEN copy, not identity and not the imported constant. The config
    # validates the dict into its own copy, so identity never holds; and comparing against the
    # constant would be self-referential once that constant is lowercased.
    assert config.response_schema == _SCHEMA_AS_GEMINI_RECEIVES_IT_TODAY


def test_a_fenced_reply_is_still_parsed(tmp_path, monkeypatch):
    """WHEN the model wraps its JSON in a ```json fence, THE SYSTEM SHALL still parse the rows.

    Pinned here because the fence-stripping sits between the response and the parser, so a routing
    change that altered how the reply text is read would break it silently - the window would simply
    yield no rows, which reads as a short document rather than as an error.
    """
    captured = {}
    _stub_the_genai_call(
        monkeypatch,
        captured,
        reply='```json\n[{"s": 1, "e": 2, "t": "A", "d": "-", "m": "-"}]\n```',
    )
    pdf = _blank_pdf(tmp_path / "synthetic.pdf", 4)

    rows = se._window_rows(pdf, 1, 2, object())

    assert [(r["start"], r["end"], r["title"]) for r in rows] == [(1, 2, "A")]
