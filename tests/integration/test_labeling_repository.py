"""
Run with local Postgres up: `make up && make migrate`, then
`APP_ENV=local uv run pytest tests/integration/test_labeling_repository.py -v`.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from apartment_finder.domain.entities import TransactionType
from apartment_finder.domain.labeling import (
    LabelingStatus,
    TeacherAnnotation,
    TeacherStatus,
    description_content_hash,
    normalize_description,
)
from apartment_finder.infrastructure.persistence.models import (
    Base,
    LabelingTaskModel,
    ListingModel,
)
from apartment_finder.infrastructure.persistence.postgres_labeling_repository import (
    PostgresLabelingRepository,
)
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    url = make_url(build_postgres_connection_url())
    if url.host not in {"localhost", "127.0.0.1", "::1", "postgres"}:
        pytest.fail("Integration tests require a local PostgreSQL host")
    schema = f"test_labeling_{uuid4().hex}"
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    repo = None
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        test_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        repo = PostgresLabelingRepository(test_url.render_as_string(hide_password=False))
        Base.metadata.create_all(repo._engine)
        yield repo
    finally:
        if repo is not None:
            repo._engine.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


def make_listing(
    index: int,
    *,
    description: str | None = None,
    area: str = "Център",
    scraped_at: datetime | None = None,
) -> ListingModel:
    return ListingModel(
        id=uuid4(),
        source="test-source",
        external_id=f"listing-{index}",
        url=f"https://example.test/listing-{index}",
        transaction_type=TransactionType.SALE,
        title=f"Test listing {index}",
        city="София",
        area=area,
        description=description
        if description is not None
        else f"Detailed description {index} with enough normalized text for human labeling work.",
        scraped_at=scraped_at or datetime.now(UTC),
        last_seen_at=scraped_at or datetime.now(UTC),
    )


def add_listings(repository, *listings: ListingModel) -> None:
    with Session(repository._engine) as session:
        session.add_all(listings)
        session.commit()


def selected_listing_areas(repository, task_ids) -> list[str]:
    with Session(repository._engine) as session:
        return [
            area
            for (area,) in session.execute(
                select(ListingModel.area)
                .join(LabelingTaskModel, LabelingTaskModel.listing_id == ListingModel.id)
                .where(LabelingTaskModel.id.in_(task_ids))
                .order_by(LabelingTaskModel.created_at.asc())
            )
        ]


def valid_description(label: str) -> str:
    return (
        f"{label} sunny two-bedroom apartment close to transit, parks, schools, and shops, "
        "with enough useful text for annotation."
    )


def test_reserve_candidates_skips_empty_and_short_descriptions(repository):
    add_listings(
        repository,
        make_listing(1, description=""),
        make_listing(2, description=None),
        make_listing(3, description="short after normalization"),
        make_listing(4, description=valid_description("valid")),
    )

    tasks = repository.reserve_candidates(10)

    assert len(tasks) == 1
    assert tasks[0].description_snapshot == normalize_description(valid_description("valid"))


def test_reserve_candidates_deduplicates_descriptions(repository):
    duplicate = valid_description("duplicate")
    add_listings(
        repository,
        make_listing(1, description=duplicate),
        make_listing(2, description=f"  {duplicate.replace(' ', '   ')}  "),
        make_listing(3, description=valid_description("unique")),
    )

    tasks = repository.reserve_candidates(10)

    assert len(tasks) == 2
    assert len({task.content_hash for task in tasks}) == 2
    assert {task.content_hash for task in tasks} == {
        description_content_hash(duplicate),
        description_content_hash(valid_description("unique")),
    }


def test_reserve_candidates_skips_already_selected_listings(repository):
    selected = make_listing(1, description=valid_description("selected"))
    fresh = make_listing(2, description=valid_description("fresh"))
    selected_id = selected.id
    fresh_id = fresh.id
    selected_description = selected.description
    add_listings(repository, selected, fresh)
    with Session(repository._engine) as session:
        session.add(
            LabelingTaskModel(
                listing_id=selected_id,
                content_hash=description_content_hash(selected_description),
                description_snapshot=normalize_description(selected_description),
            )
        )
        session.commit()

    tasks = repository.reserve_candidates(10)

    assert len(tasks) == 1
    assert tasks[0].listing_id == fresh_id


def test_reserve_candidates_respects_batch_size_and_samples_across_areas(repository):
    now = datetime.now(UTC)
    add_listings(
        repository,
        make_listing(1, area="Лозенец", scraped_at=now, description=valid_description("a1")),
        make_listing(
            2,
            area="Лозенец",
            scraped_at=now - timedelta(minutes=1),
            description=valid_description("a2"),
        ),
        make_listing(
            3,
            area="Младост",
            scraped_at=now - timedelta(minutes=2),
            description=valid_description("b1"),
        ),
    )

    tasks = repository.reserve_candidates(2)

    assert len(tasks) == 2
    assert set(selected_listing_areas(repository, [task.id for task in tasks])) == {
        "Лозенец",
        "Младост",
    }


def test_repeated_execution_does_not_create_duplicate_tasks(repository):
    add_listings(
        repository,
        make_listing(1, description=valid_description("one")),
        make_listing(2, description=valid_description("two")),
    )

    first = repository.reserve_candidates(10)
    second = repository.reserve_candidates(10)

    assert len(first) == 2
    assert second == []
    with Session(repository._engine) as session:
        assert len(session.scalars(select(LabelingTaskModel)).all()) == 2


def test_get_pending_returns_pending_tasks_in_creation_order(repository):
    add_listings(
        repository,
        make_listing(1, description=valid_description("one")),
        make_listing(2, description=valid_description("two")),
    )
    tasks = repository.reserve_candidates(10)
    with Session(repository._engine) as session:
        model = session.get(LabelingTaskModel, tasks[0].id)
        model.status = LabelingStatus.COMPLETED
        session.commit()

    pending = repository.get_pending(10)

    assert [task.id for task in pending] == [tasks[1].id]


def test_concurrent_reservations_do_not_duplicate_tasks(repository):
    add_listings(
        repository,
        *[
            make_listing(index, description=valid_description(f"concurrent {index}"))
            for index in range(20)
        ],
    )
    connection_url = repository._engine.url.render_as_string(hide_password=False)

    def reserve_batch():
        concurrent_repo = PostgresLabelingRepository(connection_url)
        try:
            return concurrent_repo.reserve_candidates(10)
        finally:
            concurrent_repo._engine.dispose()

    with ThreadPoolExecutor(max_workers=4) as executor:
        batches = list(executor.map(lambda _: reserve_batch(), range(4)))

    tasks = [task for batch in batches for task in batch]
    assert len(tasks) == 20
    assert len({task.id for task in tasks}) == 20
    assert len({task.listing_id for task in tasks}) == 20
    assert len({task.content_hash for task in tasks}) == 20


def test_concurrent_reservations_deduplicate_descriptions(repository):
    duplicate_description = valid_description("shared duplicate text")
    add_listings(
        repository,
        *[make_listing(index, description=duplicate_description) for index in range(1, 7)],
        *[
            make_listing(index, description=valid_description(f"unique {index}"))
            for index in range(7, 19)
        ],
    )
    connection_url = repository._engine.url.render_as_string(hide_password=False)

    def reserve_batch():
        concurrent_repo = PostgresLabelingRepository(connection_url)
        try:
            return concurrent_repo.reserve_candidates(5)
        finally:
            concurrent_repo._engine.dispose()

    with ThreadPoolExecutor(max_workers=4) as executor:
        batches = list(executor.map(lambda _: reserve_batch(), range(4)))

    tasks = [task for batch in batches for task in batch]
    assert len(tasks) == 13
    assert len({task.id for task in tasks}) == 13
    assert len({task.listing_id for task in tasks}) == 13
    assert len({task.content_hash for task in tasks}) == 13
    assert description_content_hash(duplicate_description) in {task.content_hash for task in tasks}


def test_approved_training_rows_require_current_synced_revision(repository):
    add_listings(repository, make_listing(1, description=valid_description("approved")))
    [task] = repository.reserve_candidates(1)
    batch = repository.create_export_batch(project_id=7, limit=1)
    repository.save_teacher_annotation(
        task.id,
        TeacherAnnotation(
            provider="fake",
            model="fake-model",
            prompt_version="prompt-v1",
            status=TeacherStatus.OK,
            result=[],
        ),
    )
    repository.mark_exported(task.id, project_id=7, label_studio_task_id=101)
    repository.record_sync_error(task.id, "Saved reviewer edit failed validation")
    assert repository.approved_training_rows() == []

    repository.sync_annotation(
        task.id,
        annotation_id=500,
        result=[],
        revision=1,
        updated_at="2026-10-24T10:00:00Z",
    )
    repository.approve_batch(batch.id, actor="reviewer@example.test", approved_task_ids=[task.id])

    rows = repository.approved_training_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == str(task.id)
    assert rows[0]["pois"] == []

    repository.sync_annotation(
        task.id,
        annotation_id=500,
        result=[
            {
                "category": "park",
                "name": "Южен парк",
                "distance": {"value": None, "unit": "nearby"},
                "evidence": "до ЮЖЕН ПАРК",
            }
        ],
        revision=2,
        updated_at="2026-10-24T11:00:00Z",
    )

    assert repository.approved_training_rows() == []


def test_get_exported_tasks_includes_approved_and_excludes_unexported(repository):
    add_listings(
        repository,
        make_listing(1, description=valid_description("exported")),
        make_listing(2, description=valid_description("pending")),
    )
    tasks = repository.reserve_candidates(2)
    batch = repository.create_export_batch(project_id=7, limit=1)
    [exported] = repository.get_batch_tasks(batch.id)
    repository.mark_exported(exported.id, project_id=7, label_studio_task_id=101)
    repository.sync_annotation(
        exported.id, annotation_id=500, result=[], revision=1,
        updated_at="2026-10-24T10:00:00Z",
    )
    repository.approve_batch(batch.id, actor="reviewer", approved_task_ids=[exported.id])

    assert len(tasks) == 2
    assert [task.id for task in repository.get_exported_tasks()] == [exported.id]
