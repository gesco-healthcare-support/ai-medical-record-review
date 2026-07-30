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


def test_diagnostic_studies_and_laboratory_results_are_separate_categories():
    # D-01/D-02: category 3 is MODALITY-based, 14 is SPECIMEN-based. "Laboratory Report" under
    # category 3 told the classifier the opposite, which is why lab work landed with imaging.
    by_id = {c["id"]: c for c in constants_categories()}
    assert "Laboratory Report" not in by_id["3"]["examples"]
    assert "Laboratory Report" in by_id["14"]["examples"]
    # The broad titles that attracted imaging into the laboratory category are gone.
    assert not {"Results", "Test Results"} & set(by_id["14"]["examples"])
    # Each description must name the other category, since the LLM stage sees only this text.
    assert "SPECIMEN" in by_id["3"]["description"]
    assert "SPECIMEN" in by_id["14"]["description"]


def test_misfiled_example_titles_moved_to_their_own_category():
    by_id = {c["id"]: c for c in constants_categories()}
    # D-03: an emergency-department encounter is a treating visit, not a diagnostic study.
    assert "Ed (Emergency Department) Provider Notes" in by_id["1"]["examples"]
    assert "Ed (Emergency Department) Provider Notes" not in by_id["3"]["examples"]
    # D-04: an unqualified supplemental report is medico-legal work (12), not a treating note (1).
    assert "Supplemental Report" not in by_id["1"]["examples"]
    assert "Supplemental Report" in by_id["12"]["examples"]
    # The qualified treating supplemental stays where it was.
    assert "Supplemental Report on Pain Management Process" in by_id["1"]["examples"]


def test_category_5_no_longer_offers_bare_section_headers():
    # D-05: these appear in nearly every report, so they matched anything with a physical-exam
    # heading rather than the therapy notes category 5 is for.
    examples = set({c["id"]: c for c in constants_categories()}["5"]["examples"])
    assert not {"History of Present Illness", "Physical Examination", "Diagnosis"} & examples
    # D-06: category 5 is where daily/SOAP notes are auto-assigned (6 is never auto-assigned), so its
    # description has to claim them.
    description = {c["id"]: c for c in constants_categories()}["5"]["description"]
    assert "SOAP" in description


def test_the_catalog_migration_carries_the_same_text_as_the_constants():
    """The classifier reads the DB catalog first, so a taxonomy edit only reaches a seeded box
    through the migration. If the two texts drift, the box keeps a description that no longer
    matches the code and nothing says so - this fails the suite instead."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "c8b1d4e70f92_category_modality_vs_specimen.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    by_id = {c["id"]: c for c in constants_categories()}
    for category_id, text in migration._NEW_DESCRIPTIONS.items():
        assert by_id[category_id]["description"] == text, (
            f"category {category_id} description drift"
        )
    assert by_id["14"]["name"] == migration._NEW_NAME_14
    # Every title the migration adds must exist in the constants, and none it removes may.
    for category_id, titles in migration._ADDITIONS.items():
        assert set(titles) <= set(by_id[category_id]["examples"])
    for category_id, titles in migration._REMOVALS.items():
        assert not set(titles) & set(by_id[category_id]["examples"])


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
