"""P3a: catalog accessors + seed_catalog + row validation on an in-memory SQLite session.

These read/seed the catalog tables, so they run on a throwaway SQLite DB (create_all from the
shared metadata) rather than the docker Postgres - fast, hermetic unit tests. They also prove
the constants fallback (unseeded DB) matches the Flask behavior.
"""

import pytest
from sqlalchemy import create_engine, select
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
    # Depositions (9) were off by default until 2026-08-06; Adrian turned them on, because a
    # reviewer had to remember a switch and the deposition prompt reached almost no output.
    assert by_id["9"]["summarize_default"] is True
    assert by_id["100"]["summarize_default"] is False  # General is the only one still off
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
    "category_id,expected", [("100", False), ("9", True), ("1", True), ("999", True)]
)
def test_summarize_default_for_constants_fallback(session, category_id, expected):
    # Unseeded -> constants fallback. 100 is the only category off by default; an UNKNOWN id
    # defaults ON, which is the safe direction - a new category is summarized until told not to.
    assert catalog.summarize_default_for(session, category_id) is expected


def test_summarize_default_for_db_backed(session):
    seed_catalog(session)
    assert catalog.summarize_default_for(session, "100") is False
    assert catalog.summarize_default_for(session, "9") is True
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


def test_an_unseeded_catalog_still_offers_category_fifteen(session):
    """The property that makes the migration's no-op on an unseeded catalog correct.

    `catalog.get_categories` falls back to `taxonomy.py` only while the `categories` table is EMPTY,
    which is the normal state for a fresh box, local dev and CI - seed_catalog() is called nowhere in
    the app. So the migration must NOT insert a row there: one row would end the fallback and
    collapse the catalog from fifteen categories to that one.

    Measured before this guard existed: running the migration against the test database left
    `categories` holding id 15 alone, and `llm_classify("Progress Report")` began returning None
    because "1" was no longer an allowed id. This pins the fallback that makes doing nothing safe.
    """
    from app.services import catalog

    ids = catalog.get_category_ids(session, active_only=True, auto_assign=True)
    assert "15" in ids, "an unseeded catalog must still offer category 15 from the constants"
    # The fallback has to carry the REST too - that is the half the bug destroyed.
    for category_id in ("1", "3", "5", "10", "13", "100"):
        assert category_id in ids
    assert catalog.summarize_default_for(session, "15") is True


def test_seed_categories_materializes_every_constant(session):
    """GUARDS the new code. Nothing may survive that keeps the catalog collapsible."""
    from app.services.seed_catalog import seed_categories

    seed_categories(session)
    ids = {c["id"] for c in catalog.get_categories(session)}
    assert ids == {c["id"] for c in constants_categories()}
    # Materializing must not change what any reader sees - that is what makes it safe to do
    # inside a write path. Same active set, same auto-assign set, same defaults.
    assert catalog.summarize_default_for(session, "100") is False
    assert catalog.summarize_default_for(session, "9") is True
    assert "6" not in catalog.get_category_ids(session, auto_assign=True)  # never auto-assigned
    assert "6" in catalog.get_category_ids(session, active_only=True)  # but manually selectable


def test_seed_categories_writes_no_prompt_rows(session):
    """GUARDS against re-introducing the shadow rows migration f1a83b5c60d2 exists to delete.

    Prompts resolve DB-first, so a seeded summary-prompt row pins that category to the text as it
    was on the day it was written: `prompts.py` edits are deployed and silently do not arrive. That
    is why seed_catalog() - which DOES write those rows - must not be called from app/, and why
    seed_categories() writes categories only. seed_catalog() is asserted alongside so the two do not
    quietly converge.
    """
    from app.services.seed_catalog import seed_categories

    seed_categories(session)
    assert session.scalars(select(Prompt).where(Prompt.role == "summary")).all() == []
    # Category 1 still resolves through prompts.py, so a deployed prompt change reaches this box.
    assert catalog.get_prompt(session, "summary", "1") == prompts["category_01"]

    seed_catalog(session)  # the full seeder on the SAME session is a no-op: categories exist
    assert session.scalars(select(Prompt).where(Prompt.role == "summary")).all() == []


def test_seed_categories_never_clobbers_an_edited_catalog(session):
    """An admin-edited row must survive; seeding is a back-fill, never a reset."""
    from app.models import Category
    from app.services.seed_catalog import seed_categories

    session.add(
        Category(
            id="1",
            name="Renamed By An Admin",
            description="",
            examples=[],
            active=True,
            auto_assign=True,
            summarize_default=True,
        )
    )
    session.commit()

    seed_categories(session)
    assert [c["name"] for c in catalog.get_categories(session)] == ["Renamed By An Admin"]


def test_creating_a_category_does_not_collapse_an_unseeded_catalog(session):
    """DEMONSTRATES the bug: this fails on origin/main, where the catalog collapses to the new row.

    The route function is called directly so the catalog is genuinely unseeded - the normal state
    for a fresh box, local dev and CI. Before the fix, POST /api/admin/categories wrote one row,
    which ended `catalog.get_categories`' all-or-nothing fallback and took every other category
    with it: `validate_rows` began rejecting category "1", so every reviewer got 400 "unknown
    category" on autosave, and General (100) flipped to summarize-by-default.
    """
    from app.api.admin import create_category
    from app.models import User
    from app.schemas.admin import CategoryCreate

    user = User(id=1, email="admin@example.com", name="A", password="x", active=True, is_admin=True)
    session.add(user)
    session.commit()

    created = create_category(CategoryCreate(id="16", name="A New Category"), session, user)
    assert created["id"] == "16"

    ids = catalog.get_category_ids(session, active_only=True)
    assert "16" in ids, "the category the admin created must exist"
    for category_id in ("1", "3", "5", "10", "13", "15", "100"):
        assert category_id in ids, f"category {category_id} was destroyed by creating '16'"
    assert validate_rows(session, [{"start": 1, "end": 2, "category": "1"}], 5) is None
    assert catalog.summarize_default_for(session, "100") is False  # General still off by default


def test_creating_a_category_that_is_already_a_constant_is_a_conflict(session):
    """DEMONSTRATES the second half: on origin/main this inserted a shadow row beside the built-in.

    Seeding BEFORE the duplicate check is what turns it into the 400 the admin needs to see - the id
    is taken, by a category the constants already carry.
    """
    from fastapi import HTTPException

    from app.api.admin import create_category
    from app.models import User
    from app.schemas.admin import CategoryCreate

    user = User(id=1, email="admin@example.com", name="A", password="x", active=True, is_admin=True)
    session.add(user)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        create_category(CategoryCreate(id="13", name="Shadow"), session, user)
    assert exc.value.status_code == 400 and "already exists" in exc.value.detail
    assert len([c for c in catalog.get_categories(session) if c["id"] == "13"]) == 1
