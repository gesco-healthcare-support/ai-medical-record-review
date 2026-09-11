"""Startup preflight: the vLLM version floor, and the cases where it must refuse to boot.

No network. The probe itself is stubbed, so these pin the POLICY - when it runs, when it refuses,
and that it refuses rather than warning. Whether vLLM actually answers GET /version is not something
a stub can establish, and it was settled the only way it could be: by curling the live 0.28.0 build
(HTTP 200, {"version": "0.28.0"}, unauthenticated).

A REAL ``Settings`` IS BUILT, AND THE GLOBAL CACHE IS LEFT ALONE. An earlier version of this file
called ``get_settings.cache_clear()`` while a fake DATABASE_URL was in the environment, so any test
running afterwards could rebuild settings pointing at a different database - which showed up as a
foreign-key violation in an unrelated test's teardown, several files away. Injecting the instance
keeps the real config behaviour under test without reaching into state other tests share.
"""

import pytest

from app.config import Settings
from app.services.llm import preflight

_BASE = {
    "SECRET_KEY": "x" * 32,
    "SECURITY_PASSWORD_SALT": "y" * 16,
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "ENVIRONMENT": "dev",
}
_KEYS = (
    "LLM_BACKEND",
    "LLM_BACKEND_OVERRIDES",
    "VLLM_BASE_URL",
    "VLLM_MODEL",
    "VLLM_MIN_VERSION",
    "VLLM_APPROVED_ORIGINS",
    "SUMMARY_PROVIDER",
)
_VLLM = {
    "LLM_BACKEND": "vllm",
    "VLLM_BASE_URL": "http://127.0.0.1:8000/v1",
    "VLLM_MODEL": "Qwen/Qwen3.6-35B-A3B-FP8",
}


def _install_settings(monkeypatch, **env):
    """Build a real Settings from a known environment and hand it to preflight only."""
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {**_BASE, **env}.items():
        monkeypatch.setenv(name, value)
    settings = Settings()  # type: ignore[call-arg]
    monkeypatch.setattr(preflight, "get_settings", lambda: settings)
    return settings


def _probe_returning(monkeypatch, version):
    seen = {}

    def _fake(root):
        seen["root"] = root
        return version

    monkeypatch.setattr(preflight, "_probe_vllm_version", _fake)
    return seen


# --- when it runs at all ----------------------------------------------------------------------------


def test_the_gemini_path_is_not_probed_at_all(monkeypatch):
    """Gemini is what we roll BACK to, so it must not gain a new way to fail at boot.

    Asserted by making the probe raise if it is called. A test that merely checked for no exception
    would pass just as well if the probe silently succeeded, and would not notice the new network
    dependency this is meant to keep off the rollback path.
    """
    _install_settings(monkeypatch, LLM_BACKEND="gemini")

    def _explode(root):
        raise AssertionError(f"the gemini path must not probe anything, but it probed {root}")

    monkeypatch.setattr(preflight, "_probe_vllm_version", _explode)
    preflight.assert_backends_ready()


def test_a_backend_reachable_only_through_an_override_is_still_probed(monkeypatch):
    # One stage is enough to send records to the pod, so the global setting is not what decides this.
    _install_settings(
        monkeypatch,
        LLM_BACKEND="gemini",
        LLM_BACKEND_OVERRIDES="segment=vllm",
        VLLM_BASE_URL="http://127.0.0.1:8000/v1",
        VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
    )
    seen = _probe_returning(monkeypatch, "0.28.0")
    preflight.assert_backends_ready()
    assert seen["root"] == "http://127.0.0.1:8000"


# --- the floor ---------------------------------------------------------------------------------------


def test_a_version_below_the_floor_refuses_to_start(monkeypatch):
    _install_settings(monkeypatch, VLLM_MIN_VERSION="0.24.0", **_VLLM)
    _probe_returning(monkeypatch, "0.23.9")
    with pytest.raises(RuntimeError, match="below the required floor"):
        preflight.assert_backends_ready()


def test_the_refusal_names_both_versions(monkeypatch):
    # Whoever hits this needs to know what was served and what was required, not just that it failed.
    _install_settings(monkeypatch, VLLM_MIN_VERSION="0.24.0", **_VLLM)
    _probe_returning(monkeypatch, "0.11.1")
    with pytest.raises(RuntimeError, match=r"0\.11\.1.*0\.24\.0"):
        preflight.assert_backends_ready()


def test_the_floor_itself_is_accepted(monkeypatch):
    _install_settings(monkeypatch, VLLM_MIN_VERSION="0.24.0", **_VLLM)
    _probe_returning(monkeypatch, "0.24.0")
    preflight.assert_backends_ready()


def test_the_version_we_actually_run_is_accepted(monkeypatch):
    _install_settings(monkeypatch, VLLM_MIN_VERSION="0.24.0", **_VLLM)
    _probe_returning(monkeypatch, "0.28.0")
    preflight.assert_backends_ready()


# --- failure is loud --------------------------------------------------------------------------------


def test_an_unreachable_server_refuses_to_start(monkeypatch):
    """The fail-LOUD half, and the one a neighbouring idiom would have got wrong.

    Both call sites sit next to a deliberate `except Exception` - orphan recovery in main, queue-lane
    enumeration in the worker. Copying that shape here would boot the app against a pod nobody
    reached. Recovery is LLM_BACKEND=gemini plus a redeploy, which needs no code change.
    """
    _install_settings(monkeypatch, **_VLLM)

    def _unreachable(root):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(preflight, "_probe_vllm_version", _unreachable)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        preflight.assert_backends_ready()


def test_an_unparseable_base_url_refuses_to_start(monkeypatch):
    # A dev box can hold a nonsense URL: the config allowlist only inspects the origin in production,
    # so this is the layer that catches it everywhere else.
    _install_settings(
        monkeypatch,
        LLM_BACKEND="vllm",
        VLLM_BASE_URL="not-a-url",
        VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
    )
    with pytest.raises(RuntimeError, match="does not parse as a URL"):
        preflight.assert_backends_ready()


# --- the two helpers --------------------------------------------------------------------------------


def test_the_probe_targets_the_server_root_not_the_v1_prefix():
    # /version is served at the root. Appending it to the base URL would request /v1/version, 404,
    # and this module would then refuse every boot while reporting an unreachable server.
    assert preflight._server_root("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000"
    assert preflight._server_root("https://pod.example:9000/v1/") == "https://pod.example:9000"


def test_version_parsing_handles_the_shapes_a_server_may_report():
    assert preflight._version_tuple("0.28.0") == (0, 28, 0)
    assert preflight._version_tuple("0.28.0rc1") == (0, 28, 0)
    assert preflight._version_tuple("0.28") == (0, 28)
    assert preflight._version_tuple("") == ()


def test_version_comparison_is_numeric_not_lexicographic():
    # The trap this avoids: "0.9.0" > "0.28.0" as strings, so a lexicographic floor check would
    # accept a server nineteen minor versions below the CVE fix.
    assert preflight._version_tuple("0.9.0") < preflight._version_tuple("0.28.0")
