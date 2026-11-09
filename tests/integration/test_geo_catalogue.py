"""PostGIS catalogue tests against an explicitly configured isolated local database."""

import os
import runpy
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import IntegrityError

from apartment_finder.domain.geo import GeoFeature, validate_geometry
from apartment_finder.infrastructure.geo.repository import PostgisCatalogueRepository


@pytest.fixture
def catalogue():
    url = os.environ.get("GEO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GEO_TEST_DATABASE_URL to an isolated local PostGIS database")
    if make_url(url).host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Geospatial tests require a local test database")
    schema = "test_geo_" + uuid4().hex
    engine = create_engine(url)
    statements = runpy.run_path("db_migrations/versions/0010_geo_catalogue.py")["STATEMENTS"]
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(
                    text(
                        statement.replace("geo.", schema + ".").replace(
                            "CREATE SCHEMA geo", "CREATE SCHEMA " + schema
                        )
                    )
                )
        yield PostgisCatalogueRepository(engine, schema)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


def boundary(identifier, left, right):
    geometry = {
        "type": "Polygon",
        "coordinates": [[[left, 42.6], [right, 42.6], [right, 42.8], [left, 42.8], [left, 42.6]]],
    }
    return GeoFeature(
        "planning_units",
        identifier,
        "boundary",
        "Unit " + identifier,
        None,
        validate_geometry(geometry, "boundary"),
        {},
    )


def test_atomic_releases_edges_and_idempotency(catalogue):
    features = [boundary("1", 23.2, 23.3), boundary("2", 23.3, 23.4)]
    manifest = {"mapping_version": "test", "snapshots": {}}
    first = catalogue.publish(features, manifest)
    assert catalogue.publish(features, manifest | {"fetched_at": "later"}) == first
    assert [r["planning_unit_id"] for r in catalogue.locate(23.25, 42.7)] == ["1"]
    assert [r["planning_unit_id"] for r in catalogue.locate(23.3, 42.7)] == ["1", "2"]
    assert catalogue.locate(24, 42.7) == []
    # Self-intersection passes simple ring checks but must fail PostGIS topology validation.
    invalid = replace(
        features[0],
        geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[23.2, 42.6], [23.3, 42.8], [23.2, 42.8], [23.3, 42.6], [23.2, 42.6]]]
            ],
        },
    )
    with pytest.raises(IntegrityError):
        catalogue.publish([invalid], manifest)
    assert catalogue.locate(23.25, 42.7)[0]["release_id"] == first
    second = catalogue.publish([features[0]], manifest)
    assert second != first
    assert catalogue.locate(23.35, 42.7) == []
    assert catalogue.publish(features, manifest) == first  # rollback to retained release


def test_radius_index_and_feature_constraints(catalogue):
    features = [
        boundary("1", 23.2, 23.4),
        GeoFeature(
            "poi_13",
            "1",
            "poi",
            "Hospital",
            "hospital",
            {"type": "Point", "coordinates": [23.3, 42.7]},
            {},
        ),
    ]
    catalogue.publish(features, {"mapping_version": "test"})
    with catalogue.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    f"SELECT count(*) FROM {catalogue.schema}.feature "
                    "WHERE kind='poi' AND ST_DWithin(geom::geography,"
                    "ST_SetSRID(ST_MakePoint(23.3,42.7),4326)::geography,100)"
                )
            ).scalar_one()
            == 1
        )
    with pytest.raises(ValueError, match="Duplicate"):
        catalogue.publish(features + features, {"mapping_version": "test"})


def test_explicit_repair_preserves_original_and_records_reason(catalogue):
    invalid = replace(
        boundary("1", 23.2, 23.3),
        geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[23.2, 42.6], [23.3, 42.8], [23.2, 42.8], [23.3, 42.6], [23.2, 42.6]]]
            ],
        },
    )
    catalogue.repair_invalid = True
    catalogue.publish([invalid], {"mapping_version": "test"})
    with catalogue.engine.connect() as connection:
        row = connection.execute(
            text(
                f"SELECT ST_IsValid(geom),ST_IsValid(original_geom),"
                f"repair_reason FROM {catalogue.schema}.feature"
            )
        ).one()
        assert row[0] is True and row[1] is False
        assert "intersection" in row[2].lower()


def test_map_filters_stale_inactive_and_wrong_release_assignments(catalogue):
    import hashlib
    import json

    from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment
    from apartment_finder.infrastructure.geo.map_repository import PostgisPlanningUnitMapRepository

    release = catalogue.publish([boundary("65", 23.2, 23.4)], {"mapping_version": "test"})
    identifier = uuid4()
    with catalogue.engine.begin() as c:
        c.execute(
            text(f"""CREATE TABLE {catalogue.schema}.listings (
            id uuid PRIMARY KEY,title text,url text,description text,status text,
            price_amount numeric,price_currency text)""")
        )
        c.execute(
            text(
                f"INSERT INTO {catalogue.schema}.listings VALUES "
                "(:id,'Real ad','javascript:alert(1)','Original','active',100000,'EUR')"
            ),
            {"id": identifier},
        )
    row = {
        "listing_id": str(identifier),
        "planning_unit_id": "65",
        "release_id": release,
        "description_sha256": hashlib.sha256(b"Original").hexdigest(),
        "resolver_version": "review-v1",
        "basis": "human_reviewed",
        "reviewed_by": "reviewer",
        "evidence": ["Reviewed evidence"],
    }
    accepted = PlanningUnitAssignment.model_validate_json(json.dumps(row))
    repository = PostgisPlanningUnitMapRepository(
        catalogue.engine, catalogue.schema, catalogue.schema
    )
    data = repository.map_data([accepted])
    assert len(data["units"]["features"]) == 1
    assert data["ads"][0]["location_precision"] == "unit_only"
    assert data["ads"][0]["url"] is None
    with catalogue.engine.begin() as c:
        c.execute(text(f"UPDATE {catalogue.schema}.listings SET url='https://[broken'"))
    assert repository.map_data([accepted])["ads"][0]["url"] is None
    coordinates = data["units"]["features"][0]["properties"]["marker"]
    assert 23.2 < coordinates[0] < 23.4 and 42.6 < coordinates[1] < 42.8
    wrong_release = accepted.model_copy(update={"release_id": "b" * 64})
    assert repository.map_data([wrong_release])["ads"] == []
    with catalogue.engine.begin() as c:
        c.execute(text(f"UPDATE {catalogue.schema}.listings SET description='Changed'"))
    assert repository.map_data([accepted])["excluded_assignments"] == 1
    with catalogue.engine.begin() as c:
        c.execute(
            text(f"UPDATE {catalogue.schema}.listings SET description='Original',status='expired'")
        )
    assert repository.map_data([accepted])["ads"] == []
