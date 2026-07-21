"""RQ queues + kind->queue routing for the split topology (P4).

Import-light (redis + rq + config only): the web tier imports this to enqueue, and the worker task
functions are referenced by dotted PATH so enqueuing never imports torch. The Redis connection is
lazy (from settings.redis_url), so importing this module neither connects nor requires Redis.
"""

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.config import get_settings

SEGMENT_QUEUE = "segment"
SUMMARIZE_QUEUE = "summarize"
QUEUE_NAMES = (SEGMENT_QUEUE, SUMMARIZE_QUEUE)

# `classify` (individual-record auto-categorization, P6) runs the classifier, so it goes on the
# segment (torch) queue - not the torch-free summarize queue.
_QUEUE_FOR_KIND = {
    "segment": SEGMENT_QUEUE,
    "classify": SEGMENT_QUEUE,
    "summarize": SUMMARIZE_QUEUE,
}
_WORKER_FN = {
    "segment": "app.worker.tasks.segment_document",
    "classify": "app.worker.tasks.classify_document",
    "summarize": "app.worker.tasks.summarize_document",
}


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def queue_for(kind: str) -> Queue:
    return Queue(_QUEUE_FOR_KIND[kind], connection=get_redis())


def worker_fn(kind: str) -> str:
    return _WORKER_FN[kind]
