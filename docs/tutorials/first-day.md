# Your first day with MRR AI

This tutorial is for a developer who has just inherited MRR AI and has never run it. You will work
through the whole product once, the way a reviewer uses it, and then run the two test suites.

By the end you will have:

- the full application stack running on your machine at `http://localhost:8080`;
- an account of your own, with admin rights;
- one record taken through every step: upload, identification, review, duplicate check,
  summarization and export;
- the test stack running, with one backend test and one frontend test passing.

Plan for a slow first build: the segmentation worker image installs PyTorch and downloads an
embedding model while it builds.

## Prerequisites

- Git, and a Bash shell. On Windows use Git Bash; every command below is written for Bash.
- Docker with the Compose v2 plugin (`docker compose version` prints a version). On Windows and
  macOS this is Docker Desktop.
- Internet access while the images build (base images, Python and Node packages, and the
  embedding model are all downloaded at build time).
- [uv](https://docs.astral.sh/uv/) for the backend tests, and Node.js 24 with corepack for the
  frontend tests. CI uses Node 24.
- Model credentials, for step 7 onward. The default backend is Gemini on Vertex AI, so you need:
    - the ID of a Google Cloud project with Vertex AI enabled, and
    - a service-account key file (JSON) for an account in that project that is allowed to call
      Gemini models on Vertex AI.

    Ask the project owner for both. Without them you can still do steps 1 to 6; identification
    will fail at step 7. Other backends (OpenAI, a self-hosted vLLM server) are described in
    [How to switch model backends](../how-to/switch-model-backends.md).

## Step 1: Clone the repository

```bash
git clone https://github.com/gesco-healthcare-support/ai-medical-record-review.git
cd ai-medical-record-review
```

Expected result: the folder contains `backend/`, `frontend/`, `deploy/`, `docs/`,
`docker-compose.yml` and `docker-compose.dev.yml`. Every later command runs from this folder
unless a step says otherwise.

## Step 2: Create the environment file

The app stack reads a `.env` file at the repository root. Start from the template made for the
container stack:

```bash
cp deploy/env.docker.example .env
```

Generate a signing key and a password salt:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Open `.env` in an editor and set:

| Key | Value for today |
| --- | --- |
| `SECRET_KEY` | The first generated value. |
| `SECURITY_PASSWORD_SALT` | The second generated value. Keep it: every password stored from now on is hashed with it, and changing it later makes those passwords stop working. |
| `ENVIRONMENT` | Leave it as `dev`. `prod` is for the server behind HTTPS: it marks the session cookie Secure, which browsers store only over HTTPS (some make an exception for `localhost`), and it refuses to start unless Vertex is on. |
| `GOOGLE_CLOUD_PROJECT` | Your Google Cloud project ID. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Leave it as `/secrets/vertex-sa.json`. |

Then copy the service-account key into the `secrets/` folder under that exact name:

```bash
cp /path/to/your-key.json secrets/vertex-sa.json
```

`.env` and everything in `secrets/` except `.gitkeep` are ignored by git. Never commit them.

Check that Compose can read the file:

```bash
docker compose config --quiet
```

Expected result: no output and exit status 0. If it prints `set SECRET_KEY in .env`, the key is
missing or empty.

## Step 3: Build the images

```bash
docker compose build
```

Expected result: the build finishes with no error. It builds four images: `mrr-backend-web` (the
API and the summarize workers), `mrr-backend-classifier` (the segment workers, with PyTorch),
`mrr-frontend` and `mrr-docs`. The classifier image is the slow one.

## Step 4: Create the database schema

Start Postgres and Redis on their own first, then run the migrations with the API image:

```bash
docker compose up -d --wait postgres redis
docker compose run --rm api alembic upgrade head
```

Expected result: Alembic prints one `Running upgrade` line per migration and exits without an
error.

## Step 5: Start the whole stack

```bash
docker compose up -d
docker compose ps
```

Expected result: `docker compose ps` lists `postgres`, `redis`, `api`, `web`, `proxy`, `docs`, and
three containers each for `segment-worker` and `summarize-worker`, all running. Open
`http://localhost:8080` in a browser: you see the sign-in page. The documentation site is served
by the same stack at `http://localhost:8080/docs/`.

## Step 6: Create your account and make it an admin

1. In the browser go to `http://localhost:8080/login?view=register`.
2. Fill in Full name, Email address, Password and Confirm password, then select Create account.
   The password needs at least 8 characters, a number and a symbol.

Expected result: you are signed in and see the "Start your first review" screen.

Now restart the workers. Each worker builds its list of per-user queues when it starts, so a
worker that was already running has no queue for the account you just created, and your jobs
would wait forever:

```bash
docker compose restart segment-worker summarize-worker
```

Grant yourself admin rights with the admin command-line tool, using the email you registered:

```bash
docker compose exec api python -m app.cli admin grant you@example.com
```

Expected result: `Granted admin to you@example.com`. Reload the page; the menu under your name now
has an Admin entry, which opens the category and prompt editor. See
[How to manage users and admins](../how-to/manage-users-and-admins.md) for the rest of the tool.

## Step 7: Upload the sample record and identify its documents

The repository carries a small PDF used by the end-to-end tests:
`frontend/e2e/fixtures/sample.pdf`. It is committed to this public repository and must stay
synthetic. Use it for every exercise; never upload a real record to a development machine.

1. On the "Start your first review" screen select Browse files and choose
   `frontend/e2e/fixtures/sample.pdf`, or drag the file onto the page.
2. Select the new row in the documents table. The record opens at `/records/<id>` with the heading
   "Ready to identify documents".
3. Select Identify documents.

Expected result: a progress panel follows the job while a segment worker splits the record into
its component documents and assigns each one a category. When it finishes, step 2 of the stepper,
Review & correct, shows a table with one row per identified document.

If the job fails, the page shows the reason. The usual first-day causes are missing or wrong
Vertex credentials (step 2) and a worker that was not restarted after you registered (step 6).
[How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md) covers the
rest.

## Step 8: Review the rows

This is where a reviewer corrects the model's work: document boundaries, titles, dates and
categories. Change a title in one row to see the editor work, and wait for the save indicator to
read Saved.

Expected result: your edit is still there after you reload the page. The rows you see are the
reviewer's copy; the model's original output is kept separately.

## Step 9: Run the duplicate check

Select Check duplicates.

Expected result: a duplicate-check job runs on a summarize worker, then the Duplicates tab shows
any groups of documents that look like copies of each other. The sample record is tiny, so an
empty result is normal. Summarizing is refused until a completed duplicate check covers the
current rows, unless the reviewer explicitly chooses to skip the check (which is recorded);
[Duplicate detection](../explanation/duplicate-detection.md) explains why.

## Step 10: Summarize

Select the Summarize button (its label counts the documents that will be summarized).

Expected result: a summarize job runs, and when it finishes step 3 of the stepper, Summaries,
shows one summary per included document.

## Step 11: Export

Select Export. The dialog asks for header details that go into the report. Type obviously
synthetic values (for example "Test Patient" and "01/01/1970"), then select Export to Word.

Expected result: the browser downloads a Word file named from the header,
`<Last>_<First>_Medical_Records_summary.docx` (without a patient name it is named after the
uploaded file instead). The other buttons produce the linked
PDF, the covering memo and a zip of everything; see
[Export formats](../reference/export-formats.md).

## Step 12: Start the test stack

The tests use their own Postgres and Redis, separate from the app stack you just used. Start them
under their own Compose project name so they can never be confused with the app's containers:

```bash
docker compose -p mrrtest -f docker-compose.dev.yml up -d --wait postgres redis
```

Expected result: two containers, publishing Postgres on host port 5432 and Redis on 6379. The app
stack keeps running beside them; its Postgres is on 5433 and its Redis publishes no host port.

## Step 13: Run one backend test

```bash
cd backend
uv sync --extra docs
DATABASE_URL=postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr SECRET_KEY=dev-only-secret SECURITY_PASSWORD_SALT=dev-only-salt uv run alembic upgrade head
uv run pytest -q tests/test_files.py
cd ..
```

Expected result: pytest reports the tests in that file as passed. The `--extra docs` flag matters:
without it the PDF and model libraries are missing and the application cannot be imported.

## Step 14: Run one frontend test

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm exec vitest run components/review/stepper.test.tsx
cd ..
```

Expected result: Vitest reports one test file passed.

## Step 15: Stop what you started

```bash
docker compose -p mrrtest -f docker-compose.dev.yml down
docker compose down
```

Both commands keep the data volumes, so the next `up` finds your account and record again.

## What you learned

- The app is one Compose stack: a proxy on port 8080 in front of a Next.js frontend and a FastAPI
  API, with Postgres, Redis and two kinds of queue worker behind them.
- The reviewer's path is upload -> identify -> review -> duplicate check -> summarize -> export,
  and each AI step is a background job on a worker.
- Workers learn about users only when they start, so they need a restart after an account is
  created.
- Tests never touch the app database. They run against a separate throwaway stack on port 5432.

## Where next

- [Architecture](../explanation/architecture.md) for how the pieces fit together.
- [Pipeline and jobs](../explanation/pipeline-and-jobs.md) for what the workers do.
- [How to run the app locally](../how-to/run-the-app-locally.md) for rebuild rules and Windows
  notes.
- [How to run the tests](../how-to/run-the-tests.md) for the full suites and the coverage floors.
