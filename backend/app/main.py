"""FastAPI application entry point.

The deny-by-default auth gate (enforce_auth) is attached as an app-level dependency, so every
route is protected unless its path is on the public allowlist. The FastAPI-Users auth/users
routers land here (P2c); documents + admin routers in later phases. Models import here so
alembic/tooling can discover the metadata via `app.main`.
"""

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse

from app import models  # noqa: F401 - registers all tables on Base.metadata
from app.auth.deps import AuthRedirect, enforce_auth
from app.auth.routes import auth_router, users_router

app = FastAPI(title="MRR AI API", version="0.1.0", dependencies=[Depends(enforce_auth)])


@app.exception_handler(AuthRedirect)
async def _auth_redirect(request: Request, exc: AuthRedirect) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(users_router)
