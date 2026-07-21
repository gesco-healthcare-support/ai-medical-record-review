"""P3a: the werkzeug-free filename sanitizer + extension check."""

from app.services.files import allowed_file, safe_name


def test_safe_name_strips_paths_and_traversal():
    assert safe_name("../../etc/passwd") == "etc_passwd"
    assert safe_name("my report (final).pdf") == "my_report_final.pdf"
    assert safe_name("") == "upload"  # empty -> fallback
    assert safe_name(None) == "upload"


def test_allowed_file():
    assert allowed_file("scan.pdf") is True
    assert allowed_file("scan.PDF") is True
    assert allowed_file("scan.exe") is False
    assert allowed_file("noextension") is False
