"""Production entry point: waitress, ONE process, many threads.

SINGLE-PROCESS is a hard constraint, not a preference:
- background pipelines run on an in-process pool (services/job_queue.py); a second
  process would have its own pool and its own view of "active" jobs, and
- the classic UI still uses module-level globals (mrr_ai/state.py).
Scale threads, never workers. Thread budget = HTTP polling headroom on top of the
pipeline workers (each running job also holds one waitress thread only briefly -
jobs run on the pool, not on request threads).

Usage: uv run python serve.py  (host/port via HOST/PORT env)
"""

import os

from waitress import serve

from mrr_ai import create_app
from mrr_ai.config import PIPELINE_WORKERS

app = create_app()

if __name__ == "__main__":
    serve(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5010)),
        threads=PIPELINE_WORKERS + 6,
    )
