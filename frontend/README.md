# frontend - MRR AI web app (Next.js)

The browser app reviewers use: sign-in, the documents list, the record workbench at
`/records/[id]` (review and correct, duplicates, summaries, beside the PDF), the category bundle
pages and the admin console. It talks only to the backend's `/api/` on the same origin, so the
HttpOnly session cookie works and there is no CORS.

Stack: Next.js 15 (App Router, `output: "standalone"`), React 19, TypeScript, TanStack Query 5,
Tailwind 4 with a hand-written design system (`app/evaluators-ds.css`), Radix-based primitives in
`components/ui/`, a vendored pdf.js viewer in `public/pdfjs/`. Tests: vitest (jsdom) and
Playwright.

How it works: [Frontend workbench](../docs/explanation/frontend-workbench.md). Every route and API
call: [Frontend routes and data](../docs/reference/frontend-routes-and-data.md).

## Layout

| path | what it is |
| --- | --- |
| [`app/`](app/README.md) | App Router routes, the root layout, providers and global styles. |
| [`components/`](components/README.md) | UI by feature: `review/` (the workbench), `documents/`, `bundle/`, `admin/`, `auth/`, `app/` (shell), `ui/` (primitives). |
| [`hooks/`](hooks/README.md) | Data and workflow hooks, including `use-review-workflow` which drives the workbench. |
| [`lib/`](lib/README.md) | The API client (`api.ts`), per-area API functions, shared types, error wording, download handling. |
| [`e2e/`](e2e/README.md) | Playwright specs that run against the full app stack. |
| `public/` | Static files: the vendored pdf.js viewer and images. |
| `Dockerfile` | Builds the standalone server image (`mrr-frontend`), Node 22. |

## Develop

```bash
corepack enable            # provides pnpm (version pinned in package.json)
cd frontend
pnpm install
pnpm dev                   # http://localhost:3000
```

`next.config.ts` forwards `/api/*` to the backend at `API_ORIGIN` (default
`http://127.0.0.1:8000` - 127.0.0.1 rather than localhost so Windows does not resolve it to IPv6).
Run the backend as described in [`../backend/README.md`](../backend/README.md). In the Docker stack
the nginx proxy routes `/api/` itself.

## Scripts

| command | what it does |
| --- | --- |
| `pnpm dev` | Development server on :3000. |
| `pnpm build` / `pnpm start` | Production build and server. |
| `pnpm typecheck` | `tsc --noEmit`. |
| `pnpm test` | vitest, once. `pnpm test:watch` to watch. |
| `pnpm test:coverage` | vitest with coverage (CI requires 80% on statements, branches, functions and lines). |
| `pnpm e2e` | Playwright against `E2E_BASE_URL` (default `http://localhost:8080`) - start the app stack first. |

More: [Run the tests](../docs/how-to/run-the-tests.md), [Extend the frontend](../docs/how-to/extend-the-frontend.md).
