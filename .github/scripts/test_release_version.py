"""Unit tests for the release version rule (stdlib unittest; run by .github/workflows/guard-tests.yml).

Each bump level is asserted from real-shaped commit messages, so a rule that stops firing fails a test by name.
"""

import unittest

from release_version import FIRST, bump, next_version

FIX = "fix(export): keep the header on page two\n\n- the header dropped after a page break"
FEAT = "feat(review): an admin can open another reviewer's record (#486)\n\n- admins support reviewers"
BANG = "feat(api)!: remove the legacy export route\n\n- nothing calls it"
FOOTER = "refactor(api): rename the export payload\n\nBREAKING CHANGE: clients must send `rows`, not `items`"
MERGE = "Merge pull request #437 from gesco-healthcare-support/staging\n\nchore(production): promote staging"


class BumpTest(unittest.TestCase):
    def test_fixes_only_bump_the_patch(self):
        self.assertEqual(bump([FIX, FIX]), "patch")

    def test_a_feature_bumps_the_minor(self):
        self.assertEqual(bump([FIX, FEAT]), "minor")

    def test_a_bang_before_the_colon_is_breaking(self):
        self.assertEqual(bump([FIX, FEAT, BANG]), "major")

    def test_a_breaking_change_footer_is_breaking(self):
        self.assertEqual(bump([FIX, FOOTER]), "major")

    def test_a_merge_commit_does_not_count_as_its_body(self):
        # A promotion's merge commit repeats a title in its body; only a commit's own subject and footers count.
        self.assertEqual(bump([MERGE]), "patch")

    def test_no_commits_is_a_patch(self):
        self.assertEqual(bump([]), "patch")


class NextVersionTest(unittest.TestCase):
    def test_the_first_release_is_the_agreed_start_whatever_the_bump(self):
        self.assertEqual(FIRST, "v0.1.0")
        for level in ("patch", "minor", "major"):
            self.assertEqual(next_version(None, level), FIRST)

    def test_each_level_on_a_0x_version(self):
        self.assertEqual(next_version("v0.1.0", "patch"), "v0.1.1")
        self.assertEqual(next_version("v0.1.0", "minor"), "v0.2.0")
        self.assertEqual(next_version("v0.4.3", "major"), "v1.0.0")

    def test_each_level_after_1_0(self):
        self.assertEqual(next_version("v1.2.3", "patch"), "v1.2.4")
        self.assertEqual(next_version("v1.2.3", "minor"), "v1.3.0")
        self.assertEqual(next_version("v1.2.3", "major"), "v2.0.0")

    def test_a_tag_that_is_not_a_version_is_refused(self):
        with self.assertRaises(ValueError):
            next_version("release-2026", "patch")


if __name__ == "__main__":
    unittest.main()
