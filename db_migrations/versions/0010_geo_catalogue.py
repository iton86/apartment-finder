"""Versioned PostGIS planning-unit, POI and street-surface catalogue."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Explicit SQL keeps PostGIS optional for the existing ORM-only workflows.
STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS postgis",
    "CREATE SCHEMA geo",
    """CREATE TABLE geo.release (
        id text PRIMARY KEY, manifest jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE geo.feature (
        release_id text NOT NULL REFERENCES geo.release(id),
        dataset text NOT NULL, external_id text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('boundary', 'poi', 'surface')),
        name text, category text, attributes jsonb NOT NULL,
        geom geometry(Geometry,4326) NOT NULL,
        original_geom geometry(Geometry,4326), repair_reason text,
        CHECK ((original_geom IS NULL) = (repair_reason IS NULL)),
        PRIMARY KEY (release_id, dataset, external_id),
        CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom)),
        CHECK ((kind = 'poi' AND GeometryType(geom) = 'POINT') OR
               (kind IN ('boundary','surface') AND GeometryType(geom) = 'MULTIPOLYGON')),
        CHECK (kind <> 'boundary' OR (name IS NOT NULL AND btrim(name) <> '')),
        CHECK (ST_CoveredBy(geom, ST_MakeEnvelope(22.5,42.3,24.35,43,4326))))""",
    "CREATE INDEX geo_feature_geometry ON geo.feature USING gist (geom)",
    "CREATE INDEX geo_feature_geography ON geo.feature USING gist ((geom::geography))",
    "CREATE INDEX geo_feature_category ON geo.feature (release_id, kind, category)",
    """CREATE TABLE geo.catalogue_state (
        name text PRIMARY KEY CHECK (name = 'sofia'),
        release_id text NOT NULL REFERENCES geo.release(id))""",
)


def upgrade() -> None:
    """Provision PostGIS and create the isolated reference catalogue schema."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove reference tables, preserving a potentially shared PostGIS extension."""
    op.execute("DROP TABLE geo.catalogue_state")
    op.execute("DROP TABLE geo.feature")
    op.execute("DROP TABLE geo.release")
    op.execute("DROP SCHEMA geo")
