"""Read a consistent planning-unit map and exclude stale ad assignments."""

import hashlib
import json
from urllib.parse import urlsplit

from sqlalchemy import bindparam, text

from apartment_finder.domain.location_resolution import ListingLocationInput
from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment
from apartment_finder.infrastructure.geo.repository import PostgisCatalogueRepository


class PostgisPlanningUnitMapRepository:
    """Expose boundaries and accepted ads through read-only snapshot queries."""

    def __init__(self, engine, schema: str = "geo", listing_schema: str = "public"):
        # Reuse strict identifier validation; schema names never originate in HTTP input.
        self.schema = PostgisCatalogueRepository(engine, schema).schema
        self.listing_schema = PostgisCatalogueRepository(engine, listing_schema).schema
        self.engine = engine

    def map_data(self, assignments: list[PlanningUnitAssignment] | None = None) -> dict:
        """Pin release and listing reads to one repeatable-read transaction."""
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
            with c.begin():
                c.execute(text("SET TRANSACTION READ ONLY"))
                release = c.execute(
                    text(f"SELECT release_id FROM {self.schema}.catalogue_state WHERE name='sofia'")
                ).scalar_one_or_none()
                if release is None:
                    raise ValueError("No active catalogue. Publish a geospatial release first.")
                units = (
                    c.execute(
                        text(f"""SELECT external_id, name, ST_AsGeoJSON(geom) AS geometry,
                    ST_AsGeoJSON(ST_PointOnSurface(geom)) AS marker
                    FROM {self.schema}.feature WHERE release_id=:release
                    AND kind='boundary' AND dataset='planning_units' ORDER BY name,external_id"""),
                        {"release": release},
                    )
                    .mappings()
                    .all()
                )
                if assignments is None:
                    assignments = self._stored_assignments(c, release)
                current = [a for a in assignments if a.release_id == release]
                rows = []
                if current:
                    query = text(f"""SELECT id,title,url,description,price_amount,price_currency
                        FROM {self.listing_schema}.listings WHERE id IN :ids
                        AND status='active'""").bindparams(bindparam("ids", expanding=True))
                    rows = (
                        c.execute(query, {"ids": [a.listing_id for a in current]}).mappings().all()
                    )
        indexed = {str(row["id"]): row for row in rows}
        unit_ids = {row["external_id"] for row in units}
        ads = []
        for assignment in current:
            row = indexed.get(str(assignment.listing_id))
            if row is None or assignment.planning_unit_id not in unit_ids:
                continue
            digest = hashlib.sha256((row["description"] or "").encode()).hexdigest()
            if digest != assignment.description_sha256:
                continue
            safe_url = None
            try:
                url = urlsplit(row["url"] or "")
                if (
                    url.scheme in {"http", "https"}
                    and url.hostname
                    and not url.username
                    and not url.password
                ):
                    safe_url = row["url"]
            except ValueError:
                pass  # Malformed scraped links must not hide an otherwise valid map.
            ads.append(
                {
                    "listing_id": str(assignment.listing_id),
                    "title": row["title"],
                    "url": safe_url,
                    "planning_unit_id": assignment.planning_unit_id,
                    "confidence": assignment.confidence,
                    "basis": assignment.basis,
                    "evidence": assignment.evidence,
                    "resolver_version": assignment.resolver_version,
                    "location_precision": "unit_only",
                    "price": str(row["price_amount"]) if row["price_amount"] is not None else None,
                    "currency": row["price_currency"],
                }
            )
        return {
            "release_id": release,
            "units": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": json.loads(row["geometry"]),
                        "properties": {
                            "id": row["external_id"],
                            "name": row["name"],
                            "marker": json.loads(row["marker"])["coordinates"],
                        },
                    }
                    for row in units
                ],
            },
            "ads": ads,
            "excluded_assignments": len(assignments) - len(ads),
        }

    def _stored_assignments(self, connection, release):
        rows = (
            connection.execute(
                text(f"""SELECT r.result,v.planning_unit_id AS reviewed_unit,
            v.reviewer,v.id AS review_id,
            l.id,l.description,l.city,l.area,l.street,l.pois,
            l.poi_model_version,l.poi_description_sha256,l.scraped_at,
            (SELECT jsonb_build_object('id',id,'source',source,'uncertainty_m',uncertainty_m)
             FROM {self.schema}.location_observation WHERE listing_id=l.id
             ORDER BY created_at DESC,id DESC LIMIT 1) AS observation
            FROM {self.schema}.resolution r JOIN {self.listing_schema}.listings l
                ON l.id=r.listing_id
            LEFT JOIN LATERAL (SELECT * FROM {self.schema}.resolution_review
                WHERE resolution_id=r.id ORDER BY id DESC LIMIT 1) v ON true
            WHERE r.release_id=:release AND l.status='active'
                AND r.result->>'policy_id'=(SELECT policy_id FROM
                    {self.schema}.resolution_state WHERE name='sofia')
                AND r.bundle_id IS NOT DISTINCT FROM (
                    SELECT bundle_id FROM {self.schema}.reference_state WHERE name='sofia')
            ORDER BY r.listing_id,r.created_at DESC,r.id DESC"""),
                {"release": release},
            )
            .mappings()
            .all()
        )
        assignments = []
        selected_listings = set()
        for row in rows:
            if row["id"] in selected_listings:
                continue
            result = row["result"]
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
                row["observation"],
            )
            if current.snapshot_hash != result["listing_snapshot_hash"]:
                continue
            selected_listings.add(row["id"])
            if row["review_id"] is not None:
                if row["reviewed_unit"] is None:
                    continue
                payload = {
                    "listing_id": str(row["id"]),
                    "planning_unit_id": row["reviewed_unit"],
                    "release_id": release,
                    "description_sha256": current.text_hash,
                    "resolver_version": result["resolver_version"],
                    "basis": "human_reviewed",
                    "reviewed_by": row["reviewer"],
                    "evidence": ["Human-reviewed planning-unit assignment"],
                }
            elif result["status"] == "accepted":
                payload = result["assignment"]
            else:
                continue
            assignments.append(PlanningUnitAssignment.model_validate_json(json.dumps(payload)))
        return assignments
