from abc import ABC, abstractmethod
from uuid import UUID

from apartment_finder.domain.labeling import LabelingBatch, LabelingTask, TeacherAnnotation


class LabelingRepository(ABC):
    @abstractmethod
    def reserve_candidates(self, limit: int) -> list[LabelingTask]:
        """Atomically reserve eligible listings for future annotation."""
        ...

    @abstractmethod
    def get_pending(self, limit: int) -> list[LabelingTask]:
        """Return pending tasks in creation order."""
        ...

    @abstractmethod
    def create_export_batch(
        self,
        *,
        project_id: int,
        limit: int,
        batch_id: UUID | None = None,
    ) -> LabelingBatch:
        """Claim pending tasks for one Label Studio export batch."""
        ...

    @abstractmethod
    def get_batch_tasks(self, batch_id: UUID) -> list[LabelingTask]:
        """Return tasks in a selection/export batch."""
        ...

    @abstractmethod
    def get_exported_tasks(self) -> list[LabelingTask]:
        """Return all tasks linked to Label Studio, including approved tasks."""
        ...

    @abstractmethod
    def get_by_label_studio_task_id(self, label_studio_task_id: int) -> LabelingTask | None:
        """Find the selected snapshot exported as a Label Studio task."""
        ...

    @abstractmethod
    def save_teacher_annotation(self, task_id: UUID, annotation: TeacherAnnotation) -> None: ...

    @abstractmethod
    def mark_exported(self, task_id: UUID, *, project_id: int, label_studio_task_id: int) -> None:
        """Record the Label Studio task created for a selected snapshot."""
        ...

    @abstractmethod
    def record_export_error(self, task_id: UUID, error: str) -> None: ...

    @abstractmethod
    def approve_batch(
        self,
        batch_id: UUID,
        *,
        actor: str,
        approved_task_ids: list[UUID],
    ) -> None:
        """Mark current synced revisions as approved after explicit human action."""
        ...

    @abstractmethod
    def sync_annotation(
        self,
        task_id: UUID,
        *,
        annotation_id: int | None,
        result: list[dict] | None,
        revision: int | None,
        updated_at,
        deleted: bool = False,
    ) -> None:
        """Persist current Label Studio annotation state for a task."""
        ...

    @abstractmethod
    def record_sync_error(self, task_id: UUID, error: str) -> None: ...

    @abstractmethod
    def approved_training_rows(self) -> list[dict]:
        """Return only approved current annotation revisions for model training."""
        ...

