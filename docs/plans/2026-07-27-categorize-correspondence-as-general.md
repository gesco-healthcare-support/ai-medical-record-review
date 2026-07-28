---
feature: Leading correspondence, routing slips and declarations classify as General (100)
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

In-house routing slips, email/letter correspondence, legal declarations and records-request indexes
are categorized 100 (General) instead of 13 (QME/AME) or 7 (WC legal forms), so they arrive
unchecked for summarization instead of being summarized as evaluations.

## Context & decisions

Why now: on the Sarhad record `record-227pp.pdf` the leading administrative documents landed in
clinical categories - "Email - AME Evaluation Cover Letter" -> 13, "Agreed Medical Evaluation
Request" -> 13, "Declaration of Compliance" -> 13, "... Medical Records Routing Sheet" -> 1,
"Schedule of Records" -> 2.

Diagnosis (verified in code):
- "Email - AME Evaluation Cover Letter" -> 13 comes from the RULES stage: `\b(qme|ame|pqme)\b`
  (`backend/app/services/classification.py:38`) fires on the word "AME" in a title that merely names
  the evaluation it accompanies. Rules match the title only (`classification.py:250-254`).
- The others have no rule and fall to embedding+LLM, which must AGREE for a confident result
  (`classification.py:266-274`). Both are biased away from 100: its corpus is nearly empty
  ("Documents that do not clearly fit any specific category." / "General Documents; Everything else",
  `app/services/taxonomy.py:218-223`) and the LLM prompt says "Choose 100 only if none of the
  specific categories fit" (`classification.py:216`).
- Category 100 IS `auto_assign=True` (only id 6 is not, `app/services/seed_catalog.py:16-51`), so it
  is a legal classifier output.

Resolved decisions:
- Decision: add high-precision TITLE rules mapping administrative documents to 100, placed FIRST in
  `_RULES`, because first match wins and the QME/AME rule must not claim a cover letter that names an
  AME.
- Decision: keep the new rules TIGHT (no bare `letter`, no bare `request`) because 100 is now
  default-UNSELECTED for summarization (PR #41): a false positive silently skips a real clinical
  document, which is worse than the current visible mis-categorization. Ambiguous titles are left to
  the embedding+LLM stage.
- Decision: also enrich category 100's description + examples, because the rules only cover titles we
  have seen and the empty corpus is why the cascade never chooses 100 for anything unseen. The
  taxonomy constants are updated (fresh databases) AND an alembic data migration updates the existing
  row ONLY when it still matches the seeded text, so an admin's own edit is never clobbered.
- Decision: the migration bumps `catalog_meta.revision`, because the classifier caches the catalog +
  embedding matrix per revision (`classification.py:141-160`) and would otherwise keep the old corpus
  until every worker restarts.
- Decision: add one clarifying sentence to the LLM classify prompt (with its reason) rather than
  restructuring the prompt, keeping the change auditable.
- Decision: no re-classification of existing records - the effect appears on the next segment or
  classify run, which the reviewer triggers (standing rule: no automatic AI calls).

## Measured against the real corpus (re-plan, main `6b111c4`)

The rules below were MEASURED against every title in the local dev DB (676 rows, 262 distinct titles)
before being written down, because a false positive now silently skips a clinical document from
summarization (100 is unchecked by default since #41).

- A first draft included `^\s*(e-?mail|fax)\b` and it stole `Fax - Align Networks Updated Progress
  Note` from category 1 (a real progress note behind a fax cover line) - dropped in favour of
  `^\s*e-?mail\s*$` plus the `cover letter` / `transmittal letter` / `correspondence` phrases.
- The refined set OVERRIDES exactly one existing rule outcome: `Email - AME Cover Letter`, today
  13 because the QME/AME rule fires on "AME" - the exact case this work is for.
- It makes 31 further titles deterministic that today depend on embedding+LLM agreement: 20 already
  land on 100 (so the rule only removes an AI call and the risk of drift), and 11 change - 6 from
  7 (subpoena declaration, penalty-of-perjury declarations, declaration of readiness, proofs of
  service, records request), 2 from 13, 2 from 12 (routing slips), 1 from 9.
- Controls that must NOT be claimed all pass: QME Panel Report -> 13, Request for Authorization ->
  10, PR-2 -> 1, MRI -> 3, Deposition -> 9, Operative Report -> 8, and the faxed progress note -> 1.
- `eval-request` has zero matches locally (the "Agreed Medical Evaluation Request" case came from
  the Sarhad record), so it is carried on the strength of the controls only.

Decision: `Declaration of Readiness to Proceed` and `Declaration for Subpoena Duces Tecum` move from
7 to 100 along with the other declarations, because Adrian asked for legal declarations to land in
General and neither is a medical record to summarize. Flagged in the PR so it can be reversed with
one pattern edit if he disagrees.

## All needed context

- `_RULES` tuple (`backend/app/services/classification.py:34-67`): ordered, first match wins;
  `match_rules` lowercases the title (`:245-252`). The QME/AME rule is the second entry (`:38`), the
  RFA rule maps `request for authorization` -> 10 (`:56`), deposition -> 9 (`:55`).
- `llm_classify` prompt (`classification.py:214-218`) + `system_instruction` (`:224-227`).
- `_corpus()` builds the embedding text as `name. description Examples: a; b`
  (`classification.py:230-233`); the LLM sees `- id: name - description` (`:139-141`).
- `taxonomy.CATEGORIES["100"]` (`app/services/taxonomy.py:218-223`) - a dataclass
  `Category(id, name, description, examples)`; `DEFAULT_ID = "100"` (`:230`).
- `Category` table columns: `description` (Text), `examples` (JSON list)
  (`backend/app/models.py:361-373`); revision bump helper `catalog.bump_revision`
  (`app/services/catalog.py:88-97`) - the migration does the equivalent in SQL.
- Alembic head is `a7c3f2e9b1d4` (`backend/alembic/versions/a7c3f2e9b1d4_category_summarize_default.py`);
  confirm with `uv run alembic heads` before writing `down_revision`. Mirror that file's
  data-backfill style (`op.execute` after the schema step).
- Tests: `backend/tests/test_classification.py` (monkeypatched genai, no rule tests yet),
  `backend/tests/test_catalog.py` for catalog constants.
- HIPAA: test titles must be SYNTHETIC - use "Acme Medical Records Routing Sheet", never the real
  firm/patient names seen on the server.

## Tasks (implementation blueprint)

### Task 1 - administrative title rules
- what: MODIFY `backend/app/services/classification.py`: insert as the FIRST entries of `_RULES`,
  under a comment stating why they precede the QME/AME rule and that precision matters because 100 is
  unchecked for summarization by default:
  `(r"routing (sheet|slip|form)|records? routing", "100")`,
  `(r"\b(cover|transmittal) letter\b|\bcorrespondence\b|^\s*e-?mail\s*$", "100")`,
  `(r"^declaration\b|proof of service|certificate of (service|mailing)|declaration under penalty", "100")`,
  `(r"schedule of records|index of records|records? (request|index)|request for (medical )?records", "100")`,
  `(r"\b(request|notice|scheduling) (for |of |to )?[\w\s-]{0,24}\b(evaluation|examination)\b|\b(evaluation|examination) (request|notice|appointment)\b", "100")`.
  These are the MEASURED patterns (see the section above), not a first draft.
- pattern: the existing rule tuples at `classification.py:36-66`.
- approach: tdd
- acceptance (EARS):
  - WHEN a title names a records routing sheet/slip, a cover or transmittal letter, correspondence, an
    email or fax, a declaration, a proof/certificate of service, a records request or a records
    index/schedule, THE SYSTEM SHALL classify it as 100 by rule.
  - WHEN a title requests or notices an evaluation or examination (e.g. "Agreed Medical Evaluation
    Request"), THE SYSTEM SHALL classify it as 100 by rule.
  - WHEN a title is "Request for Authorization", THE SYSTEM SHALL still classify it as 10.
  - WHEN a title names an actual QME/AME report, a deposition transcript, an MRI or a PR-2, THE
    SYSTEM SHALL still classify it as 13, 9, 3 and 1 respectively.

### Task 2 - LLM prompt guidance
- what: MODIFY `backend/app/services/classification.py` `llm_classify`'s prompt: after the existing
  "Choose 100 only if none of the specific categories fit." add
  "Administrative and correspondence documents - routing slips, cover letters, emails and faxes,
  legal declarations, proofs of service, records requests and record indexes - are 100 even when they
  mention a QME/AME or another document type, because they accompany that document rather than being
  it."
- pattern: the prompt string at `classification.py:214-218`.
- approach: code
- acceptance (EARS): WHEN the LLM stage classifies a document, THE SYSTEM SHALL include the
  administrative-documents instruction in its prompt.

### Task 3 - richer General corpus (constants)
- what: MODIFY `backend/app/services/taxonomy.py` `CATEGORIES["100"]`: description ->
  "Administrative, correspondence and other documents that do not fit a specific clinical category:
  in-house routing slips, cover letters, emails and faxes, legal declarations, proofs of service,
  records requests and record indexes."; examples -> `("Medical Records Routing Sheet",
  "Email - Evaluation Cover Letter", "Declaration of Compliance", "Proof of Service",
  "Schedule of Records", "Medical Evaluation Request")`.
- pattern: the sibling category entries at `taxonomy.py:36-224`.
- approach: code
- acceptance (EARS): WHEN a fresh database is seeded, THE SYSTEM SHALL store the administrative
  description and examples for category 100.

### Task 4 - migration for existing databases
- what: CREATE `backend/alembic/versions/<rev>_general_category_corpus.py` with
  `down_revision = "a7c3f2e9b1d4"`: `op.execute` an UPDATE of `categories` setting the new
  description + examples JSON for `id = '100'` guarded by
  `AND description = 'Documents that do not clearly fit any specific category.'`; then
  `UPDATE catalog_meta SET revision = revision + 1 WHERE id = 1`. Downgrade restores the previous
  description/examples under the mirrored guard and bumps the revision again.
- pattern: the data-backfill statements in
  `backend/alembic/versions/a7c3f2e9b1d4_category_summarize_default.py`.
- approach: code
- acceptance (EARS):
  - WHEN the migration runs on a database whose category 100 still holds the seeded text, THE SYSTEM
    SHALL replace its description and examples and bump the catalog revision.
  - WHEN category 100 has been edited in the admin UI, THE SYSTEM SHALL leave it unchanged.

### Task 5 - tests
- what: EXTEND `backend/tests/test_classification.py` with `match_rules` cases for Task 1's four
  criteria using SYNTHETIC titles, and one assertion that
  `taxonomy.CATEGORIES["100"].description` names routing slips and correspondence so the corpus
  cannot silently regress.
- pattern: the existing test style in `tests/test_classification.py:22-36`.
- approach: tdd
- acceptance (EARS): The system shall pass the full backend suite with new-code coverage >= 80%.

## Validation loop

1. BE: `uv run ruff check . && uv run ruff format --check .`
2. BE: `uv run pytest tests/test_classification.py -q` then `uv run pytest -q` (the ~5
   enqueue/queue-count failures are the known live-RQ-worker drain)
3. Migration: `docker compose run --rm api uv run alembic upgrade head`, then confirm in psql that
   category 100's description changed and `catalog_meta.revision` incremented; `alembic downgrade -1`
   restores it, then upgrade again.
4. Live cascade check (real AI, 6 short calls) in the segment worker container - confirm the name with
   `docker ps` first: `docker exec mrr-segment-worker-1 python -c` running `classify(title)` over the
   six synthetic administrative titles plus "Request for Authorization" and "PR-2 Progress Report",
   printing category + method; expect 100 for the six (method `rules`) and 10 / 1 for the controls.

## Risk / rollback

- Blast radius: every future classification (segmentation + the individual-records classify job) and
  the DB catalog row for 100. Existing records are untouched until re-run.
- Main risk is a rule false positive silently excluding a clinical document from summarization;
  mitigated by tight patterns, the control tests, and the fact that the reviewer still sees the
  category and the unchecked box in Review & correct.
- Rollback: revert the PR and `alembic downgrade -1` (the guarded UPDATE makes both directions
  idempotent).
