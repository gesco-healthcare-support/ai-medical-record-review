"""Failure taxonomy + control signals for resumable summarize (item 7).

Pure-Python: no DB, no Vertex. Proves classify_failure splits the seam's re-raised exceptions into
"transient" (wait-and-retry-forever) vs "permanent" (needs attention), that reason_for renders a
calm user-facing reason, and that the JobPaused/JobNeedsAttention signals carry their payloads.
"""

import httpx
from google.genai import errors

from app.errors import (
    AI_BUSY_MESSAGE,
    AI_DAILY_QUOTA_MESSAGE,
    AI_REJECTED_MESSAGE,
    GENERIC_USER_MESSAGE,
    EmptyExtractionError,
    user_facing_message,
)
from app.worker.failures import (
    JobNeedsAttention,
    JobPaused,
    classify_failure,
    reason_for,
)


def _client_error(code: int, message: str) -> errors.ClientError:
    return errors.ClientError(code, {"error": {"code": code, "message": message, "status": "X"}})


def _server_error(code: int = 503) -> errors.ServerError:
    return errors.ServerError(code, {"error": {"code": code, "message": "model overloaded"}})


# --- classify_failure -------------------------------------------------------------------------


def test_rate_limit_429_is_transient():
    assert classify_failure(_client_error(429, "Resource exhausted, please retry")) == "transient"


def test_server_5xx_is_transient():
    assert classify_failure(_server_error(503)) == "transient"


def test_transport_disconnect_is_transient():
    assert classify_failure(httpx.ConnectError("connection reset")) == "transient"


def test_daily_quota_429_is_permanent():
    # A per-day / free-tier 429 will not clear within a short retry window.
    assert classify_failure(_client_error(429, "Quota exceeded: PerDay limit")) == "permanent"


def test_auth_client_error_is_permanent():
    assert classify_failure(_client_error(403, "permission denied")) == "permanent"


def test_empty_extraction_is_permanent():
    assert classify_failure(EmptyExtractionError("no OCR text for pages 1-2")) == "permanent"


def test_unknown_exception_is_permanent():
    assert classify_failure(ValueError("unexpected")) == "permanent"


# --- reason_for -------------------------------------------------------------------------------


def test_reason_for_pipeline_error_uses_user_message():
    assert "readable text" in reason_for(EmptyExtractionError("no text")).lower()


def test_reason_for_daily_quota_mentions_daily():
    assert "daily" in reason_for(_client_error(429, "PerDay quota")).lower()


def test_reason_for_unknown_is_generic():
    assert reason_for(ValueError("x")) == GENERIC_USER_MESSAGE


# --- control signals --------------------------------------------------------------------------


def test_job_paused_carries_progress():
    sig = JobPaused(delay=60, done=3, total=10)
    assert (sig.delay, sig.done, sig.total) == (60, 3, 10)


def test_job_needs_attention_carries_rows_and_message():
    rows = [{"idx": 4, "pages": "5-6", "reason": "no readable text"}]
    sig = JobNeedsAttention("2 of 10 documents could not be summarized", rows)
    assert sig.message.startswith("2 of 10")
    assert sig.rows[0]["idx"] == 4


# --- user_facing_message: friendly genai translation (T7) -------------------------------------


def test_user_facing_message_server_error_is_busy():
    assert user_facing_message(_server_error(503)) == AI_BUSY_MESSAGE


def test_user_facing_message_transient_429_is_busy():
    assert user_facing_message(_client_error(429, "Resource exhausted, retry")) == AI_BUSY_MESSAGE


def test_user_facing_message_daily_quota():
    assert user_facing_message(_client_error(429, "PerDay quota")) == AI_DAILY_QUOTA_MESSAGE


def test_user_facing_message_auth_is_rejected():
    assert user_facing_message(_client_error(403, "permission denied")) == AI_REJECTED_MESSAGE


def test_user_facing_message_unknown_is_generic():
    assert user_facing_message(ValueError("x")) == GENERIC_USER_MESSAGE


def test_reason_for_agrees_with_user_facing_message():
    # A per-row reason and a whole-job terminal message must render identically for the same error.
    for exc in (
        _server_error(503),
        _client_error(429, "PerDay quota"),
        _client_error(403, "denied"),
        EmptyExtractionError("no text"),
        ValueError("x"),
    ):
        assert reason_for(exc) == user_facing_message(exc)


def test_httpx_read_timeout_is_transient():
    # A genai HTTP timeout surfaces as httpx.ReadTimeout (a TransportError) -> retryable.
    assert classify_failure(httpx.ReadTimeout("timed out")) == "transient"
