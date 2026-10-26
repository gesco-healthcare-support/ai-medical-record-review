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

import logging

import httpx
import pytest

from app.config import Settings
from app.errors import ModelServerOfflineError
from app.services.llm import preflight, vllm

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
    """Build a real Settings from a known environment and hand it to preflight only.

    The per-process record of servers that passed is replaced too, so one test's pass can never
    stand in for another test's probe.
    """
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {**_BASE, **env}.items():
        monkeypatch.setenv(name, value)
    settings = Settings()  # type: ignore[call-arg]
    monkeypatch.setattr(preflight, "get_settings", lambda: settings)
    monkeypatch.setattr(preflight, "_verified", set())
    return settings


def _probe_silent(monkeypatch):
    """A server nobody answers for: what httpx raises when the pod is off or the tunnel is down."""
    calls = []

    def _silent(root):
        calls.append(root)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(preflight, "_probe_vllm_version", _silent)
    return calls


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


def test_a_server_that_answers_wrongly_refuses_everywhere(monkeypatch):
    """The fail-LOUD half, and the one a neighbouring idiom would have got wrong.

    Something answered, and not the way a vetted server does - a deployment fault. Both startup
    paths raise: the web app's (which serves through a SILENT server) and the worker's (which waits
    for one). Both call sites sit next to a deliberate `except Exception`; copying that shape would
    boot the app against a server that failed its check.
    """
    _install_settings(monkeypatch, **_VLLM)

    def _garbled(root):
        raise ValueError(f"GET {root}/version returned list, expected an object")

    monkeypatch.setattr(preflight, "_probe_vllm_version", _garbled)
    with pytest.raises(RuntimeError, match="Refusing to send records"):
        preflight.assert_backends_ready()
    with pytest.raises(RuntimeError, match="Refusing to send records"):
        preflight.check_backends_before_serving()
    slept = []
    with pytest.raises(RuntimeError, match="Refusing to send records"):
        preflight.wait_for_backends(sleep=slept.append)
    assert slept == [], "a worker must not wait out a server that answered wrongly"


def test_a_version_below_the_floor_stops_the_web_app_too(monkeypatch):
    # Serving through a silent server must not become serving through a wrong one.
    _install_settings(monkeypatch, VLLM_MIN_VERSION="0.24.0", **_VLLM)
    _probe_returning(monkeypatch, "0.23.9")
    with pytest.raises(RuntimeError, match="below the required floor"):
        preflight.check_backends_before_serving()


# --- a silent server: the pod is switched off -----------------------------------------------------


def test_a_silent_server_is_reported_offline_never_as_a_pass(monkeypatch):
    """Nobody answered, so nothing was verified: the strict check must raise, and say which case."""
    _install_settings(monkeypatch, **_VLLM)
    _probe_silent(monkeypatch)
    with pytest.raises(ModelServerOfflineError, match="did not answer"):
        preflight.assert_backends_ready()


def test_the_web_app_serves_through_a_silent_server_and_says_so(monkeypatch, caplog):
    """WHEN the model server does not answer at startup, THE web app SHALL start and log it.

    The records are in this app's database, so with the pod off a reviewer can still open, edit and
    export them. Model calls stay refused (see the client test below), so nothing is sent anywhere.
    """
    _install_settings(monkeypatch, **_VLLM)
    _probe_silent(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=preflight.__name__):
        preflight.check_backends_before_serving()
    assert any("serving without it" in r.getMessage() for r in caplog.records)


def test_a_worker_waits_for_a_silent_server_and_then_takes_jobs(monkeypatch):
    """WHEN the model server does not answer at a worker's startup, THE worker SHALL wait for it.

    It asks again every poll and returns only once the server has answered and passed - so no job is
    failed row by row against a silent server, and jobs queued overnight start by themselves.
    """
    _install_settings(monkeypatch, **_VLLM)
    answers = iter(["silent", "silent", "0.28.0"])

    def _probe(root):
        answer = next(answers)
        if answer == "silent":
            raise httpx.ConnectTimeout("timed out")
        return answer

    monkeypatch.setattr(preflight, "_probe_vllm_version", _probe)
    slept = []
    preflight.wait_for_backends(poll_s=7.0, sleep=slept.append)
    assert slept == [7.0, 7.0]
    assert next(answers, "drained") == "drained", "it must stop asking once the server passed"


def test_a_waiting_worker_does_not_log_every_check(monkeypatch, caplog):
    # A night of 30-second checks is ~1,400 lines; one warning per ten minutes is enough to read.
    _install_settings(monkeypatch, **_VLLM)
    remaining = [preflight._WAIT_LOG_EVERY + 1]

    def _probe(root):
        if remaining[0]:
            remaining[0] -= 1
            raise httpx.ConnectError("connection refused")
        return "0.28.0"

    monkeypatch.setattr(preflight, "_probe_vllm_version", _probe)
    with caplog.at_level(logging.WARNING, logger=preflight.__name__):
        preflight.wait_for_backends(sleep=lambda _s: None)
    waiting = [r for r in caplog.records if "takes no job" in r.getMessage()]
    assert len(waiting) == 2


def test_the_gemini_path_never_waits_or_probes(monkeypatch):
    _install_settings(monkeypatch, LLM_BACKEND="gemini")

    def _explode(root):
        raise AssertionError(f"the gemini path must not probe anything, but it probed {root}")

    monkeypatch.setattr(preflight, "_probe_vllm_version", _explode)
    preflight.check_backends_before_serving()
    preflight.wait_for_backends(sleep=lambda _s: pytest.fail("the gemini path must not wait"))


# --- no client before a pass ----------------------------------------------------------------------


def test_no_vllm_client_is_handed_out_while_the_server_is_silent(monkeypatch):
    """The guarantee the web app's serving rests on: a model call cannot reach an unverified server.

    `_cached_client` is made to explode, so the test fails if a client is BUILT, not merely if one
    is used - a check placed after construction would still pass a weaker assertion.
    """
    settings = _install_settings(monkeypatch, **_VLLM)
    monkeypatch.setattr(vllm, "get_settings", lambda: settings)
    _probe_silent(monkeypatch)

    def _built(*_args):
        raise AssertionError("a vLLM client was built for a server that was never verified")

    monkeypatch.setattr(vllm, "_cached_client", _built)
    with pytest.raises(ModelServerOfflineError):
        vllm._client()


def test_a_pass_is_remembered_for_the_process_and_a_failure_is_not(monkeypatch):
    """One request per process once the server has passed; a server that comes up later is seen."""
    settings = _install_settings(monkeypatch, **_VLLM)
    monkeypatch.setattr(vllm, "get_settings", lambda: settings)
    monkeypatch.setattr(vllm, "_cached_client", lambda *_args: "client")
    probes = []
    answers = iter(["silent", "0.28.0"])

    def _probe(root):
        probes.append(root)
        answer = next(answers)
        if answer == "silent":
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return answer

    monkeypatch.setattr(preflight, "_probe_vllm_version", _probe)
    with pytest.raises(ModelServerOfflineError):
        vllm._client()
    assert vllm._client() == "client"
    assert vllm._client() == "client"
    assert len(probes) == 2, "the failure must be asked again, the pass must not"


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
