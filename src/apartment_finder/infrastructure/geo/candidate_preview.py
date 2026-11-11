"""Read-only candidate visualization across local listing/reference databases."""

from uuid import UUID

from sqlalchemy import text

from apartment_finder.domain.location_resolution import (
    ListingLocationInput,
    extract_evidence,
    rank_units,
    normalize_name,
)
from apartment_finder.infrastructure.geo.map_repository import PostgisPlanningUnitMapRepository
from apartment_finder.infrastructure.geo.resolution_repository import PostgisResolutionRepository


# Coarse guardrail, not an administrative boundary. Preview must abstain rather
# than plot an obviously non-Sofia unit; production needs official city polygons.
SOFIA_PREVIEW_BOUNDS = (23.0, 42.55, 23.65, 42.85)


def _sofia_city(city: str) -> bool:
    return normalize_name(city or "") in {"софия", "sofia"}


def _inside_sofia_preview(marker: list[float]) -> bool:
    west, south, east, north = SOFIA_PREVIEW_BOUNDS
    return west <= marker[0] <= east and south <= marker[1] <= north


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
        # The catalogue is Sofia-specific. Do not silently project ads from other
        # settlements into it. The marker check also catches distant candidates.
        eligible_units = {
            feature["properties"]["id"]
            for feature in data["units"]["features"]
            if _inside_sofia_preview(feature["properties"]["marker"])
        }
        ads = []
        unresolved = 0
        for row in rows:
            if not _sofia_city(row["city"]):
                unresolved += 1
                continue
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
            candidates = [
                candidate for candidate in result["candidates"]
                if candidate["planning_unit_id"] in eligible_units
            ]
            # Do not replace a geographically invalid winner with a weaker
            # runner-up: that would disguise a cross-city matching error.
            if (
                not candidates
                or result["candidates"][0]["planning_unit_id"] not in eligible_units
                or result["conflict"]
                or candidates[0]["features"][0] < 0.25
            ):
                unresolved += 1
                continue
            top = candidates[0]
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
        data.update({
            "ads": ads,
            "preview_mode": True,
            "excluded_assignments": 0,
            "unresolved_preview_ads": unresolved,
        })
        return data
