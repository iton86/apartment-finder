"""PostGIS evidence support, optimistic resolver persistence and review storage."""

import json
import math
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text

from apartment_finder.domain.geo import fingerprint, validate_geometry
from apartment_finder.domain.location_resolution import ListingLocationInput, Place, PlaceIndex
from apartment_finder.infrastructure.geo.repository import PostgisCatalogueRepository


class PostgisResolutionRepository:
    """Pin catalogue/reference versions for a bounded resolver run."""

    def __init__(self, engine, schema="geo", listing_schema="public"):
        self.engine = engine
        self.schema = PostgisCatalogueRepository(engine, schema).schema
        self.listing_schema = PostgisCatalogueRepository(engine, listing_schema).schema
        with engine.connect() as c:
            self.release_id = c.execute(
                text(f"SELECT release_id FROM {schema}.catalogue_state WHERE name='sofia'")
            ).scalar_one_or_none()
            self.bundle_id = c.execute(
                text(f"SELECT bundle_id FROM {schema}.reference_state WHERE name='sofia'")
            ).scalar_one_or_none()
        if self.release_id is None:
            raise ValueError("Publish a catalogue before resolving listings")

    def publish_references(self, bundle: dict) -> str:
        """Atomically publish complete official references, preserving earlier bundles."""
        if not bundle["addresses"] or not bundle["aliases"]:
            raise ValueError("Complete address and alias references are required")
        s = self.schema
        with self.engine.begin() as c:
            c.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": s + ".references"}
            )
            found = c.execute(
                text(f"SELECT id FROM {s}.reference_bundle WHERE id=:id"), {"id": bundle["id"]}
            ).scalar_one_or_none()
            if found is None:
                c.execute(
                    text(
                        f"INSERT INTO {s}.reference_bundle(id,manifest) "
                        "VALUES (:id,CAST(:manifest AS jsonb))"
                    ),
                    {"id": bundle["id"], "manifest": json.dumps(bundle["manifest"])},
                )
                insert = text(f"""INSERT INTO {s}.address
                    (bundle_id,external_id,settlement,street,number,name_key,geom,attributes)
                    VALUES (:bundle,:external_id,:settlement,:street,:number,:name_key,
                    ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326),CAST(:attributes AS jsonb))""")
                for offset in range(0, len(bundle["addresses"]), 500):
                    rows = [
                        row
                        | {
                            "bundle": bundle["id"],
                            "geometry": json.dumps(row["geometry"]),
                            "attributes": json.dumps(row["attributes"]),
                        }
                        for row in bundle["addresses"][offset : offset + 500]
                    ]
                    c.execute(insert, rows)
                c.execute(
                    text(f"""INSERT INTO {s}.street_alias
                    (bundle_id,external_id,old_name,new_name,source_note)
                    VALUES (:bundle,:external_id,:old_name,:new_name,:source_note)"""),
                    [row | {"bundle": bundle["id"]} for row in bundle["aliases"]],
                )
            c.execute(
                text(
                    f"INSERT INTO {s}.reference_state VALUES ('sofia',:id) "
                    "ON CONFLICT(name) DO UPDATE SET bundle_id=excluded.bundle_id"
                ),
                {"id": bundle["id"]},
            )
        return bundle["id"]

    def index(self) -> PlaceIndex:
        """Load match metadata only; spatial geometries remain in PostGIS."""
        s = self.schema
        places, aliases = [], []
        with self.engine.connect() as c:
            rows = c.execute(
                text(
                    f"SELECT dataset,external_id,name,kind,category "
                    f"FROM {s}.feature WHERE release_id=:release AND "
                    "name IS NOT NULL AND (kind='surface' OR category IS NOT NULL)"
                ),
                {"release": self.release_id},
            ).mappings()
            places.extend(
                Place(
                    r["dataset"],
                    r["external_id"],
                    r["name"],
                    "street" if r["kind"] == "surface" else r["category"],
                )
                for r in rows
            )
            if self.bundle_id:
                rows = c.execute(
                    text(
                        f"SELECT external_id,street,settlement,number FROM {s}.address "
                        "WHERE bundle_id=:bundle"
                    ),
                    {"bundle": self.bundle_id},
                ).mappings()
                places.extend(
                    Place(
                        "official_addresses",
                        r["external_id"],
                        r["street"],
                        "address",
                        r["settlement"],
                        r["number"],
                    )
                    for r in rows
                )
                aliases = list(
                    c.execute(
                        text(
                            f"SELECT old_name,new_name FROM {s}.street_alias "
                            "WHERE bundle_id=:bundle"
                        ),
                        {"bundle": self.bundle_id},
                    ).mappings()
                )
        return PlaceIndex(places, aliases)

    def listings(self):
        """Stream active listing snapshots in pages; no long-lived database transaction."""
        after = UUID(int=0)
        while True:
            with self.engine.connect() as c:
                rows = (
                    c.execute(
                        text(f"""SELECT id,description,city,area,street,pois,
                    poi_model_version,poi_description_sha256,scraped_at
                    FROM {self.listing_schema}.listings
                    WHERE id>:after AND status='active' AND description IS NOT NULL
                    ORDER BY id LIMIT 100"""),
                        {"after": after},
                    )
                    .mappings()
                    .all()
                )
                observations = {row["id"]: self.observation(c, row["id"]) for row in rows}
            if not rows:
                return
            for row in rows:
                yield ListingLocationInput(
                    str(row["id"]),
                    row["description"],
                    row["city"],
                    row["area"],
                    row["street"],
                    row["pois"] or [],
                    row["poi_model_version"],
                    row["poi_description_sha256"],
                    row["scraped_at"].isoformat(),
                    observations[row["id"]],
                )
            after = rows[-1]["id"]

    @lru_cache(maxsize=4096)
    def support(self, identities: tuple[tuple[str, str], ...], radius: float) -> dict[str, float]:
        """Integrate a uniform spatial support region over full planning-unit polygons.

        Uses metric EPSG:7801 buffering and exact polygon intersections. Supports
        multiple place branches as a mixture, retaining mass outside all units.
        """
        observation_ids = [
            identifier for dataset, identifier in identities if dataset == "verified_locations"
        ]
        s = self.schema
        feature_ids = [
            identifier
            for dataset, identifier in identities
            if dataset not in {"official_addresses", "verified_locations"}
        ]
        datasets = [
            dataset
            for dataset, _ in identities
            if dataset not in {"official_addresses", "verified_locations"}
        ]
        address_ids = [
            identifier for dataset, identifier in identities if dataset == "official_addresses"
        ]
        with self.engine.connect() as c:
            rows = (
                c.execute(
                    text(f"""WITH input AS (
                SELECT geom FROM {s}.feature f WHERE release_id=:release
                AND (dataset,external_id) IN (SELECT * FROM unnest(
                    CAST(:datasets AS text[]),CAST(:ids AS text[])))
                UNION ALL SELECT geom FROM {s}.address WHERE bundle_id=:bundle
                    AND external_id=ANY(CAST(:address_ids AS text[]))
                UNION ALL SELECT geom FROM {s}.location_observation
                    WHERE id=ANY(CAST(:observation_ids AS text[]))
                ), region AS (SELECT ST_UnaryUnion(ST_Collect(
                    ST_Buffer(ST_Transform(geom,7801),:radius))) AS geom FROM input),
                sized AS (SELECT geom,ST_Area(geom) AS area FROM region)
                SELECT f.external_id,LEAST(1.0,ST_Area(ST_Intersection(
                    ST_Transform(f.geom,7801),r.geom))/NULLIF(r.area,0)) AS mass
                FROM {s}.feature f CROSS JOIN sized r WHERE f.release_id=:release
                    AND f.dataset='planning_units' AND f.kind='boundary'
                    AND ST_Intersects(f.geom,ST_Transform(r.geom,4326))"""),
                    {
                        "release": self.release_id,
                        "bundle": self.bundle_id,
                        "datasets": datasets,
                        "ids": feature_ids,
                        "address_ids": address_ids,
                        "observation_ids": observation_ids,
                        "radius": radius,
                    },
                )
                .mappings()
                .all()
            )
        return {
            row["external_id"]: float(row["mass"])
            for row in rows
            if row["mass"] and row["mass"] > 0.0001
        }

    def exists(self, listing_id: str, input_fingerprint: str) -> bool:
        """Detect already resolved immutable inputs before expensive spatial work."""
        with self.engine.connect() as c:
            return bool(
                c.execute(
                    text(
                        f"SELECT 1 FROM {self.schema}.resolution "
                        "WHERE listing_id=:id AND input_fingerprint=:hash"
                    ),
                    {"id": UUID(listing_id), "hash": input_fingerprint},
                ).scalar()
            )

    def save(self, listing: ListingLocationInput, result: dict) -> bool:
        """Lock and compare all listing inputs; stale work never becomes accepted data."""
        with self.engine.begin() as c:
            c.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": self.schema + ".resolver." + listing.listing_id},
            )
            row = (
                c.execute(
                    text(f"""SELECT id,description,city,area,street,pois,
                poi_model_version,poi_description_sha256,status,scraped_at
                FROM {self.listing_schema}.listings
                WHERE id=:id FOR UPDATE"""),
                    {"id": UUID(listing.listing_id)},
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["status"] != "active":
                return False
            current = ListingLocationInput(
                str(row["id"]),
                row["description"] or "",
                row["city"],
                row["area"],
                row["street"],
                row["pois"] or [],
                row["poi_model_version"],
                row["poi_description_sha256"],
                row["scraped_at"].isoformat(),
                self.observation(c, row["id"]),
            )
            if current.snapshot_hash != listing.snapshot_hash:
                return False
            inserted = c.execute(
                text(f"""INSERT INTO {self.schema}.resolution
                (id,listing_id,release_id,bundle_id,input_fingerprint,description_sha256,
                 prediction_fingerprint,resolver_version,status,result)
                VALUES (:id,:listing,:release,:bundle,:input,:description,:prediction,:version,
                        :status,CAST(:result AS jsonb)) ON CONFLICT DO NOTHING RETURNING id"""),
                {
                    "id": result["id"],
                    "listing": UUID(listing.listing_id),
                    "release": self.release_id,
                    "bundle": self.bundle_id,
                    "input": result["input_fingerprint"],
                    "description": listing.text_hash,
                    "prediction": listing.prediction_hash,
                    "version": result["resolver_version"],
                    "status": result["status"],
                    "result": json.dumps(result),
                },
            ).scalar_one_or_none()
            return inserted is not None

    def review(
        self,
        resolution_id: str,
        unit: str | None,
        reviewer: str,
        group_id: str,
        split: str,
        geography_group: str,
    ) -> int:
        """Append an auditable label; null unit explicitly means unknown/outside."""
        if (
            not geography_group.strip()
            or not reviewer.strip()
            or not group_id.strip()
            or split not in {"train", "calibration", "test"}
        ):
            raise ValueError("Reviewer, property group and valid split are required")
        with self.engine.begin() as c:
            row = c.execute(
                text(f"SELECT release_id FROM {self.schema}.resolution WHERE id=:id"),
                {"id": resolution_id},
            ).one_or_none()
            if row is None:
                raise ValueError("Unknown resolution ID")
            if unit is not None:
                found = c.execute(
                    text(
                        f"SELECT 1 FROM {self.schema}.feature WHERE release_id=:release "
                        "AND dataset='planning_units' AND external_id=:unit"
                    ),
                    {"release": row[0], "unit": unit},
                ).scalar()
                if not found:
                    raise ValueError("Reviewed unit does not exist in that release")
            return c.execute(
                text(f"""INSERT INTO {self.schema}.resolution_review
                (resolution_id,planning_unit_id,reviewer,group_id,split,geography_group)
                VALUES (:id,:unit,:reviewer,:group,:split,:geography) RETURNING id"""),
                {
                    "id": resolution_id,
                    "unit": unit,
                    "reviewer": reviewer.strip(),
                    "group": group_id.strip(),
                    "geography": geography_group.strip(),
                    "split": split,
                },
            ).scalar_one()

    def reviewed_examples(self, context: dict) -> list[dict]:
        """Export one latest human label per listing and only the requested input context."""
        with self.engine.connect() as c:
            rows = (
                c.execute(
                    text(f"""SELECT DISTINCT ON (r.listing_id)
                r.listing_id,r.result,r.description_sha256,v.planning_unit_id,
                v.group_id,v.split,v.geography_group
                FROM {self.schema}.resolution r JOIN {self.schema}.resolution_review v
                ON v.resolution_id=r.id WHERE r.release_id=:release
                ORDER BY r.listing_id,v.id DESC"""),
                    {"release": self.release_id},
                )
                .mappings()
                .all()
            )
        return [
            {
                "listing_id": str(r["listing_id"]),
                "result": r["result"],
                "description_sha256": r["description_sha256"],
                "geography_group": r["geography_group"],
                "observed_at": r["result"]["observed_at"],
                "planning_unit_id": r["planning_unit_id"],
                "group_id": r["group_id"],
                "split": r["split"],
            }
            for r in rows
            if r["result"]["context"] == context
        ]

    def results(self, limit: int) -> list[dict]:
        """Return recent candidate reports for review/export."""
        with self.engine.connect() as c:
            return list(
                c.execute(
                    text(
                        f"SELECT result FROM {self.schema}.resolution "
                        "WHERE release_id=:release ORDER BY created_at DESC,id LIMIT :limit"
                    ),
                    {"release": self.release_id, "limit": limit},
                ).scalars()
            )

    def activate_policy(self, policy: dict) -> str:
        """Atomically select the resolver/model/crosswalk policy allowed on the map."""
        identifier = fingerprint(policy)
        with self.engine.begin() as c:
            c.execute(
                text(
                    f"INSERT INTO {self.schema}.resolution_policy(id,policy) "
                    "VALUES (:id,CAST(:policy AS jsonb)) ON CONFLICT DO NOTHING"
                ),
                {"id": identifier, "policy": json.dumps(policy)},
            )
            c.execute(
                text(
                    f"INSERT INTO {self.schema}.resolution_state VALUES ('sofia',:id) "
                    "ON CONFLICT(name) DO UPDATE SET policy_id=excluded.policy_id"
                ),
                {"id": identifier},
            )
        return identifier

    def observation(self, connection, listing_id):
        """Read the latest independently sourced location observation, if present."""
        return connection.execute(
            text(f"""SELECT jsonb_build_object('id',id,
            'source',source,'uncertainty_m',uncertainty_m) FROM {self.schema}.location_observation
            WHERE listing_id=:id ORDER BY created_at DESC,id DESC LIMIT 1"""),
            {"id": listing_id},
        ).scalar_one_or_none()

    def record_coordinate(self, listing_id, longitude, latitude, uncertainty_m, source):
        """Record an explicit source coordinate with uncertainty; never infer from a POI."""
        point = validate_geometry({"type": "Point", "coordinates": [longitude, latitude]}, "poi")
        if not math.isfinite(uncertainty_m) or not 1 <= uncertainty_m <= 2000 or not source.strip():
            raise ValueError("Coordinate uncertainty (1..2000 m) and provenance are required")
        payload = {
            "listing_id": str(listing_id),
            "point": point,
            "uncertainty": uncertainty_m,
            "source": source.strip(),
        }
        identifier = fingerprint(payload)
        with self.engine.begin() as c:
            c.execute(
                text(f"""INSERT INTO {self.schema}.location_observation
                (id,listing_id,geom,uncertainty_m,source) VALUES (:id,:listing,
                ST_SetSRID(ST_GeomFromGeoJSON(:point),4326),:uncertainty,:source)
                ON CONFLICT DO NOTHING"""),
                {
                    "id": identifier,
                    "listing": UUID(str(listing_id)),
                    "point": json.dumps(point),
                    "uncertainty": uncertainty_m,
                    "source": source.strip(),
                },
            )
        return identifier

    def known_units(self):
        """List planning-unit identities from the pinned catalogue release."""
        with self.engine.connect() as c:
            return set(
                c.execute(
                    text(
                        f"SELECT external_id FROM {self.schema}.feature "
                        "WHERE release_id=:release AND dataset='planning_units' AND kind='boundary'"
                    ),
                    {"release": self.release_id},
                ).scalars()
            )
