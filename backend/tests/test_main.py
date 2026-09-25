"""The application entry point: proving it can actually start.

NOTHING IN THIS SUITE STARTED THE APP BEFORE THIS FILE EXISTED. `conftest.py` imports `app` and the
shared client fixture drives it through `ASGITransport`, which does not run the lifespan - so
`main.py`'s startup sequence was executed by no test at all, including the check that refuses to
serve against a model backend it has not verified.

`asgi-lifespan` is in the dev group for exactly this. It runs the REAL ASGI startup rather than
calling `_lifespan` directly, so the wiring between the lifespan and the app is exercised too, not
only the function body.

The startup holds TWO OPPOSED RULES, and the source comment at `main.py:26-29` argues the
distinction explicitly. A failed model-backend preflight MUST stop the boot - it sits outside the
try beneath it precisely so that a PHI destination failing its check cannot be logged and carried
past. A Redis outage during orphan recovery MUST NOT - that block is wrapped so the web tier still
serves. Each is tested here, because a test of either alone passes against code that does the same
thing to both.
"""

import logging

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

import app.main as main
from app.auth.deps import AuthRedirect


async def test_the_app_runs_its_lifespan_and_then_serves(monkeypatch):
    """WHEN the application starts, THE SYSTEM SHALL run its lifespan and then answer requests.

    BOTH EXTERNALS ARE PATCHED ON THEIR SOURCE MODULES, NOT ON `app.main`, and that is the whole
    trap of this file: `_lifespan` imports them inside its own body (`main.py:30` and `:36-37`), so
    `monkeypatch.setattr(main, "assert_backends_ready", ...)` sets an attribute nothing ever reads.
    The real call still runs, the test still passes, and it asserts nothing. Measured before this
    file was written, with a throwaway control that patched both and watched which one was reached.

    Asserting the preflight was REACHED is what separates "the lifespan ran" from "the app happened
    to serve" - without it this test passes just as well against an app with no lifespan at all.
    """
    reached = []
    monkeypatch.setattr(
        "app.services.llm.preflight.assert_backends_ready", lambda: reached.append("preflight")
    )
    monkeypatch.setattr("app.worker.recovery.recover_orphans", lambda _session: 0)

    async with LifespanManager(main.app):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).json() == {"status": "ok"}

    assert reached == ["preflight"], "the lifespan did not run"


async def test_a_backend_that_fails_its_preflight_stops_the_boot(monkeypatch):
    """IF assert_backends_ready raises, THEN startup SHALL fail and the app SHALL NOT serve.

    The reason this check sits OUTSIDE the try below it, stated in the source: a PHI destination
    that fails its own fitness check must stop the boot rather than be logged and carried past. An
    app that serves in this state sends medical-record content to a model server nothing has
    verified, and the only signal would be a warning in a log nobody reads at boot.
    """
    monkeypatch.setattr("app.worker.recovery.recover_orphans", lambda _session: 0)

    def refuse():
        raise RuntimeError("vLLM is not fit to receive traffic")

    monkeypatch.setattr("app.services.llm.preflight.assert_backends_ready", refuse)

    with pytest.raises(RuntimeError, match="not fit to receive traffic"):
        async with LifespanManager(main.app):
            pass


async def test_a_recovery_failure_does_not_stop_the_boot(monkeypatch):
    """IF orphan recovery raises, THEN startup SHALL complete and the app SHALL still serve.

    The opposite ruling to the test above, and the pair is the point. Orphan recovery talks to Redis
    and the database; neither is required for the web tier to serve a reviewer their record list, so
    an outage there must degrade to a warning. Wrapping the preflight the same way - or leaving this
    one unwrapped - would each be a one-line change that no other test would notice.
    """
    monkeypatch.setattr("app.services.llm.preflight.assert_backends_ready", lambda: None)

    def redis_is_down(_session):
        raise ConnectionError("redis is unreachable")

    monkeypatch.setattr("app.worker.recovery.recover_orphans", redis_is_down)

    async with LifespanManager(main.app):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200


async def test_stale_jobs_interrupted_at_startup_are_reported(monkeypatch, caplog):
    """WHERE startup recovery interrupts stale jobs, THE SYSTEM SHALL report how many.

    A worker that died leaves jobs that look live forever. The count is the only operator-visible
    trace that the boot cleaned any up, and a silent recovery is indistinguishable from one that
    found nothing - which is the state the box is in on almost every restart.
    """
    monkeypatch.setattr("app.services.llm.preflight.assert_backends_ready", lambda: None)
    monkeypatch.setattr("app.worker.recovery.recover_orphans", lambda _session: 3)

    with caplog.at_level(logging.INFO, logger="app.main"):
        async with LifespanManager(main.app):
            pass

    reported = [record.getMessage() for record in caplog.records]
    assert any("3 stale job(s)" in message for message in reported), (
        f"the interrupted count was not reported: {reported}"
    )


async def test_expired_downloads_are_swept_at_startup_and_reported(monkeypatch, caplog):
    """WHEN the API starts, THE SYSTEM SHALL sweep expired prepared downloads and report how many (#389).

    A prepared export is patient data at rest, deleted by a timer when its token expires. A restart
    cancels those timers, so without this sweep the files a restart interrupted would stay on disk
    until somebody happened to export again."""
    monkeypatch.setattr("app.services.llm.preflight.assert_backends_ready", lambda: None)
    monkeypatch.setattr("app.worker.recovery.recover_orphans", lambda _session: 0)
    swept = []
    monkeypatch.setattr("app.services.downloads.sweep", lambda: swept.append(1) or 2)

    with caplog.at_level(logging.INFO, logger="app.main"):
        async with LifespanManager(main.app):
            pass

    assert swept, "the startup sweep did not run"
    reported = [record.getMessage() for record in caplog.records]
    assert any("2 stale file(s)" in message for message in reported), (
        f"the swept count was not reported: {reported}"
    )


async def test_a_failing_download_sweep_does_not_stop_the_app_serving(monkeypatch):
    """IF the startup download sweep fails, THEN THE SYSTEM SHALL still start and serve."""
    monkeypatch.setattr("app.services.llm.preflight.assert_backends_ready", lambda: None)
    monkeypatch.setattr("app.worker.recovery.recover_orphans", lambda _session: 0)

    def sweep_fails():
        raise OSError("the upload volume is unreadable")

    monkeypatch.setattr("app.services.downloads.sweep", sweep_fails)

    async with LifespanManager(main.app):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200


async def test_an_unauthenticated_browser_navigation_becomes_a_redirect_to_login():
    """WHEN AuthRedirect propagates, THE SYSTEM SHALL answer 302 to /login.

    Browsers get a redirect and JSON clients get a 401 - the split is the whole reason this
    exception type exists rather than raising HTTPException at the gate.

    Asserted in two parts because they fail independently: the handler must produce the redirect,
    AND it must be REGISTERED for that exception type. A handler that is correct and unregistered
    leaves a browser looking at a 500.
    """
    assert AuthRedirect in main.app.exception_handlers, "the handler is not wired to the exception"

    response = await main._auth_redirect(request=None, exc=AuthRedirect())

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
