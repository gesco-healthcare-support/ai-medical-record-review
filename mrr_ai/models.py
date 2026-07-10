"""Persistent data model: users, documents, jobs, rows, summaries, audit.

SegmentRow (raw model output, immutable) vs ReviewRow (human-corrected) is the
training-data flywheel: their diff per document, keyed by the owning Job's model and
prompt version, is the future fine-tuning dataset. PDFs stay on disk under
uploads/<user_id>/<document_id>.pdf; the DB holds paths, metadata, and row/summary
content only.

Document.status has a single writer - the job service - apart from the initial
"uploaded" and deletion, so state can never be corrupted from two places.
"""

import uuid
from datetime import UTC, datetime

from flask_security.models import fsqla_v3 as fsqla

from mrr_ai.extensions import db

fsqla.FsModels.set_db_info(db)


class Role(db.Model, fsqla.FsRoleMixin):
    pass


class User(db.Model, fsqla.FsUserMixin):
    # Display name collected at registration. Nullable at the DB level so the boot
    # migration can add it to databases with existing users (who have no name);
    # registration enforces it via a form validator instead.
    name = db.Column(db.String(255), nullable=True)
    # Marks the few accounts allowed into the admin area (category + prompt editing).
    # Not full RBAC: a single boolean, flipped via the ``flask admin`` CLI or the admin
    # UI. Added to existing databases through the boot ADD COLUMN path (default 0).
    is_admin = db.Column(db.Boolean, nullable=False, default=False)


def _utcnow():
    return datetime.now(UTC)


DOCUMENT_STATUSES = (
    "uploaded",
    "segmenting",
    "reviewing",
    "summarizing",
    "done",
    "error",
    "interrupted",
)
JOB_KINDS = ("segment", "summarize")
JOB_STATES = ("queued", "running", "done", "error", "interrupted")

# The row-dict shape shared with the segmentation/summarize engines and the editor.
ROW_FIELDS = ("start", "end", "category", "title", "date", "injury_date", "flag")


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    original_filename = db.Column(db.String(512), nullable=False)  # PHI-bearing: never log
    stored_path = db.Column(db.String(1024), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False, index=True)
    page_count = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="uploaded")
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    jobs = db.relationship(
        "Job", backref="document", cascade="all, delete-orphan", order_by="Job.id"
    )
    review_rows = db.relationship(
        "ReviewRow", backref="document", cascade="all, delete-orphan", order_by="ReviewRow.idx"
    )
    summaries = db.relationship(
        "Summary", backref="document", cascade="all, delete-orphan", order_by="Summary.idx"
    )

    @property
    def active_job(self):
        """The queued/running job, if any - at most one by job-service invariant."""
        return next((job for job in self.jobs if job.state in ("queued", "running")), None)

    def listing(self):
        """Landing-page shape; original_filename is shown to its owner only."""
        job = self.active_job
        return {
            "id": self.id,
            "original_filename": self.original_filename,
            "page_count": self.page_count,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "active_job": job.progress() if job else None,
        }


class Job(db.Model):
    """One pipeline run; doubles as provenance (model + prompt version) for its rows."""

    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(
        db.String(36), db.ForeignKey("documents.id"), nullable=False, index=True
    )
    kind = db.Column(db.String(16), nullable=False)
    state = db.Column(db.String(16), nullable=False, default="queued")
    stage = db.Column(db.String(32), nullable=False, default="starting")
    current = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=True)
    model = db.Column(db.String(64), nullable=False)
    prompt_version = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    segment_rows = db.relationship(
        "SegmentRow", backref="job", cascade="all, delete-orphan", order_by="SegmentRow.idx"
    )

    def progress(self):
        return {
            "kind": self.kind,
            "state": self.state,
            "stage": self.stage,
            "current": self.current,
            "total": self.total,
            "error": self.error,
        }


class SegmentRow(db.Model):
    """RAW segmentation output - immutable training INPUT. Never edited by the UI."""

    __tablename__ = "segment_rows"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False, index=True)
    idx = db.Column(db.Integer, nullable=False)
    start = db.Column(db.Integer, nullable=False)
    end = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(8), nullable=False)
    title = db.Column(db.String(512), nullable=False, default="-")
    date = db.Column(db.String(16), nullable=False, default="-")
    injury_date = db.Column(db.String(16), nullable=False, default="-")
    flag = db.Column(db.String(4), nullable=False, default="-")
    suggest_merge = db.Column(db.Boolean, nullable=False, default=False)

    def as_row(self):
        row = {field: getattr(self, field) for field in ROW_FIELDS}
        row["suggest_merge"] = self.suggest_merge
        return row


class ReviewRow(db.Model):
    """Human-corrected working set - the training LABEL and the summarize input."""

    __tablename__ = "review_rows"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(
        db.String(36), db.ForeignKey("documents.id"), nullable=False, index=True
    )
    idx = db.Column(db.Integer, nullable=False)
    start = db.Column(db.Integer, nullable=False)
    end = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(8), nullable=False)
    title = db.Column(db.String(512), nullable=False, default="-")
    date = db.Column(db.String(16), nullable=False, default="-")
    injury_date = db.Column(db.String(16), nullable=False, default="-")
    flag = db.Column(db.String(4), nullable=False, default="-")
    # Carried from segmentation so the editor's merge-suggestion chips survive a
    # reload; cleared naturally as the user edits and the editor saves its state.
    suggest_merge = db.Column(db.Boolean, nullable=False, default=False)
    # Whether this row is sent to summarization; lets the reviewer scope a run to
    # exactly the documents that matter without deleting the rest.
    include = db.Column(db.Boolean, nullable=False, default=True)

    def as_row(self):
        row = {field: getattr(self, field) for field in ROW_FIELDS}
        row["suggest_merge"] = self.suggest_merge
        row["include"] = self.include
        return row


class Summary(db.Model):
    __tablename__ = "summaries"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(
        db.String(36), db.ForeignKey("documents.id"), nullable=False, index=True
    )
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    idx = db.Column(db.Integer, nullable=False)
    # title/date/text are the RAW model output and stay immutable (like SegmentRow,
    # human edits vs model output is future training signal); reviewer changes land
    # in the edited_* columns and win wherever the summary is displayed or exported.
    title = db.Column(db.String(512), nullable=False)
    date = db.Column(db.String(16), nullable=False, default="-")
    text = db.Column(db.Text, nullable=False)
    # The exact extracted text the model was given for this summary - the INPUT half
    # of the fine-tuning pair. Recomputing it later would silently drift with OCR
    # versions, so it is captured at generation time. Never shown in the UI.
    source_text = db.Column(db.Text, nullable=True)
    edited_title = db.Column(db.String(512), nullable=True)
    edited_date = db.Column(db.String(16), nullable=True)
    edited_text = db.Column(db.Text, nullable=True)
    # Excluded summaries stay visible in the UI (dimmed) but never reach the Word export.
    excluded = db.Column(db.Boolean, nullable=False, default=False)
    manual_check = db.Column(db.Boolean, nullable=False, default=False)
    # Snapshot of the summarized row, so summaries stay interpretable even if the
    # review rows are edited again afterward.
    row_start = db.Column(db.Integer, nullable=False)
    row_end = db.Column(db.Integer, nullable=False)
    row_category = db.Column(db.String(8), nullable=False)

    def effective_title(self):
        return self.edited_title if self.edited_title is not None else self.title

    def effective_date(self):
        return self.edited_date if self.edited_date is not None else self.date

    def effective_text(self):
        return self.edited_text if self.edited_text is not None else self.text

    def listing(self):
        return {
            "idx": self.idx,
            "summaryTitle": self.effective_title(),
            "summaryDate": self.effective_date(),
            "summaryText": self.effective_text(),
            "manualCheck": self.manual_check,
            "excluded": self.excluded,
            "edited": any(
                value is not None
                for value in (self.edited_title, self.edited_date, self.edited_text)
            ),
            "row": {"start": self.row_start, "end": self.row_end, "category": self.row_category},
        }


class AuditLog(db.Model):
    """Who did what to which document, when. References ids only - never filenames."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    action = db.Column(db.String(32), nullable=False)
    document_id = db.Column(db.String(36), nullable=True)
    at = db.Column(db.DateTime, nullable=False, default=_utcnow)
