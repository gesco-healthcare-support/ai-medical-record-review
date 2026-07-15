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
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


def _uuid() -> str:
    import uuid

    return str(uuid.uuid4())


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
    active = Column(Boolean, nullable=False)
    fs_uniquifier = Column(String(64), unique=True, nullable=False)
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

    roles = relationship("Role", secondary=roles_users)


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

    jobs = relationship("Job", backref="document", cascade="all, delete-orphan", order_by="Job.id")
    review_rows = relationship(
        "ReviewRow", backref="document", cascade="all, delete-orphan", order_by="ReviewRow.idx"
    )
    summaries = relationship(
        "Summary", backref="document", cascade="all, delete-orphan", order_by="Summary.idx"
    )


class Job(Base):
    __tablename__ = "jobs"

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


class Category(Base):
    __tablename__ = "categories"

    id = Column(String(8), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    examples = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    auto_assign = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


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
