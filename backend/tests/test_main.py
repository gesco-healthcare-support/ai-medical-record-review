"""The application entry point: proving it can actually start.

NOTHING IN THIS SUITE STARTED THE APP BEFORE THIS FILE EXISTED. `conftest.py` imports `app` and the
shared client fixture drives it through `ASGITransport`, which does not run the lifespan - so
`main.py`'s startup sequence was executed by no test at all, including the check that refuses to
serve against a model backend it has not verified.

`asgi-lifespan` is in the dev group for exactly this. It runs the REAL ASGI startup rather than
calling `_lifespan` directly, so the wiring between the lifespan and the app is exercised too, not
only the function body. This file starts with one smoke test; the startup's two opposed rules - a
failed preflight must stop the boot, a Redis outage must not - are covered separately.
"""

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

import app.main as main


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
