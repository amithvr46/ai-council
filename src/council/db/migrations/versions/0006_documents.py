"""documents and career profile

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-18

"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("authority", sa.String(32), nullable=False, server_default="supporting"),
        sa.Column("detected_kind", sa.String(16), nullable=False, server_default="text"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])
    op.create_table(
        "career_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("technologies", sa.JSON(), nullable=True),
        sa.Column("domains", sa.JSON(), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("employers", sa.JSON(), nullable=True),
        sa.Column("certifications", sa.JSON(), nullable=True),
        sa.Column("achievements", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("career_profile")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_table("documents")
