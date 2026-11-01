"""Store model POIs and prediction provenance on listings."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("pois", sa.JSON(), nullable=True))
    op.add_column("listings", sa.Column("poi_model_version", sa.Text(), nullable=True))
    op.add_column("listings", sa.Column("poi_description_sha256", sa.String(64), nullable=True))
    op.add_column(
        "listings", sa.Column("poi_predicted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    for name in ("poi_predicted_at", "poi_description_sha256", "poi_model_version", "pois"):
        op.drop_column("listings", name)
