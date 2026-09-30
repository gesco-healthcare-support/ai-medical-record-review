"""The production release policy: no Critical or High vulnerability in a runtime image, unless a reviewed, unexpired
exception covers it (Adrian, 2026-09-28, decision 3).

The images are runtime-only by construction (no dev tooling is built into them), so a scan of the images is the
runtime scope. The exceptions live in .github/release-exceptions.json, reviewed like any other change:

    {"exceptions": [{"vulnerability": "CVE-...", "package": "openssl", "reason": "...", "expires": "YYYY-MM-DD"}]}

An exception covers one vulnerability in one package. It holds through its `expires` date; after that it is itself a
failure, used or not, so none outlives its review.

    python3 release_policy.py --exceptions .github/release-exceptions.json <grype.json> [<grype.json> ...]
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

BLOCKING = ("Critical", "High")
_FIELDS = ("vulnerability", "package", "reason", "expires")


def load_exceptions(data: dict, today: datetime.date) -> tuple[list[dict], list[str]]:
    """The exceptions that hold today, and a problem for each one that is expired or incomplete."""
    kept: list[dict] = []
    problems: list[str] = []
    for entry in data.get("exceptions", []):
        label = f"{entry.get('vulnerability')} in {entry.get('package')}"
        missing = [field for field in _FIELDS if not str(entry.get(field, "")).strip()]
        if missing:
            problems.append(f"exception for {label} is missing {', '.join(missing)}")
            continue
        try:
            expires = datetime.date.fromisoformat(entry["expires"])
        except ValueError:
            problems.append(f"exception for {label} has an unreadable expiry {entry['expires']!r} (use YYYY-MM-DD)")
            continue
        if expires < today:
            problems.append(f"exception for {label} expired on {expires.isoformat()}; review it or remove it")
            continue
        kept.append(entry)
    return kept, problems


def violations(report: dict, exceptions: list[dict]) -> list[str]:
    """Each Critical/High match in one Grype JSON report that no exception covers."""
    covered = {(e["vulnerability"], e["package"]) for e in exceptions}
    found: list[str] = []
    for match in report.get("matches", []):
        vulnerability, artifact = match.get("vulnerability", {}), match.get("artifact", {})
        severity = vulnerability.get("severity")
        key = (vulnerability.get("id"), artifact.get("name"))
        if severity in BLOCKING and key not in covered:
            found.append(f"{severity}: {key[0]} in {key[1]} {artifact.get('version', '')}".rstrip())
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Apply the production release policy to Grype JSON reports.")
    parser.add_argument("--exceptions", required=True, type=Path)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    kept, problems = load_exceptions(json.loads(args.exceptions.read_text("utf-8")), datetime.date.today())
    for report_path in args.reports:
        for violation in violations(json.loads(report_path.read_text("utf-8")), kept):
            problems.append(f"{report_path.name}: {violation}")
    for problem in problems:
        print(problem)
    if not problems:
        print(f"release policy passed for {len(args.reports)} image report(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
