"""Atomic reference catalogue publication and spatial planning-unit queries."""

import json
import math
import re
from dataclasses import asdict

from sqlalchemy import text

from apartment_finder.domain.geo import GeoFeature, fingerprint


class PostgisCatalogueRepository:
    """Keep complete immutable releases and switch readers in one transaction."""

    def __init__(self, engine, schema: str = "geo", repair_invalid: bool = False):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
            raise ValueError("Invalid catalogue schema")
        self.engine = engine
        self.schema = schema
        self.repair_invalid = repair_invalid

    def publish(self, features: list[GeoFeature], manifest: dict) -> str:
        """Validate and atomically publish; serialize competing manual imports.

        External snapshots are retained before this transaction. Invalid topology
        aborts all inserts, preserving the previous active catalogue.
        """
        if not any(f.kind == "boundary" for f in features):
            raise ValueError("Planning units required")
        identities = [(f.dataset, f.external_id) for f in features]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate source identities")
        # Include normalized data, so identical raw bytes with different mappings
        # cannot accidentally reuse a release. Retrieval timestamps are excluded.
        release = fingerprint(
            {
                "mapping": manifest["mapping_version"],
                "geometry_policy": "make-valid-v1" if self.repair_invalid else "strict-v1",
                "features": sorted(
                    (f.dataset, f.external_id, fingerprint(asdict(f))) for f in features
                ),
            }
        )
        schema = self.schema
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"{schema}.sofia"}
            )
            exists = connection.execute(
                text(f"SELECT id FROM {schema}.release WHERE id=:id"), {"id": release}
            ).scalar_one_or_none()
            if exists is None:
                connection.execute(
                    text(
                        f"INSERT INTO {schema}.release (id,manifest) "
                        "VALUES (:id,CAST(:manifest AS jsonb))"
                    ),
                    {
                        "id": release,
                        "manifest": json.dumps(
                            manifest
                            | {
                                "geometry_policy": "make-valid-v1"
                                if self.repair_invalid
                                else "strict-v1"
                            }
                        ),
                    },
                )
                statement = text(f"""INSERT INTO {schema}.feature
                    (release_id,dataset,external_id,kind,name,category,attributes,
                     geom,original_geom,repair_reason)
                    SELECT :release,:dataset,:external_id,:kind,:name,:category,
                        CAST(:attributes AS jsonb),
                        CASE WHEN :repair AND NOT ST_IsValid(g)
                            THEN ST_Multi(ST_CollectionExtract(ST_MakeValid(g),3)) ELSE g END,
                        CASE WHEN :repair AND NOT ST_IsValid(g) THEN g END,
                        CASE WHEN :repair AND NOT ST_IsValid(g) THEN ST_IsValidReason(g) END
                    FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326) AS g) input""")
                for offset in range(0, len(features), 500):
                    rows = []
                    for feature in features[offset : offset + 500]:
                        row = asdict(feature)
                        row["attributes"] = json.dumps(row["attributes"], allow_nan=False)
                        row["geometry"] = json.dumps(row["geometry"], allow_nan=False)
                        rows.append(row | {"release": release, "repair": self.repair_invalid})
                    connection.execute(statement, rows)
            connection.execute(
                text(f"""INSERT INTO {schema}.catalogue_state (name,release_id)
                VALUES ('sofia',:release) ON CONFLICT (name)
                DO UPDATE SET release_id=excluded.release_id"""),
                {"release": release},
            )
        return release

    def locate(self, longitude: float, latitude: float) -> list[dict]:
        """Return all covering planning units; shared edges can produce two results."""
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("Invalid WGS84 coordinates")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(f"""SELECT f.external_id AS planning_unit_id,
                f.name, f.release_id FROM {self.schema}.feature f
                JOIN {self.schema}.catalogue_state c ON c.release_id=f.release_id
                WHERE c.name='sofia' AND f.kind='boundary' AND f.dataset='planning_units'
                AND ST_Covers(f.geom,ST_SetSRID(ST_MakePoint(:lon,:lat),4326))
                ORDER BY f.external_id"""),
                {"lon": longitude, "lat": latitude},
            )
            return [dict(row) for row in rows.mappings()]
