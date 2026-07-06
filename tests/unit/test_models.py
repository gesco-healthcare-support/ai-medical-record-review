"""T2: model round-trips, cascades, helpers, and the SQLite WAL pragma.

All data is synthetic - no real names, dates of birth, or record content.
"""

from sqlalchemy import text


def _make_document(db, models, user_id, **overrides):
    fields = dict(
        user_id=user_id,
        original_filename="synthetic-case.pdf",
        stored_path="uploads/1/abc.pdf",
        sha256="0" * 64,
        page_count=10,
    )
    fields.update(overrides)
    document = models.Document(**fields)
    db.session.add(document)
    db.session.commit()
    return document


def test_document_job_rows_roundtrip(app, user):
    from mrr_ai import models
    from mrr_ai.extensions import db

    with app.app_context():
        document = _make_document(db, models, user.id)
        job = models.Job(
            document_id=document.id, kind="segment", model="gemini-2.5-flash", prompt_version="2"
        )
        db.session.add(job)
        db.session.commit()

        db.session.add(
            models.SegmentRow(
                job_id=job.id,
                idx=0,
                start=1,
                end=5,
                category="1",
                title="PROGRESS REPORT",
                date="01/15/2024",
                suggest_merge=True,
            )
        )
        db.session.add(
            models.ReviewRow(
                document_id=document.id,
                idx=0,
                start=1,
                end=5,
                category="1",
                title="PROGRESS REPORT",
                date="01/15/2024",
            )
        )
        db.session.commit()

        loaded = db.session.get(models.Document, document.id)
        assert loaded.status == "uploaded"
        assert loaded.jobs[0].segment_rows[0].as_row()["suggest_merge"] is True
        row = loaded.review_rows[0].as_row()
        assert row == {
            "start": 1,
            "end": 5,
            "category": "1",
            "title": "PROGRESS REPORT",
            "date": "01/15/2024",
            "injury_date": "-",
            "flag": "-",
            "suggest_merge": False,
        }


def test_cascade_delete_document(app, user):
    from mrr_ai import models
    from mrr_ai.extensions import db

    with app.app_context():
        document = _make_document(db, models, user.id)
        job = models.Job(document_id=document.id, kind="segment", model="m", prompt_version="2")
        db.session.add(job)
        db.session.commit()
        db.session.add(models.SegmentRow(job_id=job.id, idx=0, start=1, end=2, category="1"))
        db.session.add(
            models.ReviewRow(document_id=document.id, idx=0, start=1, end=2, category="1")
        )
        db.session.add(
            models.Summary(
                document_id=document.id,
                job_id=job.id,
                idx=0,
                title="t",
                text="s",
                row_start=1,
                row_end=2,
                row_category="1",
            )
        )
        db.session.commit()

        db.session.delete(document)
        db.session.commit()
        for model in (models.Job, models.SegmentRow, models.ReviewRow, models.Summary):
            assert db.session.query(model).count() == 0


def test_active_job_and_listing(app, user):
    from mrr_ai import models
    from mrr_ai.extensions import db

    with app.app_context():
        document = _make_document(db, models, user.id)
        assert document.active_job is None
        done = models.Job(
            document_id=document.id, kind="segment", state="done", model="m", prompt_version="2"
        )
        running = models.Job(
            document_id=document.id,
            kind="summarize",
            state="running",
            stage="summarizing",
            current=3,
            total=9,
            model="m",
            prompt_version="2",
        )
        db.session.add_all([done, running])
        db.session.commit()

        assert document.active_job is running
        listing = document.listing()
        assert listing["active_job"]["stage"] == "summarizing"
        assert listing["page_count"] == 10
        assert listing["status"] == "uploaded"


def test_sqlite_runs_in_wal_mode(app):
    from mrr_ai.extensions import db

    with app.app_context():
        mode = db.session.execute(text("PRAGMA journal_mode")).scalar()
        assert mode == "wal"
