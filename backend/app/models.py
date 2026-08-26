"""SQLAlchemy models for the FastAPI backend.

Ported from the Flask mrr_ai/models.py. The nine domain tables are copied verbatim (they were
already framework-agnostic). User/Role/roles_users MIRROR the exact Flask-Security fsqla_v3
schema introspected from the live DB (PRAGMA table_info) so the SQLite -> Postgres migration is
1:1 and existing argon2id logins survive; most fsqla columns (MFA/WebAuthn/unified-signin) are
unused today but kept for byte-compatibility per decision 2026-07-14.

Column-name notes: the fsqla default table name is "user" and columns "end"/"date" are SQL
reserved-ish words; SQLAlchemy quotes them automatically, and the names are kept identical to
match the source schema.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTable
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.db import Base


def _uuid() -> str:
    import uuid

    return str(uuid.uuid4())


def _uniquifier() -> str:
    """A fresh fs_uniquifier for FastAPI-Users-created accounts. The column is retained from the
    Flask-Security schema (kept NOT NULL + unique for the 1:1 migration) but is no longer the
    session identity; FastAPI-Users keys sessions by user id via the access_token table."""
    import uuid

    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- auth (Flask-Security fsqla_v3 schema, mirrored exactly) --------------------------------

roles_users = Table(
    "roles_users",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("user.id")),
    Column("role_id", Integer, ForeignKey("role.id")),
)


class Role(Base):
    __tablename__ = "role"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), unique=True, nullable=False)
    description = Column(String(255))
    permissions = Column(Text)
    update_datetime = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    username = Column(String(255), unique=True)
    password = Column(String(255))  # argon2id hash, carried over from Flask-Security
    active = Column(Boolean, nullable=False, default=True)
    fs_uniquifier = Column(String(64), unique=True, nullable=False, default=_uniquifier)
    fs_webauthn_user_handle = Column(String(64))
    confirmed_at = Column(DateTime)
    last_login_at = Column(DateTime)
    current_login_at = Column(DateTime)
    last_login_ip = Column(String(64))
    current_login_ip = Column(String(64))
    login_count = Column(Integer)
    tf_primary_method = Column(String(64))
    tf_totp_secret = Column(String(255))
    tf_phone_number = Column(String(128))
    mf_recovery_codes = Column(Text)
    us_totp_secrets = Column(Text)
    us_phone_number = Column(String(128))
    create_datetime = Column(DateTime, nullable=False, default=_utcnow)
    update_datetime = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    # Project-added columns.
    name = Column(String(255))
    is_admin = Column(Boolean, nullable=False, default=False)
    # FastAPI-Users requires an is_verified attribute; registration is non-confirmable so this
    # stays False and is never gated on (we depend on current_active_user, not _verified).
    is_verified = Column(Boolean, nullable=False, default=False)

    roles = relationship("Role", secondary=roles_users)

    # FastAPI-Users reads/writes these attribute names; map them onto the existing fsqla columns
    # so the migrated data is reused verbatim (superuser == our admin flag). Synonyms are writable,
    # so the adapter's create/update set the underlying columns correctly.
    hashed_password = synonym("password")
    is_active = synonym("active")
    is_superuser = synonym("is_admin")


class AccessToken(SQLAlchemyBaseAccessTokenTable[int], Base):
    """Opaque server-side session token (FastAPI-Users DatabaseStrategy). The base provides
    `token` (PK) + `created_at`; we add the user FK. Postgres stays the source of truth for
    sessions, which allows server-side revocation on logout. Carries no PHI."""

    __tablename__ = "access_token"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="cascade"), nullable=False
    )


# --- domain (copied from mrr_ai/models.py) --------------------------------------------------

ROW_FIELDS = ("start", "end", "category", "title", "date", "injury_date", "flag")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    original_filename = Column(String(512), nullable=False)  # PHI-bearing: never log
    stored_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    page_count = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="uploaded")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    # Report-header fields: auto-extracted on identify, reviewer-editable (all nullable).
    patient_first_name = Column(String(255))
    patient_last_name = Column(String(255))
    patient_dob = Column(String(32))
    law_firm = Column(String(512))

    jobs = relationship("Job", backref="document", cascade="all, delete-orphan", order_by="Job.id")
    review_rows = relationship(
        "ReviewRow", backref="document", cascade="all, delete-orphan", order_by="ReviewRow.idx"
    )
    summaries = relationship(
        "Summary", backref="document", cascade="all, delete-orphan", order_by="Summary.idx"
    )

    @property
    def active_job(self):
        """The in-flight job, if any - at most one by the job-service invariant. `paused` counts
        as in-flight (a summarize run awaiting its delayed resume): it blocks a second job and
        keeps the UI on the progress view."""
        return next(
            (job for job in self.jobs if job.state in ("queued", "running", "paused")), None
        )

    def listing(self):
        """Landing-page shape; original_filename is shown to its owner only."""
        job = self.active_job
        first = self.patient_first_name or ""
        last = self.patient_last_name or ""
        return {
            "id": self.id,
            "original_filename": self.original_filename,
            "page_count": self.page_count,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "active_job": job.progress() if job else None,
            "patient_first_name": first,
            "patient_last_name": last,
            "patient_name": (first + " " + last).strip(),
            "patient_dob": self.patient_dob or "",
            "law_firm": self.law_firm or "",
        }


class Job(Base):
    __tablename__ = "jobs"

    # One active (queued/running) job per document, enforced at the DB level so the invariant
    # holds across RQ worker processes (the old in-process lock cannot). A racing second enqueue
    # violates this -> IntegrityError -> the 409 (see app/services/jobs.py).
    __table_args__ = (
        Index(
            "uq_one_active_job_per_document",
            "document_id",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running', 'paused')"),
            sqlite_where=text("state IN ('queued', 'running', 'paused')"),
        ),
    )

    id = Column(Integer, primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    kind = Column(String(16), nullable=False)
    state = Column(String(16), nullable=False, default="queued")
    stage = Column(String(32), nullable=False, default="starting")
    current = Column(Integer, nullable=False, default=0)
    total = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    # `model` is the BODY model for a summarize job (and the only model for every other kind).
    # `title_model` / `audit_model` are the other two summarize calls, resolved ONCE here at job
    # creation via Settings.model_for, so a job resumed after a config change cannot switch models
    # mid-document. NULL on jobs created before 2026-08-06 and on non-summarize kinds, so read them
    # as `job.title_model or job.model` - which is exactly what those older jobs actually used. No
    # backfill: an invented value would later be indistinguishable from a recorded one.
    model = Column(String(64), nullable=False)
    title_model = Column(String(64))
    audit_model = Column(String(64))
    # `prompt_version` is a HAND-MAINTAINED constant and went unbumped through a dozen prompt PRs.
    # `prompt_fingerprint` hashes the prompt text AS RESOLVED (DB-first, code fallback), so it moves
    # on its own. Prefer the fingerprint; prompt_version stays readable for historical rows.
    prompt_version = Column(String(16), nullable=False)
    prompt_fingerprint = Column(String(16))
    # The commit the image was built from. Completes the provenance pair: `prompt_fingerprint` says
    # WHICH PROMPT ran, `build_sha` says WHICH CODE ran - and the code half matters because the
    # prompt a row was generated from is assembled by templates the fingerprint does not hash, plus
    # per-row blocks appended after it is computed. "unknown" when the image was built without the
    # GIT_SHA arg; NULL on every job created before 2026-08-11, which is not backfilled because an
    # inferred value would later be indistinguishable from a recorded one.
    build_sha = Column(String(40))
    catalog_revision = Column(Integer)
    # Resumable summarize (item 7): the CURRENT RQ job id (differs from the db id after a delayed
    # requeue, so orphan recovery correlates by this); the pause/resume cycle count (observability
    # only - transient 429s retry forever); and, when a run ends `needs_attention`, the reason +
    # the sub-documents that could not be summarized (non-PHI: idx + page range + a friendly reason).
    rq_job_id = Column(String(64))
    attempts = Column(Integer, nullable=False, server_default="0", default=0)
    attention = Column(JSON)
    # The reviewer pressed Stop. The worker also learns this from a Redis key, because the retry
    # backoff has no session - but this column is the durable record: it survives a Redis flush and
    # distinguishes a job that was ASKED to stop from one that died and was reaped.
    cancel_requested = Column(Boolean, nullable=False, server_default="false", default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    segment_rows = relationship(
        "SegmentRow", backref="job", cascade="all, delete-orphan", order_by="SegmentRow.idx"
    )

    def progress(self):
        return {
            # The id is here so the UI can address this job - the cancel endpoint is scoped by job id
            # rather than by document, so that pressing Stop cannot kill a DIFFERENT job that started
            # in the moment between the render and the click. Not PHI: a surrogate integer key.
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "stage": self.stage,
            "current": self.current,
            "total": self.total,
            "error": self.error,
            # The per-row failure detail (idx, page range, reason) a needs_attention run recorded,
            # so the UI can list + highlight exactly which sub-documents to fix/exclude. Null when
            # the run had no permanent per-row failures.
            "attention": self.attention,
        }


class SegmentRow(Base):
    __tablename__ = "segment_rows"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    idx = Column(Integer, nullable=False)
    start = Column(Integer, nullable=False)
    end = Column(Integer, nullable=False)
    category = Column(String(8), nullable=False)
    title = Column(String(512), nullable=False, default="-")
    date = Column(String(16), nullable=False, default="-")
    injury_date = Column(Text, nullable=False, default="-")  # multi-DOI: "MM/DD/YYYY, MM/DD/YYYY"
    flag = Column(String(4), nullable=False, default="-")
    suggest_merge = Column(Boolean, nullable=False, default=False)

    def as_row(self):
        row = {field: getattr(self, field) for field in ROW_FIELDS}
        row["suggest_merge"] = self.suggest_merge
        return row


class ReviewRow(Base):
    __tablename__ = "review_rows"

    id = Column(Integer, primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    idx = Column(Integer, nullable=False)
    start = Column(Integer, nullable=False)
    end = Column(Integer, nullable=False)
    category = Column(String(8), nullable=False)
    title = Column(String(512), nullable=False, default="-")
    date = Column(String(16), nullable=False, default="-")
    injury_date = Column(Text, nullable=False, default="-")  # multi-DOI: "MM/DD/YYYY, MM/DD/YYYY"
    flag = Column(String(4), nullable=False, default="-")
    suggest_merge = Column(Boolean, nullable=False, default=False)
    include = Column(Boolean, nullable=False, default=True)
    # Duplicate clustering (pre-summarize): the dedup job stores each row's full OCR text once
    # (reused by the Duplicates view + the AI-confirm call), and groups confirmed re-scans of the
    # same document under a per-document `dupe_group` int (null = singleton). The reviewer marks one
    # copy `dupe_primary` and dismisses false clusters (`dupe_dismissed`).
    source_text = Column(Text)
    dupe_group = Column(Integer, index=True)
    dupe_primary = Column(Boolean, nullable=False, default=False)
    dupe_dismissed = Column(Boolean, nullable=False, default=False)
    # The cluster's lowest pairwise character similarity (0-1), stored on every member: ~1.0 means
    # re-scans of ONE document, a low value means a recurring form series that merely shares a
    # template (measured on real records: 1.000 vs 0.219). Null for a singleton row, or for a row
    # grouped before this column existed.
    dupe_similarity = Column(Float)

    def as_row(self):
        row = {field: getattr(self, field) for field in ROW_FIELDS}
        row["suggest_merge"] = self.suggest_merge
        row["include"] = self.include
        row["dupe_group"] = self.dupe_group
        row["dupe_primary"] = self.dupe_primary
        row["dupe_dismissed"] = self.dupe_dismissed
        # The duplicate check's OCR of exactly these pages, so summarize_row can reuse it instead of
        # extracting the same text again. _store_rows only carries it across for an unchanged
        # (start, end), so it can never describe a different page range than this row's.
        row["source_text"] = self.source_text
        return row


class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    idx = Column(Integer, nullable=False)
    title = Column(String(512), nullable=False)
    date = Column(String(16), nullable=False, default="-")
    text = Column(Text, nullable=False)
    source_text = Column(Text)
    edited_title = Column(String(512))
    edited_date = Column(String(16))
    edited_text = Column(Text)
    # Faithfulness verify pass: `verified` = the pass ran; `verified_text` / `verified_title` = the
    # AI-corrected body and header (each set only when the pass found something to fix);
    # `verify_issues` = the list of {type, detail} it fixed. The raw `text` and `title` stay
    # immutable (training data); display precedence is edited > verified > raw for BOTH.
    verified = Column(Boolean, nullable=False, default=False)
    verified_text = Column(Text)
    verified_title = Column(String(512))
    verify_issues = Column(JSON)
    excluded = Column(Boolean, nullable=False, default=False)
    manual_check = Column(Boolean, nullable=False, default=False)
    row_start = Column(Integer, nullable=False)
    row_end = Column(Integer, nullable=False)
    row_category = Column(String(8), nullable=False)
    # PROVENANCE, per summary row. Job-level provenance is not enough once the three summarize calls
    # run on different models: one column cannot describe three, and a job spans many categories so
    # one prompt hash cannot describe every row either.
    #
    # `model` / `title_model` wrote this row's body and title. `audit_model` and `audit_fingerprint`
    # are ALSO NULL when the verify pass simply did not run, which is a different fact from "not
    # recorded" - check `verified` to tell them apart.
    #
    # CUTOFF: rows written before 2026-08-06 have NULL here and are unattributable. Date them against
    # deploy history and know that is what you are doing. Deliberately not backfilled - inferring
    # from timestamps would later be indistinguishable from recorded data.
    model = Column(String(64))
    title_model = Column(String(64))
    audit_model = Column(String(64))
    prompt_fingerprint = Column(String(16))
    audit_fingerprint = Column(String(16))
    # At least one of this row's pages could not be READ - extraction FAILED, as distinct from a page
    # that read cleanly and holds no words (see page_texts.extract_ok for why that difference is kept
    # alive). The body then carries a deterministic notice naming those pages, so an unreadable page
    # is STATED in the deliverable instead of silently vanishing from it.
    #
    # Read alongside `model`, which separates the two cases this one flag covers:
    #   unreadable + model IS NULL     -> nothing could be summarized; the body IS the notice.
    #   unreadable + model IS NOT NULL -> summarized off the readable pages, with a notice appended.
    # A flag rather than a `model` sentinel: NULL there already means "unattributable, written before
    # 2026-08-06", so it cannot also mean "no model wrote this", and `model` is what the pro-vs-flash
    # quality work groups by.
    unreadable = Column(Boolean, nullable=False, default=False)
    # An excluded "review of medical records" block sits immediately after this row, and this row is
    # the evaluation it belongs to - so the body carries a deterministic sentence naming those pages.
    # The senior reviewer asked for the exclusion to STAY and for a tag to say it happened, rather
    # than for the review to be summarized (2026-08-26).
    #
    # Only ever set on a row that was really summarized, so unlike `unreadable` this never pairs with
    # `model IS NULL`; a tagged row with no model would mean the tag reached a notice-only row, which
    # is a defect rather than a state worth reading.
    embedded_review = Column(Boolean, nullable=False, default=False)
    # When this row was last written. Reviewer edits land in edited_* in place, so unlike a ReviewRow
    # (which _store_rows deletes and recreates wholesale, making a timestamp meaningless there) a
    # Summary survives its own edits and can carry one.
    #
    # It is a SECONDARY instrument: the verify pass also writes this row, so a fresh timestamp does
    # not by itself mean a human touched it. The audit_log `summary.edit` event is what identifies
    # reviewer work; this column is what makes "when was this row last written at all" answerable
    # without scanning the trail.
    #
    # NULL on every row written before 2026-08-26, and deliberately NOT backfilled - an inferred
    # timestamp would later be indistinguishable from a recorded one, which is the same reason
    # `model`, `title_model` and `build_sha` were left alone above.
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    def effective_title(self):
        # Same precedence as effective_text: reviewer edit, then the AI-verified correction, then
        # the raw model output. A verified title is applied without the reviewer having to accept
        # it, because leaving a title the pass KNOWS is wrong on screen is the worse default.
        if self.edited_title is not None:
            return self.edited_title
        if self.verified_title is not None:
            return self.verified_title
        return self.title

    def effective_date(self):
        return self.edited_date if self.edited_date is not None else self.date

    def effective_text(self):
        # Reviewer edits win, then the AI-verified correction, then the raw model output.
        if self.edited_text is not None:
            return self.edited_text
        if self.verified_text is not None:
            return self.verified_text
        return self.text

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
            "verified": self.verified,
            "verifyIssues": self.verify_issues or [],
            # The reviewer-facing flag: the AI actually changed this summary (issues were found).
            "verifyChanged": bool(self.verified and self.verify_issues),
            "row": {"start": self.row_start, "end": self.row_end, "category": self.row_category},
        }


class Category(Base):
    __tablename__ = "categories"

    id = Column(String(8), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    examples = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    auto_assign = Column(Boolean, nullable=False, default=True)
    # Whether documents in this category are checked for summarization BY DEFAULT. General (100)
    # and Depositions (9) seed to False (rarely summarized); distinct from auto_assign, which gates
    # whether the classifier may assign the category at all.
    summarize_default = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    def listing(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "examples": list(self.examples or []),
            "active": self.active,
            "auto_assign": self.auto_assign,
            "summarize_default": self.summarize_default,
        }


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True)
    role = Column(String(32), nullable=False)
    category_id = Column(String(8))
    text = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (UniqueConstraint("role", "category_id", name="uq_prompt_role_category"),)


class CatalogMeta(Base):
    __tablename__ = "catalog_meta"

    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=1)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    document_id = Column(String(36))
    # What CHANGED, when the action alone does not say it - e.g. a category edit needs the id it came
    # from as well as the one it went to. Free text rather than JSON: nothing queries this yet, and a
    # human reading a row is the only consumer a write-only trail can have.
    detail = Column(Text)
    at = Column(DateTime, nullable=False, default=_utcnow)


class PageText(Base):
    """OCR text for ONE page of a document, extracted once and reused by every stage.

    Keyed by (document_id, page) rather than by row, deliberately. `review_rows.source_text` is keyed
    to a ROW, and rows change identity whenever a reviewer merges or splits - so row-keyed text cannot
    be reused across stages, and a re-segment throws it away. A page number never changes, so this
    survives every reviewer edit and every re-run.

    Before this existed the same page was OCR'd up to four times per document: once per row during
    segmentation's classification escalation, again per row during classify, again across every row in
    dedup, and again in summarize whenever the row's stored text was missing.

    `ocr_engine` is recorded because it is a measured variable, not a constant: published work on this
    task found switching from Tesseract to a commercial engine cut blank pages from 2.27% to 0.38%.
    Storing which engine produced each page is what makes an A/B on that possible without re-running
    everything blind.
    """

    __tablename__ = "page_texts"
    __table_args__ = (UniqueConstraint("document_id", "page", name="uq_page_texts_document_page"),)

    id = Column(Integer, primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    page = Column(Integer, nullable=False)
    # PHI-bearing, exactly like review_rows.source_text: never log, never leave the box.
    text = Column(Text, nullable=False, default="")
    ocr_engine = Column(String(32), nullable=False, default="tesseract")
    # Whether extraction SUCCEEDED, as distinct from succeeding and finding nothing. Empty text has
    # two very different causes: a page that errored (often transient, worth retrying) and a page that
    # is genuinely blank - a film, a photo, a separator sheet - which will never yield words. The
    # duplicate check already reports that difference to the reviewer, and blank-page RATE is the
    # headline metric for comparing OCR engines, so collapsing both into "" would lose both.
    extract_ok = Column(Boolean, nullable=False, default=True)
    char_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
