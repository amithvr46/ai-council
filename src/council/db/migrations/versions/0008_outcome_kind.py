"""requests.outcome_kind — WHAT the user wanted, not HOW it was processed

Deliberately a nullable plain string rather than an enum or a lookup table:
adding a workflow must never require a migration. The vocabulary lives in
council/outcomes.py.

Existing rows are backfilled to 'question_answer' because every request
predating this column came through the ask pipeline, which is that outcome.
Rows created later without an explicit kind stay NULL and read as 'general'.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("outcome_kind", sa.String(32), nullable=True))
    op.create_index("ix_requests_outcome_kind", "requests", ["outcome_kind"])
    # Every pre-existing request came through the ask pipeline.
    op.execute(
        "UPDATE requests SET outcome_kind = 'question_answer' WHERE outcome_kind IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_requests_outcome_kind", table_name="requests")
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("outcome_kind")
