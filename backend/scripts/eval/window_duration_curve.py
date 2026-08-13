"""Measure how long ONE segmentation vision window takes as its page count grows.

This exists because of the 2026-08-12 incident. `byte_budgeted_windows` caps a window by BYTES
(window_budget_mb, 12.5) and caps nothing else, so a byte-LIGHT PDF packs an enormous page count
into a single call: document 68cb2500 is ~52KB/page, so all 241 of its pages landed in ONE window
and segmentation failed 6/6 times on a server-side deadline. Across the 30 documents that have
segment jobs, failure tracks MAX PAGES PER WINDOW, not page count - the 793- and 2673-page records
are image-heavy, split into 26 and 48 small windows, and never failed.

The point of this script is to set the page cap from a curve rather than a guess. An earlier
attempt at this fix picked "300s" out of the air; that is the mistake being corrected.

DELIBERATELY calls client.models.generate_content directly instead of segment_engine._window_rows.
The retry wrapper's exponential backoff and the cross-process Redis pacer both add wall-clock that
is not model time, and including them would confound the one number being measured. The payload,
prompt and generation config are identical to production: same sub-PDF construction, same
SEGMENTATION_PROMPT, same _generation_config (temperature 0, response_schema, dynamic thinking).

A long client deadline is used so the deadline does not bind. That also answers a question the
incident left open: whether Vertex honours a deadline materially above 120s at all. Only 8s and
120s were ever proven honoured, so if a call here runs past 120s and still succeeds, it is proven.

PHI: prints page counts, payload sizes, timings, token counts and reply LENGTH only. No record
text is read into the output. Creates no job and touches no stored row.

Usage:
    python scripts/eval/window_duration_curve.py <document_id> [20,40,80,120,160,200,241]
"""

from __future__ import annotations

import io
import sys
import time

from google import genai
from google.genai import types
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document
from app.services.gemini import SEGMENTATION_PROMPT
from app.services.segment_engine import _generation_config

# High enough not to bind at any rung of the ladder; the whole point is to let the call finish.
LONG_TIMEOUT_MS = 600_000
DEFAULT_LADDER = [20, 40, 80, 120, 160, 200, 241]


def _client() -> genai.Client:
    """A client with a deliberately long deadline, mirroring genai_client.get_genai_client.

    Built here rather than reused because get_genai_client is lru_cached and takes its timeout from
    settings.genai_http_timeout_ms, which is the value under investigation.
    """
    settings = get_settings()
    http_options = types.HttpOptions(timeout=LONG_TIMEOUT_MS)
    if settings.use_vertex and settings.google_cloud_project:
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
            http_options=http_options,
        )
    raise SystemExit("this measurement expects the Vertex path with GOOGLE_CLOUD_PROJECT set")


def _window_part(pdf_path: str, first_page: int, last_page: int) -> tuple[types.Part, int]:
    """The inline PDF part for pages [first_page, last_page], plus its size in bytes.

    Mirrors segment_engine._window_rows exactly, so the payload measured is the payload production
    sends.
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for page in range(first_page - 1, last_page):
        writer.add_page(reader.pages[page])
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    return types.Part.from_bytes(data=data, mime_type="application/pdf"), len(data)


def _tokens(response) -> str:
    """Prompt/thinking/output token counts when the response carries usage metadata."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return "-"
    prompt = getattr(usage, "prompt_token_count", None)
    thoughts = getattr(usage, "thoughts_token_count", None)
    output = getattr(usage, "candidates_token_count", None)
    return f"in={prompt} think={thoughts} out={output}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    document_id = argv[0]
    ladder = [int(n) for n in argv[1].split(",")] if len(argv) > 1 else DEFAULT_LADDER

    session = get_sessionmaker()()
    document = session.get(Document, document_id)
    if document is None:
        raise SystemExit(f"no document {document_id}")

    settings = get_settings()
    client = _client()
    rungs = sorted({min(n, document.page_count) for n in ladder})
    print(
        f"document {document_id[:8]} pages={document.page_count} "
        f"location={settings.google_cloud_location} model={settings.genai_model} "
        f"thinking={settings.segment_thinking_budget} client_deadline={LONG_TIMEOUT_MS}ms"
    )
    print(f"production genai_http_timeout_ms = {settings.genai_http_timeout_ms}")
    print(f"{'pages':>6} {'payloadMB':>9} {'seconds':>8} {'reply':>6}  tokens / outcome")

    for pages in rungs:
        part, size = _window_part(document.stored_path, 1, pages)
        started = time.time()
        try:
            response = client.models.generate_content(
                model=settings.genai_model,
                contents=[part, SEGMENTATION_PROMPT],
                config=_generation_config(),
            )
        except Exception as exc:  # noqa: BLE001 - a failed rung is data, not a crash
            elapsed = time.time() - started
            print(
                f"{pages:>6} {size / 1048576:>9.1f} {elapsed:>8.1f} {'-':>6}  "
                f"{type(exc).__name__}: {str(exc)[:90]}"
            )
            continue
        elapsed = time.time() - started
        reply_chars = len(response.text or "")
        print(
            f"{pages:>6} {size / 1048576:>9.1f} {elapsed:>8.1f} {reply_chars:>6}  {_tokens(response)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
