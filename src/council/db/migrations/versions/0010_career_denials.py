"""career_denials — durable negative career facts

Until now the only durable career facts were positive ones. That was not a gap
in coverage, it was a correctness defect: "I have never used Harness" was
parsed as a first-person experience statement, stored as positive career prose
and then scanned for technology names, so Harness became confirmed
professional experience and was eligible for a submitted resume.

A denial cannot be stored as text alongside positive sources, because the
document scanner cannot tell the two apart — the sentence contains the word
"Harness" either way. It needs its own per-term structure, which is what this
table is.

Rows are never deleted. A later positive statement supersedes a denial rather
than erasing it: `active` goes false and both statements survive with their
timestamps, so a reversal can always be explained.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-20

"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "career_denials",
        sa.Column("term", sa.String(120), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="never_used"),
        sa.Column("statement", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Text(), nullable=False, server_default=""),
    )
    # Loading the boundary happens on every resume run and only ever wants the
    # active rows, so the index matches the query rather than the table.
    op.create_index("ix_career_denials_active", "career_denials", ["active"])


def downgrade() -> None:
    op.drop_index("ix_career_denials_active", table_name="career_denials")
    op.drop_table("career_denials")
