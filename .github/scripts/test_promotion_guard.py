"""Unit tests for the promotion order guard's decisions (stdlib unittest; run by .github/workflows/guard-tests.yml).

Every refusal below is a way to skip a stage or smuggle code past main, so each is asserted by name.
"""

import unittest

from promotion_guard import Commit, decide_branches, decide_hotfix

REPO = "gesco-healthcare-support/ai-medical-record-review"
FORK = "someone/ai-medical-record-review"
ON_MAIN = "a" * 40
NOT_ON_MAIN = "b" * 40


def pick(sha: str, source: str, parents: int = 1) -> Commit:
    """A commit made by `git cherry-pick -x` of `source`."""
    return Commit(sha, f"fix(export): something\n\n(cherry picked from commit {source})\n", parents)


class AllowedPromotions(unittest.TestCase):
    def test_main_into_staging(self):
        self.assertIs(decide_branches("staging", "main", REPO, REPO)[0], True)

    def test_staging_into_production(self):
        self.assertIs(decide_branches("production", "staging", REPO, REPO)[0], True)

    def test_main_into_qwen(self):
        self.assertIs(decide_branches("qwen", "main", REPO, REPO)[0], True)

    def test_a_base_that_is_not_a_promotion_branch_is_not_judged(self):
        self.assertIs(decide_branches("main", "feat/anything", FORK, REPO)[0], True)


class RefusedPromotions(unittest.TestCase):
    def test_a_feature_branch_cannot_skip_main_into_staging(self):
        self.assertIs(decide_branches("staging", "feat/x", REPO, REPO)[0], False)

    def test_main_cannot_skip_staging_into_production(self):
        self.assertIs(decide_branches("production", "main", REPO, REPO)[0], False)

    def test_a_hotfix_branch_cannot_go_straight_to_production(self):
        self.assertIs(decide_branches("production", "hotfix/urgent", REPO, REPO)[0], False)

    def test_staging_cannot_feed_qwen(self):
        self.assertIs(decide_branches("qwen", "staging", REPO, REPO)[0], False)

    def test_a_fork_branch_named_main_is_refused(self):
        verdict, why = decide_branches("staging", "main", FORK, REPO)
        self.assertIs(verdict, False)
        self.assertIn(FORK, why)

    def test_a_deleted_head_repository_is_refused(self):
        self.assertIs(decide_branches("production", "staging", None, REPO)[0], False)


class HotfixPath(unittest.TestCase):
    def test_a_hotfix_branch_into_staging_goes_to_the_hotfix_check(self):
        self.assertIsNone(decide_branches("staging", "hotfix/export-crash", REPO, REPO)[0])

    def test_cherry_picks_of_commits_on_main_pass(self):
        commits = [pick("c" * 40, ON_MAIN), pick("d" * 40, ON_MAIN)]
        self.assertIs(decide_hotfix(commits, {ON_MAIN: True})[0], True)

    def test_a_commit_without_the_trailer_is_refused(self):
        commits = [pick("c" * 40, ON_MAIN), Commit("d" * 40, "fix: sneaked in, never on main", 1)]
        verdict, why = decide_hotfix(commits, {ON_MAIN: True})
        self.assertIs(verdict, False)
        self.assertIn("trailer", why)

    def test_a_trailer_naming_a_commit_not_on_main_is_refused(self):
        verdict, why = decide_hotfix([pick("c" * 40, NOT_ON_MAIN)], {NOT_ON_MAIN: False})
        self.assertIs(verdict, False)
        self.assertIn("not on main", why)

    def test_a_merge_commit_is_refused(self):
        verdict, why = decide_hotfix([pick("c" * 40, ON_MAIN, parents=2)], {ON_MAIN: True})
        self.assertIs(verdict, False)
        self.assertIn("merge commit", why)

    def test_an_empty_hotfix_is_refused(self):
        self.assertIs(decide_hotfix([], {})[0], False)

    def test_an_unchecked_source_counts_as_not_on_main(self):
        self.assertIs(decide_hotfix([pick("c" * 40, ON_MAIN)], {})[0], False)


if __name__ == "__main__":
    unittest.main()
