from dataclasses import dataclass
from uuid import UUID

from apartment_finder.application.ports.label_studio import LabelStudioGateway
from apartment_finder.application.ports.labeling_repository import LabelingRepository
from apartment_finder.infrastructure.label_studio.mapper import (
    annotation_revision,
    prediction_annotation_payload,
    saved_annotation_state,
    state_matches_task,
    validate_annotation_result,
)


@dataclass(frozen=True)
class ApproveBatchResult:
    batch_id: UUID
    approved: int
    failed: int


class ApproveBatch:
    def __init__(self, repository: LabelingRepository, label_studio: LabelStudioGateway):
        self._repository = repository
        self._label_studio = label_studio

    def execute(self, *, batch_id: UUID, actor: str) -> ApproveBatchResult:
        approved_task_ids: list[UUID] = []
        failed = 0
        for task in self._repository.get_batch_tasks(batch_id):
            try:
                if task.label_studio_task_id is None:
                    self._repository.record_sync_error(
                        task.id,
                        "Task has not been exported",
                    )
                    failed += 1
                    continue
                state = self._label_studio.get_task(task.label_studio_task_id)
                if not state_matches_task(state, task):
                    raise ValueError("Label Studio task text/hash does not match selected snapshot")
                annotation = saved_annotation_state(state)
                if annotation is None:
                    prediction = prediction_annotation_payload(state)
                    if prediction is None:
                        self._repository.record_sync_error(
                            task.id,
                            "No saved annotation or teacher prediction to approve",
                        )
                        failed += 1
                        continue
                    annotation = self._label_studio.create_annotation(
                        task.label_studio_task_id,
                        result=prediction,
                    )

                pois, problems = validate_annotation_result(
                    annotation.get("result") or [],
                    task.description_snapshot,
                )
                if problems:
                    self._repository.record_sync_error(task.id, "; ".join(problems))
                    failed += 1
                    continue
                self._repository.sync_annotation(
                    task.id,
                    annotation_id=annotation.get("id"),
                    result=pois,
                    revision=annotation_revision(annotation),
                    updated_at=annotation.get("updated_at"),
                )
                approved_task_ids.append(task.id)
            except Exception as exc:  # noqa: BLE001 - validate every task before approving batch
                self._repository.record_sync_error(task.id, f"{type(exc).__name__}: {exc}")
                failed += 1

        if failed:
            return ApproveBatchResult(batch_id=batch_id, approved=0, failed=failed)

        self._repository.approve_batch(
            batch_id,
            actor=actor,
            approved_task_ids=approved_task_ids,
        )
        return ApproveBatchResult(batch_id=batch_id, approved=len(approved_task_ids), failed=0)
