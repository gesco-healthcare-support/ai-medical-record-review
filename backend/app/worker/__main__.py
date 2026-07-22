"""Worker entrypoint: `python -m app.worker <queue> [<queue> ...]` runs an RQ worker.

Segment workers (need torch + Tesseract/Poppler):   python -m app.worker segment
Summarize workers (need Tesseract/Poppler, no torch): python -m app.worker summarize
No args runs a worker on both queues (dev convenience). Segment workers reset the classifier's
per-process catalog cache at startup so the first job reads a fresh catalog.
"""

import logging
import sys

from rq import Queue, Worker

from app.worker.queues import QUEUE_NAMES, get_redis


def main(argv: list[str] | None = None) -> None:
    # Send app logs (the pipeline stages, per-row outcomes, terminal errors - ids only, never PHI)
    # to stdout at INFO so they are visible in `docker logs`. RQ configures its own "rq.worker"
    # logger separately; this handles everything under the app.* tree via the root logger.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    argv = list(sys.argv[1:] if argv is None else argv)
    queues = argv or list(QUEUE_NAMES)
    invalid = [q for q in queues if q not in QUEUE_NAMES]
    if invalid:
        raise SystemExit(f"unknown queue(s) {invalid}; choose from {list(QUEUE_NAMES)}")

    if "segment" in queues:
        # Segment workers run the classifier; reset its per-process catalog cache at startup so a
        # stale category set / embedding matrix can never outlive an edit made before this worker.
        from app.services.classification import reset_catalog_cache

        reset_catalog_cache()

    redis = get_redis()
    # with_scheduler=True runs the RQ scheduler thread in-process so `enqueue_in`-scheduled jobs
    # (a paused summarize run's delayed resume, item 7) actually fire. Enabled on every worker;
    # RQ coordinates multiple schedulers with a Redis lock, so running it here is safe.
    Worker([Queue(name, connection=redis) for name in queues], connection=redis).work(
        with_scheduler=True
    )


if __name__ == "__main__":
    main()
