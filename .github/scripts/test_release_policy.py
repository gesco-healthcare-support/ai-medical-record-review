"""Unit tests for the production release policy (stdlib unittest; run by .github/workflows/guard-tests.yml).

Each way the policy can block, or let a finding through, is asserted by name.
"""

import datetime
import unittest

from release_policy import load_exceptions, violations

TODAY = datetime.date(2026, 9, 30)


def grype(*matches):
    """A Grype JSON report holding (vulnerability id, severity, package) matches."""
    return {
        "matches": [
            {"vulnerability": {"id": vid, "severity": severity}, "artifact": {"name": package, "version": "1.0"}}
            for vid, severity, package in matches
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


if __name__ == "__main__":
    unittest.main()
