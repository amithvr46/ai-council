"""evidence items and claim assessments

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-18

"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("evidence_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "requests",
        sa.Column("evidence_override", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("request_id", sa.String(32), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw", sa.JSON(), nullable=True),
    )
    op.create_index("ix_evidence_items_request_id", "evidence_items", ["request_id"])
    op.create_table(
        "claim_assessments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("request_id", sa.String(32), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("made_by", sa.String(8), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("citations", sa.JSON(), nullable=True),
    )
    op.create_index("ix_claim_assessments_request_id", "claim_assessments", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_claim_assessments_request_id", table_name="claim_assessments")
    op.drop_table("claim_assessments")
    op.drop_index("ix_evidence_items_request_id", table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_column("requests", "evidence_override")
    op.drop_column("requests", "evidence_used")
