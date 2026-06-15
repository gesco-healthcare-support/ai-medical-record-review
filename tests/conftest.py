"""Shared pytest fixtures.

External services (OpenAI, Gemini, Tesseract/Poppler) are never called: the LLM clients
are replaced with in-memory fakes and OCR is monkeypatched per test. All inputs are
synthetic - tiny blank PDFs and hand-built CSV strings - so no API keys, network, or
patient data are ever involved.
"""

import io
import os
from types import SimpleNamespace

# Dummy secrets MUST be set before importing mrr_ai: importing the package runs
# extensions.validate_env() and builds the genai/OpenAI clients at module load.
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import pytest
from pypdf import PdfWriter

# State globals that routes mutate, with their declared defaults from mrr_ai/state.py.
# Reset before every test so cross-test ordering never leaks state (the app is
# single-process by design).
_STATE_DEFAULTS = {
    "pdf_filepath": None,
    "txt_filepath": None,
    "main_filename": "summary",
    "main_txt_filename": "txt_pages",
    "patientNameGlobal": "Patient Full Name",
    "pages_not_counting": 0,
    "num_pages": 0,
    "all_data": [],
    "manual_intervention": "",
    "indiv_mrr_folder_path": "",
    "sorted_file_paths": [],
}


@pytest.fixture(autouse=True)
def reset_state():
    """Restore mutable application state to defaults before each test."""
    from mrr_ai import state

    for name, value in _STATE_DEFAULTS.items():
        # Copy mutable defaults so a test mutating a list/dict cannot poison the next.
        setattr(state, name, value.copy() if isinstance(value, (list, dict)) else value)
    yield


@pytest.fixture
def app(tmp_path):
    """A fresh Flask app with TESTING on and the upload folder pointed at a temp dir."""
    from mrr_ai import create_app

    application = create_app()
    upload_folder = tmp_path / "uploads"
    upload_folder.mkdir(parents=True, exist_ok=True)
    application.config.update(TESTING=True, UPLOAD_FOLDER=str(upload_folder))
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def home_tmp(tmp_path, monkeypatch):
    """Redirect ``~`` to a temp dir (cross-platform) and pre-create ``~/MRRs``.

    Routes that assemble Word/CSV/PDF output write under ``os.path.expanduser('~')/MRRs``.
    """
    home = tmp_path / "home"
    (home / "MRRs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def make_pdf():
    """Return a factory that writes a tiny blank PDF of ``pages`` pages to ``path``."""

    def _make(path, pages=1):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=72, height=72)
        with open(path, "wb") as handle:
            writer.write(handle)
        return str(path)

    return _make


@pytest.fixture
def pdf_bytes():
    """Return a factory producing the raw bytes of a tiny blank ``pages``-page PDF."""

    def _bytes(pages=1):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=72, height=72)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    return _bytes


@pytest.fixture
def fake_openai():
    """Return a factory building a stand-in OpenAI client.

    ``content`` may be a string (returned verbatim) or a callable ``(**kwargs) -> str``
    so a test can vary the reply by call (e.g. summary vs title extraction).
    """

    def _build(content):
        def create(**kwargs):
            text = content(**kwargs) if callable(content) else content
            message = SimpleNamespace(content=text)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        completions = SimpleNamespace(create=create)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    return _build


@pytest.fixture
def fake_genai():
    """Return a factory building a stand-in Gemini client whose response ``.text`` is set.

    If ``text`` is an ``Exception`` instance it is raised, exercising the route's
    error branch.
    """

    def _build(text):
        def generate_content(**kwargs):
            if isinstance(text, Exception):
                raise text
            return SimpleNamespace(text=text)

        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    return _build
