"""Seed synthetic data through the app's API, then check it survived (the staging acceptance upgrade test).

The stage (.github/scripts/staging_acceptance.sh) starts PRODUCTION's code, runs `seed` against it, upgrades the
database with STAGING's migrations, starts staging's code and runs `verify`. Seeding through the API rather than SQL
keeps this script independent of the schema: it creates what a reviewer would - an account, one uploaded record, its
review rows - by the same routes the frontend uses.

All data is synthetic: an address on the reserved `.invalid` domain, the repository's synthetic sample PDF
(frontend/e2e/fixtures/sample.pdf), and invented row titles. Standard library only, so it runs on the bare runner.

    python3 acceptance_seed.py seed   --base http://localhost:8080 --pdf <path> --state <file>
    python3 acceptance_seed.py verify --base http://localhost:8080 --state <file>
"""

import argparse
import json
import secrets
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SESSION_COOKIE = "mrr_session"
# The row fields the check compares; the API adds others (flags it computes) that are not part of what was written.
_COMPARED = ("start", "end", "category", "title")


def register_body() -> dict:
    """A synthetic account. The password meets app/auth/users.py validate_password (8+, a number, a symbol)."""
    return {
        "email": f"acceptance-{secrets.token_hex(4)}@example.invalid",
        "password": f"Acceptance#{secrets.token_hex(6)}9",
        "name": "Acceptance Stage",
    }


def rows_payload() -> dict:
    """Three one-page review rows for the three-page sample PDF, in categories the unseeded catalog accepts."""
    return {
        "rows": [
            {"start": 1, "end": 1, "category": "1", "title": "Synthetic intake note"},
            {"start": 2, "end": 2, "category": "2", "title": "Synthetic follow-up visit"},
            {"start": 3, "end": 3, "category": "3", "title": "Synthetic imaging report"},
        ]
    }


def multipart_pdf(data: bytes, filename: str) -> tuple[bytes, str]:
    """A multipart/form-data body carrying the PDF in the `pdf` field POST /api/documents reads."""
    boundary = f"acceptance{secrets.token_hex(12)}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="pdf"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def session_cookie(set_cookie_headers: list[str]) -> str:
    """`mrr_session=<value>` from the login response. Read by hand because the cookie is Secure in production mode and
    the stage talks plain http to localhost, where a cookie jar would never send it back."""
    for header in set_cookie_headers:
        pair = header.split(";", 1)[0].strip()
        if pair.startswith(f"{SESSION_COOKIE}="):
            return pair
    raise RuntimeError(f"the login response set no {SESSION_COOKIE} cookie")


def verify(expected: dict, listing: list[dict], detail: dict) -> list[str]:
    """Every way the read-back differs from what was seeded; an empty list means the data survived the upgrade."""
    found: list[str] = []
    want = [{k: row[k] for k in _COMPARED} for row in expected["rows"]]
    listed = next((d for d in listing if d.get("id") == expected["document_id"]), None)
    if listed is None:
        found.append(f"document {expected['document_id']} is missing from GET /api/documents")
    elif listed.get("rows_count") != len(want):
        found.append(f"GET /api/documents reports {listed.get('rows_count')} rows, expected {len(want)}")
    got = [{k: row.get(k) for k in _COMPARED} for row in detail.get("rows", [])]
    if got != want:
        found.append(f"the document's rows changed: expected {want}, got {got}")
    return found


def _request(method: str, url: str, *, data: bytes | None = None, headers: dict | None = None):
    """One HTTP call; returns (status, response headers, body). Raises on a transport error or a 4xx/5xx."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed http://localhost URL from the stage
        return resp.status, resp.headers, resp.read()


def _login(base: str, account: dict) -> str:
    form = urllib.parse.urlencode({"username": account["email"], "password": account["password"]}).encode()
    _, headers, _ = _request(
        "POST", f"{base}/api/auth/login", data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    return session_cookie(headers.get_all("Set-Cookie") or [])


def seed(base: str, pdf: Path, state: Path) -> None:
    """Create the account, upload the PDF, write the rows, and save what the later check needs."""
    account = register_body()
    _request(
        "POST", f"{base}/api/auth/register", data=json.dumps(account).encode(),
        headers={"Content-Type": "application/json"},
    )
    cookie = _login(base, account)
    body, content_type = multipart_pdf(pdf.read_bytes(), pdf.name)
    _, _, raw = _request(
        "POST", f"{base}/api/documents", data=body, headers={"Content-Type": content_type, "Cookie": cookie}
    )
    document_id = json.loads(raw)["id"]
    rows = rows_payload()
    _request(
        "PUT", f"{base}/api/documents/{document_id}/rows", data=json.dumps(rows).encode(),
        headers={"Content-Type": "application/json", "Cookie": cookie},
    )
    state.write_text(json.dumps({"account": account, "document_id": document_id, "rows": rows["rows"]}), "utf-8")
    print(f"seeded document {document_id} with {len(rows['rows'])} rows")


def check(base: str, state: Path) -> int:
    """Log in again (through the upgraded code) and compare what the API returns with what was seeded."""
    expected = json.loads(state.read_text("utf-8"))
    cookie = _login(base, expected["account"])
    _, _, raw_list = _request("GET", f"{base}/api/documents", headers={"Cookie": cookie})
    _, _, raw_detail = _request("GET", f"{base}/api/documents/{expected['document_id']}", headers={"Cookie": cookie})
    found = verify(expected, json.loads(raw_list), json.loads(raw_detail))
    for problem in found:
        print(problem)
    if not found:
        print(f"document {expected['document_id']} and its {len(expected['rows'])} rows survived the upgrade")
    return 1 if found else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic data through the API, or verify it survived.")
    parser.add_argument("command", choices=["seed", "verify"])
    parser.add_argument("--base", required=True)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args(argv)
    if args.command == "seed":
        if args.pdf is None:
            parser.error("seed needs --pdf")
        seed(args.base, args.pdf, args.state)
        return 0
    return check(args.base, args.state)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
