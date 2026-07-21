"""One-shot data migration: copy the legacy SQLite DB into the Postgres schema.

Run AFTER `alembic upgrade head` has created the (empty) Postgres schema. Copies every table
in FK-safe order, preserving values exactly (argon2id password hash, fs_uniquifier, JSON
examples, datetimes), then resets the integer-PK sequences so future inserts don't collide.

    cd backend && uv run python scripts/migrate_from_sqlite.py [--sqlite ../instance/mrr.db]

Reads the SQLite DB through the SAME models (they mirror the schema), so SQLAlchemy coerces
SQLite's 0/1 booleans, ISO-string datetimes, and TEXT JSON into the right Python types on the
way into Postgres. Idempotent it is NOT - run against an empty Postgres schema.
"""

import argparse
import os
import shutil
import sys
import tempfile

from sqlalchemy import create_engine, insert, inspect, select, text

# Make the project root (parent of scripts/) importable when run as `python scripts/x.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402, F401 - registers every table on Base.metadata
from app.db import Base, get_engine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=os.path.join("..", "instance", "mrr.db"))
    args = parser.parse_args()

    dest = get_engine()

    # Copy the source to a local temp file before opening it: a bind-mounted SQLite (Docker
    # Desktop on Windows) cannot be opened through SQLite's VFS directly, and a throwaway copy
    # also guarantees the real legacy DB is never touched.
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "source.db")
        shutil.copy(os.path.abspath(args.sqlite), local)
        source = create_engine("sqlite:///" + local.replace("\\", "/"))
        src_inspector = inspect(source)
        src_tables = set(src_inspector.get_table_names())

        with source.connect() as src, dest.begin() as dst:
            for table in Base.metadata.sorted_tables:  # topologically FK-safe
                if table.name not in src_tables:
                    # A table the rewrite added (e.g. access_token); no legacy data to copy.
                    print(f"  {table.name}: skipped (not in source)")
                    continue
                # Copy only the columns present in BOTH schemas; rewrite-added columns
                # (e.g. user.is_verified) take their model/DB default on insert.
                src_cols = {c["name"] for c in src_inspector.get_columns(table.name)}
                cols = [c for c in table.columns if c.name in src_cols]
                rows = [dict(row) for row in src.execute(select(*cols)).mappings().all()]
                if rows:
                    dst.execute(insert(table), rows)
                print(f"  {table.name}: {len(rows)} rows")

            # Reset the sequence behind each integer 'id' PK to MAX(id) so new inserts continue
            # past the copied ids instead of colliding from 1.
            for table in Base.metadata.sorted_tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and pk[0].name == "id" and pk[0].type.python_type is int:
                    name = table.name
                    dst.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('\"{name}\"', 'id'), "
                            f'(SELECT COALESCE(MAX(id), 1) FROM "{name}"))'
                        )
                    )
    print("migration complete.")


if __name__ == "__main__":
    main()
