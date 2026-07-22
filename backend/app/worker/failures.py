"""Failure taxonomy + control signals for resumable summarize (item 7).

The genai seam (services/genai_retry.generate_with_retry) rides out transient 429 / 5xx / dropped
connections within a request, then RE-RAISES the last exception. The resumable worker asks this
module whether that raised failure is worth waiting-and-retrying (TRANSIENT: shared-quota 429,
server overload, a dropped connection) or hopeless within a retry (PERMANENT: a blank/unreadable
sub-document, an auth/permission rejection, the per-day quota, or anything unrecognized).

Transient -> pause + auto-resume the remaining rows forever ("paused, will retry"). Permanent ->
the job ends "needs attention" naming the affected sub-documents (partial results are kept). The
transient set mirrors generate_with_retry's own retryable set so the two never disagree.
"""

import httpx
from google.genai import errors

from app.errors import is_daily_quota, user_facing_message


def classify_failure(exc: Exception) -> str:
    """Return "transient" (wait and retry) or "permanent" (needs attention).

    Transient == the seam's own retryable set: a 429 that is NOT the per-day/free-tier quota, any
    5xx ServerError, and a transport-level disconnect. Everything else -- PipelineError (empty /
    unreadable OCR), a non-429 ClientError (auth / bad request), the per-day quota, and any
    unrecognized exception -- is permanent, so we surface it rather than retry forever.
    """
    if isinstance(exc, errors.ServerError):
        return "transient"
    if isinstance(exc, errors.ClientError):
        if getattr(exc, "code", None) == 429 and not is_daily_quota(exc):
            return "transient"
        return "permanent"
    if isinstance(exc, httpx.TransportError):
        return "transient"
    return "permanent"


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


class JobNeedsAttention(Exception):
    """Signal: one or more sub-documents permanently failed. End the job calmly (not "error"),
    keep every successful summary, and carry a friendly message + the affected rows (non-PHI:
    idx + page range + reason) for the UI + the audit trail."""

    def __init__(self, message: str, rows: list[dict]) -> None:
        super().__init__(message)
        self.message = message
        self.rows = rows
