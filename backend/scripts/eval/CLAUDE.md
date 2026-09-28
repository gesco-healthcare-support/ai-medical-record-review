# backend/scripts/eval - agent instructions

Measurement scripts over real records: health reports, output checks and A/B harnesses. Every
script, its flags and its model calls: `docs/reference/scripts.md`.

## Never without the user's explicit go

- Running any script here. They read real records (PHI) where the records live.
- Running a script that calls a model (`classify_prompt_ab.py`, `rule_removal_cost.py`,
  `segmentation_boundary_ab.py`, `segmentation_cap_ab.py`, `window_duration_curve.py`). Cost scales
  with rows x arms x repeats, and the calls send page text or PDF pages off the box. Some build
  their own `google-genai` client and go to Gemini whatever `LLM_BACKEND` says; check which in
  `docs/reference/scripts.md` before running one.
- `vertex_stats.py --reset`: it deletes shared Redis counters that someone else's experiment may be
  bracketing.

## Rules for a new or changed eval script

- No database writes. Ever. Reviewer-corrected `review_rows` are the only ground truth this project
  has; re-running a stage through the worker path overwrites them.
- Output is counts, ids, page numbers, dates only where the purpose needs them, and metric values.
  Never titles, page text, summary text, file names or patient fields. Strip PHI before writing any
  results file (see `classify_prompt_ab.py`, which drops `text` before `json.dump`).
- Any count pooled across documents goes through `corpus.py` (`one_copy_per_pdf()` or
  `one_copy_per_document()`), defaults to one copy per distinct `documents.sha256`, offers
  `--all-copies`, and prints how many copies it dropped.
- Keep arithmetic that decides a result in pure functions and test them without a database
  (`ab_stats.py`, `job_health.py` `classify()`).
- Model-calling A/B scripts take `--repeats`; temperature 0 is not deterministic on this API. Read
  the spread, not one run.
- Header: resolve the backend root with a guard, as `ocr_cap_word_recall.py` does
  (`if len(_HERE.parents) > 2: sys.path.insert(0, str(_HERE.parents[2]))`), and insert the script's
  own directory before importing a sibling (`from corpus import ...`).
- Add a test that loads the script by path (pattern: `tests/test_ab_stats.py`).
- Examples use `example.com` addresses and `<document_id>` placeholders. The repo is public.

## Traps

- Several scripts import PRIVATE app names (`segment_engine._window_rows`,
  `segment_engine._escalation_text`, `classification._RULES`, `classification._ADMIN_RULES`,
  `classification.llm_classify` source text). A refactor there can break a script with CI still
  green, because most model-calling scripts have no test. Run the script on a small input
  (`--limit`, `--list`, one document) against the current tree before quoting any number it prints.
- `rule_blast_radius.py` and `rule_removal_cost.py` have no `main()` guard: importing them runs
  them. Load them by path only to run them.
- Not every script uses argparse: `segmentation_cap_ab.py` and `window_duration_curve.py` read
  positional arguments, so `--help` is taken as a case or document id.
- `segmentation_boundary_ab.py` imports `ab_stats` and `prompt_variants` by bare name, so it fails
  under `python -m`. Run files with `PYTHONPATH=.` from `backend/`.
- `segmentation_cap_ab.py` needs the labelled cases under `experiments/a1-segmentation/`, whose
  location is a machine-specific path in `experiments/a1-segmentation/src/config.py`.
- `classify_prompt_ab.py --out` defaults to the working directory; `/app/instance` is read-only in
  the container. Write to `/tmp` there.
- Untracked scripts may exist in a local copy of this folder. Only tracked files are documented or
  supported; `pyproject.toml` `testpaths = ["tests"]` keeps pytest from collecting them.

## Commands (from `backend/`)

```bash
uv run ruff check scripts/eval && uv run ruff format --check scripts/eval
uv run pytest -q tests/test_ab_stats.py tests/test_eval_corpus.py tests/test_job_health.py tests/test_date_in_source.py tests/test_date_label_check.py tests/test_date_vs_human_entries.py tests/test_ocr_cap_word_recall.py tests/test_classify_prompt_ab.py
PYTHONPATH=. uv run python scripts/eval/<name>.py <flags from docs/reference/scripts.md>
```
