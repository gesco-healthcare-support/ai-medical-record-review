# How to add or edit a document category

> **[PARTIALLY STALE]** **The workflow here is current; two things in it were not.** Checked against
> `backend/app/services/catalog.py` on 2026-08-12 and corrected: the admin CLI command (it was the
> Flask one), the three `mrr_ai/` module paths, and the Developer note - a `prompts.py` edit **does**
> change an already-seeded database for any category with no `Prompt` row of its own.

Categories (and their summary prompts) live in the database and are edited at runtime from
the **admin console** - no code change or redeploy. The `Category`/`Prompt` tables are seeded
on first boot from `backend/app/services/taxonomy.py` and `backend/app/services/prompts.py`, which
remain the seed source and the fallback; everything reads through
`backend/app/services/catalog.py` thereafter.

## Prerequisite: be an admin

Admin features are gated by an `is_admin` flag on your account (not full RBAC). Grant it once
with the CLI:

```bash
docker compose exec api python -m app.cli admin grant you@example.com   # revoke / list also available
```

Or from `backend/` directly, with the database env set: `python -m app.cli admin grant …`.

Then the **Admin** link appears in the top nav (or go to `/admin`).

## Add a category (admin console)

1. On `/admin`, click **Add category**.
2. Enter:
   - **ID** - a number (e.g. `15`). It is **permanent** - it keys stored review rows, so it is
     never editable afterward.
   - **Name** and **Description** - the description + examples also feed the classifier, so
     write them the way real titles read.
   - **Example document titles** - one per line.
   - **Auto-assign** - on = the classifier may assign it; off = selectable in the review editor
     but never auto-assigned (how id 6 behaves).
   - **Active** - inactive categories are hidden from new categorization and the editor but
     keep their id and any historical rows (soft delete).
3. Optionally open **Prompt** for the new category and write its summary prompt. A brand-new id has
   no prompt in code either, so with no prompt row it inherits the general (`100`) prompt. (That is
   the *new-category* case only - see the resolution order in the Developer note below, where an
   existing category with a code prompt and no row gets its own code prompt, not the general one.)

Saving bumps the catalog revision, so the classifier reloads its category text + embedding
matrix on the next run automatically.

## Edit / deactivate

- **Edit** changes name/description/examples/auto-assign/active. Editing classifier-facing text
  changes future categorization.
- **Deactivate** (Active off) is the soft delete - ids are immutable and existing rows must stay
  interpretable, so there is no hard delete in the UI.
- **Apply an edit to existing documents:** re-run their summaries. The per-document Summaries
  re-run (and the admin `POST /api/admin/reprocess/<id>`) re-summarize with the current prompts,
  **replacing** the prior summaries; the job records the catalog revision it used.

## Developer note (changing the seed defaults)

`taxonomy.py` (classifier catalog) and `prompts.py` (summary prompts) do **not** behave the same way
here, and the difference matters.

**`taxonomy.py` is seed-and-fallback only.** Editing it changes what a brand-new DB seeds and what
the classifier falls back to when no DB is reachable; it does not alter an already-seeded database.
The admin console does that.

**`prompts.py` is consulted at runtime, and an edit to it can change an already-seeded database.**
`catalog.get_prompt(session, "summary", category_id)` resolves most-specific-first:

1. this category's own `Prompt` row - an admin edit, which always wins;
2. this category's own **code prompt** from `prompts.py`;
3. the general (`100`) `Prompt` row - only for a category the code has no prompt for (`11`);
4. the general code prompt - back-stop on an unseeded DB.

So for any category with **no `Prompt` row of its own**, step 2 decides, and editing `prompts.py`
plus a redeploy changes its delivered summaries. Step 2 sitting ahead of step 3 is deliberate:
otherwise a category with no row would be summarized with the catch-all prompt instead of its own
rules.

Whether step 1 or step 2 governs on a given box is therefore an **empirical question about that
database**, not a property of the code. `seed_catalog()` writes a `Prompt` row for every category
that has a code prompt, so a database seeded that way has step 1 winning everywhere and
`prompts.py` edits reach nothing. Check before relying on either:

```sql
SELECT role, category_id, length(text) FROM prompts;
```

No rows means code prompts govern. This is worth re-checking rather than remembering: nothing stops
someone editing a prompt through the admin console tomorrow, and the failure is silent - a prompt
change that measures as having no effect looks exactly like a change that did not help.

Note also that a category prompt is only part of the system message. `summarize_engine.build_preamble`
prepends shared rule blocks selected by category id, and an id the catalog does not ship yet receives
**every** block, so a new category is never silently under-instructed. Some behaviour you might go
looking for in `prompts.py` lives there instead.

The category column is free-text `String(8)` (no enum/FK), so new ids need no migration. See
[../explanation/categorization.md](../explanation/categorization.md) and ADR
[0006](../decisions/0006-editable-catalog-admin.md).
