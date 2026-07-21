# MRR AI backend (FastAPI)

Part of the Next.js + FastAPI re-platform (`docs/plans/2026-07-14-nextjs-fastapi-rewrite.md`).
The legacy Flask app in `../mrr_ai/` stays live until cutover; this is built alongside it.

## Dev setup

```bash
# 1. Local Postgres + Redis (from the repo root):
docker compose -f ../docker-compose.dev.yml up -d

# 2. Backend deps + env:
cd backend
uv sync                      # core (web/db/auth/queue); add --extra pipeline for the AI worker
cp .env.example .env         # fill SECRET_KEY + SECURITY_PASSWORD_SALT (carry the salt from the Flask .env)

# 3. Run the API:
uv run uvicorn app.main:app --reload --port 8000
# health check: http://localhost:8000/health
```

## Layout
- `app/config.py` - settings (pydantic-settings), Vertex-only, fail-fast in prod.
- `app/db.py` - lazy engine/session + declarative `Base`.
- `app/models.py` - SQLAlchemy models (fsqla auth schema mirrored + 9 domain tables).
- `app/main.py` - FastAPI app (routers added in later phases).

## Status
Phase 1a (foundation) - scaffold + models. Next: Alembic baseline + SQLite->Postgres migration
(P1b), then auth (P2), documents API (P3), the RQ job pipeline (P4), admin (P5), parity (P6).
