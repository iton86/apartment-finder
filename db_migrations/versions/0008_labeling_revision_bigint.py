"""Widen annotation revision hashes to 64-bit integers.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # annotation_revision() produces a 48-bit hash, beyond INTEGER's range.
    for column in ("human_revision", "approved_revision"):
        op.alter_column(
            "labeling_tasks",
            column,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # PostgreSQL rejects this conversion if any value exceeds INTEGER's
    # range. Do not truncate or discard stored annotation revisions.
    for column in ("approved_revision", "human_revision"):
        op.alter_column(
            "labeling_tasks",
            column,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
