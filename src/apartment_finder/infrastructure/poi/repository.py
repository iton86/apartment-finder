"""Save model output on listing rows with optimistic description checks."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from apartment_finder.application.ports import PoiCandidate
from apartment_finder.infrastructure.persistence.models import ListingModel


def description_hash(text: str) -> str:
    """Return the SHA-256 hex digest of the description, used to detect stale predictions."""
    return hashlib.sha256(text.encode()).hexdigest()


class ListingPoiRepository:
    """Reads listings that need POI prediction and saves the results."""

    def __init__(self, engine):
        self.engine = engine

    def pending(self, model_version):
        """Yield listings with a nonblank description whose POIs are missing or stale.

        Stale means the model version or the description hash differs. Listings are
        read in pages of 100 ordered by ID.
        """
        last_id = None
        while True:
            query = (
                select(
                    ListingModel.id,
                    ListingModel.description,
                    ListingModel.pois,
                    ListingModel.poi_model_version,
                    ListingModel.poi_description_sha256,
                )
                .order_by(ListingModel.id)
                .limit(100)
            )
            if last_id is not None:
                query = query.where(ListingModel.id > last_id)
            with Session(self.engine) as session:
                rows = session.execute(query).all()
            if not rows:
                return
            for row in rows:
                if (
                    row.description
                    and row.description.strip()
                    and (
                        row.pois is None
                        or row.poi_model_version != model_version
                        or row.poi_description_sha256 != description_hash(row.description)
                    )
                ):
                    yield PoiCandidate(row.id, row.description)
            last_id = rows[-1].id

    def save(self, candidate, prediction):
        """Save the prediction if the description is unchanged.

        The update is conditional on the stored description still equalling the
        candidate's, so a concurrent edit is never overwritten.

        Returns:
            True if the row was updated, False if the description changed meanwhile.

        Raises:
            ValueError: If the prediction was made for a different description.
        """
        if prediction["description_sha256"] != description_hash(candidate.description):
            raise ValueError("Prediction does not belong to this description")
        with Session(self.engine) as session:
            result = session.execute(
                update(ListingModel)
                .where(
                    ListingModel.id == candidate.id,
                    ListingModel.description == candidate.description,
                )
                .values(
                    pois=prediction["pois"],
                    poi_model_version=prediction["model_version"],
                    poi_description_sha256=prediction["description_sha256"],
                    poi_predicted_at=datetime.now(UTC),
                )
            )
            session.commit()
            return result.rowcount == 1
