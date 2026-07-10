"""UI page routes (template renders) and session reset."""

from flask import Blueprint, redirect, render_template

from mrr_ai import state

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    """Landing page: the user's documents. The classic hub moved to /classic."""
    return render_template("home.html")


@bp.route("/classic")
def classic():
    return render_template("index.html")


@bp.route("/pages")
def pages():
    return render_template("pages.html")


@bp.route("/pagesManual")
def pagesManual():
    return render_template("pagesManual.html")


@bp.route("/pdfsegment")
def pdfsegment():
    return render_template("pdfsegment.html")


@bp.route("/checkCSV")
def checkCSV():
    return render_template("checkCSV.html")


@bp.route("/DiagAndOpReports")
def DiagAndOpReports():
    return render_template("DiagAndOpReports.html")


@bp.route("/DepositionReports")
def DepositionReports():
    return render_template("DepositionReports.html")


@bp.route("/IndividualMRR")
def IndividualMRR():
    return render_template("individual_mrr.html")


@bp.route("/review")
def review():
    # The editor is document-scoped now; bare /review has nothing to show.
    return redirect("/")


@bp.route("/review/<document_id>")
def review_document(document_id):
    """Editor page shell; all data loads through the owner-checked documents API,
    so rendering the shell for a foreign id leaks nothing (the API answers 404)."""
    return render_template("review.html", document_id=document_id)


# Category-bundle workspaces: upload-or-pick a record, review it with the shared editor,
# then extract/summarize just the matching-category documents. One template, two category
# sets (the only difference between the pages).
@bp.route("/diagnostics")
def diagnostics():
    return render_template(
        "bundle.html",
        bundle_label="Diagnostic & Operative",
        bundle_slug="diagnostic-operative",
        categories=["3", "8"],
    )


@bp.route("/depositions")
def depositions():
    return render_template(
        "bundle.html",
        bundle_label="Depositions",
        bundle_slug="depositions",
        categories=["9"],
    )


@bp.route("/reset", methods=["POST"])
def reset():
    # Clear the session data
    state.pdf_filepath = None
    state.main_filename = "summary"
    state.all_data = []
    state.pages_not_counting = 0

    return {"message": "Session data cleared successfully"}, 200
