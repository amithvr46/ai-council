"""spend budget settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18

"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("daily_limit_usd", sa.Float(), nullable=False, server_default="5.0"),
        sa.Column("monthly_limit_usd", sa.Float(), nullable=False, server_default="30.0"),
        sa.Column("warn_threshold_pct", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("hard_stop", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("budget_settings")
