"""Job outcomes (#218): three of the five terminal states are not failures, and one number lied.

A backlog entry carried "36% of segment jobs did not complete cleanly" as a health signal for weeks;
recomputed it was 27.4%, and once restart orphans and transient Vertex failures were separated out
there was no alarming number left at all. The metric was wrong in BOTH directions - it overstated
the problem and it hid the only real signal inside two categories of non-problem.

So what is tested here is not a mapping table. It is that each of the three non-failures stays out
of the failure rate, that an unrecognized message is reported as itself rather than absorbed, and
that the taxonomy fails LOUD rather than optimistic - a health number that breaks toward "fine" is
worse than no health number.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "eval"))

from job_health import classify, render  # noqa: E402

from app.errors import (  # noqa: E402
    AI_BUSY_MESSAGE,
    AI_DAILY_QUOTA_MESSAGE,
    AI_DEADLINE_MESSAGE,
    AI_OVERSIZED_VALUE_MESSAGE,
    AI_REJECTED_MESSAGE,
    GENERIC_USER_MESSAGE,
    EmptyExtractionError,
    OcrUnavailableError,
)
from app.worker.failures import JOB_OUTCOMES, is_failure, job_outcome  # noqa: E402


def test_a_restart_orphan_is_not_a_failure():
    """`interrupted` means a restart killed the worker, so the row was reaped.

    The largest single contributor to the misleading number: 12 of 18 on one day in July, none in
    the seven weeks after. An operational event, and counting it as a pipeline failure is what made
    "one job in eight" out of a deploy.
    """
    assert job_outcome("interrupted") == "orphaned"
    assert not is_failure("orphaned")


def test_a_reviewer_pressing_stop_is_not_a_failure():
    """`cancelled` is a success by any reading, and `error` is deliberately left NULL for it."""
    assert job_outcome("cancelled", None) == "stopped"
    assert not is_failure("stopped")


def test_a_run_that_named_what_it_could_not_do_is_not_a_failure():
    """`needs_attention` kept every successful summary and reported the rest. Design, not fault."""
    assert job_outcome("needs_attention", "2 sub-documents could not be summarized") == "partial"
    assert not is_failure("partial")


def test_an_unfinished_job_is_not_an_outcome_at_all():
    """Excluded from the numerator AND the denominator.

    A rate taken over jobs that have not finished moves as they finish, so a burst of queued work
    would improve or worsen the pipeline's apparent health without anything having happened.
    """
    for state in ("queued", "running", "paused"):
        assert job_outcome(state) == "in_flight"
    assert not is_failure("in_flight")

    summary = classify([("done", None), ("running", None), ("queued", None)])
    assert summary["total"] == 3
    assert summary["finished"] == 1
    assert summary["failure_rate"] == 0.0


def test_each_persisted_error_message_lands_in_the_bucket_that_can_act_on_it():
    """The mapping works because `job.error` is written as `user_facing_message(exc)`.

    Matched against the same constants the writer uses, so this is a shared source rather than a
    guess at wording - which is what makes it safe to read months-old rows with it.
    """
    assert job_outcome("error", AI_BUSY_MESSAGE) == "failed_external"
    assert job_outcome("error", AI_DAILY_QUOTA_MESSAGE) == "failed_external"
    assert job_outcome("error", AI_DEADLINE_MESSAGE) == "failed_config"
    assert job_outcome("error", AI_REJECTED_MESSAGE) == "failed_config"
    assert job_outcome("error", AI_OVERSIZED_VALUE_MESSAGE) == "failed_config"
    assert job_outcome("error", OcrUnavailableError.user_message) == "failed_config"
    assert job_outcome("error", EmptyExtractionError.user_message) == "failed_content"
    assert job_outcome("error", GENERIC_USER_MESSAGE) == "failed_unknown"


def test_the_give_up_path_still_classifies_when_it_appends_a_sentence():
    """Why the match is `startswith` and not equality.

    `worker/tasks.py` composes the terminal message for a summarize that produced nothing as the
    friendly reason PLUS an explanation. Equality would drop the commonest real failure the pipeline
    has - a transient Vertex 429 - straight into `failed_unknown`.
    """
    composed = (
        f"{AI_BUSY_MESSAGE} No sub-documents could be summarized, so the run was stopped early."
    )

    assert job_outcome("error", composed) == "failed_external"


def test_an_unrecognized_message_is_reported_as_itself_not_absorbed():
    """A new error type must appear, not blend in.

    `failed_unknown` is the number worth watching, so the report names the message. If an unlisted
    error silently joined `failed_external`, the taxonomy would be producing a confident wrong
    answer - the exact failure mode #218 is about.
    """
    summary = classify([("error", "Something nobody has classified yet"), ("done", None)])

    assert summary["outcomes"]["failed_unknown"] == 1
    assert "Something nobody has classified yet" in summary["unrecognized"]
    assert "never came through user_facing_message" in render(summary)


def test_the_generic_message_is_a_recognized_unknown_not_a_gap_in_the_taxonomy():
    """The distinction the first real run got wrong, so it is pinned.

    `GENERIC_USER_MESSAGE` is what `user_facing_message` returns when IT does not recognize the
    exception either, so a job carrying it is a correctly classified unknown. The report listed it
    under "teach failures.py about this", which asked for a change to handle a message
    `failures.py` already handles.

    What belongs on that list is a string `user_facing_message` never produces - which means it
    reached `Job.error` without going through it. Measured on the box: 10 jobs carry the generic
    message and 7 carry raw vendor text, and only the second group is a finding.
    """
    summary = classify(
        [("error", GENERIC_USER_MESSAGE), ("error", "429 RESOURCE_EXHAUSTED. {'error': ...}")]
    )

    assert summary["outcomes"]["failed_unknown"] == 2, "both are unknown failures"
    assert list(summary["unrecognized"]) == ["429 RESOURCE_EXHAUSTED. {'error': ...}"]
    assert GENERIC_USER_MESSAGE not in render(summary)
    assert "never came through user_facing_message" in render(summary)


def test_a_state_this_code_has_never_seen_fails_loud_rather_than_optimistic():
    """An unknown state is `failed_unknown`, never `completed`.

    A health metric that breaks toward "fine" is worse than none: nobody investigates a green
    number. So the default direction is deliberately alarming.
    """
    assert job_outcome("some_future_state") == "failed_unknown"
    assert job_outcome(None) == "failed_unknown"
    assert is_failure(job_outcome(""))


def test_the_corrected_rate_is_lower_than_the_naive_one_on_the_measured_shape():
    """The correction, on the real distribution that prompted the issue.

    Segment jobs as measured 2026-08-31: 98 done, 18 interrupted, 17 error, 2 cancelled. The naive
    reading is 27.4%; once orphans and stops leave the numerator and the transient Vertex errors are
    named rather than pooled, what is left is a much smaller and far more actionable number.
    """
    rows = (
        [("done", None)] * 98
        + [("interrupted", None)] * 18
        + [("error", AI_BUSY_MESSAGE)] * 14
        + [("error", AI_DEADLINE_MESSAGE)] * 3
        + [("cancelled", None)] * 2
    )

    summary = classify(rows)

    assert round(summary["naive_rate"], 3) == 0.274, "the number the backlog entry carried"
    assert round(summary["failure_rate"], 3) == 0.126, "what the states actually say"
    assert summary["outcomes"]["failed_external"] == 14
    assert summary["outcomes"]["failed_config"] == 3
    assert summary["outcomes"]["orphaned"] == 18
    # And the report has to make the wrong number visible, or nobody stops quoting it.
    report = render(summary, "segment")
    assert "27.4%" in report
    assert "WRONG" in report
    assert "12.6%" in report


def test_the_report_names_every_bucket_it_counts_and_says_which_are_failures():
    """A count with no stated meaning is how the last one got misread.

    Every outcome present must carry its own one-line meaning, and the failure ones must be marked -
    so the reader does not have to know which states are faults to read the table.
    """
    summary = classify([("done", None), ("interrupted", None), ("error", AI_BUSY_MESSAGE)])

    report = render(summary, "all kinds")

    for outcome in ("completed", "orphaned", "failed_external"):
        assert outcome in report
        assert JOB_OUTCOMES[outcome] in report
    assert "FAIL failed_external" in report
    assert "FAIL orphaned" not in report
    assert "FAIL completed" not in report


def test_the_report_says_it_counts_runs_rather_than_documents():
    """The one place this scope legitimately differs from the corpus rule, so it must say so.

    `corpus.py` requires one row per distinct PDF for anything pooled - and that governs statements
    about DOCUMENTS. This measures runs the pipeline actually performed, where a record processed
    twice really did occupy it twice. Both are right; conflating them is how a number gets quoted at
    double (measured: 204 reviewer corrections where there were 102).
    """
    report = render(classify([("done", None)]))

    assert "sha256" in report
    assert "corpus.py" in report


def test_an_empty_scope_reports_nothing_rather_than_dividing_by_zero():
    summary = classify([])

    assert summary["total"] == 0
    assert summary["failure_rate"] == 0.0
    assert "(none)" in render(summary)


def test_rows_may_be_orm_objects_or_plain_tuples():
    """The pure function takes either, so the report is testable without a database session."""

    class _Row:
        state, error = "error", AI_BUSY_MESSAGE

    assert classify([_Row()])["outcomes"] == classify([("error", AI_BUSY_MESSAGE)])["outcomes"]
