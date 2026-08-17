"""initial requests + steps tables

Revision ID: 0001
Revises:
Create Date: 2026-08-17

"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_rating", sa.Integer(), nullable=True),
    )
    op.create_table(
        "steps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("request_id", sa.String(32), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(48), nullable=False),
        sa.Column("provider", sa.String(24), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(48), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_steps_request_id", "steps", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_steps_request_id", table_name="steps")
    op.drop_table("steps")
    op.drop_table("requests")
