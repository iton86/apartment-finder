"""Read-only candidate visualization across local listing/reference databases."""

from uuid import UUID

from sqlalchemy import text

from apartment_finder.domain.location_resolution import (
    ListingLocationInput,
    extract_evidence,
    rank_units,
)
from apartment_finder.infrastructure.geo.map_repository import PostgisPlanningUnitMapRepository
from apartment_finder.infrastructure.geo.resolution_repository import PostgisResolutionRepository


class CandidatePreviewRepository:
    """Show genuine ads with explicitly unreviewed suggested units; never persist them."""

    def __init__(self, catalogue_engine, listing_engine, limit=20):
        if not 1 <= limit <= 50:
            raise ValueError("Preview limit must be between 1 and 50")
        self.catalogue_engine = catalogue_engine
        self.listing_engine = listing_engine
        self.limit = limit

    def map_data(self, assignments=None):
        """Rank a bounded sample without saving resolutions, reviews or assignments."""
        references = PostgisResolutionRepository(self.catalogue_engine)
        index = references.index()
        data = PostgisPlanningUnitMapRepository(self.catalogue_engine).map_data([])
        if data["release_id"] != references.release_id:
            raise ValueError("Catalogue changed during preview; refresh to retry")
        with self.listing_engine.connect() as c:
            with c.begin():
                c.execute(text("SET TRANSACTION READ ONLY"))
                rows = (
                    c.execute(
                        text("""SELECT id,title,url,description,city,area,street,pois,
                    poi_model_version,poi_description_sha256,scraped_at,price_amount,price_currency
                    FROM listings WHERE status='active' AND description IS NOT NULL
                    AND street IS NOT NULL ORDER BY id LIMIT :limit"""),
                        {"limit": self.limit},
                    )
                    .mappings()
                    .all()
                )
        ads = []
        for row in rows:
            listing = ListingLocationInput(
                str(row["id"]),
                row["description"],
                row["city"],
                row["area"],
                row["street"],
                row["pois"] or [],
                row["poi_model_version"],
                row["poi_description_sha256"],
                row["scraped_at"].isoformat(),
            )
            evidence, notes = extract_evidence(listing, index)
            supports = [
                references.support(
                    tuple(sorted((p.dataset, p.external_id) for p in g.places)), g.radius_m
                )
                for g in evidence
            ]
            result = rank_units(evidence, supports, notes)
            if not result["candidates"]:
                continue
            top = result["candidates"][0]
            # No probability, acceptance or human-review claim is made in preview mode.
            ads.append(
                {
                    "listing_id": str(UUID(str(row["id"]))),
                    "title": row["title"],
                    "url": None,
                    "planning_unit_id": top["planning_unit_id"],
                    "confidence": None,
                    "basis": "candidate_preview",
                    "evidence": [
                        "UNREVIEWED: highest-ranked candidate only; the true unit may differ.",
                        *top["evidence"][:8],
                        f"{len(result['candidates'])} candidate units; "
                        f"conflict: {result['conflict']}.",
                    ],
                    "location_precision": "unit_only",
                    "price": str(row["price_amount"]) if row["price_amount"] is not None else None,
                    "currency": row["price_currency"],
                }
            )
        data.update({"ads": ads, "preview_mode": True, "excluded_assignments": 0})
        return data
