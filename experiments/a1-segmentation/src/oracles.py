"""The two Gemini boundary oracles measured in Phase 0 (0b).

Both send page images inline (types.Part.from_bytes) and use constrained-enum output so
Gemini can only answer with a valid label. temperature 0 for maximum determinism. Every call
is cost-tracked via the shared Cost accountant.

- adjacent  (ideas 2/3): given the previous and current page -> NEW | SAME.
- range_probe (idea 4) : given a document's first page and a candidate later page ->
                         SAME_DOC | NEW_DOC. This is the oracle the binary search would call.
"""

from google.genai import types

import images
from genai_client import classify_enum

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
