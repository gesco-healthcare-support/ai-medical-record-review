"""Integration tests for the page renders and the session reset route."""

from mrr_ai import state


def test_index_renders(client):
    assert client.get("/").status_code == 200


def test_reset_clears_session_state(client):
    state.all_data = [{"summaryDate": "01/02/2020"}]
    state.pages_not_counting = 5
    state.pdf_filepath = "/some/path.pdf"

    resp = client.post("/reset")

    assert resp.status_code == 200
    assert resp.get_json()["message"] == "Session data cleared successfully"
    assert state.all_data == []
    assert state.pages_not_counting == 0
    assert state.pdf_filepath is None
