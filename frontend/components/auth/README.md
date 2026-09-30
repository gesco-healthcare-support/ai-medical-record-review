# frontend/components/auth

The sign-in flow rendered by `/login`: one card that switches between sign in, create an account,
request a reset link, and set a new password. It talks to the FastAPI-Users routes under `/api/auth`
through `frontend/hooks/use-auth.ts`.

| File | What it is |
| --- | --- |
| `login-view.tsx` | `LoginView`: picks the first view from `?view=register`, `?view=forgot`, `?view=reset` or `?token=`, and sends an already signed-in user to `/` |
| `auth-shell.tsx` | `AuthShell`: the brand-only top bar and the centred card with crest, eyebrow, heading and subtitle |
| `auth-error.tsx` | `AuthError`: the in-card error band (`role="alert"`) |
| `sign-in-form.tsx` | `SignInForm`: email and password, then `/`. "Remember me" is presentational |
| `register-form.tsx` | `RegisterForm`: name, email, password and confirmation, checked against the password rules, then register and sign in (the backend does not start a session on register) |
| `forgot-form.tsx` | `ForgotForm`: asks for an email and always shows "Check your email" unless the request never reached the server |
| `reset-form.tsx` | `ResetForm`: sets a new password from the link's token; "Link expired" when there is no token |
| `password-checklist.tsx` | `passwordRules`, `passwordValid()`, `PasswordChecklist`: 8 or more characters, a number, a symbol, mirroring the backend's `validate_password()` |
| `auth-forms.test.tsx` | Each form on a dropped connection versus a real server answer |
| `login-view.test.tsx` | Which view the address selects, the signed-in redirect, moving between views |

## Use, run and test

```bash
cd frontend
pnpm exec vitest run components/auth
```

The end-to-end spec `frontend/e2e/auth.spec.ts` registers, signs out and signs back in against the
running stack.

## Documentation

- [Authentication and access](../../../docs/explanation/auth-and-access.md)
- [How to manage users and admins](../../../docs/how-to/manage-users-and-admins.md)
- [Frontend routes and data reference](../../../docs/reference/frontend-routes-and-data.md)
