# How to add or change a category

Use this when a document type has no category of its own, when the classifier keeps putting a type
in the wrong category and the category's description or examples need work, or when a category
should stop being assigned.

A category can be changed in two places, and they reach different databases:

| Route | Reaches | Use it for |
| --- | --- | --- |
| [Admin console](#add-a-category-in-the-admin-console) | The one database you edit, immediately, with no deploy | Trying a category on one box; adjusting a description, examples or flags at runtime |
| [Code plus a guarded migration](#add-a-category-in-code-for-every-box) | Every existing box after deploy, and every fresh database | A permanent category, a title rule, a built-in summary prompt |

Categories 15, 16 and 17 were added by the code route. How the catalog, the fallback and the
classifier fit together is explained in [Categorization](../explanation/categorization.md).

## Prerequisites

- For the admin console: an account with the admin (superuser) flag. See
  [How to manage users and admins](manage-users-and-admins.md).
- For the code route: a working local stack ([How to run the app locally](run-the-app-locally.md)),
  the test setup ([How to run the tests](run-the-tests.md)) and
  [How to create a database migration](create-a-database-migration.md).
- An id. Ids are positive whole numbers, stored as strings, and permanent: they key every stored row
  and cannot be edited later. The built-in ids are 1-17 and 100 (6 is selectable in the editor but
  never auto-assigned), and they count as taken even on a database whose `categories` table is
  still empty. Pick the next unused number.

## Add a category in the admin console

1. Sign in with an admin account, open the avatar menu and choose **Admin** (or go to `/admin`). The
   item is shown only to admins.
   Expected: the "Categories & prompts" page, listing every category including the built-ins.
2. Click **Add category**.
3. Fill in the dialog:
   - **ID (number)**: the unused id you chose.
   - **Name**: short and specific. The classifier's LLM vote reads it.
   - **Description**: what belongs here and, in a final sentence, what does not and where it goes
     instead. Both classifier votes read it, so write it the way the documents themselves read.
   - **Example document titles (one per line)**: real-looking titles of this type. Only the
     embedding vote reads these. Use invented examples, never titles copied from a record.
   - **Auto-assign (classifier may pick this)**: on to let the classifier assign it; off to make it
     selectable in the review editor only (how category 6 works).
   - **Summarize by default**: whether rows placed in this category start ticked for summarization.
   - **Active**: on.
4. Click **Save**.
   Expected: the dialog closes and the category appears in the table. On a database whose
   `categories` table was empty, the first save also writes every built-in category as a row, so
   the catalog never collapses to the one you created.
5. Optional: click **Prompt** on the new row and write its summary prompt. Until you do, a new id is
   summarized with the general (100) prompt. See
   [How to change a summary prompt or rule](change-a-summary-prompt-or-rule.md).

Nothing needs restarting. The save bumps the catalog revision, and each segment worker reloads the
category set on its next classification.

What to expect from the classifier: title rules are code, so no rule assigns a category created in
the console. It is reached only through the embedding and LLM votes, and only when both vote for it
is the row assigned without a review flag. Rows classified before the change keep their category.

## Edit a category in the console

1. On `/admin`, click **Edit** on the category's row.
2. Change the name, description, examples or flags. The id field is disabled.
3. Click **Save**.

| Change | Effect |
| --- | --- |
| Name or description | Both classifier votes see the new text from the next classification |
| Example titles | The embedding vote sees them from the next classification |
| Auto-assign off | The classifier stops choosing it; reviewers can still select it |
| Summarize by default | Applies to rows placed in the category from now on; existing rows keep their tick |
| Active off | See [Deactivate a category](#deactivate-a-category) |

Every save writes a `category.update` audit row naming the fields that moved.

## Deactivate a category

There is no hard delete; deactivating is the soft delete.

1. Click **Edit** on the category, untick **Active**, click **Save**.
2. If any sub-document still uses the category, the save is refused with 409 and the message
   `category N is used by K sub-documents and cannot be deactivated. Move those rows to another
   category first.` Re-categorize those rows in the review editor of each document, then save again.

The check exists because the server accepts only active categories when it saves a document's rows,
so deactivating a category in use would leave those documents unsaveable.

If you only want the classifier to stop assigning the category, untick **Auto-assign** instead: the
category stays valid for existing rows and selectable in the editor.

Deactivating does not stop a title rule from assigning the category, because rules are code and
ignore the catalog's flags. If a rule in `_RULES` targets the category, change or remove the rule by
the code route as well.

## Apply a change to existing documents

Classification happens when a document is segmented (or, for an aggregate upload, when its
`classify` job runs). A catalog change does not re-classify stored rows. To bring existing
documents in line:

- Re-categorize the affected rows in the review editor, or
- Re-run segmentation for the document. This re-classifies every row but replaces the document's
  rows and every reviewer correction on them (the loss is audited as `segment.rows_replaced`).

To bring existing summaries in line after a prompt or category change, regenerate them from the
record's workbench: **Re-summarize all from scratch** (the summarize run with `fresh`) deletes the record's
summaries and writes every one again with the current prompts, and the re-draft action on a single
summary regenerates just that one. Both replace the reviewer's edits on what they regenerate.

**Re-run summaries** under "Reprocess a record" on the admin page
(`POST /api/admin/reprocess/{document_id}`) starts a summarize run on any user's record, but it
does not pass `fresh`: a summary whose row (pages and category) is unchanged is kept as it is, and
only rows without a summary are written with the current prompts.

## Add a category in code, for every box

A database whose `categories` table already has rows ignores `taxonomy.py`, so a new constant alone
reaches only fresh databases. The migration carries the row to every existing box. Model the change
on the category-17 change.

1. **Add the constant.** In `backend/app/services/taxonomy.py`, add an entry to `CATEGORIES`:

   ```python
   "18": Category(
       "18",
       "Short category name",
       "What belongs here. A final sentence naming the confusable document type and the "
       "category it belongs to instead.",
       (
           "Example Title One",
           "Example Title Two",
       ),
   ),
   ```

   The final sentence of the description is what lets the two classifier votes tell this category
   from its neighbours; the descriptions of 15, 16 and 17 show the pattern.

2. **Add the summary prompt.** In `backend/app/services/prompts.py`, add a key `category_18` (the key
   is `category_` plus the id padded to two digits). Without one, the category is summarized with
   the general prompt. Also decide which shared rule blocks it receives in
   `backend/app/services/summarize_engine.py` (`_KNOWN_CATEGORIES` and the sets beside it); an id
   not listed there receives every block except the deposition format. Both are covered in
   [How to change a summary prompt or rule](change-a-summary-prompt-or-rule.md).

3. **Optionally add a title rule.** In `backend/app/services/classification.py`, add a
   `(pattern, "18")` pair to `_RULES` at the position its precedence needs (first match wins; read
   the comments on the neighbouring rules first). Keep gaps bounded (`.{0,40}`, never `.*` inside an
   alternation) and groups non-capturing. A rule answers without any review flag, so add one only
   when the title reliably names the type, and measure how many stored titles it would move first.

4. **Write the migration.** Create an empty revision
   ([How to create a database migration](create-a-database-migration.md)) and give it the shape of
   `backend/alembic/versions/e4b7a2c91d05_job_description_category.py`:
   - `CATEGORY_ID`, `_NAME`, `_DESCRIPTION` and `_EXAMPLES` constants, byte-identical to the
     `taxonomy.py` entry.
   - `upgrade()` counts the category rows other than this id. If there are none, the catalog is
     unseeded and already serves the constant through the fallback, so it prints that, bumps the
     revision and returns. Inserting one row there would end the fallback and collapse the catalog
     to this category alone.
   - Otherwise it inserts the row with `active`, `auto_assign` and `summarize_default` true and
     `ON CONFLICT (id) DO NOTHING`, so a row an admin already created by hand is left as it is. It
     prints which happened.
   - It bumps `catalog_meta.revision` with an upsert
     (`INSERT ... ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1`), so an
     unseeded database with no meta row still gets one and every worker reloads its cache.
   - It inserts **no** `prompts` row. A prompt row would shadow `prompts.py` for this category
     forever; with no row, the code prompt is used.
   - `downgrade()` deletes the row only while no `review_rows` row uses the category, and prints
     why it kept it otherwise.

5. **Add tests.** In `backend/tests/test_catalog.py`, copy the three category-17 tests:
   `test_an_unseeded_catalog_offers_the_job_description_category`,
   `test_the_job_description_category_resolves_its_own_code_prompt` and
   `test_the_job_description_migration_carries_the_constants_text` (which loads the migration file
   and asserts its text equals the constant). If you added a rule, pin real-looking (invented) titles
   for and against it in `backend/tests/test_classification.py`. Then run, from the repo root:

   ```bash
   cd backend
   uv run pytest -q tests/test_catalog.py tests/test_classification.py tests/test_admin_api.py
   ```

   Expected: all pass. `test_admin_api.py` needs the test database; see
   [How to run the tests](run-the-tests.md).

6. **Rebuild all three backend services and migrate.** The constants and rules run on
   `segment-worker` (the classifier image), the admin listing and the editor's rule replay on `api`,
   and the prompts on `summarize-worker`. Building only `api` leaves the segment worker on the old
   code. On a local stack, from the repo root:

   ```bash
   docker compose build api segment-worker summarize-worker
   docker compose run --rm api alembic upgrade head
   docker compose up -d --force-recreate api segment-worker summarize-worker
   ```

   The migration runs from the new image before the containers are replaced, so the new code
   never starts against the old schema (the same order as a deploy).

   Expected: the migration prints `category 18 inserted (...)` on a seeded database, or
   `categories table is unseeded - category 18 NOT inserted...` on a fresh one. For the server, see
   [How to deploy to the server](deploy-to-the-server.md).

## Change an existing category in code

To change a built-in category's name, description or examples for every box:

1. Edit the entry in `taxonomy.py`.
2. Write a migration that updates the row, modelled on
   `backend/alembic/versions/e4c8a1f70b93_widen_category_four.py`:
   - Rewrite a column only while it still holds the old text (`UPDATE ... WHERE id = :cid AND
     description = :expected`), so a description an admin edited is left alone, and print whether
     each column was rewritten.
   - Add or remove example titles one element at a time, never as a whole-array swap, so titles an
     admin added survive.
   - Bump the revision the same way as above. No "is the table empty" guard is needed: an update
     matches nothing on an empty table, and the fallback already serves the new constant there.
3. Add a test asserting the migration's text equals the constant, like
   `test_the_category_four_migration_carries_the_same_text_as_the_constants` in `test_catalog.py`.
4. Rebuild and migrate as in step 6 above.

## Verify it worked

In the admin console, the category is listed with the flags you expect.

On a local stack, from the repo root, check the stored catalog and revision:

```bash
docker compose exec postgres psql -U mrr -d mrr -c "SELECT id, name, active, auto_assign, summarize_default FROM categories ORDER BY id::int;"
docker compose exec postgres psql -U mrr -d mrr -c "SELECT revision FROM catalog_meta;"
```

No rows from the first query means the database serves the built-in constants, which already
include any category added in code.

Check the rule and the cascade on the segment worker with an invented title. The commands below use a
category-17 title; substitute one of your own category's example titles:

```bash
docker compose exec segment-worker python -c "from app.services.classification import match_rules; print(match_rules('Job Description'))"
docker compose exec segment-worker python -c "from app.services.classification import classify; print(classify('Job Description'))"
```

`match_rules` makes no model call and prints the rule's id or `None`. `classify` runs the whole
cascade, which makes an LLM call when no rule matches, and prints the category, confidence and
`method`.

## If it fails or needs undoing

| Symptom | Cause and fix |
| --- | --- |
| 400 `category N already exists` | The id is taken, possibly by a built-in on a database with an empty table. Pick another id. |
| 400 `category id must be a positive number` or `name is required` | Fix the field and save again. |
| 409 when deactivating | Rows still use the category; move them first (above). |
| The classifier never assigns the new category | Check **Active** and **Auto-assign** are on. Rows answered by a rule never reach the votes. Improve the description and examples; a split vote is assigned with a review flag and `method` `llm-disagree`. |
| A code change is not visible on the segment worker | The classifier image was not rebuilt; build `segment-worker` too. |
| Migration printed `already present - left exactly as it is` | An admin created that id by hand earlier; review the row in the console. |

To undo:

- **A console-created category** cannot be deleted. Deactivate it (possible only while no row uses
  it) or untick **Auto-assign**. A custom prompt is reverted from the **Prompt** dialog, which drops
  the prompt row so the built-in prompt applies again.
- **A code-route category**: `docker compose exec api alembic downgrade -1` runs the migration's
  downgrade, which removes the row only while no review row uses the category, then revert the code
  change and rebuild. Downgrading only makes sense if that migration is the newest one applied.

## Related pages

- [Categorization](../explanation/categorization.md) - the cascade, the catalog and its fallback.
- [How to change a summary prompt or rule](change-a-summary-prompt-or-rule.md)
- [How to create a database migration](create-a-database-migration.md) and the
  [migrations reference](../reference/migrations.md).
- [HTTP API reference](../reference/http-api.md) - the `/api/admin` routes.
- [How to manage users and admins](manage-users-and-admins.md)

<!-- reviewed: 2026-09-30 -->
