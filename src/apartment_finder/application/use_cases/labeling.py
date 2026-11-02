"""Labeling use cases: select ads, export them to Label Studio, sync and approve reviews."""

from dataclasses import dataclass
from uuid import UUID

from apartment_finder.application.ports import (
    LabelingRepository,
    LabelStudioGateway,
    TeacherAnnotationProvider,
)
from apartment_finder.domain.label_studio import (
    annotation_revision,
    prediction_annotation_payload,
    saved_annotation_state,
    state_matches_task,
    task_payload,
    validate_annotation_result,
)
from apartment_finder.domain.labeling import TeacherStatus

DEFAULT_LABELING_BATCH_SIZE = 100


@dataclass(frozen=True)
class SelectForLabelingResult:
    """IDs of the labeling tasks reserved by one selection run."""

    selected_task_ids: list[UUID]
    count: int


class SelectForLabeling:
    """Reserve eligible listings as pending labeling tasks."""

    def __init__(self, repository: LabelingRepository):
        self._repository = repository

    def execute(self, batch_size: int = DEFAULT_LABELING_BATCH_SIZE) -> SelectForLabelingResult:
        """Reserve up to ``batch_size`` candidates.

        Raises:
            ValueError: If ``batch_size`` is below 1.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        tasks = self._repository.reserve_candidates(batch_size)
        return SelectForLabelingResult(
            selected_task_ids=[task.id for task in tasks],
            count=len(tasks),
        )


@dataclass(frozen=True)
class ExportSelectedBatchResult:
    """Outcome of exporting one batch: tasks that got a Label Studio ID and tasks that did not."""

    batch_id: UUID
    exported: int
    failed: int


class ExportSelectedBatch:
    """Send a batch of pending tasks to Label Studio, with teacher pre-annotations if available."""

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
        """Claim a batch, import it into the Label Studio project and record the task IDs.

        Tasks already exported are skipped. When a teacher is configured, tasks without a
        teacher annotation are labeled first so the reviewer starts from a prediction.
        Tasks Label Studio does not return an ID for are recorded as export errors and
        counted as failed.
        """
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


@dataclass(frozen=True)
class ApproveBatchResult:
    """Outcome of approving a batch; ``approved`` is 0 whenever any task failed."""

    batch_id: UUID
    approved: int
    failed: int


class ApproveBatch:
    """Approve a reviewed batch, all-or-nothing, after validating every task."""

    def __init__(self, repository: LabelingRepository, label_studio: LabelStudioGateway):
        self._repository = repository
        self._label_studio = label_studio

    def execute(self, *, batch_id: UUID, actor: str) -> ApproveBatchResult:
        """Validate each task's annotation and approve the batch in the name of ``actor``.

        A task with no saved annotation falls back to the teacher prediction, which is
        saved as an annotation first. If any task fails (not exported, mismatched snapshot,
        invalid annotation), the errors are recorded per task and nothing is approved.
        """
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


@dataclass(frozen=True)
class SyncAnnotationsResult:
    """Counts of tasks synced and tasks that failed; skipped tasks are in neither."""

    synced: int
    failed: int


class SyncAnnotations:
    """Pull current annotation state from Label Studio into the labeling repository."""

    def __init__(self, repository: LabelingRepository, label_studio: LabelStudioGateway):
        self._repository = repository
        self._label_studio = label_studio

    def execute(self, *, batch_id: UUID) -> SyncAnnotationsResult:
        """Sync every task of the batch, recording per-task errors instead of aborting."""
        synced = 0
        failed = 0
        for task in self._repository.get_batch_tasks(batch_id):
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
        """Sync the single task exported as ``label_studio_task_id`` (e.g. on a webhook event).

        An unknown ID counts as one failure; an unexported task is skipped.
        """
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
    """Signals that a task has nothing to sync (not exported yet); not an error."""
