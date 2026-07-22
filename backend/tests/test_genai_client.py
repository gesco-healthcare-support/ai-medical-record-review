"""The genai client bounds every request with an HTTP timeout (pipeline forever-hang fix).

Without http_options.timeout a stalled Vertex call blocks a worker thread forever; with it, a stall
raises an httpx timeout that generate_with_retry already retries. HttpOptions.timeout is in ms.
"""

from app.config import get_settings
from app.services import genai_client


def _capture_client_kwargs(monkeypatch) -> dict:
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(genai_client.genai, "Client", _FakeClient)
    genai_client.get_genai_client.cache_clear()
    return captured


def test_client_bounds_http_timeout_api_key_branch(monkeypatch):
    captured = _capture_client_kwargs(monkeypatch)
    try:
        genai_client.get_genai_client()
        assert captured["http_options"].timeout == 120000
    finally:
        genai_client.get_genai_client.cache_clear()


def test_client_bounds_http_timeout_vertex_project_branch(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_vertex", True)
    monkeypatch.setattr(settings, "google_cloud_project", "proj-123")
    captured = _capture_client_kwargs(monkeypatch)
    try:
        genai_client.get_genai_client()
        assert captured["vertexai"] is True
        assert captured["http_options"].timeout == 120000
    finally:
        genai_client.get_genai_client.cache_clear()
