# frontend/components/app

The chrome shared by every signed-in page: the navy app bar with the brand on the left and the user
menu on the right, and the "My documents" back link used by sub-pages.

| File | What it is |
| --- | --- |
| `app-bar.tsx` | `AppBar`: the `.ev-topbar` header with `Brand` (linked home), an optional `action` slot, and `UserMenu` |
| `brand.tsx` | `Brand`: the crest (`/evaluators-crest.png` from `frontend/public/`), the EVALUATORS wordmark, a divider and the "Medical Record Review" label; `homeLink` makes the crest and wordmark a link to `/` |
| `back-link.tsx` | `BackLink`: a link styled `.ev-backlink`, defaulting to `/` and "My documents" |
| `user-menu.tsx` | `UserMenu`: initials avatar and name, links to Diagnostic & Operative, Depositions and (for `is_superuser`) Admin, and Sign out |
| `app-bar.test.tsx` | Brand link, action slot, user menu present |
| `user-menu.test.tsx` | Name and initials fallbacks, the Admin item gate, signing out even when the logout request fails |

`UserMenu` reads the signed-in user from `useCurrentUser()` (`GET /api/users/me`). Sign out calls
`POST /api/auth/logout`, ignores its errors, clears the query cache and goes to `/login`.

## Use, run and test

```bash
cd frontend
pnpm exec vitest run components/app
```

The auth pages render `Brand` without the home link inside their own bar (`components/auth/auth-shell.tsx`).

## Documentation

- [Frontend routes and data reference](../../../docs/reference/frontend-routes-and-data.md)
- [Design system reference](../../../docs/reference/design-system.md)
- [How to extend the frontend](../../../docs/how-to/extend-the-frontend.md)
