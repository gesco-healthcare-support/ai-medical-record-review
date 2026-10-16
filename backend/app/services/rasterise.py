"""Rasterise PDF pages to image parts, for backends that cannot take an inline PDF.

WHY THIS EXISTS. vLLM cannot carry a PDF at all. `llm/vllm.py` builds its request through
`llm/openai.py::_to_messages`, which raises TypeError on a `DocumentPart` because chat completions
has no inline-PDF part at all. So every service that sends a whole document has to have an image
path before it can cross the seam onto that backend. Gemini keeps taking the PDF; nothing routed
there reaches this module.

THE PAGE CAP IS A PARAMETER, and that is why this is a module rather than a copied loop.
Summarization caps a row at `summary_image_max_pages` (15); a segmentation window runs to
`window_max_pages` (100). A shared helper that baked in either number would silently truncate the
other caller - an 85-page hole in a segmentation window, reported as nothing at all, because a short
window looks exactly like a short document. So `max_pages` is REQUIRED and positional, mirroring
`windows.byte_budgeted_windows`, where the same argument is required for the same stated reason: a
caller that silently skips the cap is the failure mode the parameter exists to prevent.
"""

import io
import os
import threading
from collections import OrderedDict

from pdf2image import convert_from_path
from pypdf import PdfReader

from app.config import get_settings
from app.services.llm import ImagePart, TextPart

# JPEG at quality 70, unchanged from the summarize rasteriser this was extracted from. These are
# page scans: JPEG's loss is invisible against an OCR-grade source, and the byte saving is what
# keeps a multi-page payload inside one request.
_JPEG_QUALITY = 70

# Every page's crop-box long edge, per file, so a record's PDF is parsed ONCE rather than once per
# call. MEASURED 2026-10-16 on a 5,077-page, 480 MB record: opening it with pypdf and reaching one
# page's box cost 2.3s of pure Python per call, against 0.2s for Poppler to render that page. The
# injury-date read calls this once per row, so 1,637 rows spent ~63 min holding the GIL - the
# stage took 79 min with the model idle and the worker on one core. Reading all 5,077 boxes once
# costs 2.25s. On an ordinary 377-page record the per-call cost was 0.2s, which is why it hid.
#
# Keyed on (path, size, mtime) rather than the path alone, so a file replaced in place is read
# again; a path that cannot be stat'ed is not cached at all. Bounded, because one worker process
# meets many records over its life. Under a lock, because the callers run in thread pools
# (project-conventions: a module-level cache a pool touches needs one), and held while reading so
# twelve workers starting on the same record parse it once rather than twelve times.
_EDGES_CACHE_SIZE = 8
_edges_cache: "OrderedDict[tuple, tuple[float, ...]]" = OrderedDict()
_edges_lock = threading.Lock()


def _long_edge(box) -> float:
    return max(float(box.width), float(box.height))


def _crop_long_edges(pdf_path) -> tuple[float, ...]:
    """Every page's crop-box long edge in points, read once per file. See `_edges_cache`."""
    try:
        stat = os.stat(pdf_path)
        key = (os.fspath(pdf_path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = None
    with _edges_lock:
        if key is not None and key in _edges_cache:
            _edges_cache.move_to_end(key)
            return _edges_cache[key]
        edges = tuple(_long_edge(page.cropbox) for page in PdfReader(pdf_path).pages)
        if key is not None:
            _edges_cache[key] = edges
            while len(_edges_cache) > _EDGES_CACHE_SIZE:
                _edges_cache.popitem(last=False)
        return edges


def page_dpi(reader, page, settings, long_edge_px=None):
    """Render DPI for one page, lowered so its long edge lands on the requested pixel target.

    ``long_edge_px`` defaults to ``summary_image_long_edge_px``, so a caller that omits it renders
    exactly as this did before the parameter existed. It is a PARAMETER for the same reason
    ``page_image_parts`` takes its page cap as one: a second caller wanting a different value had no
    way to ask, and reading the summarize setting inside a shared helper silently imposes
    summarize's choice on everyone. The DOI read supplies its own, because reading a labelled date
    field is a different task from judging page layout and the 1024 figure was measured on the
    latter.

    THE DPI CEILING STILL BINDS, and it is easy to miss. ``summary_image_dpi`` (120) caps the
    return, so a letter page cannot exceed about 1320 px on its long edge no matter what is asked
    for here - a caller requesting 2200 silently gets ~1320. Raise ``summary_image_dpi`` too, or the
    request is a wish rather than an instruction.

    A DPI is not a resolution. It is a resolution only relative to a page's declared box, and a
    scanned PDF declares whatever its producer felt like: two thirds of the benchmark corpus sets the
    box EQUAL to the pixel count, so "120 dpi" silently means a 1.67x upscale there and a normal
    render elsewhere. Deriving the DPI per page from the box makes the OUTPUT the fixed thing rather
    than the instruction.

    A SENTENCE WAS REMOVED HERE WHEN THIS MOVED. It read "which is what `SEGMENT_LONG_EDGE_PX`
    already does on the segmentation pass", and it was wrong twice: no such constant exists anywhere
    in this repo, and segmentation sent whole PDFs rather than images, so it rendered nothing to have
    a long edge. Segmentation reaches this function for the first time through the vLLM path that
    introduced this module.

    Uses the CROP box, because that is the region Poppler actually renders; pypdf falls back to the
    media box when no crop box is set. Orientation is handled by taking the longer side rather than
    reading ``/Rotate``, so a landscape scan is capped on its long edge too.

    NOTE this lowers a NORMAL letter page as well, from 1020x1320 to about 791x1024. That is
    deliberate - both passes now see the same page at the same size - but it IS a change to what the
    summarizer reads, and it was never separately A/B'd for summarization. See
    ``summary_image_long_edge_px``.
    """
    return _dpi_for_long_edge(_long_edge(reader.pages[page - 1].cropbox), settings, long_edge_px)


def _dpi_for_long_edge(long_edge_pt, settings, long_edge_px=None):
    """`page_dpi`'s arithmetic on a long edge already read - so `page_image_parts` can take the edge
    from `_crop_long_edges` instead of opening the PDF on every call."""
    target = long_edge_px or settings.summary_image_long_edge_px
    if long_edge_pt <= 0:  # a degenerate box would divide by zero; fall back rather than guess
        return settings.summary_image_dpi
    fitted = int(target * 72.0 / long_edge_pt)
    # Never raise the DPI above the configured one, and never fall to zero on an absurd box.
    return max(1, min(settings.summary_image_dpi, fitted))


# Emitted ONCE before the labelled images, never per page.
#
# The labels are a monotonic run - "Page 1", "Page 2", "Page 3" - and that is ALSO exactly what
# one document's own pagination looks like. Measured on the six reviewer-corrected records
# (2026-09-23): boundary recall on ONE-PAGE documents was 77.6% against 95.3% on every other
# length, inside a population where the scoring bias is identical across lengths, so the gap is
# the model's and not the metric's. 78% of all remaining missed boundaries sat on documents of
# one or two pages, and the misses arrived in consecutive runs (22,23,24,25 / 146,147,148,149) -
# the signature of several one-page documents read as one paginated document.
#
# Labelling itself is not the error and must not be reverted: it took off-by-one misplacement
# from 88 to 26 over those same records. This says what a label MEANS, which the label alone
# cannot.
_LABEL_PREAMBLE = (
    "Each image below is preceded by its page label. A label gives that page's POSITION in "
    "this file and nothing else. Consecutive pages are frequently SEPARATE documents, and many "
    "documents here are a single page long, so a label following the previous one never implies "
    "the two pages belong to the same document."
)


def page_image_parts(pdf_path, start, end, max_pages, long_edge_px=None, label_pages=False):
    """Rasterize pages [start, end] to lean JPEG ``ImagePart``s, at most ``max_pages`` of them.

    ``max_pages`` is required and has no default - see the module docstring.

    ``label_pages`` puts a ``Page N`` text part immediately BEFORE each image, numbered from 1
    WITHIN THIS CALL. Default off: it costs a few tokens per page and only earns them where the
    model has to report a page POSITION back, which summarization and the DOI read do not.

    It also emits ``_LABEL_PREAMBLE`` ONCE before the first image, because the labels alone
    proved ambiguous: a monotonic run reads as one document's pagination. See that constant.

    A bare list of images carries no position at all, and a PDF part does - its container has
    discrete pages, so the model reads a page number rather than deriving one. So a caller that
    crossed from Gemini onto a rasterised backend silently swapped 'read the number off the
    container' for 'count the images'. `segment_engine._window_parts` records what that cost
    when it was measured.

    Rasterized ONE PAGE AT A TIME, which is deliberate rather than naive. The loop looks like an
    obvious optimisation: `convert_from_path` spawns a Poppler subprocess and re-parses the PDF on
    every call, so a 15-page row pays that 15 times instead of once. MEASURED 2026-08-31 on a
    13.7 MB 229-page record, 15 pages at 120 dpi, best of 3:

        per page (this)      2.32s     peak RSS  +12 MB
        one batched call     1.06s     peak RSS +155 MB

    Identical JPEG bytes either way. So batching is 2.2x faster and costs 143 MB more per CONCURRENT
    ROW - and that is the number that decides it, because `pipeline_workers` is 5: 5 x 155 MB against
    5 x 12 MB, on a box that also runs Postgres, Redis, six RQ workers and two web tiers. The saving
    is 1.26s against a row costing ~38s in model time, so about 3%, for ~700 MB of peak.

    Not worth it, and the memory argument gets SHARPER now rather than weaker: a segmentation window
    runs to 100 pages against summarization's 15, so the batched peak would be several times the
    figure measured above. Recorded with numbers so the next person tempted by the loop does not have
    to re-measure it.
    """
    settings = get_settings()
    last = min(int(end), int(start) + int(max_pages) - 1)
    # The page boxes come from a per-file cache, NOT a fresh PdfReader per call. This comment used
    # to say one reader per call was "noise next to" the Poppler subprocesses - true of an ordinary
    # record (0.2s), false of a 5,000-page one (2.3s per call against 0.2s per render). See
    # `_edges_cache` for the measurement.
    edges = _crop_long_edges(pdf_path)
    parts = []
    if label_pages:
        # once per call, not per page: it is a statement about the labels, not about a page.
        parts.append(TextPart(_LABEL_PREAMBLE))
    for offset, page in enumerate(range(int(start), last + 1), start=1):
        if label_pages:
            # BEFORE the image, so the label reads as naming what follows rather than what
            # preceded it. `_to_messages` puts every part in ONE user message and preserves
            # caller order, so the pairing survives the transport.
            parts.append(TextPart(f"Page {offset}"))
        for image in convert_from_path(
            pdf_path,
            first_page=page,
            last_page=page,
            dpi=_dpi_for_long_edge(edges[page - 1], settings, long_edge_px),
        ):
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=_JPEG_QUALITY)
            parts.append(ImagePart(data=buffer.getvalue(), mime_type="image/jpeg"))
    return parts
