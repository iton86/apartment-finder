"""Real PostGIS evidence integration, immutable results and automatic map handoff."""

import json
import os
import runpy
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import IntegrityError

from apartment_finder.application.use_cases.resolve_listing_locations import (
    resolve_listing_locations,
)
from apartment_finder.domain.geo import GeoFeature, validate_geometry
from apartment_finder.infrastructure.geo.map_repository import PostgisPlanningUnitMapRepository
from apartment_finder.infrastructure.geo.repository import PostgisCatalogueRepository
from apartment_finder.infrastructure.geo.resolution_repository import PostgisResolutionRepository


def polygon(left, right):
    return validate_geometry(
        {
            "type": "Polygon",
            "coordinates": [
                [[left, 42.6], [right, 42.6], [right, 42.8], [left, 42.8], [left, 42.6]]
            ],
        },
        "boundary",
    )


@pytest.fixture
def resolver():
    url = os.environ.get("GEO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GEO_TEST_DATABASE_URL to an isolated local PostGIS database")
    if make_url(url).host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Local database required")
    engine = create_engine(url)
    schema = "test_resolution_" + uuid4().hex
    try:
        with engine.begin() as c:
            for sql in runpy.run_path("db_migrations/versions/0010_geo_catalogue.py")["STATEMENTS"]:
                c.execute(
                    text(
                        sql.replace("geo.", schema + ".").replace(
                            "CREATE SCHEMA geo", "CREATE SCHEMA " + schema
                        )
                    )
                )
            c.execute(
                text(f"""CREATE TABLE {schema}.listings (
                id uuid PRIMARY KEY,title text,url text,description text,status text,
                price_amount numeric,price_currency text,city text,area text,street text,
                pois jsonb,poi_model_version text,poi_description_sha256 text,
                scraped_at timestamptz NOT NULL DEFAULT now())""")
            )
            for sql in runpy.run_path("db_migrations/versions/0011_planning_unit_resolution.py")[
                "STATEMENTS"
            ]:
                c.execute(
                    text(
                        sql.replace("geo.", schema + ".").replace(
                            "public.listings", schema + ".listings"
                        )
                    )
                )
        catalogue = PostgisCatalogueRepository(engine, schema)
        catalogue.publish(
            [
                GeoFeature(
                    "planning_units", "1", "boundary", "West", None, polygon(23.2, 23.3), {}
                ),
                GeoFeature(
                    "planning_units", "2", "boundary", "East", None, polygon(23.3, 23.4), {}
                ),
                GeoFeature(
                    "poi_13",
                    "9",
                    "poi",
                    "Болница Алфа",
                    "hospital",
                    {"type": "Point", "coordinates": [23.25, 42.7]},
                    {},
                ),
            ],
            {"mapping_version": "test"},
        )
        repo = PostgisResolutionRepository(engine, schema, schema)
        repo.publish_references(
            {
                "id": "a" * 64,
                "manifest": {},
                "aliases": [
                    {
                        "external_id": "1",
                        "old_name": "ул. Стара",
                        "new_name": "ул. Нова",
                        "source_note": "Official rename",
                    }
                ],
                "addresses": [
                    {
                        "external_id": "1",
                        "street": "ул. Нова",
                        "number": "12",
                        "settlement": "гр. София",
                        "name_key": "нова",
                        "geometry": {"type": "Point", "coordinates": [23.25, 42.7]},
                        "attributes": {},
                    }
                ],
            }
        )
        repo = PostgisResolutionRepository(engine, schema, schema)
        identifier = uuid4()
        with engine.begin() as c:
            c.execute(
                text(f"""INSERT INTO {schema}.listings
                (id,title,url,description,status,city,area,street,pois) VALUES
                (:id,'Test ad','https://example.test/ad','Апартамент','active',
                 'София','Лозенец','ул. Стара № 12','[]')"""),
                {"id": identifier},
            )
        yield repo, identifier
    finally:
        with engine.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


def test_real_address_support_and_idempotent_resolution(resolver):
    repo, identifier = resolver
    support = repo.support((("official_addresses", "1"),), 30)
    assert support["1"] == pytest.approx(1, abs=1e-5)
    assert "2" not in support
    result = resolve_listing_locations(repo)
    assert result["counts"]["saved"] == 1 and result["counts"]["accepted"] == 0
    report = repo.results(1)[0]
    assert report["candidates"][0]["planning_unit_id"] == "1"
    assert report["status"] == "ambiguous" and report["assignment"] is None
    assert report["observations"]["street"] == "ул. Стара № 12"
    assert resolve_listing_locations(repo)["counts"]["unchanged"] == 1
    assert report["listing_id"] == str(identifier)


def test_reviewed_result_reaches_map_and_stale_inputs_disappear(resolver):
    repo, identifier = resolver
    resolve_listing_locations(repo)
    result = repo.results(1)[0]
    repo.review(result["id"], "1", "reviewer", "property-1", "train", "west")
    map_repo = PostgisPlanningUnitMapRepository(repo.engine, repo.schema, repo.schema)
    assert len(map_repo.map_data()["ads"]) == 1
    with repo.engine.begin() as c:
        c.execute(
            text(f"UPDATE {repo.schema}.listings SET pois=CAST(:pois AS jsonb)"),
            {"pois": json.dumps([{"changed": True}])},
        )
    assert map_repo.map_data()["ads"] == []
    with repo.engine.begin() as c:
        c.execute(
            text(f"UPDATE {repo.schema}.listings SET pois=CAST(:pois AS jsonb)"),
            {"pois": json.dumps([])},
        )
    assert len(map_repo.map_data()["ads"]) == 1
    repo.activate_policy({"different_model": "v2"})
    assert map_repo.map_data()["ads"] == []
    resolve_listing_locations(repo)  # Re-activate original policy, retaining the reviewed result.
    assert len(map_repo.map_data()["ads"]) == 1
    repo.review(result["id"], None, "reviewer", "property-1", "train", "west")
    assert map_repo.map_data()["ads"] == []


def test_optimistic_save_rejects_changed_location_and_prediction(resolver):
    repo, identifier = resolver
    resolve_listing_locations(repo)
    result = repo.results(1)[0]
    original = next(repo.listings())
    with repo.engine.begin() as c:
        c.execute(
            text(f"UPDATE {repo.schema}.listings SET street='Different' WHERE id=:id"),
            {"id": identifier},
        )
    assert repo.save(original, result | {"id": "b" * 64, "input_fingerprint": "b" * 64}) is False


def test_reference_failure_keeps_previous_bundle_and_map_invalidates_on_new_reference(resolver):
    repo, _ = resolver
    resolve_listing_locations(repo)
    result = repo.results(1)[0]
    repo.review(result["id"], "1", "reviewer", "property", "train", "west")
    map_repo = PostgisPlanningUnitMapRepository(repo.engine, repo.schema, repo.schema)
    assert len(map_repo.map_data()["ads"]) == 1
    with pytest.raises(IntegrityError):
        repo.publish_references(
            {
                "id": "b" * 64,
                "manifest": {},
                "aliases": [
                    {"external_id": "1", "old_name": "A", "new_name": "B", "source_note": "test"}
                ],
                "addresses": [
                    {
                        "external_id": "1",
                        "settlement": "Sofia",
                        "street": "A",
                        "number": "1",
                        "name_key": "a",
                        "geometry": {"type": "Point", "coordinates": [0, 0]},
                        "attributes": {},
                    }
                ],
            }
        )
    assert len(map_repo.map_data()["ads"]) == 1
    repo.publish_references(
        {
            "id": "c" * 64,
            "manifest": {},
            "aliases": [
                {"external_id": "1", "old_name": "A", "new_name": "B", "source_note": "test"}
            ],
            "addresses": [
                {
                    "external_id": "2",
                    "settlement": "Sofia",
                    "street": "A",
                    "number": "1",
                    "name_key": "a",
                    "geometry": {"type": "Point", "coordinates": [23.25, 42.7]},
                    "attributes": {},
                }
            ],
        }
    )
    assert map_repo.map_data()["ads"] == []


def test_unknown_unit_and_empty_review_provenance_fail(resolver):
    repo, _ = resolver
    resolve_listing_locations(repo)
    result = repo.results(1)[0]
    with pytest.raises(ValueError):
        repo.review(result["id"], "missing", "reviewer", "property", "train", "west")
    with pytest.raises(ValueError):
        repo.review(result["id"], "1", " ", "property", "train", "west")


def test_independent_coordinate_uncertainty_and_map_freshness(resolver):
    repo, identifier = resolver
    observation = repo.record_coordinate(identifier, 23.3, 42.7, 100, "Independent address survey")
    current = next(repo.listings())
    assert current.verified_location["id"] == observation
    support = repo.support((("verified_locations", observation),), 100)
    assert support["1"] == pytest.approx(0.5, abs=0.01)
    assert support["2"] == pytest.approx(0.5, abs=0.01)
    resolve_listing_locations(repo)
    result = repo.results(1)[0]
    assert (
        result["conflict"] is False
    )  # Broad coordinate support overlaps structured address support.
    repo.review(result["id"], "1", "reviewer", "property", "train", "west")
    map_repo = PostgisPlanningUnitMapRepository(repo.engine, repo.schema, repo.schema)
    assert len(map_repo.map_data()["ads"]) == 1
    repo.record_coordinate(identifier, 23.35, 42.7, 30, "Updated independent observation")
    assert map_repo.map_data()["ads"] == []
    with pytest.raises(ValueError):
        repo.record_coordinate(identifier, 23.35, 42.7, 0, "Invalid uncertainty")


def test_review_export_contains_normalized_groups_and_original_observation_time(resolver):
    repo, _ = resolver
    resolve_listing_locations(repo)
    result = repo.results(1)[0]
    repo.review(result["id"], "1", " reviewer ", " property ", "train", " west ")
    examples = repo.reviewed_examples(result["context"])
    assert examples[0]["group_id"] == "property"
    assert examples[0]["geography_group"] == "west"
    assert examples[0]["observed_at"] == result["observed_at"]


def test_reverted_listing_reuses_historical_matching_resolution(resolver):
    repo, _ = resolver
    resolve_listing_locations(repo)
    first = repo.results(1)[0]
    repo.review(first["id"], "1", "reviewer", "property", "train", "west")
    map_repo = PostgisPlanningUnitMapRepository(repo.engine, repo.schema, repo.schema)
    assert len(map_repo.map_data()["ads"]) == 1
    with repo.engine.begin() as c:
        c.execute(text(f"UPDATE {repo.schema}.listings SET description='Changed snapshot B'"))
    resolve_listing_locations(repo)
    assert map_repo.map_data()["ads"] == []
    with repo.engine.begin() as c:
        c.execute(text(f"UPDATE {repo.schema}.listings SET description='Апартамент'"))
    assert resolve_listing_locations(repo)["counts"]["unchanged"] == 1
    assert len(map_repo.map_data()["ads"]) == 1


def test_candidate_preview_never_persists_or_claims_acceptance(resolver, monkeypatch):
    from apartment_finder.infrastructure.geo import candidate_preview

    repo, _ = resolver
    monkeypatch.setattr(candidate_preview, "PostgisResolutionRepository", lambda engine: repo)
    monkeypatch.setattr(
        candidate_preview,
        "PostgisPlanningUnitMapRepository",
        lambda engine: PostgisPlanningUnitMapRepository(engine, repo.schema, repo.schema),
    )
    listing_engine = create_engine(
        repo.engine.url.update_query_dict({"options": f"-csearch_path={repo.schema},public"})
    )
    try:
        data = candidate_preview.CandidatePreviewRepository(repo.engine, listing_engine).map_data()
        assert data["preview_mode"] is True and len(data["ads"]) == 1
        assert data["ads"][0]["basis"] == "candidate_preview"
        assert data["ads"][0]["confidence"] is None
        assert data["ads"][0]["planning_unit_id"] == "1"
        with repo.engine.connect() as c:
            assert (
                c.execute(text(f"SELECT count(*) FROM {repo.schema}.resolution")).scalar_one() == 0
            )
            assert (
                c.execute(
                    text(f"SELECT count(*) FROM {repo.schema}.resolution_review")
                ).scalar_one()
                == 0
            )
    finally:
        listing_engine.dispose()
