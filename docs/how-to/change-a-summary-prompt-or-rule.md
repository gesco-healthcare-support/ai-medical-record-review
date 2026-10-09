# How to change a summary prompt or rule

Use this when a summary includes something it should not, leaves out something it should, or comes
out in the wrong shape, and the fix is to change what the model is told or what the code does to
its answer. Examples: a category should report a new point, a shared rule is too broad, the audit
keeps deleting content it should keep, or an acronym is being lower-cased.

If the problem is one summary rather than a rule, start with
[How to troubleshoot a summary](troubleshoot-a-summary.md). If you are adding a category, use
[How to add or change a category](add-or-change-a-category.md).

## Prerequisites

- A backend development environment ([Run the app locally](run-the-app-locally.md)).
- The test database and Redis running, with migrations applied ([Run the tests](run-the-tests.md)).
- For a deployment-only prompt change: an admin account ([Manage users and admins](manage-users-and-admins.md)).
- Read [Summarization](../explanation/summarization.md) first. Every rule block in the code carries
  a comment saying why it exists and what was measured; read it before changing the rule.

## Where each rule lives

A rule usually has a pair. Change both halves in the same commit.

| To change | Edit | Also edit | Tests that pin it |
| --- | --- | --- | --- |
| What one category's summary covers | `backend/app/services/prompts.py`, key `category_NN` (or the admin console for one deployment) | Nothing, but check no admin prompt row shadows it (step 2) | `backend/tests/test_prompt_content.py` |
| A shared rule (factuality, normal findings, verdict, embedded review, billing codes, height and weight, pain, range of motion, one paragraph, deposition format, bold labels, sentence case) | The block constant in `backend/app/services/summarize_engine.py` (`_FACTUALITY`, `_C_*`, `_F_*`) | The matching audit rule in `backend/app/services/summary_verify.py` `_HOUSE_RULES`, and `VERIFY_PROMPT` if what the audit may delete changes | `test_summarize_engine.py` (the `test_the_preamble_*` and `test_the_*_rule_*` tests), `test_summary_verify.py` |
| Which categories receive a shared rule | The category sets above `build_preamble()` in `summarize_engine.py` | `backend/app/worker/tasks.py` `_EMBEDDED_REVIEW_HOSTS` when changing `_EMBEDDED_REVIEW_CATEGORIES` | `test_the_preamble_is_assembled_per_category`, `test_every_catalog_category_is_registered_in_the_preamble_sets`, `test_the_two_embedded_review_category_sets_agree` |
| The previous-visit rule | `_document_date_block()` and `_CURRENT_VISIT_CATEGORIES` in `summarize_engine.py` | Audit rule 6 in `_HOUSE_RULES`; the date gate in `_verified_outputs()` uses the same set | `test_other_categories_are_not_given_a_document_date`, `test_the_audit_is_not_given_a_document_date_outside_the_current_visit_categories` |
| The header line | `TITLE_PROMPT` in `summarize_engine.py` | The title paragraph of `VERIFY_PROMPT`; the parsers of the header shape, `without_address()`, `tidy_author_and_facility()`, `without_wc_agency()` and `category_title()` (category 7 only), `title_elements()`, `consistent_authors()`, `consistent_facilities()` and `backend/app/services/bundles.py` `split_deliverable_title()`; the `_TITLE_IMAGE_INSTRUCTION` sent after the images | `test_summarize_engine.py`, `test_bundle_cover.py` |
| An audit issue type | The `enum` in `_RESPONSE_SCHEMA` and the list in `VERIFY_PROMPT` (`summary_verify.py`) | `_CORRECTION_ONLY_ISSUES` or `_FABRICATION_ISSUE` in `summarize_engine.py`, if the rewrite guards should treat it specially | `test_every_house_rule_has_its_own_issue_type` |
| Capitalisation or one-paragraph clean-up | `backend/app/services/house_style.py` (`_ACRONYMS`, `_PROPER_NOUN_MARKERS`, `one_paragraph()`) | The acronym list in `_F_SENTENCE_CASE` and in audit rule 3 | `test_house_style.py` |
| The `**DOI**:` prefix grammar | `backend/app/services/summary_doi.py` | The copy in `frontend/components/review/summaries-view.tsx` (`DOI_PREFIX_NEW`, `DOI_PREFIX_LEGACY`) | `test_summary_doi.py`, `frontend/components/review/summaries-view.test.tsx` |
| Letter wording or emphasis in the delivered documents | `backend/app/services/reporting.py` | `frontend/components/review/markdown-text.tsx` for emphasis | `test_reporting.py` |

## Steps

1. **Decide which row of the table your change is.** A rule that should hold for many categories
    belongs in a shared block, not copied into several category prompts. A point that belongs to one
    document type belongs in that category's prompt.

2. **Check whether an admin prompt row shadows the code prompt.** A category prompt is resolved
    database first (`backend/app/services/catalog.py` `get_prompt()`): a row in the `prompts` table,
    saved from the admin console, always wins over `prompts.py`. On the deployment you care about, list
    them:

    ```bash
    docker compose exec postgres psql -U mrr -d mrr -c "SELECT category_id, revision, length(text) FROM prompts WHERE role = 'summary' ORDER BY category_id;"
    ```

    Expected result: no rows, or the categories that carry a custom prompt. In the admin console the
    same categories show the `Custom` badge. A code change to a category listed here will not reach it
    until its custom prompt is reverted (see [Undo](#undo)).

3. **Edit the category prompt**, if that is your change. Either:

    - **In code**, for every deployment: edit the `category_NN` string in `prompts.py`. Category 11
      has no key on purpose and uses the general prompt (`category_100`); the reason is recorded in
      `get_prompt()`'s docstring. Or
    - **In the admin console**, for one deployment: open `/admin`, press **Prompt** on the category's
      row, edit the text and press **Save prompt**. This creates or updates a `prompts` row, bumps the
      catalog revision and writes a `prompt.update` audit event. New summarize runs pick it up; nothing
      needs deploying.

4. **Edit a shared rule**, if that is your change. Edit the block in `summarize_engine.py` and the
    matching rule in `_HOUSE_RULES` together. They are phrased differently on purpose: generation says
    "do not write X", the audit says "find and remove X". Keep the reason comment above the block up to
    date. If the new audit rule may only correct, never delete, add its issue type to
    `_CORRECTION_ONLY_ISSUES`, or the heading guard will not protect bodies from it.

5. **Change category-set membership**, if that is your change. Every id in `taxonomy.py` must appear
    in `_KNOWN_CATEGORIES`, or it silently receives every block. Write the reason next to any set
    change, as the comments for categories 15, 16 and 17 do.

6. **Update the tests.** Pin new prompt wording in `test_prompt_content.py` (it asserts on the prompt
    text itself); pin preamble composition or audit wording in `test_summarize_engine.py` and
    `test_summary_verify.py`. Watch each new test fail against the old text before trusting it.

7. **Run the tests and the lint gates** from the repository root:

    ```bash
    cd backend
    ```

    ```bash
    uv run pytest -q tests/test_prompt_content.py tests/test_summarize_engine.py tests/test_summary_verify.py tests/test_house_style.py tests/test_provenance.py tests/test_summary_doi.py tests/test_reporting.py tests/test_bundle_cover.py
    ```

    ```bash
    uv run ruff check .
    ```

    ```bash
    uv run ruff format --check .
    ```

    If you changed the DOI grammar or emphasis in the frontend, run its tests too. From `backend/`:

    ```bash
    cd ../frontend
    ```

    ```bash
    pnpm test
    ```

    Expected result: every test passes and both ruff commands report no findings.

8. **Deploy** the change as described in [How to deploy to the server](deploy-to-the-server.md).
    Summaries are written by the summarize worker and re-drafts by the API, so both run the new code
    only once their images are rebuilt.

9. **Regenerate the summaries that should change.** Existing summaries keep the text they were
    written with; a normal Summarize run only writes rows that have no summary yet. On each record that
    should pick up the change, either:

    - press **Re-summarize all from scratch** on the Summaries tab, which deletes every summary of the
      record and regenerates them, discarding the reviewer's edits on that record; or
    - press **Re-draft** on one summary card, which regenerates that summary and discards its edits.

## Verify it worked

- **The prompt fingerprint moved.** Each summary stores `prompt_fingerprint`, a hash of the preamble
    and category prompt it was written from. Rows written after the change should carry a new value:

    ```bash
    docker compose exec postgres psql -U mrr -d mrr -c "SELECT prompt_fingerprint, count(*), max(updated_at) FROM summaries WHERE row_category = '1' GROUP BY prompt_fingerprint ORDER BY max(updated_at) DESC NULLS LAST;"
    ```

    Replace `'1'` with your category. The fingerprint covers prompt text only, not the per-row blocks
    or the house-style code; for a code-only change compare `jobs.build_sha` instead.

- **The expected fingerprint.** To compute what the code prompt of a category hashes to (this is the
    value only when no admin prompt row shadows it), from `backend/`:

    ```bash
    uv run python -c "from app.services.prompts import prompts; from app.services.provenance import summary_prompt_fingerprint; from app.services.summarize_engine import build_preamble; print(summary_prompt_fingerprint(build_preamble('1'), prompts['category_01']))"
    ```

- **A shared-rule change moved the audit too.** `summaries.audit_fingerprint` hashes `VERIFY_PROMPT`
    alone, so it changes when you edit `_HOUSE_RULES`.

- **Read a regenerated summary** against its source pages on the Summaries tab. Open the list of
    things the AI check flagged under the card; a rule that the audit keeps firing on shows up there by
    issue type.

## Undo

- **A code change:** revert the commit, deploy again, and regenerate the affected summaries as in
  step 9.
- **An admin prompt:** in `/admin`, press **Prompt** on the category and then **Revert to built-in**.
  This deletes the custom row (`DELETE /api/admin/prompts/{category_id}`), bumps the catalog revision
  and writes a `prompt.revert` audit event. The custom text is not kept anywhere; the audit event
  records only its revision and length, so copy the text first if you may want it back.

If the tests fail after your edit:

- `test_every_catalog_category_is_registered_in_the_preamble_sets`: a category id is missing from
  `_KNOWN_CATEGORIES`.
- `test_the_two_embedded_review_category_sets_agree`: `_EMBEDDED_REVIEW_CATEGORIES` and
  `_EMBEDDED_REVIEW_HOSTS` differ.
- `test_every_house_rule_has_its_own_issue_type`: an audit rule has no issue type in the schema.
- A `test_both_renderers_*` test in `test_reporting.py`: the Word and PDF letters no longer agree;
  change the shared builder rather than one renderer.

## Related pages

- [Summarization](../explanation/summarization.md)
- [Deliverable layout](../explanation/deliverable-layout.md)
- [How to troubleshoot a summary](troubleshoot-a-summary.md)
- [How to add or change a category](add-or-change-a-category.md)
- [How to run the tests](run-the-tests.md)
- [How to deploy to the server](deploy-to-the-server.md)
- [Data model](../reference/data-model.md)

<!-- reviewed: 2026-09-30 -->
