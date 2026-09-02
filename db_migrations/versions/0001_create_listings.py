"""create listings tables

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price_amount", sa.Numeric(12, 2)),
        sa.Column("price_currency", sa.String()),
        sa.Column("area_sqm", sa.Numeric(8, 2)),
        sa.Column("floor", sa.Text()),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source", "external_id"),
    )
    op.create_index("idx_listings_location", "listings", ["location"])
    op.create_index("idx_listings_price_per_sqm", "listings", ["area_sqm", "price_amount"])

    op.create_table(
        "listing_images",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.BigInteger(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.UniqueConstraint("listing_id", "sequence"),
    )


def downgrade() -> None:
    op.drop_table("listing_images")
    op.drop_index("idx_listings_price_per_sqm", table_name="listings")
    op.drop_index("idx_listings_location", table_name="listings")
    op.drop_table("listings")
