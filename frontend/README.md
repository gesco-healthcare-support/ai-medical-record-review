# MRR AI frontend (Next.js)

Next.js (App Router) + TypeScript. Part of the re-platform
(`docs/plans/2026-07-14-nextjs-fastapi-rewrite.md`); talks to the FastAPI backend same-origin.

## Dev

```bash
corepack enable            # provides pnpm (pinned in package.json)
cd frontend
pnpm install
pnpm dev                   # http://localhost:3000
```

`next.config.ts` proxies `/api/*` to the FastAPI backend (`API_ORIGIN`, default
`http://localhost:8000`) so the browser sees one origin - the HttpOnly session cookie works and
there is no CORS. In production a reverse proxy fronts both under one host.

## Scripts
- `pnpm dev` / `pnpm build` / `pnpm start`
- `pnpm typecheck` - `tsc --noEmit`

## Status
P1c scaffold: App Router shell + the Evaluators design tokens (`app/globals.css`) + a
same-origin API client (`lib/api.ts`). The real pages/components (My Documents, review editor,
bundles, admin) are built in P7 against the stable API.
