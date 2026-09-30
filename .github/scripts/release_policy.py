"""The production release policy: no Critical vulnerability, and no High one that has a fix, in a released image,
unless a reviewed, unexpired exception covers it (Adrian: 2026-09-28 decision 3; the fix-state scope 2026-09-29).

A Critical blocks whatever its fix state. A High blocks only when Grype reports a fixed version (fix state "fixed");
a High with no fix ("not-fixed", "wont-fix", "unknown") is listed, not blocked, because nothing but an exception
could clear it. The scan covers everything in the image: the app's own dependencies and the base image's packages.
Build tools (uv, pip, npm, yarn) are kept out of the images by their Dockerfiles' build stages, so they are not in
scope. The exceptions live in .github/release-exceptions.json, reviewed like any other change:

    {"exceptions": [{"vulnerability": "CVE-...", "package": "openssl", "reason": "...", "expires": "YYYY-MM-DD"}]}

An exception covers one vulnerability in one package. It holds through its `expires` date; after that it is itself a
failure, used or not, so none outlives its review.

    python3 release_policy.py --exceptions .github/release-exceptions.json [--report-only] <grype.json> [...]

`--report-only` (the staging stage's early warning) writes the same findings but always exits 0. Both modes append a
markdown summary to $GITHUB_STEP_SUMMARY when it is set.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

_FIELDS = ("vulnerability", "package", "reason", "expires")


def _blocks(severity: str | None, fix_state: str | None) -> bool:
    """Whether a finding of this severity and fix state blocks a release (see the module docstring)."""
    return severity == "Critical" or (severity == "High" and fix_state == "fixed")


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
    """Each blocking match (any Critical; a High with a fix) in one Grype JSON report that no exception covers."""
    covered = {(e["vulnerability"], e["package"]) for e in exceptions}
    found: list[str] = []
    for match in report.get("matches", []):
        vulnerability, artifact = match.get("vulnerability", {}), match.get("artifact", {})
        severity, fix_state = vulnerability.get("severity"), vulnerability.get("fix", {}).get("state")
        key = (vulnerability.get("id"), artifact.get("name"))
        if _blocks(severity, fix_state) and key not in covered:
            found.append(f"{severity}: {key[0]} in {key[1]} {artifact.get('version', '')}".rstrip())
    return found


def advisories(report: dict) -> list[str]:
    """Each High match with no fix in one Grype JSON report: listed for the reader, never blocking."""
    listed: list[str] = []
    for match in report.get("matches", []):
        vulnerability, artifact = match.get("vulnerability", {}), match.get("artifact", {})
        severity, fix_state = vulnerability.get("severity"), vulnerability.get("fix", {}).get("state")
        if severity == "High" and not _blocks(severity, fix_state):
            listed.append(
                f"High ({fix_state or 'unknown'}): {vulnerability.get('id')} in {artifact.get('name')} "
                f"{artifact.get('version', '')}".rstrip()
            )
    return listed


def _summary(problems: list[str], listed: list[str], report_only: bool) -> str:
    """The job-summary markdown: what blocks (or would block) and the unfixable Highs listed for the reader."""
    title = "Release policy (report only: nothing is blocked here)" if report_only else "Release policy"
    lines = [f"### {title}", ""]
    lines += [f"- BLOCKS: {p}" for p in problems] or ["- Nothing blocks."]
    if listed:
        lines += ["", "High findings with no fix (listed, not blocking):", ""] + [f"- {a}" for a in listed]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Apply the production release policy to Grype JSON reports.")
    parser.add_argument("--exceptions", required=True, type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    kept, problems = load_exceptions(json.loads(args.exceptions.read_text("utf-8")), datetime.date.today())
    listed: list[str] = []
    for report_path in args.reports:
        report = json.loads(report_path.read_text("utf-8"))
        problems += [f"{report_path.name}: {violation}" for violation in violations(report, kept)]
        listed += [f"{report_path.name}: {advisory}" for advisory in advisories(report)]
    for problem in problems:
        print(problem)
    if not problems:
        print(f"release policy passed for {len(args.reports)} image report(s)")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(_summary(problems, listed, args.report_only))
    return 1 if problems and not args.report_only else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
