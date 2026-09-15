"""P3a: the werkzeug-free filename sanitizer.

`allowed_file` was tested here and is gone: it was never called by the application, and the
upload path validates by PARSING the file rather than by trusting its extension. The two are
not equivalent and the difference is visible in the existing upload test, which posts
`("x.pdf", b"not a pdf")` - a name `allowed_file` accepts and the real guard rejects with 400.
"""

from app.services.files import safe_name


def test_safe_name_strips_paths_and_traversal():
    assert safe_name("../../etc/passwd") == "etc_passwd"
    assert safe_name("my report (final).pdf") == "my_report_final.pdf"
    assert safe_name("") == "upload"  # empty -> fallback
    assert safe_name(None) == "upload"
