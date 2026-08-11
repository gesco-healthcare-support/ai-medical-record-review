"""Unit tests for pipeline errors and the user-facing message mapping."""

from mrr_ai.errors import (
    GENERIC_USER_MESSAGE,
    EmptyExtractionError,
    OcrUnavailableError,
    PipelineError,
    user_facing_message,
)


def test_pipeline_errors_carry_user_messages():
    assert OcrUnavailableError().user_message
    assert EmptyExtractionError().user_message
    assert PipelineError().user_message == GENERIC_USER_MESSAGE


def test_technical_detail_kept_for_logs_but_not_leaked_to_user():
    exc = OcrUnavailableError("tesseract_cmd=/bad/path not found")
    assert "bad/path" in str(exc)  # available for server-side logs
    assert "bad/path" not in exc.user_message  # never shown to the user


def test_user_facing_message_maps_known_and_unknown():
    assert user_facing_message(OcrUnavailableError()) == OcrUnavailableError.user_message
    assert user_facing_message(EmptyExtractionError()) == EmptyExtractionError.user_message
    # An unexpected error (e.g. a raw vendor 400) never reaches the user verbatim.
    assert user_facing_message(ValueError("Model input cannot be empty")) == GENERIC_USER_MESSAGE
