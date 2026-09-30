"""Unit tests for the production release policy (stdlib unittest; run by .github/workflows/guard-tests.yml).

Each way the policy can block, or let a finding through, is asserted by name.
"""

import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from release_policy import advisories, load_exceptions, main, violations

TODAY = datetime.date(2026, 9, 30)


def grype(*matches):
    """A Grype JSON report holding (vulnerability id, severity, package[, fix state]) matches; the state defaults
    to "fixed", Grype's value when a fixed version exists."""
    return {
        "matches": [
            {
                "vulnerability": {"id": m[0], "severity": m[1], "fix": {"state": m[3] if len(m) > 3 else "fixed"}},
                "artifact": {"name": m[2], "version": "1.0"},
            }
            for m in matches
        ]
    }


def exception(vulnerability="CVE-2026-0001", package="openssl", expires="2026-10-31", reason="no fix upstream yet"):
    return {"vulnerability": vulnerability, "package": package, "reason": reason, "expires": expires}


class ExceptionsTest(unittest.TestCase):
    def test_a_well_formed_current_exception_is_kept(self):
        kept, problems = load_exceptions({"exceptions": [exception()]}, TODAY)
        self.assertEqual(len(kept), 1)
        self.assertEqual(problems, [])

    def test_an_expired_exception_is_a_problem_even_if_unused(self):
        kept, problems = load_exceptions({"exceptions": [exception(expires="2026-09-29")]}, TODAY)
        self.assertEqual(kept, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("expired", problems[0])

    def test_an_exception_expiring_today_still_holds(self):
        kept, problems = load_exceptions({"exceptions": [exception(expires="2026-09-30")]}, TODAY)
        self.assertEqual((len(kept), problems), (1, []))

    def test_an_exception_without_a_reason_is_a_problem(self):
        _, problems = load_exceptions({"exceptions": [exception(reason="")]}, TODAY)
        self.assertEqual(len(problems), 1)

    def test_an_unreadable_expiry_is_a_problem(self):
        _, problems = load_exceptions({"exceptions": [exception(expires="next week")]}, TODAY)
        self.assertEqual(len(problems), 1)

    def test_an_empty_file_is_fine(self):
        self.assertEqual(load_exceptions({"exceptions": []}, TODAY), ([], []))


class ViolationsTest(unittest.TestCase):
    def test_low_and_medium_findings_pass(self):
        report = grype(("CVE-1", "Low", "zlib"), ("CVE-2", "Medium", "zlib"), ("CVE-3", "Negligible", "zlib"))
        self.assertEqual(violations(report, []), [])

    def test_a_high_finding_blocks(self):
        self.assertEqual(len(violations(grype(("CVE-2026-0001", "High", "openssl")), [])), 1)

    def test_a_critical_finding_blocks(self):
        found = violations(grype(("CVE-2026-0002", "Critical", "libxml2")), [])
        self.assertEqual(len(found), 1)
        self.assertIn("CVE-2026-0002", found[0])

    def test_an_exception_for_that_vulnerability_and_package_lets_it_through(self):
        self.assertEqual(violations(grype(("CVE-2026-0001", "High", "openssl")), [exception()]), [])

    def test_an_exception_for_another_package_does_not(self):
        report = grype(("CVE-2026-0001", "High", "libssl3"))
        self.assertEqual(len(violations(report, [exception(package="openssl")])), 1)

    def test_an_empty_report_passes(self):
        self.assertEqual(violations({"matches": []}, []), [])

    # Scope (Adrian, 2026-09-29): every Critical blocks; a High blocks only when a fixed version exists.
    def test_a_high_with_no_fix_does_not_block(self):
        self.assertEqual(violations(grype(("CVE-2026-0003", "High", "libheif1", "not-fixed")), []), [])

    def test_a_high_the_distro_will_not_fix_does_not_block(self):
        self.assertEqual(violations(grype(("CVE-2026-0004", "High", "libheif1", "wont-fix")), []), [])

    def test_a_critical_with_no_fix_still_blocks(self):
        found = violations(grype(("CVE-2026-0005", "Critical", "libgd3", "not-fixed")), [])
        self.assertEqual(len(found), 1)
        self.assertIn("CVE-2026-0005", found[0])

    def test_highs_with_no_fix_are_listed_and_fixable_ones_are_not(self):
        report = grype(("CVE-2026-0006", "High", "libheif1", "not-fixed"), ("CVE-2026-0007", "High", "openssl"))
        listed = advisories(report)
        self.assertEqual(len(listed), 1)
        self.assertIn("CVE-2026-0006", listed[0])


class MainTest(unittest.TestCase):
    """The command line: the blocking run and the staging stage's report-only run over the same inputs."""

    def run_main(self, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            exceptions, report, summary = Path(tmp, "exc.json"), Path(tmp, "grype-x.json"), Path(tmp, "summary.md")
            exceptions.write_text(json.dumps({"exceptions": []}), "utf-8")
            report.write_text(json.dumps(grype(("CVE-2026-0008", "High", "openssl"))), "utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
                code = main(["--exceptions", str(exceptions), *extra, str(report)])
            return code, summary.read_text("utf-8") if summary.exists() else ""

    def test_a_violation_fails_the_blocking_run(self):
        self.assertEqual(self.run_main()[0], 1)

    def test_report_only_never_fails_but_still_names_the_violation(self):
        code, summary = self.run_main("--report-only")
        self.assertEqual(code, 0)
        self.assertIn("CVE-2026-0008", summary)


if __name__ == "__main__":
    unittest.main()
