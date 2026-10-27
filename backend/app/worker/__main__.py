"""Worker entrypoint: `python -m app.worker <queue> [<queue> ...]` runs an RQ worker.

Segment workers (need torch + Tesseract/Poppler):   python -m app.worker segment
Summarize workers (need Tesseract/Poppler, no torch): python -m app.worker summarize
No args runs a worker on both queues (dev convenience). Segment workers reset the classifier's
per-process catalog cache at startup so the first job reads a fresh catalog, and also read the `ocr`
queue (uploaded records' pages, no model) - including while they wait for a silent model server.

The argument is a BASE queue name. At startup it expands to the base plus one lane per existing user
(`segment`, `segment:2`, `segment:3`, ...) and the worker dequeues them ROUND-ROBIN.

Two things about that are load-bearing:

* `RoundRobinWorker`, not `Worker`. RQ's default worker reads its queues in strict priority order, so
  listing user lanes on a default worker would starve whichever user is last - the head-of-line bug
  this exists to fix, with extra steps.
* Lanes are enumerated ONCE, at startup. A user created afterwards has no lane on a running worker,
  so their jobs would sit unclaimed until it restarts. Acceptable while the tester set is fixed and
  known; if the app gets real multi-tenant use, replace enumeration with a fixed set of hashed lanes
  (each user's id hashed onto one of N queues) or re-read the user set periodically.
  **Restart the workers after adding a user.**
"""

import logging
import signal
import sys
import time

from rq import Queue
from rq.worker import RoundRobinWorker, Worker

from app.worker.queues import OCR_QUEUE, QUEUE_NAMES, get_redis, lanes_for

logger = logging.getLogger(__name__)


def _drain_ocr_queue() -> None:
    """Read every uploaded record queued on the ``ocr`` queue, then return (an RQ burst).

    Only ever while the model server is silent (see ``_read_uploads_then_sleep``): no model job can
    be taken here, because only the ``ocr`` queue is listened to. An empty queue starts no RQ worker,
    so a night of 30-second checks writes no RQ log lines. RQ installs its own SIGINT/SIGTERM handlers
    for the burst; they are put back afterwards so a stop during the next pause behaves as before.
    """
    redis = get_redis()
    queue = Queue(OCR_QUEUE, connection=redis)
    if queue.count == 0:
        return
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        Worker([queue], connection=redis).work(burst=True, with_scheduler=False)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def _read_uploads_then_sleep(seconds: float) -> None:
    """A segment worker's pause between model-server checks: read uploaded records' pages, then
    sleep whatever is left of the pause.

    A big record can keep this worker reading for most of an hour; that delays only THIS worker's
    next model check, and its model jobs could not run before that check anyway. Never raises: a
    failure here must not take the worker down while it waits (the pages are read by Identify).
    """
    started = time.monotonic()
    try:
        _drain_ocr_queue()
    except Exception:
        logger.warning("reading uploaded records while waiting failed", exc_info=True)
    time.sleep(max(0.0, seconds - (time.monotonic() - started)))


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

    # Before this process takes a single job. NOT wrapped in a try: _user_ids() below deliberately
    # swallows so that a database blip cannot stop a worker serving the base queues, and copying that
    # shape here would let a worker start against an unverified PHI destination and fail rows one by
    # one instead. A silent server (the pod is off) is waited for here, not crash-looped on: queued
    # jobs stay queued until it answers and passes. See services/llm/preflight. A segment worker
    # spends the wait reading uploaded records' pages (no model involved), so a record uploaded
    # with the server off is read before it comes back.
    from app.services.llm.preflight import wait_for_backends

    if "segment" in bases:
        wait_for_backends(sleep=_read_uploads_then_sleep)
    else:
        wait_for_backends()

    if "segment" in bases:
        # Segment workers run the classifier; reset its per-process catalog cache at startup so a
        # stale category set / embedding matrix can never outlive an edit made before this worker.
        from app.services.classification import reset_catalog_cache

        reset_catalog_cache()

    user_ids = _user_ids()
    lanes = [name for base in bases for name in lanes_for(base, user_ids)]
    # Segment workers also read uploaded records' pages. The ocr queue is not a reviewer lane, so it
    # is listened to but not handed to the fairness rule below.
    names = lanes + ([OCR_QUEUE] if "segment" in bases else [])
    logger.info("worker listening round-robin on %d queue(s): %s", len(names), names)
    # The identify cap may only hand a turn to a lane this worker actually reads (worker/fairness.py),
    # and a forked work-horse inherits this.
    from app.worker import fairness

    fairness.serve(lanes)

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
