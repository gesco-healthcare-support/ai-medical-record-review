"""P3a: catalog accessors + seed_catalog + row validation on an in-memory SQLite session.

These read/seed the catalog tables, so they run on a throwaway SQLite DB (create_all from the
shared metadata) rather than the docker Postgres - fast, hermetic unit tests. They also prove
the constants fallback (unseeded DB) matches the Flask behavior.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - registers all tables on Base.metadata
from app.db import Base
from app.services import catalog
from app.services.rows import validate_rows
from app.services.seed_catalog import constants_categories, seed_catalog


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


def test_constants_categories_shape():
    cats = constants_categories()
    assert cats
    assert "6" in {c["id"] for c in cats}  # manually-selectable, non-auto-assign
    keys = {"id", "name", "description", "examples", "active", "auto_assign"}
    assert all(keys <= c.keys() for c in cats)


def test_catalog_falls_back_to_constants_when_unseeded(session):
    ids = catalog.get_category_ids(session, active_only=True)
    assert "6" in ids
    assert catalog.catalog_version(session) == 0  # no CatalogMeta row yet


def test_seed_then_db_backed(session):
    seed_catalog(session)
    assert "6" in set(catalog.get_category_ids(session, active_only=True))
    assert catalog.catalog_version(session) == 1
    assert catalog.get_prompt(session, "summary", "1")  # seeded
    assert catalog.get_prompt(session, "summary", "11")  # no row -> general (100) fallback


def test_bump_revision(session):
    seed_catalog(session)
    assert catalog.bump_revision(session) == 2


def test_validate_rows_ok_and_errors(session):
    seed_catalog(session)
    valid = catalog.get_category_ids(session, active_only=True)[0]
    assert (
        validate_rows(session, [{"start": 1, "end": 2, "category": valid}], total_pages=5) is None
    )
    assert validate_rows(session, [], total_pages=5) == "no rows to summarize"
    assert "integers" in validate_rows(session, [{"start": "x", "end": 2, "category": valid}], 5)
    assert "1 <= start" in validate_rows(session, [{"start": 3, "end": 2, "category": valid}], 5)
    overlap = [
        {"start": 1, "end": 3, "category": valid},
        {"start": 2, "end": 4, "category": valid},
    ]
    assert "overlaps" in validate_rows(session, overlap, 5)
    assert "unknown category" in validate_rows(
        session, [{"start": 1, "end": 2, "category": "999"}], 5
    )
