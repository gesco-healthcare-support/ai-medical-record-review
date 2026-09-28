# backend/scripts/eval

Measurement scripts: operator reports on pipeline health and model traffic, checks of pipeline
output against its own input or a human report, and A/B harnesses for prompts, rules and
segmentation settings. They read real records where the records live, and each module docstring
records the measurement it was written for and how to read its output. Nothing here writes to the
database.

| File | What it is |
| --- | --- |
| `job_health.py` | Pipeline health by job outcome, beside the naive "not done" rate. `--kind`, `--since`, `--build`, `--by-kind`. |
| `vertex_stats.py` | Read, `--reset` or `--watch` the per-model call counters kept in Redis. |
| `pacer_watch.py` | CSV sampler of the pacer's send rate against the rejection rate. Read-only. |
| `corpus.py` | Library: the one-copy-per-distinct-PDF rule (`one_copy_per_pdf()`, `one_copy_per_document()`). |
| `ab_stats.py` | Library: pure aggregation for the segmentation boundary A/B. |
| `prompt_variants.py` | Library: historical segmentation prompts used as A/B arms. |
| `date_in_source.py` | Does each row's date appear in its own stored pages. Counts only. |
| `date_label_check.py` | When a page labels its dates, which labelled date a row took. |
| `date_vs_human_entries.py` | One record's rows against the human report for the same record. |
| `ocr_cap_word_recall.py` | Word recall of capped OCR renders against an uncapped one. |
| `classify_prompt_ab.py` | Classification prompt A/B against reviewer corrections. Calls the model. |
| `rule_blast_radius.py` | Which alternative of one classification rule claims which rows. |
| `rule_removal_cost.py` | Cost of removing one alternative from that rule. Calls the model. |
| `segmentation_boundary_ab.py` | Segmentation prompt arms scored against reviewer-corrected boundaries. Calls the model. |
| `segmentation_cap_ab.py` | Production segmentation against a labelled case, with and without the window page cap. Calls the model. |
| `window_duration_curve.py` | Time of one segmentation call as its page count grows. Calls the model. |
| `README.md`, `CLAUDE.md` | This file; rules for AI coding agents working here. |

## Running

On the server, inside the `api` container (the image has `scripts/` and `PYTHONPATH=/app`):

```bash
docker compose exec -T api python scripts/eval/job_health.py --by-kind
```

From a checkout, run the file from `backend/` with `PYTHONPATH=.`, so `app` is importable whether
or not the file adds it to `sys.path`, and sibling modules (`corpus`, `ab_stats`,
`prompt_variants`) still import by bare name:

```bash
cd backend
PYTHONPATH=. uv run python scripts/eval/job_health.py --by-kind
```

Scripts that pool counts across documents count one copy per distinct PDF by default and take
`--all-copies` for the pooled figure; `corpus.py` explains why.

## Tests

```bash
cd backend
uv run pytest -q tests/test_ab_stats.py tests/test_eval_corpus.py tests/test_job_health.py tests/test_date_in_source.py tests/test_date_label_check.py tests/test_date_vs_human_entries.py tests/test_ocr_cap_word_recall.py tests/test_classify_prompt_ab.py
```

Each test loads its script by file path. `vertex_stats.py`, `pacer_watch.py`,
`prompt_variants.py`, `rule_blast_radius.py`, `rule_removal_cost.py`,
`segmentation_boundary_ab.py`, `segmentation_cap_ab.py` and `window_duration_curve.py` have no
test of their own.

## Documentation

- [Scripts reference](../../../docs/reference/scripts.md): flags, reads, model calls, PHI handling
  and the run command for each script.
- [How to diagnose a stuck or failed job](../../../docs/how-to/diagnose-a-stuck-or-failed-job.md).
