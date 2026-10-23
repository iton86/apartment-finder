"""Select newly stored listing ads for scheduled model re-training.

Each run samples from ads that have never been selected for the requested
dataset, records that selection in the database, and writes a JSONL manifest
for the downstream training job. The manifest is an artifact; the database
ledger is the source of truth that keeps scheduled runs idempotent.
"""

import argparse
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import and_, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apartment_finder.infrastructure.persistence.models import (
    ListingModel,
    TrainingAdSelectionModel,
)
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url
from apartment_finder.interface.logging_config import configure_logging
from training.jsonl import write_jsonl

logger = logging.getLogger(__name__)

DEFAULT_DATASET = "listings"
DEFAULT_LIMIT = 500
DEFAULT_SAMPLE_RATE = 0.2


@dataclass(frozen=True)
class CandidateAd:
    listing_id: UUID
    source: str
    external_id: str
    url: str
    title: str
    city: str
    area: str
    scraped_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class SelectionRequest:
    dataset: str
    selection_run: str
    limit: int
    sample_rate: float
    seed: str
    reason: str

    def validate(self) -> None:
        if not self.dataset.strip():
            raise ValueError("dataset must not be empty")
        if not self.selection_run.strip():
            raise ValueError("selection_run must not be empty")
        if self.limit < 1:
            raise ValueError("limit must be positive")
        if not 0 < self.sample_rate <= 1:
            raise ValueError("sample_rate must be > 0 and <= 1")
        if not self.reason.strip():
            raise ValueError("reason must not be empty")


def default_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("ad-selection-%Y%m%dT%H%M%SZ")


def choose_candidate_ids[CandidateId](
    candidate_ids: list[CandidateId], limit: int, sample_rate: float, seed: str
) -> list[CandidateId]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if not 0 < sample_rate <= 1:
        raise ValueError("sample_rate must be > 0 and <= 1")
    if not candidate_ids:
        return []

    shuffled = list(candidate_ids)
    random.Random(seed).shuffle(shuffled)
    target = min(limit, max(1, round(len(shuffled) * sample_rate)))
    return shuffled[:target]


def manifest_record(
    candidate: CandidateAd,
    request: SelectionRequest,
    selected_at: datetime,
) -> dict:
    return {
        "dataset": request.dataset,
        "selection_run": request.selection_run,
        "selected_at": selected_at.isoformat(),
        "reason": request.reason,
        "listing_id": str(candidate.listing_id),
        "source": candidate.source,
        "external_id": candidate.external_id,
        "url": candidate.url,
        "title": candidate.title,
        "city": candidate.city,
        "area": candidate.area,
        "scraped_at": candidate.scraped_at.isoformat(),
        "last_seen_at": candidate.last_seen_at.isoformat(),
    }


class TrainingAdSelectionService:
    def __init__(self, connection_url: str):
        self._engine = create_engine(connection_url, pool_pre_ping=True)

    def close(self) -> None:
        self._engine.dispose()

    def select(self, request: SelectionRequest) -> list[dict]:
        request.validate()
        with Session(self._engine) as session:
            candidates = self._load_candidates(session, request.dataset)
            selected_ids = set(
                choose_candidate_ids(
                    [candidate.listing_id for candidate in candidates],
                    request.limit,
                    request.sample_rate,
                    request.seed,
                )
            )
            selected = [
                candidate for candidate in candidates if candidate.listing_id in selected_ids
            ]
            claimed = []
            selected_at = datetime.now(UTC)
            for candidate in selected:
                try:
                    with session.begin_nested():
                        session.add(
                            TrainingAdSelectionModel(
                                dataset=request.dataset,
                                listing_id=candidate.listing_id,
                                selection_run=request.selection_run,
                                selected_at=selected_at,
                                reason=request.reason,
                            )
                        )
                    claimed.append(candidate)
                except IntegrityError:
                    logger.info(
                        "Ad %s was already selected for dataset %s",
                        candidate.listing_id,
                        request.dataset,
                    )
            session.commit()

        return [manifest_record(candidate, request, selected_at) for candidate in claimed]

    @staticmethod
    def _load_candidates(session: Session, dataset: str) -> list[CandidateAd]:
        selection_match = and_(
            TrainingAdSelectionModel.dataset == dataset,
            TrainingAdSelectionModel.listing_id == ListingModel.id,
        )
        stmt = (
            select(
                ListingModel.id,
                ListingModel.source,
                ListingModel.external_id,
                ListingModel.url,
                ListingModel.title,
                ListingModel.city,
                ListingModel.area,
                ListingModel.scraped_at,
                ListingModel.last_seen_at,
            )
            .outerjoin(TrainingAdSelectionModel, selection_match)
            .where(TrainingAdSelectionModel.id.is_(None))
            .order_by(ListingModel.scraped_at.asc())
        )
        return [CandidateAd(*row) for row in session.execute(stmt)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--selection-run", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument(
        "--seed",
        default=None,
        help="Sampling seed. Defaults to --selection-run so each scheduled run is stable.",
    )
    parser.add_argument(
        "--reason",
        default="scheduled-continuous-retraining",
        help="Stored with each ledger row and manifest record.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Manifest path. Defaults to data/<selection-run>.jsonl.",
    )
    parser.add_argument(
        "--log-level", default=None, choices=["debug", "info", "warning", "error", "critical"]
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    selection_run = args.selection_run or default_run_id()
    request = SelectionRequest(
        dataset=args.dataset,
        selection_run=selection_run,
        limit=args.limit,
        sample_rate=args.sample_rate,
        seed=args.seed or selection_run,
        reason=args.reason,
    )
    out = args.out or Path("data") / f"{selection_run}.jsonl"

    service = TrainingAdSelectionService(build_postgres_connection_url())
    try:
        records = service.select(request)
    finally:
        service.close()

    write_jsonl(out, records)
    logger.info("Selected %d ad(s) for %s into %s", len(records), request.dataset, out)
    print(f"Selected {len(records)} ad(s) for {request.dataset}.")
    print(f"Manifest: {out}")


if __name__ == "__main__":
    main()
