# frontend/app - agent instructions

Routes, root layout, providers and the global CSS. Read `README.md` here for the file map.

## Rules

- Keep pages thin: render `<AppBar />`, an optional `<div className="ev-page-back"><BackLink /></div>`,
  and ONE client component. No data fetching, no `"use client"` in a page, no Next API routes, no
  `middleware.ts`. Data lives in `frontend/hooks/` and `frontend/lib/`.
- Never add a route under `/api/` or `/docs/`. The nginx proxy (`deploy/nginx.conf`) sends those to the
  API and the docs site; Next never sees them.
- Dynamic segments: `params` is a Promise (Next 15). `await` it, as `records/[id]/page.tsx` does.
- A component that reads `useSearchParams` must sit inside `<Suspense>` in its page. Only `next build`
  enforces this; the unit tests cannot.
- Every new route goes in `route-pages.test.tsx` AND in the Routes table of
  `docs/reference/frontend-routes-and-data.md`, path written literally (a drift test checks it).
- Access control is the backend's job. The frontend reacts to 401 (redirect) and 403 (message). An
  admin-only page still shows a notice to non-admins (see `AdminView`); gate on `is_superuser`.

## Providers (`providers.tsx`)

- The query defaults apply to every screen. Changing `staleTime`, `retry` or `refetchOnWindowFocus`
  changes when summaries, documents and duplicates refresh; the workbench relies on explicit
  invalidation, not on focus refetch.
- The 401 redirect skips any path starting with `/login`; keep that guard or sign-in loops.
- Retry stays off for 401, 403 and 404. A 404 is the backend's answer for another owner's record.

## CSS

- `layout.tsx` imports `globals.css` BEFORE `evaluators-ds.css`. Keep the order.
- `evaluators-ds.css` is not in a cascade layer; Tailwind puts its theme variables in `@layer theme`.
  Where both files define a variable (`--font-heading`, `--radius-*`, `--color-border`), the design
  system's value is the one `var()` reads.
- New design-system classes go in `evaluators-ds.css` under the matching section, with the family
  prefix. `globals.css` holds only the Tailwind and shadcn bridge and a few helpers.
- Use tokens (`var(--navy-600)`), never raw hex, outside the token block.
- ASCII only in new code and comments. Existing files contain a middle dot and dashes; do not add more.

## Tests

- Vitest collects `app/**` only because `app` is in `include` in `frontend/vitest.config.mts`. It is
  also in the coverage `include`. Keep the two lists in step; measuring a folder the runner does not
  collect fails silently.
- `route-pages.test.tsx` stubs every child. Compare bundle configs by identity (`toBe`), not by label:
  the two bundle pages are the same component.

## Commands

Run from `frontend/`:

```bash
pnpm typecheck
pnpm exec vitest run app
pnpm test
pnpm build
```

## Docs

- `docs/reference/frontend-routes-and-data.md` (routes, calls, keys)
- `docs/reference/design-system.md` (tokens, classes, breakpoints)
- `docs/how-to/extend-the-frontend.md` (adding a route)
