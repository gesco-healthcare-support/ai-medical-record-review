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


def genai_user_message(exc: Exception) -> str | None:
    """A friendly message for a google-genai error we recognize, else None.

    A ServerError (5xx) or a transient shared-quota 429 -> "busy, try again"; the per-day/free-tier
    quota -> the daily-quota message; any other ClientError (auth / bad request) -> "rejected".
    Imported lazily so this module stays light for the many callers that never touch genai."""
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.ServerError):
        return AI_BUSY_MESSAGE
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
