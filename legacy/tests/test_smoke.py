"""Route-smoke safety net.

Asserts every legacy route stays registered and the GET page routes still render for an
authenticated user (conftest's ``client`` logs in; the app denies everything anonymous).
LLM/PHI POST routes are only checked for registration, never invoked.
"""

import pytest

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


def test_all_routes_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    missing = EXPECTED_ROUTES - rules
    assert not missing, f"missing routes: {sorted(missing)}"


@pytest.mark.parametrize("path", GET_PAGES)
def test_get_pages_render(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
