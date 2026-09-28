"""Lint the SQL of every Alembic migration a pull request ADDS, with Squawk (CI, backend job).

`alembic check` proves the models and the migrations agree; it says nothing about what a migration's SQL does to
a live table. Squawk reads that SQL and flags operations that lock or rewrite tables, or lose data. It is a gate,
tuned by Adrian. The four style rules in EXCLUDED fired on almost every existing table (114 warnings on the first
10 migrations), so they were left out on 2026-09-27. The two timeout rules fire on EVERY schema change, even a
nullable column, so they were left out on 2026-09-28. The lock pile-up they guard against is handled by the
deploy script, which migrates before the app and workers restart. Any other rule fails the pull request.

Only migrations the pull request adds are linted, so the ones that predate this check are never rewritten. A new
migration must render offline (`alembic upgrade <down>:<rev> --sql`). One that reads rows must skip those reads
when `context.is_offline_mode()` is true, or this check fails and says so.

Usage, from backend/ with the CI environment set:  python scripts/ci/lint_new_migrations.py <base-commit>
"""

import ast
import subprocess
import sys
from pathlib import Path

SQUAWK = ["uvx", "--from", "squawk-cli==2.66.0", "squawk"]
EXCLUDED = ",".join(
    [
        "prefer-text-field",
        "prefer-bigint-over-int",
        "prefer-timestamp-tz",
        "prefer-identity",
        "require-lock-timeout",
        "require-statement-timeout",
    ]
)
VERSIONS = "alembic/versions/"


def added_migrations(base: str) -> list[Path]:
    """Migration files present at HEAD but not at `base`, relative to backend/."""
    out = subprocess.run(
        [
            "git",
            "diff",
            "--relative",
            "--name-only",
            "--diff-filter=A",
            base,
            "HEAD",
            "--",
            VERSIONS,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.split() if p.endswith(".py")]


def revision_ids(path: Path) -> tuple[str, str | tuple[str, ...] | None]:
    """The module-level `revision` and `down_revision` of a migration file, read without importing it."""
    found: dict[str, object] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
            found[target.id] = ast.literal_eval(value)
    revision, down = found.get("revision"), found.get("down_revision")
    if not isinstance(revision, str):
        raise ValueError(f"{path}: no module-level string `revision`")
    if down is None or isinstance(down, str):
        return revision, down
    if isinstance(down, (tuple, list)) and all(isinstance(d, str) for d in down):
        return revision, tuple(down)
    raise ValueError(f"{path}: `down_revision` is neither a string, a tuple of strings, nor None")


def lint(path: Path) -> bool:
    """Render one migration's SQL offline and run Squawk on it; True when it passes."""
    revision, down = revision_ids(path)
    if isinstance(down, (tuple, list)):
        print(
            f"{path.name}: merge revision {revision} (parents {', '.join(down)}); nothing to lint."
        )
        return True
    start = "base" if down is None else down
    render = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", "upgrade", f"{start}:{revision}", "--sql"],
        capture_output=True,
        text=True,
    )
    if render.returncode != 0:
        print(f"{path.name}: could not render its SQL offline, so Squawk cannot check it.")
        print("If it reads rows, skip those reads when `context.is_offline_mode()` is true.")
        print(render.stderr[-2000:])
        return False
    result = subprocess.run(
        [*SQUAWK, f"--exclude={EXCLUDED}", f"--stdin-filepath={path.name}.sql"],
        input=render.stdout,
        text=True,
    )
    return result.returncode == 0


def main(argv: list[str]) -> int:
    """Lint each migration the pull request adds; exit 1 if any fails, 0 if none or all pass."""
    if len(argv) != 2:
        print(__doc__)
        return 2
    migrations = added_migrations(argv[1])
    if not migrations:
        print("This pull request adds no migrations; nothing to lint.")
        return 0
    results = [lint(path) for path in migrations]
    print(f"Squawk: {results.count(True)} of {len(results)} new migration(s) passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
