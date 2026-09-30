# How to manage users and admins

## When you need it

- Someone needs the admin console (categories, prompts, reprocess), or should lose it.
- A user has forgotten their password. The "Forgot password" form sends no email, so an operator
  has to set a new one.
- A user leaves and their account must stop working.
- A new user has registered and their jobs do not start.

Accounts are created by the person, with **Create an account** on the sign-in page
(`POST /api/auth/register`). The web app has no screen for managing other users; the tasks below
use the admin CLI (`backend/app/cli.py`), `psql` in the `postgres` container, and the admin-only
`/api/users/{id}` routes. Why the pieces behave as they do:
[Authentication and access](../explanation/auth-and-access.md).

## Prerequisites

- A shell on the server, in the repository checkout that holds `docker-compose.yml`, with the stack
  running (`docker compose ps` lists `api`, `postgres` and the workers as running).
- For the password reset: `curl`, an admin account and its password, and the address reviewers use
  to reach the app. Below it is `<APP_URL>`, for example `http://<SERVER_HOST>:8080`. When the
  server runs with `ENVIRONMENT=prod` the session cookie is marked `Secure`, so `<APP_URL>` must
  start with `https://`.
- For local development the same CLI runs from `backend/` as `uv run python -m app.cli ...`, with
  `DATABASE_URL`, `SECRET_KEY` and `SECURITY_PASSWORD_SALT` set in `backend/.env` (see
  [How to run the app locally](run-the-app-locally.md)).

## Steps

### List, grant or revoke admin

1. List the current admins:

    ```bash
    docker compose exec api python -m app.cli admin list
    ```

    Expected: one email per line, or `No admin accounts.`

2. Grant admin to an existing account. The email must match the stored address exactly, including
   letter case; copy it from the user list in the next section if unsure.

    ```bash
    docker compose exec api python -m app.cli admin grant <USER_EMAIL>
    ```

    Expected: `Granted admin to <USER_EMAIL>`. The user sees **Admin** in the avatar menu after
    reloading the page; no new sign-in is needed.

3. Revoke admin:

    ```bash
    docker compose exec api python -m app.cli admin revoke <USER_EMAIL>
    ```

    Expected: `Revoked admin from <USER_EMAIL>`. It applies to the user's next request.

### Find a user's id and status

1. List every account with its id, active flag, admin flag and number of documents:

    ```bash
    docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT u.id, u.email, u.name, u.active, u.is_admin, count(d.id) AS documents FROM \"user\" u LEFT JOIN documents d ON d.user_id = u.id GROUP BY u.id ORDER BY u.id;"
    ```

    The table is named `user`, a reserved word in Postgres, so it must stay in double quotes.

### Reset a user's password

`POST /api/auth/forgot-password` only logs the user id (`on_after_forgot_password` in
`backend/app/auth/users.py`); no link is sent. An admin sets the new password through
`PATCH /api/users/{id}`, which applies the same password rule as registration (8+ characters, a
digit, a symbol) and hashes it with the app's scheme.

1. Find the user's id (previous section).

2. Sign in as an admin and keep the session cookie in a file. `read -s` keeps the password out of
   the terminal and the shell history.

    ```bash
    APP_URL="<APP_URL>"
    read -rp "Admin email: " ADMIN_EMAIL
    read -rsp "Admin password: " ADMIN_PASSWORD; echo
    curl -sS -o /dev/null -w "%{http_code}\n" -c mrr-admin-cookie.txt --data-urlencode "username=$ADMIN_EMAIL" --data-urlencode "password=$ADMIN_PASSWORD" "$APP_URL/api/auth/login"
    ```

    Expected: `204`. A `400` means wrong credentials or an inactive admin account.

3. Set the new password. It is sent inside a JSON string, so it must not contain `"` or `\`.

    ```bash
    read -rsp "New password for the user: " NEW_PASSWORD; echo
    curl -sS -b mrr-admin-cookie.txt -X PATCH -H "Content-Type: application/json" --data "{\"password\": \"$NEW_PASSWORD\"}" "$APP_URL/api/users/<USER_ID>"
    ```

    Expected: the user as JSON, for example
    `{"id":<USER_ID>,"email":"...","is_active":true,"is_superuser":false,"is_verified":false,"name":"..."}`.

4. Sign out and remove the cookie file and the variables:

    ```bash
    curl -sS -o /dev/null -w "%{http_code}\n" -b mrr-admin-cookie.txt -X POST "$APP_URL/api/auth/logout"
    rm -f mrr-admin-cookie.txt
    unset ADMIN_EMAIL ADMIN_PASSWORD NEW_PASSWORD
    ```

    Expected: `204`.

5. Give the user the new password over a channel you trust. The web app has no screen for changing
   one's own password.

### Deactivate or reactivate a user

Deactivating keeps the account and its records but stops it working: sign-in is refused with the
same answer as a wrong password, and any open session gets 401 on its next request.

1. Deactivate:

    ```bash
    docker compose exec -T postgres psql -U mrr -d mrr -c "UPDATE \"user\" SET active = false WHERE lower(email) = lower('<USER_EMAIL>');"
    ```

    Expected: `UPDATE 1`. `UPDATE 0` means no account has that email.

2. Reactivate, when needed:

    ```bash
    docker compose exec -T postgres psql -U mrr -d mrr -c "UPDATE \"user\" SET active = true WHERE lower(email) = lower('<USER_EMAIL>');"
    ```

An admin can make the same change through the API: step 2 of the password reset, then
`PATCH /api/users/<USER_ID>` with the body `{"is_active": false}`.

Deactivate rather than delete. `documents.user_id` and `audit_log.user_id` reference the user with
no `ON DELETE` rule (`backend/app/models.py`, baseline migration `73abdcd5ef01`), so the database
refuses to delete a user row while that user owns a document or has an audit entry - which is every
user who has uploaded anything.

### After a new user registers

The workers build one queue lane per existing user when they start (`backend/app/worker/__main__.py`
module docstring), so a user created after the workers started has jobs that no worker picks up.

1. Check that no job is queued, running or paused, because a restart stops the jobs the workers are
   running:

    ```bash
    docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, document_id, kind, state FROM jobs WHERE state IN ('queued', 'running', 'paused');"
    ```

    Expected: `(0 rows)`. Otherwise wait for those jobs to finish.

2. Restart the workers:

    ```bash
    docker compose restart segment-worker summarize-worker
    ```

3. Confirm the new lane is listed:

    ```bash
    docker compose logs --tail 50 segment-worker summarize-worker | grep "listening round-robin"
    ```

    Expected: each worker's line names `segment:<USER_ID>` or `summarize:<USER_ID>` for the new
    user's id.

## What happens to a deactivated user's documents

- Nothing is deleted. The rows stay in `documents` (and their rows, summaries and jobs), and the
  PDFs stay under `<UPLOAD_FOLDER>/<user_id>/` on the `mrr_uploads` volume.
- Other reviewers cannot open them: every document route answers 404 to anyone but the owner or
  an admin (`get_owned_document` in `backend/app/api/deps.py`).
- An admin can open and fix them. The records page's "Show records for" list offers active
  accounts only (`GET /api/admin/users`), so reach a deactivated user's records through
  `GET /api/documents?owner=<USER_ID>` or by the record's id. Deleting one stays with its owner,
  admins included. Nothing in the app transfers a record to another user.
- Jobs that were already queued still run.
- An admin can also start a summarize run on one of them by id
  (`POST /api/admin/reprocess/{document_id}`).
- Reactivating the account restores the user's access to all of them.
- To remove the records, the owner deletes each one from the documents list while the account is
  active (`DELETE /api/documents/{document_id}` removes the database rows and the stored PDF).

## Verify it worked

- Admin changes: `docker compose exec api python -m app.cli admin list` shows the expected emails.
- Password reset: the user signs in with the new password.
- Deactivation: the user list query shows `active` as `f`, and signing in as that user shows
  "We couldn't sign you in".
- New user: their next job leaves `queued` within seconds.

## If it fails, or to undo

| Symptom | Cause | What to do |
| --- | --- | --- |
| `No user with email <USER_EMAIL>` from the CLI, exit code 1 | No account with exactly that email (the CLI compares case-sensitively). | Copy the email from the user list, or ask the person to register first. |
| `400` with `UPDATE_USER_INVALID_PASSWORD` | The new password breaks the rule; `reason` says which part. | Choose a password with 8+ characters, a digit and a symbol. |
| `403 {"detail":"Forbidden"}` on `PATCH /api/users/...` | The signed-in account is not an admin. | Grant admin with the CLI first. |
| `401 {"detail":"Not authenticated"}` on `PATCH /api/users/...` | The cookie was not sent: sign-in failed, or `<APP_URL>` is `http://` while the server runs with `ENVIRONMENT=prod`. | Repeat step 2 against the `https://` address. |
| `404 {"detail":"Not Found"}` on `PATCH /api/users/...` | No user has that id. | Re-run the user list. |
| Wrong person made admin | | `admin revoke` with their email. |
| Wrong person deactivated | | Run the reactivate command. |

## Related pages

- [Authentication and access](../explanation/auth-and-access.md)
- [HTTP API reference](../reference/http-api.md)
- [Errors and messages reference](../reference/errors-and-messages.md)
- [How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md)
- [How to deploy to the server](deploy-to-the-server.md)
