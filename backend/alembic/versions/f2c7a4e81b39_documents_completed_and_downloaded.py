"""documents: the reviewer's Completed mark, and when the review was last downloaded

Revision ID: f2c7a4e81b39
Revises: d6a2f9c3e481
Create Date: 2026-11-05 12:00:00.000000

A reviewer asked (2026-11-05) for a mark like Outlook's "Categorize" on the records list, to see
which records are fully reviewed and downloaded without searching the log for the patient.

- `completed_at`: when the reviewer marked the record Completed; NULL is not marked. Nothing sets it
  but the reviewer, so it starts NULL everywhere.
- `downloaded_at`: when the review was last downloaded - the Word letter, the linked PDF or the ZIP
  (audit actions `export`, `export_pdf`, `export_zip`), the three exports that carry the summaries.
  The export routes set it from now on; this revision fills it once from the audit log's past
  exports, so records downloaded before today show their date too.

Both nullable, so safe on a populated table. Downgrade drops both columns; the marks are lost and the
download dates can be rebuilt from the audit log by running the upgrade again.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2c7a4e81b39"
down_revision: Union[str, Sequence[str], None] = "d6a2f9c3e481"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen here, not imported from the app: the audit actions of the three exports that carry the
# summaries (`api/documents.py` export_document, export_document_pdf, export_document_zip).
_DOWNLOAD_ACTIONS = ("export", "export_pdf", "export_zip")


def fill_downloaded_at(bind) -> None:
    """Each document's `downloaded_at` = its latest download in the audit log. A function of its
    own so a test can run it against synthetic rows."""
    bind.execute(
        sa.text(
            "UPDATE documents SET downloaded_at = last.at FROM ("
            "  SELECT document_id, max(at) AS at FROM audit_log"
            "  WHERE action IN :actions AND document_id IS NOT NULL GROUP BY document_id"
            ") AS last WHERE documents.id = last.document_id"
        ).bindparams(sa.bindparam("actions", expanding=True)),
        {"actions": list(_DOWNLOAD_ACTIONS)},
    )


def upgrade() -> None:
    op.add_column("documents", sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.add_column("documents", sa.Column("downloaded_at", sa.DateTime(), nullable=True))
    fill_downloaded_at(op.get_bind())


def downgrade() -> None:
    op.drop_column("documents", "downloaded_at")
    op.drop_column("documents", "completed_at")
