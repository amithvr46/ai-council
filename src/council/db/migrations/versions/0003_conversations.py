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
    # batch mode, not a plain add_column: SQLite cannot ALTER a table to add a
    # foreign key, so the local development database could never be migrated.
    # Batch mode is a no-op cost on Postgres and makes both dialects work.
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("conversation_id", sa.String(32), nullable=True))
        batch.create_foreign_key(
            "fk_requests_conversation_id", "conversations", ["conversation_id"], ["id"]
        )
    op.create_index("ix_requests_conversation_id", "requests", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_requests_conversation_id", table_name="requests")
    with op.batch_alter_table("requests") as batch:
        batch.drop_constraint("fk_requests_conversation_id", type_="foreignkey")
        batch.drop_column("conversation_id")
    op.drop_table("conversations")
