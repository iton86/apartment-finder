from dataclasses import dataclass
from uuid import UUID

from apartment_finder.application.ports.label_studio import LabelStudioGateway
from apartment_finder.application.ports.labeling_repository import LabelingRepository
from apartment_finder.infrastructure.label_studio.mapper import (
    annotation_revision,
    saved_annotation_state,
    state_matches_task,
    validate_annotation_result,
)


@dataclass(frozen=True)
class SyncAnnotationsResult:
    synced: int
    failed: int


class SyncAnnotations:
    def __init__(self, repository: LabelingRepository, label_studio: LabelStudioGateway):
        self._repository = repository
        self._label_studio = label_studio

    def execute(self, *, batch_id: UUID | None = None) -> SyncAnnotationsResult:
        synced = 0
        failed = 0
        tasks = (
            self._repository.get_batch_tasks(batch_id)
            if batch_id is not None
            else self._repository.get_exported_tasks()
        )
        for task in tasks:
            try:
                self._sync_task(task)
                synced += 1
            except SkippedTask:
                continue
            except Exception as exc:  # noqa: BLE001 - sync must continue task-by-task
                self._repository.record_sync_error(task.id, f"{type(exc).__name__}: {exc}")
                failed += 1
        return SyncAnnotationsResult(synced=synced, failed=failed)

    def execute_label_studio_task(self, *, label_studio_task_id: int) -> SyncAnnotationsResult:
        task = self._repository.get_by_label_studio_task_id(label_studio_task_id)
        if task is None:
            return SyncAnnotationsResult(synced=0, failed=1)
        try:
            self._sync_task(task)
        except SkippedTask:
            return SyncAnnotationsResult(synced=0, failed=0)
        except Exception as exc:  # noqa: BLE001 - webhook/event sync should record and return
            self._repository.record_sync_error(task.id, f"{type(exc).__name__}: {exc}")
            return SyncAnnotationsResult(synced=0, failed=1)
        return SyncAnnotationsResult(synced=1, failed=0)

    def _sync_task(self, task) -> None:
        if task.label_studio_task_id is None:
            raise SkippedTask
        state = self._label_studio.get_task(task.label_studio_task_id)
        if not state_matches_task(state, task):
            raise ValueError("Label Studio task text/hash does not match selected snapshot")
        annotation = saved_annotation_state(state)
        if annotation is None:
            self._repository.sync_annotation(
                task.id,
                annotation_id=None,
                result=None,
                revision=None,
                updated_at=state.updated_at,
                deleted=True,
            )
            return
        pois, problems = validate_annotation_result(
            annotation.get("result") or [],
            task.description_snapshot,
        )
        if problems:
            raise ValueError("; ".join(problems))
        self._repository.sync_annotation(
            task.id,
            annotation_id=annotation.get("id"),
            result=pois,
            revision=annotation_revision(annotation),
            updated_at=annotation.get("updated_at"),
        )


class SkippedTask(Exception):
    pass

