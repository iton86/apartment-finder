from abc import ABC, abstractmethod

from apartment_finder.domain.labeling import LabelStudioTaskPayload, LabelStudioTaskState


class LabelStudioGateway(ABC):
    @abstractmethod
    def import_tasks(
        self,
        project_id: int,
        tasks: list[LabelStudioTaskPayload],
    ) -> dict[str, int]:
        """Import tasks and return Label Studio task IDs keyed by task UUID string."""
        ...

    @abstractmethod
    def get_task(self, task_id: int) -> LabelStudioTaskState:
        """Fetch current task state, including saved annotations and predictions."""
        ...

    @abstractmethod
    def create_annotation(
        self,
        task_id: int,
        *,
        result: list[dict],
        completed_by: int | None = None,
    ) -> dict:
        """Create a saved annotation through a supported Label Studio API."""
        ...
