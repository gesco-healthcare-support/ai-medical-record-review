"""The two Gemini boundary oracles measured in Phase 0 (0b).

Both send page images inline (types.Part.from_bytes) and use constrained-enum output so
Gemini can only answer with a valid label. temperature 0 for maximum determinism. Every call
is cost-tracked via the shared Cost accountant.

- adjacent  (ideas 2/3): given the previous and current page -> NEW | SAME.
- range_probe (idea 4) : given a document's first page and a candidate later page ->
                         SAME_DOC | NEW_DOC. This is the oracle the binary search would call.
"""

import io
import sys

import images
import verdict_cache
from genai_client import classify_enum, classify_enum_async, generate_json
from google.genai import types
from pypdf import PdfReader, PdfWriter

# Reuse the production segmentation prompt + schema + tolerant parser for the window oracle
# (Solution 1), so it faithfully reproduces the current /getPages behavior within a window.
sys.path.insert(0, r"P:\MRR_AI_Source\mrr-line_source")
from mrr_ai.services.gemini import (  # noqa: E402
    SEGMENT_RESPONSE_SCHEMA,
    SEGMENTATION_PROMPT,
    parse_segment_item,
)

_SEG_SYS = (
    "You are an expert medical-records clerk. You split scanned workers' compensation "
    "medical-record files into their component documents and report exact page ranges "
    "and metadata."
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
_BELONGS_SYS = (
    "You segment a scanned medical-record PDF into its sub-documents. You are shown several "
    "pages of the document currently being assembled (its first page, a middle page, and its "
    "most recent page), then a CANDIDATE next page. Using the whole document's identity - not "
    "just the page immediately before - decide whether the candidate continues the SAME "
    "document or begins a NEW (different) document."
)


def _part(pdf_path, page_number, dpi):
    return types.Part.from_bytes(data=images.render_png(pdf_path, page_number, dpi), mime_type="image/png")


def adjacent(pdf_path, page_number, cost, dpi=150):
    """Is `page_number` the start of a new document vs the page before it? -> 'NEW'|'SAME'|None.
    Disk-cached (resume/no-respend); page rendering is skipped entirely on a cache hit."""
    def _compute():
        contents = [
            "First image = previous page. Second image = current page. "
            "Does the current page start a NEW document or continue the SAME one?",
            _part(pdf_path, page_number - 1, dpi),
            _part(pdf_path, page_number, dpi),
        ]
        return classify_enum(contents, ("NEW", "SAME"), _ADJACENT_SYS, cost)

    return verdict_cache.cached(pdf_path, "adjacent", f"p={page_number}", dpi, cost, _compute)


def range_probe(pdf_path, start_page, candidate_page, cost, dpi=150):
    """Does `candidate_page` belong to the document that began at `start_page`?
    -> 'SAME_DOC'|'NEW_DOC'|None. Disk-cached like adjacent()."""
    def _compute():
        contents = [
            "First image = the first page of a document. Second image = a later candidate page. "
            "Does the candidate still belong to the SAME document, or a NEW one?",
            _part(pdf_path, start_page, dpi),
            _part(pdf_path, candidate_page, dpi),
        ]
        return classify_enum(contents, ("SAME_DOC", "NEW_DOC"), _RANGE_SYS, cost)

    return verdict_cache.cached(pdf_path, "range_probe", f"s={start_page},c={candidate_page}",
                                dpi, cost, _compute)


def belongs_to_doc(pdf_path, doc_first, candidate_page, cost, dpi=150):
    """Does `candidate_page` continue the document assembled so far (Solution 5)?

    The document currently runs [doc_first, candidate_page - 1]. Represent it with THREE pages -
    its first (identity), a middle page (mid-document context), and its most recent page (local
    continuity) - then show the candidate. Deduped + ordered, so a 1-2 page document naturally
    collapses to fewer anchor images. -> 'SAME_DOC'|'NEW_DOC'|None. Disk-cached by (doc_first,
    candidate): the middle/preceding pages are a deterministic function of those two, so they
    fully key the call."""
    prev = candidate_page - 1
    middle = (doc_first + prev) // 2
    anchors = sorted({doc_first, middle, prev})  # pages already in the doc: first, middle, latest

    def _compute():
        contents = [
            f"Images 1-{len(anchors)} are pages already in the current document, in reading "
            "order (its first page, a middle page, and its most recent page). The LAST image is "
            "the candidate next page. Does the candidate continue the SAME document or begin a "
            "NEW one?",
            *[_part(pdf_path, a, dpi) for a in anchors],
            _part(pdf_path, candidate_page, dpi),
        ]
        return classify_enum(contents, ("SAME_DOC", "NEW_DOC"), _BELONGS_SYS, cost)

    return verdict_cache.cached(pdf_path, "belongs", f"f={doc_first},c={candidate_page}",
                                dpi, cost, _compute)


def adjacent_text(prev_markdown, cur_markdown, cost):
    """Text version of the adjacent oracle (Solution 3): markdown of the two pages -> NEW|SAME."""
    contents = [
        f"PREVIOUS PAGE (markdown):\n{prev_markdown}\n\n"
        f"CURRENT PAGE (markdown):\n{cur_markdown}\n\n"
        "Does the current page start a NEW document or continue the SAME one?"
    ]
    return classify_enum(contents, ("NEW", "SAME"), _ADJACENT_SYS, cost)


# --- async oracle variants (for the concurrent bake-off path) --------------------------------


async def adjacent_async(pdf_path, page_number, cost, dpi=150):
    """Async twin of adjacent(). Page rendering is local/inline; only the Gemini call awaits."""
    contents = [
        "First image = previous page. Second image = current page. "
        "Does the current page start a NEW document or continue the SAME one?",
        _part(pdf_path, page_number - 1, dpi),
        _part(pdf_path, page_number, dpi),
    ]
    return await classify_enum_async(contents, ("NEW", "SAME"), _ADJACENT_SYS, cost)


async def adjacent_text_async(prev_markdown, cur_markdown, cost):
    """Async twin of adjacent_text() (Solution 3)."""
    contents = [
        f"PREVIOUS PAGE (markdown):\n{prev_markdown}\n\n"
        f"CURRENT PAGE (markdown):\n{cur_markdown}\n\n"
        "Does the current page start a NEW document or continue the SAME one?"
    ]
    return await classify_enum_async(contents, ("NEW", "SAME"), _ADJACENT_SYS, cost)


# Vertex caps a single request at 20 MB AFTER base64 expansion (inline data is base64-encoded, +~33%).
# Window sub-PDFs go INLINE (the Files API is Gemini-Developer-only and rejected on Vertex --
# python-genai #1803), so guard the *encoded* size with a margin for the prompt/JSON envelope. This
# fails an oversized window loud here, with a clear message, instead of an opaque API rejection.
_INLINE_REQUEST_CAP_BYTES = 20 * 1024 * 1024
_INLINE_ENVELOPE_MARGIN_BYTES = 256 * 1024  # prompt + response-schema + request framing headroom


def window_segment(pdf_path, start_page, end_page, cost):
    """Window oracle (Solution 1): segment pages [start_page, end_page] in one call.

    Builds the page range into an in-memory PDF and sends it INLINE (types.Part.from_bytes,
    application/pdf) with the production segmentation prompt; returns absolute (start, end) spans.
    Local page numbers are offset to absolute. Inline -- not client.files.upload -- because the Files
    API is unsupported on Vertex AI, the BAA-covered endpoint (python-genai #1803).
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for p in range(start_page - 1, end_page):
        writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()
    encoded_bytes = (len(pdf_bytes) + 2) // 3 * 4  # base64 expands 3 raw bytes -> 4 encoded
    if encoded_bytes > _INLINE_REQUEST_CAP_BYTES - _INLINE_ENVELOPE_MARGIN_BYTES:
        raise RuntimeError(
            f"window {start_page}-{end_page} is {len(pdf_bytes) / 1048576:.1f} MB raw "
            f"(~{encoded_bytes / 1048576:.1f} MB base64), over the 20 MB inline request cap; "
            f"shrink the window/chunk or route this segment via a GCS gs:// URI"
        )

    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    data = generate_json([part, SEGMENTATION_PROMPT], _SEG_SYS, cost,
                         response_schema=SEGMENT_RESPONSE_SCHEMA) or []
    spans = []
    for item in data:
        try:
            s, e, *_ = parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            continue
        spans.append((s + start_page - 1, e + start_page - 1))
    return spans
