# frontend/app

The Next.js App Router tree: the root layout, the client providers, one `page.tsx` per route, and the
two global stylesheets. Pages are thin server components that render the app bar and one client
component from `frontend/components/`; nothing in this folder fetches data.

| Path | What it is |
| --- | --- |
| `layout.tsx` | Root layout: Inter and Poppins through `next/font/google`, imports `globals.css` then `evaluators-ds.css`, wraps every page in `Providers`, places the `Toaster` at the bottom centre, sets the title "MRR AI" |
| `providers.tsx` | `Providers`: the TanStack Query client (30 s stale time, no refetch on focus, one retry except for 401, 403 and 404, a 401 sends the browser to `/login`) and the Radix tooltip provider |
| `page.tsx` | `/`: My documents (`DocumentsView`) |
| `login/page.tsx` | `/login`: `LoginView` inside `Suspense` |
| `records/[id]/page.tsx` | `/records/[id]`: the record workbench (`ReviewPageClient`) |
| `diagnostics/page.tsx` | `/diagnostics`: the Diagnostic & Operative bundle builder |
| `depositions/page.tsx` | `/depositions`: the Depositions bundle builder |
| `admin/page.tsx` | `/admin`: categories, summary prompts, reprocess a record |
| `globals.css` | Tailwind v4 imports, the token bridge for Tailwind utilities and shadcn components, helper classes, the PDF pane styles |
| `evaluators-ds.css` | The Evaluators design system: every token and every `ev-`, `auth-`, `hd-`, `rc-`, `rce-`, `sum-`, `bnd-` class |
| `layout.test.tsx` | The toast host, its placement and styling, and the font variables on `<html>` |
| `providers.test.tsx` | The 401 redirect, including no redirect on `/login` itself |
| `route-pages.test.tsx` | Each route page renders the right components with the right props (children stubbed) |

## Use, run and test

From `frontend/`, `pnpm dev` serves the app on port 3000 and rewrites `/api/*` to `API_ORIGIN`
(default `http://127.0.0.1:8000`, see `next.config.ts`). In the compose stack the nginx proxy on port
8080 sends `/` here and `/api/` to the API.

```bash
cd frontend
pnpm exec vitest run app
pnpm build
```

`pnpm build` is the check for the `Suspense` rule on `/login`; the tests cannot see it.

## Documentation

- [Frontend routes and data reference](../../docs/reference/frontend-routes-and-data.md)
- [Design system reference](../../docs/reference/design-system.md)
- [The record workbench](../../docs/explanation/frontend-workbench.md)
- [How to extend the frontend](../../docs/how-to/extend-the-frontend.md)
