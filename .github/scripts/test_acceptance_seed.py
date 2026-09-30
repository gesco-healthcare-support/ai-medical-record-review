"""Unit tests for the staging acceptance seed script's pure parts (stdlib unittest; run by guard-tests.yml).

The HTTP calls are exercised end to end by the acceptance stage itself; these pin the decisions it relies on.
"""

import re
import unittest

from acceptance_seed import (
    SESSION_COOKIE,
    multipart_pdf,
    register_body,
    rows_payload,
    session_cookie,
    verify,
)

DOC_ID = "5f0c2b1e-0000-4000-8000-000000000001"


class RequestBodiesTest(unittest.TestCase):
    def test_the_synthetic_password_meets_the_apps_rules(self):
        # app/auth/users.py validate_password: at least 8 characters, a number and a symbol.
        body = register_body()
        self.assertGreaterEqual(len(body["password"]), 8)
        self.assertRegex(body["password"], r"\d")
        self.assertRegex(body["password"], r"[^A-Za-z0-9]")
        self.assertIn("name", body)

    def test_the_synthetic_address_is_one_the_app_accepts(self):
        # The backend validates addresses with email-validator, which rejects special-use top-level names with a
        # 422 on /api/auth/register. Its list in 2.3.0 (SPECIAL_USE_DOMAIN_NAMES), copied because this test runs on
        # the standard library only. example.com is reserved for documentation (RFC 2606), so it stays synthetic.
        rejected_by_email_validator = {"arpa", "invalid", "local", "localhost", "onion", "test"}
        email = register_body()["email"]
        self.assertNotIn(email.rsplit(".", 1)[-1], rejected_by_email_validator)
        self.assertTrue(email.endswith("@example.com"), "a synthetic address on the documentation domain")

    def test_the_rows_are_valid_for_a_three_page_record(self):
        rows = rows_payload()["rows"]
        self.assertEqual(len(rows), 3)
        previous_end = 0
        for row in rows:
            self.assertTrue(1 <= row["start"] <= row["end"] <= 3)
            self.assertGreater(row["start"], previous_end, "rows must not overlap")
            previous_end = row["end"]
            self.assertIsInstance(row["category"], str)

    def test_the_upload_body_carries_the_pdf_under_the_field_the_api_reads(self):
        body, content_type = multipart_pdf(b"%PDF-1.4 synthetic", "sample.pdf")
        boundary = re.search(r"boundary=(\S+)", content_type).group(1)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="pdf"; filename="sample.pdf"', body)
        self.assertIn(b"Content-Type: application/pdf", body)
        self.assertIn(b"%PDF-1.4 synthetic", body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


class SessionCookieTest(unittest.TestCase):
    def test_the_session_cookie_is_picked_out_of_several_headers(self):
        headers = [
            "other=1; Path=/",
            f"{SESSION_COOKIE}=abc123; HttpOnly; Max-Age=43200; Path=/; SameSite=lax; Secure",
        ]
        self.assertEqual(session_cookie(headers), f"{SESSION_COOKIE}=abc123")

    def test_a_missing_session_cookie_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            session_cookie(["other=1; Path=/"])


class VerifyTest(unittest.TestCase):
    def expected(self):
        return {"document_id": DOC_ID, "rows": rows_payload()["rows"]}

    def listing(self, rows_count=3):
        return [{"id": "some-other-document", "rows_count": 1}, {"id": DOC_ID, "rows_count": rows_count}]

    def detail(self, rows=None):
        return {"id": DOC_ID, "rows": rows if rows is not None else [dict(r) for r in rows_payload()["rows"]]}

    def test_data_that_survived_the_upgrade_passes(self):
        self.assertEqual(verify(self.expected(), self.listing(), self.detail()), [])

    def test_a_document_missing_from_the_listing_fails(self):
        self.assertEqual(len(verify(self.expected(), [{"id": "x", "rows_count": 3}], self.detail())), 1)

    def test_a_wrong_row_count_in_the_listing_fails(self):
        self.assertEqual(len(verify(self.expected(), self.listing(rows_count=2), self.detail())), 1)

    def test_a_changed_row_fails(self):
        rows = [dict(r) for r in rows_payload()["rows"]]
        rows[1]["category"] = "999"
        self.assertEqual(len(verify(self.expected(), self.listing(), self.detail(rows))), 1)

    def test_extra_fields_the_api_adds_to_a_row_are_ignored(self):
        rows = [dict(r, ruled_paperwork=False, include=True) for r in rows_payload()["rows"]]
        self.assertEqual(verify(self.expected(), self.listing(), self.detail(rows)), [])


if __name__ == "__main__":
    unittest.main()
