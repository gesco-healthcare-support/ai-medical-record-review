"""FastAPI application entry point.

The deny-by-default auth gate (enforce_auth) is attached as an app-level dependency, so every
route is protected unless its path is on the public allowlist. The FastAPI-Users auth/users
routers + the documents router land here; the admin router in P5. Models import here so
alembic/tooling can discover the metadata via `app.main`.
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse

from app import models  # noqa: F401 - registers all tables on Base.metadata
from app.api.admin import router as admin_router
from app.api.documents import router as documents_router
from app.api.downloads import router as downloads_router
from app.auth.deps import AuthRedirect, enforce_auth
from app.auth.routes import auth_router, users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Send app logs to stdout at INFO, as the worker does (`app/worker/__main__.py`). uvicorn configures only
    # its own loggers, so without this every `app.*` INFO line here was dropped - measured 2026-09-25 (#390).
    # A no-op when the root logger already has handlers, so a test runner's own capture is left alone.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Refuse to serve against a model server we have not verified. DELIBERATELY NOT inside the try
    # below, and the distinction is the whole point: that block swallows so a Redis outage cannot
    # stop the web app serving, which is right for orphan recovery and wrong here. A PHI destination
    # that fails its check must stop the boot, not log a warning and carry on.
    from app.services.llm.preflight import assert_backends_ready

    assert_backends_ready()
    # Heartbeat-aware orphan recovery: interrupt jobs whose worker died, but never a live job.
    # Guarded so a Redis outage at boot cannot block the web app from starting.
    try:
        from app.db import get_sessionmaker
        from app.worker.recovery import recover_orphans

        with get_sessionmaker()() as session:
            reaped = recover_orphans(session)
        if reaped:
            logger.info("startup orphan recovery interrupted %d stale job(s)", reaped)
    except Exception:
        logger.warning("startup orphan recovery failed", exc_info=True)
    # Prepared exports are patient data at rest and are deleted by a timer when their token expires; a
    # restart cancels those timers, so sweep up whatever they left. Guarded like recovery above: a
    # failed sweep must not stop the app serving, and the next export sweeps again anyway.
    try:
        from app.services.downloads import sweep

        swept = sweep()
        if swept:
            logger.info("startup download sweep removed %d stale file(s)", swept)
    except Exception:
        logger.warning("startup download sweep failed", exc_info=True)
    yield


app = FastAPI(
    title="MRR AI API",
    version="0.1.0",
    dependencies=[Depends(enforce_auth)],
    lifespan=_lifespan,
)


@app.exception_handler(AuthRedirect)
async def _auth_redirect(request: Request, exc: AuthRedirect) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(documents_router)
app.include_router(downloads_router)
app.include_router(admin_router)
