"""Domain exceptions for the document pipeline, with user-facing messages.

Job / sync-route failures are shown to non-technical users, so a pipeline error carries a
plain-language ``user_message``. Callers show that (never a raw stack trace or a vendor API error
like Vertex's "Model input cannot be empty") and log the technical detail server-side. Pure
Python - no framework - so the Flask-free services layer can raise these.
"""

GENERIC_USER_MESSAGE = (
    "Something went wrong while processing this document. Please try again; if it keeps "
    "failing, contact your administrator."
)

# Friendly wording for google-genai failures, shared by user_facing_message (terminal job errors)
# and worker.failures.reason_for (per-row summarize failures) so both render identically.
AI_BUSY_MESSAGE = (
    "The AI service was busy and the request could not be completed. Please try again shortly."
)
AI_DAILY_QUOTA_MESSAGE = "The daily AI quota has been used up; it resets on Google's schedule."
AI_REJECTED_MESSAGE = "The AI service rejected the request (a permission or request problem)."
# A deadline 504 is NOT "busy": the request was refused for needing longer than the limit WE set,
# which is a configuration fact rather than load. Calling it busy is not just imprecise, it
# misdirects - on 2026-08-12 this wording sent a real investigation hunting Vertex capacity for a
# document whose vision window simply needed 179s against a 120s limit.
AI_DEADLINE_MESSAGE = (
    "One part of this document needed longer than the current per-request time limit allows. "
    "Please contact your administrator, who can raise the limit or split the document."
)


class PipelineError(Exception):
    """A document-pipeline failure whose ``user_message`` is safe to show the user."""

    user_message = GENERIC_USER_MESSAGE

    def __init__(self, technical: str | None = None) -> None:
        # The str() carries the technical detail for logs; user_message is what the UI shows.
        super().__init__(technical or self.user_message)


class OcrUnavailableError(PipelineError):
    """Tesseract or Poppler is missing/unreachable, so pages cannot be read."""

    user_message = (
        "Text recognition (OCR) is unavailable on the server, so this document could not be "
        "read. Please contact your administrator."
    )


class EmptyExtractionError(PipelineError):
    """OCR ran but produced no text for the pages, so there is nothing to summarize."""

    user_message = (
        "No readable text was found in this document, so there was nothing to summarize. The "
        "pages may be blank or scanned images the text recognizer could not read."
    )


class PipelineTimeoutError(PipelineError):
    """A pipeline stage exceeded its wall-clock budget and was stopped rather than left to hang."""

    user_message = (
        "Processing took too long and was stopped. Please try again; if it keeps happening the "
        "document may be very large or the AI service may be busy."
    )


def is_daily_quota(exc: Exception) -> bool:
    """A per-day / free-tier 429: a sustained quota exhaustion, not a shared-quota blip."""
    text = str(exc)
    return "PerDay" in text or "free_tier" in text


def is_deadline_exceeded(exc: Exception) -> bool:
    """A 504 DEADLINE_EXCEEDED: the per-request deadline WE set was too short for this call.

    google-genai forwards HttpOptions.timeout (genai_http_timeout_ms) to Vertex as the SERVER-side
    deadline, so a call needing longer returns a server 504 rather than stalling client-side.
    Proven 2026-08-12: an 8000ms client timeout produced a server 504 at 6.2s.

    That makes it DETERMINISTIC, not transient - the same limit binds every attempt, so a retry
    re-runs the same doomed call. Measured on job 1000174: eight identical 504s over 17.5 minutes.
    Single source of truth for the seam's retry set, the worker's transient set, and the
    user-facing message, so those three cannot drift apart.

    Checked on the status code alone so callers need no google.genai import - this module stays
    light for the many callers that never touch genai.
    """
    return getattr(exc, "code", None) == 504


def genai_user_message(exc: Exception) -> str | None:
    """A friendly message for a google-genai error we recognize, else None.

    A deadline 504 -> the deadline message (our limit, not their load); any other ServerError (5xx)
    or a transient shared-quota 429 -> "busy, try again"; the per-day/free-tier quota -> the
    daily-quota message; any other ClientError (auth / bad request) -> "rejected".
    Imported lazily so this module stays light for the many callers that never touch genai."""
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.ServerError):
        return AI_DEADLINE_MESSAGE if is_deadline_exceeded(exc) else AI_BUSY_MESSAGE
    if isinstance(exc, genai_errors.ClientError):
        if getattr(exc, "code", None) == 429 and not is_daily_quota(exc):
            return AI_BUSY_MESSAGE
        if is_daily_quota(exc):
            return AI_DAILY_QUOTA_MESSAGE
        return AI_REJECTED_MESSAGE
    return None


def user_facing_message(exc: Exception) -> str:
    """The message to show a user for a failed job/route: a PipelineError's own ``user_message``,
    else a friendly translation of a known genai error, else a generic one (the technical detail is
    logged server-side, never shown raw)."""
    if isinstance(exc, PipelineError):
        return exc.user_message
    genai_message = genai_user_message(exc)
    if genai_message is not None:
        return genai_message
    return GENERIC_USER_MESSAGE
