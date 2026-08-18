"""conversations with pinning; requests join a conversation

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18

"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(120), nullable=False, server_default="New chat"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "requests",
        sa.Column(
            "conversation_id",
            sa.String(32),
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_requests_conversation_id", "requests", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_requests_conversation_id", table_name="requests")
    op.drop_column("requests", "conversation_id")
    op.drop_table("conversations")
