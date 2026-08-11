"""Integration tests for the CSV + Word export routes."""

from mrr_ai import state

_ENTRY = {
    "summaryDate": "01/02/2020",
    "summaryTitle": "Operative Report",
    "summaryText": "Summary.",
}


def test_export_results_to_csv(client, home_tmp):
    state.main_filename = "summary"

    resp = client.post("/exportresultstoCSV", json={"TXTText": "1,2,8,01/02/2020,-,-"})

    assert resp.status_code == 200
    assert resp.get_json()["message"].startswith("Content saved")
    out = home_tmp / "MRRs" / "summary.csv"
    assert out.read_text(encoding="utf-8") == "1,2,8,01/02/2020,-,-"


def test_export_results_to_word(client, home_tmp):
    state.all_data = [_ENTRY]
    state.num_pages = 5
    state.main_filename = "summary"

    resp = client.post(
        "/exportresultstoword",
        json={
            "patientName": "Pat",
            "patientdob": "01/01/1990",
            "QMEorAME": "QME",
            "lawfirm": "Firm",
        },
    )

    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers["Content-Type"]
    assert (home_tmp / "MRRs" / "summary_int.docx").exists()


def test_export_results_to_word_indiv(client, home_tmp):
    state.all_data = [_ENTRY]
    state.num_pages = 3

    resp = client.post(
        "/exportResultsToWordFileIndivRecords",
        json={
            "patientName": "Pat",
            "patientdob": "01/01/1990",
            "QMEorAME": "QME",
            "lawfirm": "Firm",
        },
    )

    assert resp.status_code == 200
    assert (home_tmp / "MRRs" / "Pat - MRR.docx").exists()
