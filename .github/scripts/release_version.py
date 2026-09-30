"""The next semantic version for a production release, from the commit messages since the previous tag.

The rule (Adrian, 2026-09-29): any breaking change - `!` before the colon of a commit subject, or a
`BREAKING CHANGE:` footer - is a major bump; otherwise any `feat` subject is a minor bump; otherwise a patch. Only a
commit's own subject and footers count, so a promotion's merge commit, whose body repeats a title, is a patch.
The subjects follow the commit format on everything reaching main (the pr-title check, pull requests into main).

The first release is v0.1.0 (Adrian, 2026-09-29). The rule then applies literally on 0.x as well, so the first
breaking change moves the version to v1.0.0.

    python3 release_version.py [--last-tag vX.Y.Z]     # reads the messages (NUL-separated) from stdin
"""

import argparse
import re
import sys

FIRST = "v0.1.0"
_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(\([a-z0-9-]+\))?(?P<bang>!)?: ")
_FOOTER = ("BREAKING CHANGE:", "BREAKING-CHANGE:")
_VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def bump(messages: list[str]) -> str:
    """'major', 'minor' or 'patch' for full commit messages (subject line, then body)."""
    level = "patch"
    for message in messages:
        lines = message.splitlines()
        match = _SUBJECT.match(lines[0]) if lines else None
        if (match and match["bang"]) or any(line.startswith(_FOOTER) for line in lines[1:]):
            return "major"
        if match and match["type"] == "feat":
            level = "minor"
    return level


def next_version(last: str | None, level: str) -> str:
    """The version after `last` (None = no release yet) for a bump level."""
    if last is None:
        return FIRST
    match = _VERSION.match(last)
    if match is None:
        raise ValueError(f"{last!r} is not a vMAJOR.MINOR.PATCH tag")
    major, minor, patch = (int(part) for part in match.groups())
    if level == "major":
        return f"v{major + 1}.0.0"
    if level == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Print the next release version from commit messages on stdin.")
    parser.add_argument("--last-tag", default=None, help="the previous release tag; omit for the first release")
    args = parser.parse_args(argv)
    messages = [m.strip("\n") for m in sys.stdin.read().split("\0") if m.strip()]
    print(next_version(args.last_tag or None, bump(messages)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
