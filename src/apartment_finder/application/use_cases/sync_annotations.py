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
    skipped: int = 0


class SyncAnnotations:
    def __init__(self, repository: LabelingRepository, label_studio: LabelStudioGateway):
        self._repository = repository
        self._label_studio = label_studio

    def execute(self, *, batch_id: UUID) -> SyncAnnotationsResult:
        synced = 0
        failed = 0
        skipped = 0
        for task in self._repository.get_batch_tasks(batch_id):
            try:
                self._sync_task(task)
                synced += 1
            except SkippedTask:
                skipped += 1
                continue
            except Exception as exc:  # noqa: BLE001 - sync must continue task-by-task
                self._repository.record_sync_error(task.id, f"{type(exc).__name__}: {exc}")
                failed += 1
        return SyncAnnotationsResult(synced=synced, failed=failed, skipped=skipped)

    def execute_label_studio_task(self, *, label_studio_task_id: int) -> SyncAnnotationsResult:
        task = self._repository.get_by_label_studio_task_id(label_studio_task_id)
        if task is None:
            return SyncAnnotationsResult(synced=0, failed=1)
        try:
            self._sync_task(task)
        except SkippedTask:
            return SyncAnnotationsResult(synced=0, failed=0, skipped=1)
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
            if task.human_annotation_id is None and task.human_revision is None:
                raise SkippedTask
            self._repository.sync_annotation(
                task.id,
                annotation_id=None,
                result=None,
                revision=None,
                updated_at=state.updated_at,
                deleted=True,
            )
            return
        result = annotation.get("result")
        if not isinstance(result, list):
            raise ValueError("Saved Label Studio annotation must contain a result list")
        pois, problems = validate_annotation_result(
            result,
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
