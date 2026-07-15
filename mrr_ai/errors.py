"""Domain exceptions for the document pipeline, with user-facing messages.

Job failures are shown to non-technical users, so a pipeline error carries a plain-language
``user_message``. The job runner stores that message on the job (never a raw stack trace or a
vendor API error like Vertex's "Model input cannot be empty") and logs the technical detail
server-side. Pure Python - no Flask - so the Flask-free services layer can raise these.
"""

GENERIC_USER_MESSAGE = (
    "Something went wrong while processing this document. Please try again; if it keeps "
    "failing, contact your administrator."
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


def user_facing_message(exc: Exception) -> str:
    """The message to show a user for a failed job: a PipelineError's own ``user_message``,
    otherwise a generic one (the technical detail is logged server-side, never shown raw)."""
    if isinstance(exc, PipelineError):
        return exc.user_message
    return GENERIC_USER_MESSAGE
