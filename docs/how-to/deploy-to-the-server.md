# How to deploy to the server

Use this to put a new commit of `main` on the in-house server. The server runs the same
`docker-compose.yml` as a developer machine, from a plain git checkout; only its `.env` differs.
The images contain the code, so pulling changes nothing until the images are rebuilt and the
containers replaced.

`<SERVER_HOST>` and `<SERVER_USER>` below are placeholders. Take the real values from the team's
private notes; never write them into the repository.

## Prerequisites

- SSH access to `<SERVER_HOST>` as `<SERVER_USER>`, and that account in the `docker` group, so no
  command below needs `sudo`. A server that has never run the app needs the one-time setup in
  `deploy/server-bootstrap.sh` first; its header explains how to run it.
- The checkout at `/home/<SERVER_USER>/mrr` with a filled-in `.env` and, for Vertex, the
  service-account key in `secrets/`. The settings themselves are described in
  [Configuration](../reference/configuration.md).
- The commit you are deploying has passed CI on `main`.
- Enough free disk space for a database dump and an archive of every uploaded record.

## Steps

### 1. Connect and go to the checkout

```bash
ssh <SERVER_USER>@<SERVER_HOST>
cd /home/<SERVER_USER>/mrr
```

### 2. Make sure no job is running

Replacing a worker container ends whatever job it is running. Check the job table first:

```bash
docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, kind, state, stage FROM jobs WHERE state IN ('queued', 'running', 'paused');"
```

Deploy when no row is `running`, or tell the reviewers that those jobs will need to be started
again. Queued jobs and the scheduled resume of a paused job are held in Redis, which the deploy does
not restart, so the new workers pick them up.
[Job and document states](../reference/job-and-document-states.md) explains the states, and
[How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md) covers a job
that still shows as running after its worker was replaced.

### 3. Back up the database, the uploads and the current commit

```bash
BK=/home/<SERVER_USER>/mrr-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BK"
docker compose exec -T postgres pg_dump -U mrr -Fc mrr > "$BK/mrr.dump"
docker compose exec -T api tar czf - -C /app/uploads . > "$BK/uploads.tgz"
git rev-parse HEAD > "$BK/git-sha.txt"
test -s "$BK/mrr.dump" && test -s "$BK/uploads.tgz" && ls -la "$BK"
```

The last line prints the three files only if the dump and the archive are both non-empty. Stop
here if it prints nothing. The database and the uploaded PDFs belong together: every document row
stores the path of its PDF inside the uploads volume. [How to back up and restore](../how-to/back-up-and-restore.md)
has the details and the restore commands. Both files contain patient data.

### 4. Pull

```bash
git pull --ff-only
```

### 5. Build every image

```bash
GIT_SHA=$(git rev-parse --short HEAD) docker compose build api segment-worker summarize-worker web docs
```

The running containers keep serving while this builds. `GIT_SHA` is read only by the three backend
builds; it becomes the `build_sha` stamped on every job row, so a stored summary can be traced to
the code that produced it. Without it the stamp is `unknown`.

Build all three backend services. `api` and `summarize-worker` share the image `mrr-backend-web`;
`segment-worker` has its own image, `mrr-backend-classifier`. A build that skips it leaves the
segment workers on the old code.

### 6. Migrate the database with the new image

```bash
docker compose run --rm api alembic upgrade head
```

This runs the migrations in a one-off container from the image you just built, while the old
containers are still up.

### 7. Replace the containers

```bash
docker compose up -d --force-recreate api segment-worker summarize-worker web docs proxy
docker compose up -d
```

The first command replaces every container that runs code from this repository, plus the proxy, so
a change to `deploy/nginx.conf` (a mounted file, not part of any image) takes effect. The second
starts anything new in `docker-compose.yml` and applies any change there to the other services.
Postgres and Redis are not recreated unless their definition changed; recreating Redis empties
the job queue.

## Why this order

- Jobs are checked first because step 7 ends any job in progress.
- The backup comes before the pull and the migration because a migration only moves forward. The
  dump is what a rollback restores.
- Building before replacing keeps the old containers serving during the long build, and makes
  sure the migration in step 6 runs the new code's migrations.
- Migrating before replacing means the new code never starts against the old schema. The API
  queries the jobs table as soon as it starts (its recovery of orphaned jobs), and the workers take
  jobs as soon as they start; code that expects a column the database does not have yet fails. In
  the other direction, the old containers run for a short time against the new schema,
  which is harmless as long as the migration only adds. A migration that drops or renames
  something the old code reads needs its own plan; see
  [How to create a database migration](../how-to/create-a-database-migration.md).
- `--force-recreate` makes Compose replace each container from the newly built image rather than
  leaving a running container in place.

## Verify it worked

```bash
docker compose ps
docker compose exec -T api python -c "from app.config import get_settings; print(get_settings().build_sha)"
docker compose exec -T segment-worker python -c "from app.config import get_settings; print(get_settings().build_sha)"
docker compose exec -T postgres psql -U mrr -d mrr -t -c 'SELECT version_num FROM alembic_version;'
docker compose run --rm api alembic heads
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/login
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/users/me
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/docs/
docker compose logs api --since 2m | grep -icE 'error|traceback'
docker compose logs segment-worker --since 2m | grep 'listening round-robin'
```

Expected:

| Check | Expected |
| --- | --- |
| `docker compose ps` | Every service running, three `segment-worker` and three `summarize-worker` containers. |
| Both `build_sha` lines | The short hash `git rev-parse --short HEAD` prints. A segment worker showing an older hash means its image was not rebuilt. |
| `alembic_version` and `alembic heads` | The same revision. |
| `/login` | `200` (the frontend through the proxy). |
| `/api/users/me` | `401` (the API through the proxy, asking for a session). |
| `/docs/` | `200` (the documentation site). |
| Error count | `0`, or only lines you can explain. |
| Worker log | One `worker listening round-robin on N queue(s)` line per segment worker. |

Then sign in as a reviewer would and open a record.

## Roll back

Choose one of two rollbacks. `$BK` is the backup folder from step 3; set it again if you are in a
new shell.

**Code only** keeps everything reviewers did since the deploy. Use it when the migrations the
deploy applied only added columns or tables, so the old code still runs on the new schema:

```bash
git checkout "$(cat "$BK/git-sha.txt")"
GIT_SHA=$(git rev-parse --short HEAD) docker compose build api segment-worker summarize-worker web docs
docker compose up -d --force-recreate api segment-worker summarize-worker web docs proxy
```

**Code and data** returns the database to the backup. Everything written after the backup
(uploads, review edits, summaries, accounts) is lost from the database:

```bash
git checkout "$(cat "$BK/git-sha.txt")"
GIT_SHA=$(git rev-parse --short HEAD) docker compose build api segment-worker summarize-worker web docs
docker compose stop api segment-worker summarize-worker
docker compose exec -T postgres pg_restore -U mrr -d mrr --clean --if-exists < "$BK/mrr.dump"
docker compose up -d --force-recreate api segment-worker summarize-worker web docs proxy
```

Restore the uploads archive as well only if files in the volume were lost or damaged; see
[How to back up and restore](../how-to/back-up-and-restore.md). The deploy itself does not touch
the uploaded files.

If the commit you return to has no `docs` service in its `docker-compose.yml`, leave `docs` out of
the build and recreate commands, and run `docker compose up -d --remove-orphans` afterwards to stop
the container the newer code started.

After a code-only rollback the database is still at the newer revision, which the older code's
migration folder does not contain, so do not run `alembic` until you are back on the newer code.

Either way the checkout is now on a detached commit. When the fix is on `main`, return with
`git checkout main` and deploy again from step 2.

## If a step fails

| Step | Failure | What to do |
| --- | --- | --- |
| 3 | The `test -s` line prints nothing | Do not continue. Find out why the dump or the archive is empty. |
| 4 | `git pull --ff-only` refuses | The checkout has local commits or edits. Inspect with `git status`; the server should carry none. |
| 5 | A build fails | Nothing has changed yet; the old containers are still serving. Fix the cause or stop here. |
| 6 | A migration fails | The old containers are still serving. `backend/alembic/env.py` runs the whole upgrade inside one transaction, so on Postgres a failure rolls back every migration in that run and `alembic_version` stays at the old revision. Fix forward, or roll back the code. |
| 7 | A container keeps restarting | `docker compose logs <service>`. A worker exits at startup when a configured model backend fails its startup check. |
| Verify | A `build_sha` is old | Rebuild that service's image and repeat step 7. |

## Things to know

- Never run `docker-compose.dev.yml` on the server. It sets no project name, so in a checkout
  folder named `mrr` Compose treats it as the app stack's project and would replace the app's
  `postgres` container with the test configuration.
- `ENVIRONMENT=prod` marks the session cookie Secure, so browsers keep it only over HTTPS, and it
  makes the API refuse to start unless Vertex is enabled. `deploy/nginx.conf` listens on plain HTTP
  port 80, published as 8080. See [Auth and access](../explanation/auth-and-access.md).
- Workers learn the list of users when they start. After creating an account on the server,
  restart them: `docker compose restart segment-worker summarize-worker`.
- A change to `.env` needs no rebuild: recreate the API and both workers together.

## Related pages

- [How to back up and restore](../how-to/back-up-and-restore.md)
- [Compose services](../reference/compose-services.md)
- [CI and merge gates](../reference/ci-and-merge-gates.md)
- [How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md)
- [How to run the app locally](../how-to/run-the-app-locally.md)
