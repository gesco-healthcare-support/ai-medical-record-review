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
        """The queued/running job, if any - at most one by the job-service invariant."""
        return next((job for job in self.jobs if job.state in ("queued", "running")), None)

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
            postgresql_where=text("state IN ('queued', 'running')"),
            sqlite_where=text("state IN ('queued', 'running')"),
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
    model = Column(String(64), nullable=False)
    prompt_version = Column(String(16), nullable=False)
    catalog_revision = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    segment_rows = relationship(
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

    def as_row(self):
        row = {field: getattr(self, field) for field in ROW_FIELDS}
        row["suggest_merge"] = self.suggest_merge
        row["include"] = self.include
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
    excluded = Column(Boolean, nullable=False, default=False)
    manual_check = Column(Boolean, nullable=False, default=False)
    row_start = Column(Integer, nullable=False)
    row_end = Column(Integer, nullable=False)
    row_category = Column(String(8), nullable=False)

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


class Category(Base):
    __tablename__ = "categories"

    id = Column(String(8), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    examples = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    auto_assign = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    def listing(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "examples": list(self.examples or []),
            "active": self.active,
            "auto_assign": self.auto_assign,
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
    at = Column(DateTime, nullable=False, default=_utcnow)
