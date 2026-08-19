"""requests.data_class — keep three data populations separate

Routing statistics are only trustworthy if they describe real usage. Three
populations must never be silently mixed:

  real       organic usage; the only population Rung 4 may learn from
  eval       deliberate benchmark runs; useful for calibration, but not
             evidence of how the system behaves in real work
  synthetic  fabricated rows; test fixtures only, never routing input

Before this column the eval runner wrote rows indistinguishable from organic
usage, so the contamination path was already open.

Existing rows are backfilled to 'real': they predate the eval runner writing a
marker, and treating genuine past usage as unusable would be a worse error than
the small chance that a benchmark run is among them.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("requests") as batch:
        batch.add_column(
            sa.Column("data_class", sa.String(16), nullable=False, server_default="real")
        )
    op.create_index("ix_requests_data_class", "requests", ["data_class"])


def downgrade() -> None:
    op.drop_index("ix_requests_data_class", table_name="requests")
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("data_class")
