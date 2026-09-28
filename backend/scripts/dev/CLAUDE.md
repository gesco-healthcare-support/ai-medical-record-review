# backend/scripts/dev - agent instructions

Hand-run developer proofs that exercise a fix on real input inside a container. Details:
`docs/reference/scripts.md`.

## Never without the user's explicit go

- Running either script. Both read a real record (PHI).
- `verify_deposition_format.py` sends a real deposition to the configured model (stages
  `deposition` and `summarize`) and costs model calls.
- Blanking `OMP_THREAD_LIMIT` / `OMP_NUM_THREADS` on a worker that is processing real jobs: it
  recreates the deadlock the variables prevent.

## Rules

- A proof prints structure, counts, page numbers and timings only. Never page text, summary text,
  titles or file names. `verify_deposition_format.py` prints only each paragraph's opening
  page-range phrase; keep new output to that standard.
- No database writes. Call the database-free functions (`summarize_row()`,
  `transcript_page_offset()`), not the worker tasks, which store rows.
- Address a live row by its `review_rows.start`, never by a stored summary's `row_start`
  (a snapshot) or `Summary.idx` (a different number from `ReviewRow.idx`).
- These scripts rely on the image's `PYTHONPATH=/app`; they do not add the backend root to
  `sys.path`. Run them in a container, or from `backend/` with `PYTHONPATH=.`.
- A script that must survive a hung thread ends with `os._exit(0)`, as the hammer does; a plain
  exit waits for the stuck thread.
- Use `<user_id>` / `<document_id>` placeholders in docstrings. The repo is public.

## Traps

- The hammer's default input is the first PDF directly inside `/app/uploads`, but stored PDFs live
  in per-user subfolders. Always pass a path.
- The worker image has no `pkill`. After a deadlocked run, `docker compose restart segment-worker`.
- `segment-worker` runs several replicas; `docker compose exec` targets one of them.

## Commands (from `backend/`)

```bash
uv run ruff check scripts/dev && uv run ruff format --check scripts/dev
```

Run commands for both scripts are in `README.md` beside this file.
