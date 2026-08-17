"""track physical API attempts alongside logical generations

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("total_api_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "steps",
        sa.Column("api_attempts", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("steps", "api_attempts")
    op.drop_column("requests", "total_api_attempts")
