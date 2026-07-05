"""JSON API for the review UI: background segmentation, row editing, summarization.

The UI polls /api/*/status because the underlying work takes minutes (Gemini windows,
per-document OpenAI calls); jobs run on the single-slot runner in services/jobs.py.
"""

from flask import Blueprint, jsonify, request, send_file

from mrr_ai import state
from mrr_ai.services import jobs
from mrr_ai.services.pdf import get_pdf_page_count
from mrr_ai.services.segment_engine import run_segmentation
from mrr_ai.services.summarize_engine import summarize_row
from mrr_ai.taxonomy import CATEGORIES

bp = Blueprint("review_api", __name__)

# Categories the editor may assign. "6" exists in the legacy prompt set but not in the
# curated taxonomy; the summarize engine handles it, so it stays selectable.
EDITABLE_CATEGORIES = sorted({*CATEGORIES.keys(), "6"}, key=lambda c: int(c))

DEFAULT_SUMMARY_MODEL = "gpt-4o-mini"


def validate_rows(rows, total_pages):
    """Return an error string for the first invalid row, or None.

    Rules mirror the client: integer pages, 1 <= start <= end <= total, ascending and
    non-overlapping (gaps are ALLOWED - users deliberately skip junk pages), known
    category. The list drives page slicing, so violations must stop the pipeline here.
    """
    if not rows:
        return "no rows to summarize"
    previous_end = 0
    for i, row in enumerate(rows, start=1):
        try:
            start, end = int(row["start"]), int(row["end"])
        except (KeyError, TypeError, ValueError):
            return f"row {i}: start/end must be integers"
        if not 1 <= start <= end <= total_pages:
            return f"row {i}: pages must satisfy 1 <= start <= end <= {total_pages}"
        if start <= previous_end:
            return f"row {i}: overlaps or is out of order with the previous row"
        previous_end = end
        if str(row.get("category")) not in EDITABLE_CATEGORIES:
            return f"row {i}: unknown category {row.get('category')!r}"
    return None


@bp.route("/api/pdf")
def api_pdf():
    if not state.pdf_filepath:
        return jsonify({"error": "no PDF uploaded"}), 404
    # Inline disposition so the browser's built-in viewer renders it in the iframe.
    return send_file(state.pdf_filepath, mimetype="application/pdf", conditional=True)


@bp.route("/api/segment/start", methods=["POST"])
def segment_start():
    if not state.pdf_filepath:
        return jsonify({"error": "no PDF uploaded"}), 400
    total_pages = get_pdf_page_count(state.pdf_filepath)
    if not total_pages:
        return jsonify({"error": "PDF is not readable"}), 400

    pdf_path = state.pdf_filepath
    started = jobs.clear() and jobs.start(
        "segment", lambda report: run_segmentation(pdf_path, total_pages, progress=report)
    )
    if not started:
        return jsonify({"error": "another job is still running"}), 409
    return jsonify({"ok": True, "total_pages": total_pages})


@bp.route("/api/segment/status")
def segment_status():
    snap = jobs.status()
    if snap.get("kind") == "segment" and snap.get("state") == "done":
        snap["rows"] = snap.pop("result")
        snap["categories"] = EDITABLE_CATEGORIES
    return jsonify(snap)


@bp.route("/api/summarize/start", methods=["POST"])
def summarize_start():
    if not state.pdf_filepath:
        return jsonify({"error": "no PDF uploaded"}), 400
    body = request.json or {}
    rows = body.get("rows") or []
    model = body.get("model") or DEFAULT_SUMMARY_MODEL

    total_pages = get_pdf_page_count(state.pdf_filepath)
    error = validate_rows(rows, total_pages)
    if error:
        return jsonify({"error": error}), 400

    pdf_path = state.pdf_filepath
    state.all_data = []  # the legacy Word export reads this after the run

    def target(report):
        summaries = []
        for i, row in enumerate(rows, start=1):
            report("summarizing", i - 1, len(rows))
            output = summarize_row(pdf_path, row, model)
            state.all_data.append(output)
            summaries.append({**output, "row": row})
            report("summarizing", i, len(rows))
        return summaries

    if not (jobs.clear() and jobs.start("summarize", target)):
        return jsonify({"error": "another job is still running"}), 409
    return jsonify({"ok": True, "count": len(rows)})


@bp.route("/api/summarize/status")
def summarize_status():
    snap = jobs.status()
    if snap.get("kind") == "summarize" and snap.get("state") == "done":
        snap["summaries"] = snap.pop("result")
    return jsonify(snap)
