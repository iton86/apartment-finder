from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from apartment_finder.application.use_cases.approve_batch import ApproveBatch
from apartment_finder.application.use_cases.export_selected_batch import ExportSelectedBatch
from apartment_finder.application.use_cases.sync_annotations import SyncAnnotations
from apartment_finder.domain.labeling import (
    LabelingBatch,
    LabelingTask,
    LabelStudioTaskState,
    TeacherAnnotation,
    TeacherStatus,
)
from apartment_finder.infrastructure.label_studio.client import (
    LabelStudioClient,
    LabelStudioError,
    task_data_matches,
)
from apartment_finder.infrastructure.label_studio.mapper import (
    task_payload,
    validate_prediction_offsets,
)
from training.label_studio import CATEGORY, NAME, TEXT, UNIT

TEXT_VALUE = "Двустаен до ЮЖЕН ПАРК, 10 мин от МОЛ БЪЛГАРИЯ."
PARK = {
    "category": "park",
    "name": "Южен парк",
    "distance": {"value": None, "unit": "nearby"},
    "evidence": "до ЮЖЕН ПАРК",
}


def make_task(**overrides) -> LabelingTask:
    values = {
        "id": uuid4(),
        "listing_id": uuid4(),
        "content_hash": "a" * 64,
        "description_snapshot": TEXT_VALUE,
        "selection_batch_id": uuid4(),
    }
    values.update(overrides)
    return LabelingTask(**values)


def accepted_annotation(annotation_id: int = 10) -> dict:
    return {
        "id": annotation_id,
        "result": [
            {
                "id": "r1",
                "from_name": CATEGORY,
                "to_name": TEXT,
                "type": "labels",
                "value": {"start": 9, "end": 21, "text": "до ЮЖЕН ПАРК", "labels": ["park"]},
            },
            {
                "id": "r1",
                "from_name": NAME,
                "to_name": TEXT,
                "type": "textarea",
                "value": {"start": 9, "end": 21, "text": ["Южен парк"]},
            },
            {
                "id": "r1",
                "from_name": UNIT,
                "to_name": TEXT,
                "type": "choices",
                "value": {
                    "start": 9,
                    "end": 21,
                    "text": "до ЮЖЕН ПАРК",
                    "choices": ["nearby"],
                },
            },
        ],
        "updated_at": "2026-10-24T10:00:00Z",
        "was_cancelled": False,
    }


@dataclass
class FakeRepository:
    tasks: list[LabelingTask]
    batch_id: UUID = field(default_factory=uuid4)
    exported: dict[UUID, int] = field(default_factory=dict)
    export_errors: dict[UUID, str] = field(default_factory=dict)
    sync_errors: dict[UUID, str] = field(default_factory=dict)
    synced: dict[UUID, dict] = field(default_factory=dict)
    approved: list[UUID] = field(default_factory=list)

    def create_export_batch(self, *, project_id: int, limit: int, batch_id: UUID | None = None):
        self.batch_id = batch_id or self.batch_id
        self.tasks = [
            LabelingTask(**{**task.__dict__, "selection_batch_id": self.batch_id})
            for task in self.tasks[:limit]
        ]
        return LabelingBatch(id=self.batch_id, label_studio_project_id=project_id)

    def get_batch_tasks(self, batch_id: UUID):
        return self.tasks

    def save_teacher_annotation(self, task_id: UUID, annotation: TeacherAnnotation):
        self.tasks = [
            LabelingTask(
                **{
                    **task.__dict__,
                    "teacher_provider": annotation.provider,
                    "teacher_model": annotation.model,
                    "teacher_prompt_version": annotation.prompt_version,
                    "teacher_status": annotation.status,
                    "teacher_result": annotation.result,
                    "teacher_problems": annotation.problems,
                }
            )
            if task.id == task_id
            else task
            for task in self.tasks
        ]

    def mark_exported(self, task_id: UUID, *, project_id: int, label_studio_task_id: int):
        self.exported[task_id] = label_studio_task_id

    def record_export_error(self, task_id: UUID, error: str):
        self.export_errors[task_id] = error

    def record_sync_error(self, task_id: UUID, error: str):
        self.sync_errors[task_id] = error

    def sync_annotation(self, task_id: UUID, **kwargs):
        self.synced[task_id] = kwargs

    def approve_batch(self, batch_id: UUID, *, actor: str, approved_task_ids: list[UUID]):
        self.approved = approved_task_ids


class FakeLabelStudio:
    def __init__(self, states=None):
        self.states = states or {}
        self.created_annotations = []
        self.imported_payloads = []

    def import_tasks(self, project_id: int, tasks):
        self.imported_payloads.extend(tasks)
        return {str(task.task_id): index for index, task in enumerate(tasks, start=100)}

    def get_task(self, task_id: int):
        state = self.states[task_id]
        if isinstance(state, Exception):
            raise state
        return state

    def create_annotation(self, task_id: int, *, result: list[dict], completed_by=None):
        annotation = {"id": 900 + task_id, "result": result, "updated_at": "2026-10-24T10:00:00Z"}
        self.created_annotations.append((task_id, result))
        return annotation


class FakeTeacher:
    def label(self, task):
        return TeacherAnnotation(
            provider="fake",
            model="fake-model",
            prompt_version="p1",
            status=TeacherStatus.OK,
            result=[PARK],
        )


def test_mapper_validates_teacher_offsets_against_exact_text():
    task = make_task(
        teacher_status=TeacherStatus.OK,
        teacher_result=[{**PARK, "evidence": "до Борисова градина"}],
    )

    assert validate_prediction_offsets(task)
    payload = task_payload(task)
    assert payload.predictions[0]["result"] == []
    assert "Борисова градина" in payload.data["review_notes"]


def test_export_selected_batch_generates_teacher_predictions_and_marks_imported_tasks():
    task = make_task()
    repository = FakeRepository([task])
    label_studio = FakeLabelStudio()

    result = ExportSelectedBatch(repository, label_studio, FakeTeacher()).execute(
        project_id=7,
        batch_size=10,
    )

    assert result.exported == 1
    assert repository.exported[task.id] == 100
    assert label_studio.imported_payloads[0].predictions[0]["result"]


def test_approve_batch_preserves_saved_human_corrections_instead_of_recreating():
    task = make_task(label_studio_task_id=55)
    corrected = accepted_annotation()
    for result in corrected["result"]:
        if result["from_name"] == UNIT:
            result["value"]["choices"] = ["nearby"]
    repository = FakeRepository([task])
    label_studio = FakeLabelStudio(
        {
            55: LabelStudioTaskState(
                task_id=55,
                data={
                    "labeling_task_id": str(task.id),
                    "content_hash": task.content_hash,
                    TEXT: task.description_snapshot,
                },
                annotations=[corrected],
            )
        }
    )

    result = ApproveBatch(repository, label_studio).execute(
        batch_id=repository.batch_id,
        actor="reviewer@example.test",
    )

    assert result.approved == 1
    assert label_studio.created_annotations == []
    assert repository.approved == [task.id]
    assert repository.synced[task.id]["result"] == [PARK]


def test_approve_batch_requires_saved_annotation_or_prediction():
    task = make_task(label_studio_task_id=55)
    repository = FakeRepository([task])
    label_studio = FakeLabelStudio(
        {
            55: LabelStudioTaskState(
                task_id=55,
                data={
                    "labeling_task_id": str(task.id),
                    "content_hash": task.content_hash,
                    TEXT: task.description_snapshot,
                },
            )
        }
    )

    result = ApproveBatch(repository, label_studio).execute(
        batch_id=repository.batch_id,
        actor="reviewer@example.test",
    )

    assert result.approved == 0
    assert task.id in repository.sync_errors
    assert repository.approved == []


def test_sync_annotations_records_errors_and_continues():
    good = make_task(label_studio_task_id=1)
    bad = make_task(label_studio_task_id=2)
    repository = FakeRepository([good, bad])
    label_studio = FakeLabelStudio(
        {
            1: LabelStudioTaskState(
                task_id=1,
                data={
                    "labeling_task_id": str(good.id),
                    "content_hash": good.content_hash,
                    TEXT: good.description_snapshot,
                },
                annotations=[accepted_annotation()],
            ),
            2: RuntimeError("temporary API failure"),
        }
    )

    result = SyncAnnotations(repository, label_studio).execute(batch_id=repository.batch_id)

    assert result.synced == 1
    assert result.failed == 1
    assert good.id in repository.synced
    assert bad.id in repository.sync_errors


def test_approve_batch_rejects_label_studio_task_with_mismatched_text():
    task = make_task(label_studio_task_id=55)
    repository = FakeRepository([task])
    label_studio = FakeLabelStudio(
        {
            55: LabelStudioTaskState(
                task_id=55,
                data={
                    "labeling_task_id": str(task.id),
                    "content_hash": task.content_hash,
                    TEXT: "different text",
                },
                annotations=[accepted_annotation()],
            )
        }
    )

    result = ApproveBatch(repository, label_studio).execute(
        batch_id=repository.batch_id,
        actor="reviewer@example.test",
    )

    assert result.approved == 0
    assert "text/hash" in repository.sync_errors[task.id]
    assert repository.approved == []


def test_label_studio_task_data_match_requires_id_hash_and_text():
    task = make_task()
    expected = task_payload(task).data
    assert task_data_matches(dict(expected), expected)
    assert not task_data_matches({**expected, TEXT: "changed"}, expected)
    assert not task_data_matches({**expected, "content_hash": "b" * 64}, expected)


def test_import_reconciliation_can_signal_stale_existing_task():
    task = make_task()
    payload = task_payload(task)

    class StaleTaskClient(LabelStudioClient):
        def __init__(self):
            super().__init__(base_url="http://label-studio.test", api_token="token")

        def _request(self, method: str, path: str, payload=None):
            if method == "GET" and path.startswith("api/tasks?"):
                return {
                    "results": [
                        {
                            "id": 123,
                            "data": {**payload_data, TEXT: "changed text"},
                            "predictions": [],
                            "annotations": [],
                        }
                    ]
                }
            raise AssertionError(f"unexpected request: {method} {path}")

    payload_data = payload.data
    with pytest.raises(LabelStudioError):
        StaleTaskClient().import_tasks(7, [payload])
