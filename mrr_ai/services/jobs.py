"""Single-slot background job runner for the review UI.

The app is single-process with module-level state (one editor, one case at a time), so
one job slot is the honest model: a second segmentation/summarization cannot run
concurrently against the same globals anyway. The runner exposes a snapshot the UI
polls; progress updates are scalar dict writes from the single worker thread (GIL-safe
for this read-mostly pattern).
"""

import threading

_lock = threading.Lock()
_job = None  # dict(kind, state, stage, current, total, result, error)


def start(kind, target):
    """Start `target(report)` on a worker thread; returns False if a job is running.

    `report(stage, current, total)` may be called freely by the target; the target's
    return value becomes the job result. Exceptions land in `error` - the job must
    surface failures to the polling UI, never die silently.
    """
    global _job
    with _lock:
        if _job is not None and _job["state"] == "running":
            return False
        _job = dict(
            kind=kind,
            state="running",
            stage="starting",
            current=0,
            total=0,
            result=None,
            error=None,
        )
        job = _job

    def report(stage, current, total):
        job["stage"], job["current"], job["total"] = stage, current, total

    def runner():
        try:
            job["result"] = target(report)
            job["state"] = "done"
        except Exception as exc:  # noqa: BLE001 - the UI needs the message, whatever it is
            job["error"] = str(exc)
            job["state"] = "error"

    threading.Thread(target=runner, daemon=True).start()
    return True


def status():
    """Snapshot for polling; results are included only when the job is done."""
    with _lock:
        if _job is None:
            return {"state": "idle"}
        snap = {k: _job[k] for k in ("kind", "state", "stage", "current", "total", "error")}
        if _job["state"] == "done":
            snap["result"] = _job["result"]
        return snap


def clear():
    """Forget a finished job (a running one keeps its slot - there is only one case)."""
    global _job
    with _lock:
        if _job is not None and _job["state"] != "running":
            _job = None
            return True
        return _job is None
