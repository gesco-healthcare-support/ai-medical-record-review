"""Bounded draining of a ThreadPoolExecutor.

The pipeline runs OCR / vision calls on small thread pools. `as_completed(futures)` with no timeout
waits forever if one worker stalls - the reproduced verify-pass forever-hang. `drain_pool` yields
futures as they complete but is bounded by an overall deadline (`as_completed(fs, timeout=t)`
measures `t` cumulatively from the call, i.e. a whole-pool budget); on the deadline it cancels the
not-yet-finished futures and raises `PoolTimeout` naming them, leaving each caller to decide what an
unfinished item means for its pool (keep a boundary split, fail the job, degrade a row, or pause).
"""

from collections.abc import Iterator
from concurrent.futures import Future, TimeoutError as FuturesTimeoutError, as_completed


class PoolTimeout(Exception):
    """A pool did not drain within its deadline. ``unfinished`` are the still-pending futures
    (cancel() has been attempted on each); the caller decides how to handle them."""

    def __init__(self, unfinished: list[Future]) -> None:
        super().__init__(f"pool drain timed out with {len(unfinished)} future(s) unfinished")
        self.unfinished = unfinished


def drain_pool(futures, timeout) -> Iterator[Future]:
    """Yield each future as it completes, bounded by ``timeout`` seconds overall. On timeout, cancel
    the unfinished futures and raise ``PoolTimeout(unfinished)``. ``futures`` may be any iterable of
    Futures - a list, or a dict keyed by Future (iterating a dict yields its keys)."""
    try:
        for future in as_completed(futures, timeout=timeout):
            yield future
    except FuturesTimeoutError as exc:
        unfinished = [f for f in futures if not f.done()]
        for f in unfinished:
            f.cancel()
        raise PoolTimeout(unfinished) from exc
