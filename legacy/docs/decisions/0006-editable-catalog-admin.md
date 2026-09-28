# ADR-0006: DB-backed editable category/prompt catalog with an is_admin gate

**Status:** Accepted (2026-07-10)

## Context
Document categories (`taxonomy.py`) and per-category summary prompts (`prompts.py`) were frozen
Python constants: changing either required a code edit + redeploy. The business needs to manage
both at runtime. There was no authorization concept beyond the deny-by-default auth gate (a
`Role` model existed via Flask-Security but was unused).

## Decision
- **Editable catalog:** move categories and summary prompts into `Category`/`Prompt` DB tables,
  **seeded on first boot from the constants** (`seed_catalog.py`, idempotent). The constants stay
  as the seed source and the runtime fallback. A single accessor (`catalog.py`) reads DB-first;
  every consumer (editor category set, row validation, summarize prompt lookup, classifier
  catalog) goes through it. Services stay Flask/DB-free - blueprints resolve prompts and inject
  them.
- **Admin access:** an `is_admin` boolean on `User` (not RBAC), granted via a `flask admin` CLI.
  The app-level `before_request` gate 403s authenticated non-admins under `/admin` + `/api/admin`.
- **Identity + lifecycle:** category ids are immutable string numbers (they key stored rows);
  deletion is a soft `active` flag; an `auto_assign` flag preserves the "classifier set vs
  editor-selectable set" split. A `CatalogMeta.revision` bumps on every edit, invalidating the
  classifier's cached catalog text + embedding matrix and stamping `Job.catalog_revision` for
  provenance. Re-processing existing documents replaces their prior summaries (opt-in).

## Alternatives
- **Full RBAC / Flask-Security roles** - more moving parts and seeding for one bit of state;
  overkill for "mark a few accounts." Rejected in favor of `is_admin`.
- **Config email allowlist for admin** - zero-DB, but keeps authz outside the DB and needs a
  redeploy to change admins. Rejected.
- **File-based (JSON/YAML) catalog overrides** - not cleanly web-editable and file writes race
  with readers less safely than WAL SQLite. Rejected in favor of DB tables.
- **Per-request DB read inside services** - violates the service-purity invariant. Rejected;
  blueprints inject resolved prompts instead.

## Consequences
- Categories/prompts are managed from `/admin` with no redeploy; behavior is identical until an
  admin edits something (constants fallback).
- Additive schema only (`User.is_admin`, `Job.catalog_revision`, three tables) via the existing
  boot ADD-COLUMN + seed-if-empty path - no Alembic needed yet.
- The classifier now reads the catalog lazily via `catalog.py` (the one sanctioned exception to
  the services no-Flask rule; falls back to constants without an app context so it stays
  unit-testable).
- Editing a prompt affects only new/re-processed jobs; re-processing replaces prior summaries, so
  the previous output is not retained as a separate training pair (accepted trade-off).
