"""RQ queues + kind->queue routing for the split topology (P4), with a lane per user.

Import-light (redis + rq + config only): the web tier imports this to enqueue, and the worker task
functions are referenced by dotted PATH so enqueuing never imports torch. The Redis connection is
lazy (from settings.redis_url), so importing this module neither connects nor requires Redis.

Two axes of separation, for two different reasons:

* BY TASK (`segment` vs `summarize`) because the images differ - the segment worker carries torch for
  the classifier and the summarize worker does not.
* BY USER (`segment:2`, `segment:3`, ...) because a single queue serialises every tester behind
  whoever got there first. Measured 2026-07-30: a segment job waited 427 seconds unstarted behind
  another user's job, and the next job then waited behind it. A lane per user plus a round-robin
  worker (see app/worker/__main__.py) makes head-of-line blocking between users impossible.

The base name with no suffix stays a valid queue and every worker still listens to it. It is the
fallback for a job whose document has no owner, and it keeps jobs enqueued under the pre-lane names
runnable across the deploy that introduces lanes, rather than orphaning them.
"""

import logging
from functools import lru_cache

from redis import Redis
from rq import Queue

from app.config import get_settings

logger = logging.getLogger(__name__)

SEGMENT_QUEUE = "segment"
SUMMARIZE_QUEUE = "summarize"
QUEUE_NAMES = (SEGMENT_QUEUE, SUMMARIZE_QUEUE)
# Reading a just-uploaded record's pages into the page-text store (no model). NOT a base queue a
# worker is started with: every SEGMENT worker reads it as well (it has Tesseract and Poppler), also
# while it waits for a silent model server (worker/__main__.py). No user lanes: nobody waits on it -
# Identify reads whatever pages are still missing itself.
OCR_QUEUE = "ocr"
_OCR_FN = "app.worker.tasks.ocr_uploaded_document"

# `classify` (individual-record auto-categorization, P6) runs the classifier, so it goes on the
# segment (torch) queue - not the torch-free summarize queue. `dedup` (duplicate clustering) is OCR
# + one cheap Gemini call with NO torch, so it rides the summarize queue's worker (which already has
# Tesseract/Poppler); no new worker container is needed.
_QUEUE_FOR_KIND = {
    "segment": SEGMENT_QUEUE,
    "classify": SEGMENT_QUEUE,
    "summarize": SUMMARIZE_QUEUE,
    "dedup": SUMMARIZE_QUEUE,
}
_WORKER_FN = {
    "segment": "app.worker.tasks.segment_document",
    "classify": "app.worker.tasks.classify_document",
    "summarize": "app.worker.tasks.summarize_document",
    "dedup": "app.worker.tasks.dedup_document",
}


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def base_queue_name(kind: str) -> str:
    """The task-level queue a kind belongs to, without a user lane."""
    return _QUEUE_FOR_KIND[kind]


def lane_name(base: str, user_id=None) -> str:
    """``segment`` -> ``segment:2``. A missing user_id yields the base name unchanged.

    The base name is deliberately still a real queue: a document with no owner has nowhere else to
    go, and jobs enqueued before lanes existed sit on it.
    """
    return base if user_id in (None, "") else f"{base}:{user_id}"


def queue_for(kind: str, user_id=None) -> Queue:
    """The queue a job of this kind and owner belongs on."""
    return Queue(lane_name(_QUEUE_FOR_KIND[kind], user_id), connection=get_redis())


def lanes_for(base: str, user_ids) -> list[str]:
    """Every queue name a worker for ``base`` should listen to: the base plus one lane per user.

    Order is base-first then ascending user id, which matters only for reproducibility - the worker
    dequeues round-robin, so position confers no priority (that is the whole point; see
    app/worker/__main__.py).
    """
    seen, names = set(), [base]
    for user_id in sorted(user_ids, key=lambda value: (str(type(value)), value)):
        name = lane_name(base, user_id)
        if name not in seen and name != base:
            seen.add(name)
            names.append(name)
    return names


def worker_fn(kind: str) -> str:
    return _WORKER_FN[kind]


def enqueue_upload_ocr(document_id: str, page_count: int) -> bool:
    """Queue the reading of a just-uploaded record's pages. Best effort: False when it could not be
    queued, and the upload goes on regardless - Identify reads any page still missing itself.

    UNTRACKED on purpose: no ``jobs`` row. One active job per document is enforced by a partial unique
    index, so a tracked reading job would make the reviewer's Identify answer 409 while it ran. A
    5,000-page record takes ~45 min to read on the test box's CPU; done here, that time is no longer
    spent inside Identify, where it holds a rented model server that has nothing to do yet.
    """
    try:
        Queue(OCR_QUEUE, connection=get_redis()).enqueue(
            _OCR_FN,
            document_id,
            job_timeout=get_settings().effective_job_timeout(page_count or 0),
        )
    except Exception:
        # Redis down at upload time must not fail the upload: the pages are read by Identify instead.
        logger.warning(
            "could not queue the page reading for document %s", document_id, exc_info=True
        )
        return False
    return True
