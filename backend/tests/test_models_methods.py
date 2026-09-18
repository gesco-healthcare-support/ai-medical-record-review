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


def test_listing_does_not_report_a_correction_the_audit_discarded():
    """WHEN a rewrite is rejected, THE SYSTEM SHALL not report the summary as corrected.

    `verified_text` is left None when `_drops_required_headings` or `_drops_deposition_structure`
    refuses a rewrite - the guards that stop the audit flattening a required structure - and the
    issues are stored either way. So `verified and verify_issues` reported "AI verify pass corrected
    this summary" over a body the audit had not touched, which is the opposite of what happened.

    Measured on the box 2026-09-18: 81 summaries in this state, every one delivered, the newest that
    same day. The count grows with however often the rewrite is refused, and #343 widened exactly
    that - on a self-hosted model it fires on every long summary.
    """
    summary = Summary(
        idx=0, title="T", date="-", text="RAW", row_start=1, row_end=2, row_category="9"
    )
    summary.verified = True
    summary.verify_issues = [{"type": "date", "detail": "d"}]
    summary.verified_text = None  # the rewrite was rejected; the raw body ships

    listing = summary.listing()
    assert listing["verifyChanged"] is False
    assert listing["verifyKeptRaw"] is True
    assert listing["summaryText"] == "RAW"


def test_listing_still_reports_a_correction_the_audit_did_apply():
    """The other side of it, so the fix is a narrowing rather than a silencing.

    The flag has to keep meaning something: a body the audit DID rewrite is the ordinary case (1,727
    of 2,981 audited on the box) and is exactly what the chip was written for.
    """
    summary = Summary(
        idx=0, title="T", date="-", text="RAW", row_start=1, row_end=2, row_category="1"
    )
    summary.verified = True
    summary.verify_issues = [{"type": "unsupported", "detail": "d"}]
    summary.verified_text = "CORRECTED"

    listing = summary.listing()
    assert listing["verifyChanged"] is True
    assert listing["verifyKeptRaw"] is False
    assert listing["summaryText"] == "CORRECTED"


def test_the_two_verify_outcomes_cannot_both_be_true():
    """WHEN the audit reports an outcome, THE SYSTEM SHALL report exactly one of the two.

    They are read as opposites on the card - "this was corrected" against "check this yourself" -
    so a row asserting both would tell a reviewer nothing. Checked across every combination of the
    three columns the pair is derived from rather than on one example, because the pair is derived
    and a derivation is where an overlap would appear.
    """
    for verified in (True, False):
        for issues in ([], [{"type": "vitals", "detail": "d"}]):
            for fixed in (None, "CORRECTED"):
                summary = Summary(
                    idx=0,
                    title="T",
                    date="-",
                    text="RAW",
                    row_start=1,
                    row_end=2,
                    row_category="1",
                )
                summary.verified = verified
                summary.verify_issues = issues
                summary.verified_text = fixed
                listing = summary.listing()
                assert not (listing["verifyChanged"] and listing["verifyKeptRaw"]), (
                    f"both flags true for verified={verified} issues={bool(issues)} "
                    f"verified_text={fixed!r}"
                )


def test_an_unaudited_summary_reports_neither_outcome():
    """The guard that keeps both flags off every card when the audit never ran.

    `verify_issues` is NULL and `verified` False for a summary nobody audited, which is most of a
    record when `summary_verify` is off. Neither flag may fire there: the first would claim a
    correction and the second would send a reviewer to check something against a pass that was
    never requested.
    """
    summary = Summary(
        idx=0, title="T", date="-", text="RAW", row_start=1, row_end=2, row_category="1"
    )
    listing = summary.listing()
    assert listing["verifyChanged"] is False
    assert listing["verifyKeptRaw"] is False
    assert listing["verifyIssues"] == []


def test_every_table_that_references_a_document_is_cascaded_by_it():
    """GUARD on the CLASS of defect, not the instance.

    `page_texts` shipped in #77 with a foreign key to `documents` and no relationship here, and
    for six weeks no record that had been OCR'd could be deleted at all - the ORM removed the
    jobs, rows and summaries and Postgres then refused the parent row. Nothing failed at review
    time and nothing failed in CI, because the one delete test uploads a document and deletes it
    before anything writes page text.

    So this asserts the RULE rather than the four tables: every mapped table with a foreign key
    to `documents.id` must either be cascaded from `Document` or carry ON DELETE CASCADE in the
    database. A fifth child table added tomorrow fails here on the day it lands rather than in
    front of a reviewer.

    The delete_orphan half matters as much as delete: without it a detached child is orphaned
    rather than removed, which is the same foreign key waiting to fire.
    """
    document_table = Document.__table__

    cascaded = set()
    for rel in Document.__mapper__.relationships:
        if "delete" in rel.cascade and "delete-orphan" in rel.cascade:
            cascaded.add(rel.mapper.class_.__table__.name)

    unprotected = []
    for mapper in Document.registry.mappers:
        table = mapper.class_.__table__
        for fk in table.foreign_keys:
            if fk.column.table is not document_table:
                continue
            db_cascade = (fk.constraint.ondelete or "").upper() == "CASCADE"
            if table.name not in cascaded and not db_cascade:
                unprotected.append(f"{table.name}.{fk.parent.name}")

    assert not unprotected, (
        f"these reference documents.id with no cascade, so deleting a document will fail: "
        f"{sorted(set(unprotected))}"
    )
