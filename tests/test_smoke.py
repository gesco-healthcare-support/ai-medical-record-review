"""Route-smoke safety net for the modularization.

Asserts every route stays registered and the GET page routes still render. Written to
work BOTH against the pre-refactor app.py and the post-refactor mrr_ai factory, so the
same assertions guard the move. LLM/PHI POST routes are only checked for registration,
never invoked.
"""

import os

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest

try:  # post-refactor
    from mrr_ai import create_app

    flask_app = create_app()
except ModuleNotFoundError:  # pre-refactor baseline
    from app import app as flask_app

EXPECTED_ROUTES = {
    "/", "/upload", "/uploadAndCheckCSV", "/uploadPages", "/summarize",
    "/getDiagOpRep", "/getDepoRep", "/exportresultstoCSV", "/exportresultstoword",
    "/exportResultsToWordFileIndivRecords", "/reset", "/getpatientnameanddob",
    "/getlawfirm", "/pages", "/pagesManual", "/pdfsegment", "/checkCSV",
    "/DiagAndOpReports", "/DepositionReports", "/IndividualMRR",
    "/create_patient_folder_indiv_mrr", "/upload_files", "/compute_page_ranges",
    "/summarize_indiv_record", "/segmentPDF", "/getPages",
}  # fmt: skip

GET_PAGES = [
    "/", "/pages", "/pagesManual", "/pdfsegment", "/checkCSV",
    "/DiagAndOpReports", "/DepositionReports", "/IndividualMRR",
]  # fmt: skip


@pytest.fixture
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


def test_all_routes_registered():
    rules = {r.rule for r in flask_app.url_map.iter_rules()}
    missing = EXPECTED_ROUTES - rules
    assert not missing, f"missing routes: {sorted(missing)}"


@pytest.mark.parametrize("path", GET_PAGES)
def test_get_pages_render(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
