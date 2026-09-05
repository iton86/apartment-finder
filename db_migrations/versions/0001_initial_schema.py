"""Create the initial database schema.

Revision ID: 0001
Revises:
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apartments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "listings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "transaction_type",
            sa.Enum(
                "sale",
                "rent",
                name="transaction_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("rooms", sa.Integer(), nullable=True),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_currency", sa.String(), nullable=True),
        sa.Column("area_sqm", sa.Numeric(8, 2), nullable=True),
        sa.Column("floor", sa.Integer(), nullable=True),
        sa.Column("building_floors", sa.Integer(), nullable=True),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("area", sa.Text(), nullable=False),
        sa.Column("street", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("agency_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "apartment_id",
            sa.Uuid(),
            sa.ForeignKey("apartments.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("source", "external_id"),
    )
    op.create_index("idx_listings_city_area", "listings", ["city", "area"])
    op.create_index(
        "idx_listings_price_per_sqm",
        "listings",
        ["area_sqm", "price_amount"],
    )

    op.create_table(
        "listing_images",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Uuid(),
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
    op.drop_index("idx_listings_city_area", table_name="listings")
    op.drop_table("listings")
    op.drop_table("apartments")
