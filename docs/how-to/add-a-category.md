# How to add or edit a document category

Categories (and their summary prompts) live in the database and are edited at runtime from
the **admin console** - no code change or redeploy. The `Category`/`Prompt` tables are seeded
on first boot from `mrr_ai/taxonomy.py` and `mrr_ai/prompts.py`, which remain the seed source
and the fallback; everything reads through `mrr_ai/catalog.py` thereafter.

## Prerequisite: be an admin

Admin features are gated by an `is_admin` flag on your account (not full RBAC). Grant it once
with the CLI:

```bash
flask --app app admin grant you@example.com   # revoke / list also available
```

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
3. Optionally open **Prompt** for the new category and write its summary prompt. With no prompt
   row it inherits the general (`100`) prompt.

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

`taxonomy.py` (classifier catalog) and `prompts.py` (summary prompts) are only the **seed** for
a fresh database and the fallback when a row is missing. Editing them changes what a brand-new
DB seeds; it does **not** alter an already-seeded database (the admin console does that). The
category column is free-text `String(8)` (no enum/FK), so new ids need no migration. See
[../explanation/categorization.md](../explanation/categorization.md) and ADR
[0006](../decisions/0006-editable-catalog-admin.md).
