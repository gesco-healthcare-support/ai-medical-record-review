"""The two Gemini boundary oracles measured in Phase 0 (0b).

Both send page images inline (types.Part.from_bytes) and use constrained-enum output so
Gemini can only answer with a valid label. temperature 0 for maximum determinism. Every call
is cost-tracked via the shared Cost accountant.

- adjacent  (ideas 2/3): given the previous and current page -> NEW | SAME.
- range_probe (idea 4) : given a document's first page and a candidate later page ->
                         SAME_DOC | NEW_DOC. This is the oracle the binary search would call.
"""

import os
import sys
import tempfile

from google.genai import types
from pypdf import PdfReader, PdfWriter

import images
from genai_client import classify_enum, generate_json, upload_file

# Reuse the production segmentation prompt + tolerant parser for the window oracle (Solution 1),
# so it faithfully reproduces the current /getPages behavior within a window.
sys.path.insert(0, r"P:\MRR_AI_Source\mrr-line_source")
from mrr_ai.services.gemini import SEGMENTATION_PROMPT, parse_segment_item  # noqa: E402

_SEG_SYS = (
    "You are an assistant that segments a large document into subdocuments and provide "
    "their metadata."
)

_ADJACENT_SYS = (
    "You segment a scanned medical-record PDF into its sub-documents. You are shown two "
    "consecutive pages. Decide whether the SECOND (current) page begins a NEW document or "
    "continues the SAME document as the first page."
)
_RANGE_SYS = (
    "You segment a scanned medical-record PDF into its sub-documents. You are shown the FIRST "
    "page of a document and a later CANDIDATE page. Decide whether the candidate page still "
    "belongs to the SAME document, or is part of a NEW (different) document."
)


def _part(pdf_path, page_number, dpi):
    return types.Part.from_bytes(data=images.render_png(pdf_path, page_number, dpi), mime_type="image/png")


def adjacent(pdf_path, page_number, cost, dpi=150):
    """Is `page_number` the start of a new document vs the page before it? -> 'NEW'|'SAME'|None."""
    contents = [
        "First image = previous page. Second image = current page. "
        "Does the current page start a NEW document or continue the SAME one?",
        _part(pdf_path, page_number - 1, dpi),
        _part(pdf_path, page_number, dpi),
    ]
    return classify_enum(contents, ("NEW", "SAME"), _ADJACENT_SYS, cost)


def range_probe(pdf_path, start_page, candidate_page, cost, dpi=150):
    """Does `candidate_page` belong to the document that began at `start_page`?
    -> 'SAME_DOC'|'NEW_DOC'|None."""
    contents = [
        "First image = the first page of a document. Second image = a later candidate page. "
        "Does the candidate still belong to the SAME document, or a NEW one?",
        _part(pdf_path, start_page, dpi),
        _part(pdf_path, candidate_page, dpi),
    ]
    return classify_enum(contents, ("SAME_DOC", "NEW_DOC"), _RANGE_SYS, cost)


def adjacent_text(prev_markdown, cur_markdown, cost):
    """Text version of the adjacent oracle (Solution 3): markdown of the two pages -> NEW|SAME."""
    contents = [
        f"PREVIOUS PAGE (markdown):\n{prev_markdown}\n\n"
        f"CURRENT PAGE (markdown):\n{cur_markdown}\n\n"
        "Does the current page start a NEW document or continue the SAME one?"
    ]
    return classify_enum(contents, ("NEW", "SAME"), _ADJACENT_SYS, cost)


def window_segment(pdf_path, start_page, end_page, cost):
    """Window oracle (Solution 1): segment pages [start_page, end_page] in one call.

    Extracts the page range to a temp PDF, uploads it, and runs the production segmentation
    prompt; returns absolute (start, end) spans. Local page numbers are offset to absolute.
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for p in range(start_page - 1, end_page):
        writer.add_page(reader.pages[p])
    tmp = os.path.join(tempfile.gettempdir(), f"pss_win_{start_page}_{end_page}.pdf")
    with open(tmp, "wb") as fh:
        writer.write(fh)

    uploaded = upload_file(tmp)
    data = generate_json([uploaded, SEGMENTATION_PROMPT], _SEG_SYS, cost) or []
    spans = []
    for item in data:
        try:
            s, e, *_ = parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            continue
        spans.append((s + start_page - 1, e + start_page - 1))
    return spans
