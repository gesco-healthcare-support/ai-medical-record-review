# backend/scripts/dev

Developer proofs: small scripts run by hand inside a container to demonstrate that a fix works on
real input. They write nothing to the database and are not part of the pipeline.

| File | What it is |
| --- | --- |
| `ocr_concurrency_hammer.py` | Runs 60 Tesseract OCR calls on 6 threads over the first 8 pages of one PDF, to show that `OMP_THREAD_LIMIT=1` / `OMP_NUM_THREADS=1` (set in `docker-compose.yml`) prevents the concurrent-OCR deadlock. Prints `<done>/60 OCRs in <secs>s`. |
| `verify_deposition_format.py` | Summarizes one real deposition sub-document and prints only its structure: paragraph count, page-range openers, cited page numbers, the step between paragraphs. Makes real model calls. |
| `README.md`, `CLAUDE.md` | This file; rules for AI coding agents working here. |

## Running

Both run inside a container, from the checkout directory on the machine that holds the records.
Stored PDFs are at `/app/uploads/<user_id>/<document_id>.pdf` in the containers.

```bash
docker compose exec segment-worker python scripts/dev/ocr_concurrency_hammer.py /app/uploads/<user_id>/<document_id>.pdf
docker compose restart segment-worker
```

The restart clears any `tesseract` process a deadlocked run left behind. To reproduce the deadlock
itself, blank the two variables for the run:

```bash
docker compose exec -e OMP_THREAD_LIMIT= -e OMP_NUM_THREADS= segment-worker python scripts/dev/ocr_concurrency_hammer.py /app/uploads/<user_id>/<document_id>.pdf
```

```bash
docker compose exec -T api python scripts/dev/verify_deposition_format.py <document_id> <row_start>
```

`<row_start>` is the first page of an existing review row of that document.

## Tests

Neither script has a test. Lint them with the rest of the backend:

```bash
cd backend
uv run ruff check scripts/dev && uv run ruff format --check scripts/dev
```

## Documentation

- [Scripts reference](../../../docs/reference/scripts.md): inputs, output, model calls and PHI
  handling for both.
- [OCR and page text](../../../docs/explanation/ocr-and-page-text.md): where the OCR thread limit
  matters.
