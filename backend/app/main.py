"""FastAPI application entry point.

The deny-by-default auth gate (enforce_auth) is attached as an app-level dependency, so every
route is protected unless its path is on the public allowlist. The FastAPI-Users auth/users
routers + the documents router land here; the admin router in P5. Models import here so
alembic/tooling can discover the metadata via `app.main`.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse

from app import models  # noqa: F401 - registers all tables on Base.metadata
from app.api.admin import router as admin_router
from app.api.documents import router as documents_router
from app.auth.deps import AuthRedirect, enforce_auth
from app.auth.routes import auth_router, users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
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
app.include_router(admin_router)
