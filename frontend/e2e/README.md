# frontend/e2e

Playwright end-to-end specs. They drive a real browser against the running app stack (the nginx proxy
on port 8080 in front of the web app, the API, Postgres and Redis), never a mocked backend, so they
check that the pieces work together. There is deliberately no `webServer` in
`frontend/playwright.config.ts`: the app is more than the Next server.

| Path | What it is |
| --- | --- |
| `support.ts` | Helpers: `uniqueEmail()` (a fresh address on the reserved `example.com` domain), `PASSWORD` (a synthetic password that meets the rules), `userMenuTrigger()`, `registerAndLogin()` |
| `auth.spec.ts` | Register, sign out, and sign back in |
| `documents.spec.ts` | Upload the sample PDF, open it (the identify start panel shows), then delete it |
| `navigation.spec.ts` | A non-admin has no Admin menu item; both bundle pages are reachable |
| `fixtures/sample.pdf` | A synthetic PDF with no patient data, committed through an exception in `frontend/.gitignore` |

Configuration (`frontend/playwright.config.ts`): Chromium only, fully parallel, base URL from
`E2E_BASE_URL` (default `http://localhost:8080`), one retry and no `.only` when `CI` is set, a trace on
the first retry, and an HTML report in `frontend/playwright-report/`.

## Use, run and test

Start the app stack first ([how to run the app locally](../../docs/how-to/run-the-app-locally.md)),
then from `frontend/`:

```bash
cd frontend
pnpm exec playwright install chromium
pnpm e2e
```

To run one spec:

```bash
pnpm exec playwright test e2e/auth.spec.ts
```

In CI the `e2e` job builds the `api` and `web` images, migrates the database, starts `api`, `web` and
`proxy`, waits for both the web page and the API health check, and runs the specs. It starts no AI
workers, so identification and summarization are not covered here.

## Documentation

- [How to run the tests](../../docs/how-to/run-the-tests.md)
- [How to extend the frontend](../../docs/how-to/extend-the-frontend.md) (tests to add)
- [CI and merge gates reference](../../docs/reference/ci-and-merge-gates.md)
