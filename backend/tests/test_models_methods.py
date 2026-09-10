"""P3a: model helper methods (progress/active_job/listing/as_row/effective_*), in memory.

Constructed as transient instances (no DB) - these methods are pure Python over the ORM
attributes. Column defaults only apply at flush, so fields the tests care about are set explicitly.
"""

from datetime import datetime

from app.models import Document, Job, ReviewRow, SegmentRow, Summary


def test_job_progress():
    job = Job(kind="segment", state="running", stage="segmenting", current=2, total=10)
    assert job.progress() == {
        # None here only because this instance is transient (the pk is assigned at flush). The field
        # exists so the UI can address a specific job when cancelling it, rather than "whatever is
        # active on this document", which could kill a job that started after the button rendered.
        "id": None,
        "kind": "segment",
        "state": "running",
        "stage": "segmenting",
        "current": 2,
        "total": 10,
        "error": None,
        "attention": None,
    }


def test_document_active_job_and_listing():
    doc = Document(
        id="d1",
        original_filename="synthetic.pdf",
        page_count=3,
        status="reviewing",
        created_at=datetime(2026, 7, 15, 12, 0, 0),
        updated_at=datetime(2026, 7, 15, 12, 0, 0),
    )
    assert doc.active_job is None
    listing = doc.listing()
    assert listing["id"] == "d1"
    assert listing["original_filename"] == "synthetic.pdf"
    assert listing["active_job"] is None

    doc.jobs = [Job(kind="summarize", state="running", stage="summarizing", current=1, total=4)]
    assert doc.active_job is not None
    assert doc.listing()["active_job"]["kind"] == "summarize"


def test_segment_and_review_as_row():
    seg = SegmentRow(
        idx=0,
        start=1,
        end=2,
        category="1",
        title="A",
        date="-",
        injury_date="-",
        flag="-",
        suggest_merge=True,
    )
    row = seg.as_row()
    assert row["start"] == 1
    assert row["category"] == "1"
    assert row["suggest_merge"] is True
    assert "include" not in row  # SegmentRow rows carry no include flag

    rev = ReviewRow(
        idx=0,
        start=1,
        end=2,
        category="1",
        title="A",
        date="-",
        injury_date="-",
        flag="-",
        suggest_merge=False,
        include=False,
    )
    review_row = rev.as_row()
    assert review_row["include"] is False
    assert review_row["suggest_merge"] is False


def test_summary_effective_and_listing():
    summary = Summary(
        idx=0,
        title="Raw",
        date="01/02/2020",
        text="raw text",
        row_start=1,
        row_end=2,
        row_category="3",
    )
    assert summary.effective_title() == "Raw"
    summary.edited_title = "Edited"
    assert summary.effective_title() == "Edited"

    listing = summary.listing()
    assert listing["summaryTitle"] == "Edited"
    assert listing["edited"] is True
    assert listing["row"] == {"start": 1, "end": 2, "category": "3"}


def test_summary_effective_text_precedence_and_verify_flag():
    summary = Summary(
        idx=0,
        title="T",
        date="-",
        text="RAW",
        row_start=1,
        row_end=2,
        row_category="1",
    )
    assert summary.effective_text() == "RAW"  # raw model output when nothing else set

    summary.verified = True
    summary.verified_text = "VERIFIED"
    summary.verify_issues = [{"type": "unsupported", "detail": "x"}]
    assert summary.effective_text() == "VERIFIED"  # AI fix wins over raw
    listing = summary.listing()
    assert listing["summaryText"] == "VERIFIED"
    assert listing["verifyChanged"] is True

    summary.edited_text = "HUMAN"
    assert summary.effective_text() == "HUMAN"  # reviewer edit wins over the AI fix
    assert summary.listing()["summaryText"] == "HUMAN"


def test_listing_says_when_an_audit_was_requested_and_did_not_complete():
    """WHEN the audit was asked for and did not run, THE SYSTEM SHALL say so on the wire.

    `verified` cannot express this by itself. `audit_model` records that an audit was REQUESTED (it
    is set from the `verify` setting) and `verified` records the OUTCOME, so a client holding only
    the second cannot separate a failed audit from one nobody asked for. The Summaries tab reads
    `verifyChanged`, which is False for a clean audit AND for a failed one - so a summary nothing
    checked rendered exactly like a summary that passed.

    Measured on the box 2026-09-10 over the 1,155 summaries that requested an audit: 36 did not get
    one, and all 36 were delivered (`excluded` false).
    """
    summary = Summary(
        idx=0, title="T", date="-", text="RAW", row_start=1, row_end=2, row_category="9"
    )
    summary.audit_model = "gemini-2.5-pro"

    summary.verified = False
    assert summary.listing()["verifyFailed"] is True

    # Completed with nothing to fix. Silent, and no longer confusable with the case above.
    summary.verified = True
    assert summary.listing()["verifyFailed"] is False
    assert summary.listing()["verifyChanged"] is False


def test_listing_is_silent_when_no_audit_was_ever_requested():
    """The guard that keeps the flag off every card, and the reason it reads two columns.

    `verified` is False when the audit was never asked for as well as when it failed.
    `summary_verify` defaults True today, but it is a SETTING: derive the flag from `not verified`
    alone and turning the audit off would flag every summary in the record as unchecked.
    """
    summary = Summary(
        idx=0, title="T", date="-", text="RAW", row_start=1, row_end=2, row_category="1"
    )
    assert summary.audit_model is None
    for verified in (False, True):
        summary.verified = verified
        assert summary.listing()["verifyFailed"] is False
