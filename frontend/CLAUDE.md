# frontend - agent instructions

Next.js 15 App Router, React 19, TypeScript, TanStack Query 5, Tailwind 4, pnpm. Folder rules are
in the `CLAUDE.md` of `app/`, `components/` (and each feature folder under it), `hooks/`, `lib/`
and `e2e/`.

## Rules

- All data comes from the backend's `/api/` in the browser, through `lib/api.ts` (`apiFetch`) or
  `lib/download.ts`. No server-side data fetching, no route handlers, no direct backend URL.
- Show errors with `humanizeError` from `lib/errors.ts`. It keeps a server's own message only for
  an `ApiError`; any other thrown error falls back to generic copy.
- User-facing copy is plain and neutral: say what happened and what to do. No reassurance, no
  promises about speed.
- Do not run prettier: the repository has no prettier config and is not prettier-formatted, so it
  would reformat hundreds of unrelated lines. `pnpm typecheck` and the tests are the gates.
- Tests share ONE jsdom worker (`vitest.config.mts`). Restore anything you change on `window`,
  `document`, prototypes or environment stubs, and turn fake timers off, or a later file fails.
- A test file named after a component is not proof its behaviour is covered: if a component has a
  click or change handler, write an interaction test that fires it.
- Never put real patient data in a fixture, story or example. The only PDF fixture,
  `e2e/fixtures/sample.pdf`, is committed to this public repository and must stay synthetic.
- The workbench's rules (keys, `replaceRows`, fields the server writes, cache invalidation after a
  run) are in `components/review/CLAUDE.md` and `hooks/CLAUDE.md`. Read them before touching rows.

## Commands

```bash
cd frontend
pnpm install
pnpm typecheck
pnpm test                           # all vitest files
pnpm test -- components/review      # a subset
pnpm test:coverage                  # what CI measures (80% floor on all four metrics)
pnpm e2e                            # needs `docker compose up -d` on :8080
```

Docs: `docs/explanation/frontend-workbench.md`, `docs/reference/frontend-routes-and-data.md`,
`docs/reference/design-system.md`, `docs/how-to/extend-the-frontend.md`.
