"""Unit tests for the docs-drift logic (stdlib unittest; run by .github/workflows/docs-drift.yml).

The repo-wide checks built on this module run in the backend suite
(backend/tests/test_docs_guides.py); these pin the rules themselves, each on a small invented tree.
"""

import unittest

from docs_guides import (
    candidates,
    citation_index,
    covering_docs,
    impact,
    is_code,
    is_guide_doc,
    nearest_guides,
    path_citations,
    readme_missing,
    resolve,
    symbol_citations,
    symbol_in_source,
)

TRACKED = {
    "README.md",
    "CLAUDE.md",
    "docker-compose.yml",
    "backend/README.md",
    "backend/CLAUDE.md",
    "backend/app/api/README.md",
    "backend/app/api/documents.py",
    "backend/app/services/jobs.py",
    "backend/app/services/__init__.py",
    "backend/app/worker/CLAUDE.md",
    "backend/tests/test_jobs.py",
    "docs/reference/http-api.md",
    "docs/plans/old-plan.md",
    "docs/backlog.md",
    "legacy/README.md",
    "frontend/lib/review-api.ts",
    "frontend/lib/review-api.test.ts",
}


class GuideDocs(unittest.TestCase):
    def test_site_pages_and_package_guides_are_guides(self):
        for path in ("docs/reference/http-api.md", "backend/README.md", "backend/app/worker/CLAUDE.md"):
            self.assertTrue(is_guide_doc(path), path)

    def test_plans_backlog_legacy_and_code_are_not(self):
        for path in ("docs/plans/old-plan.md", "docs/backlog.md", "legacy/README.md", "backend/app/api/documents.py"):
            self.assertFalse(is_guide_doc(path), path)


class Citations(unittest.TestCase):
    def test_backticked_paths_are_found_and_routes_urls_and_placeholders_are_not(self):
        text = (
            "See `backend/app/services/jobs.py` and `./deploy/nginx.conf`, not `/api/documents/{id}`, "
            "`https://example.com/a.py`, `<folder>/x.py`, `*.py` or a bare `jobs.py`."
        )
        self.assertEqual(path_citations(text), ["backend/app/services/jobs.py", "./deploy/nginx.conf"])

    def test_a_path_resolves_from_the_citing_folder_or_any_folder_above_it(self):
        self.assertEqual(
            resolve("backend/app/worker/CLAUDE.md", "services/jobs.py", TRACKED), "backend/app/services/jobs.py"
        )
        self.assertEqual(resolve("docs/reference/http-api.md", "./docker-compose.yml", TRACKED), "docker-compose.yml")
        self.assertIsNone(resolve("backend/README.md", "app/services/gone.py", TRACKED))

    def test_site_pages_may_cite_from_the_backend_or_frontend_package_root(self):
        self.assertEqual(
            resolve("docs/reference/http-api.md", "app/api/documents.py", TRACKED), "backend/app/api/documents.py"
        )
        self.assertEqual(
            resolve("docs/reference/http-api.md", "lib/review-api.ts", TRACKED), "frontend/lib/review-api.ts"
        )
        self.assertEqual(
            candidates("docs/how-to/x.md", "coverage/a.json"),
            [
                "docs/how-to/coverage/a.json",
                "docs/coverage/a.json",
                "coverage/a.json",
                "backend/coverage/a.json",
                "frontend/coverage/a.json",
            ],
        )

    def test_path_and_symbol_pairs_skip_filenames_and_foreign_dotted_names(self):
        text = (
            "`backend/app/services/jobs.py` `enqueue`; `frontend/lib/review-api.ts` (`extractHeader()`); "
            "`pyproject.toml` `uv.lock`; `scripts/copy_records.py`, `failures._STATE_OUTCOMES`; "
            "`backend/app/services/jobs.py` `jobs.create_job`"
        )
        self.assertEqual(
            symbol_citations(text),
            [
                ("backend/app/services/jobs.py", "enqueue"),
                ("frontend/lib/review-api.ts", "extractHeader"),
                ("backend/app/services/jobs.py", "create_job"),
            ],
        )

    def test_a_symbol_matches_at_a_left_word_boundary_only(self):
        source = 'def enqueue(kind):\n    return VERSIONS / "b3f7c02e91a4_utilization_review_category.py"\n'
        self.assertTrue(symbol_in_source("enqueue", source))
        self.assertTrue(symbol_in_source("b3f7c02e91a4", source))  # a migration id inside a file name
        self.assertFalse(symbol_in_source("queue", source))  # inside another name: not a match
        self.assertFalse(symbol_in_source("create_job", source))


class ReadmeLists(unittest.TestCase):
    def test_every_file_in_the_folder_is_named_except_init_dotfiles_and_lockfiles(self):
        readme = "| `documents.py` | the routes |"
        here = ["documents.py", "downloads.py", "__init__.py", ".gitignore", "uv.lock", "pnpm-lock.yaml"]
        self.assertEqual(readme_missing(readme, here), ["downloads.py"])

    def test_a_name_counts_anywhere_in_backticks_but_not_as_a_substring(self):
        readme = "Tests: `test_jobs.py`, `test_jobs_extra.py`."
        self.assertEqual(readme_missing(readme, ["test_jobs.py", "jobs.py"]), ["jobs.py"])


class Coverage(unittest.TestCase):
    def test_code_excludes_markdown_docs_tests_lockfiles_and_legacy(self):
        self.assertTrue(is_code("backend/app/services/jobs.py"))
        self.assertTrue(is_code("docker-compose.yml"))
        for path in (
            "backend/README.md",
            "docs/reference/http-api.md",
            "backend/tests/test_jobs.py",
            "frontend/lib/review-api.test.ts",
            "backend/uv.lock",
            "legacy/mrr_ai/app.py",
        ):
            self.assertFalse(is_code(path), path)

    def test_the_nearest_folder_with_a_guide_covers_a_file(self):
        self.assertEqual(nearest_guides("backend/app/api/documents.py", TRACKED), ["backend/app/api/README.md"])
        # services/ has no guide of its own, so backend/ covers it.
        self.assertEqual(
            nearest_guides("backend/app/services/jobs.py", TRACKED), ["backend/CLAUDE.md", "backend/README.md"]
        )
        self.assertEqual(nearest_guides("docker-compose.yml", TRACKED), ["CLAUDE.md", "README.md"])

    def test_a_page_covers_the_files_it_cites(self):
        index = citation_index(
            {"docs/reference/http-api.md": "Routes live in `backend/app/api/documents.py`."}, TRACKED
        )
        self.assertEqual(index, {"backend/app/api/documents.py": {"docs/reference/http-api.md"}})
        self.assertEqual(
            covering_docs("backend/app/api/documents.py", TRACKED, index),
            ["backend/app/api/README.md", "docs/reference/http-api.md"],
        )


class Impact(unittest.TestCase):
    INDEX = {"backend/app/api/documents.py": {"docs/reference/http-api.md"}}

    def test_a_code_change_with_no_covering_doc_touched_is_reported(self):
        found = impact(["backend/app/api/documents.py"], TRACKED, self.INDEX)
        self.assertEqual(
            found, [("backend/app/api/documents.py", ["backend/app/api/README.md", "docs/reference/http-api.md"])]
        )

    def test_touching_any_one_covering_doc_clears_it(self):
        self.assertEqual(
            impact(["backend/app/api/documents.py", "docs/reference/http-api.md"], TRACKED, self.INDEX), []
        )

    def test_tests_and_docs_alone_are_never_reported(self):
        self.assertEqual(impact(["backend/tests/test_jobs.py", "backend/README.md"], TRACKED, self.INDEX), [])


if __name__ == "__main__":
    unittest.main()
