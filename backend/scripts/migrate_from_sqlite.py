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

from sqlalchemy import create_engine, insert, select, text

from app import models  # noqa: F401 - registers every table on Base.metadata
from app.db import Base, get_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=os.path.join("..", "instance", "mrr.db"))
    args = parser.parse_args()

    source = create_engine(f"sqlite:///{os.path.abspath(args.sqlite)}")
    dest = get_engine()

    with source.connect() as src, dest.begin() as dst:
        for table in Base.metadata.sorted_tables:  # topologically FK-safe
            rows = [dict(row) for row in src.execute(select(table)).mappings().all()]
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
