"""Replace activity flags with status and last successful sighting.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column(
            "status",
            sa.String(length=11),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "listing_status", "listings", "status IN ('active', 'unreachable', 'expired')"
    )
    op.add_column(
        "listings",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.drop_column("listings", "deactivated_at")
    op.drop_column("listings", "is_active")


def downgrade() -> None:
    op.add_column(
        "listings",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column("listings", sa.Column("deactivated_at", sa.DateTime(timezone=True)))
    op.drop_column("listings", "last_seen_at")
    op.drop_constraint("listing_status", "listings", type_="check")
    op.drop_column("listings", "status")
