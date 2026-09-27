# MRR AI documentation

MRR AI (AI Medical Record Review) turns a large scanned medical-record PDF into a reviewed,
summarized Medical Record Review. It finds the sub-documents in the record, categorizes them, lets
a reviewer correct everything, checks for duplicates, drafts and audits a summary of each
sub-document, and exports the review. A reviewer corrects the machine's work at every stage: the
app is an assistant, not an autopilot.

These pages are for the developers and operators who maintain it. They follow the
[Diataxis](https://diataxis.fr/) split: each page is a tutorial, a how-to guide, a reference or an
explanation, never a mix.

## Where to start

| you are | start here | then |
| --- | --- | --- |
| New to the code | [Architecture](explanation/architecture.md) | [Your first day](tutorials/first-day.md), then the explanation page for the part you will change |
| Setting up a machine | [Run the app locally](how-to/run-the-app-locally.md) | [Run the tests](how-to/run-the-tests.md) |
| Running the server | [Deploy to the server](how-to/deploy-to-the-server.md) | [Back up and restore](how-to/back-up-and-restore.md), [Diagnose a stuck or failed job](how-to/diagnose-a-stuck-or-failed-job.md) |
| Answering "why did it do that?" | [Troubleshoot a summary](how-to/troubleshoot-a-summary.md) | [Job and document states](reference/job-and-document-states.md), [Errors and messages](reference/errors-and-messages.md) |
| Looking up a word | [Glossary](reference/glossary.md) | |

## Tutorials - learn by doing

- [Your first day](tutorials/first-day.md) - bring up the stack and take a synthetic record from
  upload to export.

## How-to guides - get a task done

Running it:

- [Run the app locally](how-to/run-the-app-locally.md)
- [Run the tests](how-to/run-the-tests.md)
- [Deploy to the server](how-to/deploy-to-the-server.md)
- [Back up and restore](how-to/back-up-and-restore.md)
- [Manage users and admins](how-to/manage-users-and-admins.md)
- [Diagnose a stuck or failed job](how-to/diagnose-a-stuck-or-failed-job.md)
- [Troubleshoot a summary](how-to/troubleshoot-a-summary.md)
- [Switch model backends](how-to/switch-model-backends.md)

Changing it:

- [Add or change a category](how-to/add-or-change-a-category.md)
- [Change a summary prompt or rule](how-to/change-a-summary-prompt-or-rule.md)
- [Create a database migration](how-to/create-a-database-migration.md)
- [Add an API route or export](how-to/add-an-api-route-or-export.md)
- [Add a job kind or stage](how-to/add-a-job-kind-or-stage.md)
- [Extend the frontend](how-to/extend-the-frontend.md)
- [Work on these docs](how-to/work-on-these-docs.md)

## Reference - look it up

- [HTTP API](reference/http-api.md) and [Errors and messages](reference/errors-and-messages.md)
- [Configuration](reference/configuration.md) - every setting
- [Model calls by stage](reference/model-calls-by-stage.md)
- [Job and document states](reference/job-and-document-states.md)
- [Data model](reference/data-model.md) and [Migrations](reference/migrations.md)
- [Export formats](reference/export-formats.md)
- [Compose services](reference/compose-services.md) and [CI and merge gates](reference/ci-and-merge-gates.md)
- [Scripts](reference/scripts.md)
- [Frontend routes and data](reference/frontend-routes-and-data.md) and [Design system](reference/design-system.md)
- [Glossary](reference/glossary.md)

## Explanation - understand how and why

- [Architecture](explanation/architecture.md) - the whole system on one page
- [Pipeline and jobs](explanation/pipeline-and-jobs.md)
- [OCR and page text](explanation/ocr-and-page-text.md)
- [Segmentation](explanation/segmentation.md)
- [Categorization](explanation/categorization.md)
- [Duplicate detection](explanation/duplicate-detection.md)
- [Summarization](explanation/summarization.md)
- [Deliverable layout](explanation/deliverable-layout.md)
- [Exports and downloads](explanation/exports-and-downloads.md)
- [Model providers](explanation/model-providers.md)
- [Configuration model](explanation/configuration-model.md)
- [Auth and access](explanation/auth-and-access.md)
- [Frontend workbench](explanation/frontend-workbench.md)

## Outside this site

- The code: every folder has a `README.md` (for people) and a `CLAUDE.md` (for AI coding agents).
- Segmentation research: `experiments/a1-segmentation/EXPERIMENT-LOG.md` in the repository
  records what was measured and rejected.
- History: pages written for the pre-rewrite Flask app, the old decision records and the 2025
  source material are archived in `legacy/docs/` in the repository. They describe nothing that
  runs today.
