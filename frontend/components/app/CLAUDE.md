# frontend/components/app - agent instructions

## Rules

- The user menu is the app's navigation. A new top-level page gets a `DropdownMenuItem` here with
  `asChild` wrapping a `next/link` `Link`. Gate admin-only items on `user?.is_superuser`.
- Treat a name that is empty or only whitespace as missing: every reader of `user.name` uses
  `user?.name?.trim() || <fallback>`. Keep all three readers (`initialsFrom`, `displayName`, the menu
  label) in agreement.
- Sign out must always finish locally: ignore the logout request's error, then `queryClient.clear()`,
  then navigate to `/login`. A cleared cache is what stops the next account seeing the last one's data.
- `AppBar` stays a server-compatible component; only `UserMenu` is `"use client"`.
- The display name is inside a `hidden sm:block` span, so its accessible name changes with the viewport.
  Playwright finds the trigger structurally (`frontend/e2e/support.ts` `userMenuTrigger()`); keep the
  app bar's user menu as the only button inside the `banner` landmark, or update that helper.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run components/app
pnpm typecheck
```

If you change menu items, also run the navigation spec against the running stack:

```bash
pnpm exec playwright test e2e/navigation.spec.ts
```

## Docs

- `docs/reference/frontend-routes-and-data.md` (Navigation table)
- `docs/how-to/extend-the-frontend.md`
