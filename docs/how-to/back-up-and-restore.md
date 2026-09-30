# How to back up and restore

Use this before a deploy, before any manual change to the database, before moving the app to
another machine, and whenever you need to put the data back. The app keeps its state in two
places, and a usable backup needs both from the same moment:

| What | Where it lives | Holds |
| --- | --- | --- |
| The database | Named volume `mrr_pgdata` of the `postgres` service | Accounts and sessions, documents, jobs, rows, summaries, page text, categories, prompts and the audit log |
| The uploaded files | Named volume `mrr_uploads`, mounted at `/app/uploads` in the API and both workers | Each uploaded record as `<user id>/<document id>.pdf`, plus prepared exports that are deleted a few minutes after they are made |

Every document row stores the full path of its PDF inside `/app/uploads`, so a database restored
without its files, or files without their rows, leaves records that cannot be opened or files
nothing refers to.

Every command runs from the checkout folder (on the server, `/home/<SERVER_USER>/mrr`). Both backup
files contain patient data: keep them on the server's own disk with the same access controls as
the app, and never copy them into the repository.

## Prerequisites

- The app stack running (`docker compose ps` shows `postgres` and `api` up).
- Free disk space for the dump and for an archive about the size of all uploaded records.
- On Windows, in Git Bash: prefix every command that contains `/app/uploads` with
  `MSYS_NO_PATHCONV=1`, or Git Bash rewrites the path before Docker sees it.

## Back up

1. Check that no job is running, so the database and the files describe the same moment:

    ```bash
    docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, kind, state FROM jobs WHERE state IN ('queued', 'running', 'paused');"
    ```

2. Make a backup folder and write the database dump, the uploads archive and the current commit
   into it:

    ```bash
    BK="$HOME/mrr-backup-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BK"
    docker compose exec -T postgres pg_dump -U mrr -Fc mrr > "$BK/mrr.dump"
    docker compose exec -T api tar czf - -C /app/uploads . > "$BK/uploads.tgz"
    git rev-parse HEAD > "$BK/git-sha.txt"
    ```

    `pg_dump -Fc` writes Postgres's custom format, which `pg_restore` reads. The `tar` runs inside
    the API container because that is where the volume is mounted.

3. Confirm neither file is empty:

    ```bash
    test -s "$BK/mrr.dump" && test -s "$BK/uploads.tgz" && ls -la "$BK"
    ```

## Verify the backup

List what the dump contains and count the PDFs in the archive, without restoring anything:

```bash
docker compose exec -T postgres pg_restore --list < "$BK/mrr.dump" | grep -c 'TABLE DATA'
tar tzf "$BK/uploads.tgz" | grep -c '\.pdf$'
docker compose exec -T postgres psql -U mrr -d mrr -t -c 'SELECT count(*) FROM documents;'
```

The first number is the count of tables with data in the dump. The PDF count and the document
count should match; prepared exports are named by their download token and carry no `.pdf`
extension, so they are not counted.

## Restore

Restoring replaces the current data. Take a backup of the current state first if there is any
chance you will want it.

1. Stop everything that writes to the database or the uploads, leaving Postgres running:

    ```bash
    docker compose stop api segment-worker summarize-worker
    ```

2. Restore the database:

    ```bash
    docker compose exec -T postgres pg_restore -U mrr -d mrr --clean --if-exists < "$BK/mrr.dump"
    ```

    `--clean --if-exists` drops each object in the dump before recreating it, so the restore works
    over an existing database. Tables that exist now but are not in the dump are left alone.

3. Restore the uploaded files. The API is stopped, so extract through a one-off container of the
   same image, which mounts the same volume:

    ```bash
    docker compose run --rm -T api tar xzf - -C /app/uploads < "$BK/uploads.tgz"
    ```

    Extraction adds and overwrites files. It does not delete files uploaded after the backup was
    taken; after a database restore those files have no row pointing at them.

4. Start the stopped services again:

    ```bash
    docker compose up -d --force-recreate api segment-worker summarize-worker
    ```

### Restore onto a new machine

1. Clone the repository and check out the commit in `git-sha.txt`.
2. Put the `.env` and the `secrets/` folder in place (they are not in either backup file; see below).
3. Build the images, then start Postgres and Redis alone:

    ```bash
    GIT_SHA=$(git rev-parse --short HEAD) docker compose build
    docker compose up -d --wait postgres redis
    ```

4. Restore the database into the empty `mrr` database the container created. The dump carries the
   schema and the `alembic_version` row, so no migration is needed first:

    ```bash
    docker compose exec -T postgres pg_restore -U mrr -d mrr < "$BK/mrr.dump"
    ```

5. Restore the files and start the rest:

    ```bash
    docker compose run --rm -T api tar xzf - -C /app/uploads < "$BK/uploads.tgz"
    docker compose up -d
    ```

`POSTGRES_PASSWORD` is applied only when the Postgres volume is first created, and the connection
string the app uses is built from the same variable. Set it in `.env` before step 3 and do not
change it afterwards.

## Verify the restore

```bash
docker compose exec -T postgres psql -U mrr -d mrr -t -c 'SELECT version_num FROM alembic_version;'
docker compose exec -T postgres psql -U mrr -d mrr -t -c 'SELECT count(*) FROM documents;'
docker compose exec -T api sh -c 'find /app/uploads -mindepth 2 -maxdepth 2 -name "*.pdf" | wc -l'
```

The document count and the PDF count should match. Then sign in and open a record: its PDF should
display.

## What is not backed up

| Not covered | Why it matters | What to do |
| --- | --- | --- |
| Redis | Redis runs with persistence switched off (`--save ""` and `--appendonly no`), so nothing it holds is ever written to disk, and nothing can be backed up. | Nothing to back up. See the next section for what a Redis restart loses. |
| `.env` and `secrets/` | They live in the checkout folder, not in a volume. Without the same `SECURITY_PASSWORD_SALT` no stored password verifies; without `SECRET_KEY` outstanding reset tokens stop working; without the Vertex key no AI job runs. | Keep them wherever the team keeps secrets. |
| The images | They are built from the repository. | Rebuild from the commit in `git-sha.txt`. |

Persistence is off on purpose. The job queue carries only identifiers, but a prepared export's
entry holds its file name, which contains the patient's name, for the few minutes the download
link lives. Keeping Redis in memory only means that name never reaches a disk. Do not turn on RDB
snapshots or the append-only file.

### What a Redis restart loses

When the `redis` container restarts or is recreated, everything in it is gone:

- jobs waiting in a queue, and paused summarize jobs waiting for their scheduled resume;
- prepared export downloads (the link stops working; export again);
- pacing state and model-call counters, which start again from zero;
- pending stop requests for running jobs.

The job rows in Postgres are not touched, so a lost job still shows as queued or paused. The API
reconciles them when it starts: a job whose queue entry no longer exists is marked interrupted,
and the reviewer can start it again. So after Redis restarts, and when no job is running, restart
the API and the workers:

```bash
docker compose restart api segment-worker summarize-worker
```

## If it fails

| Symptom | Cause | Fix |
| --- | --- | --- |
| The dump is empty | Postgres was not running or the command failed | `docker compose up -d --wait postgres`, then repeat. |
| `tar: /app/uploads: Cannot open` in Git Bash | Git Bash rewrote the path | Prefix the command with `MSYS_NO_PATHCONV=1`. |
| `pg_restore` reports errors about existing objects | Restoring over data without `--clean --if-exists` | Use the command in Restore step 2. |
| `password authentication failed` after a restore on a new machine | `POSTGRES_PASSWORD` differs from the value the volume was created with | Put back the original value, or recreate the empty volume with the value you want before restoring. |
| A record opens with no PDF | The files were not restored, or were restored from a different moment than the database | Restore both from the same backup folder. |

## Related pages

- [How to deploy to the server](../how-to/deploy-to-the-server.md)
- [Compose services](../reference/compose-services.md)
- [Data model](../reference/data-model.md)
- [Exports and downloads](../explanation/exports-and-downloads.md)
- [Pipeline and jobs](../explanation/pipeline-and-jobs.md)
