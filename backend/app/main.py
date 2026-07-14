"""FastAPI application entry point.

P1 scaffold: just the app + a /health probe. Routers (auth, documents, admin) and the
global deny-by-default auth middleware land in later phases (see the plan). Models import
here so `alembic`/tooling can discover the metadata via `app.main`.
"""

from fastapi import FastAPI

from app import models  # noqa: F401 - registers all tables on Base.metadata

app = FastAPI(title="MRR AI API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
