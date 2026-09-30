# frontend/e2e - agent instructions

## Rules

- Specs run against the LIVE stack on `E2E_BASE_URL` (default `http://localhost:8080`). Do not add a
  `webServer` to `playwright.config.ts` and do not mock the backend here; unit and component behaviour
  belongs in vitest.
- Start every spec from a fresh account with `registerAndLogin(page)`. Specs run in parallel against one
  database; never depend on another spec's data or on a pre-seeded user.
- Test data is synthetic only. Addresses come from `uniqueEmail()` on `example.com`; the password is
  `PASSWORD` from `support.ts`. Never use a real name, address, date of birth or record.
- Fixtures go in `e2e/fixtures/` and must contain no patient data. PDFs are ignored repo-wide;
  `frontend/.gitignore` re-includes `e2e/fixtures/**`, so a fixture elsewhere will not be committed.
- Nothing that needs the AI workers can be tested here: CI starts only `api`, `web` and `proxy`.
  Assert the screens that do not need a model (the identify start panel, menus, uploads, deletes).
- Locate by role and accessible name. The user-menu trigger's name changes with the viewport (the name
  is `hidden sm:block`), so use `userMenuTrigger(page)`.
- Uploads: `page.locator('input[type="file"]').setInputFiles(...)` works on the hidden input.

## Traps

- A spec that passes locally and fails in CI: CI retries once and forbids `.only` (`CI` is set). Check
  the uploaded `playwright-report` artifact.
- The app image is baked. After changing frontend code, rebuild and recreate the `web` service before
  running specs, or they test the previous build.
- The first request after the stack starts can hit an API that is not bound yet; CI waits for both
  `/login` and the API's `/health`. Locally, wait for the app before running.

## Commands

Run from `frontend/`, with the stack up:

```bash
pnpm exec playwright install chromium
pnpm e2e
pnpm exec playwright test e2e/documents.spec.ts
```

## Docs

- `docs/how-to/run-the-tests.md`
- `docs/how-to/extend-the-frontend.md` (tests to add)
- `docs/reference/ci-and-merge-gates.md`
