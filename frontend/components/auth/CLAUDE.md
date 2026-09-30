# frontend/components/auth - agent instructions

## Rules

- These forms write their OWN error copy, because status codes mean something different here: a 401 on
  sign-in is a wrong password (not "your session has ended"), the register form reads a 400 as an
  address already taken (the client checks the password rules first), and a 400 on reset is a spent
  or forged link. Delegate to `humanizeError()` only for a transport failure,
  tested with `isOffline(err)`.
- Forgot password must not reveal whether an account exists: show the same "Check your email" result
  for every server answer. Only a request that never reached the server may show an error.
- `passwordRules` mirrors `validate_password()` in `backend/app/auth/users.py`. Change both together.
- Login is form-encoded (`username` = the email), not JSON. Keep `login()` in `frontend/lib/auth-api.ts`
  as is; FastAPI-Users expects the OAuth2 password form.
- Register does not create a session. A successful register must be followed by a login before
  navigating to `/`.
- `LoginView` reads `useSearchParams`, so `app/login/page.tsx` wraps it in `<Suspense>`. Keep it.
- A 401 from `apiFetch()` on a `/login` path must not redirect (it would loop). `signedOut()` and the
  query-cache handler both check `pathname.startsWith("/login")`.
- Never put a real credential or address in a placeholder, fixture or test. Tests use synthetic values.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run components/auth hooks/use-auth lib/auth-api
pnpm typecheck
```

End to end, with the stack running on port 8080:

```bash
pnpm exec playwright test e2e/auth.spec.ts
```

## Docs

- `docs/explanation/auth-and-access.md`
- `docs/reference/frontend-routes-and-data.md` (Login views, client error handling)
