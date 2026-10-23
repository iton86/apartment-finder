"""Track ads selected for training datasets.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_ad_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset", sa.String(), nullable=False),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selection_run", sa.String(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.UniqueConstraint("dataset", "listing_id"),
    )
    op.create_index(
        "idx_training_ad_selections_run",
        "training_ad_selections",
        ["selection_run"],
    )


def downgrade() -> None:
    op.drop_index("idx_training_ad_selections_run", table_name="training_ad_selections")
    op.drop_table("training_ad_selections")
