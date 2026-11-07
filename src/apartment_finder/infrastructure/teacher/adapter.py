"""Adapter exposing a teacher provider through the application port."""

from datetime import UTC, datetime
from hashlib import sha256

from apartment_finder.application.ports import TeacherAnnotationProvider
from apartment_finder.domain.labeling import LabelingTask, TeacherAnnotation, TeacherStatus
from apartment_finder.infrastructure.teacher import label_safely, to_record
from apartment_finder.infrastructure.teacher.base import TeacherProvider
from apartment_finder.infrastructure.teacher.prompt import SYSTEM_PROMPT

PROMPT_VERSION = sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


class ExistingTeacherAnnotationProvider(TeacherAnnotationProvider):
    """Adapt a ``TeacherProvider`` to the application's ``TeacherAnnotationProvider`` port."""

    def __init__(self, provider: TeacherProvider):
        self._provider = provider

    def label(self, task: LabelingTask) -> TeacherAnnotation:
        """Label the task description via the provider and wrap it as a ``TeacherAnnotation``."""
        row = {
            "id": str(task.id),
            "content_hash": task.content_hash,
            "description": task.description_snapshot,
            "title": "",
            "city": "",
            "area": "",
        }
        record = to_record(row, self._provider, label_safely(self._provider, row))
        return TeacherAnnotation(
            provider=self._provider.name,
            model=self._provider.model,
            prompt_version=PROMPT_VERSION,
            status=TeacherStatus(record["status"]),
            result=record["pois"],
            problems=record["problems"],
            labeled_at=datetime.now(UTC),
        )
