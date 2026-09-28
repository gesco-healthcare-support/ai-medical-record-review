"""Promotion order guard: a pull request into a promotion branch must come from the branch above it.

The cascade is main -> staging -> production, with qwen fed from main. GitHub has no rule for "PRs into X must come
from Y", so this check does it; each branch's ruleset requires the check named `guard-into-<branch>`. The workflow
(.github/workflows/promotion-guard.yml) runs on `pull_request_target`, so the file that decides is always the
default branch's copy: a pull request cannot edit the guard that judges it.

Allowed, and only from THIS repository (a fork can name a branch `main` too):

    staging    <- main, or a hotfix/* branch passing the hotfix check
    production <- staging
    qwen       <- main
    any other base: allowed; nothing requires that check.

Hotfix check: every commit in the pull request must be a plain (one-parent) cherry-pick whose
"(cherry picked from commit <sha>)" trailer names a commit already on main, AND must make the same change as that
commit. The trailer alone proves nothing: an amended pick, or a trailer typed by hand after a conflicted pick, would
carry code that never reached main. So per file, the lines each commit adds and removes must match. Hunk line numbers
and context lines are ignored, because a pick onto staging lands at different lines. Anything that cannot be compared
is refused: a file with no text diff (binary, or too large for the API), a commit whose file list the API may have cut
off, or a pull request too long to list in one page. With that, a fix lands on main first and the order holds.

Decision logic is pure and unit-tested (test_promotion_guard.py); the GitHub API calls are kept out of it.
Commit messages and patches are untrusted input: they are matched and compared as text, never executed.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

ALLOWED_HEADS = {"staging": {"main"}, "production": {"staging"}, "qwen": {"main"}}
HOTFIX_PREFIX = "hotfix/"
HOTFIX_BASE = "staging"
TRAILER = re.compile(r"\(cherry picked from commit ([0-9a-f]{40})\)")
# One page of the pull request's commits. A hotfix that long is refused rather than half-checked.
MAX_COMMITS = 100
# GitHub's single-commit API lists at most 300 files; at that count the list may have been cut off.
MAX_FILES = 300


@dataclass(frozen=True)
class Commit:
    """One commit of the pull request: its SHA, message and number of parents."""

    sha: str
    message: str
    parents: int


@dataclass(frozen=True)
class FileChange:
    """One file a commit touches, with its unified-diff patch (None when the API gives no text diff)."""

    filename: str
    patch: str | None


def decide_branches(base: str, head: str, head_repo: str | None, this_repo: str) -> tuple[bool | None, str]:
    """Judge base and head alone. Returns (True, why), (False, why), or (None, why) when the hotfix check decides."""
    if base not in ALLOWED_HEADS:
        return True, f"{base} is not a promotion branch; nothing requires this check"
    if head_repo != this_repo:
        return False, f"refused: the head branch comes from {head_repo or 'a deleted repository'}, not {this_repo}"
    if head in ALLOWED_HEADS[base]:
        return True, f"allowed: {head} -> {base}"
    if base == HOTFIX_BASE and head.startswith(HOTFIX_PREFIX):
        return None, f"{head} -> {base}: the hotfix check decides"
    allowed = ", ".join(sorted(ALLOWED_HEADS[base]))
    return False, f"refused: {head} -> {base}; {base} accepts only {allowed}" + (
        " or a hotfix/* cherry-pick of commits on main" if base == HOTFIX_BASE else ""
    )


def changed_lines(patch: str) -> list[str]:
    """The added and removed lines of a unified-diff patch, in order; hunk headers and context are dropped."""
    return [line for line in patch.splitlines() if line.startswith(("+", "-"))]


def same_change(pick: list[FileChange] | None, source: list[FileChange] | None) -> tuple[bool, str]:
    """True when both commits touch the same files with the same added and removed lines."""
    if pick is None or source is None:
        return False, "its files could not be read"
    if len(pick) >= MAX_FILES or len(source) >= MAX_FILES:
        return False, f"it touches {MAX_FILES} or more files, more than the API lists in full"
    pick_files = {f.filename: f.patch for f in pick}
    source_files = {f.filename: f.patch for f in source}
    differing = sorted(pick_files.keys() ^ source_files.keys())
    if differing:
        return False, f"{differing[0]} is changed by only one of the two commits"
    for name in sorted(pick_files):
        pick_patch, source_patch = pick_files[name], source_files[name]
        if pick_patch is None or source_patch is None:
            return False, f"{name} has no text diff to compare (binary, or too large)"
        if changed_lines(pick_patch) != changed_lines(source_patch):
            return False, f"{name} differs"
    return True, "the same change"


def decide_hotfix(
    commits: list[Commit],
    on_main: dict[str, bool],
    files: dict[str, list[FileChange] | None],
    complete: bool = True,
) -> tuple[bool, str]:
    """Every commit must be a one-parent cherry-pick of a commit on main (`on_main[sha]`) making the same change.

    `files` maps a commit SHA to the files it touches (None when unreadable); `complete` is False when the pull
    request's commit list may have been cut off at MAX_COMMITS."""
    if not complete:
        return False, f"refused: the hotfix pull request has {MAX_COMMITS} or more commits; too many to check"
    if not commits:
        return False, "refused: the hotfix pull request has no commits"
    for commit in commits:
        short = commit.sha[:7]
        if commit.parents != 1:
            return False, f"refused: {short} is a merge commit; a hotfix holds cherry-picks only"
        found = TRAILER.findall(commit.message)
        if not found:
            return False, f"refused: {short} has no '(cherry picked from commit ...)' trailer; use git cherry-pick -x"
        source = found[-1]
        if not on_main.get(source, False):
            return False, f"refused: {short} names {source[:7]}, which is not on main"
        same, why = same_change(files.get(commit.sha), files.get(source))
        if not same:
            return False, f"refused: {short} is not the same change as {source[:7]}: {why}"
    return True, f"allowed: all {len(commits)} commit(s) are cherry-picks of commits on main, change for change"


def _get(url: str, token: str) -> Any:
    """GET a GitHub API URL as parsed JSON: a list for the commits endpoint, a dict for compare and a commit."""
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https API host
        return json.load(response)


def _pr_commits(api: str, repo: str, number: str, token: str) -> tuple[list[Commit], bool]:
    """The pull request's commits (one page), and whether that page is the whole list."""
    data = _get(f"{api}/repos/{repo}/pulls/{number}/commits?per_page={MAX_COMMITS}", token)
    commits = [Commit(c["sha"], c["commit"]["message"], len(c["parents"])) for c in data]
    return commits, len(commits) < MAX_COMMITS


def _is_on_main(api: str, repo: str, sha: str, token: str) -> bool:
    """True when `sha` is main or an ancestor of it (compare says main is ahead of it, or identical)."""
    try:
        data = _get(f"{api}/repos/{repo}/compare/{sha}...main", token)
    except urllib.error.HTTPError:
        return False
    return data.get("status") in ("ahead", "identical")


def _commit_files(api: str, repo: str, sha: str, token: str) -> list[FileChange] | None:
    """The files one commit touches, with their patches; None when the commit cannot be read."""
    try:
        data = _get(f"{api}/repos/{repo}/commits/{sha}", token)
    except urllib.error.HTTPError:
        return None
    return [FileChange(f["filename"], f.get("patch")) for f in data.get("files", [])]


def main() -> int:
    """Read the event from the environment, decide, and print why. Exit 0 = allowed, 1 = refused."""
    env = os.environ
    base, head, this_repo = env["BASE"], env["HEAD"], env["THIS_REPO"]
    verdict, why = decide_branches(base, head, env.get("HEAD_REPO") or None, this_repo)
    if verdict is None:
        api, token = env.get("GITHUB_API_URL", "https://api.github.com"), env["GH_TOKEN"]
        commits, complete = _pr_commits(api, this_repo, env["PR_NUMBER"], token)
        sources = {m for c in commits for m in TRAILER.findall(c.message)[-1:]}
        on_main = {sha: _is_on_main(api, this_repo, sha, token) for sha in sources}
        wanted = {c.sha for c in commits} | {sha for sha in sources if on_main[sha]}
        files = {sha: _commit_files(api, this_repo, sha, token) for sha in wanted}
        verdict, why = decide_hotfix(commits, on_main, files, complete)
    print(why)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
