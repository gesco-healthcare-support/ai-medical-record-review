"""P3a: model helper methods (progress/active_job/listing/as_row/effective_*), in memory.

Constructed as transient instances (no DB) - these methods are pure Python over the ORM
attributes. Column defaults only apply at flush, so fields the tests care about are set explicitly.
"""

from datetime import datetime

from app.models import Document, Job, ReviewRow, SegmentRow, Summary


def test_job_progress():
    job = Job(kind="segment", state="running", stage="segmenting", current=2, total=10)
    assert job.progress() == {
        "kind": "segment",
        "state": "running",
        "stage": "segmenting",
        "current": 2,
        "total": 10,
        "error": None,
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
    assert row["start"] == 1 and row["category"] == "1" and row["suggest_merge"] is True
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
    assert review_row["include"] is False and review_row["suggest_merge"] is False


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
