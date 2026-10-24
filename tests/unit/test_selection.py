from uuid import uuid4

import pytest

from apartment_finder.application.use_cases.select_for_labeling import SelectForLabeling
from apartment_finder.domain.labeling import (
    LabelingTask,
    description_content_hash,
    normalize_description,
)


class FakeLabelingRepository:
    def __init__(self, tasks):
        self.tasks = tasks
        self.requested_limit = None

    def reserve_candidates(self, limit: int):
        self.requested_limit = limit
        return self.tasks[:limit]

    def get_pending(self, limit: int):
        return self.tasks[:limit]


def test_normalize_description_collapses_whitespace():
    assert normalize_description("  spacious\n\nflat\tnear   metro  ") == "spacious flat near metro"


def test_description_content_hash_uses_normalized_text():
    assert description_content_hash("same\n description") == description_content_hash(
        " same description "
    )


def test_labeling_task_create_rejects_short_descriptions():
    with pytest.raises(ValueError, match="at least 80"):
        LabelingTask.create(listing_id=uuid4(), description="too short")


def test_select_for_labeling_returns_reserved_task_ids():
    task = LabelingTask.create(
        listing_id=uuid4(),
        description=(
            "A bright apartment with enough details for labeling and future evaluation "
            "work across neighborhood and amenity extraction."
        ),
    )
    repository = FakeLabelingRepository([task])

    result = SelectForLabeling(repository).execute(batch_size=5)

    assert repository.requested_limit == 5
    assert result.count == 1
    assert result.selected_task_ids == [task.id]


def test_select_for_labeling_rejects_non_positive_batch_size():
    with pytest.raises(ValueError, match="positive"):
        SelectForLabeling(FakeLabelingRepository([])).execute(batch_size=0)
