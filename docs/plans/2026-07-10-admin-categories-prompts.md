---
feature: admin-categories-prompts
date: 2026-07-10
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Give a small set of admin-marked accounts a web UI to add / edit / soft-delete document
CATEGORIES and to edit the per-category SUMMARY prompts, moving both out of hardcoded Python
constants into editable DB tables without changing pipeline behavior for existing users.

## Context

Categories (`mrr_ai/taxonomy.py`) and per-category summary prompts (`mrr_ai/prompts.py`) are
frozen Python module constants today. The category id (a string number) is a cross-module join
key referenced in taxonomy, prompts, `classification.py` (regex rules + LLM enum + cached
embedding matrix), `pages.py` (bundle sets), and `review.js` (labels). Auth is a single
deny-by-default `before_request` gate; Flask-Security `Role`/`roles_users` tables exist but are
unused. Persistence is SQLite WAL, single-process waitress, no Alembic (boot-time `create_all`
+ an additive `ADD COLUMN` stopgap). This plan is PHASE 1 of a two-phase effort.

Design decisions confirmed with Adrian (2026-07-10, via modal):
- Admin access = an `is_admin` boolean on `User` (mark a few accounts; not RBAC).
- Scope phase 1 = categories + the 14 summary prompts ONLY. Global prompts
  (segmentation / categorization / verify / title) are DEFERRED to phase 2 with guardrails.
- Storage = new DB tables, seeded from the current constants on first boot; string numeric ids
  are IMMUTABLE; clean up the existing drift.
- Re-processing existing documents = explicit opt-in admin action that REPLACES prior outputs
  (accepted trade-off: the previous summary is not retained as a training pair).
- Category deletion = soft-delete (an `active` flag), preserving id + historical rows.
- First admins bootstrapped via a small `flask` CLI command.

Known pre-existing drift this feature cleans up (from the investigation):
- `prompts.py` has no `category_11` key (id 11 falls back to the general `category_100`).
- `prompts.py` has a dead `category_06`; `taxonomy.py` omits id `6` but `review_api.py`
  re-adds it to `EDITABLE_CATEGORIES` and `review.js` has a label for it.
- `review.js` is MISSING a label for id `14` (renders as a bare number).

Out of scope / left as-is (flag, do not fix here):
- The legacy `blueprints/summarize.py` `/classic` path hard-subscripts `prompts['category_11']`
  (latent `KeyError`). It is still registered but is not the live path. Note it; do not touch.
- Global (non-summary) prompts and the classification regex `_RULES` (phase 2).

## Approach

Chosen: DB-backed `Category` and `Prompt` tables + a thin DB-first accessor with the Python
constants as the seed source AND the runtime fallback. A new admin blueprint (`/admin` pages +
`/api/admin` JSON) gated by `is_admin` inside the existing central `before_request`. Services
stay DB-free: blueprints resolve category/prompt data and inject it into the pure services.

Rejected alternatives:
- File-based (JSON/YAML) override for categories/prompts: not cleanly web-editable, file writes
  race with readers less safely than WAL SQLite, and it splits state from the DB. (Rejected.)
- Flask-Security `Role`-based admin gate: more idiomatic but adds a two-table join and seeding
  for one bit of state; overkill for "mark a few accounts". (Rejected in favor of `is_admin`.)
- Config email allowlist for admin: zero-DB but keeps authz outside the DB and needs a redeploy
  to change admins. (Rejected.)
- Per-request DB read inside services: violates the deliberate service purity
  (`summarize_engine.py` docstring: "no file/global reads here"). (Rejected; inject instead.)

Key architectural seam: a single accessor module (working name `catalog.py`) exposes
`get_categories(active_only=False)` and `get_prompt(role, category_id)` reading DB-first with a
constants fallback, plus a version counter for cache invalidation. Because the app is
single-process (waitress), an in-memory cache guarded by a version bump + a `threading.Lock`
is sufficient; no cross-process busting is needed.

## Tasks

- T1: Admin authorization foundation (`is_admin` + gate + CLI)
  - Add `is_admin = db.Column(db.Boolean, nullable=False, default=False)` to `User`
    (`models.py`) and a matching entry in `_ADDITIVE_COLUMNS['user']`
    (`__init__.py`, e.g. `('is_admin', 'BOOLEAN NOT NULL DEFAULT 0')`) so the seeded
    `instance/mrr.db` upgrades in place.
  - Extend the `before_request` gate (`security.py`) so any endpoint whose path starts with
    `/admin` or `/api/admin` additionally requires `current_user.is_admin`, returning 403
    (JSON) / redirect-or-403 (browser) for authenticated non-admins. Keep the existing
    deny-by-default for everyone.
  - Add a `flask admin grant <email>` / `revoke <email>` / `list` CLI (Flask CLI group,
    registered in the app factory) using the existing user datastore.
  - approach: tdd
  - files-touched: [mrr_ai/models.py, mrr_ai/__init__.py, mrr_ai/security.py, mrr_ai/cli.py (new), tests/unit/test_admin_auth.py (new)]
  - acceptance: a non-admin authenticated request to an `/api/admin/*` route gets 403; an
    admin passes; a non-admin request to an existing non-admin route is unaffected; the CLI
    flips `is_admin` on a user by email and rejects unknown emails.

- T2: `Category` + `Prompt` tables + idempotent boot seed
  - New `Category` model: `id` (String PK, the immutable numeric string), `name`,
    `description` (Text), `examples` (JSON/Text list), `active` (Boolean default True),
    `auto_assign` (Boolean default True), `updated_at`. `auto_assign` preserves today's two-set
    distinction: the classifier's assignable set = the 14 taxonomy ids (`auto_assign=True`),
    while the editor's selectable set = all `active` categories including id `6`
    (`auto_assign=False` -- manually selectable but never auto-assigned). New `Prompt` model: `id` (PK), `role` (String, phase 1 only `'summary'`;
    column present so phase 2 needs no migration), `category_id` (nullable FK/loose ref),
    `text` (Text), `revision` (Integer), `updated_at`. A global `catalog_revision` counter
    (small table or a row in an existing settings-like store) to stamp on jobs / bust caches.
  - Seed-if-empty in `_create_schema` (`__init__.py`): the 14 `taxonomy.CATEGORIES` ids with
    `auto_assign=True`, plus id `6` with `auto_assign=False` (name/desc from the current
    `review.js` label + `prompts['category_06']`); all `active=True`. Summary `Prompt` rows for
    every id that currently HAS a `category_NN` prompt (01-10, 12-14, 06, 100). Id `11` gets a
    `Category` row (`auto_assign=True`, since the classifier already emits 11) but NO summary
    prompt row (keeps the general fallback; admin authors it later in the UI). Seeding runs only
    when the tables are empty (mirrors the existing additive-migration ethos).
  - approach: tdd
  - files-touched: [mrr_ai/models.py, mrr_ai/__init__.py, mrr_ai/seed_catalog.py (new), tests/unit/test_catalog_seed.py (new)]
  - acceptance: a fresh DB seeds 15 category rows (the taxonomy's 14 ids -- which already
    include `100` -- plus id `6`), all `active=True`, and summary prompt rows matching the
    current `prompts` keys except `category_11`; re-running the seed is a no-op (idempotent);
    ids match the existing string convention exactly.
  - NOTE (confirm at approval): treatment of id `6` (formalize as a real category vs seed
    inactive) and whether id `11` should ship with an authored prompt or inherit-general.

- T3: DB-first accessor + wire consumers (behavior-preserving)
  - New `catalog.py`: `get_categories(active_only=False)`, `get_prompt(role, category_id)`
    (DB-first, constants fallback, general-`category_100` fallback for missing summary prompts),
    `catalog_version()`. In-memory cache keyed by `catalog_version()`.
  - Re-point read sites to the accessor WITHOUT putting DB reads in pure services:
    `documents_api.py` `payload['categories']` and `review_api.py` `EDITABLE_CATEGORIES`/
    `validate_rows` read `catalog.get_categories(active_only=True)` (editor set = all active,
    incl. id 6); the classifier (T4) reads `catalog.get_categories(auto_assign=True)`; the
    summary call sites
    (`summarize_engine.summarize_row` callers: `documents_api.py`, `review_api.py`,
    `bundles.py`) resolve the prompt via `catalog.get_prompt('summary', category_id)` in the
    blueprint and pass it into `summarize_row` (add/param the injected prompt; keep the current
    lookup as the default so nothing else breaks).
  - approach: tdd (accessor logic) + test-after (wiring)
  - files-touched: [mrr_ai/catalog.py (new), mrr_ai/services/summarize_engine.py, mrr_ai/blueprints/documents_api.py, mrr_ai/blueprints/review_api.py, mrr_ai/services/bundles.py, tests/unit/test_catalog_accessor.py (new)]
  - acceptance: with the DB seeded, `get_categories`/`get_prompt` return values identical to the
    constants; editing a DB prompt row changes what `get_prompt` returns; a category with no
    summary prompt row returns the general prompt; the full existing suite still passes.

- T4: Classifier cache-invalidation seam
  - When a category's classifier-facing fields (`description`, `examples`, `active`) change,
    bump `catalog_version` and rebuild `classification._CATALOG_TEXT` + the embedding matrix
    (`_category_ids` / `_category_matrix`) lazily on next use, under the existing embed lock, so
    request threads never read a half-rebuilt matrix. `classification` reads its catalog from
    `catalog.get_categories(auto_assign=True)` (active + auto-assignable only) instead of
    importing `CATEGORIES` directly, so id 6 stays out of the classifier while remaining
    editor-selectable.
  - approach: test-after
  - files-touched: [mrr_ai/services/classification.py, mrr_ai/catalog.py, tests/unit/test_classifier_reload.py (new)]
  - acceptance: editing a category description bumps the version; the next `classify()` uses the
    updated catalog text; the embedding matrix re-encodes exactly once after an edit (not per
    request); deactivating a category removes it from the classifier's allowed set.

- T5: Admin blueprint + JSON API (CRUD)
  - New `blueprints/admin_api.py` (url_prefix `/api/admin`): categories list/create/patch/
    soft-delete (deactivate/reactivate) and summary-prompt get/update. All `is_admin`-gated
    (via the T1 prefix gate) with CSRF. Enforce: id immutable on edit; new category id must be
    a non-colliding numeric string; prompt `text` non-empty. Write an `AuditLog` row on every
    change (reuse existing `audit()`).
  - approach: tdd (validation + immutability + audit) + test-after (endpoints)
  - files-touched: [mrr_ai/blueprints/admin_api.py (new), mrr_ai/blueprints/__init__.py, tests/unit/test_admin_api.py (new)]
  - acceptance: admin can create/edit/deactivate a category and edit a summary prompt via the
    API; attempting to change an id or create a colliding id is rejected; non-admin gets 403;
    each successful edit produces an AuditLog row.

- T6: Admin UI (pages + JS)
  - New `blueprints/pages.py` route `/admin` rendering an admin page in the Evaluators design
    system: a categories table (reuse the shared table look) with add / edit (name,
    description, examples, auto-assign, active) and a per-category summary-prompt editor
    (textarea + save). New `templates/admin.html` +
    `static/admin.js` (vanilla, fetch + XSRF). Add an admin-only nav link (rendered only when
    `current_user.is_admin`).
  - approach: test-after (Playwright live verification)
  - files-touched: [mrr_ai/blueprints/pages.py, mrr_ai/templates/admin.html (new), mrr_ai/static/admin.js (new), mrr_ai/static/evaluators.css, relevant templates for the nav link]
  - acceptance: an admin loads `/admin`, adds a category, edits a summary prompt, deactivates a
    category; changes persist and are reflected in the review editor's category dropdown and in
    the served prompt; a non-admin never sees the nav link and gets 403 at `/admin`.

- T7: Opt-in re-processing (replace)
  - Admin action to re-summarize a selected document (and a bounded batch) with the current
    prompts, REPLACING existing summaries. Reuse the existing re-run / bundle-summarize
    machinery (`documents_api` re-summarize path) rather than new pipeline code. Stamp the
    current `catalog_revision` on the job. Respect the single-active-job-per-document invariant
    and the summarize cap.
  - approach: test-after
  - files-touched: [mrr_ai/blueprints/admin_api.py, mrr_ai/blueprints/documents_api.py (reuse), tests/unit/test_admin_reprocess.py (new)]
  - acceptance: after editing a summary prompt, an admin triggers re-process on a document; its
    summaries are regenerated and replaced; the job records the current catalog revision; a
    second concurrent re-process on the same doc is refused (409).

- T8: Drift cleanup + tests + docs
  - Make `review.js` labels data-driven from the served categories (fixes the missing `14`
    label and the phantom `6`). Update `tests/unit/test_taxonomy.py` (and any id-range
    assertions) to reflect the DB-backed, immutable-id model. Update `mrr_ai/CLAUDE.md` /
    `services/CLAUDE.md` notes where categories/prompts are now sourced.
  - approach: test-after (JS via Playwright) + code (docs)
  - files-touched: [mrr_ai/static/review.js, tests/unit/test_taxonomy.py, mrr_ai/CLAUDE.md, mrr_ai/services/CLAUDE.md]
  - acceptance: category `14` renders with its label; `6` renders consistently; the taxonomy
    tests pass against the new model; docs describe the DB-first source.

## Risk / Rollback

- Blast radius: MEDIUM. Additive schema (one column + two tables) and a new admin blueprint are
  low-risk, but T3/T4 touch LIVE read paths (summary prompt lookup, classifier catalog). The
  accessor's constants-fallback and behavior-preserving wiring are the primary safeguard: if the
  DB is empty or a row is missing, behavior is identical to today.
- Specific risks: (a) classifier cache not invalidated -> stale categorization (mitigated by T4
  + tests); (b) a bad summary-prompt edit affects live PHI summaries (mitigated: summary prompts
  have no format placeholders so cannot crash; edits are audited; re-processing is opt-in); (c)
  seeding running against a non-empty DB and duplicating rows (mitigated: seed-if-empty guard).
- Rollback: revert the PR. Schema is additive, so the extra column/tables are inert if unused;
  no destructive migration. The seeded `instance/mrr.db` is preserved (additive-only, per the
  existing boot-migration contract).

## Verification

End-to-end after all tasks (synthetic account only; do NOT drive Adrian's real PHI account):
1. Full suite green (`pytest -q`) + ruff clean.
2. `flask admin grant verify-t1@example.com`; confirm the account can reach `/admin` and a
   second non-admin account gets 403 at `/admin` and `/api/admin/categories`.
3. Playwright (synthetic account): load `/admin`; add a category (new id), edit an existing
   summary prompt, deactivate a category; confirm the review editor dropdown reflects the
   changes and the deactivated category is gone from new categorization.
4. Confirm editing a category description bumps the catalog version and the classifier picks it
   up (unit + a targeted live check).
5. Trigger opt-in re-process on a seeded document after a prompt edit; confirm summaries are
   replaced and the job stamps the catalog revision.
6. Confirm behavior parity for a NON-admin, normal user: upload -> segment -> review ->
   summarize is unchanged from before the feature.
