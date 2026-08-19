"""technology discovery cache, source conflicts, generated artifacts

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technology_cache",
        sa.Column("term", sa.String(120), primary_key=True),
        sa.Column("is_technology", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kind", sa.String(32), nullable=False, server_default="other"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_conflicts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("values", sa.JSON(), nullable=True),
        sa.Column("resolved_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="resume_tailor"),
        sa.Column("jd_document_id", sa.String(32), nullable=True),
        sa.Column("role_family", sa.String(32), nullable=False, server_default=""),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("trace", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="complete"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("source_conflicts")
    op.drop_table("technology_cache")
