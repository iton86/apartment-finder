from dataclasses import dataclass
from uuid import UUID

from apartment_finder.application.ports.labeling_repository import LabelingRepository

DEFAULT_LABELING_BATCH_SIZE = 100


@dataclass(frozen=True)
class SelectForLabelingResult:
    selected_task_ids: list[UUID]
    count: int


class SelectForLabeling:
    def __init__(self, repository: LabelingRepository):
        self._repository = repository

    def execute(self, batch_size: int = DEFAULT_LABELING_BATCH_SIZE) -> SelectForLabelingResult:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        tasks = self._repository.reserve_candidates(batch_size)
        return SelectForLabelingResult(
            selected_task_ids=[task.id for task in tasks],
            count=len(tasks),
        )
