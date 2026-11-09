"""Official address references, immutable resolutions and reviewed unit labels."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE geo.location_observation (
        id text PRIMARY KEY,
        listing_id uuid NOT NULL REFERENCES public.listings(id) ON DELETE CASCADE,
        geom geometry(Point,4326) NOT NULL, uncertainty_m double precision NOT NULL
        CHECK(uncertainty_m BETWEEN 1 AND 2000), source text NOT NULL CHECK(btrim(source)<>''),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(ST_CoveredBy(geom,ST_MakeEnvelope(22.5,42.3,24.35,43,4326))))""",
    "CREATE INDEX geo_observation_listing ON geo.location_observation(listing_id,created_at DESC)",
    """CREATE TABLE geo.resolution_policy (
        id text PRIMARY KEY, policy jsonb NOT NULL)""",
    """CREATE TABLE geo.resolution_state (
        name text PRIMARY KEY CHECK(name='sofia'), policy_id text NOT NULL
        REFERENCES geo.resolution_policy(id))""",
    """CREATE TABLE geo.reference_bundle (
        id text PRIMARY KEY, manifest jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE geo.address (
        bundle_id text NOT NULL REFERENCES geo.reference_bundle(id), external_id text NOT NULL,
        settlement text NOT NULL, street text NOT NULL, number text NOT NULL,
        name_key text NOT NULL, geom geometry(Point,4326) NOT NULL, attributes jsonb NOT NULL,
        PRIMARY KEY(bundle_id,external_id), CHECK(NOT ST_IsEmpty(geom)),
        CHECK(ST_CoveredBy(geom,ST_MakeEnvelope(22.5,42.3,24.35,43,4326))))""",
    "CREATE INDEX geo_address_name ON geo.address(bundle_id,name_key,number)",
    "CREATE INDEX geo_address_geom ON geo.address USING gist(geom)",
    """CREATE TABLE geo.street_alias (
        bundle_id text NOT NULL REFERENCES geo.reference_bundle(id), external_id text NOT NULL,
        old_name text NOT NULL, new_name text NOT NULL, source_note text NOT NULL,
        PRIMARY KEY(bundle_id,external_id))""",
    """CREATE TABLE geo.reference_state (
        name text PRIMARY KEY CHECK(name='sofia'), bundle_id text NOT NULL
        REFERENCES geo.reference_bundle(id))""",
    """CREATE TABLE geo.resolution (
        id text PRIMARY KEY,
        listing_id uuid NOT NULL REFERENCES public.listings(id) ON DELETE CASCADE,
        release_id text NOT NULL REFERENCES geo.release(id), bundle_id text
        REFERENCES geo.reference_bundle(id), input_fingerprint text NOT NULL,
        description_sha256 text NOT NULL, prediction_fingerprint text NOT NULL,
        resolver_version text NOT NULL, status text NOT NULL
        CHECK(status IN ('accepted','ambiguous','unmatched')), result jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(listing_id,input_fingerprint))""",
    "CREATE INDEX geo_resolution_listing ON geo.resolution(listing_id,created_at DESC)",
    """CREATE TABLE geo.resolution_review (
        id bigserial PRIMARY KEY, resolution_id text NOT NULL REFERENCES geo.resolution(id),
        planning_unit_id text, reviewer text NOT NULL CHECK(btrim(reviewer)<>''),
        group_id text NOT NULL CHECK(btrim(group_id)<>''),
        geography_group text NOT NULL CHECK(btrim(geography_group)<>''),
        split text NOT NULL CHECK(split IN ('train','calibration','test')),
        created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX geo_review_resolution ON geo.resolution_review(resolution_id,id DESC)",
)


def upgrade() -> None:
    """Create versioned resolver inputs, outputs and review records."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only resolver tables, preserving the reference catalogue."""
    for table in (
        "resolution_state",
        "resolution_policy",
        "location_observation",
        "resolution_review",
        "resolution",
        "reference_state",
        "street_alias",
        "address",
        "reference_bundle",
    ):
        op.execute(f"DROP TABLE IF EXISTS geo.{table}")
