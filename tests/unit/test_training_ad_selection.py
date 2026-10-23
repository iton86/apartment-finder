from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apartment_finder.domain.entities import TransactionType
from apartment_finder.infrastructure.persistence.models import (
    Base,
    ListingModel,
    TrainingAdSelectionModel,
)
from training.ad_selection import (
    SelectionRequest,
    TrainingAdSelectionService,
    choose_candidate_ids,
)


def test_choose_candidate_ids_is_bounded_and_stable():
    candidates = list(range(10))

    first = choose_candidate_ids(candidates, limit=3, sample_rate=0.8, seed="run")
    second = choose_candidate_ids(candidates, limit=3, sample_rate=0.8, seed="run")

    assert first == second
    assert len(first) == 3
    assert set(first) <= set(candidates)


def test_choose_candidate_ids_keeps_small_batches_nonempty():
    assert choose_candidate_ids([42], limit=10, sample_rate=0.01, seed="run") == [42]


def test_selection_records_new_ads_once_per_dataset(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'selection.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    listing_ids = [uuid4(), uuid4(), uuid4()]
    with Session(engine) as session:
        session.add_all(
            [
                ListingModel(
                    id=listing_id,
                    source="imot.bg",
                    external_id=f"listing-{index}",
                    url=f"https://example.test/listing-{index}",
                    transaction_type=TransactionType.SALE,
                    title=f"Test listing {index}",
                    city="София",
                    area="Център",
                )
                for index, listing_id in enumerate(listing_ids, start=1)
            ]
        )
        session.commit()

    service = TrainingAdSelectionService(url)
    try:
        first = service.select(
            SelectionRequest(
                dataset="poi-extractor",
                selection_run="run-1",
                limit=2,
                sample_rate=1.0,
                seed="stable",
                reason="test",
            )
        )
        second = service.select(
            SelectionRequest(
                dataset="poi-extractor",
                selection_run="run-2",
                limit=10,
                sample_rate=1.0,
                seed="stable",
                reason="test",
            )
        )
        other_dataset = service.select(
            SelectionRequest(
                dataset="price-model",
                selection_run="run-3",
                limit=10,
                sample_rate=1.0,
                seed="stable",
                reason="test",
            )
        )
    finally:
        service.close()
        engine.dispose()

    assert len(first) == 2
    assert len(second) == 1
    assert len(other_dataset) == 3
    assert {record["dataset"] for record in first + second} == {"poi-extractor"}
    assert first[0]["listing_id"] in {str(listing_id) for listing_id in listing_ids}
    assert first[0]["source"] == "imot.bg"

    check = create_engine(url)
    with Session(check) as session:
        rows = session.scalars(select(TrainingAdSelectionModel)).all()
    check.dispose()
    assert len(rows) == 6
