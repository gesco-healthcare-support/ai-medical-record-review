"""drop seeded summary-prompt rows that merely shadow the code

Revision ID: f1a83b5c60d2
Revises: d4e7a1c93f26
Create Date: 2026-07-28 11:10:00.000000

Prompts resolve DB-first, and every box seeded historically carries one summary-prompt row per
category. Those rows shadow prompts.py forever: editing a prompt in code and deploying changed
nothing on an existing box, which is why prompt edits appeared to have no effect. seed_catalog() is
not called anywhere in this app, so a FRESH box already runs off the code - this makes existing boxes
behave the same, with an admin edit still overriding as a deliberate exception.

Guarded per row by an IMMUTABLE sha256 snapshot of the seeded text (below), not by comparing against
the live constants: a later prompts.py edit must not change what this migration deletes, or a box
that upgrades after that edit would keep its shadow forever and the guard would silently under-delete.
A row whose text hashes to anything else - i.e. edited in the admin UI - is kept untouched.

Pair with catalog.get_prompt's chain (this category's CODE prompt BEFORE the general row): without
that, a category whose row is dropped here would resolve to the catch-all general prompt.
"""

import hashlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.seed_catalog import code_summary_prompt

# revision identifiers, used by Alembic.
revision: str = "f1a83b5c60d2"
down_revision: Union[str, Sequence[str], None] = "d4e7a1c93f26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# sha256 of the summary prompt text seed_catalog() wrote, per category id, captured 2026-07-28 from
# the seeded catalog (all 14 rows were byte-identical to the constants of that day, i.e. nobody had
# customized a prompt). Frozen deliberately - see the module docstring.
_SEEDED_SHA256 = {
    "1": "224fbb50067195214e6253a33e94ecf665ad154f11cc51ebfd635a8c6b27948d",
    "2": "c029a803f608e1fb59709c435e01266dc5db78fd97490059161b89764fa31610",
    "3": "d8f759dbd06c0280fc7e2861f81d4152f5e803c90c86717432bde4abb5c80f2d",
    "4": "3dddaa3f88c4b5d0882e06d62c7d6dff085e66f82c4f3cee81956d4ae3af53eb",
    "5": "95401ecc66bc3fd016141b6ba4057ccd2168f984bbb495d138b45d5d2d3526ca",
    "6": "2857ecf82626b0b8e29f0a22f5a7784da6c1cd24e2aa8c74a7b2630999ac87d5",
    "7": "153532dfc8dd1099f61e869691cff42e66d7a7c43952e8aed4282fde58179199",
    "8": "f94b6dc92d172b3dda46570634b015fceb008340cf68ab9a1e670475d9798cff",
    "9": "c5694cff09a47f09883b11be7701ac2a17b23a20939ab5bacfcf6682157e34b5",
    "10": "21b019688288cc0dbe2d72cb34d00c15dee98200c2d73ff854c17a12b1d55ffe",
    "12": "08ed773df9a69fc46532c7105a83aac8ad5d07847f4f00466cbd07a0e183937e",
    "13": "6de6808a1cfbae8afd30d91b133fb0d74e3853bcfc34832292b341787e5add3c",
    "14": "83e33cfc98177e85a6f349b3d9858327b9805f11b8f1e69696470c58fd8b0cbf",
    "100": "3accc9275f1a46a5245296e874cd4efb72a1fa18647a7b42ea6bc1e97319c74b",
}


def _bump_revision() -> None:
    """Force the classifier/worker caches (keyed on the catalog revision) to reload.

    Upsert rather than UPDATE: an unseeded catalog has no meta row, so an UPDATE would be a no-op
    (mirrors catalog.bump_revision).
    """
    op.execute(
        "INSERT INTO catalog_meta (id, revision) VALUES (1, 1) "
        "ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1"
    )


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT category_id, text FROM prompts WHERE role = 'summary'")
    ).all()
    dropped, kept = [], []
    for category_id, text in rows:
        expected = _SEEDED_SHA256.get(str(category_id))
        if expected and hashlib.sha256(text.encode()).hexdigest() == expected:
            connection.execute(
                sa.text("DELETE FROM prompts WHERE role = 'summary' AND category_id = :cid"),
                {"cid": category_id},
            )
            dropped.append(str(category_id))
        else:
            kept.append(str(category_id))
    print(f"prompt shadows dropped: {sorted(dropped)}; customized rows kept: {sorted(kept)}")
    _bump_revision()


def downgrade() -> None:
    """Re-seed a row per code-defined category, restoring the DB-shadows-the-code state.

    Restores from the CURRENT constants (a hash cannot reproduce text); if a prompt has changed in
    code since the upgrade, the restored row carries today's text - still a faithful shadow, which is
    all the pre-upgrade state was. Never overwrites a row that exists, so an admin edit survives.
    """
    connection = op.get_bind()
    existing = {
        str(row[0])
        for row in connection.execute(
            sa.text("SELECT category_id FROM prompts WHERE role = 'summary'")
        ).all()
    }
    for category_id in _SEEDED_SHA256:
        text = code_summary_prompt(category_id)
        # Skip a category the code no longer defines, and never overwrite an existing row (that
        # would clobber an admin's custom prompt).
        if text is None or str(category_id) in existing:
            continue
        # updated_at is NOT NULL with only a client-side SQLAlchemy default, so raw SQL must supply
        # it - omitting it fails the whole downgrade.
        connection.execute(
            sa.text(
                "INSERT INTO prompts (role, category_id, text, revision, updated_at) "
                "VALUES ('summary', :cid, :text, 1, NOW())"
            ),
            {"cid": str(category_id), "text": text},
        )
    _bump_revision()
