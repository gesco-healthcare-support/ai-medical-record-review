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

Hotfix check: every commit in the pull request is a plain (one-parent) cherry-pick whose
"(cherry picked from commit <sha>)" trailer names a commit already on main. So a fix still lands on main first, and
the order holds. `git cherry-pick -x` writes that trailer only for conflict-free picks, so a pick with conflicts
cannot use this path.

Decision logic is pure and unit-tested (test_promotion_guard.py); the GitHub API calls are kept out of it.
Commit messages are untrusted input: they are matched with a regex and never executed.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

ALLOWED_HEADS = {"staging": {"main"}, "production": {"staging"}, "qwen": {"main"}}
HOTFIX_PREFIX = "hotfix/"
HOTFIX_BASE = "staging"
TRAILER = re.compile(r"\(cherry picked from commit ([0-9a-f]{40})\)")


@dataclass(frozen=True)
class Commit:
    """One commit of the pull request: its SHA, message and number of parents."""

    sha: str
    message: str
    parents: int


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


def decide_hotfix(commits: list[Commit], on_main: dict[str, bool]) -> tuple[bool, str]:
    """Every commit must be a one-parent cherry-pick whose trailer names a commit on main (`on_main[sha]`)."""
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
    return True, f"allowed: all {len(commits)} commit(s) are cherry-picks of commits on main"


def _get(url: str, token: str) -> object:
    """GET a GitHub API URL as JSON."""
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https API host
        return json.load(response)


def _pr_commits(api: str, repo: str, number: str, token: str) -> list[Commit]:
    """The pull request's commits (the API returns at most 250, which is far beyond any hotfix)."""
    data = _get(f"{api}/repos/{repo}/pulls/{number}/commits?per_page=100", token)
    return [Commit(c["sha"], c["commit"]["message"], len(c["parents"])) for c in data]


def _is_on_main(api: str, repo: str, sha: str, token: str) -> bool:
    """True when `sha` is main or an ancestor of it (compare says main is ahead of it, or identical)."""
    try:
        data = _get(f"{api}/repos/{repo}/compare/{sha}...main", token)
    except urllib.error.HTTPError:
        return False
    return data.get("status") in ("ahead", "identical")


def main() -> int:
    """Read the event from the environment, decide, and print why. Exit 0 = allowed, 1 = refused."""
    env = os.environ
    base, head, this_repo = env["BASE"], env["HEAD"], env["THIS_REPO"]
    verdict, why = decide_branches(base, head, env.get("HEAD_REPO") or None, this_repo)
    if verdict is None:
        api, token = env.get("GITHUB_API_URL", "https://api.github.com"), env["GH_TOKEN"]
        commits = _pr_commits(api, this_repo, env["PR_NUMBER"], token)
        sources = {m for c in commits for m in TRAILER.findall(c.message)[-1:]}
        on_main = {sha: _is_on_main(api, this_repo, sha, token) for sha in sources}
        verdict, why = decide_hotfix(commits, on_main)
    print(why)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
