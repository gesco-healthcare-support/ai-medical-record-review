"""RQ worker tier (P4, Path B).

Two queues (split topology B): `segment` (torch/classifier workers) + `summarize` (torch-free
workers). Both worker types need Tesseract + Poppler; only segment workers additionally load torch
+ the embedding model (the `classifier` extra). Run with `python -m app.worker <queue>`.

`queues` is import-light (redis only) so the web tier can enqueue without importing torch; the
heavy task functions live in `tasks` and are referenced by dotted path.
"""
