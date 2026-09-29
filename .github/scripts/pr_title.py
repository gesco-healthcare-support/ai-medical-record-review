"""PR title check for pull requests into main (.github/workflows/pr-title.yml).

A squash merge makes the pull request's title main's commit subject, so the title must follow the repository's commit
format: `<type>(<scope>): <subject>`, with `!` before the colon for a breaking change. The type is one of TYPES; the
scope must be listed in .claude/rules/commit-scopes.md (adding a scope there is part of the pull request that needs
it); the title is ASCII only, has no trailing period, and is at most MAX_LENGTH characters.

Dependabot is exempt from the length limit only: its titles follow the format (its commit-message prefix is set in
.github/dependabot.yml), but their length cannot be configured, and a grouped update's title runs past 72 characters.

The title is untrusted input: it is matched as text and printed, never executed. The decisions are pure and
unit-tested (test_pr_title.py); `main` only reads the environment the workflow sets.
"""

import os
import re
import sys
from pathlib import Path

TYPES = ("feat", "fix", "docs", "chore", "refactor", "perf", "test", "style", "ci", "build")
MAX_LENGTH = 72
DEPENDABOT = "dependabot[bot]"
SCOPES_FILE = Path(__file__).resolve().parents[2] / ".claude" / "rules" / "commit-scopes.md"

_TITLE = re.compile(r"^(?P<type>[a-z]+)\((?P<scope>[a-z0-9-]+)\)!?: \S")
# A scope is a backticked name that opens a `- ` bullet, alone or in a comma-separated run: "- `ocr` - ..." or
# "- `backend`, `frontend` - ...". Backticked words in prose are not scopes.
_SCOPE_BULLET = re.compile(r"^- ((?:`[a-z0-9-]+`(?:, )?)+)")


def read_scopes(text: str) -> set[str]:
    """The scopes listed in commit-scopes.md."""
    scopes: set[str] = set()
    for line in text.splitlines():
        match = _SCOPE_BULLET.match(line)
        if match:
            scopes.update(re.findall(r"`([a-z0-9-]+)`", match.group(1)))
    return scopes


def problems(title: str, author: str, scopes: set[str]) -> list[str]:
    """Every rule `title` breaks, one sentence each; an empty list means it passes."""
    found: list[str] = []
    if not title.isascii():
        found.append("The title must be ASCII only (no smart quotes, dashes or accented letters).")
    if author != DEPENDABOT and len(title) > MAX_LENGTH:
        found.append(f"The title is {len(title)} characters; the limit is {MAX_LENGTH}.")
    if title.endswith("."):
        found.append("The title must not end with a period.")
    match = _TITLE.match(title)
    if match is None:
        found.append(
            "The title must read `<type>(<scope>): <subject>`, with `!` before the colon for a breaking change."
        )
        return found
    if match["type"] not in TYPES:
        found.append(f"`{match['type']}` is not a commit type; use one of: {', '.join(TYPES)}.")
    if match["scope"] not in scopes:
        found.append(
            f"`{match['scope']}` is not a scope in .claude/rules/commit-scopes.md; use a listed one, or add it there."
        )
    return found


def main() -> int:
    """Check the title the workflow passes in PR_TITLE (author in PR_AUTHOR); exit 1 on any problem."""
    scopes = read_scopes(SCOPES_FILE.read_text(encoding="utf-8"))
    if not scopes:
        # Fail loudly: an empty list would otherwise reject every title for a reason nobody could see.
        print(f"No scopes could be read from {SCOPES_FILE}; its bullet format may have changed.")
        return 1
    found = problems(os.environ.get("PR_TITLE", ""), os.environ.get("PR_AUTHOR", ""), scopes)
    for problem in found:
        print(problem)
    if not found:
        print("The title follows the commit format.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
