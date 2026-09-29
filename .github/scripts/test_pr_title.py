"""Unit tests for the PR title check (stdlib unittest; run by .github/workflows/guard-tests.yml).

Each rule is asserted failing on its own, so a rule that stops firing fails a test by name.
"""

import os
import unittest
from unittest import mock

from pr_title import MAX_LENGTH, SCOPES_FILE, main, problems, read_scopes

SCOPES = {"api", "ci", "docs", "quality", "tooling", "export"}
HUMAN = "AdrianG916"
DEPENDABOT = "dependabot[bot]"
# A real Dependabot group title (2026-09-29, #472): 88 characters, well-formed, with a listed scope.
DEPENDABOT_GROUP = "chore(tooling): bump the python-minor-and-patch group across 1 directory with 13 updates"


class ScopeListTest(unittest.TestCase):
    def test_the_real_scope_file_parses_to_the_full_list(self):
        # A silent parse failure would reject every title (or, worse, a later "no scopes" guard would be removed),
        # so the real file must yield the scopes the repo uses.
        scopes = read_scopes(SCOPES_FILE.read_text(encoding="utf-8"))
        self.assertTrue({"ci", "docs", "quality", "tooling", "backend", "frontend", "export"} <= scopes)
        self.assertGreaterEqual(len(scopes), 28)  # the file lists 28 on 2026-09-29

    def test_only_backticked_names_opening_a_bullet_are_scopes(self):
        text = "Intro with `notascope` in prose.\n\n- `ocr` - OCR\n- `backend`, `frontend` - a side\n"
        self.assertEqual(read_scopes(text), {"ocr", "backend", "frontend"})


class TitleRulesTest(unittest.TestCase):
    def test_a_well_formed_title_passes(self):
        self.assertEqual(problems("ci(quality): lint containers and check PR titles", HUMAN, SCOPES), [])

    def test_a_breaking_change_marker_is_allowed(self):
        self.assertEqual(problems("feat(api)!: remove the legacy export route", HUMAN, SCOPES), [])

    def test_a_missing_scope_fails(self):
        self.assertEqual(len(problems("fix: repair the export", HUMAN, SCOPES)), 1)

    def test_a_missing_space_after_the_colon_fails(self):
        self.assertEqual(len(problems("fix(api):repair the export", HUMAN, SCOPES)), 1)

    def test_an_unknown_type_fails(self):
        found = problems("feature(api): add a route", HUMAN, SCOPES)
        self.assertEqual(len(found), 1)
        self.assertIn("feature", found[0])

    def test_an_unlisted_scope_fails(self):
        found = problems("chore(qwen): bring qwen up to main", HUMAN, SCOPES)
        self.assertEqual(len(found), 1)
        self.assertIn("qwen", found[0])

    def test_a_trailing_period_fails(self):
        self.assertEqual(len(problems("fix(api): repair the export.", HUMAN, SCOPES)), 1)

    def test_a_non_ascii_title_fails(self):
        accented = "fix(api): repair the caf" + chr(0xE9) + " export"  # built with chr() so this file stays ASCII
        self.assertEqual(len(problems(accented, HUMAN, SCOPES)), 1)

    def test_a_human_title_at_the_limit_passes_and_one_over_fails(self):
        at_limit = "fix(api): " + "x" * (MAX_LENGTH - len("fix(api): "))
        self.assertEqual(len(at_limit), MAX_LENGTH)
        self.assertEqual(problems(at_limit, HUMAN, SCOPES), [])
        self.assertEqual(len(problems(at_limit + "x", HUMAN, SCOPES)), 1)

    def test_dependabot_is_exempt_from_the_length_limit_only(self):
        self.assertGreater(len(DEPENDABOT_GROUP), MAX_LENGTH)
        self.assertEqual(problems(DEPENDABOT_GROUP, DEPENDABOT, SCOPES), [])
        self.assertEqual(len(problems(DEPENDABOT_GROUP, HUMAN, SCOPES)), 1)
        self.assertEqual(len(problems("chore(deps): bump x from 1 to 2", DEPENDABOT, SCOPES)), 1)


class MainTest(unittest.TestCase):
    def run_main(self, title, author=HUMAN):
        with mock.patch.dict(os.environ, {"PR_TITLE": title, "PR_AUTHOR": author}), mock.patch("builtins.print"):
            return main()

    def test_a_passing_title_exits_zero(self):
        self.assertEqual(self.run_main("ci(quality): lint containers and check PR titles"), 0)

    def test_a_failing_title_exits_one(self):
        self.assertEqual(self.run_main("chore(qwen): bring qwen up to main"), 1)


if __name__ == "__main__":
    unittest.main()
