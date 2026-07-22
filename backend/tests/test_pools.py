"""Bounded pool draining (pipeline forever-hang fix).

drain_pool yields futures as they complete but never waits past an overall deadline; on timeout it
cancels the stragglers and raises PoolTimeout naming them, so no as_completed() wait is unbounded.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.pools import PoolTimeout, drain_pool


def test_drain_pool_yields_all_within_timeout():
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit((lambda i=i: i)) for i in range(5)]
        seen = list(drain_pool(futures, timeout=5))
    assert sorted(f.result() for f in seen) == [0, 1, 2, 3, 4]


def test_drain_pool_accepts_a_dict_of_futures():
    with ThreadPoolExecutor(max_workers=2) as pool:
        fmap = {pool.submit((lambda i=i: i)): i for i in range(3)}
        seen = list(drain_pool(fmap, timeout=5))
    assert len(seen) == 3


def test_drain_pool_times_out_and_reports_unfinished():
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        fast = [pool.submit(lambda: "fast") for _ in range(2)]
        slow = pool.submit(time.sleep, 3)  # blocks past the deadline
        seen = []
        with pytest.raises(PoolTimeout) as excinfo:
            for future in drain_pool(fast + [slow], timeout=0.5):
                seen.append(future)
        assert set(seen) == set(fast)  # the fast futures were yielded before the deadline
        assert excinfo.value.unfinished == [slow]  # the straggler is reported (and cancelled)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
