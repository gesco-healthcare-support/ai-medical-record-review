"""The reference pages list everything the code defines - checked, not trusted.

A reference page that falls behind the code is worse than none: it still reads as complete. Each
test here enumerates one kind of thing from the code itself and asserts that its page in
`docs/reference/` gives every one a ROW of its own, so adding a setting, an HTTP route, a migration,
a compose service, a frontend page or a CI job without its row fails CI and names what is missing.
How to add the row: `docs/how-to/work-on-these-docs.md`.

Two things keep these tests honest, and both were found by breaking the pages on purpose:

- Presence is checked in the column the item owns (usually the first), not anywhere on the page.
  A migration id also appears as the next migration's parent, and a setting is often named in a
  neighbour's description, so a page-wide search passed with the row itself deleted.
- Each enumerator has a sentinel test. A parser that silently found nothing produces zero
  parametrized cases, and zero cases pass; the sentinel proves the enumeration actually ran.
"""

import re
from pathlib import Path

import pytest

from app.config import Settings

REPO = Path(__file__).resolve().parents[2]
REFERENCE = REPO / "docs" / "reference"
FIX_HINT = "Add its row in the same pull request (docs/how-to/work-on-these-docs.md)."


def _table_rows(page: str) -> list[list[str]]:
    """The cells of every Markdown table row on a reference page, stripped of surrounding space."""
    text = (REFERENCE / page).read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            rows.append([cell.strip() for cell in line[1:-1].split("|")])
    return rows


def _column(page: str, index: int) -> set[str]:
    return {row[index] for row in _table_rows(page) if len(row) > index}


# --- settings -> configuration.md (first column) --------------------------------------------------


def _env_names() -> list[str]:
    """Every environment variable `Settings` reads (no env prefix; an alias wins over the field name)."""
    names = set()
    for field_name, field in Settings.model_fields.items():
        alias = field.validation_alias if isinstance(field.validation_alias, str) else field.alias
        names.add((alias or field_name).upper())
    return sorted(names)


def test_the_settings_enumeration_found_the_settings():
    assert "DATABASE_URL" in _env_names()


@pytest.mark.parametrize("env_name", _env_names())
def test_every_setting_has_a_row_in_the_configuration_reference(env_name):
    assert f"`{env_name}`" in _column("configuration.md", 0), (
        f"Setting {env_name} has no row in docs/reference/configuration.md. {FIX_HINT}"
    )


# --- HTTP routes -> http-api.md (method and path in the same row) ---------------------------------


_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _api_routes() -> list[tuple[str, str]]:
    """Every operation in the app's OpenAPI schema. Not `app.routes`: since FastAPI 0.139 an
    included router is a lazy `_IncludedRouter` there, so walking `app.routes` for `APIRoute` finds
    only the routes declared on the app itself (`/health`) and silently skips the other 45. No route
    here sets `include_in_schema=False`, so the schema is the complete list."""
    from app.main import app

    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, item in paths.items()
        for method in item
        if method in _HTTP_METHODS
    )


def test_the_route_enumeration_reached_every_router():
    """One route from each included router, plus the app's own: a regression to the `app.routes`
    walk would find only the last one, and this names what is missing."""
    found = set(_api_routes())
    for expected in [
        ("GET", "/health"),
        ("POST", "/api/documents/{document_id}/segment/start"),
        ("GET", "/api/documents/{document_id}/downloads/{token}"),
        ("GET", "/api/admin/categories"),
        ("POST", "/api/auth/login"),
        ("GET", "/api/users/me"),
    ]:
        assert expected in found, f"route enumeration missed {expected}"


@pytest.mark.parametrize(("method", "path"), _api_routes())
def test_every_route_has_a_row_in_the_http_api_reference(method, path):
    rows = [row for row in _table_rows("http-api.md") if method in row and f"`{path}`" in row]
    assert rows, f"{method} {path} has no row in docs/reference/http-api.md. {FIX_HINT}"


# --- migrations -> migrations.md (the Revision column, the second) --------------------------------

_REVISION = re.compile(r"^revision(?:\s*:\s*str)?\s*=\s*[\"']([0-9a-f]+)[\"']", re.MULTILINE)


def _revisions() -> list[str]:
    found = []
    for path in sorted((REPO / "backend" / "alembic" / "versions").glob("*.py")):
        match = _REVISION.search(path.read_text(encoding="utf-8"))
        if match:
            found.append(match.group(1))
    return found


def test_the_migration_enumeration_found_the_baseline():
    assert "73abdcd5ef01" in _revisions()


@pytest.mark.parametrize("revision", _revisions())
def test_every_migration_has_a_row_in_the_migrations_reference(revision):
    assert f"`{revision}`" in _column("migrations.md", 1), (
        f"Migration {revision} has no row in docs/reference/migrations.md. {FIX_HINT}"
    )


# --- compose services -> compose-services.md (first column) ---------------------------------------

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.dev.yml")


def _compose_services() -> list[tuple[str, str]]:
    """(file, service) for every service in both compose files, read without a YAML dependency:
    the two-space-indented keys of the top-level `services:` block."""
    found = []
    for name in _COMPOSE_FILES:
        text = (REPO / name).read_text(encoding="utf-8")
        block = re.search(r"^services:[ \t]*\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
        assert block, f"no top-level services: block in {name}"
        for service in re.findall(
            r"^  ([a-z0-9][a-z0-9_-]*):[ \t]*$", block.group(1), re.MULTILINE
        ):
            found.append((name, service))
    return found


def test_the_compose_enumeration_found_both_files():
    services = _compose_services()
    assert ("docker-compose.yml", "proxy") in services
    assert ("docker-compose.dev.yml", "postgres") in services


@pytest.mark.parametrize(("compose_file", "service"), _compose_services())
def test_every_compose_service_has_a_row_in_the_compose_reference(compose_file, service):
    assert f"`{service}`" in _column("compose-services.md", 0), (
        f"Service {service} ({compose_file}) has no row in docs/reference/compose-services.md. "
        f"{FIX_HINT}"
    )


# --- frontend pages -> frontend-routes-and-data.md (first column) ---------------------------------


def _frontend_routes() -> list[str]:
    app_dir = REPO / "frontend" / "app"
    routes = []
    for page in sorted(app_dir.rglob("page.tsx")):
        parts = page.parent.relative_to(app_dir).parts
        routes.append("/" + "/".join(parts))
    return routes


def test_the_frontend_enumeration_found_the_pages():
    routes = _frontend_routes()
    assert "/" in routes
    assert "/records/[id]" in routes


@pytest.mark.parametrize("route", _frontend_routes())
def test_every_frontend_route_has_a_row_in_the_frontend_reference(route):
    assert f"`{route}`" in _column("frontend-routes-and-data.md", 0), (
        f"Frontend route {route} has no row in docs/reference/frontend-routes-and-data.md. "
        f"{FIX_HINT}"
    )


# --- CI jobs -> ci-and-merge-gates.md (the Jobs table: job, then its workflow file) ----------------


def _workflow_jobs() -> list[tuple[str, str]]:
    """(workflow file, job id) for every job in `.github/workflows/`, read without a YAML dependency:
    the two-space-indented keys of the top-level `jobs:` block. The job id, not its display name:
    `promotion-guard` reports as `guard-into-<branch>`, and the page names both."""
    found = []
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        block = re.search(r"^jobs:[ \t]*\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
        assert block, f"no top-level jobs: block in {path.name}"
        for job in re.findall(r"^  ([a-z0-9][a-z0-9_-]*):[ \t]*$", block.group(1), re.MULTILINE):
            found.append((path.name, job))
    return found


def test_the_workflow_enumeration_found_every_workflow():
    jobs = _workflow_jobs()
    assert ("ci.yml", "backend") in jobs
    assert ("ci.yml", "sonarcloud") in jobs
    assert ("promotion-guard.yml", "promotion-guard") in jobs


@pytest.mark.parametrize(("workflow", "job"), _workflow_jobs())
def test_every_ci_job_has_a_row_in_the_ci_reference(workflow, job):
    """The row must name the job AND its workflow file, which only the Jobs table does: a job named
    in the required-checks table or in prose does not count."""
    rows = [
        row
        for row in _table_rows("ci-and-merge-gates.md")
        if len(row) > 1 and row[0] == f"`{job}`" and row[1] == f"`{workflow}`"
    ]
    assert rows, (
        f"CI job {job} ({workflow}) has no row in the Jobs table of "
        f"docs/reference/ci-and-merge-gates.md. {FIX_HINT}"
    )
