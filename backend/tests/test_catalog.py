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
from app.models import Prompt
from app.services import catalog
from app.services.prompts import prompts
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
    keys = {"id", "name", "description", "examples", "active", "auto_assign", "summarize_default"}
    assert all(keys <= c.keys() for c in cats)
    by_id = {c["id"]: c for c in cats}
    assert by_id["9"]["summarize_default"] is False  # Depositions off by default
    assert by_id["100"]["summarize_default"] is False  # General off by default
    assert by_id["1"]["summarize_default"] is True  # everything else on


@pytest.mark.parametrize(
    "category_id,expected", [("9", False), ("100", False), ("1", True), ("999", True)]
)
def test_summarize_default_for_constants_fallback(session, category_id, expected):
    # Unseeded -> constants fallback; 9/100 off, others (incl. unknown) on.
    assert catalog.summarize_default_for(session, category_id) is expected


def test_summarize_default_for_db_backed(session):
    seed_catalog(session)
    assert catalog.summarize_default_for(session, "9") is False
    assert catalog.summarize_default_for(session, "100") is False
    assert catalog.summarize_default_for(session, "1") is True


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


def _general_row(session, text="GENERAL ROW PROMPT"):
    """A custom general (100) prompt row, the only row on an otherwise code-driven box."""
    session.add(Prompt(role="summary", category_id="100", text=text, revision=1))
    session.commit()


def test_code_prompt_beats_the_general_row(session):
    """WHEN a category has no prompt row but the code defines one, THE SYSTEM SHALL return the code
    prompt even though a general (100) row exists. Otherwise deleting the seeded shadows would hand
    every category the catch-all prompt - a diagnostic study summarized with the general rules."""
    _general_row(session)
    diagnostics = catalog.get_prompt(session, "summary", "3")
    assert diagnostics == prompts["category_03"]
    assert diagnostics != "GENERAL ROW PROMPT"


def test_category_row_still_wins(session):
    # WHEN a category has a prompt row, THE SYSTEM SHALL return that row's text (an admin override).
    session.add(Prompt(role="summary", category_id="3", text="CUSTOM FOR 3", revision=1))
    _general_row(session)
    assert catalog.get_prompt(session, "summary", "3") == "CUSTOM FOR 3"


def test_category_without_a_code_prompt_falls_back_to_the_general_row(session):
    # Category 11 has neither a row nor a code prompt -> the general ROW, then the general constant.
    _general_row(session)
    assert catalog.get_prompt(session, "summary", "11") == "GENERAL ROW PROMPT"


def test_unseeded_box_falls_back_to_the_code_constants(session):
    assert catalog.get_prompt(session, "summary", "3") == prompts["category_03"]
    assert catalog.get_prompt(session, "summary", "11") == prompts["category_100"]
    assert catalog.get_prompt(session, "verify", "3") is None  # only summaries have a fallback


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
