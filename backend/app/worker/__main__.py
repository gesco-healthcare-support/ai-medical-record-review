"""Worker entrypoint: `python -m app.worker <queue> [<queue> ...]` runs an RQ worker.

Segment workers (need torch + Tesseract/Poppler):   python -m app.worker segment
Summarize workers (need Tesseract/Poppler, no torch): python -m app.worker summarize
No args runs a worker on both queues (dev convenience). Segment workers reset the classifier's
per-process catalog cache at startup so the first job reads a fresh catalog.

The argument is a BASE queue name. At startup it expands to the base plus one lane per existing user
(`segment`, `segment:2`, `segment:3`, ...) and the worker dequeues them ROUND-ROBIN.

Two things about that are load-bearing:

* `RoundRobinWorker`, not `Worker`. RQ's default worker reads its queues in strict priority order, so
  listing user lanes on a default worker would starve whichever user is last - the head-of-line bug
  this exists to fix, with extra steps.
* Lanes are enumerated ONCE, at startup. A user created afterwards has no lane on a running worker,
  so their jobs would sit unclaimed until it restarts. Acceptable while the tester set is fixed and
  known; if the app gets real multi-tenant use, replace enumeration with fixed hashed lanes (option B
  in docs/plans/2026-07-30-worker-queue-scaling.md) or re-read the user set periodically.
  **Restart the workers after adding a user.**
"""

import logging
import sys

from rq import Queue
from rq.worker import RoundRobinWorker

from app.worker.queues import QUEUE_NAMES, get_redis, lanes_for

logger = logging.getLogger(__name__)


def _user_ids() -> list:
    """Every user id that could own a job, for lane expansion.

    Fails SOFT: on any DB problem the worker still starts and serves the base queue, because a worker
    that refuses to boot because it could not list users is worse than one that serves fewer lanes.
    """
    try:
        from sqlalchemy import select

        from app.db import get_sessionmaker
        from app.models import User

        with get_sessionmaker()() as session:
            return list(session.scalars(select(User.id)).all())
    except Exception:
        logger.warning("could not enumerate users for queue lanes; serving base queues only")
        return []


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
    bases = argv or list(QUEUE_NAMES)
    invalid = [q for q in bases if q not in QUEUE_NAMES]
    if invalid:
        raise SystemExit(f"unknown queue(s) {invalid}; choose from {list(QUEUE_NAMES)}")

    if "segment" in bases:
        # Segment workers run the classifier; reset its per-process catalog cache at startup so a
        # stale category set / embedding matrix can never outlive an edit made before this worker.
        from app.services.classification import reset_catalog_cache

        reset_catalog_cache()

    user_ids = _user_ids()
    names = [name for base in bases for name in lanes_for(base, user_ids)]
    logger.info("worker listening round-robin on %d queue(s): %s", len(names), names)

    redis = get_redis()
    # with_scheduler=True runs the RQ scheduler thread in-process so `enqueue_in`-scheduled jobs
    # (a paused summarize run's delayed resume, item 7) actually fire. Enabled on every worker;
    # RQ coordinates multiple schedulers with a Redis lock, so running it here is safe.
    # RoundRobinWorker inherits work() from Worker unchanged - it only overrides reorder_queues - so
    # with_scheduler behaves exactly as before the class swap.
    RoundRobinWorker([Queue(name, connection=redis) for name in names], connection=redis).work(
        with_scheduler=True
    )


if __name__ == "__main__":
    main()
