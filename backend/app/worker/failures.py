"""Failure taxonomy + control signals for resumable summarize (item 7).

The genai seam (services/genai_retry.generate_with_retry) rides out transient 429 / 5xx / dropped
connections within a request, then RE-RAISES the last exception. The resumable worker asks this
module whether that raised failure is worth waiting-and-retrying (TRANSIENT: shared-quota 429,
server overload, a dropped connection) or hopeless within a retry (PERMANENT: a blank/unreadable
sub-document, an auth/permission rejection, the per-day quota, a deadline 504, or anything
unrecognized).

Transient -> pause + auto-resume the remaining rows forever ("paused, will retry"). Permanent ->
the job ends "needs attention" naming the affected sub-documents (partial results are kept). The
transient set mirrors generate_with_retry's own retryable set so the two never disagree.

`job_outcome` / `is_failure` are the same taxonomy applied AFTER the fact, to a stored Job row
(#218). They live beside `classify_failure` on purpose: a query that reimplemented this mapping
would drift from the runtime one silently, and the whole complaint in #218 is that reading job
states as though they were all outcomes gives the wrong answer about the pipeline's health.
"""

import httpx
from google.genai import errors

from app.errors import (
    AI_BUSY_MESSAGE,
    AI_DAILY_QUOTA_MESSAGE,
    AI_DEADLINE_MESSAGE,
    AI_OVERSIZED_VALUE_MESSAGE,
    AI_REJECTED_MESSAGE,
    GENERIC_USER_MESSAGE,
    EmptyExtractionError,
    OcrUnavailableError,
    PipelineTimeoutError,
    is_daily_quota,
    is_deadline_exceeded,
    user_facing_message,
)


def classify_failure(exc: Exception) -> str:
    """Return "transient" (wait and retry) or "permanent" (needs attention).

    Transient == the seam's own retryable set: a 429 that is NOT the per-day/free-tier quota, a 5xx
    ServerError that is NOT a deadline 504, and a transport-level disconnect. Everything else --
    PipelineError (empty / unreadable OCR), a non-429 ClientError (auth / bad request), the per-day
    quota, a deadline 504, and any unrecognized exception -- is permanent, so we surface it rather
    than retry forever.
    """
    if isinstance(exc, errors.ServerError):
        # Mirrors generate_with_retry's carve-out: the deadline binds every attempt identically, so
        # pausing and auto-resuming would replay the same doomed call indefinitely.
        return "permanent" if is_deadline_exceeded(exc) else "transient"
    if isinstance(exc, errors.ClientError):
        if getattr(exc, "code", None) == 429 and not is_daily_quota(exc):
            return "transient"
        return "permanent"
    if isinstance(exc, httpx.TransportError):
        return "transient"
    return "permanent"


# The persisted outcome vocabulary, and what each bucket MEANS - which is the whole point of #218.
# Three of the five non-`done` job states are not failures at all, so counting them together makes
# the pipeline's health unreadable in both directions: it overstated the problem (27.4% "did not
# complete cleanly", of which the largest part was 12 restart orphans on one day in July) and it hid
# the one real signal (transient Vertex failures) inside two categories of non-problem.
JOB_OUTCOMES: dict[str, str] = {
    "in_flight": "still queued, running or paused - not an outcome, so not in any rate",
    "completed": "finished normally",
    "partial": "finished and named the sub-documents it could not do (needs_attention)",
    "stopped": "a reviewer pressed Stop - a success, by any reading",
    "orphaned": "reaped after a restart killed its worker - NOT a failure",
    "failed_external": "the AI service was busy, over quota, or too slow - outside this code",
    "failed_config": "administrator-actionable: a deadline, a missing binary, an oversized value",
    "failed_content": "a property of the document itself - nothing readable to work with",
    "failed_unknown": "an error this taxonomy does not recognize - the number worth watching",
}

# `Job.error` is always written as `errors.user_facing_message(exc)` (worker/tasks.py:215), so a
# STORED job can be classified by matching that text back against the SAME constants the writer
# used. That shared source is what keeps the two halves honest - this is not substring guessing at
# vendor wording.
#
# `startswith` rather than equality because the summarize give-up path appends a sentence to one of
# these constants (tasks.py:1241) rather than replacing it.
#
# An error type not listed here lands in `failed_unknown` BY DESIGN, and the report names the
# unrecognized message so a new one shows up as itself rather than being quietly folded into a
# bucket it does not belong to. Deliberately not a test over `PipelineError.__subclasses__()`:
# forcing this list to be exhaustive would fail the suite for adding an error type, which is not the
# same thing as miscounting one.
_ERROR_CAUSES: tuple[tuple[str, str], ...] = (
    (AI_BUSY_MESSAGE, "failed_external"),
    (AI_DAILY_QUOTA_MESSAGE, "failed_external"),
    (PipelineTimeoutError.user_message, "failed_external"),
    (AI_DEADLINE_MESSAGE, "failed_config"),
    (AI_REJECTED_MESSAGE, "failed_config"),
    (AI_OVERSIZED_VALUE_MESSAGE, "failed_config"),
    (OcrUnavailableError.user_message, "failed_config"),
    (EmptyExtractionError.user_message, "failed_content"),
    # Last, and only as an exact match: the generic message is a PREFIX of nothing, but putting it
    # ahead of the others would be a live hazard if its wording ever changed to something shared.
    (GENERIC_USER_MESSAGE, "failed_unknown"),
)

_STATE_OUTCOMES: dict[str, str] = {
    "queued": "in_flight",
    "running": "in_flight",
    "paused": "in_flight",
    "done": "completed",
    "needs_attention": "partial",
    "cancelled": "stopped",
    "interrupted": "orphaned",
}


def job_outcome(state: str | None, error: str | None = None) -> str:
    """What a STORED job row actually says happened - one of `JOB_OUTCOMES`.

    The persisted counterpart of `classify_failure` above, which lives here rather than in a query
    so the two cannot drift: that one classifies a live exception to decide whether to retry, this
    one classifies the record left behind. Same taxonomy, two moments.

    Only `state == "error"` consults `error`, and an unrecognized message is `failed_unknown` rather
    than a guess. A state this function has never heard of is also `failed_unknown` - louder than
    silently returning "completed", which is the direction a health number must never fail in.
    """
    if state in _STATE_OUTCOMES:
        return _STATE_OUTCOMES[state]
    if state != "error":
        return "failed_unknown"
    text = (error or "").strip()
    for message, outcome in _ERROR_CAUSES:
        if text.startswith(message):
            return outcome
    return "failed_unknown"


def is_failure(outcome: str) -> bool:
    """Whether an outcome belongs in a failure RATE.

    `orphaned` and `stopped` are excluded, which is the correction #218 asks for: a restart orphan
    is an operational event and a reviewer's Stop is a success. `partial` is excluded too - it
    finished and said what it could not do, which is the design working rather than a fault.

    `in_flight` is not a failure and is also not a DENOMINATOR: a rate taken over jobs that have not
    finished moves as they finish, which is another way the obvious query misleads.
    """
    return outcome.startswith("failed_")


def reason_for(exc: Exception) -> str:
    """A calm, user-facing reason for a permanent failure -- shares user_facing_message's wording
    (a PipelineError's message, a friendly genai translation, or the generic fallback), so a
    per-row reason and a whole-job terminal message never disagree."""
    return user_facing_message(exc)


class JobPaused(Exception):
    """Signal from summarize work(): transient pressure -> pause and auto-resume the rest.

    Carries the progress so the runner can persist it (the bar keeps its position) and the fixed
    delay before the scheduled resume.
    """

    def __init__(self, delay: int, done: int, total: int) -> None:
        super().__init__(f"paused after {done}/{total}")
        self.delay = delay
        self.done = done
        self.total = total


class JobCancelled(Exception):
    """Signal: the reviewer asked for this job to stop.

    A THIRD cooperative signal in the same shape as JobPaused / JobNeedsAttention, deliberately - a
    stop is a normal outcome rather than a fault, and reusing the existing mechanism means the runner
    already knows how to end it cleanly without a rollback.

    Carries the progress so the finalizer can persist where it stopped, which is what a later Continue
    shows as its starting point. No message field: the reviewer knows why they pressed Stop, and
    inventing one would put "cancelled" in the `error` column, which is for real faults.
    """

    def __init__(self, done: int, total: int) -> None:
        super().__init__(f"cancelled at {done}/{total}")
        self.done = done
        self.total = total


class JobNeedsAttention(Exception):
    """Signal: one or more sub-documents permanently failed. End the job calmly (not "error"),
    keep every successful summary, and carry a friendly message + the affected rows (non-PHI:
    idx + page range + reason) for the UI + the audit trail."""

    def __init__(self, message: str, rows: list[dict]) -> None:
        super().__init__(message)
        self.message = message
        self.rows = rows
