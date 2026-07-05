"""Sub-document segmentation routes (sliding-window engine underneath)."""

from flask import Blueprint, request

from mrr_ai import state
from mrr_ai.services.pdf import get_pdf_page_count, segment_pdf
from mrr_ai.services.segment_engine import run_segmentation

bp = Blueprint("segmentation", __name__)


def rows_to_csv_lines(rows):
    """The 6-column CSV contract: start,end,category,doc_date,injury_date,manual_flag."""
    return [
        f"{r['start']},{r['end']},{r['category']},{r['date']},{r['injury_date']},{r['flag']}"
        for r in rows
    ]


@bp.route("/segmentPDF", methods=["POST"])
def segmentPDF():
    print("inside segmented PDF")

    segment_pdf(state.pdf_filepath, pages_per_segment=100)
    return {"pages": "File segmentation finalyzed. You can get the files from the MRR folder."}


# Automatic segmentation upload
@bp.route("/getPages", methods=["POST"])
def getPages():
    # pageDelimiter is accepted for backward compatibility but no longer used: windows
    # are byte-budgeted (Vertex inline cap) and overlap (no document severed at a seam).
    _ = request.json or {}

    total_pages = get_pdf_page_count(state.pdf_filepath)
    if not total_pages:
        return {"pages": "ERROR: no readable PDF uploaded"}

    try:
        rows = run_segmentation(state.pdf_filepath, total_pages)
    except Exception as e:
        # The old UI renders whatever string lands in "pages"; keep that contract.
        print(f"An error occurred during segmentation: {e}")
        return {"pages": str(e)}

    return {"pages": "\n".join(rows_to_csv_lines(rows))}
