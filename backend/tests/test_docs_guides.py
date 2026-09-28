"""The package guides and pages still describe files and symbols that exist - checked, not trusted.

Companion to test_docs_reference_drift.py, which checks the reference TABLES against the code. This one
checks the rest of the docs mechanically, using the same rules as the pull-request and weekly checks in
`.github/workflows/docs-drift.yml` (one implementation: `.github/scripts/docs_guides.py`):

- every file directly in a folder that has a README.md is named in that README (the "what each file
  does" list an agent reads before the code); `__init__.py`, dotfiles and lockfiles are exempt;
- every repository path a doc cites in backticks exists (relative to the doc's folder or any folder above
  it), or is gitignored output, or is an npm package path;
- every "`path` `symbol`" citation names a symbol that still occurs in that file;
- every CLAUDE.md stays under 200 lines, the size Anthropic's guidance gives for reliable adherence.

Renames, deletions and new files are what these catch. A changed behaviour under an unchanged name is not
mechanical; the pull-request check and the weekly report exist for that.
"""

import importlib.util
import json
import posixpath
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIX_HINT = "Fix the doc in the same pull request (docs/how-to/work-on-these-docs.md)."

# The shared rules live beside the CI script that also uses them, outside the backend package.
_spec = importlib.util.spec_from_file_location(
    "docs_guides", REPO / ".github" / "scripts" / "docs_guides.py"
)
assert _spec and _spec.loader, "cannot load .github/scripts/docs_guides.py"
docs_guides = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_guides)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


TRACKED = set(_git("ls-files").splitlines())
GUIDES = sorted(p for p in TRACKED if docs_guides.is_guide_doc(p))
TEXTS = {p: (REPO / p).read_text(encoding="utf-8") for p in GUIDES}
_package = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
NPM_PACKAGES = set(_package.get("dependencies", {})) | set(_package.get("devDependencies", {}))


def _ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO).returncode == 0


# --- README file lists --------------------------------------------------------------------------------


def _readme_cases() -> list[tuple[str, str]]:
    cases = []
    for readme in GUIDES:
        folder = posixpath.dirname(readme)
        if posixpath.basename(readme) != "README.md" or not folder or folder.startswith("docs"):
            continue
        here = sorted(
            posixpath.basename(p)
            for p in TRACKED
            if posixpath.dirname(p) == folder and not p.endswith(".md")
        )
        cases += [(readme, name) for name in docs_guides.readme_missing("", here)]
    return cases


def test_the_readme_enumeration_found_the_package_readmes():
    readmes = {readme for readme, _ in _readme_cases()}
    assert "backend/app/api/README.md" in readmes
    assert "frontend/components/review/README.md" in readmes


@pytest.mark.parametrize(("readme", "name"), _readme_cases())
def test_every_file_is_named_in_its_folder_readme(readme, name):
    assert not docs_guides.readme_missing(TEXTS[readme], [name]), (
        f"{posixpath.dirname(readme)}/{name} is not named in {readme}. Add a row saying what it is. "
        f"{FIX_HINT}"
    )


# --- cited paths ------------------------------------------------------------------------------------


def _path_cases() -> list[tuple[str, str]]:
    return sorted(
        {(doc, cited) for doc in GUIDES for cited in docs_guides.path_citations(TEXTS[doc])}
    )


def test_the_citation_enumeration_found_the_citations():
    cases = _path_cases()
    assert len(cases) > 500
    assert ("docs/reference/job-and-document-states.md", "backend/app/services/jobs.py") in cases


@pytest.mark.parametrize(("doc", "cited"), _path_cases())
def test_every_cited_path_exists(doc, cited):
    if docs_guides.resolve(doc, cited, TRACKED):
        return
    if cited.split("/")[0] in NPM_PACKAGES:  # an import path such as `shadcn/tailwind.css`
        return
    # Gitignored output a doc may name, e.g. `frontend/coverage/coverage-summary.json`.
    if any(_ignored(candidate) for candidate in docs_guides.candidates(doc, cited)):
        return
    pytest.fail(f"{doc} cites `{cited}`, which does not exist. {FIX_HINT}")


# --- cited symbols ----------------------------------------------------------------------------------


def _symbol_cases() -> list[tuple[str, str, str]]:
    cases = set()
    for doc in GUIDES:
        for cited, symbol in docs_guides.symbol_citations(TEXTS[doc]):
            target = docs_guides.resolve(doc, cited, TRACKED)
            if target:
                cases.add((doc, target, symbol))
    return sorted(cases)


def test_the_symbol_enumeration_found_the_symbols():
    assert len(_symbol_cases()) > 100


@pytest.mark.parametrize(("doc", "path", "symbol"), _symbol_cases())
def test_every_cited_symbol_exists_in_its_file(doc, path, symbol):
    source = (REPO / path).read_text(encoding="utf-8", errors="replace")
    assert docs_guides.symbol_in_source(symbol, source), (
        f"{doc} cites `{symbol}` in `{path}`, which no longer contains it. {FIX_HINT}"
    )


# --- CLAUDE.md size ---------------------------------------------------------------------------------

CLAUDE_FILES = [p for p in GUIDES if posixpath.basename(p) == "CLAUDE.md"]


def test_the_claude_md_enumeration_found_them():
    assert "CLAUDE.md" in CLAUDE_FILES
    assert "backend/app/services/llm/CLAUDE.md" in CLAUDE_FILES


@pytest.mark.parametrize("path", CLAUDE_FILES)
def test_every_claude_md_stays_under_the_size_limit(path):
    lines = TEXTS[path].count("\n") + 1
    assert lines <= docs_guides.CLAUDE_MD_MAX_LINES, (
        f"{path} has {lines} lines; keep it at {docs_guides.CLAUDE_MD_MAX_LINES} or fewer. Move detail "
        "into the folder's README.md or a docs page and link to it."
    )
