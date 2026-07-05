"""UI page routes (template renders) and session reset."""

from flask import Blueprint, render_template

from mrr_ai import state

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
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
    return render_template("review.html")


@bp.route("/reset", methods=["POST"])
def reset():
    # Clear the session data
    state.pdf_filepath = None
    state.main_filename = "summary"
    state.all_data = []
    state.pages_not_counting = 0

    return {"message": "Session data cleared successfully"}, 200
