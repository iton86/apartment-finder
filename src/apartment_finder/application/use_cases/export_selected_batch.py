from dataclasses import dataclass
from uuid import UUID

from apartment_finder.application.ports.label_studio import LabelStudioGateway
from apartment_finder.application.ports.labeling_repository import LabelingRepository
from apartment_finder.application.ports.teacher_annotation import TeacherAnnotationProvider
from apartment_finder.domain.labeling import TeacherStatus
from apartment_finder.infrastructure.label_studio.mapper import task_payload


@dataclass(frozen=True)
class ExportSelectedBatchResult:
    batch_id: UUID
    exported: int
    failed: int


class ExportSelectedBatch:
    def __init__(
        self,
        repository: LabelingRepository,
        label_studio: LabelStudioGateway,
        teacher: TeacherAnnotationProvider | None = None,
    ):
        self._repository = repository
        self._label_studio = label_studio
        self._teacher = teacher

    def execute(
        self,
        *,
        project_id: int,
        batch_size: int,
        batch_id: UUID | None = None,
    ) -> ExportSelectedBatchResult:
        batch = self._repository.create_export_batch(
            project_id=project_id,
            limit=batch_size,
            batch_id=batch_id,
        )
        tasks = self._repository.get_batch_tasks(batch.id)
        payloads = []
        failed = 0
        for task in tasks:
            if task.label_studio_task_id is not None:
                continue
            if task.teacher_status is TeacherStatus.MISSING and self._teacher is not None:
                self._repository.save_teacher_annotation(task.id, self._teacher.label(task))
                task = next(
                    reloaded
                    for reloaded in self._repository.get_batch_tasks(batch.id)
                    if reloaded.id == task.id
                )
            payloads.append(task_payload(task))

        imported = self._label_studio.import_tasks(project_id, payloads)
        for payload in payloads:
            label_studio_task_id = imported.get(str(payload.task_id))
            if label_studio_task_id is None:
                self._repository.record_export_error(
                    payload.task_id,
                    "Label Studio import did not return or reconcile a task ID",
                )
                failed += 1
            else:
                self._repository.mark_exported(
                    payload.task_id,
                    project_id=project_id,
                    label_studio_task_id=label_studio_task_id,
                )

        return ExportSelectedBatchResult(
            batch_id=batch.id,
            exported=len(payloads) - failed,
            failed=failed,
        )
