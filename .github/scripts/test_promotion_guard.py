"""Unit tests for the promotion order guard's decisions (stdlib unittest; run by .github/workflows/guard-tests.yml).

Every refusal below is a way to skip a stage or smuggle code past main, so each is asserted by name.
"""

import unittest

from promotion_guard import MAX_FILES, Commit, FileChange, decide_branches, decide_hotfix, same_change

REPO = "gesco-healthcare-support/ai-medical-record-review"
FORK = "someone/ai-medical-record-review"
ON_MAIN = "a" * 40
NOT_ON_MAIN = "b" * 40
PICK_1 = "c" * 40
PICK_2 = "d" * 40
FILE = "backend/app/services/reporting.py"

# The change as it landed on main.
SOURCE_PATCH = "@@ -10,6 +10,7 @@ def export():\n     a = 1\n-    b = 2\n+    b = 3\n+    c = 4\n     d = 5\n"
# The same change picked onto staging: other line numbers, other context lines, identical +/- lines.
SHIFTED_PATCH = (
    "@@ -42,6 +42,7 @@ def export():\n     a = 1  # staging\n-    b = 2\n+    b = 3\n+    c = 4\n     z = 9\n"
)
# A decoy: the pick was amended, so one added line differs from what reached main.
AMENDED_PATCH = "@@ -42,6 +42,7 @@ def export():\n     a = 1\n-    b = 2\n+    b = 3\n+    c = 5\n     d = 5\n"


def pick(sha: str, source: str, parents: int = 1) -> Commit:
    """A commit made by `git cherry-pick -x` of `source`."""
    return Commit(sha, f"fix(export): something\n\n(cherry picked from commit {source})\n", parents)


def files(pick_patch: str | None = SHIFTED_PATCH, *extra: FileChange) -> dict[str, list[FileChange] | None]:
    """Files for both picks and the source on main; by default each pick is the source's change, shifted."""
    return {
        ON_MAIN: [FileChange(FILE, SOURCE_PATCH)],
        PICK_1: [FileChange(FILE, pick_patch), *extra],
        PICK_2: [FileChange(FILE, pick_patch), *extra],
    }


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
        commits = [pick(PICK_1, ON_MAIN), pick(PICK_2, ON_MAIN)]
        self.assertIs(decide_hotfix(commits, {ON_MAIN: True}, files())[0], True)

    def test_a_commit_without_the_trailer_is_refused(self):
        commits = [pick(PICK_1, ON_MAIN), Commit(PICK_2, "fix: sneaked in, never on main", 1)]
        verdict, why = decide_hotfix(commits, {ON_MAIN: True}, files())
        self.assertIs(verdict, False)
        self.assertIn("trailer", why)

    def test_a_trailer_naming_a_commit_not_on_main_is_refused(self):
        verdict, why = decide_hotfix([pick(PICK_1, NOT_ON_MAIN)], {NOT_ON_MAIN: False}, files())
        self.assertIs(verdict, False)
        self.assertIn("not on main", why)

    def test_a_merge_commit_is_refused(self):
        verdict, why = decide_hotfix([pick(PICK_1, ON_MAIN, parents=2)], {ON_MAIN: True}, files())
        self.assertIs(verdict, False)
        self.assertIn("merge commit", why)

    def test_an_empty_hotfix_is_refused(self):
        self.assertIs(decide_hotfix([], {}, {})[0], False)

    def test_an_unchecked_source_counts_as_not_on_main(self):
        self.assertIs(decide_hotfix([pick(PICK_1, ON_MAIN)], {}, files())[0], False)


class HotfixChangeMatchesMain(unittest.TestCase):
    """The trailer alone proves nothing; the pick must make the same change as the commit it names."""

    def test_the_same_change_at_other_lines_and_context_passes(self):
        self.assertEqual(same_change(files()[PICK_1], files()[ON_MAIN]), (True, "the same change"))

    def test_an_amended_pick_is_refused_naming_the_file(self):
        verdict, why = decide_hotfix([pick(PICK_1, ON_MAIN)], {ON_MAIN: True}, files(AMENDED_PATCH))
        self.assertIs(verdict, False)
        self.assertIn(f"{FILE} differs", why)

    def test_a_pick_touching_an_extra_file_is_refused_naming_it(self):
        extra = FileChange("backend/app/secret_backdoor.py", "@@ -0,0 +1 @@\n+import os\n")
        verdict, why = decide_hotfix([pick(PICK_1, ON_MAIN)], {ON_MAIN: True}, files(SHIFTED_PATCH, extra))
        self.assertIs(verdict, False)
        self.assertIn("backend/app/secret_backdoor.py", why)

    def test_a_file_without_a_text_diff_is_refused(self):
        verdict, why = decide_hotfix([pick(PICK_1, ON_MAIN)], {ON_MAIN: True}, files(None))
        self.assertIs(verdict, False)
        self.assertIn("no text diff", why)

    def test_unreadable_files_are_refused(self):
        unreadable = {ON_MAIN: None, PICK_1: [FileChange(FILE, SHIFTED_PATCH)]}
        verdict, why = decide_hotfix([pick(PICK_1, ON_MAIN)], {ON_MAIN: True}, unreadable)
        self.assertIs(verdict, False)
        self.assertIn("could not be read", why)

    def test_a_file_list_that_may_be_cut_off_is_refused(self):
        many = [FileChange(f"f{i}.py", "+x\n") for i in range(MAX_FILES)]
        verdict, why = same_change(many, many)
        self.assertIs(verdict, False)
        self.assertIn("more files", why)

    def test_a_commit_list_that_may_be_cut_off_is_refused(self):
        verdict, why = decide_hotfix([pick(PICK_1, ON_MAIN)], {ON_MAIN: True}, files(), complete=False)
        self.assertIs(verdict, False)
        self.assertIn("too many to check", why)


if __name__ == "__main__":
    unittest.main()
