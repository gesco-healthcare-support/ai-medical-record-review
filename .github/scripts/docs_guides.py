"""Keep the docs, package READMEs and CLAUDE.md files in step with the code.

One module, three users:

- backend/tests/test_docs_guides.py (blocking, in the required `backend` job): every file a folder holds
  is named in its README, every cited path and path+symbol still exists, every CLAUDE.md stays short.
- `python docs_guides.py impact <base> <head>` (docs-drift.yml, on pull requests, WARN ONLY): lists
  code the pull request changed whose describing docs it did not touch.
- `python docs_guides.py report` (docs-drift.yml, weekly): lists docs whose code changed after the doc
  last did, as the body of one GitHub issue.

"Which docs describe this file" is never written down by hand; it is derived:

- the README.md and CLAUDE.md of the nearest folder above the file that has either (the package guide);
- every page, README or CLAUDE.md that cites the file by path.

No check can tell that a sentence became false while every name stayed the same, so `impact` names the
docs to re-read rather than guessing at them. Stdlib only: this runs on the runner's own Python with
nothing installed.
"""

import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Iterable

GUIDE_NAMES = ("CLAUDE.md", "README.md")
# Not guides: working artefacts, the superseded Flask app, the research experiments, vendored packages.
EXCLUDED = re.compile(r"^(docs/plans/|docs/backlog\.md$|legacy/|experiments/)|(^|/)node_modules/")
EXTENSIONS = "py|ts|tsx|mjs|js|yml|yaml|toml|json|sh|conf|css|txt|ini|cfg|html|md|lock|sql|ps1"
_SEGMENT = r"[A-Za-z0-9_.\-\[\]]+"
_PATH = r"(?:\.{1,2}/)?" + _SEGMENT + r"(?:/" + _SEGMENT + r")+\.(?:" + EXTENSIONS + r")"
# A backticked token with at least one "/" and a file extension. Placeholders (`<x>`), globs (`*`),
# braces and URLs are not paths, so the character class leaves them out.
PATH = re.compile(r"`(" + _PATH + r")`")
# "`some/file.py` `name`", "`file.py` (`name()`)" or "`file.py`, `name`": a path, then one symbol.
PATH_THEN_SYMBOL = re.compile(
    r"`((?:\.{1,2}/)?" + _SEGMENT + r"(?:/" + _SEGMENT + r")*\.(?:" + EXTENSIONS + r"))`"
    r"[ ,]*\(?`([A-Za-z_][A-Za-z0-9_.]*)(?:\(\))?`"
)
FILENAME = re.compile(r"\.(?:" + EXTENSIONS + r")$")
LOCKFILES = ("uv.lock", "pnpm-lock.yaml", "package-lock.json", "poetry.lock")
TEST_FILE = re.compile(r"(^|/)(tests?|e2e)/|(^|/)test_[^/]*\.py$|\.(test|spec)\.[jt]sx?$")
NOT_CODE = re.compile(r"^(docs/|legacy/|experiments/|\.github/pr-media/|frontend/public/)")
CLAUDE_MD_MAX_LINES = 200


# --- what is a guide, what is code ----------------------------------------------------------------


def is_guide_doc(path: str) -> bool:
    """A site page under docs/, or a package README.md / CLAUDE.md, outside the excluded trees."""
    if EXCLUDED.search(path) or not path.endswith(".md"):
        return False
    return path.startswith("docs/") or posixpath.basename(path) in GUIDE_NAMES


def is_code(path: str) -> bool:
    """A file whose change can make a doc wrong: not Markdown, not a test, not a lockfile, not vendored."""
    if path.endswith(".md") or NOT_CODE.search(path) or TEST_FILE.search(path):
        return False
    return posixpath.basename(path) not in LOCKFILES


# --- citations ------------------------------------------------------------------------------------


def path_citations(text: str) -> list[str]:
    """Every backticked repository path in `text`, in order of appearance."""
    return [m.group(1) for m in PATH.finditer(text)]


PACKAGE_ROOTS = ("backend", "frontend")


def candidates(doc: str, cited: str) -> list[str]:
    """Every repository path `cited` may mean, in the order tried: relative to `doc`'s folder, to each
    folder above it up to the root, then to each package root. Package guides cite `services/jobs.py`
    from inside `backend/app/`; site pages cite `scripts/eval/x.py` and `components/ui/dialog.tsx`
    from the backend and frontend package roots."""
    found = []
    folder = posixpath.dirname(doc)
    while True:
        found.append(posixpath.normpath(posixpath.join(folder, cited) if folder else cited))
        if not folder:
            break
        folder = posixpath.dirname(folder)
    found += [posixpath.normpath(posixpath.join(root, cited)) for root in PACKAGE_ROOTS]
    return list(dict.fromkeys(found))


def resolve(doc: str, cited: str, tracked: set[str]) -> str | None:
    """The first of `candidates` that is a tracked file; None when none is."""
    return next((c for c in candidates(doc, cited) if c in tracked), None)


def symbol_citations(text: str) -> list[tuple[str, str]]:
    """(path, symbol) for each "path, then one symbol" citation. Skips a second FILENAME (a list of
    files, not a symbol), and a dotted name whose head is not the cited file's own module
    (`failures.X` after `copy_records.py` points at another file). `jobs.create_job` after `jobs.py`
    checks `create_job`."""
    found = []
    for match in PATH_THEN_SYMBOL.finditer(text):
        path, symbol = match.group(1), match.group(2)
        if FILENAME.search(symbol):
            continue
        if "." in symbol:
            head, _, symbol = symbol.rpartition(".")
            stem = posixpath.splitext(posixpath.basename(path))[0]
            if head.split(".")[-1] != stem:
                continue
        found.append((path, symbol))
    return found


def symbol_in_source(symbol: str, source: str) -> bool:
    """`symbol` occurs in `source` starting at a word boundary. No right boundary on purpose: a
    migration id is cited bare but written inside a file name (`b3f7c02e91a4_utilization...`). A symbol
    that survives only as the prefix of a longer name passes - a false pass, never a false failure in a
    blocking check."""
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(symbol), source) is not None


def citation_index(texts: dict[str, str], tracked: set[str]) -> dict[str, set[str]]:
    """{tracked file: the docs that cite it by path}."""
    index: dict[str, set[str]] = {}
    for doc, text in texts.items():
        for cited in path_citations(text):
            target = resolve(doc, cited, tracked)
            if target and target != doc:
                index.setdefault(target, set()).add(doc)
    return index


# --- README file lists ----------------------------------------------------------------------------


def readme_missing(readme_text: str, files_here: Iterable[str]) -> list[str]:
    """The names in `files_here` that the README never names in backticks. Exempt: `__init__.py`
    (package markers), dotfiles (tool config) and lockfiles (generated)."""
    named = set(re.findall(r"`([^`\s]+)`", readme_text))
    return [
        name
        for name in files_here
        if name not in named and name != "__init__.py" and not name.startswith(".") and name not in LOCKFILES
    ]


# --- coverage and the pull-request check ----------------------------------------------------------


def nearest_guides(path: str, tracked: set[str]) -> list[str]:
    """The README.md / CLAUDE.md of the nearest folder above `path` that has either; the root last."""
    folder = posixpath.dirname(path)
    while True:
        guides = [posixpath.join(folder, name) if folder else name for name in GUIDE_NAMES]
        present = [g for g in guides if g in tracked and not EXCLUDED.search(g)]
        if present or not folder:
            return present
        folder = posixpath.dirname(folder)


def covering_docs(path: str, tracked: set[str], index: dict[str, set[str]]) -> list[str]:
    """Every doc that describes `path`: its package guides, plus every doc citing it."""
    return sorted(set(nearest_guides(path, tracked)) | index.get(path, set()))


def impact(changed: Iterable[str], tracked: set[str], index: dict[str, set[str]]) -> list[tuple[str, list[str]]]:
    """(code file, its covering docs) for each changed code file whose covering docs were ALL left
    untouched. Touching any one of them counts as having looked."""
    changed = set(changed)
    found = []
    for path in sorted(changed):
        if not is_code(path):
            continue
        docs = covering_docs(path, tracked, index)
        if docs and not changed.intersection(docs):
            found.append((path, docs))
    return found


def covered_code(doc: str, tracked: set[str], index: dict[str, set[str]]) -> list[str]:
    """The code files a doc describes: those it cites, and, for a package guide, the files it is the
    nearest guide of."""
    files = {path for path, docs in index.items() if doc in docs and is_code(path)}
    if not doc.startswith("docs/"):
        files |= {p for p in tracked if is_code(p) and doc in nearest_guides(p, tracked)}
    return sorted(files)


# --- git ------------------------------------------------------------------------------------------


def git(*args: str) -> str:
    """stdout of a git command run in the current directory (the repository root); raises on failure."""
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout


def tracked_files() -> set[str]:
    return set(git("ls-files").splitlines())


def guide_texts(tracked: set[str]) -> dict[str, str]:
    """{guide doc: its text} for every guide doc in the repository."""
    texts = {}
    for path in sorted(p for p in tracked if is_guide_doc(p)):
        with open(path, encoding="utf-8") as handle:
            texts[path] = handle.read()
    return texts


# --- command line ---------------------------------------------------------------------------------


def run_impact(base: str, head: str, emit: Callable[[str], None] = print) -> int:
    """Warn (never fail) about code the pull request changed without touching the docs describing it."""
    tracked = tracked_files()
    index = citation_index(guide_texts(tracked), tracked)
    changed = git("diff", "--name-only", f"{base}...{head}").splitlines()
    found = impact(changed, tracked, index)
    lines = ["## Docs to re-read", ""]
    if not found:
        lines.append("Every changed code file had one of its describing docs updated, or has none.")
    else:
        lines.append(
            "This pull request changed code without touching any doc that describes it. Re-read "
            "each doc listed: update it if the change made it wrong, or say in the pull request why "
            "no change is needed. This check only warns; it never blocks a merge."
        )
        lines += ["", "| changed file | docs that describe it |", "| --- | --- |"]
        for path, docs in found:
            lines.append(f"| `{path}` | {', '.join(f'`{d}`' for d in docs)} |")
            emit(f"::warning file={path},title=Docs may need an update::Re-read: {', '.join(docs)}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        emit("\n".join(lines))
    return 0


def stale_docs(tracked: set[str], index: dict[str, set[str]]) -> list[tuple[str, int, str]]:
    """(doc, commits to its code since the doc's own last commit, the newest of them) per stale doc."""
    stale = []
    for doc in sorted(p for p in tracked if is_guide_doc(p)):
        files = covered_code(doc, tracked, index)
        doc_commit = git("log", "-1", "--format=%H", "--", doc).strip()
        if not files or not doc_commit:
            continue
        commits = git("log", "--format=%h %s", f"{doc_commit}..HEAD", "--", *files).splitlines()
        if commits:
            stale.append((doc, len(commits), commits[0]))
    return stale


def run_report(emit: Callable[[str], None] = print) -> int:
    """Markdown for the weekly drift issue."""
    tracked = tracked_files()
    stale = stale_docs(tracked, citation_index(guide_texts(tracked), tracked))
    emit(
        "Docs whose code changed after the doc last did. Kept up to date weekly by "
        "`.github/workflows/docs-drift.yml`; how to clear an entry is in "
        "`docs/how-to/work-on-these-docs.md`."
    )
    emit("")
    if not stale:
        emit("Nothing to review: every doc is newer than the code it describes.")
        return 0
    emit(f"{len(stale)} docs to review.")
    emit("")
    emit("| doc | commits to its code since | newest |")
    emit("| --- | --- | --- |")
    for doc, count, newest in stale:
        emit(f"| `{doc}` | {count} | {newest.replace('|', '/')} |")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "impact":
        return run_impact(argv[1], argv[2])
    if argv == ["report"]:
        return run_report()
    print("usage: docs_guides.py impact <base> <head> | report", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
