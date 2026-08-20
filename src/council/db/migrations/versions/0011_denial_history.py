"""career_denials.history — reversals must not overwrite each other

0010 kept one row per term with a single `superseded_by`. That records ONE
reversal. The realistic sequence is longer:

    "I have never used Harness"        -> denied
    "I have used Harness professionally" -> superseded
    "Actually I have never used Harness" -> denied again

At step 3, `record_denials` reset `superseded_at` and `superseded_by` to put the
denial back in force, which erased step 2 entirely. The current state was right
and the audit trail was a lie by omission — the user could no longer see that
they had ever claimed the technology.

`history` is append-only: every transition lands in it with its timestamp and
the user's own words. The scalar columns still describe CURRENT state, so
nothing reading them has to change; history answers "how did we get here?".

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-20

"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("career_denials") as batch:
        batch.add_column(sa.Column("history", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("career_denials") as batch:
        batch.drop_column("history")
